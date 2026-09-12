"""
帧知 - 重试裁判失败的记录

复用 results_generation.json 里已存的**答案**与**检索片段**，只对 judge_error
的记录重新裁判（judge 内部会梯度加大 max_tokens 重试）。

为什么必须复用存下来的 context：
    重判的正确语义是「用**当初生成答案时**的材料重新打分」。如果重判时重新跑一遍
    检索，评的就是「**现在**检索到的片段」——索引或字幕一变（本项目就发生过字幕
    被 ASR 覆盖、之后修正重跑），重判结果与原分数根本不可比。而且白花一遍 embedding。

    旧结果（2026-09-13 之前）没有存 context，只能回退到重新检索，此时会显式告警。

用法：
    python evaluation/retry_failed.py
"""
import asyncio
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import judge  # noqa: E402
from eval_generation import _fmt, CONCURRENCY  # noqa: E402

DATASET = os.path.join(BASE, "evaluation", "dataset.json")
RESULT = os.path.join(BASE, "evaluation", "results_generation.json")
TOP_K = 5


async def rejudge(rec: dict, case: dict, video_id: str, index, meta, sem) -> dict:
    """重判一条记录。index/meta 只在「没存 context」时才需要，可为 None。"""
    async with sem:
        try:
            context = rec.get("context")
            if not context:
                # 老结果没存 context：只能重新检索，但必须让调用方知道这次不可比
                from backend.services.rag_pipeline.embedding_service import embed_single
                from backend.services.rag_pipeline.vector_store import search
                emb = await embed_single(case["question"], video_id=video_id)
                hits = await asyncio.to_thread(search, index, meta, emb, top_k=TOP_K)
                context = "\n\n".join(
                    f"【{_fmt(r['chunk']['start_time'])}~{_fmt(r['chunk']['end_time'])}】{r['chunk']['text']}"
                    for r in hits
                )
            # 判定统一走 judge_record —— 这里原本是第四份复制品
            rec["context"] = context
            rec["_ts"] = case["time_start"]      # 评测记录里存的是 time_range，不是 _ts/_te
            rec["_te"] = case["time_end"]
            await judge.judge_record(rec)
            rec["judge_error"] = None
            print(f"  [重判成功] {rec['question'][:36]}")
        except Exception as e:
            rec["judge_error"] = str(e)[:200]
            print(f"  [仍失败] {rec['question'][:36]} <- {str(e)[:60]}")
        return rec


def summarize(records: list) -> dict:
    from collections import defaultdict
    by = defaultdict(list)
    for r in records:
        by[r["type"]].append(r)
    out = {}
    for t in ("single_hop", "multi_hop", "joint", "visual_only", "unanswerable"):
        # 生成失败的记录没有答案，不参与统计（与 eval_generation 口径一致）
        rs = [r for r in by.get(t, [])
              if not r.get("judge_error") and not r.get("gen_error")]
        if not rs:
            continue
        n = len(rs)
        if t == "unanswerable":
            out[t] = {"n": n, "refusal_rate": round(sum(1 for r in rs if r.get("refused")) / n, 3)}
        else:
            acc = [r["citation_accurate"] for r in rs if r.get("has_citation")]
            out[t] = {
                "n": n,
                "faithfulness": round(sum(r["faithfulness"] for r in rs) / n, 3),
                "relevancy": round(sum(r["relevancy"] for r in rs) / n, 3),
                "citation_coverage": round(sum(1 for r in rs if r.get("has_citation")) / n, 3),
                "citation_accuracy": round(sum(1 for a in acc if a) / len(acc), 3) if acc else None,
            }
    return out


async def main():
    from backend.services.rag_pipeline.vector_store import load_index

    dataset = json.load(open(DATASET, encoding="utf-8"))
    case_map = {c["id"]: (c, v["video_id"]) for v in dataset["videos"] for c in v["cases"]}
    data = json.load(open(RESULT, encoding="utf-8"))
    records = data["records"]

    failed = [r for r in records if r.get("judge_error")]
    print(f"待重判 {len(failed)} 条")
    if not failed:
        return

    sem = asyncio.Semaphore(CONCURRENCY)
    idx_cache = {}
    tasks, missing, need_search = [], [], 0
    for rec in failed:
        entry = case_map.get(rec["id"])
        if entry is None:
            # 数据集重建过、id 变了：跳过并报告，而不是 KeyError 崩掉整个重判
            missing.append(rec["id"])
            continue
        case, vid = entry
        if rec.get("context"):
            tasks.append(rejudge(rec, case, vid, None, None, sem))
        else:
            need_search += 1
            if vid not in idx_cache:
                idx_cache[vid] = load_index(vid)
            index, meta = idx_cache[vid]
            tasks.append(rejudge(rec, case, vid, index, meta, sem))

    if missing:
        print(f"[警告] {len(missing)} 条在数据集中找不到对应题目，已跳过：{missing[:5]}")
    if need_search:
        print(f"[警告] {need_search} 条没有存 context（旧结果），回退到重新检索 —— "
              f"评的是现在的检索结果，与原分数不可比")
    print(f"复用已存 context: {len(tasks) - need_search} 条")

    await asyncio.gather(*tasks)

    still = [r for r in records if r.get("judge_error")]
    data["summary"] = summarize(records)
    data["judge_failures"] = len(still)
    # 原子写：直接覆写的话，写一半崩溃会损坏 200KB 的原始评测产物
    tmp = RESULT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, RESULT)

    print("\n" + "=" * 78)
    print(f"{'类型':<14}{'n':>4}{'忠实度':>9}{'相关性':>9}{'引用覆盖':>10}{'引用准确':>10}{'拒答率':>9}")
    print("-" * 78)
    for t, row in data["summary"].items():
        if t == "unanswerable":
            print(f"{t:<14}{row['n']:>4}{'—':>9}{'—':>9}{'—':>10}{'—':>10}{row['refusal_rate']:>9.3f}")
        else:
            ca = row.get("citation_accuracy")
            ca_disp = "—" if ca is None else f"{ca:>10.3f}"   # None = 无人引用，不是 0 分
            print(f"{t:<14}{row['n']:>4}{row['faithfulness']:>9.3f}{row['relevancy']:>9.3f}"
                  f"{row['citation_coverage']:>10.3f}{ca_disp}{'—':>9}")
    print("=" * 78)
    print(f"仍有失败: {len(still)} 条")


if __name__ == "__main__":
    asyncio.run(main())
