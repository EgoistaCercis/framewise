"""
帧知 - V5 评测：与视频多模态模型的对比

把画面**直接交给多模态模型**（而不像 V3/V4 那样先经 VL 转成文字描述），
回答「一体化多模态 vs 我们的两段式链路，架构上差多少」。

三个变体（`--mode`）：

  frame   V5-A：字幕 + **暂停点 1 帧**
          · 信息量与 V3 **完全相同** → 差异纯粹来自「画面直给 vs 经 VL 转述」
          · 这是最有价值的对照：能直接量出 VL 转述这一步损失了多少
  frames  V5-B1：字幕 + **我们自己均匀抽的 N 帧**（`--fps`）
          · 采样策略由我们控制，与 video 模式形成「自采样 vs 原生采样」的对照
  video   V5-B2：字幕 + **整个视频文件**（`video_url` 直传，模型自己采样）
          · 这才是真正意义上的「原生视频理解」
          · 实测模型按 **260 tok/秒** 采样（≈0.3fps），20 分钟视频约 31 万 tokens

## 裁判口径（★ 重要，决定结论是否成立）

裁判材料 = **字幕（文本）+ 模型实际看到的画面**，由 `--judge-context` 控制：

  img（默认）     画面**直接给裁判看**（`judge_images` 通道，GLM-5.3-Flash 支持图片）
                  · 裁判不再依赖有损的 VL 转述
                  · **顺带解决踩坑 #19「判官看不到图片」** —— 它有资格评判画面内容了
  vl              用 VL 把画面转成文字描述再给裁判 —— 与 V3 的口径一致，但多一笔 VL 开销
  transcript      只给字幕 —— 与 V3 **不可直接比较**，仅作参考

为什么不再默认 vl：V3 的裁判材料带的是**画面描述**，而裁判真正需要的是**画面本身**。
直接给图既更准（没有转述损失）、又更便宜（省掉 VL 描述那笔输出成本，
实测 frames 模式下 VL 开销 ¥1.22，而帧作固定前缀给裁判只要 ¥0.78）。

⚠️ **仍存在的口径差异**：V3/V4 的裁判材料是「字幕 + VL 描述」，V5 是「字幕 + 原图」——
**两者不可直接混排**。要严格可比，应当用同一口径重判 V3/V4（即也给它们的裁判喂图）。
本脚本未做这件事，报告里需注明。

## 与 V1~V4 的可比性

- **裁判完全同源**：`judge.judge_record` + `JUDGE_*` 模型，绝不换尺子
- ⚠️ **V5 的 VL 描述基于缩图（默认 720p），V3 的基于原图**。缩图可能让描述遗漏
  小字/细线，导致两边裁判材料存在**系统性微差**。量级预计很小，但排查 `visual_only`
  分数差时要记得这个变量。可用 `--frame-width` 调大以缩小该差异（代价是上传体积）。
- **但 V5 与 V3 的口径仍有差异**：V3 是**顺序**问答（刻意命中前缀缓存），
  V5 是**并发**（多模态请求慢，顺序不现实）。并发下首题尚未落缓存、其余已首发，
  因此 `cache_hit_rate` 会低于 V3。报告成本时须注明。

## 用法

    # 冒烟：先一个视频、少量帧
    python evaluation/eval_v5_native_vl.py --mode frame  --videos 1 --limit 3
    python evaluation/eval_v5_native_vl.py --mode video  --videos 1 --limit 3

    # 全量
    python evaluation/eval_v5_native_vl.py --mode frame
    python evaluation/eval_v5_native_vl.py --mode video
"""
import argparse
import asyncio
import base64
import json
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import judge  # noqa: E402
from eval_full_context import format_transcript, _fmt  # noqa: E402
from media_utils import extract_frame_for, local_video_path  # noqa: E402

DATASET = os.path.join(BASE, "evaluation", "dataset.json")
SUBDIR = os.path.join(BASE, "evaluation", "subtitles")
CACHE_DIR = os.path.join(BASE, "data", "v5_assets")

