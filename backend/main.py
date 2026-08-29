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
    ASR_MODE, DASHSCOPE_API_KEY,
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
    logger.info(f"   ASR模式: {ASR_MODE}")

    # 恢复启动时中断的"处理中"状态 → 标记为 error
    import asyncio
    recovered = 0
    for vid, st in list(video_states.items()):
        if st.get("status") == "processing":
            # 检查磁盘缓存是否存在
            from backend.services.media.cache_service import embedding_cache_exists
            if embedding_cache_exists(vid):
                st["status"] = "ready"
                logger.info(f"Recovered {vid} from disk cache → ready")
            else:
                st["status"] = "error"
                st["error"] = "服务重启导致处理中断，请重新提交"
            recovered += 1
    if recovered:
        _save_states()
        logger.info(f"Recovered {recovered} stale states")

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
    smart: bool = False  # 前端「智能」按钮：使用高阶模型


class AskFrameRequest(BaseModel):
    question: str
    timestamp: float  # 当前暂停时间戳
    frame_base64: str | None = None  # 浏览器端截图的base64（优先使用）
    smart: bool = False  # 前端「智能」按钮：使用高阶模型


# ═══════════════════════════════════════════
# API Routes
# ═══════════════════════════════════════════

@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/api/llm_config")
async def llm_config():
    """返回 LLM 配置信息（不含密钥），供前端判断「智能模型」是否已配置"""
    from backend import config as _c
    smart_configured = bool(_c.SMART_LLM_API_KEY)
    return {
        "default_provider": _c.LLM_PROVIDER,
        "default_model": _c.LLM_MODEL,
        "smart_configured": smart_configured,
        "smart_provider": _c.SMART_LLM_PROVIDER if smart_configured else None,
        "smart_model": _c.SMART_LLM_MODEL if smart_configured else None,
    }


@app.get("/api/memory")
async def list_memory():
    """查看长期记忆"""
    from backend.services.memory.memory_service import get_all_memories
    return get_all_memories()


@app.delete("/api/memory")
async def clear_memory():
    """清空长期记忆"""
    from backend.services.memory.memory_service import get_all_memories, delete_memory
    for m in get_all_memories():
        delete_memory(m["key"])
    return {"status": "ok"}


# ═══════════════════════════════════════════
# 用量统计 API
# ═══════════════════════════════════════════

@app.post("/api/videos/{video_id}/captured_subtitles_url")
async def captured_subtitles_url(video_id: str, req: dict):
    """接收浏览器拦截的B站字幕URL，下载、缓存、建立索引（一步到位）"""
    from backend.services.rag_pipeline.chunk_service import chunk_subtitles
    from backend.services.rag_pipeline.embedding_service import embed_texts
    from backend.services.rag_pipeline.vector_store import build_index
    from backend.services.media.cache_service import save_subtitle_cache
    import httpx

    state = video_states.get(video_id)
    if not state:
        raise HTTPException(404, "视频不存在")

    url = req.get("subtitle_url", "")
    if not url:
        raise HTTPException(400, "字幕URL为空")
    if url.startswith("//"): url = "https:" + url

    # B站 ai_subtitle 的 auth_key 签名绑定具体视频页面，Referer 必须是该页面否则 403
    referer = req.get("referer", "") or "https://www.bilibili.com/"
    if "bilibili.com" not in referer:
        referer = "https://www.bilibili.com/"

    logger.info(f"[{video_id}] Downloading B站 subtitle (referer={referer[:60]}...)")
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(url, headers={
            "Referer": referer,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
            "Origin": "https://www.bilibili.com",
            "Accept": "application/json, text/plain, */*",
        })
        resp.raise_for_status()
        sub_data = resp.json()

    body = sub_data.get("body", [])
    subtitles = []
    for item in body:
        text = item.get("content", "").strip()
        if text:
            subtitles.append({"text": text, "start": round(item.get("from", 0), 2), "end": round(item.get("to", 0), 2)})

    if not subtitles:
        raise HTTPException(400, "字幕数据为空")

    logger.info(f"[{video_id}] B站 subtitle: {len(subtitles)} lines (cached, wait for index)")
    state["subtitles"] = subtitles
    state["video_hash"] = video_id
    save_subtitle_cache(video_id, subtitles)

    # 缓存字幕，中止正在跑的后台任务（如有），等用户手动触发索引
    state["status"] = "subtitles"
    state["progress"] = 30
    state["progress_text"] = "字幕已缓存，点击 🔄 处理"
    _save_states()
    logger.info(f"[{video_id}] Subtitles cached, pending manual indexing")
    return {"status": "ok", "segments": len(subtitles)}


