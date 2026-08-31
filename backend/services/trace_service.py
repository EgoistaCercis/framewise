"""
帧知 - 轨迹记录服务（Trace）

append-only 记录 agent 的完整执行轨迹：用户输入、工具调用、工具结果、最终回答、错误等。
只增加不改；大内容（如工具结果）落盘到文件，数据库只存文件路径。

表结构（SQLite，只 INSERT）：
    traces(id, session_id, video_id, step, event_type, tool_name, content, content_file, timestamp)
"""
import os
import sqlite3
from datetime import datetime

from backend.config import DATA_DIR

DB_PATH = os.path.join(DATA_DIR, "trace.db")
TRACE_DIR = os.path.join(DATA_DIR, "traces")

# 超过该字符数，内容落盘到文件，DB 只存路径
MAX_INLINE = 5000


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    db = _conn()
    db.execute("""
        CREATE TABLE IF NOT EXISTS traces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            video_id TEXT,
            step INTEGER NOT NULL DEFAULT 0,
            event_type TEXT NOT NULL,   -- user / tool_call / tool_result / answer / error
            tool_name TEXT,
            content TEXT,
            content_file TEXT,          -- 大内容落盘的文件路径（相对 DATA_DIR）
            timestamp TEXT NOT NULL
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_trace_session ON traces(session_id, step)")
    db.commit()
    db.close()
    os.makedirs(TRACE_DIR, exist_ok=True)


def log_trace(session_id: str, video_id: str, step: int, event_type: str,
              content: str = "", tool_name: str = None):
    """追加一条轨迹。大内容自动落盘，DB 只存文件路径。"""
    content = content or ""
    content_file = ""
    if len(content) > MAX_INLINE:
        content_file = _dump_to_file(session_id, step, event_type, content)
        content = ""

    db = _conn()
    db.execute(
        "INSERT INTO traces (session_id, video_id, step, event_type, tool_name, content, content_file, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (session_id, video_id, step, event_type, tool_name, content, content_file, datetime.now().isoformat()),
    )
    db.commit()
    db.close()


def _dump_to_file(session_id: str, step: int, event_type: str, content: str) -> str:
    """大内容落盘，返回相对 DATA_DIR 的路径"""
    session_dir = os.path.join(TRACE_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)
    filename = f"{step:02d}_{event_type}_{datetime.now().strftime('%H%M%S%f')}.txt"
    path = os.path.join(session_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return os.path.relpath(path, DATA_DIR)


def get_session_trace(session_id: str) -> list[dict]:
    """查询某个 session 的完整轨迹"""
    db = _conn()
    rows = db.execute(
        "SELECT * FROM traces WHERE session_id = ? ORDER BY step, id",
        (session_id,),
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]


# 启动时初始化
init()
