"""
帧知 - URL视频处理服务
通过 yt-dlp 获取音频和视频信息，不下载完整视频
"""
import os
import re
import json
from loguru import logger
import tempfile
import subprocess
from pathlib import Path

from backend.config import FFMPEG_PATH, DATA_DIR, ASR_MODE
from backend.services.cache_service import frame_cache_path, frame_cache_exists

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


def _extract_bilibili_subtitles_api(bvid: str, p: int = 1) -> list[dict] | None:
    """直接调用B站API获取字幕"""
    import httpx
    try:
        # Step 1: 获取视频 cid
        info_url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
        r = httpx.get(info_url, timeout=15,
                      headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"})
        r.raise_for_status()
        data = r.json()["data"]

        # 获取指定分P的 cid
        pages = data.get("pages", [])
        cid = pages[p - 1]["cid"] if p <= len(pages) and p > 0 else pages[0]["cid"]

        # Step 2: 获取字幕列表
        sub_url = f"https://api.bilibili.com/x/player/v2?bvid={bvid}&cid={cid}"
        r = httpx.get(sub_url, timeout=15,
                      headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"})
        r.raise_for_status()
        sub_data = r.json()["data"].get("subtitle", {}).get("subtitles", [])

        if not sub_data:
            return None

        # 取中文字幕
        sub_info = next((s for s in sub_data if "zh" in s.get("lan_doc", "").lower()), sub_data[0])
        sub_url = "https:" + sub_info["subtitle_url"] if sub_info["subtitle_url"].startswith("//") else sub_info["subtitle_url"]

        r = httpx.get(sub_url, timeout=15)
        r.raise_for_status()
        body = r.json()["body"]

        subtitles = []
        for item in body:
            text = item.get("content", "").strip()
            if text:
                subtitles.append({
                    "text": text,
                    "start": round(item.get("from", 0), 2),
                    "end": round(item.get("to", 0), 2),
                })
        return subtitles if subtitles else None
    except Exception as e:
        logger.debug(f"B站API subtitles: {e}")
        return None


def extract_subtitles(url: str) -> list[dict] | None:
    """提取B站视频字幕（优先B站API，fallback yt-dlp）"""
    import re

    # 1. B站API（最准）
    bv = re.search(r'(BV\w+)', url)
    if bv:
        p = int((re.search(r'[?&]p=(\d+)', url) or [None, 1])[1] or 1)
        result = _extract_bilibili_subtitles_api(bv.group(1), p)
        if result:
            logger.info(f"Got B站 subtitles via API: {len(result)} segments")
            return result

    # 2. yt-dlp fallback
    import yt_dlp
    ydl_opts = {
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["zh-Hans", "zh", "en"],
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "force_ipv4": True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            subs = info.get("subtitles") or info.get("automatic_captions") or {}
            for lang in ["zh-Hans", "zh", "zh-CN", "en"]:
                entries = subs.get(lang)
                if entries:
                    # 下载字幕文件
                    sub_url = entries[-1]["url"]  # 取最后一个格式
                    import httpx
                    r = httpx.get(sub_url, timeout=30, follow_redirects=True)
                    r.raise_for_status()
                    return _parse_vtt(r.text)
        logger.info("No B站 subtitles found, will fall back to ASR")
    except Exception as e:
        logger.warning(f"Failed to extract subtitles: {e}")
    return None


def _parse_vtt(vtt_text: str) -> list[dict]:
    """解析 VTT 字幕文件为帧知格式"""
    import re
    subtitles = []
    # 匹配时间戳行: 00:00:01.000 --> 00:00:05.000
    pattern = r'(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[.,](\d{3})'
    parts = re.split(pattern, vtt_text)

    for i in range(1, len(parts), 9):
        if i + 8 >= len(parts):
            break
        h1, m1, s1, ms1 = int(parts[i]), int(parts[i+1]), int(parts[i+2]), int(parts[i+3])
        h2, m2, s2, ms2 = int(parts[i+4]), int(parts[i+5]), int(parts[i+6]), int(parts[i+7])
        text = parts[i+8].strip()
        # 清理 VTT 标签
        text = re.sub(r'<[^>]+>', '', text)
        text = text.replace('&nbsp;', ' ').replace('&amp;', '&')
        if text and text not in {'WEBVTT', 'Kind: captions', 'Language:'}:
            subtitles.append({
                "text": text,
                "start": round(h1 * 3600 + m1 * 60 + s1 + ms1 / 1000, 2),
                "end": round(h2 * 3600 + m2 * 60 + s2 + ms2 / 1000, 2),
            })
    return subtitles


def get_audio_stream_url(url: str) -> str | None:
    """用 yt-dlp 获取音频直链（不下载），直接传给 ASR API 处理"""
    import yt_dlp
    ydl_opts = {
        "format": "bestaudio[protocol!=m3u8]/bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "force_ipv4": True,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.bilibili.com/",
        },
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            stream_url = info.get("url", "")
            if stream_url:
                logger.info(f"Got audio stream URL ({len(stream_url)} chars)")
                return stream_url
    except Exception as e:
        logger.warning(f"Failed to get stream URL: {e}")
    return None


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
        "format": "bestaudio[protocol!=m3u8]/bestaudio/best",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "retries": 5,
        "socket_timeout": 30,
        "force_ipv4": True,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.bilibili.com/",
        },
    }
    # 不下载完整视频，只下原始音频流
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    # 查找下载的文件（可能是 webm/m4a/opus 等格式）
    raw_file = None
    for ext in ["m4a", "webm", "opus", "mp4", "mkv"]:
        candidate = os.path.join(AUDIO_DIR, f"{video_id}.{ext}")
        if os.path.exists(candidate):
            raw_file = candidate
            break

    if not raw_file:
        raise FileNotFoundError(f"Audio download failed for {video_id}")

    logger.info(f"Raw audio: {raw_file} ({os.path.getsize(raw_file)/1024/1024:.1f} MB)")

    # 转成 mp3 (16kHz mono, 48kbps) — 小体积，API 上传快
    final_path = os.path.join(AUDIO_DIR, f"{video_id}_asr.mp3")
    cmd = [
        FFMPEG_PATH, "-y",
        "-i", raw_file,
        "-ar", "16000",
        "-ac", "1",
        "-b:a", "48k",
        final_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"ffmpeg failed: {result.stderr[:200]}")
        raise RuntimeError(f"Audio conversion failed: {result.stderr[:100]}")

    # 删除原始文件
    os.remove(raw_file)

    size_mb = os.path.getsize(final_path) / 1024 / 1024
    logger.info(f"Audio ready: {final_path} ({size_mb:.1f} MB)")
    return final_path


def download_frame_at_time(url: str, timestamp: float, video_id: str) -> str:
    """
    下载视频指定时间点的一帧（不下载整个视频）
    策略: 用 yt-dlp 获取直链 → ffmpeg 拉流截帧

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

    # 方案1: 用 yt-dlp -g 获取直链，ffmpeg 拉流截帧（最快最稳定）
    try:
        stream_url = _get_stream_url(url)
        if stream_url:
            cmd = [
                FFMPEG_PATH, "-y",
                "-ss", str(timestamp),
                "-i", stream_url,
                "-vframes", "1",
                "-q:v", "2",
                "-timeout", "20",
                frame_path,
            ]
            subprocess.run(cmd, check=True, capture_output=True, timeout=30)
            logger.info(f"Frame captured via stream: {timestamp}s → {frame_path}")
            return frame_path
    except Exception as e:
        logger.warning(f"Stream capture failed: {e}, trying fallback...")

    # 方案2: ffmpeg 直接拉原 URL
    try:
        _download_frame_fallback(url, timestamp, frame_path)
    except Exception as e:
        logger.error(f"Frame fallback also failed: {e}")
        raise

    return frame_path


def _get_stream_url(url: str) -> str:
    """用 yt-dlp 获取最佳视频流直链（不下载）"""
    import yt_dlp
    ydl_opts = {
        "format": "best[height<=720]/best",
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return info.get("url", "")


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
