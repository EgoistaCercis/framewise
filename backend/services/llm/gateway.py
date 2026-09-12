"""
帧知 - 模型网关层

目标：把各厂商的 HTTP / SSE / 协议差异统一收敛成 OpenAI 兼容协议。
调用点只认识本网关，不关心 base_url、流式解析、错误翻译、usage 归一化。

能力：Chat(含流式) / Embedding / Vision / ASR(上传) / ASR(URL 直传, DashScope 专有)

新增厂商：在 backend.config 的 _PROVIDER / ENDPOINT / API_KEY / MODEL 处配置，
网关按服务类型路由 base_url，调用点无需改动。

用法：
    from backend.services.llm.gateway import chat, chat_stream, embed, vision, asr
    text, usage = await chat(messages, system_prompt=..., max_tokens=8192)
    vecs = await embed([text])
    desc, usage = await vision(image_b64, "描述图片")
    subtitles = await asr(audio_path)
"""
import asyncio
import random

import httpx
from loguru import logger
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


def _is_configured(key: str) -> bool:
    """API Key 是否**真正**配置了。

    `your_key_here` 是 .env.example 里的占位符，用户很容易原样拷进 .env ——
    它非空但无效，必须当成「未配置」，否则会拿占位符去请求（必然 401）。

    这个约定原本只写在 main.py 的 UI 提示里，网关的路由判定用的是真值判断，
    于是出现「UI 说本次回落到默认模型、网关却拿占位符去请求」的分裂。
    收在这里，保证判定只有一处。
    """
    return bool(key) and key.strip() != "your_key_here"


def _chat_cfg(smart: bool = False) -> dict:
    """返回 chat 配置；smart=True 时使用独立的高阶模型（厂家可不同）

    SMART_LLM_API_KEY 未配置（含占位符）时**回落默认模型** —— 与 config.py 的
    注释和前端提示保持一致（前端会显示"智能模型未配置，本次仍使用默认模型"）。
    """
    if smart and _is_configured(config.SMART_LLM_API_KEY):
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
_clients: dict[tuple, AsyncOpenAI] = {}
_client_lock = asyncio.Lock()


async def _client(cfg: dict) -> AsyncOpenAI:
    """按 (base_url, api_key) 获取（并缓存）AsyncOpenAI 客户端，复用连接池。

    **key 必须带 api_key**：同一个 base_url 完全可能配了不同的 key ——
    `LLM_*` 与 `SMART_LLM_*` 指向同一厂商（如都用 deepseek），
    chat 与 embedding 共用同一个兼容端点。只按 base_url 缓存的话，
    后配置的一方会静默复用先入缓存的 client，**拿别人的 key 发请求**：
    轻则鉴权失败，重则静默记到另一个账号，而症状看起来完全无关（401 / 配额错乱）。
    """
    key = (cfg["base_url"], cfg["api_key"])
    if key not in _clients:
        async with _client_lock:
            if key not in _clients:
                # 占位符也当未配置：这里拦住能给出「去 .env 配置」的明确提示，
                # 而不是让它发出去换回一个含糊的 401
                if not _is_configured(cfg["api_key"]):
                    raise RuntimeError(f"{cfg['provider']} API Key 未配置，请在 .env 中设置")
                _clients[key] = AsyncOpenAI(
                    base_url=cfg["base_url"],
                    api_key=cfg["api_key"],
                    timeout=config.LLM_TIMEOUT,
                )
    return _clients[key]


# ── 统一重试策略 ──────────────────────────────────────────
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
# httpx.TransportError 一并纳入：网关里有裸 httpx 调用（取 ASR 转写结果），
# 那些路径抛的是 httpx 自己的异常，不是 openai SDK 包装过的，
# 不纳进来的话 _with_retry 对它们形同虚设。
RETRYABLE_EXC = (APIConnectionError, APITimeoutError, RateLimitError, httpx.TransportError)
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
            # 同样加抖动：原来只有网络错误分支有抖动，429/5xx 没有 ——
            # 被限流时一批并发请求会按同样的退避曲线同步重试，形成波纹、再次撞限流
            await asyncio.sleep(BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.3))
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


# 流式是否带 stream_options（不带就拿不到 usage，成本记账会缺一大块）。
# 个别 OpenAI 兼容厂商不认这个参数、直接 400（不可重试）。首次遇到就**永久降级**，
# 避免每次请求都白试一遍 —— 这是「换厂商时的隐性断点」，降级时会 warning。
_stream_usage_supported = True


def _stream_opts() -> dict:
    return {"stream_options": {"include_usage": True}} if _stream_usage_supported else {}


