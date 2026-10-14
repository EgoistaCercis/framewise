"""管理员视角的跨库查询：谁在用、用了多少、**问了什么**。

★ 为什么单独一个模块：下钻一次要同时碰**两个库** —— `usage.db` 的 `usage_log`
（谁、多少、何时）和 `framewise.db` 的 `conversations`（问了什么）。放进
`cost_service` 或 `conversation_service` 任何一个，都会让那个服务开始 import
对方的库路径。收在这里，"这是管理视角、跨库"只写在一处。

口径固定：**用 `usage_log.caller` 选人，用 `conversations.scope` 取内容**。
两者的换算只在"主人"那条路上不一样，见 `_scope_for_caller`。

⚠️ 这个模块**只被管理员端点调用**（`/api/admin/*`）。它不做权限判断 ——
权限在路由层（`is_admin()`），别把它当成一个可以随便调的查询接口。
"""
import sqlite3
from datetime import datetime, timedelta

from loguru import logger

# 表格里代表"没有 caller"的显示名（评测脚本、后台任务这类没有请求上下文的调用）
_UNATTRIBUTED = "(未归因)"
# 超过这么多天没来 → 标"沉寂"。7 天是拍的，但"一周没动静"对试用场景够用
_SILENT_DAYS = 7


def _conn(path: str) -> sqlite3.Connection:
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c


def _usage_conn() -> sqlite3.Connection:
    from backend.services.llm.cost_service import DB_PATH
    return _conn(DB_PATH)


def _conv_conn() -> sqlite3.Connection:
    from backend.services.rag_pipeline.conversation_service import DB_PATH
    return _conn(DB_PATH)


def _scope_for_caller(who: str) -> str:
    """把记账口径（`usage_log.caller`）翻成数据分区键（`conversations.scope`）。

    具名体验者两边都是人名，直接对齐。差额**只在"主人"那条路上**：
    usage 里记的是 `local` / `admin`（人看得懂，也能把"本机直连"和"用管理员密钥"
    分开统计），而 conversations 里存的是**空串**（历史共享桶，见 `auth.current_scope`）。

    不对齐的后果很具体：点"（我）"那一行会查出一个空列表，看起来像数据丢了。
    """
    from backend.services.auth import LOCAL_CALLER, ADMIN_CALLER
    if who in (LOCAL_CALLER, ADMIN_CALLER, _UNATTRIBUTED, ""):
        return ""
    return who


def _caller_for_query(who: str) -> str:
    """`_scope_for_caller` 的反向：把表格里的**显示名**还原成库里存的值。

    表格把空 caller 显示成 `(未归因)`，但库里存的是**空串** ——
    拿显示名直接去查，那一行永远点不出东西。
    """
    return "" if who == _UNATTRIBUTED else who


def _titles(video_ids) -> dict:
    """从内存里的 video_states 取视频标题；取不到就退回 id。

    ⚠️ 标题只在内存 + `data/video_states.json` 里，**不是所有视频都有** ——
    所以页面上要接受"有些行显示的是 id"，这也和既有的 `get_stats_by_video` 一致，
    不要为了标题去别处捞。
    """
    out = {}
    try:
        from backend.main import video_states
    except Exception:
        return out
    for vid in set(v for v in video_ids if v):
        st = video_states.get(vid) or {}
        out[vid] = st.get("title") or st.get("original_name") or vid
    return out


def _status(who: str, known: set, today_tokens: int, last_at: str, limit: int) -> str:
    """这一行的状态标签。

    ★ 这是本页**最有用的一列** —— 数字谁都能看，但"他现在还在用吗、
    密钥还作不作数、今天是不是把额度刷满了"才是管理试用者要判断的东西。
    """
    from backend.services.auth import LOCAL_CALLER, ADMIN_CALLER
    if who == _UNATTRIBUTED:
        return "未归因"                      # 评测脚本/后台任务，不是人
    if who in (LOCAL_CALLER, ADMIN_CALLER):
        return "我"
    if who not in known:
        return "已吊销"                      # 密钥已不在 .env 里
    if limit > 0 and today_tokens >= limit:
        return "今日超限"
    try:
        days = (datetime.now() - datetime.fromisoformat(last_at)).days
    except (TypeError, ValueError):
        return "活跃"                        # 时间戳坏了也别让整页崩
    if days >= _SILENT_DAYS:
        return f"沉寂 {days} 天"
    if days >= 1:
        return f"{days} 天前"
    return "今日活跃"


