"""
帧知 - RAG问答服务
检索 + DeepSeek LLM 生成答案
"""
import logging
import httpx
from backend.config import (
    DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, RAG_TOP_K
)
from backend.services.embedding_service import embed_single
from backend.services.vector_store import load_index, search

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是视频学习助手"帧知"，根据视频字幕内容回答用户问题。

规则：
1. 优先依据检索到的字幕内容回答，不要使用外部知识
2. 回答中必须引用相关时间戳，格式：【MM:SS~MM:SS】（如【12:35~13:08】）
3. 如果字幕信息不足以回答，请明确说明"根据当前视频内容无法确定"，不要编造答案
4. 回答简洁清晰，适合学习场景
5. 可以引用多个相关片段来组织完整答案"""


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
    query_embedding = await embed_single(question)

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
    query_embedding = await embed_single(question)
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


async def _call_deepseek(user_prompt: str, video_id: str = None) -> str:
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
            {"role": "system", "content": SYSTEM_PROMPT},
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
