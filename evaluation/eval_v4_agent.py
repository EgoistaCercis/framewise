"""
帧知 - V4 评测：全量字幕 + Agent 自主调用视觉

用项目的真实 Agent（工具只给 analyze_frame），让模型**自主决定**是否需要看画面。
对比 V3（每题都固定调视觉），回答核心问题：

    「固定每次都调视觉」vs「Agent 自主决定」——质量差多少、成本省多少？

设计：
- 字幕作为固定前缀注入（保缓存），问题在后
- context.timestamp = 题目的 time_start（模拟「用户暂停点」，Agent 默认就看这一帧）
- 记录：是否调用了视觉、调用的时间点、每步 token 用量

用法：
    python evaluation/eval_v4_agent.py [--limit N]
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
from eval_full_context import format_transcript  # noqa: E402

DATASET = os.path.join(BASE, "evaluation", "dataset.json")
OUT = os.path.join(BASE, "evaluation", "results_v4_agent.json")
JUDGE_CONCURRENCY = 4


def _first_ts(case: dict) -> float:
    ts = case["time_start"]
    if isinstance(ts, list):
        ts = ts[0]
    return max(0.0, float(ts))


def make_recording_tool(record: list):
    """包装 analyze_frame 工具，记录每次调用的时间点"""
    from backend.services.agent.tools import AnalyzeFrameTool

    class RecordingFrameTool(AnalyzeFrameTool):
        async def run(self, context, timestamp=None, **kwargs):
            record.append(timestamp)
            return await super().run(context, timestamp=timestamp, **kwargs)

    return RecordingFrameTool()


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

    import backend.services.agent.agent as agent_mod
    from backend.main import video_states

    # 拦截 usage（Agent.run 本身不返回）
    usage_box = []
    _orig = agent_mod.gateway.chat_with_tools

    async def patched(*a, **kw):
        msg, usage = await _orig(*a, **kw)
        usage_box.append(usage)
        return msg, usage

    agent_mod.gateway.chat_with_tools = patched

    dataset = json.load(open(DATASET, encoding="utf-8"))
    records, t0 = [], time.time()

    for video in dataset["videos"]:
        vid, name = video["video_id"], video["name"]
        state = video_states.get(vid, {})
        transcript = format_transcript(vid)
        cases = video["cases"][: args.limit] if args.limit else video["cases"]
        print(f"--- {name}（{len(cases)} 题）")

        for i, case in enumerate(cases, 1):
            pause_ts = _first_ts(case)
            context = {
                "video_id": vid, "video_hash": vid,
                "url": state.get("url"), "video_path": state.get("video_path"),
                "is_url_mode": state.get("is_url_mode", False),
                "timestamp": pause_ts,          # 模拟「用户暂停点」
                "smart": False,
            }
            called = []
            agent = agent_mod.Agent(tools=[make_recording_tool(called)])

            # 注入全量字幕（替代 memory/conversation），保持固定前缀
            async def build(user_message, ctx, _t=transcript):
                return [
                    {"role": "user", "content": f"<transcript>\n{_t}\n</transcript>"},
                    {"role": "user", "content": user_message},
                ]
            agent._build_messages = build

            usage_box.clear()
            t1 = time.time()
            try:
                result = await agent.run(case["question"], context)
                ans = result["answer"]
                steps = result["steps"]
            except Exception as e:
                ans, steps = f"[Agent 异常] {type(e).__name__}: {str(e)[:150]}", 0
            lat = round(time.time() - t1, 2)

            usage = usage_box[0] if usage_box else {}
            rec = {
                "id": case["id"], "type": case["type"], "question": case["question"],
                "reference_answer": case["reference_answer"], "answer": ans,
                "context": transcript,
                "pause_ts": pause_ts,
                "vision_called": len(called) > 0,      # ★ 是否自主调用了视觉
                "vision_count": len(called),
                "vision_ts": called,
                "steps": steps, "latency_s": lat,
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "cached_tokens": usage.get("cached_tokens", 0),
                "_ts": case["time_start"], "_te": case["time_end"],
            }
            records.append(rec)
            v = "看画面" if rec["vision_called"] else "没看  "
            print(f"  [{i:2}/{len(cases)}] {v} steps={steps} {lat:>5.1f}s {case['question'][:30]}")

    print("\n裁判中...")
    sem = asyncio.Semaphore(JUDGE_CONCURRENCY)
    await asyncio.gather(*[judge_one(r, sem) for r in records])

    # ── 汇总 ──
    from collections import defaultdict
    by = defaultdict(list)
    for r in records:
        if not r.get("judge_error"):
            by[r["type"]].append(r)

    print("\n" + "=" * 96)
    print(f"{'类型':<14}{'n':>4}{'忠实度':>9}{'相关性':>9}{'引用准确':>10}{'拒答率':>9}"
          f"{'视觉调用率':>12}{'延迟':>8}")
    print("-" * 96)
    summary = {}
    for t in ("single_hop", "multi_hop", "joint", "visual_only", "unanswerable"):
        rs = by.get(t, [])
        if not rs:
            continue
        n = len(rs)
        vr = sum(1 for r in rs if r["vision_called"]) / n
        lat = sum(r["latency_s"] for r in rs) / n
        if t == "unanswerable":
            row = {"n": n, "refusal_rate": round(sum(1 for r in rs if r.get("refused")) / n, 3),
                   "vision_rate": round(vr, 3), "avg_latency_s": round(lat, 1)}
            print(f"{t:<14}{n:>4}{'—':>9}{'—':>9}{'—':>10}{row['refusal_rate']:>9.3f}{vr:>12.3f}{lat:>8.1f}")
        else:
            f = sum(r["faithfulness"] for r in rs) / n
            rel = sum(r["relevancy"] for r in rs) / n
            acc = [r["citation_accurate"] for r in rs if r.get("has_citation")]
            ar = sum(1 for a in acc if a) / len(acc) if acc else 0
            row = {"n": n, "faithfulness": round(f, 3), "relevancy": round(rel, 3),
                   "citation_accuracy": round(ar, 3), "vision_rate": round(vr, 3),
                   "avg_latency_s": round(lat, 1)}
            print(f"{t:<14}{n:>4}{f:>9.3f}{rel:>9.3f}{ar:>10.3f}{'—':>9}{vr:>12.3f}{lat:>8.1f}")
        summary[t] = row

    total_vr = sum(1 for r in records if r["vision_called"]) / len(records)
    cost = {
        "avg_prompt_tokens": round(sum(r["prompt_tokens"] for r in records) / len(records), 0),
        "avg_cached_tokens": round(sum(r["cached_tokens"] for r in records) / len(records), 0),
        "avg_latency_s": round(sum(r["latency_s"] for r in records) / len(records), 1),
        "overall_vision_rate": round(total_vr, 3),
        "avg_steps": round(sum(r["steps"] for r in records) / len(records), 2),
    }
    print("-" * 96)
    print(f"整体视觉调用率 {total_vr*100:.0f}% | 平均步数 {cost['avg_steps']} | "
          f"平均 input {cost['avg_prompt_tokens']:.0f}（缓存 {cost['avg_cached_tokens']:.0f}）| "
          f"平均延迟 {cost['avg_latency_s']}s")

    fails = [r for r in records if r.get("judge_error")]
    json.dump({"summary": summary, "cost": cost, "judge_failures": len(fails),
               "records": [{k: v for k, v in r.items() if k != "context"} for r in records]},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n耗时 {(time.time()-t0)/60:.1f} 分钟，已存 {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
