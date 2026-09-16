"""
帧知 - 记忆服务（三层卡片结构）

存储格式：类别(category) → 子类别(subcategory) → 键值对(key: value)

设计要点（详见 项目文档/项目改进/记忆系统改造方案_20260917.md）：

1. **三种写入语义分开**（原来是统一的 `ON CONFLICT DO UPDATE`，后写必胜）：
   - NEW：新 key → 插入
   - **REINFORCE**：同 key 同值 → `hit_count += 1`、刷新 `last_seen`
     —— 这是**加权，不是写入**。原来的实现把"再次确认"和"改主意"当成同一件事，
     只刷新时间戳，既丢信息又让强度无从判断
   - REPLACE：同 key 不同值 → **只有用户明确改口才允许**，否则记冲突日志并跳过

2. **`updated_at` 只表示"最后写入"**，判断新鲜度要用 `last_seen`（最后一次被确认）。

3. **C 类（learning）有 TTL**：超过 `MEMORY_TTL_DAYS` 没再出现 → 移入归档表（**不删除**）。
   归档是**写时惰性触发**的 —— 注入发生在每轮问答上，读路径不该有写副作用。

4. **RESTORE**：写入命中归档表里已有的 key → 恢复并继承 `hit_count`，
   而不是新建（否则 archive/live 双份 + 计数清零）。
"""
import os
import sqlite3
from datetime import datetime, timedelta

from loguru import logger

from backend import config
from backend.config import DATA_DIR

DB_PATH = os.path.join(DATA_DIR, "framewise.db")

# 注入优先级：数字越小越靠前。未列出的类别排最后。
_CLASS_ORDER = {"preferences": 0, "user_profile": 1, "learning": 2}
# 有 TTL 的类别（学习主题是阶段性的；偏好与画像是长期的）
_TTL_CLASSES = ("learning",)
_ARCHIVE_REASON_TTL = "ttl_expired"


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _now() -> str:
    return datetime.now().isoformat()


def init():
    """建表 + 迁移。幂等，可对已有库重复执行。"""
    db = _conn()
    db.execute("""
        CREATE TABLE IF NOT EXISTS memory_cards (
            category TEXT NOT NULL,
            subcategory TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            hit_count INTEGER DEFAULT 1,   -- 被"再次确认"的次数（强度）
            last_seen TEXT,                -- 最后一次被确认（≠ updated_at）
            created_at TEXT,
            source TEXT,                   -- 哪一轮/什么理由写进来的
            PRIMARY KEY (category, subcategory, key)
        )
    """)
    # 迁移老库：ALTER TABLE ADD COLUMN 没有 IF NOT EXISTS，靠 PRAGMA 先查
    cols = {r[1] for r in db.execute("PRAGMA table_info(memory_cards)")}
    added = []
    for name, ddl in (("hit_count", "INTEGER DEFAULT 1"), ("last_seen", "TEXT"),
                      ("created_at", "TEXT"), ("source", "TEXT")):
        if name not in cols:
            db.execute(f"ALTER TABLE memory_cards ADD COLUMN {name} {ddl}")
            added.append(name)
    if added:
        # 老行没有这些值 → 用 updated_at 回填（只能近似，"最后写入"当"最后确认"）
        db.execute("UPDATE memory_cards SET last_seen = updated_at WHERE last_seen IS NULL")
        db.execute("UPDATE memory_cards SET created_at = updated_at WHERE created_at IS NULL")
        db.execute("UPDATE memory_cards SET hit_count = 1 WHERE hit_count IS NULL")
        logger.info(f"[memory] schema 迁移完成，补列：{added}")

    # 归档表：结构同 memory_cards + 归档时间与原因（可回查、可恢复）
    db.execute("""
        CREATE TABLE IF NOT EXISTS memory_cards_archive (
            category TEXT, subcategory TEXT, key TEXT, value TEXT,
            updated_at TEXT, hit_count INTEGER, last_seen TEXT, created_at TEXT,
            source TEXT, archived_at TEXT NOT NULL, reason TEXT,
            PRIMARY KEY (category, subcategory, key, archived_at)
        )
    """)
    # 冲突日志：REPLACE 被拒绝时落这里。
    # 不放 logger —— 要能统计"哪个 key 反复冲突"，那说明模型没理解用户意图。
    db.execute("""
        CREATE TABLE IF NOT EXISTS memory_conflicts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT, subcategory TEXT, key TEXT,
            old_value TEXT, new_value TEXT, reason TEXT,
            created_at TEXT NOT NULL
        )
    """)
    db.commit()
    db.close()


