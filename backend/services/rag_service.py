"""
帧知 - RAG问答服务
检索 + DeepSeek LLM 生成答案
"""
from loguru import logger
import httpx
from backend.config import (
    DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, RAG_TOP_K
)
from backend.services.embedding_service import embed_single
from backend.services.vector_store import load_index, search

SYSTEM_PROMPT = """你是视频学习助手"帧知"，帮助用户理解和学习视频内容。

回答策略：
1. 优先依据检索到的视频字幕内容回答
2. 如果视频中有相关讲解，必须引用时间戳，格式：【MM:SS~MM:SS】（如【12:35~13:08】）
3. 如果视频内容不足以回答用户问题，可以结合你的外部知识补充，但需要明确区分来源：
   - 视频中提到的内容 → 标注时间戳
   - 外部知识补充 → 注明"根据通用知识"
4. 如果发现视频中的说法可能存在错误或过时，可以善意指出并提供更准确的信息
5. 回答简洁清晰，适合学习场景
6. 可以引用多个相关片段来组织完整答案"""


def _format_time(seconds: float) -> str:
    """秒数格式化 MM:SS"""
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"


async def answer_text_question(
    video_hash: str,
    question: str,
    top_k: int = None,
    video_id: str = None,
    smart: bool = False,
) -> dict:
    """
    文本问答：检索 + LLM生成

    返回: {answer, references: [{text, start_time, end_time}, ...]}
    """
    if top_k is None:
        top_k = RAG_TOP_K

    # Step 1: 加载向量索引
    index, meta = load_index(video_hash)

    # Step 2: 问题向量化
    query_embedding = await embed_single(question, video_id=video_id)

    # Step 3: 检索
    results = search(index, meta, query_embedding, top_k)

    # Step 4: 构建上下文
    context_parts = []
    for r in results:
        c = r["chunk"]
        time_range = f"【{_format_time(c['start_time'])}~{_format_time(c['end_time'])}】"
        context_parts.append(f"{time_range} {c['text']}")

    context = "\n\n".join(context_parts)

    # Step 5: 调用LLM
    conv_ctx = await _get_conversation_context(video_id)
    user_prompt = f"""{conv_ctx}以下是视频字幕中的相关片段：

{context}

用户问题：{question}

请根据以上字幕内容和对话上下文回答用户问题。"""

    answer = await _call_deepseek(user_prompt, video_id=video_id, smart=smart)

    # Step 6: 构建引用
    references = []
    for r in results:
        c = r["chunk"]
        references.append({
            "text": c["text"],
            "start_time": c["start_time"],
            "end_time": c["end_time"],
            "score": r["score"],
        })

    return {
        "answer": answer,
        "references": references,
    }


async def answer_with_frame_context(
    video_hash: str,
    question: str,
    frame_description: str,
    top_k: int = None,
    video_id: str = None,
    smart: bool = False,
) -> dict:
    """
    结合画面描述的问答
    """
    if top_k is None:
        top_k = RAG_TOP_K

    # 加载索引
    index, meta = load_index(video_hash)

    # 检索
    query_embedding = await embed_single(question, video_id=video_id)
    results = search(index, meta, query_embedding, top_k)

    # 构建上下文
    context_parts = []
    for r in results:
        c = r["chunk"]
        time_range = f"【{_format_time(c['start_time'])}~{_format_time(c['end_time'])}】"
        context_parts.append(f"{time_range} {c['text']}")

    context = "\n\n".join(context_parts)

    conv_ctx = await _get_conversation_context(video_id)
    user_prompt = f"""{conv_ctx}以下是视频字幕中的相关片段：

{context}

以下是用户暂停时画面内容的分析：

{frame_description}

用户问题：{question}

请结合以上字幕内容、画面信息和对话上下文，综合回答用户问题。"""

    answer = await _call_deepseek(user_prompt, video_id=video_id, smart=smart)

    references = []
    for r in results:
        c = r["chunk"]
        references.append({
            "text": c["text"],
            "start_time": c["start_time"],
            "end_time": c["end_time"],
            "score": r["score"],
        })

    return {
        "answer": answer,
        "references": references,
        "frame_description": frame_description,
    }


