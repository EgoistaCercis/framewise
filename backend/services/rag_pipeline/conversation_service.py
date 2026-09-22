"""
帧知 - 对话管理服务
多轮对话存储、上下文压缩（四层策略）
"""
import os
import sqlite3
from datetime import datetime
from loguru import logger

from backend.config import (
    DATA_DIR, CONTEXT_MAX_MESSAGES, CONTEXT_MAX_TOKENS,
    TOOL_TRIM_LENGTH,
)

DB_PATH = os.path.join(DATA_DIR, "framewise.db")


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _scope() -> str:
    """当前调用者的数据分区键（见 auth.current_scope）。

    空串 = 本机直连 / 无请求上下文，沿用历史上那份共享的对话。
    读写都从 ContextVar 取 —— 写入点在 main.py 的各个路由里，
    漏一个就会把别人的问题存进自己的历史。
    """
    from backend.services.auth import current_scope
    return current_scope()


def init():
    """初始化表（含自动迁移）"""
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
    # 迁移：添加 content_type 字段（v2）
    try:
        db.execute("ALTER TABLE conversations ADD COLUMN content_type TEXT DEFAULT 'message'")
    except sqlite3.OperationalError:
        pass  # 已存在
    # 迁移：添加 references 字段（v3）
    try:
        db.execute("ALTER TABLE conversations ADD COLUMN references_json TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass  # 已存在
    # 迁移：添加 scope 字段（v4，多人共用后端时的数据隔离）。
    # 这里**不需要重建表**（不像 memory_cards）—— conversations 的主键是自增 id，
    # scope 只是查询维度，不参与唯一性。
    # 老行自动落到默认值 ''，也就是本机/历史那份共享桶，行为与改动前一致。
    try:
        db.execute("ALTER TABLE conversations ADD COLUMN scope TEXT NOT NULL DEFAULT ''")
    except sqlite3.OperationalError:
        pass  # 已存在
    db.execute("CREATE INDEX IF NOT EXISTS idx_conv_video ON conversations(video_id, timestamp)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_conv_scope "
               "ON conversations(scope, video_id, timestamp)")
    db.commit()
    db.close()


def save_message(video_id: str, role: str, content: str, content_type: str = "message",
                 references: list = None):
    """保存一条对话"""
    import json as _json
    refs_json = _json.dumps(references, ensure_ascii=False) if references else ""
    db = _conn()
    db.execute(
        "INSERT INTO conversations "
        "(scope, video_id, role, content, content_type, references_json, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (_scope(), video_id, role, content, content_type, refs_json, datetime.now().isoformat())
    )
    db.commit()
    db.close()
    logger.debug(f"[{video_id}] {role} ({content_type}): {content[:60]}...")


def save_exchange(video_id: str, question: str, answer: str, references: list = None):
    """保存一轮问答"""
    save_message(video_id, "user", question)
    save_message(video_id, "assistant", answer, references=references)


def list_conversations() -> list[dict]:
    """列出所有有对话记录的视频，按最近更新排序"""
    db = _conn()
    rows = db.execute("""
        SELECT
            video_id,
            COUNT(*) as msg_count,
            MAX(timestamp) as last_time
        FROM conversations
        WHERE scope = ? AND (content_type IS NULL OR content_type != 'summary')
        GROUP BY video_id
        ORDER BY last_time DESC
        LIMIT 100
    """, (_scope(),)).fetchall()
    db.close()

    result = []
    for r in rows:
        from backend.main import video_states
        state = video_states.get(r["video_id"], {})
        title = state.get("original_name") or r["video_id"]
        result.append({
            "video_id": r["video_id"],
            "title": title,
            "msg_count": r["msg_count"],
            "last_time": r["last_time"],
        })
    return result


def get_history(video_id: str, limit: int = 20) -> list[dict]:
    """获取视频的对话历史"""
    import json as _json
    db = _conn()
    rows = db.execute(
        "SELECT role, content, content_type, references_json, timestamp FROM conversations "
        "WHERE scope = ? AND video_id = ? AND content_type != 'summary' "
        "ORDER BY timestamp ASC LIMIT ?",
        (_scope(), video_id, limit)
    ).fetchall()
    db.close()
    result = []
    for r in rows:
        refs = []
        if r["references_json"]:
            try:
                refs = _json.loads(r["references_json"])
            except (ValueError, TypeError):
                refs = []
        result.append({
            "role": r["role"],
            "content": r["content"],
            "content_type": r["content_type"] or "message",
            "references": refs,
            "timestamp": r["timestamp"],
        })
    return result


# ═══════════════════════════════════════════
# 上下文压缩（四层策略）
# ═══════════════════════════════════════════

def _estimate_tokens(msgs: list[dict]) -> int:
    """估算消息的 token 数（中文约 1 token/1.5 字）"""
    total = 0
    for m in msgs:
        total += len(m.get("content", "")) // 1.5
    return int(total)


def _trim_tool(content: str) -> str:
    """第2层：裁剪工具输出"""
    if len(content) <= TOOL_TRIM_LENGTH:
        return content
    return content[:TOOL_TRIM_LENGTH] + "\n\n（工具输出已裁剪，完整内容可查看原始消息）"


def _load_all_messages(video_id: str) -> list[dict]:
    """加载所有非摘要消息（用于压缩前分析）"""
    db = _conn()
    rows = db.execute(
        "SELECT role, content, content_type FROM conversations "
        "WHERE scope = ? AND video_id = ? ORDER BY timestamp ASC",
        (_scope(), video_id)
    ).fetchall()
    db.close()
    return [
        {"role": r["role"], "content": r["content"], "content_type": r["content_type"] or "message"}
        for r in rows
    ]


async def get_recent_context(video_id: str) -> str:
    """
    获取压缩后的对话上下文（四层策略）

    1. 条数限制（50条）
    2. 工具消息裁剪
    3. LLM 摘要（超 80% token）
    4. 应急截断
    """
    if not video_id:
        return ""

    # 压缩的**执行**交给 CompressAgent（本函数只决定"什么时候压、压哪段"）
    from backend.services.agent.compress_agent import CompressAgent
    _compressor = CompressAgent()

    # 加载所有消息
    all_msgs = _load_all_messages(video_id)
    if not all_msgs:
        return ""

    # 第1层：条数上限
    if len(all_msgs) > CONTEXT_MAX_MESSAGES:
        all_msgs = all_msgs[-CONTEXT_MAX_MESSAGES:]

    # 第2层：工具消息裁剪
    for m in all_msgs:
        if m.get("content_type") == "tool":
            m["content"] = _trim_tool(m["content"])

    # 第3层：LLM 摘要（token 超 80% 时）
    tokens = _estimate_tokens(all_msgs)
    if tokens > CONTEXT_MAX_TOKENS:
        logger.info(f"[{video_id}] Context {tokens} tokens exceeds {CONTEXT_MAX_TOKENS}, summarizing...")
        try:
            summary = await _compressor.summarize_history(all_msgs)
            if summary:
                # 用摘要替换最旧的 60% 消息
                cut = int(len(all_msgs) * 0.6)
                all_msgs = all_msgs[cut:]
                all_msgs.insert(0, {
                    "role": "system",
                    "content": summary,
                    "content_type": "summary",
                })
                logger.info(f"[{video_id}] Summarized to {_estimate_tokens(all_msgs)} tokens")
        except Exception as e:
            logger.warning(f"[{video_id}] Summarization failed: {e}")

        # 如果摘要后仍然超标 → 继续摘要
        for _ in range(3):  # 最多尝试3次
            if _estimate_tokens(all_msgs) <= CONTEXT_MAX_TOKENS:
                break
            # 再摘要最旧的 20%
            cut = max(1, int(len(all_msgs) * 0.2))
            older = all_msgs[:cut]
            newer = all_msgs[cut:]
            try:
                summary = await _compressor.summarize_history(older)
                if summary:
                    newer.insert(0, {"role": "system", "content": summary, "content_type": "summary"})
                all_msgs = newer
            except Exception:
                break

    # 第4层：应急截断（最终兜底）
    while _estimate_tokens(all_msgs) > CONTEXT_MAX_TOKENS and len(all_msgs) > 1:
        all_msgs.pop(0)  # 丢弃最旧的一条

    # 拼接成 Prompt 格式
    lines = ["\n## 之前的对话上下文（请结合上下文理解用户的追问）\n"]
    for m in all_msgs:
        ct = m.get("content_type", "message")
        if ct == "summary":
            lines.append(f"（**对话摘要**：{m['content']}）")
        else:
            role_label = "用户" if m["role"] == "user" else "帧知"
            lines.append(f"**{role_label}**：{m['content']}")

    return "\n".join(lines) + "\n"


# 启动时初始化（与 memory_service / cost_service 同一套约定：import 即建表）
#
# ★ 这一行原本是**缺失**的：`init()` 写好了却从来没人调用。今天不出问题纯粹是因为
#   开发机的库早就建过表了 —— 全新部署（服务器上 data/ 干净时）第一条对话就会
#   `no such table: conversations`。加 scope 列也依赖它，所以一并补上。
init()

