# 帧知 (FrameWise) 🎬

基于多模态 RAG 的视频学习 Agent。上传视频或粘贴B站/YouTube链接，AI 自动建立知识索引，支持文本问答、画面分析、主动出题。

## ✨ 特性

- 🎙️ **语音转文字** — API 秒级处理，支持中英文
- 📝 **内容问答** — 带时间戳引用，点击跳转视频
- 🖼️ **画面分析** — 暂停时自动截图 + 视觉理解
- ❓ **主动学习** — AI 出题考察理解程度
- 🧩 **Chrome 插件** — B站/YouTube 原生集成，全屏可用
- 💾 **多轮对话** — 上下文记忆，跨会话持久化
- 📊 **用量追踪** — Token 消耗 + 费用统计
- 🐳 **Docker 部署** — 一行命令启动

## 🚀 快速开始

### 前置要求

- Python 3.12+
- ffmpeg（Windows 需[下载](https://ffmpeg.org/download.html)，Linux `apt install ffmpeg`）

### Docker（推荐）

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
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

### 处理本地视频

浏览器打开 http://localhost:8000，可处理本地视频

### Chrome 插件

1. 打开 `chrome://extensions/`，开启右上角**开发者模式**
2. 点击**加载已解压的扩展程序**，选择 `extension/` 目录
3. 打开 B站/YouTube 视频，右侧出现 🎬 悬浮按钮即可使用

### 配置 API Key

编辑 `.env` 文件，至少填入以下 Key：

| 服务 | 获取地址 |
|------|---------|
| `LLM_API_KEY` | [DeepSeek](https://platform.deepseek.com/) |
| `EMBEDDING_API_KEY` | [硅基流动](https://siliconflow.cn/) |
| `VISION_API_KEY` | [阿里云 DashScope](https://dashscope.aliyun.com/) |

详见 `.env.example` 中的完整注释。

## 🏗️ 架构

```
用户 → Web前端 / Chrome插件
         ↓
    FastAPI 网关层（CORS + Loguru）
         ↓
  ┌──────┼──────┐
  ↓      ↓      ↓
视频处理  ASR    RAG 检索
(yt-dlp) (API)  (FAISS)
                  ↓
        多模态 LLM（DeepSeek / Qwen VL）
                  ↓
           回答 + 时间戳
```

## 📁 项目结构

```
framewise/
├── backend/                  # FastAPI 后端
│   ├── main.py               # API 路由
│   ├── config.py             # 配置管理（.env 驱动）
│   └── services/             # 核心服务
│       ├── provider_service  # API 厂商标配层
│       ├── rag_service       # RAG 问答
│       ├── vision_service    # 画面分析
│       ├── asr_service       # 语音识别
│       └── ...
├── extension/                # Chrome 插件
│   ├── content.js            # B站/YouTube 注入
│   └── manifest.json
├── frontend/                 # Web 前端
│   └── static/
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 📄 协议

[Apache License 2.0](LICENSE)
