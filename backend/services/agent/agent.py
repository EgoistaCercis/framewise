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
import uuid

from loguru import logger

from backend.prompts import AGENT_SYSTEM_PROMPT
from backend.services.agent.compress_agent import CompressAgent
from backend.services.agent.tools import get_tools_openai, get_tool
from backend.services.llm import gateway


def _new_session() -> str:
    """生成一次问答的 trace session id"""
    return uuid.uuid4().hex[:12]


def _log_trace(session_id: str, video_id: str, step: int, event_type: str,
               content: str = "", tool_name: str = None):
    """记录一条轨迹，失败不影响主流程"""
    try:
        from backend.services.trace_service import log_trace
        log_trace(session_id, video_id, step, event_type, content, tool_name)
    except Exception:
        pass


# ── 高危操作确认管理（human-in-the-loop）──────────────────
# 工具执行前请求用户批准：run_stream 推送 confirm 事件并阻塞等待，
# 前端调 /api/approve 接口唤醒。
import asyncio

_pending_confirmations: dict = {}  # confirm_id -> {"event": asyncio.Event, "approved": bool, "message": str}


def _new_confirmation(message: str) -> str:
    """创建一条待确认请求，返回 confirm_id"""
    confirm_id = uuid.uuid4().hex[:12]
    _pending_confirmations[confirm_id] = {
        "event": asyncio.Event(),
        "approved": False,
        "message": message,
    }
    return confirm_id


async def _wait_confirmation(confirm_id: str, timeout: float = 120.0) -> bool:
    """阻塞等待用户确认，返回是否批准（超时默认拒绝）"""
    entry = _pending_confirmations.get(confirm_id)
    if not entry:
        return False
    try:
        await asyncio.wait_for(entry["event"].wait(), timeout=timeout)
    except asyncio.TimeoutError:
        return False
    return entry["approved"]


def _resolve_confirmation(confirm_id: str, approved: bool) -> bool:
    """由 /api/approve 调用，设置确认结果并唤醒等待"""
    entry = _pending_confirmations.get(confirm_id)
    if not entry:
        return False
    entry["approved"] = approved
    entry["event"].set()
    _pending_confirmations.pop(confirm_id, None)
    return True


def _load_memory_context() -> str:
    """加载长期记忆（默认全量）。作为独立消息注入 messages 列表，避免污染 system prompt 缓存。"""
    try:
        from backend.services.memory.memory_service import format_cards_for_prompt
        return format_cards_for_prompt()
    except Exception:
        return ""


async def _load_conversation_context(video_id: str) -> str:
    """加载历史对话上下文（含四层压缩），作为独立消息注入。"""
    if not video_id:
        return ""
    try:
        from backend.services.rag_pipeline.conversation_service import get_recent_context
        return await get_recent_context(video_id)
    except Exception:
        return ""


def _fmt_ts(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60:02d}:{s % 60:02d}"


def format_player_state(context: dict) -> str:
    """把「用户在视频的哪个位置」告诉 Agent。

    之前只在 context 里传了 timestamp、却没告诉模型，于是模型要调 analyze_frame
    只能自己从字幕猜时间点——画面题的答案又恰恰不在字幕里，结果就是瞎扫全片
    （实测：16 道画面题调了 222 次，只有 22% 落在答案区间，最极端一题扫 0~840s 共 44 次）。

    作为独立消息注入而非写进 system prompt，与 <memory>/<conversation> 一致：
    system prompt 保持稳定以命中前缀缓存。
    """
    ts = context.get("timestamp")
    if ts is None:
        return ""
    return (f'<player_state>用户当前暂停在 {_fmt_ts(ts)}（第 {int(ts)} 秒）。'
            f'若用户问的是"现在/当前"的画面，直接用这个位置。</player_state>')


