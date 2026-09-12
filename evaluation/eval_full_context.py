"""
帧知 - V2 全量字幕注入评测

不检索，把整段字幕作为独立消息注入，与 V1（RAG 检索）对比。

关键设计：
1. 字幕作为 <transcript> 消息注入（不塞 system prompt），保持缓存前缀稳定
2. 【同一视频内顺序提问】——第一个问题冷启动，后续命中前缀缓存
   这样才能真实测出「缓存后成本」，脱离缓存谈成本不公平
3. 记录 usage 里的 cached_tokens，分「冷启动 / 缓存命中」报告成本

用法：
    python evaluation/eval_full_context.py [--limit N]
"""
import argparse
import asyncio
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import judge  # noqa: E402

DATASET = os.path.join(BASE, "evaluation", "dataset.json")
SUBDIR = os.path.join(BASE, "evaluation", "subtitles")
OUT = os.path.join(BASE, "evaluation", "results_full_context.json")
JUDGE_CONCURRENCY = 4
BLOCK_CHARS = 300   # 字幕合并成 ~300 字的块，减少时间戳噪音


def _fmt(s: float) -> str:
    return f"{int(s // 60):02d}:{int(s % 60):02d}"


def format_transcript(video_id: str) -> str:
    """字幕 → 带时间戳的紧凑文本（合并成 ~300 字的块）"""
    subs = json.load(open(os.path.join(SUBDIR, f"{video_id}.json"), encoding="utf-8"))
    lines, buf, start = [], [], None
    for s in subs:
        if start is None:
            start = s["start"]
        buf.append(s["text"])
        if sum(len(x) for x in buf) >= BLOCK_CHARS:
            lines.append(f"【{_fmt(start)}~{_fmt(s['end'])}】{''.join(buf)}")
            buf, start = [], None
    if buf:
        lines.append(f"【{_fmt(start)}~{_fmt(subs[-1]['end'])}】{''.join(buf)}")
    return "\n".join(lines)


async def answer_one(transcript: str, question: str) -> tuple:
    """全量字幕回答问题，返回 (answer, usage, latency, error)。

    error 非 None 表示这次生成失败（网络抖动、限流、超时等）。
    **必须兜住**：外层 asyncio.gather 没开 return_exceptions，抛出去会让整场
    评测崩掉、已经跑完的记录全部丢失——一次抖动不该毁掉几十分钟的跑批。
    失败记录带 gen_error 占位，统计与裁判都会跳过它。
    """
    from backend.services.llm.gateway import chat
    from backend.prompts import SYSTEM_PROMPT
    from backend.config import LLM_MAX_TOKENS

    t0 = time.time()
    try:
        ans, usage = await chat(
            messages=[
                {"role": "user", "content": f"<transcript>\n{transcript}\n</transcript>"},
                {"role": "user", "content": question},
            ],
            system_prompt=SYSTEM_PROMPT,
            max_tokens=LLM_MAX_TOKENS,
        )
        return ans, usage, round(time.time() - t0, 2), None
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:200]}"
        return f"[LLM 异常] {err}", {}, round(time.time() - t0, 2), err


