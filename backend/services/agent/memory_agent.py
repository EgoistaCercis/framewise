"""
帧知 - 记忆管理代理（Memory Agent）

独立于主 agent 的子代理，专门负责用户长期记忆的保存、更新、回忆与删除。
在主 agent 的任务闭环结束之后才被调用，根据那轮问答更新记忆。

不加载主 agent 的业务工具，只持有记忆工具（save / recall / delete）。

本模块同时承载**记忆沉淀的策略与触发**（`should_update_memory` /
`update_memory_async`）—— 它们原先散在 `main.py` 里，那只是路由层，
不该定义"什么时候该沉淀记忆"这种领域策略。

分层说明：存储层是 `services/memory/memory_service.py`（纯 SQLite，不调 LLM），
本模块才是**代理层**（调 LLM 做提取）。所以触发逻辑放这里而不是放存储层。
"""
import asyncio

from loguru import logger

from backend import config
from backend.prompts import MEMORY_AGENT_SYSTEM_PROMPT
from backend.services.agent.agent import Agent
from backend.services.agent.tools import MEMORY_TOOLS


class MemoryAgent(Agent):
    """记忆管理代理：负责记忆的增删改查"""

    def __init__(self, max_iterations: int = 4, smart: bool = False):
        super().__init__(
            system_prompt=MEMORY_AGENT_SYSTEM_PROMPT,
            max_iterations=max_iterations,
            smart=smart,
            tools=MEMORY_TOOLS,
        )

    async def update_from_conversation(self, question: str, answer: str) -> dict:
        """在主 agent 闭环结束后，从一轮问答中提取并更新记忆。

        返回 memory agent 的执行结果 {"answer", "steps", "tool_calls"}。
        """
        task = f"""根据下面这轮问答，判断是否需要更新长期记忆。

上方 `<memory>` 里的 `subcategory/key=v` 就是**现有卡片**（已由框架注入，不用再去 recall）。
写入规则：

① **本轮对话里已经提过的事，不要再写一遍** —— 那是重复，不是强化；
② 同一件事在**不同轮次**里又出现、且值没变 → 允许再写一次**同 key 同值**（这是**强化**，会累加计数）；
③ **值要逐字复用**，不要换同义词（换措辞会被当成"用户改口了"）；
④ 需要**新建 key** 时带 `reason` 说明理由 —— 能复用已有 key 就不要新建；
⑤ 要**改动已有键的值**，只有用户在本轮**明确改口**（"以后不要用中文了"）才允许：
   这时带 `overwrite=true` 和 `reason`。**只是你的措辞变化、或你判断"应该更新"，都不算改口**
   —— 那种情况会被拒绝并记一条冲突日志，工具会告诉你 `未写入`。

示例（**正面与负面同等重要，照着判断，不要只学"该存什么"**）：
- 用户说"希望回答简洁点" → save_memory(preferences, answer_style, style, 简洁)
- 用户说"我是计算机专业的学生" → save_memory(user_profile, identity, major, 计算机)
- 用户第二次提到 HITL，值没变 → save_memory(learning, topics, hitl, HITL)　# 强化已有 key，不是新建
- 用户问"Transformer 是什么" → **无操作**（单次提问 ≠ 在学它）
- 用户问"这个函数的时间复杂度是多少" → **无操作**（视频内容，transcript 里查得到）
- 用户说"谢谢，讲得很清楚" → **无操作**（寒暄，无预测价值）

没有值得记的就直接说明「无需更新」，**不要调用任何工具**。

用户问题：{question}
AI回答：{answer[:2000]}"""
        return await self.run(task, {"smart": self.smart})


# ── 沉淀策略与触发 ────────────────────────────────────
#
# 原来每轮问答结束都无条件起一个 MemoryAgent（最多 4 轮迭代的 LLM 调用），
# 哪怕纯闲聊也要跑一趟才得出「无需更新」—— 实测「几乎每轮都落卡」，
# 既烧钱又往记忆里灌噪声（详见 项目文档/项目改进/记忆系统改造方案_20260917.md）。
#
# 两个过滤器**叠加**，不是二选一：
#   ① 关键词命中 → 立刻沉淀（覆盖绝大多数显式偏好表达）
#   ② 否则每 N 轮兜底跑一次（捞白名单漏掉的，如"我比较喜欢…"这类无触发词的表达）
_MEMORY_HINT_WORDS = (
    "以后", "别再", "不要", "不用", "我喜欢", "我不喜欢", "我是", "我学", "我在学",
    "换个说法", "简洁", "详细", "举例", "记住", "下次", "偏好", "习惯", "风格",
    "初学者", "专业", "工作", "中文", "英文", "解释一下", "讲深",
)
_MEMORY_ROUNDS: dict = {}          # video_id -> 距上次沉淀的轮数


def should_update_memory(question: str, video_id: str) -> bool:
    """本轮问答是否值得启动 MemoryAgent。

    注意这是**前置过滤**，不是判断"该不该记"——那个判断仍然归 MemoryAgent 自己。
    这里只负责挡掉明显不值得花一次 LLM 调用的轮次。
    """
    q = (question or "").strip()
    if not q:
        return False
    if any(w in q for w in _MEMORY_HINT_WORDS):
        _MEMORY_ROUNDS[video_id] = 0
        return True
    n = _MEMORY_ROUNDS.get(video_id, 0) + 1
    if n >= config.MEMORY_BATCH_ROUNDS:
        _MEMORY_ROUNDS[video_id] = 0
        return True
    _MEMORY_ROUNDS[video_id] = n
    return False


def _log_task_failure(task) -> None:
    """MemoryAgent 是 fire-and-forget 的 —— 挂了必须能看见。

    原来 create_task 没挂回调，异常被 asyncio 静默吞掉：
    记忆写不进去，而日志里一点痕迹都没有，坏了永远不知道。
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(f"MemoryAgent 执行失败（本轮记忆未更新）：{type(exc).__name__}: {exc}")


def update_memory_async(question: str, answer: str, video_id: str,
                        smart: bool = False) -> None:
    """问答闭环结束后异步更新长期记忆 —— **全产品唯一的写记忆入口**。

    各端点（插件的 /ask_agent_stream、Web 的 /ask 等）都应该调这一个函数，
    **不要再各写一份触发逻辑**：历史上就有过两条独立的写路径
    （rag_service.extract_memory），规则不一致、互相覆盖同一张表。
    """
    if not should_update_memory(question, video_id):
        return
    _t = asyncio.create_task(
        MemoryAgent(smart=smart).update_from_conversation(question, answer))
    _t.add_done_callback(_log_task_failure)