FRAME_CONCURRENCY = 3
JUDGE_CONCURRENCY = int(os.getenv("EVAL_JUDGE_CONCURRENCY", "40"))
VIDEO_CONCURRENCY = int(os.getenv("EVAL_VIDEO_CONCURRENCY", "4"))
# 全局并发上限：V5 的延迟与缓存命中率都是对比指标，
# 无节制并发既会撞限流、也会让这两个指标失真
GLOBAL_CONCURRENCY = int(os.getenv("EVAL_V5_GLOBAL_CONCURRENCY", "8"))
MAX_FRAMES_PER_VIDEO = int(os.getenv("EVAL_V5_MAX_FRAMES", "64"))

# 实测：MULTIMODAL 模型按 260 tok/秒 采样视频（30/60/120s 三档线性吻合）
VIDEO_TOK_PER_SEC = 260


def _first_ts(case: dict) -> float:
    """取题目答案区间起点作为「暂停点」（与 V3/V4 口径一致）"""
    ts = case["time_start"]
    if isinstance(ts, list):
        ts = ts[0]
    return max(0.0, float(ts))


def video_duration(video_id: str, state: dict = None) -> float:
    """视频**真实**时长（ffmpeg）。

    不能用字幕末尾代替：实测「卡尔爬楼梯」字幕到 941s 而视频有 953s ——
    差 12 秒会让「整段视频」这类标注被误判成越界。
    """
    import re
    from backend.config import FFMPEG_PATH
    src = local_video_path(video_id) or (state or {}).get("video_path")
    if not src or not os.path.exists(src):
        raise FileNotFoundError(f"找不到本地视频，无法确定时长：{video_id}")
    r = subprocess.run([FFMPEG_PATH, "-i", src], capture_output=True)
    m = re.search(r"Duration: (\d+):(\d+):(\d+)\.(\d+)",
                  r.stderr.decode("utf-8", "replace"))
    if not m:
        raise RuntimeError(f"读不出视频时长：{src}")
    h, mi, sec, cs = (int(x) for x in m.groups())
    return h * 3600 + mi * 60 + sec + cs / 100


async def _grab(video_id: str, state: dict, ts: float,
                sem: asyncio.Semaphore) -> tuple:
    """抽帧，返回 (frame_path, err)。路径而非 base64 —— 后面补 VL 描述还要用"""
    async with sem:
        return await extract_frame_for(video_id, state, ts)


def _downscale(frame_path: str, width: int) -> str:
    """把帧缩到指定宽度（缓存到 CACHE_DIR）。

    为什么不改 vision_service.extract_frame：它的缓存按 (video, ts) 索引，
    在里面加缩放会**污染 V3/V4 已缓存的帧**、破坏历史可比性。所以在评测侧另存一份。
    """
    from backend.config import FFMPEG_PATH
    os.makedirs(CACHE_DIR, exist_ok=True)
    out = os.path.join(CACHE_DIR, f"{os.path.basename(os.path.dirname(frame_path))}"
                                  f"_{os.path.basename(frame_path)}__w{width}.jpg")
    if os.path.exists(out):
        return out
    r = subprocess.run(
        [FFMPEG_PATH, "-y", "-i", frame_path, "-vf", f"scale={width}:-2",
         "-q:v", "4", "-loglevel", "error", out],
        capture_output=True)
    if r.returncode != 0 or not os.path.exists(out):
        raise RuntimeError(f"缩图失败：{r.stderr.decode('utf-8', 'replace')[:120]}")
    return out


def _b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


async def _downscale_async(frame_path: str, width: int) -> str:
    """缩图的异步包装。

    ⚠️ ffmpeg 是**同步子进程**，直接调会冻住整个事件循环 ——
    vision_service.extract_frame 为同一个原因专门包了 `asyncio.to_thread`
    （其 docstring 写明「直接 await 会阻塞事件循环、把并发退化成串行」）。
    """
    return await asyncio.to_thread(_downscale, frame_path, width)


async def describe_frames(paths: list, video_id: str,
                          sem: asyncio.Semaphore) -> list:
    """给一批帧补 VL 描述（用于裁判材料，见模块 docstring 的「裁判口径」）"""
    from backend.services.media.vision_service import analyze_frame

    async def one(p):
        async with sem:
            try:
                return await analyze_frame(p, video_id=video_id)
            except Exception as e:
                return f"[描述失败] {type(e).__name__}: {str(e)[:80]}"

    return await asyncio.gather(*[one(p) for p in paths])


