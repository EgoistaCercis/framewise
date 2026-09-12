"""
帧知 - 评测日志

给每个评测脚本配置**独立日志文件**，便于事后追溯「每一题发生了什么」——
比如 Agent 在第几题、调了几次视觉工具、看了哪些时间点、裁判给了什么分。

与生产日志（`data/logs/`）分离，落在 `evaluation/logs/`。

用法：
    from eval_logger import setup_eval_log
    log_path = setup_eval_log("eval_v4_agent")
    logger.info("...")        # 正常用 loguru 即可
"""
import os
import time

from loguru import logger

EVAL_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")

_FMT = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <7} | {name}:{function}:{line} | {message}"

# httpx/httpcore/openai 的 DEBUG 会打出完整的 HTTP 请求响应体，极度噪音
# （实测 2 分钟就 1MB），评测日志里只保留自己打的业务日志。
_NOISY = ("httpx", "httpcore", "openai", "urllib3", "asyncio", "dashscope", "faiss")

def _keep(record) -> bool:
    return not record["name"].startswith(_NOISY)


def setup_eval_log(script_name: str, *, level: str = "INFO") -> str:
    """为当前评测脚本挂一个独立日志文件，返回日志路径。

    - **追加 sink**，不影响 config.py 已配置的控制台/生产日志
    - 过滤掉 HTTP 客户端等库的噪音日志

    ⚠️ 顺序陷阱：`backend/config.py` 在模块顶层会调用 `logger.remove()`，
    清空**所有**已注册的 handler。如果本函数在 backend 被 import 之前调用，
    刚加的文件 sink 会在之后被静默清掉——日志文件只剩开头两行，
    而跑批本身完全正常，非常难发现（V3 就中过这个招）。

    所以这里主动先把 backend.config 拉进来，确保那次 `remove()` 已经发生过。
    """
    try:
        import backend.config  # noqa: F401  —— 触发其 logger.remove()，必须早于本次 add
    except Exception:
        pass

    os.makedirs(EVAL_LOG_DIR, exist_ok=True)
    path = os.path.join(EVAL_LOG_DIR, f"{script_name}_{time.strftime('%Y%m%d_%H%M%S')}.log")
    logger.add(path, format=_FMT, level=level, encoding="utf-8", enqueue=True, filter=_keep)
    return path


def log_question(case: dict, *, idx: int = None, total: int = None) -> None:
    """记录一题的开始（统一的格式，便于 grep）"""
    tag = f"[{idx}/{total}]" if idx and total else ""
    logger.info(f"── {tag} {case.get('id', '?')} ({case.get('type', '?')}) {case.get('question', '')[:60]}")


def log_tool_calls(calls: list) -> None:
    """记录一次问答里 Agent 的工具调用（视觉调用的时间点、成败）"""
    if not calls:
        logger.info("   工具调用: 无")
        return
    parts = []
    for c in calls:
        ts = c.get("ts")
        mark = "✓" if c.get("ok") else "✗"
        parts.append(f"{mark}{ts:.0f}s" if isinstance(ts, (int, float)) else f"{mark}{ts}")
    logger.info(f"   工具调用 {len(calls)} 次: {' '.join(parts)}")
