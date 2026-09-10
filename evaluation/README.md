# 帧知视频问答评测集 v1

面向**视频学习场景**的问答评测集，用于评测「视频 RAG / 视频问答系统」的能力边界。
覆盖算法、深度学习、AI 应用开发三个领域的教育类视频。

## 数据集构成

| 项 | 数量 |
|---|---|
| 视频 | 8 个（B站，教育类） |
| 问题 | **80 道**（5 类各 16 道） |
| 字幕 | 8 份带时间戳的字幕（ASR 转写，共 301KB） |

### 视频列表

| 领域 | 视频 | BV 号 |
|---|---|---|
| 深度学习 | 李沐·Transformer | BV1EY6FBwE8P (p=192) |
| 深度学习 | 李沐·注意力机制 | BV1EY6FBwE8P (p=181) |
| 深度学习 | LayerNorm 与 BatchNorm 的区别 | BV1fm421j7SQ |
| 算法 | 卡尔·N 皇后 | BV1Rd4y1c7Bq |
| 算法 | 卡尔·环形链表 | BV1if4y1d7ob |
| 算法 | 卡尔·爬楼梯 | BV17h411h7UH |
| AI 应用 | Deep Agents 实战·HITL 人机环路 | BV1v4Mh6eEuE |
| AI 应用 | ClaudeCode 源码精读·agent loop | BV1v9DBBtEym |

## 问题类型（评测集的核心设计）

不只测「字幕能答的问题」，而是**覆盖能力边界**，让不同架构的差距显性化：

| 类型 | 定义 | 考察 | 需要视觉 |
|---|---|---|---|
| `single_hop` | 答案在单段字幕中直接可答 | 基础检索 | ❌ |
| `multi_hop` | 需综合多段字幕 | 跨片段检索+综合 | ❌ |
| `visual_only` | 答案只在画面里，字幕没有 | 视觉理解 | ✅ |
| `joint` | 需字幕 + 画面结合 | 多模态联合 | ✅ |
| `unanswerable` | 视频根本没讲过，应拒答 | 幻觉抑制 | 视情况 |

**示例**：
- `visual_only`：「主讲人身上穿的短袖 T 恤是什么颜色？」
- `unanswerable`：「主讲人佩戴的眼镜是什么品牌？」

这类问题**只看字幕的系统和多模态系统的差距会立刻显现**。

## 数据格式

```
evaluation/
├── dataset.json          # 结构化评测集（80 题，含标准答案/依据/时间段）
├── subtitles/            # 8 份带时间戳的字幕（对应视频的 ASR 转写）
│   └── {video_id}.json
├── build_dataset.py      # 从 markdown 评测集构建 dataset.json
├── eval_retrieval.py     # 检索层评测（Recall@K / MRR）
├── eval_generation.py    # 生成层评测（忠实度/相关性/引用/拒答）
├── judge.py              # LLM-as-judge 公共模块
├── retry_failed.py       # 重试裁判失败的记录
└── results_*.json        # 评测原始结果
```

`dataset.json` 单题结构：

```json
{
  "id": "b8739307cc61_q1",
  "type": "single_hop",
  "question": "Transformer跟使用注意力的seq2seq模型最大的区别是什么？",
  "reference_answer": "Transformer是纯基于注意力的架构...",
  "evidence": "跟使用注意力的seq2seq不同，Transformer是纯基于注意力",
  "time_start": 91,
  "time_end": 239
}
```

## 评测结果（v1，2026-09-11）

在「纯字幕 RAG」（检索 Top5 → LLM 生成）上的基线：

| 问题类型 | 检索 R@5 | 生成相关性 | 拒答率 |
|---|---|---|---|
| single_hop | 0.750 | **0.941** | — |
| multi_hop | 0.812 ⚠️ | 0.762 | — |
| joint | 0.375 | 0.681 | — |
| visual_only | 0.312 ⚠️ | **0.219** | — |
| unanswerable | — | — | **1.000** |

**推荐关注的两条曲线**：

1. **相关性随视觉依赖度递减**：`0.941 → 0.762 → 0.681 → 0.219`
   问题越依赖画面，纯字幕 RAG 表现越差——**这是视觉能力价值的量化证据**。

2. **拒答率 1.000**：16 道无依据题全部正确拒答，未出现编造。

## 已知局限（使用时请注意）

| 项 | 说明 |
|---|---|
| **字幕为 ASR 转写** | 含错字，与视频官方字幕存在差异，会系统性拉低检索指标 |
| **multi_hop 检索指标虚高** | 该类答案区间平均宽 401 秒（≈8 个 chunk），「时间区间有交集」判定近乎必然命中，**该行数字不可用作检索能力** |
| **visual_only 存在假命中** | 答案本不在字幕中，命中来自时间巧合 |
| **judge 与被评模型同源** | LLM-as-judge 存在自评偏差，**绝对值仅供参考，趋势更可信** |

## 复现方式

```bash
python evaluation/build_dataset.py      # 构建 dataset.json
python evaluation/eval_retrieval.py     # 检索层（约 3s）
python evaluation/eval_generation.py    # 生成层（约 9min，消耗 token）
python evaluation/retry_failed.py       # 重判失败记录（如需要）
```

## 数据来源与使用说明

- 视频来自 B站公开教育内容，字幕为**自动语音转写**，仅供评测研究使用
- 版权归原视频作者所有；请勿用于商业用途
- 评测集本身（问题/标准答案/标注）由本项目构建