@app.post("/api/videos/{video_id}/captured_subtitles")
async def captured_subtitles(video_id: str, req: dict):
    """接收Chrome插件采集的B站AI字幕"""
    from backend.services.rag_pipeline.chunk_service import chunk_subtitles
    from backend.services.rag_pipeline.embedding_service import embed_texts
    from backend.services.rag_pipeline.vector_store import build_index
    from backend.services.media.cache_service import save_subtitle_cache

    state = video_states.get(video_id)
    if not state:
        raise HTTPException(404, "视频不存在")

    subtitles = req.get("subtitles", [])
    if not subtitles:
        raise HTTPException(400, "字幕数据为空")

    logger.info(f"[{video_id}] Received {len(subtitles)} subtitle lines from browser")

    state["subtitles"] = subtitles
    state["video_hash"] = video_id
    save_subtitle_cache(video_id, subtitles)

    chunks = chunk_subtitles(subtitles, video_id)
    state["chunks"] = chunks

    chunk_texts = [c["text"] for c in chunks]
    embeddings = await embed_texts(chunk_texts, video_id=video_id)
    build_index(chunks, embeddings, video_id)
    _save_states()

    state["status"] = "ready"
    state["progress"] = 100
    logger.info(f"[{video_id}] Subtitles processed: {len(chunks)} chunks")
    return {"status": "ok", "chunks": len(chunks)}


@app.post("/api/videos/{video_id}/quiz")
async def generate_quiz_endpoint(video_id: str, req: dict):
    """主动学习：根据当前视频位置生成考题"""
    from backend.services.rag_pipeline.rag_service import generate_quiz

    state = video_states.get(video_id)
    if not state:
        raise HTTPException(404, "视频不存在")
    if state["status"] != "ready":
        raise HTTPException(400, "视频尚未处理完成")

    timestamp = req.get("timestamp", 0)
    smart = req.get("smart", False)
    logger.info(f"[{video_id}] 智能出题 @ {timestamp:.0f}s (smart={smart})")
    result = await generate_quiz(
        video_hash=state["video_hash"],
        timestamp=timestamp,
        video_id=video_id,
        smart=smart,
    )
    return result


@app.get("/api/videos/{video_id}/history")
async def get_chat_history(video_id: str, limit: int = 50):
    """获取视频的聊天记录"""
    from backend.services.rag_pipeline.conversation_service import get_history
    return get_history(video_id, limit)


@app.get("/api/conversations")
async def list_conversations():
    """列出所有有对话记录的视频"""
    from backend.services.rag_pipeline.conversation_service import list_conversations
    return list_conversations()


@app.get("/api/usage/today")
async def usage_today():
    """今日用量统计"""
    from backend.services.llm.cost_service import get_today_stats
    return get_today_stats()


@app.get("/api/usage/total")
async def usage_total():
    """总用量统计"""
    from backend.services.llm.cost_service import get_total_stats
    return get_total_stats()


@app.get("/api/usage/by_model")
async def usage_by_model():
    """按模型分组统计"""
    from backend.services.llm.cost_service import get_stats_by_model
    return get_stats_by_model()


@app.get("/api/usage/history")
async def usage_history(limit: int = 30):
    """最近调用记录"""
    from backend.services.llm.cost_service import get_history
    return get_history(limit)


# ═══════════════════════════════════════════════════════
# 定价（pricing 组件）
# ═══════════════════════════════════════════════════════

