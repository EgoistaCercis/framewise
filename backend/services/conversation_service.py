"""
帧知 - 对话管理服务
多轮对话存储、上下文加载
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
    """初始化表"""
    db = _conn()
    db.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_conv_video ON conversations(video_id, timestamp)")
    db.commit()
    db.close()


def save_message(video_id: str, role: str, content: str):
    """保存一条对话"""
    db = _conn()
    db.execute(
        "INSERT INTO conversations (video_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
        (video_id, role, content, datetime.now().isoformat())
    )
    db.commit()
    db.close()
    logger.debug(f"[{video_id}] {role}: {content[:60]}...")


def save_exchange(video_id: str, question: str, answer: str):
    """保存一轮问答"""
    save_message(video_id, "user", question)
    save_message(video_id, "assistant", answer)


def get_history(video_id: str, limit: int = 20) -> list[dict]:
    """获取视频的对话历史"""
    db = _conn()
    rows = db.execute(
        "SELECT role, content, timestamp FROM conversations "
        "WHERE video_id = ? ORDER BY timestamp ASC LIMIT ?",
        (video_id, limit)
    ).fetchall()
    db.close()
    return [{"role": r["role"], "content": r["content"], "timestamp": r["timestamp"]} for r in rows]


def get_recent_context(video_id: str, turns: int = 5) -> str:
    """
    获取最近 N 轮对话上下文，用于拼入 Prompt

    返回格式化的上下文文本，如果没有历史则返回空字符串
    """
    msgs = get_history(video_id, limit=turns * 2)  # user + assistant = 2 per turn
    if not msgs:
        return ""

    lines = ["\n## 之前的对话上下文（请结合上下文理解用户的追问）\n"]
    for m in msgs:
        role_label = "用户" if m["role"] == "user" else "帧知"
        lines.append(f"**{role_label}**：{m['content']}")
    return "\n".join(lines) + "\n"


def list_conversations() -> list[dict]:
    """列出所有有对话记录的视频，按最近更新排序"""
    db = _conn()
    rows = db.execute("""
        SELECT
            video_id,
            COUNT(*) as msg_count,
            MAX(timestamp) as last_time,
            (SELECT content FROM conversations c2
             WHERE c2.video_id = conv.video_id ORDER BY timestamp ASC LIMIT 1
            ) as first_question
        FROM conversations conv
        GROUP BY video_id
        ORDER BY last_time DESC
        LIMIT 100
    """).fetchall()
    db.close()

    result = []
    for r in rows:
        # 尝试从 video_states 获取视频标题
        from backend.main import video_states
        state = video_states.get(r["video_id"], {})
        title = state.get("original_name") or r["video_id"]
        result.append({
            "video_id": r["video_id"],
            "title": title,
            "msg_count": r["msg_count"],
            "last_time": r["last_time"],
            "first_question": r["first_question"],
        })
    return result


# 启动时初始化
init()
