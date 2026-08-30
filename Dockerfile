FROM python:3.12-slim

WORKDIR /app

# 系统依赖（换国内 apt 镜像源，避免 deb.debian.org 在国内构建超时）
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' \
        /etc/apt/sources.list.d/*.sources /etc/apt/sources.list 2>/dev/null || true \
    && apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 应用代码
COPY . .

# 数据目录
RUN mkdir -p data/uploads data/subtitles data/embeddings data/frames data/audio data/logs

EXPOSE 8123

CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8123", "--reload"]