class PricingUpdate(BaseModel):
    model: str
    input_ppm: float
    output_ppm: float
    cache_ppm: float = 0
    reasoning_ppm: float = 0
    provider: str = ""
    note: str = ""


@app.get("/api/pricing")
async def pricing_list():
    """查看所有模型的价格版本（含历史）"""
    from backend.services.llm.pricing_service import get_all
    return get_all()


@app.post("/api/pricing")
async def pricing_update(req: PricingUpdate):
    """更新某模型为最新价（旧版本置为 inactive）"""
    from backend.services.llm.pricing_service import update_price
    pid = update_price(
        req.model, req.input_ppm, req.output_ppm,
        cache_ppm=req.cache_ppm, reasoning_ppm=req.reasoning_ppm,
        provider=req.provider, note=req.note,
    )
    return {"status": "ok", "id": pid, "model": req.model}


@app.post("/api/pricing/refresh")
async def pricing_refresh(model: str = ""):
    """自动拉取最新价（预留接口，默认未接入）"""
    from backend.services.llm.pricing_service import fetch_latest_price
    if not model:
        return {"status": "not_implemented", "message": "请指定 model；自动拉价未接入"}
    price = fetch_latest_price(model)
    if price is None:
        return {"status": "not_implemented", "message": f"自动拉价未接入：{model}"}
    return {"status": "ok", "model": model, "price": price}


@app.post("/api/videos/from_url")
async def process_url(background_tasks: BackgroundTasks, req: dict):
    """
    从视频链接创建知识索引（仅下载音频，不存视频）
    Body: {url: "https://..."}
    """
    from backend.services.media.url_service import get_video_info, download_audio

    url = req.get("url", "").strip()
    if not url:
        raise HTTPException(400, "请提供视频链接")

    # URL标准化：提取规范ID，去除跟踪参数
    canonical_id = _canonical_video_id(url)
    video_id = hashlib.md5(canonical_id.encode()).hexdigest()[:12]

    # 检查内存状态
    if video_id in video_states:
        st = video_states[video_id]
        if st.get("status") == "ready":
            logger.info(f"Video already ready (memory): {video_id}")
            return {"video_id": video_id, "status": "ready", "title": st["original_name"]}
        elif st.get("status") == "subtitles":
            # 字幕已缓存，等待手动触发
            force = req.get("force", False)
            if force:
                logger.info(f"[{video_id}] Manual trigger with subtitles")
                st["status"] = "processing"  # 改状态让后台任务放行
                _save_states()
                background_tasks.add_task(_process_url_task, video_id, url)
                return {"video_id": video_id, "status": "processing", "title": st.get("original_name", url)}
            logger.info(f"[{video_id}] Subtitles cached, waiting for manual trigger")
            return {"video_id": video_id, "status": "subtitles", "title": st.get("original_name", url)}
        elif st.get("status") == "error":
            logger.info(f"Video previously failed, retrying: {video_id}")
            del video_states[video_id]

    # 检查磁盘缓存（服务重启后内存清空，但磁盘缓存还在）
    from backend.services.media.cache_service import embedding_cache_exists, subtitle_cache_exists
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
        "progress": state.get("progress", 0),
        "progress_text": state.get("progress_text", ""),
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
    from backend.services.rag_pipeline.rag_service import answer_text_question

    state = video_states.get(video_id)
    if not state:
        raise HTTPException(404, "视频不存在")
    if state["status"] != "ready":
        raise HTTPException(400, f"视频尚未处理完成，当前状态: {state['status']}")

    try:
        result = await answer_text_question(
            video_hash=state["video_hash"],
            question=req.question,
            video_id=video_id,
            smart=req.smart,
        )
    except FileNotFoundError:
        state["status"] = "error"
        state["error"] = "索引文件丢失，请点击🔄按钮重新处理"
        _save_states()
        raise HTTPException(410, "索引丢失，请重新处理该视频")
    except RuntimeError as e:
        error_msg = str(e)
        logger.error(f"[{video_id}] Provider error: {error_msg}")
        raise HTTPException(503, detail=error_msg)

    # 保存对话记录
    from backend.services.rag_pipeline.conversation_service import save_exchange
    save_exchange(video_id, req.question, result["answer"], references=result["references"])

    # 提取长期记忆（后台不阻塞）
    import asyncio
    from backend.services.rag_pipeline.rag_service import extract_memory
    asyncio.create_task(extract_memory(req.question, result["answer"]))

    return {
        "video_id": video_id,
        "question": req.question,
        "answer": result["answer"],
        "references": result["references"],
        "timestamp": req.timestamp,
    }


