"""
帧知 - Layer 2 生成评测

对每个问题跑一遍「纯 RAG」回答（检索 Top5 → 拼 prompt → LLM 生成），
再用 LLM-as-judge 打分：

- 忠实度 Faithfulness：回答是否忠于检索到的字幕（防编造）
- 相关性 Relevancy：是否切题、与标准答案语义一致
- 引用准确性 Citation：回答里的时间戳是否指向答案时间段（正则自动判定）
- 拒答率 Refusal：unanswerable 题是否正确拒答（幻觉抑制）

注意：
- 用干净的「单轮 RAG」路径，不注入对话历史/长期记忆，避免污染评测
- judge 与被评模型同源，存在自评偏差

用法：
    python evaluation/eval_generation.py [--limit N]
"""
import argparse
import asyncio
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # 让 judge 可导入

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DATASET = os.path.join(BASE, "evaluation", "dataset.json")
OUT = os.path.join(BASE, "evaluation", "results_generation.json")
TOP_K = 5
CONCURRENCY = 4


def _fmt(s: float) -> str:
    return f"{int(s // 60):02d}:{int(s % 60):02d}"


def build_prompt(question: str, hits: list) -> str:
    """构造干净的单轮 RAG prompt（不含对话历史/记忆）"""
    parts = []
    for r in hits:
        c = r["chunk"]
        parts.append(f"【{_fmt(c['start_time'])}~{_fmt(c['end_time'])}】{c['text']}")
    context = "\n\n".join(parts)
    return f"""以下是视频字幕中的相关片段：

{context}

用户问题：{question}

请根据以上字幕内容回答用户问题。"""


