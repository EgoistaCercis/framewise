"""
帧知 - 字幕文本Chunk切分服务
根据语义完整性和时间连续性切分字幕
"""
import logging
from backend.config import CHUNK_MAX_LENGTH, CHUNK_OVERLAP

logger = logging.getLogger(__name__)


def chunk_subtitles(subtitles: list[dict], video_id: str) -> list[dict]:
    """
    将字幕列表切分为语义连续的Chunk

    策略:
    1. 按句子边界（标点符号）优先切分
    2. 控制每个Chunk长度不超过 CHUNK_MAX_LENGTH
    3. 保留时间连续性
    4. Chunk之间有少量重叠

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

        # 达到最大长度 或 遇到句尾标点 → 切分
        if len(combined) >= CHUNK_MAX_LENGTH or _is_sentence_end(text):
            chunk_text = combined
            chunks.append({
                "chunk_id": f"{video_id}_chunk_{chunk_idx:04d}",
                "video_id": video_id,
                "text": chunk_text,
                "start_time": current_start,
                "end_time": current_end,
                "segments": current_segments,
            })
            chunk_idx += 1

            # 重叠处理：保留最后一小段到下个chunk
            if CHUNK_OVERLAP > 0 and len(current_texts) > 1:
                overlap_text = current_texts[-1]
                current_texts = [overlap_text]
                current_start = current_segments[-1]["start"]
                current_segments = [current_segments[-1]]
            else:
                current_texts = []
                current_start = None
                current_end = None
                current_segments = []

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

    logger.info(f"Chunked {len(subtitles)} segments into {len(chunks)} chunks")
    return chunks


def _is_sentence_end(text: str) -> bool:
    """判断文本是否为句子结尾"""
    endings = {"。", "！", "？", ".", "!", "?", "\n", "；", ";"}
    return any(text.rstrip().endswith(e) for e in endings)
