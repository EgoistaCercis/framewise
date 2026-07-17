"""
帧知 - ASR API 服务
优先用 DashScope Paraformer（支持直接传URL），fallback 到本地文件上传
"""
import os
import httpx
from loguru import logger


async def transcribe_via_url(audio_url: str, api_key: str) -> list[dict]:
    """
    通过 DashScope Paraformer 直接传入音频 URL（无需下载/上传）

    返回: [{text, start, end}, ...]
    """
    import dashscope
    from dashscope.audio.asr import Transcription
    from http import HTTPStatus

    dashscope.api_key = api_key

    logger.info(f"Submitting audio URL to DashScope Paraformer...")
    task = Transcription.async_call(
        model="paraformer-v2",
        file_urls=[audio_url],
        language_hints=["zh", "en"],
    )

    # 等待完成
    result = Transcription.wait(task=task.output.task_id)
    if result.status_code != HTTPStatus.OK:
        raise Exception(f"ASR failed: {result.code} - {result.message}")

    # 获取转录结果
    if not (result.output and result.output.get("results")):
        raise Exception("ASR: no results returned")

    transcript_url = result.output["results"][0]["transcription_url"]
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(transcript_url)
        resp.raise_for_status()
        transcription = resp.json()

    # 解析
    subtitles = []
    for item in transcription.get("transcripts", []):
        for sent in item.get("sentences", []):
            text = sent.get("text", "").strip()
            if text:
                subtitles.append({
                    "text": text,
                    "start": round(sent.get("begin_time", 0) / 1000, 2),
                    "end": round(sent.get("end_time", 0) / 1000, 2),
                })

    # 用量统计
    from backend.services.cost_service import log_usage
    log_usage(
        model="paraformer-v2",
        provider="DashScope",
        call_type="asr",
        input_tokens=sum(len(s["text"]) for s in subtitles) // 2,
        output_tokens=0,
    )

    logger.info(f"ASR complete via DashScope: {len(subtitles)} segments")
    return subtitles


async def transcribe_via_upload(audio_path: str, api_key: str, base_url: str) -> list[dict]:
    """
    Fallback: 硅基流动 SenseVoice（上传本地文件）

    返回: [{text, start, end}, ...]
    """
    url = f"{base_url}/audio/transcriptions"
    file_size = os.path.getsize(audio_path)
    logger.info(f"Uploading audio for ASR: {file_size / 1024 / 1024:.1f} MB")

    async with httpx.AsyncClient(timeout=300.0) as client:
        with open(audio_path, "rb") as f:
            ext = os.path.splitext(audio_path)[1].lower()
            mime_map = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4"}
            mime = mime_map.get(ext, "audio/wav")
            files = {"file": (os.path.basename(audio_path), f, mime)}
            data = {"model": "FunAudioLLM/SenseVoiceSmall", "response_format": "verbose_json"}
            headers = {"Authorization": f"Bearer {api_key}"}

            resp = await client.post(url, files=files, data=data, headers=headers)
            resp.raise_for_status()
            result = resp.json()

    subtitles = []
    for seg in result.get("segments", []):
        text = seg.get("text", "").strip()
        if text:
            subtitles.append({"text": text, "start": round(seg.get("start", 0), 2), "end": round(seg.get("end", 0), 2)})

    if not subtitles:
        text = result.get("text", "").strip()
        if text:
            subtitles.append({"text": text, "start": 0, "end": 0})

    from backend.services.cost_service import log_usage
    log_usage(model="FunAudioLLM/SenseVoiceSmall", provider="SiliconFlow",
              call_type="asr", input_tokens=file_size // 100, output_tokens=0)

    logger.info(f"ASR complete via SiliconFlow: {len(subtitles)} segments")
    return subtitles


async def transcribe_api(audio_path: str, api_key: str, base_url: str = "https://api.siliconflow.cn/v1",
                         audio_url: str = None, dashscope_key: str = None) -> list[dict]:
    """
    统一入口：优先通过 URL 直传 DashScope，失败则用本地文件上传

    参数:
        audio_path: 本地文件路径（fallback用）
        api_key: SiliconFlow API Key（fallback用）
        base_url: SiliconFlow API 地址
        audio_url: 音频的公网URL（DashScope直传）
        dashscope_key: DashScope API Key
    """
    # 优先：DashScope URL 直传
    if audio_url and dashscope_key:
        try:
            return await transcribe_via_url(audio_url, dashscope_key)
        except Exception as e:
            logger.warning(f"DashScope URL ASR failed: {e}, falling back to file upload")

    # Fallback：本地文件上传
    return await transcribe_via_upload(audio_path, api_key, base_url)
