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
NOTE_DIR = os.getenv("NOTE_DIR", os.path.join(DATA_DIR, "notes"))

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
# 推理模型需要更大 max_tokens（reasoning + 回答）
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "8192"))

# ── 上下文预算 ────────────────────────────────────────
# 模型窗口。**这是外部事实，必须记录依据** ——
# 原来的 CONTEXT_MAX_TOKENS 注释写「80% 窗口」却没说窗口是多大，无从核对。
# 依据：DeepSeek 官方 API 文档，deepseek-chat 系列上下文为 128K
#      （https://api-docs.deepseek.com/ ，2026-09 核对）。
# 换模型时必须同步改这个值 —— 它现在也是各处预算的推导依据。
MODEL_CONTEXT_WINDOW_TOKENS = int(os.getenv("MODEL_CONTEXT_WINDOW_TOKENS", "128000"))

# 单次请求**输入**的策略上限。
# 取窗口的一半，而不是贴着窗口跑：要给输出(LLM_MAX_TOKENS)和上下文突发留余量，
# 也让"临时换一个 64K 窗口的模型"仍然安全。
# ★ 各处预算（字幕/历史/记忆/工具预留）之和不得越过它，文件末尾有启动自检。
INPUT_BUDGET_TOKENS = int(os.getenv("INPUT_BUDGET_TOKENS", "64000"))

# 单次模型调用的整体超时（秒）。
# 注意这是个**整体**超时，不是只连不上的超时：推理模型生成上万 token 很容易吃满。
# 配合网关的重试（超时可重试），最坏情况是一道题重头生成 3 次。
# 评测里的 judge 用 max_tokens=16000，本地实测偶尔会逼近这个上限，故做成可配置。
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "120"))

# 单个工具的单次执行上限（秒）。
# 工具内部可能调模型（analyze_frame 的 VL、generate_quiz）或拉流下载，
# 都可能长时间挂起；Agent 循环本身没有别的兜底，一个工具卡死就占住整轮对话。
AGENT_TOOL_TIMEOUT = float(os.getenv("AGENT_TOOL_TIMEOUT", "180"))

# LLM-as-judge 专用模型（仅评测使用）。
# 独立配置的意义：裁判与被评模型**同源会有自评偏差**（自己评自己偏松），
# 换一个厂家的模型来评，结论才更有说服力。不配则回落到默认 chat 模型
# （此时必须在报告里注明"裁判与被评模型同源"）。
JUDGE_PROVIDER = os.getenv("JUDGE_PROVIDER", "")
JUDGE_ENDPOINT = os.getenv("JUDGE_ENDPOINT", "")
JUDGE_API_KEY = os.getenv("JUDGE_API_KEY", "")
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "")
# 裁判专用超时（秒）。**必须比 LLM_TIMEOUT 大** —— 裁判一次判定是
# "始终思考" 模型 + max_tokens=12000，实测 reasoning 就吃 4000~6500，
# 单次生成可达 2~4 分钟，用产品侧的 120s 会在生成中途被打断。
# 打断的代价不只是失败：被丢弃的那次**已经生成并计费**了，只是我们没等。
# 裁判是离线批处理，没有 "用户在看屏幕" 的约束，给足时间是正确取舍。
JUDGE_TIMEOUT = float(os.getenv("JUDGE_TIMEOUT", "300"))

# 多模态模型（评测 V5 用：把画面**直接**给模型，而不是先经 VL 转成文字）。
# 与 VISION_* 的区别：VISION_* 是「描述单帧」的辅助模型，输出文字供主模型消费；
# MULTIMODAL_* 是「直接把图和问题一起理解」的模型。两者角色不同，故分开配置。
MULTIMODAL_PROVIDER = os.getenv("MULTIMODAL_PROVIDER", "")
MULTIMODAL_ENDPOINT = os.getenv("MULTIMODAL_ENDPOINT", "")
MULTIMODAL_API_KEY = os.getenv("MULTIMODAL_API_KEY", "")
MULTIMODAL_MODEL = os.getenv("MULTIMODAL_MODEL", "")