class Agent:
    """工具型 Agent，循环调用 LLM 与工具直到产出最终答案"""

    def __init__(self, system_prompt: str = None, max_iterations: int = 8,
                 smart: bool = False, tools: list = None):
        self.system_prompt = system_prompt or AGENT_SYSTEM_PROMPT
        self.max_iterations = max_iterations
        self.smart = smart
        # 保存实际工具对象：执行时也要按这份列表查找，
        # 否则传入自定义工具（如子类包装）会被全局注册表绕过
        self.tools = list(tools) if tools else None
        self.tools_openai = get_tools_openai(tools)
        self.compress_agent = CompressAgent(smart=smart)

    async def _build_messages(self, user_message: str, context: dict) -> list[dict]:
        """构造初始消息列表。长期记忆与历史对话作为独立消息（XML 标签）注入，
        保持 system prompt 稳定以命中前缀缓存。"""
        messages = []
        memory_text = _load_memory_context()
        if memory_text:
            messages.append({
                "role": "user",
                "content": f"<memory>\n{memory_text.strip()}\n</memory>",
            })
        conv_ctx = await _load_conversation_context(context.get("video_id"))
        if conv_ctx:
            messages.append({
                "role": "user",
                "content": f"<conversation>\n{conv_ctx.strip()}\n</conversation>",
            })
        # 播放位置放在最后（每轮都在变），不破坏前面稳定前缀的缓存
        player = format_player_state(context)
        if player:
            messages.append({"role": "user", "content": player})
        messages.append({"role": "user", "content": user_message})
        return messages

    async def run(self, user_message: str, context: dict) -> dict:
        """执行 agent loop，返回 {"answer", "steps", "tool_calls"}"""
        messages = await self._build_messages(user_message, context)
        tool_call_log = []
        session_id = _new_session()
        video_id = context.get("video_id")
        _log_trace(session_id, video_id, 0, "user", user_message)

        for step in range(1, self.max_iterations + 1):
            message, usage = await gateway.chat_with_tools(
                messages,
                system_prompt=self.system_prompt,
                tools=self.tools_openai,
                smart=self.smart,
                video_id=video_id,      # 记账在网关做，这里只负责把视频归因传下去
            )

            # 无工具调用 → 最终答案
            if not message["tool_calls"]:
                _log_trace(session_id, video_id, step, "answer", message["content"])
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
                _log_trace(session_id, video_id, step, "tool_call",
                           json.dumps({"name": tc["name"], "arguments": tc["arguments"]}, ensure_ascii=False),
                           tool_name=tc["name"])
                result = await self._execute_tool(tc, context)
                result = await self._compress_tool_result(user_message, tc["name"], result)
                _log_trace(session_id, video_id, step, "tool_result", result, tool_name=tc["name"])
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })

        logger.warning(f"Agent 达到最大迭代轮次 {self.max_iterations}，强制结束")
        _log_trace(session_id, video_id, self.max_iterations, "error", "达到最大迭代轮次")
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
        messages = await self._build_messages(user_message, context)
        tool_call_log = []
        session_id = _new_session()
        video_id = context.get("video_id")
        _log_trace(session_id, video_id, 0, "user", user_message)

        for step in range(1, self.max_iterations + 1):
            full_content = ""
            tool_calls = []
            try:
                async for event in gateway.chat_with_tools_stream(
                    messages,
                    system_prompt=self.system_prompt,
                    tools=self.tools_openai,
                    smart=self.smart,
                    video_id=video_id,      # 记账在网关做
                ):
                    if event["type"] == "content":
                        full_content += event["delta"]
                        yield {"type": "content", "delta": event["delta"]}
                    elif event["type"] == "done":
                        tool_calls = event["tool_calls"]
            except RuntimeError as e:
                _log_trace(session_id, video_id, step, "error", str(e))
                yield {"type": "error", "message": str(e)}
                return

            # 无工具调用 → 最终答案（content 已流式 yield）
            if not tool_calls:
                _log_trace(session_id, video_id, step, "answer", full_content)
                yield {"type": "done", "answer": full_content, "steps": step, "tool_calls": tool_call_log}
                return

            # 工具轮：推送工具状态并执行
            tool_call_log.extend([tc["name"] for tc in tool_calls])
            messages.append(self._assistant_message({"content": full_content, "tool_calls": tool_calls}))
            for tc in tool_calls:
                _log_trace(session_id, video_id, step, "tool_call",
                           json.dumps({"name": tc["name"], "arguments": tc["arguments"]}, ensure_ascii=False),
                           tool_name=tc["name"])
                yield {"type": "tool", "name": tc["name"]}

                # 高危操作（覆盖文件、删除文件等）需用户确认
                tool = get_tool(tc["name"])
                arguments = json.loads(tc["arguments"]) if tc["arguments"] else {}
                if tool is not None and tool.requires_confirmation(**arguments):
                    message = tool.confirm_message(**arguments)
                    confirm_id = _new_confirmation(message)
                    yield {"type": "confirm", "confirm_id": confirm_id, "tool": tc["name"], "message": message}
                    if await _wait_confirmation(confirm_id):
                        result = await self._execute_tool(tc, context)
                    else:
                        result = f"用户拒绝了该操作：{tc['name']}"
                else:
                    result = await self._execute_tool(tc, context)

                result = await self._compress_tool_result(user_message, tc["name"], result)
                _log_trace(session_id, video_id, step, "tool_result", result, tool_name=tc["name"])
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})

        _log_trace(session_id, video_id, self.max_iterations, "error", "达到最大迭代轮次")
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

    async def _compress_tool_result(self, user_intent: str, tool_name: str, tool_result: str) -> str:
        """结合用户意图压缩工具结果（上下文感知，短内容不压缩）"""
        return await self.compress_agent.compress(user_intent, tool_name, tool_result)

    def find_tool(self, name: str):
        """按名字取工具：优先用构造时传入的列表，再回退全局注册表"""
        if self.tools:
            for t in self.tools:
                if t.name == name:
                    return t
        return get_tool(name)

    async def _execute_tool(self, tool_call: dict, context: dict) -> str:
        """执行单个工具调用，返回结果文本（含错误处理）"""
        name = tool_call["name"]
        tool = self.find_tool(name)
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
