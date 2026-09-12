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
            # time_start / time_end 兼容两种形式：
            #   - 单值：单一答案区间（single_hop / visual_only / joint / unanswerable）
            #   - 并列列表：多个答案片段，按索引一一对应（multi_hop）
            ts, te = q.get("time_start", 0), q.get("time_end", 0)
            _ts = ts if isinstance(ts, list) else [ts]
            _te = te if isinstance(te, list) else [te]
            if len(_ts) != len(_te):
                problems.append(f"{e['name']} 第{n}题 start/end 长度不一致")
            # unanswerable 题**本来就没有答案区间**，约定用 -1 作哨兵
            # （评测侧靠"跳过负数区间"来排除它们参与检索指标）。
            # 所以负数与区间检查都要先排除这一类，否则全是误报。
            if t != "unanswerable":
                if any(v < 0 for v in _ts) or any(v < 0 for v in _te):
                    problems.append(f"{e['name']} 第{n}题时间戳为负: {ts}~{te}")
                # 起止倒挂：脏数据会安静进数据集，之后所有指标都建在它上面。
                # 本项目已经吃过一次「标注超出视频时长」的亏（见踩坑记录 #13），
                # 能自动查的就别靠人看。
                for s, en in zip(_ts, _te):
                    if en <= s:
                        problems.append(f"{e['name']} 第{n}题区间起止倒挂或为空: {s}~{en}")
            if not str(q.get("question", "")).strip():
                problems.append(f"{e['name']} 第{n}题问题为空")
            if not str(q.get("reference_answer", "")).strip():
                problems.append(f"{e['name']} 第{n}题标准答案为空")
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
            "subtitle_source": "bilibili_ai_subtitle",
            "note": "字幕取自 B站 AI 字幕（平台机器转写），仍有识别错误（如 numpy→南派），会影响检索指标",
            "time_range_note": "multi_hop 的 time_start/time_end 为并列列表，按索引一一对应多个答案片段",
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
