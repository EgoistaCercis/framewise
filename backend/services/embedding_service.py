"""
帧知 - Embedding向量化服务
通过硅基流动 SiliconFlow API 调用 BAAI/bge-m3
"""
import logging
import httpx
from backend.config import (
    SILICONFLOW_API_KEY, SILICONFLOW_BASE_URL, SILICONFLOW_EMBEDDING_MODEL
)

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 1024  # BGE-M3 输出维度


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    批量文本向量化

    参数:
        texts: 文本列表
    返回:
        向量列表 [[float, ...], ...]
    """
    if not texts:
        return []

    url = f"{SILICONFLOW_BASE_URL}/embeddings"
    headers = {
        "Authorization": f"Bearer {SILICONFLOW_API_KEY}",
        "Content-Type": "application/json",
    }

    # 分批处理（API限制）
    batch_size = 32
    all_embeddings = []

    async with httpx.AsyncClient(timeout=60.0) as client:
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            payload = {
                "model": SILICONFLOW_EMBEDDING_MODEL,
                "input": batch,
            }

            try:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()

                # 按索引排序结果
                batch_results = sorted(data["data"], key=lambda x: x["index"])
                batch_embeddings = [item["embedding"] for item in batch_results]
                all_embeddings.extend(batch_embeddings)

                # 记录 token 用量
                usage = data.get("usage", {})
                total_tokens = usage.get("total_tokens", 0)
                if total_tokens:
                    from backend.services.cost_service import log_usage
                    log_usage(
                        model=SILICONFLOW_EMBEDDING_MODEL,
                        provider="SiliconFlow",
                        call_type="embedding",
                        input_tokens=total_tokens,
                        output_tokens=0,
                    )

                logger.debug(f"Embedded batch {i // batch_size + 1}: {len(batch)} texts")
            except Exception as e:
                logger.error(f"Embedding API error for batch {i // batch_size + 1}: {e}")
                raise

    logger.info(f"Embedded {len(texts)} texts → {len(all_embeddings)} vectors ({len(all_embeddings[0])}d)")
    return all_embeddings


async def embed_single(text: str) -> list[float]:
    """单个文本向量化"""
    results = await embed_texts([text])
    return results[0]
