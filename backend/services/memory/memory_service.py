"""
帧知 - 记忆服务（JSON cards 三层结构）

存储格式：类别(category) -> 子类别(subcategory) -> 键值对(key: value)

SQLite 表 memory_cards，复合主键 (category, subcategory, key)。
对外提供卡片级别的增删查，以及序列化为三层嵌套 JSON 的能力。

示例三层结构：
{
  "user_profile": {
    "identity": {"role": "学生", "major": "计算机"}
  },
  "preferences": {
    "answer_style": {"style": "简洁", "language": "中文"}
  },
  "learning": {
    "topics": {"current": "Transformer"}
  }
}
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
        CREATE TABLE IF NOT EXISTS memory_cards (
            category TEXT NOT NULL,
            subcategory TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (category, subcategory, key)
        )
    """)
    db.commit()
    db.close()


def set_card(category: str, subcategory: str, key: str, value: str):
    """保存/更新一张记忆卡片"""
    db = _conn()
    db.execute(
        "INSERT INTO memory_cards (category, subcategory, key, value, updated_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(category, subcategory, key) DO UPDATE SET "
        "value = excluded.value, updated_at = excluded.updated_at",
        (category, subcategory, key, value, datetime.now().isoformat()),
    )
    db.commit()
    db.close()
    logger.debug(f"Memory card set: {category}/{subcategory}/{key} = {value[:60]}")


def delete_card(category: str, subcategory: str = None, key: str = None):
    """删除记忆卡片。

    - 仅 category：删除整个类别
    - category + subcategory：删除整个子类别
    - category + subcategory + key：删除具体键值对
    """
    db = _conn()
    if subcategory is None:
        db.execute("DELETE FROM memory_cards WHERE category = ?", (category,))
    elif key is None:
        db.execute(
            "DELETE FROM memory_cards WHERE category = ? AND subcategory = ?",
            (category, subcategory),
        )
    else:
        db.execute(
            "DELETE FROM memory_cards WHERE category = ? AND subcategory = ? AND key = ?",
            (category, subcategory, key),
        )
    db.commit()
    db.close()


def get_all_cards() -> dict:
    """返回三层嵌套 dict（JSON 树）：{category: {subcategory: {key: value}}}"""
    db = _conn()
    rows = db.execute(
        "SELECT category, subcategory, key, value FROM memory_cards "
        "ORDER BY category, subcategory, updated_at DESC"
    ).fetchall()
    db.close()

    tree = {}
    for r in rows:
        tree.setdefault(r["category"], {}).setdefault(r["subcategory"], {})[r["key"]] = r["value"]
    return tree


def format_cards_for_prompt() -> str:
    """把记忆格式化为 Prompt 上下文（体现三层结构）"""
    cards = get_all_cards()
    if not cards:
        return ""

    lines = ["\n## 关于用户（长期记忆，请参考）\n"]
    for category, subs in cards.items():
        lines.append(f"### {category}")
        for subcategory, kv in subs.items():
            items = "；".join(f"{k}={v}" for k, v in kv.items())
            lines.append(f"- {subcategory}：{items}")
    return "\n".join(lines) + "\n"


# 启动时初始化
init()
