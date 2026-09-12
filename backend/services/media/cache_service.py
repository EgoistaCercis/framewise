"""
帧知 - 文件级缓存服务
三级缓存: 视频Hash → 字幕缓存 → Embedding缓存 → 帧缓存
"""
import os
import json
import hashlib
from loguru import logger

from backend.config import SUBTITLE_DIR, EMBEDDING_DIR, FRAME_DIR


def _file_hash(filepath: str) -> str:
    """计算文件MD5"""
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def get_video_hash(video_path: str) -> str:
    return _file_hash(video_path)


# ── 字幕缓存 ──
#
# 字幕有两个来源，质量差异很大：
#   official —— B站官方/AI字幕（精准、带准确时间戳）
#   asr      —— 本地语音识别兜底（有错字，时间戳为估算）
#
# 两者写同一个缓存文件。历史 bug：ASR 完成得晚，把先落盘的官方字幕覆盖掉了。
# 现在引入来源优先级 + 写前检查，低优先级来源不会覆盖高优先级的。
#
# 来源存在「边车文件」{id}.src.txt 里（内容就一个词），不改 .json 格式，
# 避免破坏已有缓存与所有读取方。

SOURCE_PRIORITY = {"official": 2, "asr": 1}


def subtitle_cache_path(video_hash: str) -> str:
    return os.path.join(SUBTITLE_DIR, f"{video_hash}.json")


def _subtitle_source_path(video_hash: str) -> str:
    return os.path.join(SUBTITLE_DIR, f"{video_hash}.src.txt")


def subtitle_cache_exists(video_hash: str) -> bool:
    return os.path.exists(subtitle_cache_path(video_hash))


def load_subtitle_cache(video_hash: str) -> list[dict]:
    path = subtitle_cache_path(video_hash)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_subtitle_source(video_hash: str) -> str:
    """返回 'official' / 'asr'；老缓存没有边车文件则返回 ''（未知）"""
    path = _subtitle_source_path(video_hash)
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def save_subtitle_cache(video_hash: str, subtitles: list[dict],
                        source: str = "asr", force: bool = False) -> bool:
    """写字幕缓存。

    已有更高优先级来源时跳过（除非 force）。返回是否真的写入。
    """
    if not force and subtitle_cache_exists(video_hash):
        existing = load_subtitle_source(video_hash)
        if SOURCE_PRIORITY.get(existing, 0) >= SOURCE_PRIORITY.get(source, 0):
            logger.info(
                f"字幕缓存已存在（来源 {existing or '未知'}，优先级 >= {source}），跳过写入: {video_hash}"
            )
            return False

    path = subtitle_cache_path(video_hash)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(subtitles, f, ensure_ascii=False, indent=2)
    with open(_subtitle_source_path(video_hash), "w", encoding="utf-8") as f:
        f.write(source)
    return True


def clear_subtitle_cache(video_hash: str):
    """删除字幕缓存与来源标记（重新生成时用）"""
    for p in (subtitle_cache_path(video_hash), _subtitle_source_path(video_hash)):
        if os.path.exists(p):
            os.remove(p)


# ── Embedding缓存 (FAISS索引) ──

def embedding_cache_path(video_hash: str) -> str:
    return os.path.join(EMBEDDING_DIR, f"{video_hash}.faiss")


def embedding_cache_exists(video_hash: str) -> bool:
    return os.path.exists(embedding_cache_path(video_hash))


def embedding_meta_path(video_hash: str) -> str:
    return os.path.join(EMBEDDING_DIR, f"{video_hash}.meta.json")


def save_embedding_meta(video_hash: str, meta: dict):
    path = embedding_meta_path(video_hash)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def load_embedding_meta(video_hash: str) -> dict:
    path = embedding_meta_path(video_hash)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ── 帧缓存 ──

def frame_cache_path(video_hash: str, timestamp: float) -> str:
    os.makedirs(os.path.join(FRAME_DIR, video_hash), exist_ok=True)
    return os.path.join(FRAME_DIR, video_hash, f"{timestamp:.1f}.jpg")


def frame_cache_exists(video_hash: str, timestamp: float) -> bool:
    return os.path.exists(frame_cache_path(video_hash, timestamp))