QUIZ_PROMPT = """你是视频学习助手"帧知"。用户暂停了视频，想检验自己是否理解了当前内容。

请根据以下视频字幕片段，生成 2~3 个考题来考察用户的理解程度。

严格按以下格式输出（不要任何前言、后语、解释）：

Q1: （考察核心概念的题目）
<answer>（标准答案）</answer>

Q2: （考察理解深度的题目）
<answer>（标准答案）</answer>

Q3: （可选，考察应用能力的题目）
<answer>（标准答案）</answer>

要求：
- 题目覆盖当前片段的核心知识点
- 题型可以是概念解释、判断对错、填空、简答
- 难度适中，能检验用户是否真正理解
- 答案简洁准确
- 不要输出任何格式之外的文字"""


async def generate_quiz(
    video_hash: str,
    timestamp: float,
    video_id: str = None,
    smart: bool = False,
) -> dict:
    """
    主动学习：根据当前视频片段生成考题

    返回: {questions: [{question, answer}, ...], context_time: str}
    """
    from backend.services.embedding_service import embed_single
    from backend.services.vector_store import load_index, search

    index, meta = load_index(video_hash)

    # 找到当前时间戳附近的 chunks（优先当前时间的）
    query_embedding = await embed_single("当前正在讲解的内容", video_id=video_id)
    all_results = search(index, meta, query_embedding, top_k=10)

    # 筛选出当前时间附近的内容（前后各30秒）
    nearby = [r for r in all_results
              if abs(r["chunk"]["start_time"] - timestamp) < 30][:5]
    if len(nearby) < 2:
        nearby = all_results[:3]

    # 构建上下文
    context_parts = []
    for r in nearby:
        c = r["chunk"]
        context_parts.append(
            f"【{_format_time(c['start_time'])}~{_format_time(c['end_time'])}】{c['text']}"
        )
    context = "\n\n".join(context_parts)

    user_prompt = f"""以下是视频当前片段的内容：

{context}

用户暂停在 {_format_time(timestamp)} 处，请根据以上内容出题考察用户。"""

    answer = await _call_deepseek(user_prompt, video_id=video_id, system_prompt=QUIZ_PROMPT, smart=smart)

    # 解析问题和答案
    questions = _parse_quiz(answer)
    return {
        "questions": questions,
        "context_time": _format_time(timestamp),
        "context_chunks": [r["chunk"] for r in nearby],
    }


def _parse_quiz(text: str) -> list[dict]:
    """解析LLM生成的题目，严格匹配 Q数字: 格式"""
    import re
    questions = []
    # 只匹配 Q1:/Q2:/Q3: 格式的行
    pattern = r'Q\d+[：:]\s*(.*?)(?=\nQ\d+[：:]|\n*$)'
    matches = re.findall(pattern, text, re.DOTALL)

    for match in matches:
        match = match.strip()
        if not match:
            continue
        # 提取答案
        ans_match = re.search(r'<answer>(.*?)</answer>', match, re.DOTALL)
        if ans_match:
            answer = ans_match.group(1).strip()
            question = re.sub(r'<answer>.*?</answer>', '', match, flags=re.DOTALL).strip()
        else:
            question = match
            answer = "（点击展开答案）"
        if question:
            questions.append({"question": question, "answer": answer})

    return questions


async def prepare_rag_context(video_hash: str, question: str, video_id: str = None, top_k: int = None) -> tuple:
    """
    检索 + 构建 Prompt，返回 (user_prompt, results)
    供流式和非流式共用
    """
    if top_k is None:
        top_k = RAG_TOP_K

    index, meta = load_index(video_hash)
    query_embedding = await embed_single(question, video_id=video_id)
    results = search(index, meta, query_embedding, top_k)

    context_parts = []
    for r in results:
        c = r["chunk"]
        time_range = f"【{_format_time(c['start_time'])}~{_format_time(c['end_time'])}】"
        context_parts.append(f"{time_range} {c['text']}")
    context = "\n\n".join(context_parts)

    conv_ctx = await _get_conversation_context(video_id)
    mem_ctx = _get_memory_context()
    user_prompt = f"""{mem_ctx}{conv_ctx}以下是视频字幕中的相关片段：

{context}

用户问题：{question}

请根据以上字幕内容和对话上下文回答用户问题。"""

    return user_prompt, results


