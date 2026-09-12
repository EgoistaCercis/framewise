"""
帧知 - 评测用媒体工具

抽帧优先用本地视频文件（项目文档/评测/视频/），失败再走 URL 拉流。

为什么优先本地：
- 快（无 yt-dlp + 网络）
- 稳（部分 B站视频 yt-dlp 拿不到 formats，如登录态受限的视频）
"""
import asyncio
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_VIDEO_DIR = os.path.join(BASE, "项目文档", "评测", "视频")

# 并发抽帧时，同一 (视频, 时间点) 可能被两个协程同时命中缓存未命中分支，
# 两个 ffmpeg 写同一个输出文件会写出坏图。按 key 加锁即可。
_frame_locks: dict[tuple, asyncio.Lock] = {}


def _lock_for(video_id: str, timestamp: float) -> asyncio.Lock:
    key = (video_id, round(float(timestamp), 3))
    if key not in _frame_locks:
        _frame_locks[key] = asyncio.Lock()
    return _frame_locks[key]

# 评测视频 → 本地文件名关键词（一一对应，无歧义）
_KEYWORD = {
    "b8739307cc61": "Transformer",
    "a16bdbbe10cf": "注意力机制",
    "8f5733550739": "LayerNorm",
    "3362144c2032": "N皇后",
    "d7c8a5aa25af": "环形链表",
    "079807e4a7e0": "爬楼梯",
    "116ab6082bb7": "Deep Agents",
    "adef16e057e2": "ClaudeCode",
}


def local_video_path(video_id: str) -> str | None:
    """按 video_id 找本地评测视频文件，找不到返回 None"""
    kw = _KEYWORD.get(video_id)
    if not kw or not os.path.isdir(LOCAL_VIDEO_DIR):
        return None
    for f in os.listdir(LOCAL_VIDEO_DIR):
        if kw in f and f.lower().endswith((".mp4", ".mkv", ".flv", ".webm")):
            return os.path.join(LOCAL_VIDEO_DIR, f)
    return None


async def extract_frame_for(video_id: str, state: dict, timestamp: float) -> tuple:
    """抽帧，返回 (frame_path, error)。优先本地，回退 URL。

    `extract_frame` 内部是同步 `subprocess.run`，直接 await 会阻塞事件循环、
    把并发退化成串行，所以丢到线程池；同一时间点再按锁串行，避免写坏缓存图。
    """
    local = local_video_path(video_id)
    if local:
        from backend.services.media.vision_service import extract_frame
        try:
            async with _lock_for(video_id, timestamp):
                return await extract_frame(local, timestamp, video_id), None
        except Exception as e:
            return None, f"local: {type(e).__name__}: {str(e)[:100]}"

    if state.get("video_path"):
        from backend.services.media.vision_service import extract_frame
        try:
            async with _lock_for(video_id, timestamp):
                return await extract_frame(state["video_path"], timestamp, video_id), None
        except Exception as e:
            return None, f"video_path: {type(e).__name__}: {str(e)[:100]}"

    if state.get("is_url_mode") and state.get("url"):
        from backend.services.media.url_service import download_frame_at_time
        try:
            return await asyncio.to_thread(
                download_frame_at_time, state["url"], timestamp, video_id
            ), None
        except Exception as e:
            return None, f"url: {type(e).__name__}: {str(e)[:100]}"

    return None, "无可用视频源"
