"""
帧知 - V3 评测：全量字幕 + 固定视觉

在 V2（全量字幕）基础上，【每题都】分析「用户暂停点」那一帧，把画面描述一起给模型。

- 暂停点取题目的 time_start（模拟「用户看到该画面时暂停提问」）—— 见评测报告中的假设说明
- 字幕仍作为固定前缀注入 → 保持前缀缓存

流程：并行预抽帧 → 顺序问答（保缓存）→ 并行裁判

用法：
    python evaluation/eval_v3_full_vision.py [--limit N]
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
from eval_full_context import format_transcript, _fmt  # noqa: E402

DATASET = os.path.join(BASE, "evaluation", "dataset.json")
OUT = os.path.join(BASE, "evaluation", "results_v3_full_vision.json")
FRAME_CONCURRENCY = 3
JUDGE_CONCURRENCY = 4


def _first_ts(case: dict) -> float:
    """取题目答案区间的起点作为「暂停点」；列表（multi_hop）取第一个"""
    ts = case["time_start"]
    if isinstance(ts, list):
        ts = ts[0]
    return max(0.0, float(ts))


async def extract_one(video_id: str, state: dict, ts: float, sem: asyncio.Semaphore) -> dict:
    """抽帧 + 视觉分析（优先本地视频，回退 URL）"""
    from backend.services.media.vision_service import analyze_frame
    from media_utils import extract_frame_for

    async with sem:
        frame_path, err = await extract_frame_for(video_id, state, ts)
        if not frame_path:
            return {"ts": ts, "desc": None, "error": err}
        try:
            desc = await analyze_frame(frame_path, video_id=video_id)
            return {"ts": ts, "desc": desc, "error": None}
        except Exception as e:
            return {"ts": ts, "desc": None, "error": f"vl: {type(e).__name__}: {str(e)[:100]}"}


async def judge_one(rec: dict, sem: asyncio.Semaphore) -> dict:
    async with sem:
        try:
            if rec["type"] == "unanswerable":
                j = await judge.judge_refusal(rec["question"], rec["answer"])
                rec["refused"] = j["refused"]
            else:
                f = await judge.judge_faithfulness(rec["context"], rec["answer"])
                r = await judge.judge_relevancy(rec["question"], rec["reference_answer"], rec["answer"])
                c = judge.citation_hit(rec["answer"], rec["_ts"], rec["_te"])
                rec.update({
                    "faithfulness": f["score"], "relevancy": r["score"],
                    "has_citation": c["has_citation"], "citation_accurate": c["accurate"],
                })
            rec["judge_error"] = None
        except Exception as e:
            rec["judge_error"] = str(e)[:200]
        return rec


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    from backend.main import video_states
    from backend.services.llm.gateway import chat as llm_chat
    from backend.prompts import SYSTEM_PROMPT
    from backend.config import LLM_MAX_TOKENS

    dataset = json.load(open(DATASET, encoding="utf-8"))
    records, t0 = [], time.time()

    for video in dataset["videos"]:
        vid, name = video["video_id"], video["name"]
        state = video_states.get(vid, {})
        transcript = format_transcript(vid)
        cases = video["cases"][: args.limit] if args.limit else video["cases"]

        # ── 并行预抽帧 ──
        ts_list = [_first_ts(c) for c in cases]
        sem_f = asyncio.Semaphore(FRAME_CONCURRENCY)
        print(f"--- {name}（{len(cases)} 题，抽帧中…）")
        frames = await asyncio.gather(*[extract_one(vid, state, ts, sem_f) for ts in ts_list])
        ok = sum(1 for f in frames if f["desc"])
        print(f"    抽帧完成 {ok}/{len(frames)}")

        # ── 顺序问答（保缓存）──
        for i, (case, fr) in enumerate(zip(cases, frames), 1):
            frame_msg = ""
            if fr["desc"]:
                frame_msg = f'<current_frame time="{_fmt(fr["ts"])}">\n{fr["desc"]}\n</current_frame>'
            msgs = [{"role": "user", "content": f"<transcript>\n{transcript}\n</transcript>"}]
            if frame_msg:
                msgs.append({"role": "user", "content": frame_msg})
            msgs.append({"role": "user", "content": case["question"]})

            t1 = time.time()
            ans, usage = await llm_chat(messages=msgs, system_prompt=SYSTEM_PROMPT,
                                        max_tokens=LLM_MAX_TOKENS)
            lat = round(time.time() - t1, 2)

            ctx = transcript + ("\n\n" + frame_msg if frame_msg else "")
            rec = {
                "id": case["id"], "type": case["type"], "question": case["question"],
                "reference_answer": case["reference_answer"], "answer": ans,
                "context": ctx, "frame_ts": fr["ts"], "frame_ok": bool(fr["desc"]),
                "frame_error": fr["error"], "latency_s": lat,
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "cached_tokens": usage.get("cached_tokens", 0),
                "cold": (i == 1),
                "_ts": case["time_start"], "_te": case["time_end"],
            }
            records.append(rec)
            print(f"  [{i:2}/{len(cases)}] in={rec['prompt_tokens']:>5} cache={rec['cached_tokens']:>5} "
                  f"{lat:>5.1f}s {case['question'][:30]}")

    print("\n裁判中...")
    sem = asyncio.Semaphore(JUDGE_CONCURRENCY)
    await asyncio.gather(*[judge_one(r, sem) for r in records])

    # ── 汇总 ──
    from collections import defaultdict
    by = defaultdict(list)
    for r in records:
        if not r.get("judge_error"):
            by[r["type"]].append(r)

    print("\n" + "=" * 88)
    print(f"{'类型':<14}{'n':>4}{'忠实度':>9}{'相关性':>9}{'引用准确':>10}{'拒答率':>9}{'延迟':>8}")
    print("-" * 88)
    summary = {}
    for t in ("single_hop", "multi_hop", "joint", "visual_only", "unanswerable"):
        rs = by.get(t, [])
        if not rs:
            continue
        n = len(rs); lat = sum(r["latency_s"] for r in rs) / n
        if t == "unanswerable":
            row = {"n": n, "refusal_rate": round(sum(1 for r in rs if r.get("refused")) / n, 3),
                   "avg_latency_s": round(lat, 1)}
            print(f"{t:<14}{n:>4}{'—':>9}{'—':>9}{'—':>10}{row['refusal_rate']:>9.3f}{lat:>8.1f}")
        else:
            f = sum(r["faithfulness"] for r in rs) / n
            rel = sum(r["relevancy"] for r in rs) / n
            acc = [r["citation_accurate"] for r in rs if r.get("has_citation")]
            ar = sum(1 for a in acc if a) / len(acc) if acc else 0
            row = {"n": n, "faithfulness": round(f, 3), "relevancy": round(rel, 3),
                   "citation_accuracy": round(ar, 3), "avg_latency_s": round(lat, 1)}
            print(f"{t:<14}{n:>4}{f:>9.3f}{rel:>9.3f}{ar:>10.3f}{'—':>9}{lat:>8.1f}")
        summary[t] = row

    frame_fail = sum(1 for r in records if not r["frame_ok"])
    cost = {
        "avg_prompt_tokens": round(sum(r["prompt_tokens"] for r in records) / len(records), 0),
        "avg_cached_tokens": round(sum(r["cached_tokens"] for r in records) / len(records), 0),
        "avg_latency_s": round(sum(r["latency_s"] for r in records) / len(records), 1),
        "frame_failures": frame_fail,
        "total_prompt_tokens": sum(r["prompt_tokens"] for r in records),
    }
    print("-" * 88)
    print(f"抽帧失败 {frame_fail} | 平均 input {cost['avg_prompt_tokens']:.0f}"
          f"（缓存 {cost['avg_cached_tokens']:.0f}）| 平均延迟 {cost['avg_latency_s']}s")

    fails = [r for r in records if r.get("judge_error")]
    json.dump({"summary": summary, "cost": cost, "judge_failures": len(fails),
               "records": [{k: v for k, v in r.items() if k != "context"} for r in records]},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n耗时 {(time.time()-t0)/60:.1f} 分钟，已存 {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