@app.post("/api/videos/{video_id}/ask_stream")
async def ask_stream(video_id: str, req: AskRequest):
    """流式文本问答：SSE 推送 token"""
    from fastapi.responses import StreamingResponse
    import json

    state = video_states.get(video_id)
    if not state:
        raise HTTPException(404, "视频不存在")
    if state["status"] != "ready":
        raise HTTPException(400, f"视频尚未处理完成，当前状态: {state['status']}")

    from backend.services.rag_pipeline.rag_service import prepare_rag_context, SYSTEM_PROMPT

    try:
        user_prompt, results = await prepare_rag_context(
            video_hash=state["video_hash"],
            question=req.question,
            video_id=video_id,
        )
    except FileNotFoundError:
        raise HTTPException(410, "索引丢失，请重新处理该视频")

    refs = [{"text": r["chunk"]["text"], "start_time": r["chunk"]["start_time"],
              "end_time": r["chunk"]["end_time"], "score": r["score"]} for r in results]

    async def generate():
        from backend.services.llm.gateway import chat_stream
        from backend.config import LLM_MAX_TOKENS
        full = ""
        try:
            async for token in chat_stream(
                messages=[{"role": "user", "content": user_prompt}],
                system_prompt=SYSTEM_PROMPT,
                max_tokens=LLM_MAX_TOKENS,
                smart=req.smart,
            ):
                full += token
                yield f"data: {json.dumps({'token': token})}\n\n".encode()
        except RuntimeError as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n".encode()
            return

        # 保存对话记录
        from backend.services.rag_pipeline.conversation_service import save_exchange
        save_exchange(video_id, req.question, full, references=refs)
        import asyncio as _aio
        from backend.services.rag_pipeline.rag_service import extract_memory
        _aio.create_task(extract_memory(req.question, full))
        yield f"data: {json.dumps({'done': True, 'references': refs})}\n\n".encode()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
        from backend.services.media.url_service import download_frame_at_time
        frame_path = download_frame_at_time(state["url"], t, video_id)
    else:
        # 本地模式：从本地视频截图
        from backend.services.media.vision_service import extract_frame
        frame_path = await extract_frame(
            video_path=state["video_path"],
            timestamp=t,
            video_hash=state["video_hash"],
        )

    return FileResponse(frame_path, media_type="image/jpeg")


@app.post("/api/videos/{video_id}/ask_frame")
async def ask_with_frame(video_id: str, req: AskFrameRequest):
    """画面问答：截取当前帧 + 视觉分析 + RAG回答"""
    from backend.services.media.vision_service import process_frame_question, analyze_frame
    from backend.services.rag_pipeline.rag_service import answer_with_frame_context

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
        from backend.services.media.url_service import download_frame_at_time
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
    try:
        result = await answer_with_frame_context(
            video_hash=state["video_hash"],
            question=req.question,
            frame_description=frame_result["description"],
            video_id=video_id,
            smart=req.smart,
        )
    except RuntimeError as e:
        raise HTTPException(503, detail=str(e))

    # 保存对话记录
    from backend.services.rag_pipeline.conversation_service import save_exchange
    save_exchange(video_id, req.question, result["answer"], references=result["references"])

    return {
        "video_id": video_id,
        "question": req.question,
        "timestamp": req.timestamp,
        "answer": result["answer"],
        "references": result["references"],
        "frame_description": result.get("frame_description", frame_result["description"]),
    }


