"""
帧知 - ASR API 服务
通过厂商标配层调用
"""
import os
import httpx
from loguru import logger


async def transcribe_via_url(audio_url: str, api_key: str) -> list[dict]:
    """DashScope Paraformer URL 直传"""
    from backend.services.provider_service import call_asr_url
    return await call_asr_url(audio_url)


async def transcribe_via_upload(audio_path: str, api_key: str = None, base_url: str = None) -> list[dict]:
    """本地文件上传 ASR（通过厂商标配层）"""
    from backend.services.provider_service import call_asr_upload

    file_size = os.path.getsize(audio_path)
    logger.info(f"Uploading audio for ASR: {file_size / 1024 / 1024:.1f} MB")
    subtitles = await call_asr_upload(audio_path)

    from backend.services.provider_service import get_provider
    provider, cfg = get_provider("asr")
    from backend.services.cost_service import log_usage
    log_usage(model=cfg["model"], provider=provider, call_type="asr",
              input_tokens=file_size // 100, output_tokens=0)

    logger.info(f"ASR complete: {len(subtitles)} segments via {provider}")
    return subtitles


async def transcribe_api(audio_path: str, api_key: str = None, base_url: str = None,
                         audio_url: str = None, dashscope_key: str = None) -> list[dict]:
    """统一入口：优先 URL 直传 DashScope，失败则上传文件"""
    if audio_url and dashscope_key:
        try:
            return await transcribe_via_url(audio_url, dashscope_key)
        except Exception as e:
            logger.warning(f"DashScope URL ASR failed: {e}, falling back to file upload")

    return await transcribe_via_upload(audio_path, api_key, base_url)
