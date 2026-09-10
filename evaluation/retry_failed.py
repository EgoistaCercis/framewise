"""
帧知 - 重试裁判失败的记录

复用 results_generation.json 里已存的答案，只对 judge_error 的记录重新裁判
（judge 内部会梯度加大 max_tokens 重试）。

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
    from backend.services.rag_pipeline.embedding_service import embed_single
    from backend.services.rag_pipeline.vector_store import search

    async with sem:
        try:
            emb = await embed_single(case["question"], video_id=video_id)
            hits = search(index, meta, emb, top_k=TOP_K)
            context = "\n\n".join(
                f"【{_fmt(r['chunk']['start_time'])}~{_fmt(r['chunk']['end_time'])}】{r['chunk']['text']}"
                for r in hits
            )
            if case["type"] == "unanswerable":
                j = await judge.judge_refusal(case["question"], rec["answer"])
                rec["refused"] = j["refused"]
                rec["refusal_reason"] = j["reason"]
            else:
                f = await judge.judge_faithfulness(context, rec["answer"])
                r = await judge.judge_relevancy(case["question"], case["reference_answer"], rec["answer"])
                c = judge.citation_hit(rec["answer"], case["time_start"], case["time_end"])
                rec.update({
                    "faithfulness": f["score"], "unsupported": f["unsupported"][:3],
                    "relevancy": r["score"], "relevancy_reason": r["reason"],
                    "has_citation": c["has_citation"], "citation_accurate": c["accurate"],
                })
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
        rs = [r for r in by.get(t, []) if not r.get("judge_error")]
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
    tasks = []
    for rec in failed:
        case, vid = case_map[rec["id"]]
        if vid not in idx_cache:
            idx_cache[vid] = load_index(vid)
        index, meta = idx_cache[vid]
        tasks.append(rejudge(rec, case, vid, index, meta, sem))
    await asyncio.gather(*tasks)

    still = [r for r in records if r.get("judge_error")]
    data["summary"] = summarize(records)
    data["judge_failures"] = len(still)
    json.dump(data, open(RESULT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print("\n" + "=" * 78)
    print(f"{'类型':<14}{'n':>4}{'忠实度':>9}{'相关性':>9}{'引用覆盖':>10}{'引用准确':>10}{'拒答率':>9}")
    print("-" * 78)
    for t, row in data["summary"].items():
        if t == "unanswerable":
            print(f"{t:<14}{row['n']:>4}{'—':>9}{'—':>9}{'—':>10}{'—':>10}{row['refusal_rate']:>9.3f}")
        else:
            ca = row.get("citation_accuracy")
            print(f"{t:<14}{row['n']:>4}{row['faithfulness']:>9.3f}{row['relevancy']:>9.3f}"
                  f"{row['citation_coverage']:>10.3f}{(ca if ca is not None else 0):>10.3f}{'—':>9}")
    print("=" * 78)
    print(f"仍有失败: {len(still)} 条")


if __name__ == "__main__":
    asyncio.run(main())