# Smart Chat（前端可切换的高阶模型，厂家/endpoint/key/model 均独立于 default）
# 前端「智能」按钮开启 chat 时使用；未配置 SMART_LLM_API_KEY 时自动回落 default
SMART_LLM_PROVIDER = os.getenv("SMART_LLM_PROVIDER", LLM_PROVIDER)
SMART_LLM_ENDPOINT = os.getenv("SMART_LLM_ENDPOINT", LLM_ENDPOINT)
SMART_LLM_API_KEY = os.getenv("SMART_LLM_API_KEY", "")
SMART_LLM_MODEL = os.getenv("SMART_LLM_MODEL", "deepseek-v4-pro")

# Embedding
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "siliconflow")
EMBEDDING_ENDPOINT = os.getenv("EMBEDDING_ENDPOINT", "https://api.siliconflow.cn/v1/embeddings")
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")

# Vision
VISION_PROVIDER = os.getenv("VISION_PROVIDER", "dashscope")
# OpenAI 兼容 endpoint（支持 image_url 多模态），网关统一走此协议
VISION_BASE_URL = os.getenv("VISION_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
# 旧专有 endpoint，仅保留兼容
VISION_ENDPOINT = os.getenv("VISION_ENDPOINT", "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation")
VISION_API_KEY = os.getenv("VISION_API_KEY", "")
VISION_MODEL = os.getenv("VISION_MODEL", "qwen-vl-plus")
VISION_FORMAT = os.getenv("VISION_FORMAT", "openai_vision")

# ASR
ASR_PROVIDER = os.getenv("ASR_PROVIDER", "siliconflow")
ASR_ENDPOINT = os.getenv("ASR_ENDPOINT", "https://api.siliconflow.cn/v1/audio/transcriptions")
ASR_API_KEY = os.getenv("ASR_API_KEY", "")
ASR_MODEL_ASR = os.getenv("ASR_MODEL", "FunAudioLLM/SenseVoiceSmall")

# ASR URL 直传 (DashScope Paraformer)
ASR_URL_PROVIDER = os.getenv("ASR_URL_PROVIDER", "dashscope")
ASR_URL_API_KEY = os.getenv("ASR_URL_API_KEY", "")

# B 站 Cookie（可选，但**部署到机房服务器时常常是必需的**）。
#
# ★ 不配也能跑：代码会自动调 `/x/frontend/finger/spi` 取一个 `buvid3`
#   （设备指纹，无需登录）。但 B 站对**机房 IP** 的风控更严，
#   只带 buvid3 有时仍返回 412，这时贴一条登录后的完整 Cookie 最稳。
#
# 取值：浏览器打开 bilibili.com → F12 → Network → 任一请求 →
#       复制请求头里的整条 Cookie（至少要有 SESSDATA 和 buvid3）。
# ⚠️ Cookie 等同账号凭据：只填在自己的 .env 里，别提交、别外发。
BILIBILI_COOKIE = os.getenv("BILIBILI_COOKIE", "")

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
PORT = int(os.getenv("PORT", "8123"))

# 访问密钥（详见 services/auth.py）。
# 留空时：只监听本机 → 放行；监听 0.0.0.0 等对外地址 → **拒绝所有 /api 请求**。
# 也就是说「想对外提供服务，就必须设一个」—— 避免忘了配就裸奔在公网上，
# 被扫到的人拿去调 /api/videos/from_url 烧你的 LLM 额度。
# 生成：python -c "import secrets; print(secrets.token_urlsafe(24))"
API_AUTH_KEY = os.getenv("API_AUTH_KEY", "")

# 多用户：给每人发一个独立密钥，格式 `名字:密钥,名字:密钥`。
# 与上面的单密钥**可以共存**（单密钥会以 "default" 这个名字参与记账）。
#
# 为什么建议每人一个而不是共享一个：
#   ① 用量能**按人归因**，共享密钥下所有人的消耗混在一起，分不出谁
#   ② 能**单独吊销**——共享密钥要踢掉一个人，等于所有人重新配置
#   ③ 能**单独限额**（见下面的 API_DAILY_TOKEN_LIMIT）
API_AUTH_KEYS = os.getenv("API_AUTH_KEYS", "")

# 每人每天的 token 上限（输入 + 输出，按自然日）。0 = 不限制。
# 超过后该密钥的请求一律返回 429。
#
# ★ 限额比"事后看报表"更重要：等从报表里发现异常，额度已经烧完了。
#   给别人的密钥意味着他们能调 /api/videos/from_url 让你服务器下任意视频
#   （占带宽和磁盘，不只是 token），所以宁可就设一个保守的数。
API_DAILY_TOKEN_LIMIT = int(os.getenv("API_DAILY_TOKEN_LIMIT", "0"))

# 单次上传文件的大小上限（MB）。
# ★ 上传端点是**唯一**一个请求体完全由调用者控制的入口，而它原来是把整个文件
#   一把读进内存的 —— 持钥者传一个超大文件就能 OOM；docker-compose 没设
#   memory limit，被拖垮的是宿主机。现在分块写盘 + 超限即拒（413）。
# 默认 500MB 的依据：本项目自己的测试视频都在十几 MB 量级，
# 500MB 足够覆盖几小时的课程录像，同时把"一个请求打死服务"挡在外面。
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "500"))

