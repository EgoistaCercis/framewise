"""
帧知 - 文件级缓存服务
三级缓存: 视频Hash → 字幕缓存 → Embedding缓存 → 帧缓存
"""
import os
import json
import hashlib
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

def subtitle_cache_path(video_hash: str) -> str:
    return os.path.join(SUBTITLE_DIR, f"{video_hash}.json")


def subtitle_cache_exists(video_hash: str) -> bool:
    return os.path.exists(subtitle_cache_path(video_hash))


def load_subtitle_cache(video_hash: str) -> list[dict]:
    path = subtitle_cache_path(video_hash)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_subtitle_cache(video_hash: str, subtitles: list[dict]):
    path = subtitle_cache_path(video_hash)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(subtitles, f, ensure_ascii=False, indent=2)


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
