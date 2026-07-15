"""
帧知 - URL视频处理服务
通过 yt-dlp 获取音频和视频信息，不下载完整视频
"""
import os
import re
import json
import logging
import tempfile
import subprocess
from pathlib import Path

from backend.config import FFMPEG_PATH, DATA_DIR

logger = logging.getLogger(__name__)

AUDIO_DIR = os.path.join(DATA_DIR, "audio")


def get_video_info(url: str) -> dict:
    """
    获取视频元信息（不下载）

    返回: {title, duration, webpage_url, thumbnail, uploader, ...}
    """
    import yt_dlp

    opts = {
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    result = {
        "title": info.get("title", "Unknown"),
        "duration": info.get("duration", 0),
        "webpage_url": info.get("webpage_url", url),
        "thumbnail": info.get("thumbnail", ""),
        "uploader": info.get("uploader", ""),
        "embed_url": _get_embed_url(url, info),
    }
    logger.info(f"Video info: {result['title']} ({result['duration']}s)")
    return result


def download_audio(url: str, video_id: str) -> str:
    """
    只下载音频，返回音频文件路径

    返回: audio_path (wav, 16kHz mono)
    """
    import yt_dlp

    os.makedirs(AUDIO_DIR, exist_ok=True)
    output_template = os.path.join(AUDIO_DIR, f"{video_id}.%(ext)s")

    # 第一步：用 yt-dlp 下载最佳音频
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "128",
        }],
        "ffmpeg_location": os.path.dirname(FFMPEG_PATH),
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    # yt-dlp 输出是 mp3
    mp3_path = os.path.join(AUDIO_DIR, f"{video_id}.mp3")
    if not os.path.exists(mp3_path):
        raise FileNotFoundError(f"Audio download failed, expected: {mp3_path}")

    # 第二步：转成 16kHz mono WAV（faster-whisper 需要）
    wav_path = os.path.join(AUDIO_DIR, f"{video_id}.wav")
    cmd = [
        FFMPEG_PATH, "-y",
        "-i", mp3_path,
        "-ar", "16000",
        "-ac", "1",
        "-sample_fmt", "s16",
        wav_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)

    # 删除 mp3，只保留 wav
    os.remove(mp3_path)

    size_mb = os.path.getsize(wav_path) / 1024 / 1024
    logger.info(f"Audio downloaded: {wav_path} ({size_mb:.1f} MB)")
    return wav_path


def download_frame_at_time(url: str, timestamp: float, video_id: str) -> str:
    """
    下载视频指定时间点的一帧（不下载整个视频）

    返回: 帧图片路径
    """
    import yt_dlp

    from backend.config import FRAME_DIR
    from backend.services.cache_service import frame_cache_path, frame_cache_exists

    # 检查缓存
    frame_path = frame_cache_path(video_id, timestamp)
    if frame_cache_exists(video_id, timestamp):
        return frame_path

    os.makedirs(os.path.dirname(frame_path), exist_ok=True)

    # 用 yt-dlp 下载包含目标时间的视频片段，然后 ffmpeg 截帧
    audio_dir = AUDIO_DIR
    tmp_video = os.path.join(audio_dir, f"{video_id}_tmp.mp4")

    # 下载目标时间前后各 2 秒的视频片段（含视频流）
    ydl_opts = {
        "format": "best[height<=720]/best",
        "outtmpl": tmp_video,
        "quiet": True,
        "no_warnings": True,
        "download_ranges": lambda info, ydl: [{
            "start_time": max(0, timestamp - 1),
            "end_time": timestamp + 1,
        }],
        "force_keyframes_at_cuts": True,
        "ffmpeg_location": os.path.dirname(FFMPEG_PATH),
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    # 从片段中截取目标帧
    if os.path.exists(tmp_video):
        cmd = [
            FFMPEG_PATH, "-y",
            "-ss", "1",  # 片段中约在1秒位置
            "-i", tmp_video,
            "-vframes", "1",
            "-q:v", "2",
            frame_path,
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        os.remove(tmp_video)
        logger.info(f"Frame downloaded: {timestamp}s → {frame_path}")
    else:
        # fallback: yt-dlp 不支持 download_ranges，直接用原 URL 截取
        # 这种方式对 B站等支持较好
        _download_frame_fallback(url, timestamp, frame_path)

    return frame_path


def _download_frame_fallback(url: str, timestamp: float, output_path: str):
    """备用方案：用 ffmpeg 直接拉流截帧"""
    cmd = [
        FFMPEG_PATH, "-y",
        "-ss", str(timestamp),
        "-i", url,
        "-vframes", "1",
        "-q:v", "2",
        "-timeout", "15",
        output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=30)
    logger.info(f"Frame captured via ffmpeg: {timestamp}s → {output_path}")


def _get_embed_url(url: str, info: dict) -> str:
    """根据URL来源生成嵌入播放地址"""
    if "bilibili.com" in url or "b23.tv" in url:
        bvid = info.get("id", "")
        return f"//player.bilibili.com/player.html?bvid={bvid}&page=1&high_quality=1"
    if "youtube.com" in url or "youtu.be" in url:
        vid = info.get("id", "")
        return f"https://www.youtube.com/embed/{vid}"
    # 其他平台，返回原始URL
    return url


def cleanup_audio(video_id: str):
    """清理音频文件"""
    wav_path = os.path.join(AUDIO_DIR, f"{video_id}.wav")
    if os.path.exists(wav_path):
        os.remove(wav_path)
        logger.debug(f"Audio cleaned: {wav_path}")
