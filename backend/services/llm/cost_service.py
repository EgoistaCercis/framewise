"""
帧知 - Token用量与费用统计服务
基于 SQLite 记录每次 API 调用的 token 消耗和费用
"""
import os
import sqlite3
from loguru import logger
from datetime import datetime, date

from backend.config import DATA_DIR

DB_PATH = os.path.join(DATA_DIR, "usage.db")

# 定价已迁移到 backend.services.pricing_service（带版本历史）


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """初始化数据库表"""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS usage_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            model TEXT NOT NULL,
            provider TEXT NOT NULL,
            call_type TEXT NOT NULL,       -- chat / embedding / vision
            video_id TEXT,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cached_tokens INTEGER NOT NULL DEFAULT 0,
            reasoning_tokens INTEGER NOT NULL DEFAULT 0,
            input_cost REAL NOT NULL DEFAULT 0,
            output_cost REAL NOT NULL DEFAULT 0,
            cache_cost REAL NOT NULL DEFAULT 0,
            reasoning_cost REAL NOT NULL DEFAULT 0,
            total_cost REAL NOT NULL DEFAULT 0,
            metadata TEXT,                  -- JSON: question摘要等
            caller TEXT                     -- 调用者（多用户归因），见 auth.current_caller()
        )
    """)
    conn.commit()
    conn.close()

    _migrate()


def _migrate():
    """给已存在的 usage_log 表补齐新增列（cached/reasoning）"""
    conn = _get_conn()
    for col, ddl in [
        ("cached_tokens", "INTEGER NOT NULL DEFAULT 0"),
        ("reasoning_tokens", "INTEGER NOT NULL DEFAULT 0"),
        ("cache_cost", "REAL NOT NULL DEFAULT 0"),
        ("reasoning_cost", "REAL NOT NULL DEFAULT 0"),
        # 调用者（取自 auth 的 ContextVar；本地直连时是 "local"，评测等无请求上下文时是 ""）
        ("caller", "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE usage_log ADD COLUMN {col} {ddl}")
        except sqlite3.OperationalError:
            pass  # 列已存在
    conn.commit()
    conn.close()


def log_usage(
    model: str,
    provider: str,
    call_type: str,
    input_tokens: int,
    output_tokens: int = 0,
    video_id: str = None,
    metadata: str = None,
    caller: str = "",
    cached_tokens: int = 0,
    reasoning_tokens: int = 0,
    timestamp: str = None,
):
    """记录一次 API 调用（含 cached/reasoning 细分，成本按调用时刻价格计算）"""
    from backend.services.llm.pricing_service import compute_cost
    cost = compute_cost(
        model, input_tokens, output_tokens,
        cached_tokens=cached_tokens, reasoning_tokens=reasoning_tokens,
        timestamp=timestamp,
    )
    ts = timestamp or datetime.now().isoformat()

    conn = _get_conn()
    conn.execute("""
        INSERT INTO usage_log (timestamp, model, provider, call_type, video_id,
                               input_tokens, output_tokens, cached_tokens, reasoning_tokens,
                               input_cost, output_cost, cache_cost, reasoning_cost, total_cost,
                               metadata, caller)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        ts, model, provider, call_type, video_id,
        input_tokens, output_tokens, cached_tokens, reasoning_tokens,
        cost["input_cost"], cost["output_cost"], cost["cache_cost"],
        cost["reasoning_cost"], cost["total_cost"], metadata, caller,
    ))
    conn.commit()
    conn.close()

    logger.info(
        f"[Cost] {provider}/{model} | {call_type} | "
        f"in:{input_tokens}(cache:{cached_tokens}) out:{output_tokens}(reason:{reasoning_tokens}) | "
        f"¥{cost['total_cost']:.4f}"
    )


def get_today_stats(caller: str = None) -> dict:
    """今日用量统计。

    `caller` 传了就只统计**这个调用者**的 —— 远程用户不该看到全站总量
    （既是隐私，也会让他误以为那是自己的用量）。不传 = 全量（管理员视角）。
    """
    today = date.today().isoformat()
    where, params = "date(timestamp) = ?", [today]
    if caller:
        where += " AND caller = ?"
        params.append(caller)
    conn = _get_conn()
    row = conn.execute(f"""
        SELECT
            COUNT(*) as calls,
            COALESCE(SUM(input_tokens), 0) as total_input,
            COALESCE(SUM(output_tokens), 0) as total_output,
            COALESCE(SUM(total_cost), 0) as total_cost
        FROM usage_log
        WHERE {where}
    """, params).fetchone()
    conn.close()

    return {
        "date": today,
        "calls": row["calls"],
        "total_input_tokens": row["total_input"],
        "total_output_tokens": row["total_output"],
        "total_cost": round(row["total_cost"], 4),
    } if row else {"date": today, "calls": 0, "total_input_tokens": 0, "total_output_tokens": 0, "total_cost": 0}


