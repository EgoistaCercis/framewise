"""
帧知 - 记忆管理代理（Memory Agent）

独立于主 agent 的子代理，专门负责用户长期记忆的保存、更新、回忆与删除。
在主 agent 的任务闭环结束之后才被调用，根据那轮问答更新记忆。

不加载主 agent 的业务工具，只持有记忆工具（save / recall / delete）。
"""
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
