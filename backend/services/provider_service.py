"""
帧知 - 厂商标配查询层（provider 路由）

所有模型调用（Chat / Embedding / Vision / ASR）已统一迁移到
backend.services.gateway（OpenAI 网关层）。

本模块仅保留 get_provider()，供调用方查询某服务类型的
(provider_name, cfg)，用于记录 token 用量、日志等。
"""


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
        "asr": ["dashscope", "siliconflow"],
        "vision": ["dashscope"],
    }
    for name in priority.get(service, []):
        cfg = PROVIDERS.get(name, {}).get(service)
        if cfg and cfg.get("api_key"):
            return name, cfg
    names = {"chat": "LLM", "embedding": "Embedding", "asr": "ASR", "vision": "Vision"}
    raise RuntimeError(f"{names.get(service, service)} API Key 未配置，请在 .env 中设置")


# Embedding / Vision / ASR 调用已迁移到 backend.services.gateway（OpenAI 网关层）
