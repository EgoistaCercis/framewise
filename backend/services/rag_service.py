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
    user_prompt = f"""以下是视频字幕中的相关片段：

{context}

用户问题：{question}

请根据以上字幕内容回答用户问题。"""

    answer = await _call_deepseek(user_prompt, video_id=video_id)

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

    user_prompt = f"""以下是视频字幕中的相关片段：

{context}

以下是用户暂停时画面内容的分析：

{frame_description}

用户问题：{question}

请结合以上字幕内容和画面信息，综合回答用户问题。"""

    answer = await _call_deepseek(user_prompt, video_id=video_id)

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

    answer = await _call_deepseek(user_prompt, video_id=video_id, system_prompt=QUIZ_PROMPT)

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


async def _call_deepseek(user_prompt: str, video_id: str = None, system_prompt: str = None) -> str:
    """调用 DeepSeek API，并记录 token 用量"""
    from backend.services.cost_service import log_usage

    url = f"{DEEPSEEK_BASE_URL}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt or SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.7,
        "max_tokens": 1024,
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        answer = data["choices"][0]["message"]["content"]

        # 记录用量
        usage = data.get("usage", {})
        log_usage(
            model=DEEPSEEK_MODEL,
            provider="DeepSeek",
            call_type="chat",
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            video_id=video_id,
        )

        logger.debug(f"DeepSeek answer: {answer[:100]}...")
        return answer
