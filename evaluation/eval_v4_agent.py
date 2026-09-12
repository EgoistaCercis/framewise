"""
帧知 - V4 评测：全量字幕 + Agent 自主调用视觉

用项目的真实 Agent（工具只给 analyze_frame），让模型**自主决定**是否需要看画面。
对比 V3（每题都固定调视觉），回答核心问题：

    「固定每次都调视觉」vs「Agent 自主决定」——质量差多少、成本省多少？

设计：
- 字幕作为固定前缀注入（保缓存），问题在后
- context.timestamp = 题目的 time_start（模拟「用户暂停点」，Agent 默认就看这一帧）
- 记录：是否调用了视觉、调用的时间点、每步 token 用量

提速（实测把 90 分钟压到 ~7 分钟）：
- **视频级并行**：8 个视频同时跑，视频内仍顺序提问 —— 这样同一字幕前缀
  的缓存复用仍然成立（cached_tokens 依然有意义），只是把 8 条线叠起来跑。
  不做「题目级并行」是因为那样同一前缀会被 10 个并发请求同时冷启动，缓存全废。
- **视觉提示词结构化**：单帧 7.1s → 1.3~3.4s（见 backend/prompts.VISION_PROMPT）
- **抽帧丢线程池**：`extract_frame` 是同步 subprocess，直接 await 会阻塞事件循环
- usage 采集改用 ContextVar —— 否则并行后多个视频会往同一个 list 里塞，串数据

用法：
    python evaluation/eval_v4_agent.py [--limit N] [--videos N]
"""
import argparse
import asyncio
import contextvars
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
from eval_logger import setup_eval_log, log_question, log_tool_calls  # noqa: E402
from loguru import logger  # noqa: E402

DATASET = os.path.join(BASE, "evaluation", "dataset.json")
OUT = os.path.join(BASE, "evaluation", "results_v4_agent.json")
JUDGE_CONCURRENCY = 6
VIDEO_CONCURRENCY = 8      # 8 个视频并行（=数据集全部视频数）

# 每个视频任务各持一条 usage 收集线，避免并行下互相串数据
_usage_sink: contextvars.ContextVar = contextvars.ContextVar("v4_usage_sink", default=None)


def _first_ts(case: dict) -> float:
    ts = case["time_start"]
    if isinstance(ts, list):
        ts = ts[0]
    return max(0.0, float(ts))


def make_recording_tool(record: list):
    """包装 analyze_frame：记录每次调用的时间点与结果（含失败）

    与产品内 AnalyzeFrameTool 的差异（评测适配）：
    - 抽帧优先用本地视频文件（产品内只认 video_path / URL 拉流，
      而部分 B站视频 yt-dlp 拿不到直链，会全部失败）
    - 失败也记录，便于统计「Agent 调了但没成功」的比例
    """
    from backend.services.agent.tools import AnalyzeFrameTool
    from backend.services.media.vision_service import analyze_frame
    from media_utils import extract_frame_for

    class RecordingFrameTool(AnalyzeFrameTool):
        async def run(self, context, timestamp=None, **kwargs):
            ts = timestamp if timestamp is not None else context.get("timestamp", 0)
            try:
                fp, err = await extract_frame_for(context["video_id"], context.get("_state", {}), ts)
                if not fp:
                    record.append({"ts": ts, "desc": None, "ok": False, "err": err})
                    return f"无法截帧分析画面：{err}"
                desc = await analyze_frame(fp, video_id=context.get("video_id"))
                record.append({"ts": ts, "desc": desc, "ok": True})
                return f"画面分析结果：{desc}"
            except Exception as e:
                record.append({"ts": ts, "desc": None, "ok": False, "err": str(e)[:120]})
                raise

    return RecordingFrameTool()


async def judge_one(rec: dict, sem: asyncio.Semaphore) -> dict:
    """裁判单条记录。判定逻辑统一在 judge.judge_record（原先三个脚本各一份复制品）。"""
    async with sem:
        try:
            await judge.judge_record(rec)
            rec["judge_error"] = None
        except Exception as e:
            rec["judge_error"] = str(e)[:200]
        return rec


