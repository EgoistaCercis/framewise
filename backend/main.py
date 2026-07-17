"""
帧知 - FastAPI 主应用
多模态RAG视频学习Agent
"""
import os
import uuid
import json
import hashlib
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.config import (
    UPLOAD_DIR, SUBTITLE_DIR, EMBEDDING_DIR, FRAME_DIR,
    HOST, PORT, DATA_DIR, DEEPSEEK_MODEL, SILICONFLOW_EMBEDDING_MODEL, WHISPER_MODEL_SIZE,
)

# 确保目录存在
for d in [UPLOAD_DIR, SUBTITLE_DIR, EMBEDDING_DIR, FRAME_DIR]:
    os.makedirs(d, exist_ok=True)

from loguru import logger

app = FastAPI(title="帧知 - 视频学习Agent", version="0.1.0")


@app.on_event("startup")
async def startup_event():
    logger.info(f"🚀 帧知服务启动: http://{HOST}:{PORT}")
    logger.info(f"   数据目录: {os.path.abspath(DATA_DIR)}")
    logger.info(f"   DeepSeek模型: {DEEPSEEK_MODEL}")
    logger.info(f"   Embedding模型: {SILICONFLOW_EMBEDDING_MODEL}")
    logger.info(f"   Whisper模型: {WHISPER_MODEL_SIZE}")

# CORS — 允许浏览器插件跨域请求
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 请求日志中间件（含 request_id 追踪）
@app.middleware("http")
async def log_requests(request, call_next):
    import uuid
    from time import time

    req_id = uuid.uuid4().hex[:8]
    start = time()

    with logger.contextualize(request_id=req_id):
        response = await call_next(request)
        elapsed = (time() - start) * 1000
        if not request.url.path.startswith("/static") and request.url.path != "/api/health":
            logger.info(f"{request.method} {request.url.path} → {response.status_code} ({elapsed:.0f}ms)")
    return response

# ── 状态持久化管理 ──

STATES_FILE = os.path.join(DATA_DIR, "video_states.json")
video_states: dict = {}

def _save_states():
    """持久化 video_states 到文件"""
    try:
        serializable = {}
        for vid, state in video_states.items():
            s = {k: v for k, v in state.items()}
            # 不序列化大对象（subtitles, chunks 有单独缓存）
            s.pop("subtitles", None)
            s.pop("chunks", None)
            serializable[vid] = s
        with open(STATES_FILE, "w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Failed to save states: {e}")


def _load_states():
    """从文件恢复 video_states"""
    global video_states
    if os.path.exists(STATES_FILE):
        try:
            with open(STATES_FILE, "r", encoding="utf-8") as f:
                video_states = json.load(f)
            # 更新 data 目录路径（可能在别的机器上不同）
            for vid, state in video_states.items():
                if state.get("video_path") and not os.path.exists(state["video_path"]):
                    state["video_path"] = None  # 本地文件已不存在
            logger.info(f"Restored {len(video_states)} video states from disk")
        except Exception as e:
            logger.warning(f"Failed to load states: {e}")
            video_states = {}
    else:
        video_states = {}

_load_states()


# ═══════════════════════════════════════════
# Pydantic Models
# ═══════════════════════════════════════════

class AskRequest(BaseModel):
    question: str
    timestamp: float | None = None  # 可选：当前播放时间戳


class AskFrameRequest(BaseModel):
    question: str
    timestamp: float  # 当前暂停时间戳
    frame_base64: str | None = None  # 浏览器端截图的base64（优先使用）


# ═══════════════════════════════════════════
# API Routes
# ═══════════════════════════════════════════

@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


# ═══════════════════════════════════════════
# 用量统计 API
# ═══════════════════════════════════════════

