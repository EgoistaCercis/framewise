"""
帧知 - 视觉理解服务
ffmpeg提取视频帧 + Qwen VL (DashScope) 画面分析
"""
import os
import base64
import logging
import subprocess
import httpx
from backend.config import DASHSCOPE_API_KEY, DASHSCOPE_VL_MODEL, FFMPEG_PATH
from backend.services.cache_service import frame_cache_path, frame_cache_exists

logger = logging.getLogger(__name__)


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


async def analyze_frame(frame_path: str) -> str:
    """
    使用 Qwen VL 分析帧内容

    参数:
        frame_path: 帧图片路径

    返回: 画面描述文本
    """
    # 读取图片并转base64
    with open(frame_path, "rb") as f:
        image_data = base64.b64encode(f.read()).decode("utf-8")

    # 调用 DashScope Qwen VL API
    url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
    headers = {
        "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": DASHSCOPE_VL_MODEL,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"image": f"data:image/jpeg;base64,{image_data}"},
                        {"text": "请详细描述这张图片/视频帧中的内容。包括：文字、图表、公式、代码、UI界面、人物动作等所有可见元素。如果是PPT或教学视频，描述幻灯片内容。"},
                    ],
                }
            ]
        },
        "parameters": {
            "max_tokens": 500,
        },
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        # 记录 token 用量
        usage = data.get("usage", {})
        from backend.services.cost_service import log_usage
        log_usage(
            model=DASHSCOPE_VL_MODEL,
            provider="DashScope",
            call_type="vision",
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )

        description = data["output"]["choices"][0]["message"]["content"]
        # 清理base64前缀（API有时会返回）
        if isinstance(description, list):
            description = " ".join(
                item.get("text", "") if isinstance(item, dict) else str(item)
                for item in description
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
