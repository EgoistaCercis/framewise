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


def _norm_ranges(ts, te) -> list:
    """统一成 [(start, end), ...]。

    multi_hop 题的 time_start / time_end 是并列列表（索引一一对应多个答案片段），
    其余类型是单值。这里统一成区间列表，便于统一判定。
    """
    if isinstance(ts, list):
        return list(zip(ts, te))
    return [(ts, te)]


def _overlap(chunk: dict, s: float, e: float) -> bool:
    """检索到的 chunk 与某个答案区间是否有交集"""
    return chunk["start_time"] <= e and chunk["end_time"] >= s


def _hit_info(results: list, ranges: list) -> tuple:
    """返回 (首个命中的排名, 命中的片段索引集合)

    - 排名用于 MRR（第一个命中的位置）
    - 片段集合用于多片段覆盖率（multi_hop 是否每个片段都检索到了）
    """
    hit_segs, first_rank = set(), 0
    for i, r in enumerate(results, 1):
        for si, (s, e) in enumerate(ranges):
            if _overlap(r["chunk"], s, e):
                hit_segs.add(si)
                if first_rank == 0:
                    first_rank = i
    return first_rank, hit_segs


async def main():
    from backend.services.rag_pipeline.embedding_service import embed_texts
    from backend.services.rag_pipeline.vector_store import load_index, search

    dataset = json.load(open(DATASET, encoding="utf-8"))
    from eval_logger import setup_eval_log
    from loguru import logger
    log_path = setup_eval_log("eval_retrieval")
    logger.info("=== V1 检索层评测开始 ===")
    logger.info(f"评测日志: {log_path}")
    per_type = {}      # type -> {n, hit@k, mrr, widths}
    details = []
    t0 = time.time()

    for video in dataset["videos"]:
        vid, name = video["video_id"], video["name"]
        index, meta = load_index(vid)

        # 只对有时间段的题做检索评测（unanswerable 无正确答案区间）
        cases = []
        for c in video["cases"]:
            if c["type"] == "unanswerable":
                continue
            ranges = _norm_ranges(c["time_start"], c["time_end"])
            if any(s < 0 or e < 0 for s, e in ranges):
                continue
            cases.append((c, ranges))
        if not cases:
            continue

        # 批量 embedding，省调用
        embs = await embed_texts([c["question"] for c, _ in cases], video_id=vid)

        for (case, ranges), emb in zip(cases, embs):
            results = search(index, meta, emb, top_k=TOP_K)
            rank, hit_segs = _hit_info(results, ranges)
            n_seg = len(ranges)
            coverage = len(hit_segs) / n_seg

            t = case["type"]
            st = per_type.setdefault(t, {
                "n": 0, "hit": {1: 0, 3: 0, 5: 0}, "mrr": 0.0, "width": 0.0,
                "seg_total": 0, "seg_hit": 0, "full": 0,
            })
            st["n"] += 1
            st["width"] += sum(e - s for s, e in ranges)
            st["seg_total"] += n_seg
            st["seg_hit"] += len(hit_segs)
            if len(hit_segs) == n_seg:
                st["full"] += 1
            for k in (1, 3, 5):
                if rank and rank <= k:
                    st["hit"][k] += 1
            st["mrr"] += (1.0 / rank) if rank else 0.0

            details.append({
                "id": case["id"], "video": name, "type": t,
                "question": case["question"], "ranges": ranges,
                "rank": rank, "segments": n_seg,
                "segments_hit": len(hit_segs), "coverage": round(coverage, 3),
                "top1": [round(r["chunk"]["start_time"], 1) for r in results[:1]],
                "top1_score": round(results[0]["score"], 3) if results else None,
            })
            mark = "HIT " if rank else "MISS"
            cov = f" 覆盖{len(hit_segs)}/{n_seg}" if n_seg > 1 else ""
            print(f"  [{mark}] r={rank}{cov} {name[:14]:14} {t:13} {case['question'][:34]}")
            logger.info(f"{mark} rank={rank} 覆盖{len(hit_segs)}/{n_seg} [{t}] "
                        f"{case['id']} {case['question'][:50]}")

        print(f"--- {name} 完成")

    elapsed = time.time() - t0

    # 汇总
    print("\n" + "=" * 92)
    print(f"{'类型':<14}{'n':>4}{'R@1':>8}{'R@3':>8}{'R@5':>8}{'MRR':>8}"
          f"{'片段覆盖':>10}{'全片段命中':>12}{'区间宽':>8}")
    print("-" * 92)
    summary = {}
    for t, st in sorted(per_type.items()):
        n = st["n"]
        cov = st["seg_hit"] / st["seg_total"] if st["seg_total"] else 0.0
        full = st["full"] / n
        row = {
            "n": n,
            "recall@1": round(st["hit"][1] / n, 3),
            "recall@3": round(st["hit"][3] / n, 3),
            "recall@5": round(st["hit"][5] / n, 3),
            "mrr": round(st["mrr"] / n, 3),
            "segment_coverage": round(cov, 3),
            "full_segment_hit_rate": round(full, 3),
            "avg_width_s": round(st["width"] / n, 1),
        }
        summary[t] = row
        print(f"{t:<14}{n:>4}{row['recall@1']:>8.3f}{row['recall@3']:>8.3f}{row['recall@5']:>8.3f}"
              f"{row['mrr']:>8.3f}{cov:>10.3f}{full:>12.3f}{row['avg_width_s']:>8.1f}")

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
