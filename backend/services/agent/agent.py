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
import asyncio
import json
import uuid

from loguru import logger

from backend import config
from backend.prompts import AGENT_SYSTEM_PROMPT
from backend.services.agent.compress_agent import CompressAgent
from backend.services.agent.tools import get_tools_openai, MAIN_TOOLS
from backend.services.llm import gateway

# 单个工具的单次执行上限，见 config.AGENT_TOOL_TIMEOUT
_TOOL_TIMEOUT = config.AGENT_TOOL_TIMEOUT


def _new_session() -> str:
    """生成一次问答的 trace session id"""
    return uuid.uuid4().hex[:12]


# 模型偶尔会把工具调用**写成文本**而不是走原生 function calling ——
# 尤其在它调不动工具的时候（例如夹带了 `tools=None` 的兜底轮）。
# 这种内容绝不能当答案返回给用户：看起来像乱码，末尾还常带被截断的特殊 token。
_TOOLCALL_ARTIFACTS = ("<tool_calls>", "</tool_calls>", "<invoke name=",
                       "<function_calls>", "antml:invoke", "<｜")


def _looks_like_tool_call(text: str) -> bool:
    """判断一段输出是不是「工具调用的文本残渣」而非真正的回答"""
    t = (text or "").strip()
    if not t:
        return False
    low = t.lower()
    return any(a.lower() in low for a in _TOOLCALL_ARTIFACTS)


def _log_trace(session_id: str, video_id: str, step: int, event_type: str,
               content: str = "", tool_name: str = None):
    """记录一条轨迹，失败不影响主流程"""
    try:
        from backend.services.trace_service import log_trace
        log_trace(session_id, video_id, step, event_type, content, tool_name)
    except Exception as e:
        # 轨迹库坏了不能影响主流程，但也不能完全没声音 —— 否则排查时
        # 会以为"没有轨迹"是正常的
        logger.debug(f"轨迹记录失败（不影响问答）: {type(e).__name__}: {e}")


# ── 高危操作确认管理（human-in-the-loop）──────────────────
# 工具执行前请求用户批准：run_stream 推送 confirm 事件并阻塞等待，
# 前端调 /api/approve 接口唤醒。
#
# 已知限制：confirm_id 是全局命名空间，任何拿到 id 的客户端都能 approve。
# 单用户应用可接受；多用户前必须绑定会话（session_id 已存进 entry，待接入校验）。
_pending_confirmations: dict = {}  # confirm_id -> {"event", "approved", "message", "session_id"}


def _new_confirmation(message: str, session_id: str = None) -> str:
    """创建一条待确认请求，返回 confirm_id。

    session_id 一并存下，供将来做会话绑定（见文末说明）。
    """
    confirm_id = uuid.uuid4().hex[:12]
    _pending_confirmations[confirm_id] = {
        "event": asyncio.Event(),
        "approved": False,
        "message": message,
        "session_id": session_id,
    }
    return confirm_id


async def _wait_confirmation(confirm_id: str, timeout: float = 120.0) -> str:
    """阻塞等待用户确认，返回 "approved" / "denied" / "timeout"。

    **返回三态而不是 bool**：超时和明确拒绝是两回事。用户可能只是离开了，
    若都返回 False 并统一写成"用户拒绝了"，模型会以为这个方向被用户否定，
    后续对话方向被带偏。

    **清理**：原来只在 `_resolve_confirmation` 里 pop，超时的条目永远留在字典里
    （只进不出，长期运行内存泄漏）。这里在 finally 里统一清理，两条路径都覆盖。
    """
    entry = _pending_confirmations.get(confirm_id)
    if not entry:
        return "denied"
    try:
        await asyncio.wait_for(entry["event"].wait(), timeout=timeout)
        return "approved" if entry["approved"] else "denied"
    except asyncio.TimeoutError:
        return "timeout"
    finally:
        _pending_confirmations.pop(confirm_id, None)


