"""
帧知 - 文本压缩代理（Compress Agent）

**用 LLM 把长文本变短** 这件事只在本模块发生。目前有两种**模式**，不是两个组件：

| 模式 | 对象 | 输入性质 | 压法 |
|---|---|---|---|
| `compress` | 当轮工具结果 | 结构化，与用户意图有相关性 | **上下文感知压缩**（保相关、丢无关） |
| `summarize_history` | 历史对话 | 已是一问一答的自然语言 | **摘要**（保"谁问了什么、答了什么"） |

⚠️ **不要因为"都是压缩"就把两者合并成一个函数**：
- 工具结果需要「结合意图判断哪些片段有用」——无差别摘要会把这个判断丢掉
- 历史对话需要「保持对话结构」——逐条压缩会让模型看不出谁说了什么

**职责边界**：调用方（如 `conversation_service`）负责**决定**
（什么时候压、压哪一段），本模块负责**执行**（怎么压）。
"""
from loguru import logger

from backend.config import TOOL_TRIM_LENGTH, SUMMARY_TARGET_LENGTH
from backend.prompts import COMPRESS_PROMPT, SUMMARY_PROMPT
from backend.services.llm import gateway


class CompressAgent:
    """文本压缩代理：两种模式，见模块 docstring"""

    def __init__(self, smart: bool = False):
        self.smart = smart

    async def compress(self, user_intent: str, tool_name: str, tool_result: str) -> str:
        """**模式一**：结合用户意图压缩工具结果。

        短内容原样返回（避免浪费 LLM 调用），压缩失败回退原文。
        """
        if not tool_result or len(tool_result) <= TOOL_TRIM_LENGTH:
            return tool_result

        prompt = f"""用户意图：{user_intent}

工具名称：{tool_name}

工具结果：
{tool_result}"""

        try:
            compressed, _ = await gateway.chat(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=COMPRESS_PROMPT,
                max_tokens=800,
                smart=self.smart,
            )
            result = compressed.strip()
            return result or tool_result
        except Exception as e:
            # 回退是静默的：压不了就返回原文，但**必须留痕** ——
            # 否则"压缩一直失败"这件事永远不会被发现（项目里踩过太多次）
            logger.warning(f"工具结果压缩失败，回退原文：{type(e).__name__}: {str(e)[:120]}")
            return tool_result

    async def summarize_history(self, msgs: list[dict]) -> str:
        """**模式二**：把一段历史对话摘要成短文。

        与 `compress` 的关键差别：这里的输入**已经是一问一答的自然语言**，
        要保住的是「谁问了什么、AI 答了什么」这种**对话性**，而不是"哪些片段跟当前问题相关"。

        每条消息先截到 200 字 —— 摘要的输入不需要全文，太长反而稀释重点。
        失败返回空串（调用方据此回退到未摘要的列表），不抛异常打断整轮问答。
        """
        if not msgs:
            return ""

        text = "\n".join(
            f"{'用户' if m['role'] == 'user' else 'AI'}: {(m.get('content') or '')[:200]}"
            for m in msgs
        )
        prompt = f"""以下是视频学习对话的片段，请用 {SUMMARY_TARGET_LENGTH} 字以内的中文简洁概括用户问了什么、AI 回答了什么。
只输出摘要文本，不要额外解释。

{text}"""

        try:
            answer, _ = await gateway.chat(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=SUMMARY_PROMPT,
                max_tokens=300,
                smart=self.smart,
            )
            return (answer or "").strip()
        except Exception as e:
            logger.warning(f"对话摘要失败，回退到未摘要：{type(e).__name__}: {str(e)[:120]}")
            return ""
