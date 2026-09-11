"""
帧知 - LLM-as-judge 公共模块

用 LLM 对回答打分，供生成层评测调用。
注意：judge 与被评模型同源（都用 DeepSeek），存在自评偏差，结论需结合人工抽查。
"""
import json
import re


def _extract_json(text: str) -> dict:
    """从 LLM 输出里稳健地抠出 JSON"""
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
    except json.JSONDecodeError:
        return {}


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
# （实测：400→全被推理吃光输出为空；3000→长答案仍不够；6000→稳定）
JUDGE_MAX_TOKENS = 6000


async def _ask(system: str, user: str, max_tokens: int = JUDGE_MAX_TOKENS) -> dict:
    """调用 judge，空输出时梯度加大 max_tokens 重试（推理会吃 token）"""
    from backend.services.llm.gateway import chat
    last_usage = {}
    for mt in (max_tokens, max_tokens * 2, 16000):
        ans, usage = await chat(
            messages=[{"role": "user", "content": user}],
            system_prompt=system,
            temperature=0.0,
            max_tokens=mt,
        )
        last_usage = usage
        if ans.strip():
            return _extract_json(ans)
    # 三次都空：显式报错，不静默当 0 分
    raise RuntimeError(f"judge 返回空内容（reasoning 吃光 token）：{last_usage}")


async def judge_faithfulness(context: str, answer: str) -> dict:
    """忠实度：回答是否忠于检索到的材料（不编造）"""
    r = await _ask(FAITHFULNESS_PROMPT,
                   f"【参考材料】\n{context[:6000]}\n\n【回答】\n{answer}")
    return {"score": float(r.get("score", 0.0)), "unsupported": r.get("unsupported", [])}


async def judge_relevancy(question: str, reference: str, answer: str) -> dict:
    """相关性：是否切题、与标准答案语义一致"""
    r = await _ask(RELEVANCY_PROMPT,
                   f"【问题】\n{question}\n\n【标准答案】\n{reference}\n\n【回答】\n{answer}")
    return {"score": float(r.get("score", 0.0)), "reason": r.get("reason", "")}


async def judge_refusal(question: str, answer: str) -> dict:
    """拒答判定：用于 unanswerable 题（应拒答）"""
    r = await _ask(REFUSAL_PROMPT, f"【问题】\n{question}\n\n【回答】\n{answer}")
    return {"refused": bool(r.get("refused", False)), "reason": r.get("reason", "")}


def extract_citations(answer: str) -> list:
    """从回答里抽出时间戳引用，如【12:35~13:08】→ [(755, 788)]"""
    out = []
    for m in re.finditer(r"(\d{1,2}):(\d{2})\s*[~～-]\s*(\d{1,2}):(\d{2})", answer):
        a, b, c, d = (int(x) for x in m.groups())
        out.append((a * 60 + b, c * 60 + d))
    return out


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