def prepare_video(video_id: str, state: dict, width: int) -> str:
    """准备可直传的视频文件（缩到指定宽度、低码率，缓存复用）。

    实测：10 秒 480p/crf32 只要 39KB（讲座画面静态、压缩率极高），
    20 分钟视频约 3~5MB —— 比「抽 240 帧」的 36MB 小一个数量级。
    """
    src = local_video_path(video_id) or state.get("video_path")
    if not src or not os.path.exists(src):
        raise FileNotFoundError(f"找不到本地视频文件，无法直传：{video_id}")
    from backend.config import FFMPEG_PATH
    os.makedirs(CACHE_DIR, exist_ok=True)
    out = os.path.join(CACHE_DIR, f"{video_id}__w{width}.mp4")
    if os.path.exists(out):
        return out
    r = subprocess.run(
        [FFMPEG_PATH, "-y", "-i", src, "-vf", f"scale={width}:-2",
         "-c:v", "libx264", "-crf", "30", "-preset", "veryfast", "-an",
         "-loglevel", "error", out],
        capture_output=True)
    if r.returncode != 0 or not os.path.exists(out):
        raise RuntimeError(f"视频转码失败：{r.stderr.decode('utf-8', 'replace')[:150]}")
    return out


async def build_assets(mode: str, video_id: str, state: dict,
                       args, sem: asyncio.Semaphore) -> dict:
    """按模式准备素材，返回 dict：

    frame   {"per_question": True}                       —— 逐题现抽
    frames  {"paths": [...], "b64": [...], "desc": [...], "meta": {...}}
    video   {"video_path": ..., "b64": ..., "desc": [...], "meta": {...}}
    """
    dur = video_duration(video_id, state)

    if mode == "frame":
        return {"per_question": True, "duration": round(dur, 1)}

    if mode == "frames":
        n = min(int(dur * args.fps), MAX_FRAMES_PER_VIDEO)
        step = dur / max(n, 1)
        ts_list = [round(i * step, 1) for i in range(n)]
        got = await asyncio.gather(*[_grab(video_id, state, t, sem) for t in ts_list])
        paths = [p for p, e in got if p]
        # 缩图：原分辨率 1080p 单帧约 114KB，N 帧 base64 后能到几十 MB，
        # 既超多数模型的单请求图片上限、也把上传时间推高一个量级
        # 并行 + 不阻塞事件循环（ffmpeg 是同步子进程）
        small = await asyncio.gather(
            *[_downscale_async(p, args.frame_width) for p in paths])
        desc = await describe_frames(small, video_id, sem) if args.judge_context == "vl" else []
        judge_imgs = small if args.judge_context == "img" else []
        meta = {"requested": len(ts_list), "ok": len(paths), "duration": round(dur, 1),
                "failed_ts": [t for (p, e), t in zip(got, ts_list) if not p][:8]}
        return {"paths": small, "b64": [_b64(p) for p in small], "desc": desc,
                "judge_imgs": [_b64(p) for p in judge_imgs], "meta": meta}

    # video
    # 转码整段视频可能 10~60 秒，必须丢线程池，否则其余视频的抽帧/问答全停摆
    vpath = await asyncio.to_thread(prepare_video, video_id, state, args.video_width)
    # 裁判材料：按模型自己的采样率（260 tok/s ≈ 0.3fps）补描述，才贴近它「看到」的东西
    desc = []
    judge_imgs = []
    if args.judge_context in ("vl", "img"):
        n = min(int(dur * args.judge_fps), MAX_FRAMES_PER_VIDEO)
        step = dur / max(n, 1)
        got = await asyncio.gather(
            *[_grab(video_id, state, round(i * step, 1), sem) for i in range(n)])
        paths = await asyncio.gather(
            *[_downscale_async(p, args.frame_width) for p, e in got if p])
        judge_imgs = [_b64(p) for p in paths]
        if args.judge_context == "vl":
            desc = await describe_frames(paths, video_id, sem)
    size_mb = os.path.getsize(vpath) / 1024 / 1024
    meta = {"duration": round(dur, 1), "video_mb": round(size_mb, 2),
            "est_tokens": int(dur * VIDEO_TOK_PER_SEC),
            "desc_frames": len(desc),
            # 描述帧数受 MAX_FRAMES_PER_VIDEO 封顶，长视频的实际密度会低于 --judge-fps
            "desc_fps_effective": round(len(desc) / dur, 4) if dur else 0}
    return {"video_path": vpath, "b64": _b64(vpath), "desc": desc,
            "judge_imgs": judge_imgs, "meta": meta}


