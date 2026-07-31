"""
帧知 - 字幕文本Chunk切分服务
根据语义完整性和时间连续性切分字幕
"""
from loguru import logger
from backend.config import CHUNK_MAX_LENGTH, CHUNK_OVERLAP

CHUNK_MIN_LENGTH = 50  # 最短chunk阈值，太短的句子合并到相邻chunk


def chunk_subtitles(subtitles: list[dict], video_id: str) -> list[dict]:
    """
    将字幕列表切分为语义连续的Chunk

    策略:
    1. 按句子边界（标点符号）优先切分
    2. 小于 CHUNK_MIN_LENGTH 的句子合并到下一个chunk
    3. 控制每个Chunk长度不超过 CHUNK_MAX_LENGTH
    4. 保留时间连续性

    返回: [{chunk_id, video_id, text, start_time, end_time, segments}, ...]
    """
    chunks = []
    current_texts = []
    current_start = None
    current_end = None
    current_segments = []

    chunk_idx = 0

    for seg in subtitles:
        text = seg["text"]
        start = seg["start"]
        end = seg["end"]

        if current_start is None:
            current_start = start

        current_texts.append(text)
        current_end = end
        current_segments.append(seg)

        combined = " ".join(current_texts)

        # 达到最大长度 → 必须切分
        if len(combined) >= CHUNK_MAX_LENGTH:
            chunks.append({
                "chunk_id": f"{video_id}_chunk_{chunk_idx:04d}",
                "video_id": video_id,
                "text": combined,
                "start_time": current_start,
                "end_time": current_end,
                "segments": current_segments,
            })
            chunk_idx += 1
            current_texts = []
            current_start = None
            current_end = None
            current_segments = []
        # 遇到句尾 且 累积长度 >= 最小阈值 → 切分
        elif _is_sentence_end(text) and len(combined) >= CHUNK_MIN_LENGTH:
            chunks.append({
                "chunk_id": f"{video_id}_chunk_{chunk_idx:04d}",
                "video_id": video_id,
                "text": combined,
                "start_time": current_start,
                "end_time": current_end,
                "segments": current_segments,
            })
            chunk_idx += 1
            current_texts = []
            current_start = None
            current_end = None
            current_segments = []
        # 否则继续累积（短句合并到相邻句子）

    # 处理剩余文本
    if current_texts:
        chunks.append({
            "chunk_id": f"{video_id}_chunk_{chunk_idx:04d}",
            "video_id": video_id,
            "text": " ".join(current_texts),
            "start_time": current_start,
            "end_time": current_end,
            "segments": current_segments,
        })

    logger.info(f"Chunked {len(subtitles)} segments into {len(chunks)} chunks (min={CHUNK_MIN_LENGTH})")
    return chunks


def _is_sentence_end(text: str) -> bool:
    """判断文本是否为句子结尾"""
    endings = {"。", "！", "？", ".", "!", "?", "\n", "；", ";"}
    return any(text.rstrip().endswith(e) for e in endings)