def _degrade_stream_usage(e) -> None:
    global _stream_usage_supported
    if _stream_usage_supported:
        _stream_usage_supported = False
        logger.warning(
            f"该厂商不接受 stream_options（{str(e)[:120]}），已永久降级："
            f"后续流式调用将拿不到 usage，成本记账会缺失这部分用量"
        )


def _first_choice(resp, desc: str):
    """取第一个 choice 的 message，为空时给出可读错误。

    个别厂商在内容过滤/安全拦截时会返回 HTTP 200 但 `choices` 为空数组，
    直接取 `[0]` 会变成 IndexError，再被 translate_error 包成一句含糊的
    RuntimeError —— 排查时完全看不出是「被内容过滤」。
    """
    choices = getattr(resp, "choices", None)
    if not choices:
        raise RuntimeError(
            f"{desc} 返回了空 choices —— 通常是被厂商的内容过滤/安全策略拦截，"
            f"也可能是该厂商的协议不完全兼容"
        )
    return choices[0].message


# ── 用量记账（网关层统一收口）─────────────────────────────
# 记账放在网关，而不是散在各个调用方。网关是所有模型调用的唯一入口，
# 放这里才有「新增调用路径自动入账」的效果。
#
# 之前的做法是让调用方自己调 log_usage，结果：只有 Agent 路径、视觉、嵌入、ASR
# 各自记了，而**走 gateway.chat 直连的调用（V1/V2/V3 的作答、以及所有评测的裁判）
# 从来没进过账**——成本分析里它们是完全隐形的。
def _record(cfg: dict, call_type: str, *, input_tokens: int = 0,
            output_tokens: int = 0, cached_tokens: int = 0,
            reasoning_tokens: int = 0, video_id: str = None) -> None:
    """把一次调用的用量写进 usage.db。

    记账失败绝不能让模型调用跟着失败，所以整体包 try —— 用量统计是附属功能，
    不能因为写库出错就打断用户正在进行的问答。
    """
    try:
        from backend.services.llm.cost_service import log_usage
        log_usage(
            model=cfg["model"], provider=cfg["provider"], call_type=call_type,
            input_tokens=input_tokens, output_tokens=output_tokens,
            cached_tokens=cached_tokens, reasoning_tokens=reasoning_tokens,
            video_id=video_id,
        )
    except Exception as e:
        logger.warning(f"[Cost] 用量记账失败（不影响本次调用）: {e}")


def _record_usage(cfg: dict, call_type: str, usage: dict, video_id: str = None) -> None:
    """把 API 返回的 usage 归一化后记账"""
    if not usage:
        return
    _record(
        cfg, call_type,
        input_tokens=usage.get("prompt_tokens", 0) or 0,
        output_tokens=usage.get("completion_tokens", 0) or 0,
        cached_tokens=usage.get("cached_tokens", 0) or 0,
        reasoning_tokens=usage.get("reasoning_tokens", 0) or 0,
        video_id=video_id,
    )


def _record_text_estimate(cfg: dict, call_type: str, text_chars: int,
                          video_id: str = None) -> None:
    """embedding / ASR 不返回 usage，按字符数估算 token（约 2 字符 = 1 token）。

    估算规则本来就该跟着调用走——放在网关就不必每个调用方各写一遍。
    """
    _record(cfg, call_type, input_tokens=text_chars // 2, video_id=video_id)


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
               smart: bool = False, video_id: str = None) -> tuple[str, dict]:
    """调用 LLM 对话，返回 (回答文本, usage信息)

    smart=True 时使用独立的高阶模型（config.SMART_LLM_*，厂家可不同）。
    video_id 仅用于把用量归到某个视频（可选；不传则只记总量）。
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
        msg = _first_choice(resp, "chat")
        answer = msg.content or ""
        usage = _usage_to_dict(resp.usage)
        _record_usage(cfg, "chat", usage, video_id)
        return answer, usage
    except Exception as e:
        raise RuntimeError(translate_error(e, cfg["provider"])) from e


async def chat_with_tools(messages: list[dict], system_prompt: str = None,
                          tools: list[dict] = None, temperature: float = 0.7,
                          max_tokens: int = None, smart: bool = False,
                          video_id: str = None) -> tuple[dict, dict]:
    """支持 function calling 的 LLM 对话。

    参数:
        tools: OpenAI function calling 格式的 tool 定义列表
    返回:
        (message, usage)
        message = {"content": str, "tool_calls": [{"id","name","arguments"}]}
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
                tools=tools or None,
            ),
            desc="chat_with_tools",
        )
        msg = _first_choice(resp, "chat_with_tools")
        message = {
            "content": msg.content or "",
            "tool_calls": [
                {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments}
                for tc in (msg.tool_calls or [])
            ],
        }
        usage = _usage_to_dict(resp.usage)
        _record_usage(cfg, "chat_tools", usage, video_id)
        return message, usage
    except Exception as e:
        raise RuntimeError(translate_error(e, cfg["provider"])) from e