async def _analyze_frame_req(video_id: str, state: dict, req: AskFrameRequest) -> str:
    """提取帧并分析画面，返回描述文本"""
    from backend.services.media.vision_service import process_frame_question, analyze_frame

    if req.frame_base64:
        import base64, tempfile
        frame_data = base64.b64decode(req.frame_base64)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp.write(frame_data)
            frame_path = tmp.name
        try:
            return await analyze_frame(frame_path, video_id=video_id)
        finally:
            os.unlink(frame_path)
    elif state.get("is_url_mode"):
        from backend.services.media.url_service import download_frame_at_time
        frame_path = download_frame_at_time(state["url"], req.timestamp, video_id)
        return await analyze_frame(frame_path, video_id=video_id)
    else:
        frame_result = await process_frame_question(
            video_path=state["video_path"],
            video_hash=state["video_hash"],
            timestamp=req.timestamp,
            question=req.question,
        )
        return frame_result["description"]


@app.post("/api/videos/{video_id}/ask_frame_stream")
async def ask_frame_stream(video_id: str, req: AskFrameRequest):
    """画面问答流式：先分析画面，再流式输出 LLM 回答"""
    from fastapi.responses import StreamingResponse
    import json

    state = video_states.get(video_id)
    if not state:
        raise HTTPException(404, "视频不存在")
    if state["status"] != "ready":
        raise HTTPException(400, f"视频尚未处理完成，当前状态: {state['status']}")

    from backend.services.rag_pipeline.rag_service import prepare_frame_context, SYSTEM_PROMPT

    # 先分析画面（非流式，耗时）
    try:
        description = await _analyze_frame_req(video_id, state, req)
        user_prompt, results = await prepare_frame_context(
            video_hash=state["video_hash"],
            question=req.question,
            frame_description=description,
            video_id=video_id,
        )
    except RuntimeError as e:
        raise HTTPException(503, detail=str(e))

    refs = [{"text": r["chunk"]["text"], "start_time": r["chunk"]["start_time"],
              "end_time": r["chunk"]["end_time"], "score": r["score"]} for r in results]

    async def generate():
        from backend.services.llm.gateway import chat_stream
        from backend.config import LLM_MAX_TOKENS
        full = ""
        try:
            async for token in chat_stream(
                messages=[{"role": "user", "content": user_prompt}],
                system_prompt=SYSTEM_PROMPT,
                max_tokens=LLM_MAX_TOKENS,
                smart=req.smart,
            ):
                full += token
                yield f"data: {json.dumps({'token': token})}\n\n".encode()
        except RuntimeError as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n".encode()
            return

        from backend.services.rag_pipeline.conversation_service import save_exchange
        save_exchange(video_id, req.question, full, references=refs)
        import asyncio as _aio
        from backend.services.rag_pipeline.rag_service import extract_memory
        _aio.create_task(extract_memory(req.question, full))
        yield f"data: {json.dumps({'done': True, 'references': refs, 'frame_description': description})}\n\n".encode()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
    """从URL提取规范视频ID，含合集分集"""
    from urllib.parse import urlparse, parse_qs
    import re
    bv = re.search(r'(BV\w+)', url)
    if bv:
        # 检查是否合集分集 ?p=N 或 &p=N
        p = re.search(r'[?&]p=(\d+)', url)
        pid = f"{bv.group(1)}_p{p.group(1)}" if p else bv.group(1)
        return f"bilibili:{pid}"
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
    from backend.services.media.cache_service import get_video_hash
    from backend.services.media.asr_service import process_video_to_subtitles
    from backend.services.rag_pipeline.chunk_service import chunk_subtitles
    from backend.services.rag_pipeline.embedding_service import embed_texts
    from backend.services.rag_pipeline.vector_store import build_index

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
    """后台处理URL视频：获取音频直链 → ASR(URL直传) → Chunk → Embedding"""
    from backend.services.media.url_service import download_audio, cleanup_audio, get_audio_stream_url, extract_subtitles
    from backend.services.media.cache_service import save_subtitle_cache, subtitle_cache_exists, load_subtitle_cache
    from backend.services.media.asr_service import transcribe
    from backend.services.media.asr_api_service import transcribe_via_url
    from backend.services.rag_pipeline.chunk_service import chunk_subtitles
    from backend.services.rag_pipeline.embedding_service import embed_texts
    from backend.services.rag_pipeline.vector_store import build_index

    state = video_states.get(video_id)
    if not state:
        return

    def _progress(pct, text):
        state["progress"] = pct
        state["progress_text"] = text
        _save_states()

    audio_path = None
    try:
        state["video_hash"] = video_id
        _progress(5, "获取视频信息...")

        # 0. 浏览器字幕已缓存且非手动触发 → 不自动索引
        if subtitle_cache_exists(video_id) and state.get("status") == "subtitles":
            logger.info(f"[{video_id}] Browser subtitles present, skip auto-indexing")
            return

        # 1. 检查字幕缓存
        if subtitle_cache_exists(video_id):
            subtitles = load_subtitle_cache(video_id)
            logger.info(f"[{video_id}] Subtitle cache hit ({len(subtitles)} lines), skip ASR")
            _progress(30, "字幕已缓存")
        elif state.get("subtitles"):
            subtitles = state["subtitles"]
            logger.info(f"[{video_id}] Subtitles from memory, skip ASR")
            _progress(30, "字幕已缓存")
        else:
            # 2. 优先用B站AI字幕（准确、免费、带时间戳）
            subtitles = extract_subtitles(url)
            if subtitles:
                logger.info(f"[{video_id}] Got B站 subtitles: {len(subtitles)} segments")
                _progress(30, "字幕已获取")
            else:
                # 3. 获取音频 → ASR
                import time
                _progress(10, "获取音频流...")
                # 尝试 DashScope URL 直传（仅对可公开访问的 URL 有效）
                stream_url = get_audio_stream_url(url)
                dashscope_ok = False
                if stream_url and DASHSCOPE_API_KEY:
                    logger.info(f"[{video_id}] Trying DashScope URL direct ASR...")
                    t0 = time.time()
                    try:
                        if "mcdn.bilivideo" in stream_url or "bilivideo.com" in stream_url:
                            raise Exception("B站CDN URL need auth, skip direct")
                        subtitles = await transcribe_via_url(stream_url, DASHSCOPE_API_KEY)
                        dashscope_ok = True
                        logger.info(f"[{video_id}] DashScope ASR done in {time.time()-t0:.1f}s")
                    except Exception as e:
                        logger.info(f"[{video_id}] DashScope direct failed ({str(e)[:50]}), downloading...")

                if not dashscope_ok:
                    _progress(15, "下载音频中...")
                    t0 = time.time()
                    audio_path = download_audio(url, video_id)
                    _progress(30, "语音识别中...")
                    dt = time.time() - t0
                    logger.info(f"[{video_id}] Audio downloaded in {dt:.0f}s, uploading for ASR...")
                    t0 = time.time()
                    subtitles = await transcribe(audio_path)
                    logger.info(f"[{video_id}] ASR done in {time.time()-t0:.0f}s: {len(subtitles)} segments")
                else:
                    _progress(30, "语音识别中...")

                save_subtitle_cache(video_id, subtitles)
                _progress(60, "知识索引中...")

        state["subtitles"] = subtitles

        # 2.5 二次检查：浏览器是否在后台已采集到字幕
        if subtitle_cache_exists(video_id) and state.get("status") == "subtitles":
            logger.info(f"[{video_id}] Browser subtitles arrived during processing, defer indexing")
            return

        # 3. Chunk + Embedding + FAISS
        chunks = chunk_subtitles(subtitles, video_id)
        state["chunks"] = chunks
        _progress(75, "向量化中...")

        chunk_texts = [c["text"] for c in chunks]
        embeddings = await embed_texts(chunk_texts, video_id=video_id)
        _progress(90, "构建索引...")
        build_index(chunks, embeddings, video_id)

        state["status"] = "ready"
        state["progress"] = 100
        state["progress_text"] = "就绪"
        _save_states()
        logger.info(f"[{video_id}] Processing complete! Ready for Q&A.")

    except Exception as e:
        state["status"] = "error"
        state["error"] = str(e)
        _save_states()
        logger.opt(exception=e).error(f"[{video_id}] Processing failed")
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
