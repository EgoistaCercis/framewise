"""
手动下载 faster-whisper-tiny 模型（绕过 HF Hub Xet 问题）
直接下载文件到本地，然后 faster-whisper 从本地加载
"""
import os
import sys
import urllib.request
import json

# 模型文件列表 (faster-whisper-tiny)
FILES = [
    "config.json",
    "model.bin",
    "tokenizer.json",
    "preprocessor_config.json",
]

HF_MIRROR = "https://hf-mirror.com"
REPO = "Systran/faster-whisper-tiny"

# 本地存储路径
LOCAL_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "models", "faster-whisper-tiny")
LOCAL_DIR = os.path.normpath(LOCAL_DIR)


def download(url, dest):
    """简单文件下载"""
    print(f"  下载: {url}")
    print(f"  保存到: {dest}")

    def report(block_num, block_size, total_size):
        downloaded = block_num * block_size
        if total_size > 0:
            pct = min(100, downloaded * 100 // total_size)
            mb = downloaded / 1024 / 1024
            total_mb = total_size / 1024 / 1024
            sys.stdout.write(f"\r  {pct}% {mb:.1f}/{total_mb:.1f} MB")
            sys.stdout.flush()

    urllib.request.urlretrieve(url, dest, report)
    size_mb = os.path.getsize(dest) / 1024 / 1024
    print(f"\r  完成 ({size_mb:.1f} MB)")


def main():
    os.makedirs(LOCAL_DIR, exist_ok=True)

    print(f"下载 faster-whisper-tiny 模型文件")
    print(f"源: {HF_MIRROR}/{REPO}")
    print(f"目标: {LOCAL_DIR}")
    print()

    for fname in FILES:
        dest = os.path.join(LOCAL_DIR, fname)
        if os.path.exists(dest):
            print(f"  {fname} 已存在，跳过")
            continue

        url = f"{HF_MIRROR}/{REPO}/resolve/main/{fname}"
        try:
            download(url, dest)
        except Exception as e:
            print(f"  错误: {e}")

    # 验证
    print("\n验证下载结果:")
    all_ok = True
    for fname in FILES:
        path = os.path.join(LOCAL_DIR, fname)
        exists = os.path.exists(path)
        size = os.path.getsize(path) / 1024 / 1024 if exists else 0
        status = f"✓ {size:.1f} MB" if exists else "✗ 缺失"
        print(f"  {fname}: {status}")
        if not exists:
            all_ok = False

    if all_ok:
        print(f"\n✅ 模型下载完成!")
        print(f"   路径: {LOCAL_DIR}")
    else:
        print(f"\n❌ 部分文件下载失败，请重试")


if __name__ == "__main__":
    main()
