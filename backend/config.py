"""
帧知 - 配置管理
"""
import os
from dotenv import load_dotenv

load_dotenv()

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

# DeepSeek API
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# 硅基流动 SiliconFlow (Embedding)
SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY", "")
SILICONFLOW_BASE_URL = os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
SILICONFLOW_EMBEDDING_MODEL = os.getenv("SILICONFLOW_EMBEDDING_MODEL", "BAAI/bge-m3")

# 阿里云 DashScope (Vision)
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_VL_MODEL = os.getenv("DASHSCOPE_VL_MODEL", "qwen-vl-plus")

# 服务配置
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

# ASR 配置
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "base")  # tiny/base/small/medium/large
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "auto")  # auto/cpu/cuda
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "auto")  # auto/float16/int8

# Chunk 配置
CHUNK_MAX_LENGTH = int(os.getenv("CHUNK_MAX_LENGTH", "300"))  # 每chunk最大字符数
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "50"))  # chunk重叠字符数

# ffmpeg 路径
FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")

# RAG 配置
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "5"))  # 检索返回的chunk数量

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