async def prepare_frame_context(video_hash: str, question: str, frame_description: str,
                                video_id: str = None, top_k: int = None) -> tuple:
    """检索 + 构建画面问答 Prompt，返回 (user_prompt, results)"""
    if top_k is None:
        top_k = RAG_TOP_K

    index, meta = load_index(video_hash)
    query_embedding = await embed_single(question, video_id=video_id)
    results = search(index, meta, query_embedding, top_k)

    context_parts = []
    for r in results:
        c = r["chunk"]
        context_parts.append(f"【{_format_time(c['start_time'])}~{_format_time(c['end_time'])}】{c['text']}")
    context = "\n\n".join(context_parts)

    conv_ctx = await _get_conversation_context(video_id)
    mem_ctx = _get_memory_context()
    user_prompt = f"""{mem_ctx}{conv_ctx}以下是视频字幕中的相关片段：

{context}

以下是用户暂停时画面内容的分析：

{frame_description}

用户问题：{question}

请结合以上字幕内容、画面信息和对话上下文，综合回答用户问题。"""

    return user_prompt, results


def _get_memory_context() -> str:
    """加载长期记忆（用户偏好等），拼入 Prompt"""
    try:
        from backend.services.memory_service import format_memories_for_prompt
        return format_memories_for_prompt()
    except Exception:
        return ""


async def extract_memory(question: str, answer: str):
    """
    从一轮问答中提取值得长期记住的信息（用户偏好、学习主题等）
    调用 LLM 提取，结果存到 memory
    """
    from backend.services.memory_service import set_memory
    from backend.services.gateway import chat
    from backend.config import LLM_MAX_TOKENS

    # 对话太短不值得提取
    if len(question) < 5 or len(answer) < 20:
        return

    prompt = f"""从下面的视频学习对话中，提取值得长期记住的用户信息。

只提取以下两类，其他忽略：
1. 用户偏好：回答风格（简洁/详细）、语言偏好、是否需要举例等
2. 学习主题：用户正在学习什么（如 Transformer、RAG、机器学习等）

如果对话中没有明显的偏好或学习主题，输出"无"。

格式（每行一条，不要序号）：
偏好：xxx
主题：xxx

用户问题：{question}

AI回答：{answer[:300]}"""

    try:
        result, _ = await chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="你是记忆提取助手，只输出用户偏好和学习主题，没有则输出'无'。",
            max_tokens=200,
        )
        text = result.strip()
        if text == "无" or not text:
            return

        # 解析并存储
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("偏好：") and len(line) > 3:
                set_memory("user_preference", line[3:])
            elif line.startswith("主题：") and len(line) > 3:
                set_memory("learning_topic", line[3:])
    except Exception as e:
        logger.debug(f"Memory extraction failed: {e}")


async def _get_conversation_context(video_id: str) -> str:
    """加载对话上下文（含四层压缩）"""
    if not video_id:
        return ""
    try:
        from backend.services.conversation_service import get_recent_context
        return await get_recent_context(video_id)
    except Exception:
        return ""


async def _call_deepseek(user_prompt: str, video_id: str = None,
                         system_prompt: str = None, smart: bool = False) -> str:
    """调用 LLM（通过网关层），并记录 token 用量。

    smart=True 时使用独立的高阶模型（config.SMART_LLM_*，厂家可不同）。
    """
    from backend.services.cost_service import log_usage
    from backend.services.gateway import chat
    from backend.services.provider_service import get_provider
    from backend import config

    from backend.config import LLM_MAX_TOKENS
    if smart and config.SMART_LLM_API_KEY:
        model = config.SMART_LLM_MODEL
        provider = config.SMART_LLM_PROVIDER
    else:
        provider, cfg = get_provider("chat")
        model = cfg["model"]
    answer, usage = await chat(
        messages=[{"role": "user", "content": user_prompt}],
        system_prompt=system_prompt or SYSTEM_PROMPT,
        max_tokens=LLM_MAX_TOKENS,
        smart=smart,
    )

    log_usage(
        model=model,
        provider=provider,
        call_type="chat",
        input_tokens=usage.get("prompt_tokens", 0),
        output_tokens=usage.get("completion_tokens", 0),
        video_id=video_id,
    )

    logger.debug(f"LLM answer: {answer[:100]}...")
    return answer