async def one_case(case: dict, video_id: str, index, meta, sem: asyncio.Semaphore) -> dict:
    from backend.services.rag_pipeline.embedding_service import embed_single
    from backend.services.rag_pipeline.vector_store import search
    from backend.services.llm.gateway import chat
    from backend.prompts import SYSTEM_PROMPT
    from backend.config import LLM_MAX_TOKENS
    import judge

    async with sem:
        t0 = time.time()
        gen_err = None
        # 生成段必须兜住：外层 gather 没开 return_exceptions，一次抖动会毁掉整场跑批
        try:
            emb = await embed_single(case["question"], video_id=video_id)
            hits = search(index, meta, emb, top_k=TOP_K)
            context = "\n\n".join(
                f"【{_fmt(r['chunk']['start_time'])}~{_fmt(r['chunk']['end_time'])}】{r['chunk']['text']}"
                for r in hits
            )
            prompt = build_prompt(case["question"], hits)
            answer, usage = await chat(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=SYSTEM_PROMPT,
                max_tokens=LLM_MAX_TOKENS,
            )
        except Exception as e:
            gen_err = f"{type(e).__name__}: {str(e)[:200]}"
            context, answer, usage = "", f"[LLM 异常] {gen_err}", {}
        latency = time.time() - t0

        rec = {
            "id": case["id"], "type": case["type"], "question": case["question"],
            "reference_answer": case["reference_answer"],
            "answer": answer, "latency_s": round(latency, 2),
            "tokens": usage.get("total_tokens", 0),
            "time_range": [case["time_start"], case["time_end"]],
            # ★ 持久化检索到的片段：retry_failed 重判时直接复用，
            #   否则重判会重新检索一遍，评的是"现在检索到的片段"而不是
            #   "当初生成答案时用的片段"——索引/字幕一变就完全不可比
            "context": context,
            "gen_error": gen_err,
        }

        rec["judge_error"] = None
        if gen_err:
            # 没有答案可判，直接返回（gen_error 已记录，统计会跳过它）
            return rec
        try:
            if case["type"] == "unanswerable":
                # 无依据题：看是否拒答
                j = await judge.judge_refusal(case["question"], answer)
                rec["refused"] = j["refused"]
                rec["refusal_reason"] = j["reason"]
            else:
                f = await judge.judge_faithfulness(context, answer)
                r = await judge.judge_relevancy(case["question"], case["reference_answer"], answer)
                c = judge.citation_hit(answer, case["time_start"], case["time_end"])
                rec.update({
                    "faithfulness": f["score"], "unsupported": f["unsupported"][:3],
                    "relevancy": r["score"], "relevancy_reason": r["reason"],
                    "has_citation": c["has_citation"], "citation_accurate": c["accurate"],
                })
        except Exception as e:
            # 裁判失败显式记录，不当作 0 分
            rec["judge_error"] = str(e)[:200]
        return rec


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 题（调试用）")
    args = ap.parse_args()

    from backend.services.rag_pipeline.vector_store import load_index

    dataset = json.load(open(DATASET, encoding="utf-8"))
    from eval_logger import setup_eval_log
    from loguru import logger
    log_path = setup_eval_log("eval_generation")
    logger.info("=== V1 生成层评测开始 ===")
    logger.info(f"评测日志: {log_path}")
    sem = asyncio.Semaphore(CONCURRENCY)
    records = []
    t0 = time.time()

    for video in dataset["videos"]:
        vid, name = video["video_id"], video["name"]
        index, meta = load_index(vid)
        cases = video["cases"]
        if args.limit:
            cases = cases[: args.limit]
        print(f"--- {name} ({len(cases)} 题)")
        recs = await asyncio.gather(*[one_case(c, vid, index, meta, sem) for c in cases])
        for rec in recs:
            q = rec["question"][:36]
            if rec.get("gen_error"):
                print(f"  [生成失败] {q}")
                logger.warning(f"生成失败 {rec['id']}: {rec['gen_error'][:80]}")
            elif rec.get("judge_error"):
                print(f"  [裁判失败] {q}")
                logger.warning(f"裁判失败 {rec['id']}: {rec['judge_error'][:80]}")
            elif rec["type"] == "unanswerable":
                print(f"  [{'拒答' if rec.get('refused') else '未拒答'}] {q}")
                logger.info(f"{rec['id']} 拒答={rec.get('refused')} 票={rec.get('refusal_votes', '')}")
            else:
                print(f"  [忠实{rec['faithfulness']:.1f} 相关{rec['relevancy']:.1f}] {q}")
                logger.info(f"{rec['id']} [{rec['type']}] 忠实={rec['faithfulness']:.3f} "
                            f"相关={rec['relevancy']:.3f} 引用准={rec.get('citation_accurate')} "
                            f"延迟={rec['latency_s']}s tokens={rec.get('tokens', 0)}")
        records.extend(recs)

    # ── 汇总 ──
    print("\n" + "=" * 78)
    print(f"{'类型':<14}{'n':>4}{'忠实度':>9}{'相关性':>9}{'引用覆盖':>10}{'引用准确':>10}{'拒答率':>9}")
    print("-" * 78)
    summary = {}
    from collections import defaultdict
    by = defaultdict(list)
    for r in records:
        by[r["type"]].append(r)

    err = [r for r in records if r.get("judge_error")]
    gen_fail = [r for r in records if r.get("gen_error")]
    if gen_fail:
        print(f"[警告] {len(gen_fail)} 条生成失败（已从统计中剔除）")
    if err:
        print(f"[警告] {len(err)} 条裁判失败（已从统计中剔除）")

    for t in ("single_hop", "multi_hop", "joint", "visual_only", "unanswerable"):
        rs = [r for r in by.get(t, [])
              if not r.get("judge_error") and not r.get("gen_error")]
        if not rs:
            continue
        n = len(rs)
        if t == "unanswerable":
            row = {"n": n, "refusal_rate": round(sum(1 for r in rs if r.get("refused")) / n, 3)}
            print(f"{t:<14}{n:>4}{'—':>9}{'—':>9}{'—':>10}{'—':>10}{row['refusal_rate']:>9.3f}")
        else:
            f = sum(r["faithfulness"] for r in rs) / n
            rel = sum(r["relevancy"] for r in rs) / n
            has_c = sum(1 for r in rs if r.get("has_citation")) / n
            acc = [r["citation_accurate"] for r in rs if r.get("has_citation")]
            acc_r = (sum(1 for a in acc if a) / len(acc)) if acc else None
            row = {"n": n, "faithfulness": round(f, 3), "relevancy": round(rel, 3),
                   "citation_coverage": round(has_c, 3),
                   "citation_accuracy": round(acc_r, 3) if acc_r is not None else None}
            print(f"{t:<14}{n:>4}{f:>9.3f}{rel:>9.3f}{has_c:>10.3f}"
                  f"{(acc_r if acc_r is not None else 0):>10.3f}{'—':>9}")
        summary[t] = row

    elapsed = time.time() - t0
    total_tokens = sum(r.get("tokens", 0) for r in records)
    json.dump({"summary": summary, "records": records,
               "elapsed_s": round(elapsed, 1), "total_tokens": total_tokens,
               "judge_failures": len(err), "gen_failures": len(gen_fail)},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("=" * 78)
    print(f"耗时 {elapsed:.1f}s，总 token {total_tokens}，明细已存 {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
