"""
帧知 - LLM-as-judge 公共模块

用 LLM 对回答打分，供生成层评测调用。

裁判走**独立的 `JUDGE_*` 模型**（现为 `glm-5.3-flash`），与被评模型（deepseek）**不同源**，
已消除自评偏差；未配置 `JUDGE_*` 时回落到默认模型并告警（此时偏差回归）。

支持**带图评判**：记录的 `judge_images` 字段非空时，裁判会直接看到画面，
而不是只读 VL 转述 —— 这让画面题的判据不再依赖有损的中间环节（见踩坑 #19）。
V1~V4 不带该字段，行为与以前完全一致。
"""
import json
import re


def _extract_json(text: str) -> dict:
    """从 LLM 输出里稳健地抠出 JSON。

    解析失败**必须抛错**，不能返回 `{}` —— 后者会让 `r.get("score", 0.0)`
    静默落成 0 分，把「裁判没看懂」伪装成「回答不忠实」，而且不会进
    `judge_failures` 统计。这是踩坑记录 #5（裁判静默失败）的同类残留：
    当时只修了「输出为空」那一半，「有输出但抠不出 JSON」这一半漏了。
    """
    raw = text
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    else:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            text = m.group(0)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"抠不出 JSON（{e}）；原文前 120 字：{raw[:120]!r}") from e


FAITHFULNESS_PROMPT = """你是严格的评测员，判断「回答」中的每个陈述是否能在「参考材料」中找到依据。

只依据参考材料判断，不要用你自己的知识补充。回答中若出现参考材料没有的信息，即视为「无依据」。

输出 JSON（不要任何其他文字）：
{"score": 0.0~1.0, "unsupported": ["无依据的陈述1", "无依据的陈述2"]}

score 含义：有依据的陈述占比（1.0 = 全部有依据，0.0 = 全部无依据）。"""

RELEVANCY_PROMPT = """你是严格的评测员，判断「回答」是否切题且完整。

参考「标准答案」，但不要要求逐字一致，语义等价即可。

输出 JSON（不要任何其他文字）：
{"score": 0.0~1.0, "reason": "一句话理由"}

score 含义：0.0=完全跑题或错误，0.5=部分正确/不完整，1.0=切题且完整准确。"""

REFUSAL_PROMPT = """判断「回答」是否在拒绝作答（即：明确表示视频中没有相关内容、无法回答）。

**关键：看回答的核心结论，不要被回答的长度带偏。**
即使回答很长、还额外解释了视频里讲了什么，只要核心结论是「视频中没有这个内容」，就算拒答。

【拒答】的特征（核心结论是"没有"）：
- "视频中没有提到"、"未提及"、"没有给出"、"无法回答"
- "讲者并没有比较 X"、"没有看到 Y"

【非拒答】的特征（核心结论是"是什么"）：
- 直接给出了用户所问内容的具体答案（即使可能不准确）
- 编造了信息

只输出 JSON（不要任何其他文字）：
{"refused": true/false, "reason": "一句话理由"}"""


# judge 模型的推理阶段会消耗 token，且推理量随答案复杂度暴涨
# （实测：400→全被推理吃光输出为空；3000→长答案仍不够）
#
# ⚠️ 6000 仍偏小：GLM-5.3-Flash 是「始终思考」模型，实测**单次判定的推理量可达
# 4000~6000**，于是经常出现「6000 被推理吃光 → 输出为空 → 梯度重试到 12000 才成功」。
# 那第一遍是**必然失败但仍要付全价**的浪费（多一次完整生成，裁判时间近乎翻倍）。
#
# 提到 12000 是**纯提速、不改语义**：成功的那次本来就跑在 12000，
# 只是省掉了前面那次注定失败的尝试 —— 判定结果不变，因此不影响历史可比性。
JUDGE_MAX_TOKENS = 12000


