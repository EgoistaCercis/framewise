"""
帧知 - API 厂商标配化抽象层
统一封装 Chat / Embedding / Vision / ASR 调用
新增厂家只需在 PROVIDERS 配置中加一行
"""
import httpx
from loguru import logger

# ── 厂商标配表 ──
# 格式: { 厂家名: { 服务类型: { endpoint, model, api_key, ... } } }
# 新增厂家：复制下面任意一个配置块，修改参数即可

def _load_providers():
    """从 config.py 的 .env 变量构建厂商标配表"""
    from backend.config import (
        LLM_PROVIDER, LLM_ENDPOINT, LLM_API_KEY, LLM_MODEL,
        EMBEDDING_PROVIDER, EMBEDDING_ENDPOINT, EMBEDDING_API_KEY, EMBEDDING_MODEL,
        VISION_PROVIDER, VISION_ENDPOINT, VISION_API_KEY, VISION_MODEL, VISION_FORMAT,
        ASR_PROVIDER, ASR_ENDPOINT, ASR_API_KEY, ASR_MODEL_ASR,
        ASR_URL_PROVIDER, ASR_URL_API_KEY,
    )
    providers = {}

    # Chat
    if LLM_API_KEY:
        providers[LLM_PROVIDER] = providers.get(LLM_PROVIDER, {})
        providers[LLM_PROVIDER]["chat"] = {
            "endpoint": LLM_ENDPOINT, "model": LLM_MODEL,
            "api_key": LLM_API_KEY, "format": "openai_chat",
        }

    # Embedding
    if EMBEDDING_API_KEY:
        providers[EMBEDDING_PROVIDER] = providers.get(EMBEDDING_PROVIDER, {})
        providers[EMBEDDING_PROVIDER]["embedding"] = {
            "endpoint": EMBEDDING_ENDPOINT, "model": EMBEDDING_MODEL,
            "api_key": EMBEDDING_API_KEY, "format": "openai_embedding",
        }

    # Vision
    if VISION_API_KEY:
        providers[VISION_PROVIDER] = providers.get(VISION_PROVIDER, {})
        providers[VISION_PROVIDER]["vision"] = {
            "endpoint": VISION_ENDPOINT, "model": VISION_MODEL,
            "api_key": VISION_API_KEY, "format": VISION_FORMAT,
        }

    # ASR Upload
    if ASR_API_KEY:
        providers[ASR_PROVIDER] = providers.get(ASR_PROVIDER, {})
        providers[ASR_PROVIDER]["asr"] = {
            "endpoint": ASR_ENDPOINT, "model": ASR_MODEL_ASR,
            "api_key": ASR_API_KEY, "format": "openai_asr",
        }

    # ASR URL (DashScope)
    if ASR_URL_API_KEY:
        providers[ASR_URL_PROVIDER] = providers.get(ASR_URL_PROVIDER, {})
        providers[ASR_URL_PROVIDER]["asr_url"] = {
            "endpoint": "dashscope://transcription", "model": "paraformer-v2",
            "api_key": ASR_URL_API_KEY, "format": "dashscope_asr_url",
        }

    return providers


PROVIDERS = _load_providers()


def get_provider(service: str) -> tuple:
    """根据服务类型获取可用的 (provider_name, config)"""
    priority = {
        "chat": ["deepseek"],
        "embedding": ["siliconflow"],
        "asr": ["dashscope", "siliconflow"],  # DashScope URL 优先，SiliconFlow fallback
        "vision": ["dashscope"],
    }
    for name in priority.get(service, []):
        cfg = PROVIDERS.get(name, {}).get(service)
        if cfg and cfg.get("api_key"):
            return name, cfg
    raise RuntimeError(f"No provider configured for: {service}")