async def chat_stream(messages: list[dict], system_prompt: str = None,
                      temperature: float = 0.7, max_tokens: int = None,
                      smart: bool = False, video_id: str = None):
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

    def _mk(extra: dict):
        return lambda: client.chat.completions.create(
            model=cfg["model"], messages=msgs, temperature=temperature,
            max_tokens=max_tokens, stream=True, **extra,
        )

    try:
        try:
            # 不带 stream_options 的话，OpenAI 兼容流式默认不返回 usage，
            # 所有流式调用（产品的默认交互路径）用量会全丢
            stream = await _with_retry(_mk(_stream_opts()), desc="chat_stream")
        except BadRequestError as e:
            _degrade_stream_usage(e)
            stream = await _with_retry(_mk({}), desc="chat_stream")
        stream_usage = {}
        async for chunk in stream:
            # 末块 choices 为空、只带 usage —— 必须在 continue 之前取
            if getattr(chunk, "usage", None):
                stream_usage = _usage_to_dict(chunk.usage)
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content
        _record_usage(cfg, "chat", stream_usage, video_id)
    except Exception as e:
        raise RuntimeError(translate_error(e, cfg["provider"])) from e


async def chat_with_tools_stream(messages: list[dict], system_prompt: str = None,
                                 tools: list[dict] = None, temperature: float = 0.7,
                                 max_tokens: int = None, smart: bool = False,
                                 video_id: str = None):
    """流式 + tool calls 的 LLM 对话，yield 事件 dict。

    事件：
        {"type": "content", "delta": str}  文本增量
        {"type": "done", "content": str, "tool_calls": [...]}  流结束汇总
    """
    if max_tokens is None:
        max_tokens = config.LLM_MAX_TOKENS
    cfg = _chat_cfg(smart)
    client = await _client(cfg)

    msgs = []
    if system_prompt:
        msgs.append({"role": "system", "content": system_prompt})
    msgs.extend(messages)

    def _mk(extra: dict):
        return lambda: client.chat.completions.create(
            model=cfg["model"], messages=msgs, temperature=temperature,
            max_tokens=max_tokens, tools=tools or None, stream=True, **extra,
        )

    try:
        try:
            stream = await _with_retry(_mk(_stream_opts()), desc="chat_with_tools_stream")
        except BadRequestError as e:
            _degrade_stream_usage(e)
            stream = await _with_retry(_mk({}), desc="chat_with_tools_stream")
        content_parts = []
        tool_calls = {}  # index -> {"id", "name", "arguments"}
        stream_usage = {}
        async for chunk in stream:
            if getattr(chunk, "usage", None):
                stream_usage = _usage_to_dict(chunk.usage)
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is None:
                continue
            if delta.content:
                content_parts.append(delta.content)
                yield {"type": "content", "delta": delta.content}
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    entry = tool_calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                    if getattr(tc, "id", None):
                        entry["id"] = tc.id
                    if tc.function and tc.function.name:
                        entry["name"] = tc.function.name
                    if tc.function and tc.function.arguments:
                        entry["arguments"] += tc.function.arguments

        tc_list = [
            {"id": e["id"], "name": e["name"], "arguments": e["arguments"]}
            for _, e in sorted(tool_calls.items())
        ]
        _record_usage(cfg, "chat", stream_usage, video_id)
        yield {"type": "done", "content": "".join(content_parts), "tool_calls": tc_list, "usage": stream_usage}
    except Exception as e:
        raise RuntimeError(translate_error(e, cfg["provider"])) from e


# ═══════════════════════════════════════════════════════
# Embedding
# ═══════════════════════════════════════════════════════
async def embed(texts: list[str], video_id: str = None) -> list[list[float]]:
    """调用 Embedding 模型，返回向量列表（自动分批）"""
    if not texts:
        return []
    cfg = _service_cfg("embedding")
    client = await _client(cfg)

    all_embeddings = []
    real_tokens = 0        # 厂商若返回 usage 则用它，最后按真值记账
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
            u = _usage_to_dict(getattr(resp, "usage", None))
            if u.get("prompt_tokens"):
                real_tokens += u["prompt_tokens"]
        # 优先用接口返回的真值；只有厂商不返回 usage 时才按字符数估算
        if real_tokens:
            _record(cfg, "embedding", input_tokens=real_tokens, video_id=video_id)
        else:
            _record_text_estimate(cfg, "embedding", sum(len(t) for t in texts), video_id)
        return all_embeddings
    except Exception as e:
        raise RuntimeError(translate_error(e, cfg["provider"])) from e