async def _ask(system: str, user: str, max_tokens: int = JUDGE_MAX_TOKENS,
               images: list = None) -> dict:
    """调用 judge，梯度加大 max_tokens 重试。

    两种失败都要重试，而且**最终必须显式报错**（走 judge_error 通道），
    绝不静默返回 0 分：
    - 输出为空：推理把 token 吃光了（踩坑记录 #5）
    - 有输出但抠不出 JSON：裁判写了散文没按格式（同类残留，2026-09-13 补修）
    """
    import asyncio as _aio
    from backend.services.llm.gateway import chat
    last_err = "judge 未产生任何有效输出"
    # images 非空时走**带图评判**（裁判直接看画面，而不是只读 VL 转述）——
    # 见 judge_record 的说明。不带图时消息结构与以前**完全一致**，V1~V4 不受影响。
    msg = {"role": "user", "content": user}
    if images:
        msg["images"] = images

    # ★ 外层抗限流：裁判端点（bigmodel）会**突发性**返回 429 ——
    #   实测同一时刻并发 4 全挂、并发 16 全通，不是固定并发上限。
    #   网关自身的重试只有 3 次、退避最多 2.4s，扛不过这种突发；
    #   而裁判一旦失败整条记录就被剔除，**一次限流会让整份报告变空**（实测 24/24 全废）。
    #   所以这里再加一层更长退避的重试。
    for attempt in range(3):
        if attempt:
            await _aio.sleep(3.0 * attempt)      # 3s / 6s
        try:
            for mt in (max_tokens, max_tokens * 2, 16000):
                ans, usage = await chat(
                    messages=[msg],
                    system_prompt=system,
                    temperature=0.0,
                    max_tokens=mt,
                    judge=True,  # 走 JUDGE_* 专用模型；未配置则回落并告警（见 _chat_cfg）
                )
                if not ans.strip():
                    last_err = f"输出为空（reasoning 吃光 token）：{usage}"
                    continue
                try:
                    return _extract_json(ans)
                except ValueError as e:
                    last_err = str(e)
                    continue
        except Exception as e:                   # 多为限流/网络，换一轮外层重试
            last_err = f"{type(e).__name__}: {str(e)[:120]}"
            continue
    raise RuntimeError(f"judge 判定失败（外层重试 3 轮）：{last_err}")


# 参考材料的截断上限。注意：全量字幕方案下 context = 完整字幕（可达 1 万字），
# 上限给小了会把后半段（如 V3 追加的画面描述）整个截掉，
# 导致回答里那部分内容被误判为「无依据」→ 忠实度假性下降。
CONTEXT_LIMIT = 24000


def _score_of(r: dict, key: str = "score") -> float:
    """取分数字段；缺字段就抛错。

    JSON 解析成功不等于内容合规——裁判可能给出 `{"reason": "..."}` 而漏掉 score。
    这时用 `.get(key, 0.0)` 又会静默变成 0 分，等于换个姿势重犯同一个错。
    """
    if key not in r:
        raise ValueError(f"judge 输出缺少字段 {key!r}：{r}")
    return float(r[key])


async def judge_faithfulness(context: str, answer: str, images: list = None) -> dict:
    """忠实度：回答是否忠于参考材料（不编造）。

    `images` 非空时裁判**直接看画面**，而不是只读 VL 的描述 ——
    这既收紧了判据（不再依赖有损的转述），也修掉了「判官看不到图片」的盲区（踩坑 #19）。
    """
    r = await _ask(FAITHFULNESS_PROMPT,
                   f"【参考材料】\n{context[:CONTEXT_LIMIT]}\n\n【回答】\n{answer}",
                   images=images)
    return {"score": _score_of(r), "unsupported": r.get("unsupported", [])}


async def judge_relevancy(question: str, reference: str, answer: str,
                          images: list = None) -> dict:
    """相关性：是否切题、与标准答案语义一致。`images` 语义同 judge_faithfulness。"""
    r = await _ask(RELEVANCY_PROMPT,
                   f"【问题】\n{question}\n\n【标准答案】\n{reference}\n\n【回答】\n{answer}",
                   images=images)
    return {"score": _score_of(r), "reason": r.get("reason", "")}


async def _one_refusal_vote(question: str, answer: str) -> bool:
    r = await _ask(REFUSAL_PROMPT, f"【问题】\n{question}\n\n【回答】\n{answer}")
    return bool(r.get("refused", False))


