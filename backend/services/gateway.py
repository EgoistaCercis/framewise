"""
帧知 - 模型网关层

目标：把各厂商的 HTTP / SSE / 协议差异统一收敛成 OpenAI 兼容协议。
调用点只认识本网关，不关心 base_url、流式解析、错误翻译、usage 归一化。

能力：Chat(含流式) / Embedding / Vision / ASR(上传) / ASR(URL 直传, DashScope 专有)

新增厂商：在 backend.config 的 _PROVIDER / ENDPOINT / API_KEY / MODEL 处配置，
网关按服务类型路由 base_url，调用点无需改动。

用法：
    from backend.services.gateway import chat, chat_stream, embed, vision, asr
    text, usage = await chat(messages, system_prompt=..., max_tokens=8192)
    vecs = await embed([text])
    desc, usage = await vision(image_b64, "描述图片")
    subtitles = await asr(audio_path)
"""
import asyncio
import random

from openai import (
    AsyncOpenAI,
    APIError,
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    RateLimitError,
)

from backend import config


# ── 厂商标配表（从 .env 推导）─────────────────────────────
# endpoint 形如 https://api.deepseek.com/v1/chat/completions
# base_url 取到 /v1，SDK 会自动拼接 /chat/completions
def _endpoint_to_base(endpoint: str, suffix: str) -> str:
    return endpoint.rstrip("/").rsplit(suffix, 1)[0]


def _service_cfg(service: str) -> dict:
    """返回某服务类型的 {provider, base_url, api_key, model}"""
    if service == "chat":
        return {
            "provider": config.LLM_PROVIDER,
            "base_url": _endpoint_to_base(config.LLM_ENDPOINT, "/chat/completions"),
            "api_key": config.LLM_API_KEY,
            "model": config.LLM_MODEL,
        }
    if service == "embedding":
        return {
            "provider": config.EMBEDDING_PROVIDER,
            "base_url": _endpoint_to_base(config.EMBEDDING_ENDPOINT, "/embeddings"),
            "api_key": config.EMBEDDING_API_KEY,
            "model": config.EMBEDDING_MODEL,
        }
    if service == "vision":
        return {
            "provider": config.VISION_PROVIDER,
            "base_url": config.VISION_BASE_URL,
            "api_key": config.VISION_API_KEY,
            "model": config.VISION_MODEL,
        }
    if service == "asr":
        return {
            "provider": config.ASR_PROVIDER,
            "base_url": _endpoint_to_base(config.ASR_ENDPOINT, "/audio/transcriptions"),
            "api_key": config.ASR_API_KEY,
            "model": config.ASR_MODEL_ASR,
        }
    raise ValueError(f"未支持的服务类型: {service}")


def _chat_cfg(smart: bool = False) -> dict:
    """返回 chat 配置；smart=True 时使用独立的高阶模型（厂家可不同）"""
    if smart and config.SMART_LLM_API_KEY:
        return {
            "provider": config.SMART_LLM_PROVIDER,
            "base_url": _endpoint_to_base(config.SMART_LLM_ENDPOINT, "/chat/completions"),
            "api_key": config.SMART_LLM_API_KEY,
            "model": config.SMART_LLM_MODEL,
        }
    return {
        "provider": config.LLM_PROVIDER,
        "base_url": _endpoint_to_base(config.LLM_ENDPOINT, "/chat/completions"),
        "api_key": config.LLM_API_KEY,
        "model": config.LLM_MODEL,
    }


# ── 客户端缓存（按 base_url 复用连接池）───────────────────
_clients: dict[str, AsyncOpenAI] = {}
_client_lock = asyncio.Lock()


async def _client(cfg: dict) -> AsyncOpenAI:
    """按 base_url 获取（并缓存）AsyncOpenAI 客户端，复用连接池"""
    base = cfg["base_url"]
    if base not in _clients:
        async with _client_lock:
            if base not in _clients:
                if not cfg["api_key"]:
                    raise RuntimeError(f"{cfg['provider']} API Key 未配置，请在 .env 中设置")
                _clients[base] = AsyncOpenAI(
                    base_url=base,
                    api_key=cfg["api_key"],
                    timeout=120.0,
                )
    return _clients[base]


# ── 统一重试策略 ──────────────────────────────────────────
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
RETRYABLE_EXC = (APIConnectionError, APITimeoutError, RateLimitError)
MAX_RETRIES = 3
BASE_DELAY = 0.6


async def _with_retry(fn, *, retries: int = MAX_RETRIES, desc: str = ""):
    """对可重试错误做指数退避重试：
    - 重试：网络错误(APITimeoutError/APIConnectionError)、429、5xx(500/502/503/504)
    - 不重试：400/401/404 等客户端错误（重试无意义）
    重试耗尽后抛出最后一个异常。
    """
    last = None
    for attempt in range(retries + 1):
        try:
            return await fn()
        except RETRYABLE_EXC as e:
            last = e
            if attempt >= retries:
                break
            await asyncio.sleep(BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.3))
        except APIError as e:
            if e.status_code not in RETRYABLE_STATUS or attempt >= retries:
                raise
            last = e
            await asyncio.sleep(BASE_DELAY * (2 ** attempt))
    raise last