def save_card(category: str, subcategory: str, key: str, value: str,
              overwrite: bool = False, reason: str = "") -> dict:
    """保存/强化/替换一张卡，返回 {"action": ..., "old_value": ...}。

    action 取值：new / reinforced / replaced / conflict / restored / rejected
    调用方据此给模型回话 —— 尤其是 conflict，必须让模型知道"没写进去、需要用户明确改口"。
    """
    value = (value or "").strip()
    if not value:
        return {"action": "rejected", "reason": "空值"}

    db = _conn()
    cur = db.execute(
        "SELECT value FROM memory_cards WHERE category=? AND subcategory=? AND key=?",
        (category, subcategory, key)).fetchone()
    arch = db.execute(
        "SELECT hit_count FROM memory_cards_archive "
        "WHERE category=? AND subcategory=? AND key=? ORDER BY archived_at DESC LIMIT 1",
        (category, subcategory, key)).fetchone()
    now = _now()

    if cur is None and arch is not None:
        # RESTORE：恢复归档卡而不是新建，继承原强度
        db.execute(
            "INSERT INTO memory_cards (category, subcategory, key, value, updated_at, "
            "hit_count, last_seen, created_at, source) VALUES (?,?,?,?,?,?,?,?,?)",
            (category, subcategory, key, value, now,
             (arch["hit_count"] or 1) + 1, now, now, reason or "restored"))
        db.execute("DELETE FROM memory_cards_archive WHERE category=? AND subcategory=? AND key=?",
                   (category, subcategory, key))
        action, old = "restored", None
    elif cur is None:
        db.execute(
            "INSERT INTO memory_cards (category, subcategory, key, value, updated_at, "
            "hit_count, last_seen, created_at, source) VALUES (?,?,?,?,?,?,?,?,?)",
            (category, subcategory, key, value, now, 1, now, now, reason or ""))
        action, old = "new", None
    elif (cur["value"] or "").strip() == value:
        # REINFORCE：加权，不覆盖
        db.execute(
            "UPDATE memory_cards SET hit_count = COALESCE(hit_count, 1) + 1, last_seen = ? "
            "WHERE category=? AND subcategory=? AND key=?",
            (now, category, subcategory, key))
        action, old = "reinforced", cur["value"]
    elif overwrite:
        db.execute(
            "UPDATE memory_cards SET value=?, updated_at=?, last_seen=?, source=? "
            "WHERE category=? AND subcategory=? AND key=?",
            (value, now, now, reason or "user_override", category, subcategory, key))
        action, old = "replaced", cur["value"]
    else:
        # CONFLICT：不写，记日志
        db.execute(
            "INSERT INTO memory_conflicts (category, subcategory, key, old_value, new_value, "
            "reason, created_at) VALUES (?,?,?,?,?,?,?)",
            (category, subcategory, key, cur["value"], value, reason, now))
        action, old = "conflict", cur["value"]

    db.commit()
    db.close()
    if action != "conflict":
        archive_expired()          # 写时惰性归档
    logger.debug(f"Memory card {action}: {category}/{subcategory}/{key}")
    return {"action": action, "old_value": old}


def archive_expired(days: int = None) -> int:
    """把超 TTL 没再出现的 C 类卡片移入归档表（不删除），返回归档条数。

    惰性触发（每次写入后顺带扫一遍）：注入是每轮都跑的读路径，不该在那里做写操作；
    而写入本来就低频，扫描成本可忽略。
    """
    days = days if days is not None else config.MEMORY_TTL_DAYS
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    marks = ",".join("?" * len(_TTL_CLASSES))
    db = _conn()
    rows = db.execute(
        f"SELECT * FROM memory_cards WHERE category IN ({marks}) "
        f"AND COALESCE(last_seen, updated_at) < ?", (*_TTL_CLASSES, cutoff)).fetchall()
    for r in rows:
        db.execute(
            "INSERT OR REPLACE INTO memory_cards_archive (category, subcategory, key, value, "
            "updated_at, hit_count, last_seen, created_at, source, archived_at, reason) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (r["category"], r["subcategory"], r["key"], r["value"], r["updated_at"],
             r["hit_count"], r["last_seen"], r["created_at"], r["source"],
             _now(), _ARCHIVE_REASON_TTL))
        db.execute("DELETE FROM memory_cards WHERE category=? AND subcategory=? AND key=?",
                   (r["category"], r["subcategory"], r["key"]))
    if rows:
        db.commit()
        logger.info(f"[memory] 归档 {len(rows)} 张超期卡（>{days} 天未再出现）")
    db.close()
    return len(rows)


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
        db.execute("DELETE FROM memory_cards WHERE category = ? AND subcategory = ?",
                   (category, subcategory))
    else:
        db.execute("DELETE FROM memory_cards WHERE category = ? AND subcategory = ? AND key = ?",
                   (category, subcategory, key))
    db.commit()
    db.close()