# 允许服务器**代为访问**的媒体站点（逗号分隔，子域名自动放行）。
# 见 main._assert_url_allowed：`/api/videos/from_url` 和 `captured_subtitles_url`
# 都是让服务器去 GET 调用者给的 URL，不设闸的话持钥者可以拿它探测内网服务
# 或云元数据端点（169.254.169.254 能换到临时凭据）。
# 产品本来就只支持 B 站和 YouTube，所以白名单不会挡住正常用法；
# 要支持别的站点在这里加域名即可。
#
# ★ `hdslb.com` 必须在里面 —— B 站的**字幕 JSON 就在这个 CDN 上**
#   （`aisubtitle.hdslb.com`，插件的 manifest 也为此申请了 `*://*.hdslb.com/*`）。
#   第一版漏了它，结果整个"字幕拦截 → 上传"链路全 400、插件卡在"正在处理字幕"。
#   收录白名单时**要照着真实请求的域名来填，不能只凭"看起来像"** ——
#   当时测试用的是 `api.bilibili.com`（确实该放行），但真实链路走的是 hdslb。
#
# ⚠️ 这份名单是"漏一个就静默弄坏一个功能"的开关（只会在日志里留一条
#   ⛔ 拒绝非白名单站点）。启动时会把它打出来，改完记得核对。
ALLOWED_MEDIA_HOSTS = [
    h.strip().lower() for h in os.getenv(
        "ALLOWED_MEDIA_HOSTS",
        "bilibili.com,b23.tv,bilibili.tv,hdslb.com,"
        "youtube.com,youtu.be,youtube-nocookie.com",
    ).split(",") if h.strip()
]

# 是否对外暴露 /docs /redoc /openapi.json。
# ★ 默认按监听地址决定：只监听本机时开着（自己调试方便），
#   对外监听时关掉 —— 它会把全部 API 结构白送给扫描器。
# 需要远程看文档时显式设 API_DOCS=1。
_docs_env = os.getenv("API_DOCS", "")
if _docs_env == "":
    API_DOCS = HOST in ("127.0.0.1", "localhost", "::1")
else:
    API_DOCS = _docs_env.strip().lower() in ("1", "true", "yes", "on")

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

# 全量字幕注入的阈值（估算 token）。
# 字幕短于它 → 整段注入 messages；超长 → 退回 RAG 检索。
# 依据是评测结论：全量注入对 RAG 质量 12 项胜 9 / 负 1 / 平 2，
# 且整段字幕是稳定前缀、命中缓存后**等效成本只有 RAG 的 35%**。
#
# ★ 它和各处预算**共享** INPUT_BUDGET_TOKENS，不是各自独立的数字 ——
#   文件末尾有自检，保证"字幕 + 历史 + 记忆 + 工具预留"之和不超过它。
#   原来这些数互不知情，谁也不知道加起来是多少。
#
# 30000 是实测标定的：评测集 8 个视频字幕在 2,482~5,750 token（中位 4,090），
# 对最大的一条有 5.2× 余量；覆盖到约 1.5 小时的讲座；
# 3 小时课程（≈54,000）会被正确挡住，退回 RAG。
# 设为 0 可关闭全量注入，产品行为退回纯 RAG。
FULL_CONTEXT_MAX_TOKENS = int(os.getenv("FULL_CONTEXT_MAX_TOKENS", "30000"))