async def judge_one(rec: dict, sem: asyncio.Semaphore) -> dict:
    async with sem:
        try:
            if rec["type"] == "unanswerable":
                j = await judge.judge_refusal(rec["question"], rec["answer"])
                rec["refused"] = j["refused"]
                rec["refusal_reason"] = j["reason"]
            else:
                f = await judge.judge_faithfulness(rec["context"], rec["answer"])
                r = await judge.judge_relevancy(rec["question"], rec["reference_answer"], rec["answer"])
                c = judge.citation_hit(rec["answer"], rec["_ts"], rec["_te"])
                rec.update({
                    "faithfulness": f["score"], "unsupported": f["unsupported"][:3],
                    "relevancy": r["score"], "relevancy_reason": r["reason"],
                    "has_citation": c["has_citation"], "citation_accurate": c["accurate"],
                })
            rec["judge_error"] = None
        except Exception as e:
            rec["judge_error"] = str(e)[:200]
        return rec


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="每个视频只跑前 N 题（调试用）")
    args = ap.parse_args()

    dataset = json.load(open(DATASET, encoding="utf-8"))
    from eval_logger import setup_eval_log
    from loguru import logger
    log_path = setup_eval_log("eval_full_context")
    logger.info("=== V2 全量字幕注入评测开始 ===")
    logger.info(f"评测日志: {log_path}")
    records, t0 = [], time.time()

    # ── 阶段 1：顺序生成回答（保证缓存命中）──
    for video in dataset["videos"]:
        vid, name = video["video_id"], video["name"]
        transcript = format_transcript(vid)
        est = int(len(transcript) / 1.5)
        cases = video["cases"][: args.limit] if args.limit else video["cases"]
        print(f"--- {name}（字幕 {len(transcript)} 字 ≈ {est} tokens，{len(cases)} 题）")

        for i, case in enumerate(cases, 1):
            ans, usage, lat, gen_err = await answer_one(transcript, case["question"])
            rec = {
                "id": case["id"], "type": case["type"], "question": case["question"],
                "reference_answer": case["reference_answer"], "answer": ans,
                "context": transcript,
                "gen_error": gen_err,
                "latency_s": lat,
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "cached_tokens": usage.get("cached_tokens", 0),
                "cold": (i == 1),          # 该视频的第一个问题 = 冷启动
                "_ts": case["time_start"], "_te": case["time_end"],
            }
            records.append(rec)
            cache = rec["cached_tokens"]
            if gen_err:
                print(f"  [{i:2}/{len(cases)}] [生成失败] {case['question'][:32]}")
                logger.warning(f"[{i}/{len(cases)}] {case['id']} 生成失败：{gen_err}")
            else:
                print(f"  [{i:2}/{len(cases)}] in={rec['prompt_tokens']:>5} cache={cache:>5} "
                      f"{lat:>5.1f}s  {case['question'][:32]}")
                logger.info(f"[{i}/{len(cases)}] {case['id']} [{case['type']}] "
                            f"input={rec['prompt_tokens']}(缓存{cache}) 延迟={lat}s "
                            f"{'冷启动' if rec['cold'] else '缓存命中'}")

    # ── 阶段 2：并行裁判 ──
    # 生成失败的记录没有答案可判，直接跳过（它的 gen_error 已经记下了）
    todo = [r for r in records if not r.get("gen_error")]
    n_gen_fail = len(records) - len(todo)
    print(f"\n裁判中...（跳过 {n_gen_fail} 条生成失败）")
    sem = asyncio.Semaphore(JUDGE_CONCURRENCY)
    await asyncio.gather(*[judge_one(r, sem) for r in todo])

    # ── 汇总 ──
    from collections import defaultdict
    by = defaultdict(list)
    for r in records:
        if not r.get("judge_error") and not r.get("gen_error"):
            by[r["type"]].append(r)

    print("\n" + "=" * 88)
    print(f"{'类型':<14}{'n':>4}{'忠实度':>9}{'相关性':>9}{'引用准确':>10}{'拒答率':>9}{'平均延迟':>10}")
    print("-" * 88)
    summary = {}
    for t in ("single_hop", "multi_hop", "joint", "visual_only", "unanswerable"):
        rs = by.get(t, [])
        if not rs:
            continue
        n = len(rs)
        lat = sum(r["latency_s"] for r in rs) / n
        if t == "unanswerable":
            row = {"n": n, "refusal_rate": round(sum(1 for r in rs if r.get("refused")) / n, 3),
                   "avg_latency_s": round(lat, 1)}
            print(f"{t:<14}{n:>4}{'—':>9}{'—':>9}{'—':>10}{row['refusal_rate']:>9.3f}{lat:>10.1f}")
        else:
            f = sum(r["faithfulness"] for r in rs) / n
            rel = sum(r["relevancy"] for r in rs) / n
            acc = [r["citation_accurate"] for r in rs if r.get("has_citation")]
            ar = (sum(1 for a in acc if a) / len(acc)) if acc else 0
            row = {"n": n, "faithfulness": round(f, 3), "relevancy": round(rel, 3),
                   "citation_accuracy": round(ar, 3), "avg_latency_s": round(lat, 1)}
            print(f"{t:<14}{n:>4}{f:>9.3f}{rel:>9.3f}{ar:>10.3f}{'—':>9}{lat:>10.1f}")
        summary[t] = row

    # 成本
    cold = [r for r in records if r["cold"]]
    warm = [r for r in records if not r["cold"]]
    cost = {
        "cold_avg_prompt_tokens": round(sum(r["prompt_tokens"] for r in cold) / len(cold), 0) if cold else 0,
        "warm_avg_prompt_tokens": round(sum(r["prompt_tokens"] for r in warm) / len(warm), 0) if warm else 0,
        "warm_avg_cached_tokens": round(sum(r["cached_tokens"] for r in warm) / len(warm), 0) if warm else 0,
        "total_prompt_tokens": sum(r["prompt_tokens"] for r in records),
        "total_completion_tokens": sum(r["completion_tokens"] for r in records),
        "avg_latency_s": round(sum(r["latency_s"] for r in records) / len(records), 1),
    }
    print("-" * 88)
    print(f"成本：冷启动 input {cost['cold_avg_prompt_tokens']:.0f} → 缓存命中 input {cost['warm_avg_prompt_tokens']:.0f}"
          f"（其中 cached {cost['warm_avg_cached_tokens']:.0f}）")
    print(f"      平均延迟 {cost['avg_latency_s']}s | 总 input {cost['total_prompt_tokens']:,}")

    fails = [r for r in records if r.get("judge_error")]
    json.dump({"summary": summary, "cost": cost,
               "judge_failures": len(fails), "gen_failures": n_gen_fail,
               "records": [{k: v for k, v in r.items() if k != "context"} for r in records]},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    note = ""
    if n_gen_fail:
        note += f"（生成失败 {n_gen_fail}）"
    if fails:
        note += f"（裁判失败 {len(fails)}）"
    print(f"\n耗时 {(time.time()-t0)/60:.1f} 分钟，明细已存 {OUT}{note}")


if __name__ == "__main__":
    asyncio.run(main())