async def ask_one(video_id: str, state: dict, case: dict, transcript: str,
                  mode: str, assets: dict, args, sem: asyncio.Semaphore) -> dict:
    """问一题"""
    from backend.services.llm.gateway import chat
    from backend.prompts import SYSTEM_PROMPT
    from backend.config import LLM_MAX_TOKENS

    async with sem:
        frame_err = None
        descs = []
        judge_imgs = []

        if mode == "frame":
            path, frame_err = await _grab(video_id, state, _first_ts(case), _frame_sem)
            msgs = [{"role": "user", "content": f"<transcript>\n{transcript}\n</transcript>"}]
            if path:
                small = await _downscale_async(path, args.frame_width)
                msgs.append({"role": "user",
                             "content": f"这是用户提问时视频暂停在 {_fmt(_first_ts(case))} 的那一帧。",
                             "images": [_b64(small)]})
                if args.judge_context == "vl":
                    descs = await describe_frames([small], video_id, _frame_sem)
            n_frames = 1 if path else 0
            judge_imgs = [_b64(small)] if path else []

        elif mode == "frames":
            msgs = [{"role": "user", "content": f"<transcript>\n{transcript}\n</transcript>"}]
            b64s = assets["b64"]
            if b64s:
                msgs.append({
                    "role": "user",
                    "content": f"以下按时间顺序均匀抽取的 {len(b64s)} 帧，覆盖全片。",
                    "images": b64s,
                })
            n_frames = len(b64s)
            judge_imgs = b64s

        else:  # video
            msgs = [{"role": "user", "content": f"<transcript>\n{transcript}\n</transcript>"},
                    {"role": "user", "content": "以下是该视频的完整内容。",
                     "video": assets["b64"]}]
            n_frames = -1                      # -1 = 整段视频（非帧）
            judge_imgs = assets["judge_imgs"]

        msgs.append({"role": "user", "content": case["question"]})

        # ★ 帧失败直接短路：原实现在 chat **之后**才判 frame_err，
        #   结果是「没有画面的题」仍会完整调一次（且因不带 images 而路由到
        #   默认 chat 模型）并计费，然后才被剔除 —— 纯浪费，也违背「不静默降级」的本意。
        if frame_err and mode == "frame":
            return {
                "id": case["id"], "type": case["type"], "question": case["question"],
                "reference_answer": case["reference_answer"],
                "answer": f"[未发起] 抽帧失败，未提供画面：{frame_err}",
                "context": transcript, "frames": 0, "frame_error": frame_err,
                "gen_error": f"抽帧失败，未提供画面：{frame_err}",
                "latency_s": 0.0, "prompt_tokens": 0, "completion_tokens": 0,
                "cached_tokens": 0, "judge_error": "未发起（无画面）",
                "_ts": case["time_start"], "_te": case["time_end"],
            }

        t0 = time.time()
        try:
            # ★ 全局并发上限罩在**请求**上，不是「在跑几个视频」——
            #   8 个视频 / 上限 8 时，罩 run_video 等于什么都没限制，
            #   实际打向端点的并发仍是 VIDEO_CONCURRENCY × 视频数。
            async with _global_sem:
                ans, usage = await chat(messages=msgs, system_prompt=SYSTEM_PROMPT,
                                        max_tokens=LLM_MAX_TOKENS, video_id=video_id)
            gen_err = None
        except Exception as e:
            ans, usage = f"[异常] {type(e).__name__}: {str(e)[:150]}", {}
            gen_err = f"{type(e).__name__}: {str(e)[:150]}"
        lat = round(time.time() - t0, 2)

        # 裁判材料 = 字幕 + 模型实际看到的画面的 VL 描述（见模块 docstring）
        ctx = transcript
        if args.judge_context == "vl" and descs:
            ctx += "\n\n<frames>\n" + "\n".join(
                f'<frame idx="{i}">\n{d}\n</frame>' for i, d in enumerate(descs, 1)) + "\n</frames>"
        elif args.judge_context == "vl" and n_frames == 0:
            # 注：这条分支在当前控制流下**实际不会生效** ——
            #   frames 模式 ok==0 会在 run_video 里直接 raise；
            #   frame 模式帧失败已在上面短路（gen_error + 不进统计）。
            #   留着是为将来放宽「ok==0 就 raise」时仍有兜底。
            ctx += '\n\n<frame error="true">画面获取失败，模型未看到任何画面</frame>'

        return {
            "id": case["id"], "type": case["type"], "question": case["question"],
            "reference_answer": case["reference_answer"], "answer": ans,
            "context": ctx,
            "frames": n_frames, "frame_error": frame_err,
            "judge_images": judge_imgs if args.judge_context == "img" else [],
            "gen_error": gen_err, "latency_s": lat,
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "cached_tokens": usage.get("cached_tokens", 0),
            "_ts": case["time_start"], "_te": case["time_end"],
        }


