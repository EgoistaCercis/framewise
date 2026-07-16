"""
帧知 - ASR语音识别服务
基于 faster-whisper 实现本地语音转文字
"""
import os
from loguru import logger
from backend.config import WHISPER_MODEL_SIZE, WHISPER_DEVICE, WHISPER_COMPUTE_TYPE, FFMPEG_PATH

# 全局模型实例（懒加载）
_model = None

# 本地模型路径（优先使用，避免 HF Hub 下载）
_LOCAL_MODEL_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "models", "faster-whisper-tiny")
)


def _get_model():
    """懒加载 whisper 模型（优先从本地加载）"""
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        # 优先使用本地下载的模型，没有则让 faster-whisper 自行下载
        if os.path.exists(_LOCAL_MODEL_DIR) and os.path.exists(os.path.join(_LOCAL_MODEL_DIR, "model.bin")):
            model_path = _LOCAL_MODEL_DIR
            logger.info(f"Loading Whisper model from local: {model_path}")
        else:
            model_path = WHISPER_MODEL_SIZE
            logger.info(f"Loading Whisper model: {WHISPER_MODEL_SIZE} (will download from HF)")

        _model = WhisperModel(
            model_path,
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE_TYPE,
        )
        logger.info("Whisper model loaded successfully")
    return _model


def extract_audio(video_path: str, audio_path: str):
    """从视频中提取音频（使用ffmpeg）"""
    import subprocess
    cmd = [
        FFMPEG_PATH, "-y", "-i", video_path,
        "-vn", "-acodec", "pcm_s16le",
        "-ar", "16000", "-ac", "1",
        audio_path
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    logger.info(f"Audio extracted to {audio_path}")


def transcribe(audio_path: str) -> list[dict]:
    """
    使用 faster-whisper 转录音频
    返回: [{text, start, end}, ...]
    """
    model = _get_model()
    segments, info = model.transcribe(audio_path, beam_size=5, language="zh")
    logger.info(f"Detected language: {info.language} (probability: {info.language_probability:.2f})")

    subtitles = []
    for seg in segments:
        subtitles.append({
            "text": seg.text.strip(),
            "start": round(seg.start, 2),
            "end": round(seg.end, 2),
        })

    logger.info(f"Transcription complete: {len(subtitles)} segments")
    return subtitles


async def process_video_to_subtitles(video_path: str, video_hash: str) -> list[dict]:
    """
    完整流程: 提取音频 → ASR转录 → 返回字幕
    """
    import tempfile
    from backend.services.cache_service import (
        subtitle_cache_exists, load_subtitle_cache, save_subtitle_cache
    )

    # 检查字幕缓存
    if subtitle_cache_exists(video_hash):
        logger.info(f"Subtitle cache hit: {video_hash}")
        return load_subtitle_cache(video_hash)

    # 提取音频
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        audio_path = tmp.name

    try:
        extract_audio(video_path, audio_path)
        subtitles = transcribe(audio_path)
        save_subtitle_cache(video_hash, subtitles)
        return subtitles
    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)
            logger.debug(f"Cleaned up audio temp file: {audio_path}")
