"""
帧知 - Agent 工具集

将项目现有能力封装为 Agent 可调用的 tool（OpenAI function calling 格式）。
每个 Tool 声明 name / description / parameters（JSON Schema），并实现 run()。

新增 tool：继承 Tool 并实现 run()，加入 TOOLS 注册表即可。
"""
import json


def _fmt(seconds: float) -> str:
    """秒数 → MM:SS"""
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"


class Tool:
    """Agent 可调用工具基类"""
    name: str = ""
    description: str = ""
    parameters: dict = {"type": "object", "properties": {}, "required": []}

    def to_openai(self) -> dict:
        """转成 OpenAI function calling 的 tool 定义"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    async def run(self, context: dict, **kwargs) -> str:
        """执行工具，返回结果文本（喂回 LLM）。

        context 为 agent 传入的会话上下文，含 video_hash / video_id 等。
        """
        raise NotImplementedError


class RagAnswerTool(Tool):
    """检索视频字幕，返回相关片段（不生成最终答案，交给 Agent 组织）"""
    name = "rag_answer"
    description = "当用户询问视频内容相关的问题、需要从视频字幕中找答案时调用。检索并返回带时间戳的相关字幕片段。"
    parameters = {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "要检索字幕回答的问题"},
        },
        "required": ["question"],
    }

    async def run(self, context: dict, question: str = "", **kwargs) -> str:
        from backend.services.rag_pipeline.vector_store import load_index, search
        from backend.services.rag_pipeline.embedding_service import embed_single

        index, meta = load_index(context["video_hash"])
        query_embedding = await embed_single(question, video_id=context.get("video_id"))
        results = search(index, meta, query_embedding, top_k=5)
        if not results:
            return "未检索到相关字幕内容"
        parts = []
        for r in results:
            c = r["chunk"]
            parts.append(f"【{_fmt(c['start_time'])}~{_fmt(c['end_time'])}】{c['text']}")
        return "检索到的字幕片段：\n" + "\n".join(parts)


class AnalyzeFrameTool(Tool):
    """截取画面帧并做视觉理解"""
    name = "analyze_frame"
    description = "当用户询问视频画面、视觉内容相关的问题时调用。截取指定时间点的画面帧并做视觉分析，返回画面描述。"
    parameters = {
        "type": "object",
        "properties": {
            "timestamp": {"type": "number", "description": "要分析的画面时间点（秒）"},
        },
        "required": ["timestamp"],
    }

    async def run(self, context: dict, timestamp: float = 0.0, **kwargs) -> str:
        from backend.services.media.vision_service import process_frame_question, analyze_frame

        video_path = context.get("video_path")
        if video_path:
            result = await process_frame_question(
                video_path, context["video_hash"], timestamp, "请描述当前画面中的内容"
            )
            return f"画面分析结果：{result['description']}"

        if context.get("is_url_mode") and context.get("url"):
            from backend.services.media.url_service import download_frame_at_time
            frame_path = download_frame_at_time(context["url"], timestamp, context.get("video_id"))
            description = await analyze_frame(frame_path, video_id=context.get("video_id"))
            return f"画面分析结果：{description}"

        return "当前视频缺少本地文件，无法截帧分析画面"


class GenerateQuizTool(Tool):
    """根据当前视频内容生成测验题"""
    name = "generate_quiz"
    description = "当用户要求出题、测验或考一考自己时调用。根据视频指定时间点附近内容生成测验题，考察理解程度。"
    parameters = {
        "type": "object",
        "properties": {
            "timestamp": {"type": "number", "description": "出题所依据的视频时间点（秒）"},
        },
        "required": ["timestamp"],
    }

    async def run(self, context: dict, timestamp: float = 0.0, **kwargs) -> str:
        from backend.services.rag_pipeline.rag_service import generate_quiz

        result = await generate_quiz(
            video_hash=context["video_hash"],
            timestamp=timestamp,
            video_id=context.get("video_id"),
            smart=context.get("smart", False),
        )
        questions = result.get("questions", [])
        if not questions:
            return "未能生成测验题"
        lines = []
        for i, q in enumerate(questions, 1):
            lines.append(f"{i}. {q['question']}\n   答案：{q['answer']}")
        return "测验题：\n" + "\n".join(lines)


class SaveMemoryTool(Tool):
    """保存一条长期记忆（三层结构）"""
    name = "save_memory"
    description = "当需要保存或更新用户的偏好、学习主题等长期记忆时调用。记忆按「类别→子类别→键值对」三层结构组织。"
    parameters = {
        "type": "object",
        "properties": {
            "category": {"type": "string", "description": "类别，如 user_profile、preferences、learning"},
            "subcategory": {"type": "string", "description": "子类别，如 identity、answer_style、topics"},
            "key": {"type": "string", "description": "键，如 role、style、current"},
            "value": {"type": "string", "description": "值，即具体记忆内容"},
        },
        "required": ["category", "subcategory", "key", "value"],
    }

    async def run(self, context: dict, category: str = "", subcategory: str = "",
                  key: str = "", value: str = "", **kwargs) -> str:
        from backend.services.memory.memory_service import set_card
        set_card(category, subcategory, key, value)
        return f"已保存记忆：{category}/{subcategory}/{key} = {value}"


class RecallMemoryTool(Tool):
    """回忆长期记忆"""
    name = "recall_memory"
    description = "当需要了解用户之前的偏好或学习主题时调用，回忆长期记忆，用于回答时参考用户偏好。"
    parameters = {"type": "object", "properties": {}}

    async def run(self, context: dict, **kwargs) -> str:
        from backend.services.memory.memory_service import format_cards_for_prompt
        mem = format_cards_for_prompt()
        return mem if mem.strip() else "暂无长期记忆"


class DeleteMemoryTool(Tool):
    """删除长期记忆"""
    name = "delete_memory"
    description = "当需要删除过时或不再需要的记忆时调用。category 为类别，subcategory/key 可选（留空则删除整个类别或子类别）。"
    parameters = {
        "type": "object",
        "properties": {
            "category": {"type": "string", "description": "要删除的类别"},
            "subcategory": {"type": "string", "description": "要删除的子类别（可选）"},
            "key": {"type": "string", "description": "要删除的键（可选）"},
        },
        "required": ["category"],
    }

    async def run(self, context: dict, category: str = "", subcategory: str = None,
                  key: str = None, **kwargs) -> str:
        from backend.services.memory.memory_service import delete_card
        delete_card(category, subcategory, key)
        return f"已删除记忆：{category}/{subcategory or ''}/{key or ''}"


# ── 注册表（按 agent 分组）───────────────────────────────
# 主 agent 工具：业务能力（不含记忆，记忆由 memory agent 独立处理）
MAIN_TOOLS: list[Tool] = [
    RagAnswerTool(),
    AnalyzeFrameTool(),
    GenerateQuizTool(),
]

# 记忆 agent 工具：记忆的增删改查
MEMORY_TOOLS: list[Tool] = [
    SaveMemoryTool(),
    RecallMemoryTool(),
    DeleteMemoryTool(),
]

# 全部工具（用于按名查找）
_ALL_TOOLS: list[Tool] = MAIN_TOOLS + MEMORY_TOOLS


def get_tools_openai(tools: list[Tool] = None) -> list[dict]:
    """返回指定 tool 列表的 OpenAI 定义（默认返回主 agent 工具）"""
    tools = tools or MAIN_TOOLS
    return [t.to_openai() for t in tools]


def get_tool(name: str) -> Tool | None:
    """按名字取 tool"""
    for t in _ALL_TOOLS:
        if t.name == name:
            return t
    return None
