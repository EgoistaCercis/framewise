"""
帧知 - 评测成本核算

背景：评测脚本一直只记 **token 数**和**延迟**，从来没算过钱，也没把视觉调用的
开销纳入对比。于是「RAG vs 全量字幕 vs +视觉 vs Agent」这条链上，
**成本维度是缺失的**——只有延迟可比。

好在这条信息并没有丢：`backend.services.llm.cost_service` 会把**每一次** API 调用
（含视觉）写进 `data/usage.db`，带 token 明细和当时的成交价。
本脚本按**时间窗口**把某次评测跑批的账捞出来。

为什么按时间窗口而不是让评测脚本自己记账：
- 视觉调用发生在工具内部，评测脚本的 usage 拦截（patch chat_with_tools）够不到它
- 一次跑批横跨 chat / vision 两类调用，窗口切分是唯一能覆盖全部开销的口径

用法：
    python evaluation/eval_cost.py --start "2026-09-13 02:59:09" --end "2026-09-13 03:14:11" --label "V4 自主视觉"
    python evaluation/eval_cost.py --last-log eval_v3_full_vision   # 从日志文件自动取窗口
"""
import argparse
import os
import re
import sqlite3
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from backend.config import DATA_DIR  # noqa: E402

DB = os.path.join(DATA_DIR, "usage.db")
LOG_DIR = os.path.join(BASE, "evaluation", "logs")

# usage.db 里存的是 ISO 格式（带 T），命令行给人看的是空格分隔 —— 统一转换
def _iso(ts: str) -> str:
    return ts.strip().replace(" ", "T")


def window_from_log(prefix: str) -> tuple:
    """从评测日志文件名/首末行推出跑批的时间窗口"""
    cands = sorted(f for f in os.listdir(LOG_DIR) if f.startswith(prefix) and f.endswith(".log"))
    if not cands:
        raise SystemExit(f"没找到 {prefix}*.log")
    path = os.path.join(LOG_DIR, cands[-1])
    stamps = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
            if m:
                stamps.append(m.group(1))
    if not stamps:
        raise SystemExit(f"{path} 里没解析到时间戳")
    return stamps[0], stamps[-1], path


def report(start: str, end: str, label: str, questions: int = 0) -> dict:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT call_type, COUNT(*) n,
                  SUM(input_tokens) i, SUM(output_tokens) o, SUM(cached_tokens) ck,
                  ROUND(SUM(input_cost), 6) ic, ROUND(SUM(output_cost), 6) oc,
                  ROUND(SUM(cache_cost), 6) cc, ROUND(SUM(total_cost), 6) tc
           FROM usage_log WHERE timestamp >= ? AND timestamp <= ?
           GROUP BY call_type ORDER BY tc DESC""",
        (_iso(start), _iso(end)),
    ).fetchall()
    conn.close()

    print("=" * 78)
    print(f"{label}    窗口 {start} → {end}")
    print("-" * 78)
    print(f"{'调用类型':<10}{'次数':>6}{'input':>11}{'output':>10}{'cached':>10}{'费用(¥)':>12}")
    total = 0.0
    vision_n = 0
    for r in rows:
        print(f"{r['call_type']:<10}{r['n']:>6}{r['i'] or 0:>11,}{r['o'] or 0:>10,}"
              f"{r['ck'] or 0:>10,}{r['tc'] or 0:>12.4f}")
        total += r["tc"] or 0
        if r["call_type"] == "vision":
            vision_n = r["n"]
    print("-" * 78)
    if questions:
        print(f"{'合计':<10}{'':>6}{'':>11}{'':>10}{'':>10}{total:>12.4f}"
              f"   （{questions} 题 → 每题 ¥{total/questions:.4f}）")
    else:
        print(f"{'合计':<10}{'':>6}{'':>11}{'':>10}{'':>10}{total:>12.4f}")
    print(f"视觉调用 {vision_n} 次")
    return {"total": round(total, 6), "vision_calls": vision_n}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--label", default="跑批")
    ap.add_argument("--questions", type=int, default=0)
    ap.add_argument("--last-log", help="从该前缀最新的评测日志自动取时间窗口")
    a = ap.parse_args()

    if a.last_log:
        s, e, path = window_from_log(a.last_log)
        print(f"（窗口取自 {os.path.basename(path)}）")
    else:
        if not (a.start and a.end):
            raise SystemExit("需要 --start/--end，或用 --last-log 自动取")
        s, e = a.start, a.end
    report(s, e, a.label, a.questions)
