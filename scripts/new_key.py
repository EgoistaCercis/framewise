"""给体验者发密钥：生成密钥 + 校验名字 + 打印可直接粘贴的 .env 行。

用法：

    python scripts/new_key.py 小王 小李          # 批量生成
    python scripts/new_key.py 小王 --append       # 直接追加写入 .env（会提示）

为什么要有个脚本，而不是随手 openssl rand：

1. **名字有规则**（见 auth._validate_names）：只允许数字/字母/下划线/连字符/中文，
   且 ≤32 字符，而且**两个名字规整后不能撞车** —— 撞了会被启动时的校验丢弃，
   被丢的人表现为 401，很难自己想到是名字问题。
   这里直接用 `auth.scope_name_ok` / `sanitize_scope_name` 校验，和运行时**同一份规则**，
   不会出现"脚本说没问题、服务却拒绝"。
2. **检查重名与冲突**：对着当前 `.env` 里的名字查一遍，避免把别人的密钥顶掉。
3. **一次发多人**：宣传期要连续发密钥，一个一个手敲容易抄错。
"""
import argparse
import os
import re
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services.auth import scope_name_ok, sanitize_scope_name, SHARED_KEY_NAME

ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
KEY_BYTES = 24          # token_urlsafe(24) ≈ 32 字符 / 192 bit，够用


def _read_env_names() -> dict:
    """读 .env 里已有的 名字 → 密钥（只看 API_AUTH_KEYS 那一行）。"""
    names = {}
    if not os.path.exists(ENV_PATH):
        return names
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip().startswith("API_AUTH_KEYS="):
                continue
            for item in line.split("=", 1)[1].strip().split(","):
                item = item.strip()
                if item and ":" in item:
                    n, k = item.split(":", 1)
                    names[n.strip()] = k.strip()
    return names


def _append_to_env(new_items: list) -> None:
    """把新条目并进 API_AUTH_KEYS（保留原有内容）。"""
    lines = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()

    existing = _read_env_names()
    merged = list(existing.items()) + [(n, k) for n, k in new_items]
    value = ",".join(f"{n}:{k}" for n, k in merged)

    found = False
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        for line in lines:
            if line.strip().startswith("API_AUTH_KEYS="):
                f.write(f"API_AUTH_KEYS={value}\n")
                found = True
            else:
                f.write(line)
        if not found:
            f.write(f"API_AUTH_KEYS={value}\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="生成体验者密钥")
    ap.add_argument("names", nargs="+", help="体验者的名字（每人一个，建议中文名或英文名）")
    ap.add_argument("--append", action="store_true",
                    help="直接写入 .env 的 API_AUTH_KEYS（默认只打印，不动文件）")
    args = ap.parse_args()

    existing = _read_env_names()
    taken = {sanitize_scope_name(n) for n in existing}

    generated, bad = [], []
    for raw in args.names:
        name = raw.strip()
        if name in existing:
            # 不能静默覆盖：名字相同但密钥不同 = 那个人手上的旧密钥当场失效，
            # 而他不会知道。要轮换就该是有意为之，手动改。
            bad.append((raw, "这个名字已经在 .env 的 API_AUTH_KEYS 里了。"
                             "若确实要轮换（旧密钥作废），请手动改那一行"))
            continue
        if name == SHARED_KEY_NAME:
            bad.append((raw, f"名字 {SHARED_KEY_NAME!r} 是共享密钥（API_AUTH_KEY）专用的，"
                             f"会被忽略。请换一个"))
            continue
        if not scope_name_ok(name):
            bad.append((raw, "名字不合法：只允许数字/字母/下划线/连字符/中文，且不超过 32 字符"))
            continue
        if sanitize_scope_name(name) in taken:
            bad.append((raw, f"与已有名字冲突（两者都会规整为 {sanitize_scope_name(name)!r}），"
                             f"会导致两人共用同一份数据。请换一个"))
            continue
        taken.add(sanitize_scope_name(name))
        generated.append((name, secrets.token_urlsafe(KEY_BYTES)))

    for raw, why in bad:
        print(f"  ✗ {raw}：{why}", file=sys.stderr)

    if generated:
        print("\n新密钥（每人一条，各自独立 —— 用量可归因、可单独吊销、可单独限额）：\n")
        for name, key in generated:
            print(f"  {name}:{key}")
        print("\n粘贴到后端 .env（把这一整行替换掉原来的 API_AUTH_KEYS）：\n")
        merged = list(existing.items()) + generated
        print("  API_AUTH_KEYS=" + ",".join(f"{n}:{k}" for n, k in merged))
        print("\n发给对方时只给**他自己那一串密钥**，别把整行发出去。")
        print("对方在插件「设置 → 访问密钥」里填那一串。")
        if args.append:
            _append_to_env(generated)
            print(f"\n✅ 已写入 {ENV_PATH}（重启服务后生效）")
        else:
            print("\n（加 --append 可直接写入 .env）")

    return 1 if bad and not generated else 0


if __name__ == "__main__":
    sys.exit(main())
