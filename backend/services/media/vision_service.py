"""
帧知 - 视觉理解服务
ffmpeg提取视频帧 + Qwen VL (DashScope) 画面分析
"""
import asyncio
import os
import base64
from loguru import logger
import subprocess
import httpx
from backend.config import DASHSCOPE_API_KEY, DASHSCOPE_VL_MODEL, FFMPEG_PATH
from backend.services.media.cache_service import frame_cache_path, frame_cache_exists


def extract_frame_sync(video_path: str, timestamp: float, video_hash: str) -> str:
    """抽帧的同步实现（ffmpeg 子进程是阻塞的）"""
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


async def extract_frame(video_path: str, timestamp: float, video_hash: str) -> str:
    """
    从视频中提取指定时间戳的帧

    参数:
        video_path: 视频文件路径
        timestamp: 时间戳（秒）
        video_hash: 视频hash（用于缓存）

    返回: 帧图片路径

    实现说明：ffmpeg 是同步子进程，直接在事件循环里跑会把并发退化成串行
    （Agent 并行抽帧时尤其明显），所以丢到线程池；对外仍是 async 接口。
    """
    return await asyncio.to_thread(extract_frame_sync, video_path, timestamp, video_hash)


async def analyze_frame(frame_path: str, video_id: str = None, prompt: str = None) -> str:
    """使用视觉模型分析帧内容（通过厂商标配层）

    prompt 默认用结构化的 VISION_PROMPT（而不是泛泛的「详细描述」）：
    输出更短更聚焦，实测单帧 7.1s → 1.3~3.4s，且不会被 max_tokens 截断。
    """
    import base64
    from backend.prompts import VISION_PROMPT
    from backend.services.llm.gateway import vision

    with open(frame_path, "rb") as f:
        image_data = base64.b64encode(f.read()).decode("utf-8")

    # 用量记账由网关负责（原来这里自己调 log_usage，与网关收口后重复）
    description, _ = await vision(
        image_data, prompt=prompt or VISION_PROMPT, video_id=video_id,
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