# ═══════════════════════════════════════════════════════
# Vision（OpenAI 兼容多模态）
# ═══════════════════════════════════════════════════════
async def vision(image_base64: str, prompt: str = None,
                 video_id: str = None) -> tuple[str, dict]:
    """调用视觉模型分析图片，返回 (描述文本, usage信息)"""
    cfg = _service_cfg("vision")
    client = await _client(cfg)

    from backend.prompts import VISION_PROMPT

    content = [
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
        {"type": "text", "text": prompt or VISION_PROMPT},
    ]
    try:
        resp = await _with_retry(
            lambda: client.chat.completions.create(
                model=cfg["model"],
                messages=[{"role": "user", "content": content}],
                # 提示词要求 200 字，实测仍会写到 ~250 token 撞上限；400 留出余量，
                # 且因为 VISION_PROMPT 把关键信息排在前面，偶发截断也只丢尾部
                max_tokens=400,
            ),
            desc="vision",
        )
        answer = _first_choice(resp, "vision").content or ""
        usage = _vision_usage(resp.usage)
        _record_usage(cfg, "vision", usage, video_id)
        return answer, usage
    except Exception as e:
        raise RuntimeError(translate_error(e, cfg["provider"])) from e


# ═══════════════════════════════════════════════════════
# ASR（文件上传，OpenAI 兼容 /audio/transcriptions）
# ═══════════════════════════════════════════════════════
async def asr(audio_path: str, video_id: str = None) -> list[dict]:
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

    subtitles = _parse_subtitles(resp)
    # ASR 接口不返回 usage。注意：转写文本是**输出**不是输入，
    # 且 ASR 通常按音频时长计费、与 token 无对应关系 —— 这里只是给用量一个量级参考，
    # 别拿它做严谨的成本核算（成本请以 usage.db 里的总价为准）。
    _record(cfg, "asr", output_tokens=sum(len(s.get("text", "")) for s in subtitles) // 2,
            video_id=video_id)
    return subtitles


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
async def asr_url(audio_url: str, video_id: str = None) -> list[dict]:
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
    task_id = task.output.task_id

    # Transcription.wait 是 DashScope SDK 的**同步阻塞轮询**，长视频要等好几分钟。
    # 直接在事件循环里调用会把整个后端冻住——期间所有在线用户的对话全部卡死。
    # （与评测侧给 ffmpeg 抽帧加 to_thread 是同一类问题，只是这个藏在 SDK 里）
    #
    # 只重试「等待」、不重试「提交」：重新 async_call 会产生第二个转写任务、
    # 重复计费；拿同一个 task_id 重新轮询是安全的。
    result = await _with_retry(
        lambda: asyncio.to_thread(Transcription.wait, task=task_id),
        desc="asr_url_wait",
    )
    if result.status_code != HTTPStatus.OK:
        raise RuntimeError(f"ASR 任务失败（{getattr(result, 'code', '?')}）："
                           f"{getattr(result, 'message', '')}")

    # 任务级成功 ≠ 文件级成功：每个文件有独立的 subtask_status。
    # 原来直接 result.output["results"][0]["transcription_url"]，
    # 文件级失败时会抛 KeyError/IndexError —— 把「转写失败」伪装成「代码有 bug」。
    items = (getattr(result, "output", None) or {}).get("results") or []
    if not items:
        raise RuntimeError(f"ASR 未返回任何结果：{getattr(result, 'message', '')}")
    first = items[0]
    if first.get("subtask_status") != "SUCCEEDED":
        raise RuntimeError(f"ASR 文件级转写失败（{first.get('subtask_status')}）："
                           f"{first.get('message', '')}")
    if not first.get("transcription_url"):
        raise RuntimeError(f"ASR 转写成功但未返回结果地址：{first}")
    transcript_url = first["transcription_url"]

    # 取转写结果只是一次普通 GET，重试安全（不会重复触发转写）
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await _with_retry(lambda: client.get(transcript_url), desc="asr_transcript")
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
    # 本路径用 DashScope 专有 SDK、模型名为硬编码，故记账也用它自己的名字
    # （不取 _service_cfg("asr")，那是文件上传路径的 ASR_MODEL，两者可能不同）。
    # 转写文本记进 output_tokens：它是输出；且 ASR 按音频时长计费，token 只是量级参考。
    _record(
        {"model": "paraformer-v2", "provider": "dashscope"}, "asr",
        output_tokens=sum(len(s["text"]) for s in subtitles) // 2, video_id=video_id,
    )
    return subtitles