# ── usage 归一化：SDK 对象 → 兼容 dict ─────────────────────
def _usage_to_dict(usage) -> dict:
    """提取 OpenAI usage 为兼容 dict，含 cached / reasoning 细粒度字段"""
    if usage is None:
        return {}

    prompt_details = getattr(usage, "prompt_tokens_details", None)
    completion_details = getattr(usage, "completion_tokens_details", None)
    cached_tokens = getattr(prompt_details, "cached_tokens", 0) or 0
    reasoning_tokens = getattr(completion_details, "reasoning_tokens", 0) or 0

    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", 0),
        "completion_tokens": getattr(usage, "completion_tokens", 0),
        "total_tokens": getattr(usage, "total_tokens", 0),
        "cached_tokens": cached_tokens,
        "reasoning_tokens": reasoning_tokens,
    }


def _vision_usage(usage) -> dict:
    """vision 同时提供 OpenAI 键与原 DashScope input/output 键，兼容调用点"""
    d = _usage_to_dict(usage)
    d["input_tokens"] = d["prompt_tokens"]
    d["output_tokens"] = d["completion_tokens"]
    return d


# ── 错误翻译：openai 异常 → 中文提示 ───────────────────────
def translate_error(e: Exception, provider: str = "") -> str:
    """将 openai SDK 异常翻译为用户友好的中文提示"""
    if isinstance(e, AuthenticationError):
        return f"{provider} API Key 无效，请检查 .env 配置"
    if isinstance(e, RateLimitError):
        return f"{provider} 请求过于频繁（429），请稍后重试"
    if isinstance(e, APITimeoutError):
        return f"{provider} 请求超时，请检查网络"
    if isinstance(e, APIConnectionError):
        return f"无法连接 {provider}，请检查网络"
    if isinstance(e, BadRequestError):
        return f"{provider} 请求参数错误：{str(e)[:200]}"
    if isinstance(e, NotFoundError):
        return f"{provider} 接口不存在（404），请检查 base_url 配置"
    if isinstance(e, APIError):
        return f"{provider} API 错误（{e.status_code}）：{str(e)[:200]}"
    return f"{provider} 调用失败：{str(e)[:100]}"


# ═══════════════════════════════════════════════════════
# Chat
# ═══════════════════════════════════════════════════════
async def chat(messages: list[dict], system_prompt: str = None,
               temperature: float = 0.7, max_tokens: int = None,
               smart: bool = False) -> tuple[str, dict]:
    """调用 LLM 对话，返回 (回答文本, usage信息)

    smart=True 时使用独立的高阶模型（config.SMART_LLM_*，厂家可不同）。
    """
    if max_tokens is None:
        max_tokens = config.LLM_MAX_TOKENS
    cfg = _chat_cfg(smart)
    client = await _client(cfg)

    msgs = []
    if system_prompt:
        msgs.append({"role": "system", "content": system_prompt})
    msgs.extend(messages)

    try:
        resp = await _with_retry(
            lambda: client.chat.completions.create(
                model=cfg["model"],
                messages=msgs,
                temperature=temperature,
                max_tokens=max_tokens,
            ),
            desc="chat",
        )
        answer = resp.choices[0].message.content or ""
        return answer, _usage_to_dict(resp.usage)
    except Exception as e:
        raise RuntimeError(translate_error(e, cfg["provider"])) from e