def get_total_stats(caller: str = None) -> dict:
    """总用量统计（`caller` 传了则只统计该调用者）"""
    where, params = "", []
    if caller:
        where, params = " WHERE caller = ?", [caller]
    conn = _get_conn()
    row = conn.execute(f"""
        SELECT
            COUNT(*) as calls,
            COALESCE(SUM(input_tokens), 0) as total_input,
            COALESCE(SUM(output_tokens), 0) as total_output,
            COALESCE(SUM(total_cost), 0) as total_cost
        FROM usage_log{where}
    """, params).fetchone()
    conn.close()

    return {
        "calls": row["calls"],
        "total_input_tokens": row["total_input"],
        "total_output_tokens": row["total_output"],
        "total_cost": round(row["total_cost"], 4),
    }


def get_history(limit: int = 50) -> list[dict]:
    """最近调用记录"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT * FROM usage_log
        ORDER BY timestamp DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stats_by_model(caller: str = None) -> list[dict]:
    """按模型分组统计（`caller` 传了则只看该调用者）"""
    where, params = "", []
    if caller:
        where, params = " WHERE caller = ?", [caller]
    conn = _get_conn()
    rows = conn.execute(f"""
        SELECT
            model,
            provider,
            COUNT(*) as calls,
            SUM(input_tokens) as total_input,
            SUM(output_tokens) as total_output,
            SUM(total_cost) as total_cost
        FROM usage_log{where}
        GROUP BY model, provider
        ORDER BY total_cost DESC
    """, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stats_by_video() -> list[dict]:
    """按视频分组统计（关联视频标题）"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT
            video_id,
            COUNT(*) as calls,
            SUM(input_tokens) as total_input,
            SUM(output_tokens) as total_output,
            SUM(total_cost) as total_cost
        FROM usage_log
        WHERE video_id IS NOT NULL AND video_id != ''
        GROUP BY video_id
        ORDER BY total_cost DESC
    """).fetchall()
    conn.close()

    result = []
    for r in rows:
        title = r["video_id"]
        try:
            from backend.main import video_states
            state = video_states.get(r["video_id"], {})
            title = state.get("title") or state.get("original_name") or r["video_id"]
        except Exception:
            pass
        result.append({
            "video_id": r["video_id"],
            "title": title,
            "calls": r["calls"],
            "total_input_tokens": r["total_input"],
            "total_output_tokens": r["total_output"],
            "total_cost": round(r["total_cost"], 4),
        })
    return result


def get_video_stats(video_id: str) -> dict:
    """单个视频的用量统计"""
    conn = _get_conn()
    row = conn.execute("""
        SELECT
            COUNT(*) as calls,
            COALESCE(SUM(input_tokens), 0) as total_input,
            COALESCE(SUM(output_tokens), 0) as total_output,
            COALESCE(SUM(total_cost), 0) as total_cost
        FROM usage_log
        WHERE video_id = ?
    """, (video_id,)).fetchone()
    conn.close()

    return {
        "video_id": video_id,
        "calls": row["calls"] or 0,
        "total_input_tokens": row["total_input"] or 0,
        "total_output_tokens": row["total_output"] or 0,
        "total_cost": round(row["total_cost"] or 0, 4),
    }


# 启动时初始化
init_db()
logger.debug(f"Usage DB initialized: {DB_PATH}")


# ── 按调用者归因（多用户配额用）────────────────────────
#
# ⚠️ 时序：这些查询是在**请求进行中**被调用的（鉴权中间件要判断"还能不能发"），
#    所以必须够快。SQLite 单机查询走索引，这个量级（个人部署）没问题；
#    真要做成服务，再加内存缓存或 Redis。

def tokens_used_today(caller: str) -> int:
    """某个调用者**当天**已消耗的 token（输入+输出）。

    用于每日配额判断。按自然日、本地时区（timestamp 存的就是本地 iso 格式）。
    """
    if not caller:
        return 0
    conn = _get_conn()
    row = conn.execute(
        "SELECT COALESCE(SUM(input_tokens + output_tokens), 0) AS t "
        "FROM usage_log WHERE caller = ? AND substr(timestamp, 1, 10) = ?",
        (caller, datetime.now().strftime("%Y-%m-%d"))).fetchone()
    conn.close()
    return int(row["t"] or 0)


def get_stats_by_caller() -> list:
    """按调用者汇总用量与成本（谁用了多少）。

    caller 为空的行是"无请求上下文"的调用（评测脚本、后台任务等），
    单独归到 (未归因) 一项，不要和真实用户混在一起看。
    """
    conn = _get_conn()
    rows = conn.execute("""
        SELECT COALESCE(NULLIF(caller, ''), '(未归因)') AS who,
               COUNT(*) AS calls,
               SUM(input_tokens) AS input_tokens,
               SUM(output_tokens) AS output_tokens,
               SUM(cached_tokens) AS cached_tokens,
               SUM(total_cost) AS total_cost,
               MAX(timestamp) AS last_at
        FROM usage_log GROUP BY who ORDER BY total_cost DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]
