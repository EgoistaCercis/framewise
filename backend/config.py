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