def get_all_cards() -> dict:
    """返回三层嵌套 dict：{category: {subcategory: {key: value}}}（供 UI / 调试用）"""
    db = _conn()
    rows = db.execute(
        "SELECT category, subcategory, key, value FROM memory_cards "
        "ORDER BY category, subcategory, updated_at DESC").fetchall()
    db.close()
    tree = {}
    for r in rows:
        tree.setdefault(r["category"], {}).setdefault(r["subcategory"], {})[r["key"]] = r["value"]
    return tree


# ── 注入侧（读取）─────────────────────────────────────

def _est_tokens(text: str) -> int:
    """粗略估算 token（中文约 1 token / 1.5 字，与 gateway 同口径）。"""
    return int(len(text) / 1.5)


def _strength(r) -> float:
    """类内排序：强度 × 新鲜度。

    强度用 hit_count（被重复确认的次数）；
    新鲜度用 last_seen 的远近 —— 越近越靠前，但只做加权不做硬过滤，避免老卡直接消失。
    """
    try:
        seen = datetime.fromisoformat(r["last_seen"] or r["updated_at"])
        age_days = max(0.0, (datetime.now() - seen).total_seconds() / 86400)
    except Exception:
        age_days = 999.0
    return float(r["hit_count"] or 1) / (1.0 + age_days / 30.0)


def format_cards_for_prompt(budget_tokens: int = None) -> str:
    """把记忆渲染成给模型看的文本块（不含 <memory> 标签，由调用方包）。

    三条格式约束（都来自实测，见设计文档第五节）：

    1. **必须暴露 key**（`subcategory/key=value`）—— MemoryAgent 的写入契约要求
       「同一个 key 已存在时不要重复写」，看不到 key 就无法遵守，只会一直新建。
    2. **C 类（learning）只给 key、不给内容** —— 它负责"承接上下文"，不是"提供知识"；
       内容已在 transcript 里，重复注入是双份成本。
    3. **A/B 类不展示时间** —— "3 个月前说过要简洁"仍然有效，展示时间反而诱导模型打折；
       **C 类展示日期**（真会过时），且**必须用绝对日期**：
       相对时间（"3 天前"）会让 prompt 每天都不一样，前缀缓存每天至少失效一次。
    """
    if budget_tokens is None:
        budget_tokens = config.MEMORY_PROMPT_BUDGET_TOKENS
    db = _conn()
    rows = db.execute(
        "SELECT category, subcategory, key, value, hit_count, last_seen, updated_at "
        "FROM memory_cards").fetchall()
    db.close()
    if not rows:
        return ""

    groups = {}
    for r in rows:
        groups.setdefault(r["category"], []).append(r)

    # 按类别优先级输出（preferences → user_profile → learning → 其他）
    lines, used = [], 0
    for cat in sorted(groups, key=lambda c: _CLASS_ORDER.get(c, 9)):
        items = groups[cat]
        items.sort(key=_strength, reverse=True)
        is_ttl = cat in _TTL_CLASSES

        parts = []
        for r in items:
            if is_ttl:
                # 只给 key（+ 最近出现日期），不给内容
                try:
                    d = datetime.fromisoformat(r["last_seen"] or r["updated_at"]).strftime("%m-%d")
                except Exception:
                    d = "?"
                parts.append(f"{r['key']}（{d}）")
            else:
                parts.append(f"{r['subcategory']}/{r['key']}={r['value']}")

        label = _group_label(cat, items[0]["subcategory"] if is_ttl else None, is_ttl)
        seg = f"{label}：{'；'.join(parts)}"

        # 预算截断：**按类别优先级**，不是按强度统一排 —— 保证偏好永远进得去
        if used + _est_tokens(seg) > budget_tokens:
            logger.debug(f"[memory] 注入预算 {budget_tokens} token 用尽，{cat} 整组跳过")
            continue
        lines.append(seg)
        used += _est_tokens(seg)

    if not lines:
        # 极端情况：连一组都放不下 → 至少保住优先级最高的一组（截断内容而不是全丢）
        cat = sorted(groups, key=lambda c: _CLASS_ORDER.get(c, 9))[0]
        items = groups[cat]
        items.sort(key=_strength, reverse=True)
        first = items[0]
        lines = [f"{cat}：{first['subcategory']}/{first['key']}={first['value']}"]
    return "\n".join(lines)


def _group_label(cat: str, sub: str, is_ttl: bool) -> str:
    if is_ttl:
        return f"{cat}/{sub}（仅 key，不含细节）"
    return f"{cat}"


# 启动时初始化
init()
