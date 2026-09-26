# 帧知 (FrameWise) 🎬

视频学习 AI 助手（浏览器插件）。上传视频或粘贴 B站/YouTube 链接，AI 自动建立知识索引；
B站视频页点悬浮按钮即可对视频提问，**AI 结合字幕与按需画面分析给出带时间戳的回答，不打断学习流**。

## ⚡ 立刻试用（推荐 · 不用部署、不用配任何 API Key）

**装个插件就能用，后端已经跑好了。**

1. 从 [Releases](https://github.com/EgoistaCercis/framewise/releases) 下载 `framewise-extension.zip`；
   解压后在 `chrome://extensions/` 打开**开发者模式** → 「加载已解压的扩展程序」→ 选解压出来的文件夹
2. 点插件右上角 ⚙️ **设置**，**后端地址**填 `https://fw.framewise2026.icu`
3. **访问密钥**：在 B站私信我，或在 [Issues](https://github.com/EgoistaCercis/framewise/issues) 里留言拿一个
4. 打开任意 B站视频（有字幕的）→ 点右侧 🎬 → 直接提问

> 用的是**我部署的服务器**：你出插件，算力我出。额度有限，所以是**一人一个密钥 + 每日配额**，先到先得 🙂
>
> 想完全自己掌控（数据不出自己的机器），见下面的[快速开始](#-快速开始)自行部署 —— 也只需要填三个 API Key。

## ✨ 特性

- 📝 **内容问答** — 带时间戳引用，点击跳转视频
- 🖼️ **画面分析** — 暂停时自动截图 + 视觉理解
- 🪶 **轻量视觉** — 只送「字幕 + 暂停那一帧」而非整段视频；
  实测画面题不输原生多模态，成本 **1/8**、延迟 **1/6.6**（见[评测](#-评测)）
- ❓ **主动学习** — AI 出题考察理解程度
- 🧩 **Chrome 插件** — B站/YouTube 原生集成，全屏可用
- 💾 **多轮对话** — 上下文记忆，跨会话持久化
- 📊 **用量追踪** — Token 消耗 + 费用统计
- 🐳 **Docker 部署** — 一行命令启动

## 🚀 快速开始

自部署的话，**只需要填三个 API Key**（聊天模型、画面分析模型、向量化模型）——
`.env.example` 已按「必填 → 对外部署必看 → 可选」重新归类，前两组填完即可跑。

### 前置要求

- Python 3.12+
- ffmpeg（Windows 需[下载](https://ffmpeg.org/download.html)，Linux `apt install ffmpeg`）

### Docker 部署（自部署时推荐）

```bash
git clone https://github.com/EgoistaCercis/framewise.git
cd framewise
cp .env.example .env        # 编辑 .env 填入 API Key
docker-compose up -d
```


### 手动安装

```bash
git clone https://github.com/EgoistaCercis/framewise.git
cd framewise
pip install -r requirements.txt
cp .env.example .env        # 编辑 .env 填入 API Key
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8123
```

### 处理本地视频

浏览器打开 `http://localhost:8123`，可处理本地视频

### Chrome 插件

**方式一：源码安装（开发者）**

1. 打开 `chrome://extensions/`，开启右上角**开发者模式**
2. 点击**加载已解压的扩展程序**，选择项目的 `extension/` 目录
3. 打开 B站/YouTube 视频，右侧出现 🎬 悬浮按钮即可使用

**方式二：Zip 安装（普通用户）**

1. 从 [GitHub Releases](https://github.com/EgoistaCercis/framewise/releases) 下载 `framewise-extension.zip`
2. 解压到任意文件夹
3. `chrome://extensions/` → 加载已解压的扩展程序 → 选择解压后的文件夹

### 配置 API Key

编辑 `.env` 文件，至少填入以下 Key：

| 服务 | 获取地址 |
|------|---------|
| `LLM_API_KEY` | [DeepSeek](https://platform.deepseek.com/) |
| `EMBEDDING_API_KEY` | [硅基流动](https://siliconflow.cn/) |
| `VISION_API_KEY` | [阿里云 DashScope](https://dashscope.aliyun.com/) |

详见 `.env.example` 中的完整注释。

### 部署到服务器

默认只监听本机（`HOST=127.0.0.1`），本机自己用不需要任何额外配置。
要让**远端插件连过来**，需要处理三件事：

**① 设置访问密钥**

后端默认**没有任何鉴权**，暴露到公网意味着任何人扫到就能调
`/api/videos/from_url` 让服务器下视频、**烧掉你的 LLM 额度**。

所以程序里做了个硬约束：**监听非本机地址时，没配密钥就拒绝所有 `/api` 请求**。

```bash
# 生成一个随机密钥（每次运行结果都不同）
python -c "import secrets; print(secrets.token_urlsafe(24))"
# 或
openssl rand -base64 32
```

写进 `.env`：

```env
HOST=0.0.0.0
API_AUTH_KEY=<上面生成的那串>
```

> 这个密钥只是**一串随机字符**，不是按某个公式算出来的 ——
> 生成方法公开不影响安全性（每次跑结果都不一样）。
> 真正要守住的是**生成出来的那串值**：只放进 `.env`（已在 `.gitignore` 里）
> 和插件的设置里，不要提交、不要贴到公开地方。

**② 配 HTTPS（必须）**

浏览器插件运行在 `https://www.bilibili.com` 上，
从 https 页面请求 `http://` 地址会被按「**混合内容**」直接拦掉
（表现为"连不上后端"，很容易误判成后端没起）。

用 Caddy 最省事，证书自动签发续期：

```
your.domain.com {
    reverse_proxy localhost:8123
}
```

**③ 在插件里填地址和密钥**

插件 → 设置 → 「后端地址」填 `https://your.domain.com`，
「访问密钥」填上面那串。保存时会自动验一次连通性。

## 使用样例

![示例](docs/images/new_ui_20260831.png)

## 🏗️ 架构

> 📐 **[交互式架构图](docs/framewise-architecture.html)**（可切换浅色/深色、缩放、搜索、按关系追踪）

```
用户 → Chrome 插件 / Web 前端（SSE 流式输出）
         ↓
  FastAPI（backend/main.py：CORS + 日志 + 视频状态管理 + 笔记目录）
         ↓
  ┌─ Agent 层（services/agent/）────────────────────────────┐
  │  主 agent：ReAct loop（流式），多轮 + 迭代上限 + 兜底回答  │
  │  工具集：rag_answer / analyze_frame / generate_quiz      │
  │          write_file / read_file / list_notes / delete_file│
  │  记忆工具：save_memory / recall_memory / delete_memory    │
  │  ├ memory agent：长期记忆（JSON cards 三层）              │
  │  └ compress agent：工具结果上下文感知压缩                  │
  └──────────────────────────────────────────────────────────┘
         ↓
  模型网关 gateway.py
  统一 OpenAI 协议 · function calling · 流式 · 前缀缓存友好
  四路模型路由：judge（裁判）> multimodal（图文/视频）> smart > default
         ↓
  多模态模型（DeepSeek / Qwen-VL / GLM / BGE-M3 / SenseVoice）

横向基础设施：
  cost（用量分视频统计，网关统一记账） · pricing（定价版本历史）
  trace（轨迹落盘） · memory（长期记忆存储） · cache（帧/视频缓存）

评测体系（evaluation/，独立于产品运行）：
  80 题评测集 · LLM-as-judge（独立裁判模型） · 多方案对比脚本 · 成本核算
```

**视觉策略（本项目的核心取舍）**：不做「把整段视频丢给多模态模型」，
而是**字幕 + 用户暂停的那一帧**。实测这一取舍在画面题上不输原生多模态
（`visual_only` 相关性 0.872 vs 0.801），代价只有 **1/8 成本、1/6.6 延迟**。
详见 [评测](#-评测)。

## 📁 项目结构

```
framewise/
├── backend/                     # FastAPI 后端
│   ├── main.py                  # API 路由 + 应用入口 + 视频状态管理 + 笔记目录接口
│   ├── config.py                # 配置管理（.env 驱动，含各路模型/超时）
│   ├── prompts.py               # 统一提示词（主 agent / memory / compress / 视觉）
│   └── services/
│       ├── agent/               # Agent 层
│       │   ├── agent.py         # 主 agent（ReAct loop，run 是 run_stream 的薄委托）
│       │   ├── tools.py         # 工具集 + registry + 工具结果压缩 + HITL 确认
│       │   ├── memory_agent.py  # 记忆代理（save / recall / delete）
│       │   └── compress_agent.py# 工具结果上下文感知压缩
│       ├── llm/                 # 模型网关 / 厂商 / 定价 / 用量
│       │   ├── gateway.py       # OpenAI 统一网关（chat/stream/tools/embed/vision/asr）
│       │   │                    #   四路路由 judge > multimodal > smart > default
│       │   ├── provider_service.py  # 厂商标配查询
│       │   ├── pricing_service.py   # 模型定价（带版本历史）
│       │   └── cost_service.py      # token 用量与费用统计（分视频）
│       ├── media/               # 多媒体摄取与理解
│       │   ├── url_service.py       # 视频/字幕获取（官方字幕优先，ASR 兜底）
│       │   ├── asr_service.py       # 语音识别（本地）
│       │   ├── asr_api_service.py   # 语音识别（API）
│       │   ├── vision_service.py    # 截帧 + 画面理解（同步/异步分离）
│       │   └── cache_service.py     # 帧/视频文件缓存
│       ├── rag_pipeline/        # 检索与对话链路
│       │   ├── rag_service.py        # RAG 问答
│       │   ├── conversation_service.py # 多轮对话 + 上下文管理
│       │   ├── embedding_service.py  # 向量化
│       │   ├── vector_store.py       # FAISS 检索
│       │   └── chunk_service.py      # 字幕切分
│       ├── memory/              # 长期记忆存储（JSON cards 三层）
│       │   └── memory_service.py
│       └── trace_service.py     # 轨迹记录（append-only + 大内容落盘）
├── extension/                   # Chrome 插件
│   ├── content.js               # B站/YouTube 注入
│   └── manifest.json
├── frontend/static/             # Web 前端（index.html + css/ + js/）
├── evaluation/                  # 评测集与评测脚本（独立于产品运行）
│   ├── dataset.json             # 80 题标注集（8 个教育视频）
│   ├── subtitles/               # 带时间戳字幕
│   ├── judge.py                 # LLM-as-judge 统一入口（忠实度/相关性/拒答）
│   ├── eval_*.py                # 各方案评测脚本（RAG / 全量上下文 / 固定视觉 / Agent / V5）
│   ├── eval_cost.py             # 成本核算（按跑批窗口从 usage.db 捞账）
│   ├── eval_logger.py           # 跑批日志（含健康检查）
│   └── media_utils.py           # 评测用视频定位与抽帧
├── scripts/                     # 辅助脚本
├── docs/                        # 架构图等
├── data/                        # 运行时数据（缓存 / usage.db / 视频状态）
├── 项目文档/                     # 设计文档、代码审查、踩坑记录、待办
├── Dockerfile · docker-compose.yml · requirements.txt · .env.example
```

## 📊 评测

项目包含一个**开源的视频问答评测集**（[`evaluation/`](evaluation/)），用于量化视频 RAG 的能力边界。

评测集含 **8 个教育视频、80 道题**，覆盖 5 类问题：字幕直答 / 跨片段综合 / 仅画面可答 / 字幕+画面联合 / 无依据，
配套带时间戳字幕与可复现的评测脚本。

在「纯字幕 RAG」上的基线结果：

| 问题类型 | 检索 R@5 | 生成相关性 |
|---|---|---|
| 字幕直答 `single_hop` | 0.750 | **0.969** |
| 跨片段综合 `multi_hop` | 0.625 | 0.762 |
| 字幕+画面 `joint` | 0.375 | 0.713 |
| **仅画面可答 `visual_only`** | 0.312 | **0.237** |
| 无依据 `unanswerable` | — | 拒答率 **1.000** |

**三条关键曲线**：

- **生成相关性随视觉依赖度递减**：`0.969 → 0.762 → 0.713 → 0.237`
  问题越依赖画面，纯字幕 RAG 表现越差——量化了视觉能力的价值缺口
- **跨片段检索是结构性短板**：`multi_hop` 的**全片段命中率仅 0.125**，
  即 87.5% 的跨片段问题检索不完整（单次 top-k 难以覆盖分散的多处信息）
- **无依据题 100% 拒答**：幻觉抑制扎实，未出现编造

> **检索 vs 全量注入**：同评测集上，不检索、直接注入整段字幕**质量 12 项胜 9 / 负 1 / 平 2**
> （`multi_hop` 忠实度 0.414→0.781）—— 这是产品默认路径改成全量注入的依据。
> 代价是每题成本**略高约 20%**（`¥0.0034` vs `¥0.0028`，含输出）：输入侧因为字幕是稳定前缀、
> 命中缓存而更省，但回答更长 —— 以完整账单为准，见 [evaluation/README.md](evaluation/README.md)。
> 详见 [evaluation/README.md](evaluation/README.md)。

### 字幕+暂停帧 vs 原生多模态（V5）

立项的核心假设是「**不需要把整段视频丢给多模态模型**」。同 80 题、同裁判实测：

| | 字幕 + 暂停帧 | 原生整段视频 | 倍数 |
|---|---|---|---|
| 平均延迟 | **15.5s** | 102.7s | **6.6×** |
| 总 input token | 352,104 | 20,734,564 | **59×** |
| 实测费用 | **¥0.024/题** | **¥0.198/题** | **8.4×** |

质量上两档互有胜负，但**方向是反的**：

- 整段视频赢在**纯文本推理**（`multi_hop` 相关性 0.997 vs 0.934）——上下文完整
- 单帧赢在**画面题**（`visual_only` 0.872 vs 0.801）——整段视频**没有「该看哪一帧」
  的锚点**，模型得自己在 10~20 分钟里找那一刻；暂停帧直接把它放在证据上

**结论：原生多模态没有更好，而我们的方案便宜一个数量级。**

> ⚠️ 一个诚实的 caveat：暂停帧用的是题目标注的答案区间起点，相当于给了
> 「答案在哪一刻」的提示。但这也是真实产品形态 —— 用户就是在他好奇的那一刻
> 暂停提问的。**不能**把这读成「单帧的视觉理解能力更强」。

详见 [evaluation/README.md](evaluation/README.md)（含已知局限与复现方式）。

## 📄 协议

[Apache License 2.0](LICENSE)

## ✉️ 联系方式

Egoista_G