# ── 长期记忆 ──────────────────────────────────────────
# 注入预算（估算 token）。<memory> 块超过它就按类别优先级截断：
# preferences（偏好）> user_profile（画像）> learning（主题）。
# 按**类别**而不是按强度统一排 —— 否则一张反复写过的长视频摘要卡
# 会把一条短的真偏好挤出去。
MEMORY_PROMPT_BUDGET_TOKENS = int(os.getenv("MEMORY_PROMPT_BUDGET_TOKENS", "400"))
# C 类（学习主题）多久没再出现就归档。A/B 类无 TTL（用户明确说过的话长期有效）。
MEMORY_TTL_DAYS = int(os.getenv("MEMORY_TTL_DAYS", "30"))
# 攒批兜底的间隔轮数：关键词白名单没命中时，每 N 轮至少跑一次记忆提取。
# 白名单会漏掉"我比较喜欢…"这类没有触发词的表达，所以要有个兜底周期。
MEMORY_BATCH_ROUNDS = int(os.getenv("MEMORY_BATCH_ROUNDS", "5"))

# 上下文压缩配置（四层策略）
CONTEXT_MAX_MESSAGES = int(os.getenv("CONTEXT_MAX_MESSAGES", "50"))       # 第1层：最大消息数
# 历史对话的**摘要触发阈值**。超过它就把最旧的部分交给 LLM 摘要。
#
# 这个数是**从输入预算推出来的**，不是拍脑袋：
#   INPUT_BUDGET_TOKENS   64,000
#   − 字幕注入            30,000
#   − 记忆/系统/工具预留   7,500
#   = 历史上限            26,500  → 取 20,000 留余量
#
# ★ 同时必须**低于「条数上限能产出的最坏值」**，否则这套摘要机制永远不会触发。
#   实测（user/assistant 交替，50 条里约 25 条是回答）：
#     50 条 × 中位 489 字符 ≈  8,650 token  → 不触发
#     50 条 × P90 1021     ≈ 17,500 token  → 不触发
#     50 条 × 最长 1945    ≈ 32,900 token  → **触发**
#   **原值 80000 在 32,900 之上** —— 于是摘要 + 梯度再摘要 + 应急截断三层
#   从来没跑过一次，是一整套"看起来在工作"的死代码。
CONTEXT_MAX_TOKENS = int(os.getenv("CONTEXT_MAX_TOKENS", "20000"))
TOOL_TRIM_LENGTH = int(os.getenv("TOOL_TRIM_LENGTH", "500"))             # 第2层：工具输出裁剪
SUMMARY_TARGET_LENGTH = int(os.getenv("SUMMARY_TARGET_LENGTH", "200"))    # 第3层：摘要目标


# ═══════════════════════════════════════════
# 上下文预算自检（启动时一次）
# ═══════════════════════════════════════════
#
# 各处预算（字幕 / 历史 / 记忆 / 工具预留）是**各自独立**定出来的，
# 原来没有任何地方核算它们的**和** —— 谁也不知道加起来会不会超过模型窗口。
#
# 这里只做**只读检查**：不改任何行为，只在超了的时候发出警告。
# 目的是让"谁把某个阈值调大了"立刻可见，而不是等厂商 API 报上下文超限。
def _check_context_budget() -> str:
    """返回警告文本；预算之和在范围内则返回空串。"""
    parts = {
        "字幕注入": FULL_CONTEXT_MAX_TOKENS,
        "历史摘要阈值": CONTEXT_MAX_TOKENS,
        "记忆注入": MEMORY_PROMPT_BUDGET_TOKENS,
        "系统提示(估)": 1000,
        "工具结果预留(估)": 6000,      # 8 轮 × 2 次工具 × ~330 token
        "播放位置+提问(估)": 100,
    }
    total = sum(parts.values())
    if total <= INPUT_BUDGET_TOKENS:
        return ""
    detail = "、".join(f"{k} {v:,}" for k, v in parts.items())
    return (f"各处上下文预算之和 {total:,} 超过输入预算 {INPUT_BUDGET_TOKENS:,}"
            f"（模型窗口 {MODEL_CONTEXT_WINDOW_TOKENS:,} 的一半）。"
            f"明细：{detail}。请调小其中之一。")


_context_budget_warning = _check_context_budget()
if _context_budget_warning:
    # 不在导入期抛异常：服务要能起来，但必须让人看见
    import sys as _sys
    print("", file=_sys.stderr)
    print(f"[config][警告] {_context_budget_warning}", file=_sys.stderr)
    print("", file=_sys.stderr)

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
