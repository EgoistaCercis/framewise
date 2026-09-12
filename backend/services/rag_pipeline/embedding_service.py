"""
帧知 - Embedding向量化服务
通过厂商标配层调用
"""
from loguru import logger
from backend.services.llm.gateway import embed
from backend.config import SILICONFLOW_EMBEDDING_MODEL

EMBEDDING_DIM = 1024  # BGE-M3 输出维度


async def embed_texts(texts: list[str], video_id: str = None) -> list[list[float]]:
    if not texts:
        return []

    # 用量记账由网关负责（按字符数估算的逻辑也挪过去了）
    embeddings = await embed(texts, video_id=video_id)

    logger.info(f"Embedded {len(texts)} texts ({sum(len(t) for t in texts)} chars)")
    return embeddings


async def embed_single(text: str, video_id: str = None) -> list[float]:
    results = await embed_texts([text], video_id=video_id)
    return results[0]