async def run_video(video: dict, limit: int) -> list[dict]:
    """跑完一个视频的全部题目。

    **视频内顺序提问**是刻意的：第一题冷启动、后续命中同一字幕前缀的缓存，
    这样报告里的 cached_tokens 才是真实的产品内表现。视频之间才并行。
    """
    import backend.services.agent.agent as agent_mod
    from backend.main import video_states

    vid, name = video["video_id"], video["name"]
    tag = f"[{name[:12]}]"
    state = video_states.get(vid, {})
    transcript = format_transcript(vid)
    cases = video["cases"][:limit] if limit else video["cases"]

    print(f"{tag} 开始（{len(cases)} 题）")
    logger.info(f"--- 视频 {name} ({vid})，{len(cases)} 题 ---")

    recs = []
    for i, case in enumerate(cases, 1):
        pause_ts = _first_ts(case)
        context = {
            "video_id": vid, "video_hash": vid,
            "url": state.get("url"), "video_path": state.get("video_path"),
            "is_url_mode": state.get("is_url_mode", False),
            "timestamp": pause_ts,          # 模拟「用户暂停点」
            "smart": False,
            "_state": state,                # 供评测版抽帧工具使用
        }
        called = []
        agent = agent_mod.Agent(tools=[make_recording_tool(called)])

        # 注入全量字幕（替代 memory/conversation），保持固定前缀。
        # 注意：<player_state> 必须跟着一起注入 —— 否则这里 override 掉 _build_messages
        # 就把「用户暂停在哪」丢了，测出来的还是修之前的瞎扫行为。
        async def build(user_message, ctx, _t=transcript):
            msgs = [{"role": "user", "content": f"<transcript>\n{_t}\n</transcript>"}]
            player = agent_mod.format_player_state(ctx)
            if player:
                msgs.append({"role": "user", "content": player})
            msgs.append({"role": "user", "content": user_message})
            return msgs
        agent._build_messages = build

        box: list = []
        _usage_sink.set(box)        # 本任务专属的收集线
        t1 = time.time()
        try:
            result = await agent.run(case["question"], context)
            ans = result["answer"]
            steps = result["steps"]
        except Exception as e:
            ans, steps = f"[Agent 异常] {type(e).__name__}: {str(e)[:150]}", 0
        lat = round(time.time() - t1, 2)

        # ★ 必须跨步求和。原来只取 box[0]（Agent 第一步）的 usage，
        # 后面每一步的 token 全丢了 → 平均 input 被严重低估，成本分析做不了。
        usage = {k: sum(u.get(k, 0) for u in box)
                 for k in ("prompt_tokens", "completion_tokens", "cached_tokens")} if box else {}
        # 裁判参考材料 = 字幕 + Agent 实际看过的画面描述
        # （否则回答里引用画面的内容会被误判为「无依据」）
        ctx_parts = [transcript]
        for fr in called:
            if fr.get("desc"):
                ctx_parts.append(f'<frame time="{fr["ts"]}">\n{fr["desc"]}\n</frame>')
        rec = {
            "id": case["id"], "type": case["type"], "question": case["question"],
            "reference_answer": case["reference_answer"], "answer": ans,
            "context": "\n\n".join(ctx_parts),
            "pause_ts": pause_ts,
            "vision_called": len(called) > 0,      # ★ 是否自主调用了视觉
            "vision_count": len(called),
            # ★ 成功次数必须单独记：只记「调用了几次」的话，工具全挂了也看不出来
            # —— 上一轮 UnboundLocalError 让 23/80 题的画面全取不到，
            #    而 vision_count 照常是正数，「看画面」照常打印，跑了 9 分钟才发现。
            "vision_ok": sum(1 for fr in called if fr.get("ok")),
            "vision_ts": [fr["ts"] for fr in called],
            "steps": steps, "latency_s": lat,
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "cached_tokens": usage.get("cached_tokens", 0),
            "_ts": case["time_start"], "_te": case["time_end"],
        }
        recs.append(rec)
        if not rec["vision_called"]:
            v = "没看  "
        elif rec["vision_ok"] == rec["vision_count"]:
            v = f"看画面{rec['vision_count']}次"
        elif rec["vision_ok"] == 0:
            v = f"⚠全失败{rec['vision_count']}次"     # 工具挂了，答案必然不可信
        else:
            v = f"看画面{rec['vision_ok']}/{rec['vision_count']}次"
        print(f"{tag} [{i:2}/{len(cases)}] {v} steps={steps} {lat:>5.1f}s "
              f"{case['question'][:26]}")
        log_question(case, idx=i, total=len(cases))
        log_tool_calls(called)
        logger.info(f"   步数={steps} 延迟={lat:.1f}s "
                    f"input={rec['prompt_tokens']}(缓存{rec['cached_tokens']})")
        logger.info(f"   回答: {ans[:120]}")

    print(f"{tag} ✓ 完成 {len(recs)} 题")
    return recs


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--videos", type=int, default=0, help="只跑前 N 个视频（调试用）")
    args = ap.parse_args()

    import backend.services.agent.agent as agent_mod

    # 拦截 usage（Agent.run 本身不返回），按 ContextVar 分发给当前视频任务
    _orig = agent_mod.gateway.chat_with_tools

    async def patched(*a, **kw):
        msg, usage = await _orig(*a, **kw)
        sink = _usage_sink.get()
        if sink is not None:
            sink.append(usage)
        return msg, usage

    agent_mod.gateway.chat_with_tools = patched

    dataset = json.load(open(DATASET, encoding="utf-8"))
    log_path = setup_eval_log("eval_v4_agent")
    logger.info(f"=== V4 评测开始（Agent 自主调用视觉）===")
    logger.info(f"评测日志: {log_path}")
    logger.info(f"数据集: {dataset['meta']['videos']} 视频 / {dataset['meta']['questions']} 题"
                + (f"（--limit {args.limit}）" if args.limit else ""))
    logger.info(f"视频级并行度 {VIDEO_CONCURRENCY}，视觉提示词=结构化短文")

    t0 = time.time()
    videos = dataset["videos"][: args.videos] if args.videos else dataset["videos"]
    sem_v = asyncio.Semaphore(VIDEO_CONCURRENCY)
    print(f"并行跑 {len(videos)} 个视频（每个视频内顺序提问，保前缀缓存）…")

    async def guarded(v):
        async with sem_v:
            return await run_video(v, args.limit)

    results = await asyncio.gather(*[guarded(v) for v in videos])
    records = [r for rs in results for r in rs]

    print("\n裁判中...")
    logger.info("裁判阶段开始")
    sem = asyncio.Semaphore(JUDGE_CONCURRENCY)
    await asyncio.gather(*[judge_one(r, sem) for r in records])

    for r in records:
        if r.get("judge_error"):
            logger.warning(f"   裁判失败 {r['id']}: {r['judge_error'][:80]}")
        elif r["type"] == "unanswerable":
            logger.info(f"   {r['id']} 拒答={r.get('refused')} 票={r.get('refusal_votes', '')}")
        else:
            logger.info(f"   {r['id']} 忠实={r.get('faithfulness')} 相关={r.get('relevancy')} "
                        f"引用={r.get('citation_accurate')}")

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

    # ── 视觉调用次数分布 ──
    # 这组数字是用来定「要不要设上限、设几」的：上限应当卡在正常题目的 P90/P95 之外，
    # 只截住「盲试」的长尾，而不是把多步问题一起砍掉。
    from collections import Counter
    def _dist(rs):
        return dict(sorted(Counter(r["vision_count"] for r in rs).items()))

    all_counts = sorted(r["vision_count"] for r in records)
    def _pct(p):
        if not all_counts:
            return 0
        return all_counts[min(len(all_counts) - 1, int(len(all_counts) * p))]

    vision_dist = {
        "overall": _dist(records),
        "by_type": {t: _dist(by[t]) for t in ("single_hop", "multi_hop", "joint",
                                              "visual_only", "unanswerable") if by.get(t)},
        "p50": _pct(0.50), "p90": _pct(0.90), "p95": _pct(0.95), "max": all_counts[-1] if all_counts else 0,
        "never_called": sum(1 for c in all_counts if c == 0),
    }
    cost["vision_count_dist"] = vision_dist

    print("\n视觉调用次数分布（用于定上限）")
    print(f"  次数 → 题目数: {vision_dist['overall']}")
    print(f"  P50={vision_dist['p50']} P90={vision_dist['p90']} P95={vision_dist['p95']} "
          f"max={vision_dist['max']} 未调用={vision_dist['never_called']}/{len(records)}")
    for t, d in vision_dist["by_type"].items():
        print(f"  {t:<14}{d}")

    # ── 健康检查：工具大面积失败时结果不可信，必须显式叫停而不是照常出报告 ──
    tot_calls = sum(r["vision_count"] for r in records)
    tot_ok = sum(r["vision_ok"] for r in records)
    all_failed = [r["id"] for r in records if r["vision_count"] and not r["vision_ok"]]
    fail_rate = 1 - (tot_ok / tot_calls) if tot_calls else 0
    health = {
        "vision_calls": tot_calls, "vision_ok": tot_ok,
        "vision_fail_rate": round(fail_rate, 3),
        "questions_all_failed": len(all_failed),
        "ok": fail_rate < 0.1 and len(all_failed) <= 2,
    }
    cost["health"] = health

    print("\n跑批健康检查")
    print(f"  视觉调用 {tot_calls} 次，成功 {tot_ok} 次（失败率 {fail_rate:.1%}）")
    if health["ok"]:
        print("  ✓ 正常")
    else:
        print(f"  ✗ 异常：{len(all_failed)} 题的画面调用全部失败 → 结果不可信")
        print(f"    涉及题目: {all_failed[:8]}{' …' if len(all_failed) > 8 else ''}")
        print("    先查日志里的工具报错，别拿这份结果下结论")
        logger.error(f"健康检查未通过：视觉失败率 {fail_rate:.1%}，"
                     f"{len(all_failed)} 题全失败 {all_failed[:8]}")

    fails = [r for r in records if r.get("judge_error")]
    json.dump({"summary": summary, "cost": cost, "judge_failures": len(fails),
               "health": health,
               "records": [{k: v for k, v in r.items() if k != "context"} for r in records]},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n耗时 {(time.time()-t0)/60:.1f} 分钟，已存 {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
