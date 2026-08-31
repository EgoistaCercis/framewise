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
        task = f"""从下面的聊天内容中提取值得长期记住的用户信息、偏好等，保存或更新到记忆（三层结构：类别→子类别→键值对）。

示例：
- 用户说"希望回答简洁点" → save_memory(category=preferences, subcategory=answer_style, key=style, value=简洁)
- 用户正在学 Transformer → save_memory(category=learning, subcategory=topics, key=current, value=Transformer)
- 用户是计算机专业学生 → save_memory(category=user_profile, subcategory=identity, key=major, value=计算机)

如果聊天中没有明显的用户信息或偏好，直接说明"无需更新"，不要调用工具。

用户问题：{question}
AI回答：{answer[:500]}"""
        return await self.run(task, {"smart": self.smart})
