"""
帧知 - Embedding向量化服务
通过厂商标配层调用
"""
from loguru import logger
from backend.services.llm.gateway import embed
from backend.services.llm.provider_service import get_provider
from backend.services.llm.cost_service import log_usage
from backend.config import SILICONFLOW_EMBEDDING_MODEL

EMBEDDING_DIM = 1024  # BGE-M3 输出维度


async def embed_texts(texts: list[str], video_id: str = None) -> list[list[float]]:
    if not texts:
        return []

    provider, cfg = get_provider("embedding")
    embeddings = await embed(texts)

    # 记录用量（估算）
    total_chars = sum(len(t) for t in texts)
    log_usage(
        model=cfg["model"],
        provider=provider,
        call_type="embedding",
        input_tokens=total_chars // 2,
        output_tokens=0,
        video_id=video_id,
    )

    logger.info(f"Embedded {len(texts)} texts ({total_chars} chars)")
    return embeddings


async def embed_single(text: str, video_id: str = None) -> list[float]:
    results = await embed_texts([text], video_id=video_id)
    return results[0]