@app.post("/api/videos/{video_id}/quiz")
async def generate_quiz_endpoint(video_id: str, req: dict):
    """主动学习：根据当前视频位置生成考题"""
    from backend.services.rag_service import generate_quiz

    state = video_states.get(video_id)
    if not state:
        raise HTTPException(404, "视频不存在")
    if state["status"] != "ready":
        raise HTTPException(400, "视频尚未处理完成")

    timestamp = req.get("timestamp", 0)
    logger.info(f"[{video_id}] 智能出题 @ {timestamp:.0f}s")
    result = await generate_quiz(
        video_hash=state["video_hash"],
        timestamp=timestamp,
        video_id=video_id,
    )
    return result


@app.get("/api/videos/{video_id}/history")
async def get_chat_history(video_id: str, limit: int = 50):
    """获取视频的聊天记录"""
    from backend.services.conversation_service import get_history
    return get_history(video_id, limit)


@app.get("/api/conversations")
async def list_conversations():
    """列出所有有对话记录的视频"""
    from backend.services.conversation_service import list_conversations
    return list_conversations()


@app.get("/api/usage/today")
async def usage_today():
    """今日用量统计"""
    from backend.services.cost_service import get_today_stats
    return get_today_stats()


@app.get("/api/usage/total")
async def usage_total():
    """总用量统计"""
    from backend.services.cost_service import get_total_stats
    return get_total_stats()


@app.get("/api/usage/by_model")
async def usage_by_model():
    """按模型分组统计"""
    from backend.services.cost_service import get_stats_by_model
    return get_stats_by_model()


@app.get("/api/usage/history")
async def usage_history(limit: int = 30):
    """最近调用记录"""
    from backend.services.cost_service import get_history
    return get_history(limit)


@app.post("/api/videos/from_url")
async def process_url(background_tasks: BackgroundTasks, req: dict):
    """
    从视频链接创建知识索引（仅下载音频，不存视频）
    Body: {url: "https://..."}
    """
    from backend.services.url_service import get_video_info, download_audio

    url = req.get("url", "").strip()
    if not url:
        raise HTTPException(400, "请提供视频链接")

    # URL标准化：提取规范ID，去除跟踪参数
    canonical_id = _canonical_video_id(url)
    video_id = hashlib.md5(canonical_id.encode()).hexdigest()[:12]

    # 检查内存状态
    if video_id in video_states and video_states[video_id].get("status") == "ready":
        logger.info(f"Video already ready (memory): {video_id}")
        return {
            "video_id": video_id,
            "status": "ready",
            "title": video_states[video_id]["original_name"],
        }

    # 检查磁盘缓存（服务重启后内存清空，但磁盘缓存还在）
    from backend.services.cache_service import embedding_cache_exists, subtitle_cache_exists
    if embedding_cache_exists(video_id) or subtitle_cache_exists(video_id):
        logger.info(f"Video already indexed (disk cache): {video_id}")
        video_states[video_id] = {
            "status": "ready",
            "video_path": None,
            "video_hash": video_id,
            "original_name": url,
            "subtitles": None,
            "chunks": None,
            "url": url,
            "embed_url": url,
            "is_url_mode": True,
        }
        _save_states()
        return {"video_id": video_id, "status": "ready", "title": url}

    # 获取视频信息
    try:
        info = get_video_info(url)
    except Exception as e:
        raise HTTPException(400, f"无法获取视频信息: {str(e)}")

    # 初始化状态
    video_states[video_id] = {
        "status": "processing",
        "video_path": None,  # URL模式没有本地视频
        "video_hash": None,
        "original_name": info["title"],
        "subtitles": None,
        "chunks": None,
        "url": url,
        "embed_url": info.get("embed_url", url),
        "duration": info.get("duration", 0),
        "is_url_mode": True,
    }

    # 后台处理：下载音频 → ASR → Chunk → Embedding
    background_tasks.add_task(_process_url_task, video_id, url)
    _save_states()

    logger.info(f"URL video processing: {video_id} ({info['title']})")
    return {
        "video_id": video_id,
        "status": "processing",
        "title": info["title"],
        "duration": info["duration"],
    }