async def call_chat(messages: list[dict], system_prompt: str = None,
                    temperature: float = 0.7, max_tokens: int = 1024,
                    provider: str = None) -> tuple[str, dict]:
    """调用 LLM 对话，返回 (回答文本, usage信息)"""
    if provider is None:
        provider, cfg = get_provider("chat")
    else:
        cfg = PROVIDERS[provider]["chat"]

    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system_prompt or ""},
            *messages,
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(cfg["endpoint"], json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    answer = data["choices"][0]["message"]["content"]
    return answer, data.get("usage", {})


async def call_embedding(texts: list[str], provider: str = None) -> list[list[float]]:
    """调用 Embedding 模型，返回向量列表"""
    if not texts:
        return []

    if provider is None:
        provider, cfg = get_provider("embedding")
    else:
        cfg = PROVIDERS[provider]["embedding"]

    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }

    all_embeddings = []
    batch_size = 32
    async with httpx.AsyncClient(timeout=60.0) as client:
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            payload = {"model": cfg["model"], "input": batch}
            resp = await client.post(cfg["endpoint"], json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            batch_results = sorted(data["data"], key=lambda x: x["index"])
            all_embeddings.extend([item["embedding"] for item in batch_results])

    return all_embeddings


async def call_vision(image_base64: str, prompt: str = None,
                      provider: str = None) -> tuple[str, dict]:
    """调用视觉模型分析图片，返回 (描述文本, usage信息)"""
    if provider is None:
        provider, cfg = get_provider("vision")
    else:
        cfg = PROVIDERS[provider]["vision"]

    if cfg["format"] == "dashscope_vision":
        headers = {
            "Authorization": f"Bearer {cfg['api_key']}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": cfg["model"],
            "input": {
                "messages": [{
                    "role": "user",
                    "content": [
                        {"image": f"data:image/jpeg;base64,{image_base64}"},
                        {"text": prompt or "请详细描述这张图片/视频帧中的内容。"},
                    ],
                }]
            },
            "parameters": {"max_tokens": 500},
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(cfg["endpoint"], json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        description = data["output"]["choices"][0]["message"]["content"]
        if isinstance(description, list):
            description = " ".join(
                item.get("text", "") if isinstance(item, dict) else str(item)
                for item in description
            )
        return description, data.get("usage", {})

    raise RuntimeError(f"Unsupported vision format: {cfg['format']}")


async def call_asr_upload(audio_path: str, provider: str = None) -> list[dict]:
    """通过上传文件调用 ASR，返回 [{text, start, end}, ...]"""
    import os
    if provider is None:
        _, cfg = get_provider("asr")
        # 如果默认是 dashscope，切到 siliconflow 做 upload
        if PROVIDERS.get("dashscope", {}).get("asr_url", {}).get("api_key"):
            provider = "siliconflow"
        else:
            provider, cfg = get_provider("asr")
    else:
        cfg = PROVIDERS[provider]["asr"]

    headers = {"Authorization": f"Bearer {cfg['api_key']}"}
    ext = os.path.splitext(audio_path)[1].lower()
    mime_map = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4"}
    mime = mime_map.get(ext, "audio/wav")

    async with httpx.AsyncClient(timeout=300.0) as client:
        with open(audio_path, "rb") as f:
            files = {"file": (os.path.basename(audio_path), f, mime)}
            data = {
                "model": cfg["model"],
                "response_format": "verbose_json",
                "timestamp_granularities": ["segment"],
            }
            resp = await client.post(cfg["endpoint"], files=files, data=data, headers=headers)
            resp.raise_for_status()
            result = resp.json()

    subtitles = []
    for seg in result.get("segments", []):
        text = seg.get("text", "").strip()
        if text:
            subtitles.append({
                "text": text,
                "start": round(seg.get("start", 0), 2),
                "end": round(seg.get("end", 0), 2),
            })

    if not subtitles:
        text = result.get("text", "").strip()
        if text:
            subtitles.append({"text": text, "start": 0, "end": round(len(text) / 5, 2)})

    # 全0时间戳兜底
    if subtitles and all(s["start"] == 0 and s["end"] == 0 for s in subtitles):
        import re
        new_subtitles = []
        for s in subtitles:
            for sent in re.split(r'(?<=[。！？；\n\.\!\?;])', s["text"]):
                sent = sent.strip()
                if not sent: continue
                dur = max(1.0, len(sent) / 5)
                start = new_subtitles[-1]["end"] if new_subtitles else 0.0
                new_subtitles.append({"text": sent, "start": round(start, 2), "end": round(start + dur, 2)})
        if new_subtitles:
            subtitles = new_subtitles

    return subtitles


async def call_asr_url(audio_url: str, provider: str = None) -> list[dict]:
    """通过 URL 调用 DashScope ASR，返回 [{text, start, end}, ...]"""
    import dashscope
    from dashscope.audio.asr import Transcription
    from http import HTTPStatus

    if provider is None:
        provider, cfg = get_provider("asr")
    else:
        cfg = PROVIDERS[provider].get("asr_url") or PROVIDERS[provider]["asr"]

    dashscope.api_key = cfg["api_key"]

    task = Transcription.async_call(
        model=cfg["model"],
        file_urls=[audio_url],
        language_hints=["zh", "en"],
    )
    result = Transcription.wait(task=task.output.task_id)
    if result.status_code != HTTPStatus.OK:
        raise RuntimeError(f"ASR failed: {result.code} - {result.message}")

    transcript_url = result.output["results"][0]["transcription_url"]
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(transcript_url)
        resp.raise_for_status()
        transcription = resp.json()

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

    return subtitles
