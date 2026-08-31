"""
帧知 - Agent Loop（ReAct 风格）

让 LLM 通过「调用工具 → 观察结果 → 再调用工具」的多步循环完成复杂任务，
而非单轮问答。

用法：
    from backend.services.agent.agent import Agent
    agent = Agent(smart=False)
    result = await agent.run(user_message, context)
    # result = {"answer": str, "steps": int, "tool_calls": [...]}
"""
import json

from loguru import logger

from backend.prompts import AGENT_SYSTEM_PROMPT
from backend.services.agent.tools import get_tools_openai, get_tool
from backend.services.llm import gateway


def _load_memory_context() -> str:
    """加载长期记忆（默认全量）。作为独立消息注入 messages 列表，避免污染 system prompt 缓存。"""
    try:
        from backend.services.memory.memory_service import format_cards_for_prompt
        return format_cards_for_prompt()
    except Exception:
        return ""


class Agent:
    """工具型 Agent，循环调用 LLM 与工具直到产出最终答案"""

    def __init__(self, system_prompt: str = None, max_iterations: int = 8,
                 smart: bool = False, tools: list = None):
        self.system_prompt = system_prompt or AGENT_SYSTEM_PROMPT
        self.max_iterations = max_iterations
        self.smart = smart
        self.tools_openai = get_tools_openai(tools)

    def _build_messages(self, user_message: str) -> list[dict]:
        """构造初始消息列表。长期记忆作为独立消息（XML 标签）注入，
        保持 system prompt 稳定以命中前缀缓存。"""
        messages = []
        memory_text = _load_memory_context()
        if memory_text:
            messages.append({
                "role": "user",
                "content": f"<memory>\n{memory_text.strip()}\n</memory>",
            })
        messages.append({"role": "user", "content": user_message})
        return messages

    async def run(self, user_message: str, context: dict) -> dict:
        """执行 agent loop，返回 {"answer", "steps", "tool_calls"}"""
        messages = self._build_messages(user_message)
        tool_call_log = []

        for step in range(1, self.max_iterations + 1):
            message, usage = await gateway.chat_with_tools(
                messages,
                system_prompt=self.system_prompt,
                tools=self.tools_openai,
                smart=self.smart,
            )

            # 无工具调用 → 最终答案
            if not message["tool_calls"]:
                return {
                    "answer": message["content"],
                    "steps": step,
                    "tool_calls": tool_call_log,
                }

            # 记录本轮 tool_calls 并构造 assistant 消息
            tool_calls = message["tool_calls"]
            tool_call_log.extend([tc["name"] for tc in tool_calls])
            messages.append(self._assistant_message(message))

            # 逐个执行工具，结果作为 tool 消息回填
            for tc in tool_calls:
                result = await self._execute_tool(tc, context)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })

        logger.warning(f"Agent 达到最大迭代轮次 {self.max_iterations}，强制结束")
        return {
            "answer": "抱歉，这个问题比较复杂，我尝试了多次仍未完成。请换一种方式提问。",
            "steps": self.max_iterations,
            "tool_calls": tool_call_log,
        }

    async def run_stream(self, user_message: str, context: dict):
        """流式 agent loop，yield 事件 dict：
        - {"type": "content", "delta": str}    最终答案 token
        - {"type": "tool", "name": str}        工具调用状态
        - {"type": "done", "answer", "steps", "tool_calls"}  结束
        - {"type": "error", "message": str}    错误
        """
        messages = self._build_messages(user_message)
        tool_call_log = []

        for step in range(1, self.max_iterations + 1):
            full_content = ""
            tool_calls = []
            try:
                async for event in gateway.chat_with_tools_stream(
                    messages,
                    system_prompt=self.system_prompt,
                    tools=self.tools_openai,
                    smart=self.smart,
                ):
                    if event["type"] == "content":
                        full_content += event["delta"]
                        yield {"type": "content", "delta": event["delta"]}
                    elif event["type"] == "done":
                        tool_calls = event["tool_calls"]
            except RuntimeError as e:
                yield {"type": "error", "message": str(e)}
                return

            # 无工具调用 → 最终答案（content 已流式 yield）
            if not tool_calls:
                yield {"type": "done", "answer": full_content, "steps": step, "tool_calls": tool_call_log}
                return

            # 工具轮：推送工具状态并执行
            tool_call_log.extend([tc["name"] for tc in tool_calls])
            messages.append(self._assistant_message({"content": full_content, "tool_calls": tool_calls}))
            for tc in tool_calls:
                yield {"type": "tool", "name": tc["name"]}
                result = await self._execute_tool(tc, context)
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})

        yield {
            "type": "done",
            "answer": "抱歉，这个问题比较复杂，我尝试了多次仍未完成。请换一种方式提问。",
            "steps": self.max_iterations,
            "tool_calls": tool_call_log,
        }

    @staticmethod
    def _assistant_message(message: dict) -> dict:
        """把 chat_with_tools 返回的 message 转成 OpenAI 对话消息格式"""
        return {
            "role": "assistant",
            "content": message["content"],
            "tool_calls": [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": tc["arguments"]},
                }
                for tc in message["tool_calls"]
            ],
        }

    @staticmethod
    async def _execute_tool(tool_call: dict, context: dict) -> str:
        """执行单个工具调用，返回结果文本（含错误处理）"""
        name = tool_call["name"]
        tool = get_tool(name)
        if tool is None:
            return f"未知工具：{name}"

        try:
            arguments = json.loads(tool_call["arguments"]) if tool_call["arguments"] else {}
        except json.JSONDecodeError:
            arguments = {}

        try:
            return await tool.run(context, **arguments)
        except Exception as e:
            logger.warning(f"工具 {name} 执行失败: {e}")
            return f"工具 {name} 执行失败：{e}"