def list_trial_users() -> dict:
    """表格的一行一条：谁 / 调用次数 / token / 费用 / 最后活跃 / 状态。

    按人汇总**复用 `cost_service.get_stats_by_caller()`**，不另写一遍 GROUP BY ——
    那个函数的 COALESCE/NULLIF 口径（空 caller 归到"(未归因)"）跟这里必须一致。

    今日用量**一次查完**再在 Python 里分发：逐人查的话 N 个人就是 N 次查询，
    而这个页面每次打开都要跑一遍。
    """
    from backend.services.llm.cost_service import get_stats_by_caller
    from backend.services.auth import configured_names, ADMIN_CALLER, LOCAL_CALLER
    from backend.config import API_DAILY_TOKEN_LIMIT

    now = datetime.now()
    today, tomorrow = now.strftime("%Y-%m-%d"), (now + timedelta(days=1)).strftime("%Y-%m-%d")

    conn = _usage_conn()
    today_by_who = {
        r["who"]: (r["calls"], r["tok"]) for r in conn.execute(
            "SELECT COALESCE(NULLIF(caller, ''), ?) AS who, COUNT(*) AS calls, "
            "COALESCE(SUM(input_tokens + output_tokens), 0) AS tok "
            "FROM usage_log WHERE timestamp >= ? AND timestamp < ? GROUP BY who",
            (_UNATTRIBUTED, today, tomorrow))
    }
    conn.close()

    known = configured_names()
    users = []
    for r in get_stats_by_caller():
        who = r["who"]
        today_calls, today_tok = today_by_who.get(who, (0, 0))
        users.append({
            "who": who,
            "calls": r["calls"],
            "input_tokens": r["input_tokens"] or 0,
            "output_tokens": r["output_tokens"] or 0,
            "cached_tokens": r["cached_tokens"] or 0,
            "total_cost": round(r["total_cost"] or 0, 4),
            "last_at": r["last_at"],
            "today_calls": today_calls,
            "today_tokens": today_tok,
            "configured": who in known,
            # 本人那两行在页面上标出来，免得你以为来了个新用户
            "is_me": who in (ADMIN_CALLER, LOCAL_CALLER),
            "status": _status(who, known, today_tok, r["last_at"], API_DAILY_TOKEN_LIMIT),
        })

    # 按最后活跃排序，而不是按花费 —— 打开这一页首先想知道的是"现在谁在用"；
    # 花费那一列就在右边，扫一眼就能看出谁在烧钱。
    users.sort(key=lambda u: u["last_at"] or "", reverse=True)
    return {
        "generated_at": now.isoformat(),
        "daily_limit": API_DAILY_TOKEN_LIMIT,
        "silent_days": _SILENT_DAYS,
        "users": users,
    }


def drill_down(who: str, questions: int = 20, videos: int = 50) -> dict:
    """某个人：他看过的视频 + 他最近问了什么。

    两个数据源在两个库里，所以查两次再在 Python 里按 video_id 合并。

    **查不到不报 404**：密钥被吊销、视频状态被清理之后，历史提问还在 ——
    "这人以前问过什么"恰恰是最该看到的场景，不该因为现在没数据就报错。
    """
    scope = _scope_for_caller(who)
    caller = _caller_for_query(who)

    # ① 他看过的视频（按花费排 —— 贵的那几个最值得看）
    conn = _usage_conn()
    vids = [dict(r) for r in conn.execute(
        "SELECT video_id, COUNT(*) AS calls, "
        "COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens, "
        "COALESCE(SUM(total_cost), 0) AS cost, MAX(timestamp) AS last_at "
        "FROM usage_log WHERE caller = ? AND video_id IS NOT NULL AND video_id != '' "
        "GROUP BY video_id ORDER BY cost DESC LIMIT ?", (caller, videos))]
    tot = conn.execute(
        "SELECT COUNT(*) AS calls, COALESCE(SUM(input_tokens), 0) AS input_tokens, "
        "COALESCE(SUM(output_tokens), 0) AS output_tokens, "
        "COALESCE(SUM(cached_tokens), 0) AS cached_tokens, "
        "COALESCE(SUM(total_cost), 0) AS total_cost, "
        "MIN(timestamp) AS first_at, MAX(timestamp) AS last_at "
        "FROM usage_log WHERE caller = ?", (caller,)).fetchone()
    conn.close()

    # ② 他在各视频的互动轮数 + 最近提问（对话库）
    per_video, asked = {}, []
    try:
        cc = _conv_conn()
        for r in cc.execute(
                "SELECT video_id, COUNT(*) AS msg_count, MAX(timestamp) AS last_time "
                "FROM conversations WHERE scope = ? AND role = 'user' "
                "AND (content_type IS NULL OR content_type != 'summary') "
                "GROUP BY video_id", (scope,)):
            per_video[r["video_id"]] = dict(r)
        asked = [dict(r) for r in cc.execute(
            "SELECT video_id, content, timestamp FROM conversations "
            "WHERE scope = ? AND role = 'user' "
            "AND (content_type IS NULL OR content_type != 'summary') "
            "ORDER BY timestamp DESC LIMIT ?", (scope, questions))]
        cc.close()
    except sqlite3.OperationalError as e:
        # 对话库还没建表（全新部署）→ 当成"这个人没问过"，而不是让整页 500
        logger.warning(f"读对话库失败，本次下钻不含提问内容：{e}")

    titles = _titles([v["video_id"] for v in vids] + [q["video_id"] for q in asked])
    for v in vids:
        v["title"] = titles.get(v["video_id"], v["video_id"])
        c = per_video.get(v["video_id"]) or {}
        v["msg_count"] = c.get("msg_count", 0)
    for q in asked:
        q["title"] = titles.get(q["video_id"], q["video_id"])

    # 只记"看了谁的多少条"，**不记提问内容** —— 否则日志文件里会多出一份
    # 完整的用户提问副本，等于把隐私复制到另一个地方
    logger.info(f"[admin] 下钻 {who!r}：{len(vids)} 个视频 / {len(asked)} 条提问")
    return {
        "who": who,
        "scope": scope,
        # 给前端标一句"这是本机/管理员的共享桶"，免得看到空 scope 以为查错了人
        "shared_bucket": scope == "",
        "totals": dict(tot) if tot else {},
        "videos": vids,
        "recent_questions": asked,
    }