@app.post("/api/videos/upload")
async def upload_video(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    """上传视频文件，启动后台ASR处理"""
    # 生成唯一ID并保存文件
    video_id = uuid.uuid4().hex[:12]
    ext = Path(file.filename).suffix or ".mp4"
    safe_name = f"{video_id}{ext}"
    video_path = os.path.join(UPLOAD_DIR, safe_name)

    content = await file.read()
    with open(video_path, "wb") as f:
        f.write(content)

    # 初始化状态
    video_states[video_id] = {
        "status": "processing",
        "video_path": video_path,
        "video_hash": None,
        "original_name": file.filename,
        "subtitles": None,
        "chunks": None,
    }

    # 后台处理
    background_tasks.add_task(_process_video_task, video_id)

    logger.info(f"Video uploaded: {video_id} ({file.filename})")
    return {"video_id": video_id, "status": "processing", "original_name": file.filename}


@app.get("/api/videos/{video_id}")
async def get_video_info(video_id: str):
    """获取视频信息和处理状态"""
    state = video_states.get(video_id)
    if not state:
        raise HTTPException(404, "视频不存在")
    return {
        "video_id": video_id,
        "status": state["status"],
        "original_name": state["original_name"],
        "chunk_count": len(state.get("chunks", [])) if state.get("chunks") else 0,
        "is_url_mode": state.get("is_url_mode", False),
        "embed_url": state.get("embed_url", None),
        "duration": state.get("duration", 0),
    }


@app.get("/api/videos/{video_id}/subtitles")
async def get_subtitles(video_id: str):
    """获取视频字幕（前端播放器同步用）"""
    state = video_states.get(video_id)
    if not state:
        raise HTTPException(404, "视频不存在")
    if state["status"] != "ready":
        raise HTTPException(400, f"视频尚未处理完成，当前状态: {state['status']}")

    return {
        "subtitles": state.get("subtitles", []),
        "video_id": video_id,
    }


@app.post("/api/videos/{video_id}/ask")
async def ask_question(video_id: str, req: AskRequest):
    """文本问答：基于字幕内容的RAG问答"""
    from backend.services.rag_service import answer_text_question

    state = video_states.get(video_id)
    if not state:
        raise HTTPException(404, "视频不存在")
    if state["status"] != "ready":
        raise HTTPException(400, f"视频尚未处理完成，当前状态: {state['status']}")

    logger.info(f"[{video_id}] 文本提问: {req.question[:50]}...")
    result = await answer_text_question(
        video_hash=state["video_hash"],
        question=req.question,
        video_id=video_id,
    )

    # 保存对话记录
    from backend.services.conversation_service import save_exchange
    save_exchange(video_id, req.question, result["answer"])

    return {
        "video_id": video_id,
        "question": req.question,
        "answer": result["answer"],
        "references": result["references"],
        "timestamp": req.timestamp,
    }


@app.get("/api/videos/{video_id}/frame")
async def get_frame(video_id: str, t: float):
    """获取视频指定时间戳的帧图片"""
    state = video_states.get(video_id)
    if not state:
        raise HTTPException(404, "视频不存在")
    if state["status"] != "ready":
        raise HTTPException(400, "视频尚未处理完成")

    if state.get("is_url_mode"):
        # URL模式：从远程下载帧
        from backend.services.url_service import download_frame_at_time
        frame_path = download_frame_at_time(state["url"], t, video_id)
    else:
        # 本地模式：从本地视频截图
        from backend.services.vision_service import extract_frame
        frame_path = await extract_frame(
            video_path=state["video_path"],
            timestamp=t,
            video_hash=state["video_hash"],
        )

    return FileResponse(frame_path, media_type="image/jpeg")


@app.post("/api/videos/{video_id}/ask_frame")
async def ask_with_frame(video_id: str, req: AskFrameRequest):
    """画面问答：截取当前帧 + 视觉分析 + RAG回答"""
    from backend.services.vision_service import process_frame_question, analyze_frame
    from backend.services.rag_service import answer_with_frame_context

    state = video_states.get(video_id)
    if not state:
        raise HTTPException(404, "视频不存在")
    if state["status"] != "ready":
        raise HTTPException(400, f"视频尚未处理完成，当前状态: {state['status']}")

    # Step 1: 提取帧 + 视觉分析
    if req.frame_base64:
        # 浏览器端已截图，直接用base64分析
        import base64
        import tempfile
        frame_data = base64.b64decode(req.frame_base64)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp.write(frame_data)
            frame_path = tmp.name
        description = await analyze_frame(frame_path, video_id=video_id)
        frame_result = {"frame_path": frame_path, "description": description}
        # 清理临时文件
        os.unlink(frame_path)
    elif state.get("is_url_mode"):
        # URL模式服务端截帧
        from backend.services.url_service import download_frame_at_time
        frame_path = download_frame_at_time(state["url"], req.timestamp, video_id)
        description = await analyze_frame(frame_path, video_id=video_id)
        frame_result = {"frame_path": frame_path, "description": description}
    else:
        # 本地视频服务端截帧
        frame_result = await process_frame_question(
            video_path=state["video_path"],
            video_hash=state["video_hash"],
            timestamp=req.timestamp,
            question=req.question,
        )

    # Step 2: 结合RAG回答
    result = await answer_with_frame_context(
        video_hash=state["video_hash"],
        question=req.question,
        frame_description=frame_result["description"],
        video_id=video_id,
    )

    # 保存对话记录
    from backend.services.conversation_service import save_exchange
    save_exchange(video_id, req.question, result["answer"])

    return {
        "video_id": video_id,
        "question": req.question,
        "timestamp": req.timestamp,
        "answer": result["answer"],
        "references": result["references"],
        "frame_description": result.get("frame_description", frame_result["description"]),
    }


@app.get("/api/videos/{video_id}/file")
async def serve_video(video_id: str):
    """提供视频文件播放"""
    state = video_states.get(video_id)
    if not state:
        raise HTTPException(404, "视频不存在")

    if state.get("is_url_mode"):
        # URL模式：返回嵌入URL，前端用iframe播放
        return {"is_url_mode": True, "embed_url": state.get("embed_url", state["url"])}

    return FileResponse(
        state["video_path"],
        media_type="video/mp4",
        headers={"Accept-Ranges": "bytes"},
    )


# ═══════════════════════════════════════════
def _canonical_video_id(url: str) -> str:
    """从URL提取规范视频ID，忽略跟踪参数"""
    from urllib.parse import urlparse, parse_qs
    import re
    bv = re.search(r'(BV\w+)', url)
    if bv:
        return f"bilibili:{bv.group(1)}"
    ep = re.search(r'/bangumi/play/(\w+)', url)
    if ep:
        return f"bilibili:ep{ep.group(1)}"
    parsed = urlparse(url)
    if "youtube.com" in parsed.netloc or "youtu.be" in parsed.netloc:
        yt_id = (parsed.path.strip("/") if "youtu.be" in parsed.netloc
                 else parse_qs(parsed.query).get("v", [""])[0])
        if yt_id:
            return f"youtube:{yt_id}"
    return parsed.scheme + "://" + parsed.netloc + parsed.path


# 后台任务
# ═══════════════════════════════════════════

async def _process_video_task(video_id: str):
    """后台处理视频：ASR → Chunk → Embedding → FAISS"""
    from backend.services.cache_service import get_video_hash
    from backend.services.asr_service import process_video_to_subtitles
    from backend.services.chunk_service import chunk_subtitles
    from backend.services.embedding_service import embed_texts
    from backend.services.vector_store import build_index

    state = video_states.get(video_id)
    if not state:
        return

    try:
        video_path = state["video_path"]

        # 1. 计算视频Hash
        video_hash = get_video_hash(video_path)
        state["video_hash"] = video_hash
        logger.info(f"[{video_id}] Video hash: {video_hash}")

        # 2. ASR → 字幕
        logger.info(f"[{video_id}] Starting ASR...")
        subtitles = await process_video_to_subtitles(video_path, video_hash)
        state["subtitles"] = subtitles
        logger.info(f"[{video_id}] ASR done: {len(subtitles)} segments")

        # 3. Chunk切分
        chunks = chunk_subtitles(subtitles, video_id)
        state["chunks"] = chunks

        # 4. Embedding
        logger.info(f"[{video_id}] Generating embeddings for {len(chunks)} chunks...")
        chunk_texts = [c["text"] for c in chunks]
        embeddings = await embed_texts(chunk_texts, video_id=video_id)

        # 5. 构建FAISS索引
        build_index(chunks, embeddings, video_hash)

        # 6. 更新状态
        state["status"] = "ready"
        _save_states()
        logger.info(f"[{video_id}] Processing complete! Ready for Q&A.")

    except Exception as e:
        state["status"] = "error"
        state["error"] = str(e)
        _save_states()
        logger.opt(exception=e).error(f"[{video_id}] Processing failed")


async def _process_url_task(video_id: str, url: str):
    """后台处理URL视频：下载音频 → ASR → Chunk → Embedding"""
    from backend.services.url_service import download_audio, cleanup_audio
    from backend.services.cache_service import save_subtitle_cache, subtitle_cache_exists, load_subtitle_cache
    from backend.services.asr_service import transcribe
    from backend.services.chunk_service import chunk_subtitles
    from backend.services.embedding_service import embed_texts
    from backend.services.vector_store import build_index

    state = video_states.get(video_id)
    if not state:
        return

    audio_path = None
    try:
        # 1. 下载音频
        logger.info(f"[{video_id}] Downloading audio from URL...")
        audio_path = download_audio(url, video_id)

        # 2. URL模式用video_id作缓存键（同一URL永远同一ID，无需音频hash）
        state["video_hash"] = video_id

        # 3. 检查字幕缓存
        if subtitle_cache_exists(video_id):
            logger.info(f"[{video_id}] Subtitle cache hit")
            subtitles = load_subtitle_cache(video_id)
        else:
            logger.info(f"[{video_id}] Starting ASR...")
            subtitles = transcribe(audio_path)
            save_subtitle_cache(video_id, subtitles)

        state["subtitles"] = subtitles
        logger.info(f"[{video_id}] ASR done: {len(subtitles)} segments")

        # 4. Chunk + Embedding + FAISS
        chunks = chunk_subtitles(subtitles, video_id)
        state["chunks"] = chunks

        chunk_texts = [c["text"] for c in chunks]
        embeddings = await embed_texts(chunk_texts, video_id=video_id)
        build_index(chunks, embeddings, video_id)

        state["status"] = "ready"
        _save_states()
        logger.info(f"[{video_id}] URL processing complete! Ready for Q&A.")

    except Exception as e:
        state["status"] = "error"
        state["error"] = str(e)
        _save_states()
        logger.opt(exception=e).error(f"[{video_id}] URL processing failed")
    finally:
        if audio_path and os.path.exists(audio_path):
            cleanup_audio(video_id)


# ═══════════════════════════════════════════
# 静态文件 & 前端
# ═══════════════════════════════════════════

# 挂载前端静态资源
frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend", "static")
if os.path.exists(frontend_dir):
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")


@app.get("/")
async def index():
    """前端入口"""
    index_path = os.path.join(frontend_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return JSONResponse({"message": "帧知API服务运行中", "docs": "/docs"}, status_code=200)


# ═══════════════════════════════════════════
# 启动入口
# ═══════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host=HOST, port=PORT, reload=True)
