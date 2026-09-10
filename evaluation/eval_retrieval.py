"""
帧知 - Layer 1 检索评测

对评测集里的每个问题做向量检索，判断 Top-K 结果是否命中答案时间段。

指标：Recall@1/3/5、MRR，按问题类型分组。

注意：
- unanswerable 没有正确答案区间 → 不参与检索指标
- visual_only 答案不在字幕里 → 检索失败是【预期结果】，作为对照组单列
- 字幕由 ASR 生成，与评测集 evidence 引用的官方字幕有差异，会拉低指标

用法：
    python evaluation/eval_retrieval.py
"""
import asyncio
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

# Windows 控制台默认 GBK，遇 emoji 会崩；强制 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DATASET = os.path.join(BASE, "evaluation", "dataset.json")
OUT = os.path.join(BASE, "evaluation", "results_retrieval.json")
TOP_K = 5


def _overlap(chunk: dict, ts: float, te: float) -> bool:
    """检索到的 chunk 与答案时间段是否有交集"""
    return chunk["start_time"] <= te and chunk["end_time"] >= ts


def _hit_rank(results: list, ts: float, te: float) -> int:
    """第一个命中结果的排名（1-based），没命中返回 0"""
    for i, r in enumerate(results, 1):
        if _overlap(r["chunk"], ts, te):
            return i
    return 0


async def main():
    from backend.services.rag_pipeline.embedding_service import embed_texts
    from backend.services.rag_pipeline.vector_store import load_index, search

    dataset = json.load(open(DATASET, encoding="utf-8"))
    per_type = {}      # type -> {n, hit@k, mrr, widths}
    details = []
    t0 = time.time()

    for video in dataset["videos"]:
        vid, name = video["video_id"], video["name"]
        index, meta = load_index(vid)

        # 只对有时间段的题做检索评测（unanswerable 无正确答案区间）
        cases = [c for c in video["cases"] if c["type"] != "unanswerable" and c["time_start"] >= 0]
        if not cases:
            continue

        # 批量 embedding，省调用
        embs = await embed_texts([c["question"] for c in cases], video_id=vid)

        for case, emb in zip(cases, embs):
            results = search(index, meta, emb, top_k=TOP_K)
            ts, te = case["time_start"], case["time_end"]
            rank = _hit_rank(results, ts, te)

            t = case["type"]
            st = per_type.setdefault(t, {"n": 0, "hit": {1: 0, 3: 0, 5: 0}, "mrr": 0.0, "width": 0.0})
            st["n"] += 1
            st["width"] += (te - ts)
            for k in (1, 3, 5):
                if rank and rank <= k:
                    st["hit"][k] += 1
            st["mrr"] += (1.0 / rank) if rank else 0.0

            details.append({
                "id": case["id"], "video": name, "type": t,
                "question": case["question"], "time": [ts, te],
                "rank": rank,
                "top1": [round(r["chunk"]["start_time"], 1) for r in results[:1]],
                "top1_score": round(results[0]["score"], 3) if results else None,
            })
            mark = "HIT " if rank else "MISS"
            print(f"  [{mark}] r={rank} {name[:14]:14} {t:13} {case['question'][:38]}")

        print(f"--- {name} 完成")

    elapsed = time.time() - t0

    # 汇总
    print("\n" + "=" * 70)
    print(f"{'类型':<14}{'n':>4}{'R@1':>8}{'R@3':>8}{'R@5':>8}{'MRR':>8}{'区间宽度':>10}")
    print("-" * 70)
    summary = {}
    for t, st in sorted(per_type.items()):
        n = st["n"]
        row = {
            "n": n,
            "recall@1": round(st["hit"][1] / n, 3),
            "recall@3": round(st["hit"][3] / n, 3),
            "recall@5": round(st["hit"][5] / n, 3),
            "mrr": round(st["mrr"] / n, 3),
            "avg_width_s": round(st["width"] / n, 1),
        }
        summary[t] = row
        print(f"{t:<14}{n:>4}{row['recall@1']:>8.3f}{row['recall@3']:>8.3f}"
              f"{row['recall@5']:>8.3f}{row['mrr']:>8.3f}{row['avg_width_s']:>10.1f}")

    # RAG 主战场（排除对照组 visual_only）
    main_types = ["single_hop", "multi_hop", "joint"]
    tot_n = sum(summary[t]["n"] for t in main_types if t in summary)
    if tot_n:
        agg = {k: round(sum(summary[t][k] * summary[t]["n"] for t in main_types if t in summary) / tot_n, 3)
               for k in ("recall@1", "recall@3", "recall@5", "mrr")}
        print("-" * 70)
        print(f"{'RAG主战场合计':<14}{tot_n:>4}{agg['recall@1']:>8.3f}{agg['recall@3']:>8.3f}"
              f"{agg['recall@5']:>8.3f}{agg['mrr']:>8.3f}")

    json.dump({"summary": summary, "details": details, "elapsed_s": round(elapsed, 1)},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("=" * 70)
    print(f"耗时 {elapsed:.1f}s，明细已存 {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
