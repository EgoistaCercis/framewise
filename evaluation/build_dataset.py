"""
帧知 - 评测集构建脚本

把「项目文档/评测/项目评测视频领域*.md」里的 markdown 评测集，
解析成结构化 dataset.json，并建立 BV号+分P → 项目 video_id 的映射。

用法：
    python evaluation/build_dataset.py
"""
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

SRC_MD = os.path.join(BASE, "项目文档", "评测", "项目评测视频领域20260908.md")
STATES = os.path.join(BASE, "data", "video_states.json")
OUT = os.path.join(BASE, "evaluation", "dataset.json")

VALID_TYPES = {"single_hop", "multi_hop", "visual_only", "joint", "unanswerable"}


def load_video_index() -> dict:
    """加载 video_states.json，建立 (BV号, 分P) → video_id 映射"""
    if not os.path.exists(STATES):
        raise FileNotFoundError(f"找不到 {STATES}，请先让视频建立索引")
    states = json.load(open(STATES, encoding="utf-8"))
    idx = {}
    for vid, s in states.items():
        url = str(s.get("url") or "")
        m = re.search(r"(BV[0-9A-Za-z]{10})", url)
        if not m:
            continue
        p = re.search(r"[?&]p=(\d+)", url)
        idx[(m.group(1), p.group(1) if p else "")] = (vid, s.get("status"))
    return idx


def parse_markdown(path: str) -> list:
    """解析 markdown：每个「名称：URL」行后面跟一个 JSON 块"""
    lines = open(path, encoding="utf-8").read().split("\n")
    entries, i = [], 0
    while i < len(lines):
        m = re.search(r"(https://www\.bilibili\.com/video/BV[0-9A-Za-z]{10}[^\s]*)", lines[i])
        if not m:
            i += 1
            continue
        url = m.group(1)
        name = re.sub(r"[：:]\s*$", "", lines[i][: m.start()].strip()).strip()
        name = name.replace("​", "").strip()  # 去零宽字符

        # 找下一个 ``` 块
        j = i + 1
        while j < len(lines) and not lines[j].strip().startswith("```"):
            j += 1
        j += 1
        buf = []
        while j < len(lines) and not lines[j].strip().startswith("```"):
            buf.append(lines[j])
            j += 1
        try:
            entries.append({"name": name, "url": url, "cases": json.loads("\n".join(buf))})
        except json.JSONDecodeError as e:
            print(f"  [解析失败] {name}: {e}")
        i = j + 1
    return entries


def main():
    idx = load_video_index()
    raw = parse_markdown(SRC_MD)

    videos, problems = [], []
    type_count = {}

    for e in raw:
        m = re.search(r"(BV[0-9A-Za-z]{10})", e["url"])
        p = re.search(r"[?&]p=(\d+)", e["url"])
        key = (m.group(1), p.group(1) if p else "")
        hit = idx.get(key)
        if not hit:
            problems.append(f"视频未索引: {e['name']} {key}")
            continue
        video_id, status = hit
        if status != "ready":
            problems.append(f"视频状态非 ready: {e['name']} ({status})")

        cases = []
        for n, q in enumerate(e["cases"], 1):
            t = q.get("type")
            if t not in VALID_TYPES:
                problems.append(f"{e['name']} 第{n}题类型非法: {t}")
            ts, te = q.get("time_start", 0), q.get("time_end", 0)
            if ts < 0 or te < 0:
                problems.append(f"{e['name']} 第{n}题时间戳为负: {ts}~{te}")
            type_count[t] = type_count.get(t, 0) + 1
            cases.append({
                "id": f"{video_id}_q{n}",
                "type": t,
                "question": q.get("question", ""),
                "reference_answer": q.get("reference_answer", ""),
                "evidence": q.get("evidence", ""),
                "time_start": ts,
                "time_end": te,
            })

        videos.append({
            "video_id": video_id,
            "bv": key[0],
            "page": key[1],
            "name": e["name"],
            "url": e["url"],
            "cases": cases,
        })

    out = {
        "meta": {
            "source": os.path.basename(SRC_MD),
            "videos": len(videos),
            "questions": sum(len(v["cases"]) for v in videos),
            "type_distribution": type_count,
            "subtitle_source": "asr",
            "note": "字幕由 ASR 生成，与评测集 evidence 引用的官方字幕存在差异，会影响检索指标",
        },
        "videos": videos,
    }
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print(f"已写出: {OUT}")
    print(f"  视频 {out['meta']['videos']} 个，题目 {out['meta']['questions']} 道")
    print(f"  类型分布: {type_count}")
    if problems:
        print(f"  [警告] {len(problems)} 条:")
        for p in problems[:10]:
            print(f"    - {p}")


if __name__ == "__main__":
    main()