def _resolve_confirmation(confirm_id: str, approved: bool) -> bool:
    """由 /api/approve 调用，设置确认结果并唤醒等待"""
    entry = _pending_confirmations.get(confirm_id)
    if not entry:
        return False
    entry["approved"] = approved
    entry["event"].set()
    # 这里 pop 是幂等的兜底：等待方持有 entry 引用，pop 之后仍能读到 approved。
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
        # 否则传入自定义工具（如子类包装）会被全局注册表绕过。
        # 注意判 `is not None` 而不是真值：`Agent(tools=[])` 的语义是"不给任何工具"，
        # 若按真值判断，空列表会被静默换成整套主工具（含删除文件）。
        self.tools = list(tools) if tools is not None else None
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
        """非流式便捷封装：抽干 run_stream，只取最终结果。

        ★ 循环逻辑**只有 run_stream 一份**。

        这个方法过去是一份**独立复制的循环**，与 run_stream 约 70% 重复，
        而确认机制、异常处理、参数解析只存在于 run_stream 里。于是它成了一个
        「看起来还能用」的高危入口：

        - 从不检查 `requires_confirmation` → 误用会**静默执行** delete_file / 覆盖文件
        - `json.loads` 无 try、网关异常直接上抛（run_stream 转成 error 事件）
        - 评测 V4 走的就是它 → **测的不是产品实际运行的代码**

        改为委托后上面三条一次性消解，且调用方（评测 V4、MemoryAgent）无需改动：
        它们拿到的仍是同一个 `{"answer","steps","tool_calls"}` 契约，
        但背后跑的是产品真正在用的那条循环。

        需要看中间过程（工具调用、确认请求）时请直接用 run_stream。

        ⚠️ **确认语义**：`run` 是无人值守的收集器，它忽略 `confirm` 事件。
        如果跑的工具 `requires_confirmation()` 为真，`_wait_confirmation` 会
        一直等到超时（120s），然后以「未在时限内收到用户确认」跳过该操作 ——
        也就是**静默跳过**，而不是失败。
        交互式场景（需要用户点确认）请直接用 `run_stream`。
        """
        answer, steps, tool_calls = "", 0, []
        async for ev in self.run_stream(user_message, context):
            t = ev.get("type")
            if t == "error":
                # 保持旧契约：非流式调用方按异常处理
                raise RuntimeError(ev["message"])
            if t == "done":
                answer = ev["answer"]
                steps = ev["steps"]
                tool_calls = ev["tool_calls"]
        return {"answer": answer, "steps": steps, "tool_calls": tool_calls}

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
        empty_retried = False      # 空答案只重试一次，避免死循环
        last_tool_sig = None       # 上一轮 (工具名, 参数) 签名，用于识别重复调用

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
                # 空答案守卫：既无 tool_calls 也无 content 时，原样返回会让前端
                # 显示一片空白。视为异常轮次重试一次，仍空才如实说明。
                # 「工具调用残渣」同理 —— 那是模型把调用写成了文本，不是回答。
                invalid = (not full_content.strip()) or _looks_like_tool_call(full_content)
                if invalid and not empty_retried:
                    empty_retried = True
                    logger.warning("模型未给出有效回答（空内容或工具调用残渣），重试一次")
                    _log_trace(session_id, video_id, step, "error", "无效回答，重试")
                    messages.append({"role": "user",
                                     "content": "你上一次没有给出有效回答。"
                                                "请直接用正常文字回答问题，不要输出工具调用格式。"})
                    continue
                answer = full_content.strip()
                if not answer or _looks_like_tool_call(answer):
                    logger.warning("重试后仍无有效回答，返回兜底文案")
                    answer = "抱歉，我这次没能生成有效回答，请再试一次。"
                _log_trace(session_id, video_id, step, "answer", answer)
                yield {"type": "done", "answer": answer, "steps": step, "tool_calls": tool_call_log}
                return

            # 工具轮：推送工具状态并执行
            # 带参数摘要：只存工具名的话，排查时看到"调了 analyze_frame 三次"
            # 却不知道看的是哪三帧，还得去翻 trace 库
            tool_call_log.extend(
                f'{tc["name"]}({tc["arguments"][:80]})' if tc.get("arguments") else tc["name"]
                for tc in tool_calls)
            messages.append(self._assistant_message({"content": full_content, "tool_calls": tool_calls}))

            # 重复调用检测：同一批 (工具名, 参数) 与上一轮完全相同，说明模型在原地打转
            # （画面题历史上曾对同一题扫 44 次）。提示它基于已有信息作答，而不是
            # 干等到 max_iterations 耗尽 —— 每轮都是完整上下文的 token + 延迟。
            # 注意这里只**判定**，提示要等工具结果回填完再插（见循环末尾的说明）。
            sig = tuple(sorted((tc["name"], tc["arguments"]) for tc in tool_calls))
            repeated = (sig == last_tool_sig)
            last_tool_sig = sig
            for tc in tool_calls:
                _log_trace(session_id, video_id, step, "tool_call",
                           json.dumps({"name": tc["name"], "arguments": tc["arguments"]}, ensure_ascii=False),
                           tool_name=tc["name"])
                yield {"type": "tool", "name": tc["name"]}

                # 参数解析失败不能让整个流崩掉：模型输出截断/畸形 JSON 实际会发生，
                # 而这里原来只 catch 了网关的 RuntimeError，JSONDecodeError 会直接冲出生成器。
                try:
                    arguments = json.loads(tc["arguments"]) if tc["arguments"] else {}
                    if not isinstance(arguments, dict):
                        raise ValueError(f"参数应为 JSON 对象，实为 {type(arguments).__name__}")
                except (json.JSONDecodeError, ValueError) as e:
                    result = (f"参数解析失败（{str(e)[:80]}）：工具参数不是合法 JSON 对象，"
                              f"请按 schema 重新调用。")
                    _log_trace(session_id, video_id, step, "tool_result", result, tool_name=tc["name"])
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
                    continue

                # 高危操作（覆盖文件、删除文件等）需用户确认。
                # 必须用 find_tool 而不是 get_tool：确认判断和实际执行要是**同一个对象**，
                # 否则可能出现「拿 A 判断不用确认、却用 B 去执行」——
                # 一个 requires_confirmation=True 的工具会被静默跳过确认。
                #
                # 确认钩子收 dict 而**不是 `**arguments` 展开**：展开会把模型可控的
                # JSON 键变成关键字参数，模型只要传 {"self": ...} 就会
                # `TypeError: got multiple values for argument 'self'`，整个流中断。
                tool = self.find_tool(tc["name"])
                verdict = "n/a"
                if tool is not None and tool.requires_confirmation(arguments):
                    message = tool.confirm_message(arguments)
                    confirm_id = _new_confirmation(message, session_id)
                    # try/finally 包住 yield：客户端在收到确认请求后立刻断开时，
                    # 生成器会被 close，不清理的话这条确认就永远留在字典里
                    try:
                        yield {"type": "confirm", "confirm_id": confirm_id,
                               "tool": tc["name"], "message": message}
                        verdict = await _wait_confirmation(confirm_id)
                    finally:
                        _pending_confirmations.pop(confirm_id, None)

                    if verdict == "approved":
                        result = await self._execute_tool(tc, context)
                    elif verdict == "timeout":
                        # 超时 ≠ 明确拒绝，文案必须区分（见 _wait_confirmation 说明）
                        result = f"未在时限内收到用户确认，操作已跳过：{tc['name']}"
                    else:
                        result = f"用户拒绝了该操作：{tc['name']}"
                else:
                    result = await self._execute_tool(tc, context)

                result = await self._compress_tool_result(user_message, tc["name"], result)
                _log_trace(session_id, video_id, step, "tool_result", result, tool_name=tc["name"])
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})

            # ★ 重复提示必须放在**所有 tool 结果回填之后**。
            #   OpenAI 协议要求 `tool` 消息紧跟带 tool_calls 的 assistant 消息；
            #   中间夹一条 user 消息属于协议违规 —— DeepSeek 容忍，
            #   但严格实现的厂商（OpenAI 本家、部分兼容网关）会直接 400。
            if repeated:
                messages.append({
                    "role": "user",
                    "content": "你刚刚用完全相同的参数重复调用了同一个工具，结果不会有变化。"
                               "请基于已经拿到的信息直接作答，或换一个完全不同的思路。",
                })
                logger.info("检测到重复工具调用，已提示模型改变策略")

        # ── 迭代耗尽兜底 ──
        # 原来直接返回写死的道歉，把前 N 轮收集的工具结果全部作废。用户白等，
        # 成本也白花。这里追加**一次不带 tools 的调用**，让模型基于已经拿到的
        # 材料尽力作答；真的答不出，它自己会说"信息不足"，比我们替它放弃好。
        _log_trace(session_id, video_id, self.max_iterations, "error", "达到最大迭代轮次，改为基于已有信息作答")
        logger.warning(f"Agent 达到最大迭代轮次 {self.max_iterations}，改为基于已有信息作答")
        try:
            final = ""
            async for event in gateway.chat_with_tools_stream(
                messages,
                system_prompt=self.system_prompt,
                tools=None,             # ★ 不给工具，逼它直接作答
                smart=self.smart,
                video_id=video_id,
            ):
                if event["type"] == "content":
                    final += event["delta"]
                    yield {"type": "content", "delta": event["delta"]}
            answer = final.strip()
            # ★ 兜底这轮传的是 tools=None，模型调不动工具时会把调用**写成文本** ——
            #   实测就出现过 `<tool_calls><invoke name="analyze_frame">…` 被当成答案返回。
            #   这种残渣必须拦掉，退回致歉文案（旧行为），而不是把乱码丢给用户。
            if not answer or _looks_like_tool_call(answer):
                logger.warning("兜底作答没有产出有效内容（空或工具调用残渣），回退到致歉文案")
                answer = ("抱歉，我调用了多次工具仍未收集到足够信息，无法可靠回答这个问题。"
                          "可以试着把问题问得更具体一些。")
        except Exception as e:
            logger.warning(f"迭代耗尽后的兜底作答失败：{e}")
            answer = ("抱歉，这个问题比较复杂，我尝试了多次仍未完成。请换一种方式提问。")

        yield {
            "type": "done",
            "answer": answer,
            "steps": self.max_iterations,
            "tool_calls": tool_call_log,
            "exhausted": True,
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
        """按名字取工具。

        搜索范围必须与「告诉模型有哪些工具」的那份**完全一致**（见 __init__ 里的
        self.tools / get_tools_openai）。两者一旦不一致，就会出现：
        - 模型被告知的工具，执行时查不到 → 调用失败
        - 模型从没被告知的工具，执行时却查得到 → 越权（原来就是这个：
          主 agent 能查到记忆工具，而记忆的增删改按设计只属于 MemoryAgent）

        判定用 `is None` 而非真值：None = 用默认主工具集，[] = 没有工具。
        回退目标是 MAIN_TOOLS（与 get_tools_openai(None) 同一份），
        而不是 _ALL_TOOLS —— 后者会把记忆工具也放进来。
        """
        for t in (MAIN_TOOLS if self.tools is None else self.tools):
            if t.name == name:
                return t
        return None

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
            # 工具级超时：工具内部可能调模型（analyze_frame 的 VL、generate_quiz）
            # 或拉流下载，都可能长时间挂起。Agent 循环本身没有别的兜底，
            # 一个工具卡死就会占住整轮对话、把用户晾在那里。
            return await asyncio.wait_for(
                tool.run(context, **arguments), timeout=_TOOL_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning(f"工具 {name} 执行超时（>{_TOOL_TIMEOUT:.0f}s）")
            return (f"工具 {name} 执行超时（超过 {_TOOL_TIMEOUT:.0f} 秒），已中止。"
                    f"可以换个方式再试，或改用其他工具。")
        except Exception as e:
            logger.warning(f"工具 {name} 执行失败: {e}")
            return f"工具 {name} 执行失败：{e}"