async def judge_refusal(question: str, answer: str, votes: int = 3) -> dict:
    """拒答判定：用于 unanswerable 题（应拒答）

    裁判在长回答上有随机性（实测同一题重复判定会出现 1/3 翻转），
    所以跑多轮取多数，降低单次判定的噪声。
    """
    import asyncio as _aio
    results = await _aio.gather(*[_one_refusal_vote(question, answer) for _ in range(votes)])
    n_yes = sum(results)
    return {
        "refused": n_yes * 2 > votes,     # 过半即为拒答
        "votes": list(results),
        "reason": f"多数投票 {n_yes}/{votes}",
    }


async def judge_record(rec: dict) -> dict:
    """对一条评测记录做完整裁判，结果写回 rec。

    ★ 统一入口：原先 V2/V3/V4 各有一份近乎复制的 judge_one，而且**字段不一致**——
    V2 存 refusal_reason / unsupported / relevancy_reason，V3/V4 不存；
    V4 打印 refusal_votes 却从来没存过（日志里那一列恒为空）。
    结果是同一个指标在不同变体里的口径对不上，横向比较时无从判断差异来自模型还是来自记账。

    要求 rec 具备：type / question / answer，非 unanswerable 时还需
    context / reference_answer / _ts / _te。

    失败时**直接抛异常**，由调用方记进 judge_error —— 绝不静默当 0 分。
    """
    imgs = rec.get("judge_images")      # 可选：裁判直接看的画面（V5 用；V1~V4 不带）

    if rec["type"] == "unanswerable":
        j = await judge_refusal(rec["question"], rec["answer"])
        rec["refused"] = j["refused"]
        rec["refusal_votes"] = j["votes"]
        rec["refusal_reason"] = j["reason"]
    else:
        f = await judge_faithfulness(rec["context"], rec["answer"], images=imgs)
        r = await judge_relevancy(rec["question"], rec["reference_answer"], rec["answer"],
                                  images=imgs)
        c = citation_hit(rec["answer"], rec["_ts"], rec["_te"])
        rec.update({
            "faithfulness": f["score"], "unsupported": f["unsupported"][:3],
            "relevancy": r["score"], "relevancy_reason": r["reason"],
            "has_citation": c["has_citation"], "citation_accurate": c["accurate"],
        })
    return rec


# 区间引用：12:35~13:08（裸写或带括号都认）
_CITE_RANGE = r"(\d{1,2}):(\d{2})\s*[~～\-–—]\s*(\d{1,2}):(\d{2})"
# 单点引用：只认被括号包起来的（【10:51】/ [10:51] / 【10:51 附近】）。
# 不认裸写的 MM:SS —— 正文里 "3:5"、"1:2" 这类比例会大量误判成时间戳。
_CITE_POINT = r"[【\[]\s*(\d{1,2}):(\d{2})\s*(?:附近|左右)?\s*[】\]]"


def extract_citations(answer: str) -> list:
    """从回答里抽出时间戳引用，返回 [(start_sec, end_sec), ...]。

    区间引用 → (起, 止)；单点引用 → (t, t)。
    原先只认 `MM:SS~MM:SS` 区间，单点写法（如【10:51 附近】）完全不计 ——
    于是 citation_coverage 被系统性拉低，而且答得越"精确到一点"反而越吃亏。
    """
    out = []
    for m in re.finditer(_CITE_RANGE, answer):
        a, b, c, d = (int(x) for x in m.groups())
        out.append((a * 60 + b, c * 60 + d))
    for m in re.finditer(_CITE_POINT, answer):
        t = int(m.group(1)) * 60 + int(m.group(2))
        out.append((t, t))
    return sorted(out)


def citation_hit(answer: str, ts, te) -> dict:
    """引用准确性：回答里的时间戳是否指向答案时间段（自动判定，无需 LLM）

    ts/te 支持单值或并列列表（multi_hop 题有多个答案片段）。
    """
    ranges = list(zip(ts, te)) if isinstance(ts, list) else [(ts, te)]
    cites = extract_citations(answer)
    if not cites:
        return {"has_citation": False, "accurate": None, "cites": [],
                "cited_segments": 0, "total_segments": len(ranges)}
    covered = {i for c in cites for i, (s, e) in enumerate(ranges) if c[0] <= e and c[1] >= s}
    return {"has_citation": True, "accurate": bool(covered), "cites": cites,
            "cited_segments": len(covered), "total_segments": len(ranges)}
