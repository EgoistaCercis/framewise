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

# ── 各模型定价 (人民币/1K tokens) ──
PRICING = {
    # DeepSeek
    "deepseek-chat":      {"input": 0.001,  "output": 0.002},   # ¥1/1M input, ¥2/1M output
    # SiliconFlow Embedding
    "BAAI/bge-m3":        {"input": 0.0007, "output": 0.0},     # ¥0.7/1M tokens
    # DashScope Qwen VL
    "qwen-vl-plus":       {"input": 0.0015, "output": 0.006},   # ¥1.5/1M input, ¥6/1M output
    # SiliconFlow ASR
    "FunAudioLLM/SenseVoiceSmall": {"input": 0.0005, "output": 0.0},  # 约 ¥0.5/1K tokens
    # DashScope Paraformer ASR
    "paraformer-v2":      {"input": 0.0008, "output": 0.0},
    # 默认
    "default":            {"input": 0.001,  "output": 0.002},
}


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
            input_cost REAL NOT NULL DEFAULT 0,
            output_cost REAL NOT NULL DEFAULT 0,
            total_cost REAL NOT NULL DEFAULT 0,
            metadata TEXT                   -- JSON: question摘要等
        )
    """)
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
):
    """记录一次 API 调用"""
    pricing = PRICING.get(model, PRICING["default"])
    input_cost = (input_tokens / 1000) * pricing["input"]
    output_cost = (output_tokens / 1000) * pricing["output"]
    total_cost = input_cost + output_cost

    conn = _get_conn()
    conn.execute("""
        INSERT INTO usage_log (timestamp, model, provider, call_type, video_id,
                               input_tokens, output_tokens, input_cost, output_cost, total_cost, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().isoformat(),
        model,
        provider,
        call_type,
        video_id,
        input_tokens,
        output_tokens,
        round(input_cost, 6),
        round(output_cost, 6),
        round(total_cost, 6),
        metadata,
    ))
    conn.commit()
    conn.close()

    logger.info(
        f"[Cost] {provider}/{model} | {call_type} | "
        f"in:{input_tokens} out:{output_tokens} | "
        f"¥{total_cost:.4f}"
    )


def get_today_stats() -> dict:
    """今日用量统计"""
    today = date.today().isoformat()
    conn = _get_conn()
    row = conn.execute("""
        SELECT
            COUNT(*) as calls,
            COALESCE(SUM(input_tokens), 0) as total_input,
            COALESCE(SUM(output_tokens), 0) as total_output,
            COALESCE(SUM(total_cost), 0) as total_cost
        FROM usage_log
        WHERE date(timestamp) = ?
    """, (today,)).fetchone()
    conn.close()

    return {
        "date": today,
        "calls": row["calls"],
        "total_input_tokens": row["total_input"],
        "total_output_tokens": row["total_output"],
        "total_cost": round(row["total_cost"], 4),
    } if row else {"date": today, "calls": 0, "total_input_tokens": 0, "total_output_tokens": 0, "total_cost": 0}


def get_total_stats() -> dict:
    """总用量统计"""
    conn = _get_conn()
    row = conn.execute("""
        SELECT
            COUNT(*) as calls,
            COALESCE(SUM(input_tokens), 0) as total_input,
            COALESCE(SUM(output_tokens), 0) as total_output,
            COALESCE(SUM(total_cost), 0) as total_cost
        FROM usage_log
    """).fetchone()
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


def get_stats_by_model() -> list[dict]:
    """按模型分组统计"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT
            model,
            provider,
            COUNT(*) as calls,
            SUM(input_tokens) as total_input,
            SUM(output_tokens) as total_output,
            SUM(total_cost) as total_cost
        FROM usage_log
        GROUP BY model, provider
        ORDER BY total_cost DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# 启动时初始化
init_db()
logger.debug(f"Usage DB initialized: {DB_PATH}")
