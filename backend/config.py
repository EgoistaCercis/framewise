"""
帧知 - 配置管理
"""
import os
from dotenv import load_dotenv

load_dotenv(override=True)

# Hugging Face 镜像（国内加速，需在 import faster_whisper 前设置）
if os.getenv("HF_ENDPOINT"):
    os.environ["HF_ENDPOINT"] = os.getenv("HF_ENDPOINT")
os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"  # 禁用Xet，用纯HTTP下载

# 数据目录
DATA_DIR = os.getenv("DATA_DIR", "./data")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
SUBTITLE_DIR = os.path.join(DATA_DIR, "subtitles")
EMBEDDING_DIR = os.path.join(DATA_DIR, "embeddings")
FRAME_DIR = os.path.join(DATA_DIR, "frames")

# ═══════════════════════════════════════════
# API 厂商标配（在 .env 中配置，无需改代码）
# 格式: {服务}_PROVIDER / {服务}_ENDPOINT / {服务}_API_KEY / {服务}_MODEL / {服务}_FORMAT
# 新增厂家只需修改 .env 中对应的 _PROVIDER 和 _ENDPOINT
# ═══════════════════════════════════════════

# Chat / LLM
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "deepseek")
LLM_ENDPOINT = os.getenv("LLM_ENDPOINT", "https://api.deepseek.com/v1/chat/completions")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-v4-pro")

# Embedding
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "siliconflow")
EMBEDDING_ENDPOINT = os.getenv("EMBEDDING_ENDPOINT", "https://api.siliconflow.cn/v1/embeddings")
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")

# Vision
VISION_PROVIDER = os.getenv("VISION_PROVIDER", "dashscope")
VISION_ENDPOINT = os.getenv("VISION_ENDPOINT", "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation")
VISION_API_KEY = os.getenv("VISION_API_KEY", "")
VISION_MODEL = os.getenv("VISION_MODEL", "qwen-vl-plus")
VISION_FORMAT = os.getenv("VISION_FORMAT", "dashscope_vision")

# ASR
ASR_PROVIDER = os.getenv("ASR_PROVIDER", "siliconflow")
ASR_ENDPOINT = os.getenv("ASR_ENDPOINT", "https://api.siliconflow.cn/v1/audio/transcriptions")
ASR_API_KEY = os.getenv("ASR_API_KEY", "")
ASR_MODEL_ASR = os.getenv("ASR_MODEL", "FunAudioLLM/SenseVoiceSmall")

# ASR URL 直传 (DashScope Paraformer)
ASR_URL_PROVIDER = os.getenv("ASR_URL_PROVIDER", "dashscope")
ASR_URL_API_KEY = os.getenv("ASR_URL_API_KEY", "")

# 向后兼容旧变量名
DEEPSEEK_API_KEY = LLM_API_KEY
DEEPSEEK_BASE_URL = os.path.dirname(LLM_ENDPOINT.rstrip("/v1/chat/completions"))
DEEPSEEK_MODEL = LLM_MODEL
SILICONFLOW_API_KEY = EMBEDDING_API_KEY
SILICONFLOW_BASE_URL = os.path.dirname(EMBEDDING_ENDPOINT.rstrip("/embeddings"))
SILICONFLOW_EMBEDDING_MODEL = EMBEDDING_MODEL
DASHSCOPE_API_KEY = VISION_API_KEY or ASR_URL_API_KEY
DASHSCOPE_VL_MODEL = VISION_MODEL

# 服务配置
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

# ASR 模式: "local" = faster-whisper, "api" = 硅基流动 SenseVoice
ASR_MODE = os.getenv("ASR_MODE", "api")

# 本地 ASR 配置 (ASR_MODE=local)
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "base")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "auto")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "auto")

# Chunk 配置
CHUNK_MAX_LENGTH = int(os.getenv("CHUNK_MAX_LENGTH", "300"))  # 每chunk最大字符数
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "50"))  # chunk重叠字符数

# ffmpeg 路径（Windows 需完整路径，Linux/Docker 用 "ffmpeg"）
_ffmpeg_env = os.getenv("FFMPEG_PATH", "ffmpeg")
FFMPEG_PATH = _ffmpeg_env if os.path.exists(_ffmpeg_env) or _ffmpeg_env == "ffmpeg" else "ffmpeg"

# RAG 配置
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "5"))  # 检索返回的chunk数量

# 上下文压缩配置（四层策略）
CONTEXT_MAX_MESSAGES = int(os.getenv("CONTEXT_MAX_MESSAGES", "50"))       # 第1层：最大消息数
CONTEXT_MAX_TOKENS = int(os.getenv("CONTEXT_MAX_TOKENS", "80000"))       # 80% 窗口
TOOL_TRIM_LENGTH = int(os.getenv("TOOL_TRIM_LENGTH", "500"))             # 第2层：工具输出裁剪
SUMMARY_TARGET_LENGTH = int(os.getenv("SUMMARY_TARGET_LENGTH", "200"))    # 第3层：摘要目标

# ═══════════════════════════════════════════
# Loguru 日志配置
# ═══════════════════════════════════════════

import sys
from loguru import logger as _loguru_logger

LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<magenta>req-{extra[request_id]}</magenta> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)

LOG_FORMAT_FILE = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
    "{level: <8} | "
    "req-{extra[request_id]} | "
    "{name}:{function}:{line} - "
    "{message}"
)

LOG_DIR = os.path.join(DATA_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

_loguru_logger.remove()

# 控制台（彩色）
_loguru_logger.add(
    sys.stderr,
    format=LOG_FORMAT,
    level="INFO",
    colorize=True,
)

# 全量日志文件（午夜轮转，保留 14 天）
_loguru_logger.add(
    os.path.join(LOG_DIR, "framewise.log"),
    format=LOG_FORMAT_FILE,
    level="DEBUG",
    rotation="00:00",
    retention="14 days",
    encoding="utf-8",
    enqueue=True,
)

# 错误日志单独文件
_loguru_logger.add(
    os.path.join(LOG_DIR, "error.log"),
    format=LOG_FORMAT_FILE,
    level="ERROR",
    rotation="00:00",
    retention="30 days",
    encoding="utf-8",
    enqueue=True,
)

# 设置默认 extra，防止 KeyError
_loguru_logger.configure(extra={"request_id": ""})

# 拦截标准 logging 模块，uvicorn/FastAPI 的日志也走 loguru
import logging as _logging

class _InterceptHandler(_logging.Handler):
    def emit(self, record):
        level = _loguru_logger.level(record.levelname).name if _loguru_logger.level(record.levelname) else record.levelno
        frame = _logging.currentframe()
        depth = 2
        while frame and frame.f_code.co_filename == _logging.__file__:
            frame = frame.f_back
            depth += 1
        _loguru_logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )

_logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)

# 将 uvicorn 的日志级别设为 INFO，避免 DEBUG 噪音
for _name in ["uvicorn", "uvicorn.access", "uvicorn.error"]:
    _logging.getLogger(_name).handlers = [_InterceptHandler()]
    _logging.getLogger(_name).propagate = False
