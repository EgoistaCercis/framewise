"""
帧知 - FastAPI 主应用
多模态RAG视频学习Agent
"""
import os
import uuid
import logging
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.config import (
    UPLOAD_DIR, SUBTITLE_DIR, EMBEDDING_DIR, FRAME_DIR,
    HOST, PORT,
)

# 确保目录存在
for d in [UPLOAD_DIR, SUBTITLE_DIR, EMBEDDING_DIR, FRAME_DIR]:
    os.makedirs(d, exist_ok=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("帧知")

app = FastAPI(title="帧知 - 视频学习Agent", version="0.1.0")

# ── 内存状态管理 (MVP阶段，后续可改Redis) ──
video_states: dict = {}  # video_id → {status, video_path, video_hash, subtitles, ...}


# ═══════════════════════════════════════════
# Pydantic Models
# ═══════════════════════════════════════════

class AskRequest(BaseModel):
    question: str
    timestamp: float | None = None  # 可选：当前播放时间戳


class AskFrameRequest(BaseModel):
    question: str
    timestamp: float  # 必须：当前暂停时间戳


# ═══════════════════════════════════════════
# API Routes
# ═══════════════════════════════════════════

@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


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

    result = await answer_text_question(
        video_hash=state["video_hash"],
        question=req.question,
    )

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
    from backend.services.vision_service import extract_frame

    state = video_states.get(video_id)
    if not state:
        raise HTTPException(404, "视频不存在")
    if state["status"] != "ready":
        raise HTTPException(400, "视频尚未处理完成")

    frame_path = await extract_frame(
        video_path=state["video_path"],
        timestamp=t,
        video_hash=state["video_hash"],
    )

    return FileResponse(frame_path, media_type="image/jpeg")


@app.post("/api/videos/{video_id}/ask_frame")
async def ask_with_frame(video_id: str, req: AskFrameRequest):
    """画面问答：截取当前帧 + 视觉分析 + RAG回答"""
    from backend.services.vision_service import process_frame_question
    from backend.services.rag_service import answer_with_frame_context

    state = video_states.get(video_id)
    if not state:
        raise HTTPException(404, "视频不存在")
    if state["status"] != "ready":
        raise HTTPException(400, f"视频尚未处理完成，当前状态: {state['status']}")

    # Step 1: 提取帧 + 视觉分析
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
    )

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

    return FileResponse(
        state["video_path"],
        media_type="video/mp4",
        headers={"Accept-Ranges": "bytes"},
    )


# ═══════════════════════════════════════════
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
        embeddings = await embed_texts(chunk_texts)

        # 5. 构建FAISS索引
        build_index(chunks, embeddings, video_hash)

        # 6. 更新状态
        state["status"] = "ready"
        logger.info(f"[{video_id}] Processing complete! Ready for Q&A.")

    except Exception as e:
        state["status"] = "error"
        state["error"] = str(e)
        logger.error(f"[{video_id}] Processing failed: {e}", exc_info=True)


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