async def chat_stream(messages: list[dict], system_prompt: str = None,
                      temperature: float = 0.7, max_tokens: int = None,
                      smart: bool = False):
    """流式 LLM 对话，异步 yield 每个 token 文本

    smart=True 时使用独立的高阶模型。
    重试仅在建立连接阶段进行；一旦开始产出 token 就不再重发，避免重复。
    """
    if max_tokens is None:
        max_tokens = config.LLM_MAX_TOKENS
    cfg = _chat_cfg(smart)
    client = await _client(cfg)

    msgs = []
    if system_prompt:
        msgs.append({"role": "system", "content": system_prompt})
    msgs.extend(messages)

    try:
        stream = await _with_retry(
            lambda: client.chat.completions.create(
                model=cfg["model"],
                messages=msgs,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            ),
            desc="chat_stream",
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content
    except Exception as e:
        raise RuntimeError(translate_error(e, cfg["provider"])) from e


# ═══════════════════════════════════════════════════════
# Embedding
# ═══════════════════════════════════════════════════════
async def embed(texts: list[str], provider: str = None) -> list[list[float]]:
    """调用 Embedding 模型，返回向量列表（自动分批）"""
    if not texts:
        return []
    cfg = _service_cfg("embedding")
    client = await _client(cfg)

    all_embeddings = []
    batch_size = 32
    try:
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            resp = await _with_retry(
                lambda: client.embeddings.create(model=cfg["model"], input=batch),
                desc="embedding",
            )
            batch_results = sorted(resp.data, key=lambda x: x.index)
            all_embeddings.extend([item.embedding for item in batch_results])
        return all_embeddings
    except Exception as e:
        raise RuntimeError(translate_error(e, cfg["provider"])) from e


# ═══════════════════════════════════════════════════════
# Vision（OpenAI 兼容多模态）
# ═══════════════════════════════════════════════════════
async def vision(image_base64: str, prompt: str = None,
                 provider: str = None) -> tuple[str, dict]:
    """调用视觉模型分析图片，返回 (描述文本, usage信息)"""
    cfg = _service_cfg("vision")
    client = await _client(cfg)

    content = [
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
        {"type": "text", "text": prompt or "请详细描述这张图片/视频帧中的内容。"},
    ]
    try:
        resp = await _with_retry(
            lambda: client.chat.completions.create(
                model=cfg["model"],
                messages=[{"role": "user", "content": content}],
                max_tokens=500,
            ),
            desc="vision",
        )
        answer = resp.choices[0].message.content or ""
        return answer, _vision_usage(resp.usage)
    except Exception as e:
        raise RuntimeError(translate_error(e, cfg["provider"])) from e


# ═══════════════════════════════════════════════════════
# ASR（文件上传，OpenAI 兼容 /audio/transcriptions）
# ═══════════════════════════════════════════════════════
async def asr(audio_path: str, provider: str = None) -> list[dict]:
    """上传本地音频做语音转写，返回 [{text, start, end}, ...]"""
    import os

    cfg = _service_cfg("asr")
    client = await _client(cfg)

    mime_map = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4"}
    filename = os.path.basename(audio_path)
    mime = mime_map.get(os.path.splitext(filename)[1].lower(), "audio/wav")

    with open(audio_path, "rb") as f:
        files = (filename, f, mime)
        try:
            resp = await _with_retry(
                lambda: client.audio.transcriptions.create(
                    model=cfg["model"],
                    file=files,
                    response_format="verbose_json",
                    timestamp_granularities=["segment"],
                ),
                desc="asr",
            )
        except Exception as e:
            raise RuntimeError(translate_error(e, cfg["provider"])) from e

    return _parse_subtitles(resp)


def _parse_subtitles(result) -> list[dict]:
    """把 ASR 结果解析为 [{text, start, end}, ...]，含长句按标点拆分"""
    import re
    segments = getattr(result, "segments", None)
    if segments is None:
        segments = []

    subtitles = []
    for seg in segments:
        text = (getattr(seg, "text", "") or "").strip()
        if text:
            subtitles.append({
                "text": text,
                "start": round(getattr(seg, "start", 0) or 0, 2),
                "end": round(getattr(seg, "end", 0) or 0, 2),
            })

    if not subtitles:
        text = (getattr(result, "text", "") or "").strip()
        if text:
            subtitles.append({"text": text, "start": 0, "end": round(len(text) / 5, 2)})

    # 全0时间戳 或 只有1段且时长>60秒 → 按句子拆分
    need_split = all(s["start"] == 0 and s["end"] == 0 for s in subtitles)
    if not need_split and len(subtitles) <= 2:
        for s in subtitles:
            if s["end"] - s["start"] > 60 or s["end"] == 0:
                need_split = True
                break
    if need_split:
        new_subtitles = []
        for s in subtitles:
            for sent in re.split(r'(?<=[。！？；\n\.\!\?;])', s["text"]):
                sent = sent.strip()
                if not sent:
                    continue
                dur = max(1.0, len(sent) / 5)
                start = new_subtitles[-1]["end"] if new_subtitles else 0.0
                new_subtitles.append({"text": sent, "start": round(start, 2), "end": round(start + dur, 2)})
        if new_subtitles:
            subtitles = new_subtitles

    return subtitles


# ═══════════════════════════════════════════════════════
# ASR（URL 直传，DashScope 专有 SDK —— 无 OpenAI 对应物）
# ═══════════════════════════════════════════════════════
async def asr_url(audio_url: str, provider: str = None) -> list[dict]:
    """通过 URL 调用 DashScope Paraformer（异步任务），返回 [{text, start, end}, ...]"""
    import dashscope
    from dashscope.audio.asr import Transcription
    from http import HTTPStatus

    api_key = config.ASR_URL_API_KEY
    if not api_key:
        raise RuntimeError("ASR URL Api Key 未配置（ASR_URL_API_KEY），请在 .env 中设置")
    dashscope.api_key = api_key

    task = Transcription.async_call(
        model="paraformer-v2",
        file_urls=[audio_url],
        language_hints=["zh", "en"],
    )
    result = Transcription.wait(task=task.output.task_id)
    if result.status_code != HTTPStatus.OK:
        raise RuntimeError(f"ASR failed: {result.code} - {result.message}")

    transcript_url = result.output["results"][0]["transcription_url"]
    import httpx
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
