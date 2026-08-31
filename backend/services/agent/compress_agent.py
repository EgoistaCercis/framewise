"""
帧知 - 工具结果压缩代理（Compress Agent）

结合用户意图，对工具调用结果做上下文感知压缩，替代简单粗暴的直接截断。
短内容（≤ TOOL_TRIM_LENGTH）不压缩，避免浪费 LLM 调用。
"""
from backend.config import TOOL_TRIM_LENGTH
from backend.prompts import COMPRESS_PROMPT
from backend.services.llm import gateway


class CompressAgent:
    """工具结果压缩代理：上下文感知压缩"""

    def __init__(self, smart: bool = False):
        self.smart = smart

    async def compress(self, user_intent: str, tool_name: str, tool_result: str) -> str:
        """结合用户意图压缩工具结果。短内容原样返回，压缩失败回退原文。"""
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
        except Exception:
            return tool_result