async def judge_one(rec: dict, sem: asyncio.Semaphore) -> dict:
    """裁判单条：复用统一入口，口径与 V1~V4 完全一致"""
    async with sem:
        try:
            await judge.judge_record(rec)
            rec["judge_error"] = None
        except Exception as e:
            rec["judge_error"] = str(e)[:200]
        return rec


# 这两个信号量都**仅作脚本入口使用**：由 main() 赋值，
# import 本模块后直接调用 ask_one / run_video 会拿到 None。
# （若将来要把它当库用，应改成参数传入或 ContextVar。）
#
# ⚠️ 二者都必须在**模块级**声明：`ask_one` 里用 `async with _global_sem`
# 引用的是全局名，只写 `global _global_sem` + 在 main 里赋值是不够的 ——
# 漏了这里的声明就会在读取时报 NameError。
_frame_sem: asyncio.Semaphore = None
_global_sem: asyncio.Semaphore = None


async def run_video(video: dict, args) -> list:
    from backend.main import video_states
    from loguru import logger

    vid, name = video["video_id"], video["name"]
    tag = f"[{name[:12]}]"
    state = video_states.get(vid, {})
    transcript = format_transcript(vid)
    cases = video["cases"][: args.limit] if args.limit else video["cases"]

    assets = await build_assets(args.mode, vid, state, args, _frame_sem)
    if args.mode != "frame":
        m = assets["meta"]
        print(f"{tag} 素材就绪：{m}")
        logger.info(f"--- {name}：mode={args.mode} meta={m} ---")
        if args.mode == "frames" and m["ok"] == 0:
            raise RuntimeError(f"{name}：一帧都没抽到，放弃该视频（不静默降级）")
        if args.mode == "video" and not assets["b64"]:
            raise RuntimeError(f"{name}：视频素材为空，放弃该视频")

    sem_q = asyncio.Semaphore(VIDEO_CONCURRENCY)

    async def safe(i, c):
        """题目级隔离：**一题失败不能让整个视频丢掉**。
        原实现下 gather 一旦抛异常，该视频的 10 题全部作废
        —— 实测因为一道越界标注就丢了 2 个视频（占样本 20%）。"""
        try:
            return await ask_one(vid, state, c, transcript, args.mode, assets, args, sem_q)
        except Exception as e:
            logger.warning(f"{c['id']} 失败（不影响同视频其他题）：{type(e).__name__}: {e}")
            return {"id": c["id"], "type": c["type"], "question": c["question"],
                    "reference_answer": c["reference_answer"],
                    "answer": f"[题目级异常] {type(e).__name__}: {str(e)[:150]}",
                    "context": transcript, "frames": 0, "frame_error": str(e)[:150],
                    "gen_error": f"{type(e).__name__}: {str(e)[:150]}", "latency_s": 0.0,
                    "prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0,
                    "judge_images": [], "judge_error": "未发起（题目异常）",
                    "_ts": c["time_start"], "_te": c["time_end"]}

    recs = await asyncio.gather(*[safe(i, c) for i, c in enumerate(cases)])

    for i, r in enumerate(recs, 1):
        if r["gen_error"]:
            flag = "失败"
        elif r["frames"] == -1:
            flag = "整视频"
        elif r["frames"]:
            flag = f"帧{r['frames']}"
        else:
            flag = "无画面"
        print(f"{tag} [{i:2}/{len(cases)}] {flag:>7} in={r['prompt_tokens']:>7} "
              f"缓存{r['cached_tokens']:>7} {r['latency_s']:>6.1f}s {r['question'][:22]}")
        logger.info(f"[{i}/{len(cases)}] {r['id']} [{r['type']}] frames={r['frames']} "
                    f"input={r['prompt_tokens']}(缓存{r['cached_tokens']}) 延迟={r['latency_s']}s"
                    + (f" 生成失败:{r['gen_error']}" if r['gen_error'] else ""))
    # meta 一并返回：原来只 print/logger，结果文件里查不到「哪个视频缺了哪段画面」
    return {"video_id": vid, "video_name": name,
            "meta": assets.get("meta") or {"duration": assets.get("duration")},
            "records": recs}


