"""
帧知 - 记忆服务
简单的 key-value 长期记忆，存用户偏好和学习主题
"""
import os
import sqlite3
from datetime import datetime
from loguru import logger

from backend.config import DATA_DIR

DB_PATH = os.path.join(DATA_DIR, "framewise.db")


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    db = _conn()
    db.execute("""
        CREATE TABLE IF NOT EXISTS memory (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    db.commit()
    db.close()


def set_memory(key: str, value: str):
    """保存/更新一条记忆"""
    db = _conn()
    db.execute(
        "INSERT INTO memory (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, value, datetime.now().isoformat())
    )
    db.commit()
    db.close()
    logger.debug(f"Memory set: {key} = {value[:60]}")


def get_memory(key: str) -> str | None:
    db = _conn()
    row = db.execute("SELECT value FROM memory WHERE key = ?", (key,)).fetchone()
    db.close()
    return row["value"] if row else None


def get_all_memories() -> list[dict]:
    db = _conn()
    rows = db.execute("SELECT key, value FROM memory ORDER BY updated_at DESC").fetchall()
    db.close()
    return [{"key": r["key"], "value": r["value"]} for r in rows]


def delete_memory(key: str):
    db = _conn()
    db.execute("DELETE FROM memory WHERE key = ?", (key,))
    db.commit()
    db.close()


def format_memories_for_prompt() -> str:
    """把记忆格式化为 Prompt 上下文"""
    memories = get_all_memories()
    if not memories:
        return ""

    lines = ["\n## 关于用户（长期记忆，请参考）\n"]
    for m in memories:
        lines.append(f"- {m['value']}")
    return "\n".join(lines) + "\n"


# 启动时初始化
init()
