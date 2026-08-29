"""
帧知 - 视觉理解服务
ffmpeg提取视频帧 + Qwen VL (DashScope) 画面分析
"""
import os
import base64
from loguru import logger
import subprocess
import httpx
from backend.config import DASHSCOPE_API_KEY, DASHSCOPE_VL_MODEL, FFMPEG_PATH
from backend.services.cache_service import frame_cache_path, frame_cache_exists


async def extract_frame(video_path: str, timestamp: float, video_hash: str) -> str:
    """
    从视频中提取指定时间戳的帧

    参数:
        video_path: 视频文件路径
        timestamp: 时间戳（秒）
        video_hash: 视频hash（用于缓存）

    返回: 帧图片路径
    """
    cache_path = frame_cache_path(video_hash, timestamp)

    # 检查帧缓存
    if frame_cache_exists(video_hash, timestamp):
        logger.debug(f"Frame cache hit: {video_hash} @ {timestamp}s")
        return cache_path

    # ffmpeg 截取帧
    cmd = [
        FFMPEG_PATH, "-y",
        "-ss", str(timestamp),
        "-i", video_path,
        "-vframes", "1",
        "-q:v", "2",
        cache_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    logger.info(f"Frame extracted: {video_hash} @ {timestamp}s → {cache_path}")
    return cache_path


async def analyze_frame(frame_path: str, video_id: str = None) -> str:
    """使用视觉模型分析帧内容（通过厂商标配层）"""
    import base64
    from backend.services.gateway import vision
    from backend.services.provider_service import get_provider
    from backend.services.cost_service import log_usage

    with open(frame_path, "rb") as f:
        image_data = base64.b64encode(f.read()).decode("utf-8")

    provider, cfg = get_provider("vision")
    description, usage = await vision(image_data)

    log_usage(
        model=cfg["model"],
        provider=provider,
        call_type="vision",
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        cached_tokens=usage.get("cached_tokens", 0),
        reasoning_tokens=usage.get("reasoning_tokens", 0),
        video_id=video_id,
    )

    logger.info(f"Frame analyzed: {description[:100]}...")
    return description


async def process_frame_question(
    video_path: str,
    video_hash: str,
    timestamp: float,
    question: str,
) -> dict:
    """
    完整画面问答流程：提取帧 → 分析 → 返回描述

    返回: {frame_path, description}
    """
    # Step 1: 提取帧
    frame_path = await extract_frame(video_path, timestamp, video_hash)

    # Step 2: 视觉分析
    description = await analyze_frame(frame_path)

    return {
        "frame_path": frame_path,
        "description": description,
    }