async def main():
    global _frame_sem, _global_sem
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["frame", "frames", "video"], default="frame")
    ap.add_argument("--fps", type=float, default=0.2, help="frames 模式的抽帧密度")
    ap.add_argument("--judge-context", choices=["img", "vl", "transcript"], default="img",
                    help="vl=补 VL 描述（与 V3 口径一致，默认）；transcript=只给字幕（不可与 V3 直接比）")
    ap.add_argument("--judge-fps", type=float, default=0.1,
                    help="video 模式给裁判补描述时的抽帧密度（模型自身约 0.3fps）")
    ap.add_argument("--frame-width", type=int, default=720, help="送模型的帧宽度（控制上传体积）")
    ap.add_argument("--video-width", type=int, default=640, help="video 模式的视频宽度")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--videos", type=int, default=0, help="只跑前 N 个视频（冒烟用）")
    args = ap.parse_args()

    from backend.config import MULTIMODAL_MODEL, MULTIMODAL_ENDPOINT, MULTIMODAL_API_KEY
    from eval_logger import setup_eval_log
    from loguru import logger

    if not MULTIMODAL_API_KEY or not MULTIMODAL_MODEL:
        raise SystemExit("MULTIMODAL_* 未配置（.env），无法评测。")
    try:
        ep_host = MULTIMODAL_ENDPOINT.split("/")[2]
    except IndexError:
        ep_host = f"(无法解析: {MULTIMODAL_ENDPOINT[:40]})"

    dataset = json.load(open(DATASET, encoding="utf-8"))
    out_path = os.path.join(BASE, "evaluation", f"results_v5_{args.mode}.json")
    log_path = setup_eval_log(f"eval_v5_{args.mode}")
    logger.info(f"=== V5-{args.mode} 评测开始 ===")
    logger.info(f"模型: {MULTIMODAL_MODEL} @ {ep_host}")
    logger.info(f"裁判口径: {args.judge_context}  fps={args.fps} 帧宽={args.frame_width}")

    _frame_sem = asyncio.Semaphore(FRAME_CONCURRENCY)
    _global_sem = asyncio.Semaphore(GLOBAL_CONCURRENCY)
    videos = dataset["videos"][: args.videos] if args.videos else dataset["videos"]

    print(f"模式={args.mode} 模型={MULTIMODAL_MODEL} 视频数={len(videos)} "
          f"裁判口径={args.judge_context}")

    async def guarded(v):
        return await run_video(v, args)

    t0 = time.time()
    raw = await asyncio.gather(*[guarded(v) for v in videos], return_exceptions=True)
    records, failed_videos, video_metas = [], [], []
    for v, r in zip(videos, raw):
        if isinstance(r, Exception):
            failed_videos.append(f"{v['name']}: {type(r).__name__}: {r}")
            logger.error(f"视频级失败 {v['name']}: {type(r).__name__}: {r}")
        else:
            video_metas.append(r)
            records.extend(r["records"])
    if failed_videos:
        print(f"\n[警告] {len(failed_videos)} 个视频整体失败（其余继续）：")
        for f in failed_videos:
            print(f"    {f}")
    if not records:
        raise SystemExit("没有任何记录产出，终止。")

    print(f"\n裁判中...（{len(records)} 条）")
    sem = asyncio.Semaphore(JUDGE_CONCURRENCY)
    await asyncio.gather(*[judge_one(r, sem) for r in records])

    # ── 汇总（gen_error / judge_error 一律剔除）──
    from collections import defaultdict
    good = [r for r in records if not r.get("judge_error") and not r.get("gen_error")]
    by = defaultdict(list)
    for r in good:
        by[r["type"]].append(r)

    print("\n" + "=" * 88)
    print(f"{'类型':<14}{'n':>4}{'忠实度':>9}{'相关性':>9}{'引用准确':>10}{'拒答率':>9}{'平均延迟':>10}")
    print("-" * 88)
    summary = {}
    for t in ("single_hop", "multi_hop", "joint", "visual_only", "unanswerable"):
        rs = by.get(t, [])
        if not rs:
            continue
        n = len(rs); lat = sum(r["latency_s"] for r in rs) / n
        if t == "unanswerable":
            row = {"n": n, "refusal_rate": round(sum(1 for r in rs if r.get("refused")) / n, 3),
                   "avg_latency_s": round(lat, 1)}
            print(f"{t:<14}{n:>4}{'—':>9}{'—':>9}{'—':>10}{row['refusal_rate']:>9.3f}{lat:>10.1f}")
        else:
            f = sum(r["faithfulness"] for r in rs) / n
            rel = sum(r["relevancy"] for r in rs) / n
            acc = [r["citation_accurate"] for r in rs if r.get("has_citation")]
            ar = (sum(1 for a in acc if a) / len(acc)) if acc else None
            row = {"n": n, "faithfulness": round(f, 3), "relevancy": round(rel, 3),
                   "citation_accuracy": round(ar, 3) if ar is not None else None,
                   "avg_latency_s": round(lat, 1)}
            ad = "—" if ar is None else f"{ar:>10.3f}"
            print(f"{t:<14}{n:>4}{f:>9.3f}{rel:>9.3f}{ad}{'—':>9}{lat:>10.1f}")
        summary[t] = row

    # 成本块：与 summary 同口径，只统计有效记录
    tot_in = sum(r["prompt_tokens"] for r in good)
    tot_cached = sum(r["cached_tokens"] for r in good)
    tot_out = sum(r["completion_tokens"] for r in good)
    cost = {
        "total_prompt_tokens": tot_in, "total_cached_tokens": tot_cached,
        "total_completion_tokens": tot_out,
        "avg_latency_s": round(sum(r["latency_s"] for r in good) / len(good), 1) if good else 0.0,
        "cache_hit_rate": round(tot_cached / tot_in, 3) if tot_in else 0,
        "gen_failures": len(records) - len(good) - sum(1 for r in records if r.get("judge_error")),
        "judge_failures": sum(1 for r in records if r.get("judge_error")),
        "failed_videos": failed_videos,
    }
    print("-" * 88)
    print(f"总 input {tot_in:,}（缓存 {tot_cached:,}，命中率 {cost['cache_hit_rate']:.1%}）"
          f" | 总 output {tot_out:,} | 平均延迟 {cost['avg_latency_s']}s")
    if cost["gen_failures"] or cost["judge_failures"]:
        print(f"[警告] 生成失败 {cost['gen_failures']} / 裁判失败 {cost['judge_failures']}（已剔除）")

    # 原子写：直写时崩溃会损坏评测产物（与 retry_failed 同一家规）
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"summary": summary, "cost": cost, "mode": args.mode, "fps": args.fps,
                   "judge_context": args.judge_context, "model": MULTIMODAL_MODEL,
                   "video_metas": video_metas,
                   "records": [{k: v for k, v in r.items() if k != "context"} for r in records]},
                  fh, ensure_ascii=False, indent=2)
    os.replace(tmp, out_path)
    print(f"\n耗时 {(time.time()-t0)/60:.1f} 分钟，已存 {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
