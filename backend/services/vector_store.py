"""
帧知 - FAISS向量存储与检索
"""
import os
import logging
import numpy as np
import faiss
from backend.config import EMBEDDING_DIR, RAG_TOP_K
from backend.services.cache_service import (
    embedding_cache_path, embedding_meta_path,
    embedding_cache_exists, save_embedding_meta, load_embedding_meta
)

logger = logging.getLogger(__name__)


def build_index(chunks: list[dict], embeddings: list[list[float]], video_hash: str):
    """
    构建FAISS索引并持久化

    参数:
        chunks: Chunk列表
        embeddings: 对应的向量列表
        video_hash: 视频hash
    """
    dim = len(embeddings[0])
    vectors = np.array(embeddings, dtype=np.float32)

    # 构建索引 (内积搜索，适合归一化向量)
    index = faiss.IndexFlatIP(dim)

    # L2归一化（内积 = 余弦相似度）
    faiss.normalize_L2(vectors)
    index.add(vectors)

    # 持久化索引
    index_path = embedding_cache_path(video_hash)
    faiss.write_index(index, index_path)

    # 保存元数据（chunk信息）
    meta = {
        "video_hash": video_hash,
        "chunks": chunks,
        "dim": dim,
        "count": len(chunks),
    }
    save_embedding_meta(video_hash, meta)

    logger.info(f"FAISS index built: {len(chunks)} vectors ({dim}d) → {index_path}")
    return index


def load_index(video_hash: str):
    """加载FAISS索引和元数据"""
    if not embedding_cache_exists(video_hash):
        raise FileNotFoundError(f"FAISS index not found for {video_hash}")

    index_path = embedding_cache_path(video_hash)
    index = faiss.read_index(index_path)
    meta = load_embedding_meta(video_hash)

    logger.debug(f"FAISS index loaded: {meta['count']} vectors ({meta['dim']}d)")
    return index, meta


def search(index, meta: dict, query_embedding: list[float], top_k: int = None) -> list[dict]:
    """
    向量检索

    参数:
        index: FAISS索引
        meta: 元数据（含chunks）
        query_embedding: 查询向量
        top_k: 返回数量

    返回:
        [{chunk, score}, ...] 按相似度降序
    """
    if top_k is None:
        top_k = RAG_TOP_K

    query_vec = np.array([query_embedding], dtype=np.float32)
    faiss.normalize_L2(query_vec)

    scores, indices = index.search(query_vec, min(top_k, meta["count"]))

    results = []
    for score, idx in zip(scores[0], indices[0]):
        if idx >= 0 and idx < len(meta["chunks"]):
            results.append({
                "chunk": meta["chunks"][idx],
                "score": float(score),
            })

    logger.debug(f"Search returned {len(results)} results, top score: {results[0]['score']:.3f}" if results else "Search: no results")
    return results
