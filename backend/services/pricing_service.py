"""
帧知 - 模型定价组件

独立维护各模型价格（人民币 / 百万 tokens），带生效时间版本，
成本按「调用时刻生效的价格」计算，保证历史成本回溯准确。

- 手动/批量更新最新价时插入新版本并把旧版本置为 inactive
- 预留 fetch_latest() 自动拉价接口（国内厂商暂无统一公开价格 API，默认关闭）

用量统计在 backend.services.cost_service，本组件只管「定价」。
"""
import os
import sqlite3
from datetime import datetime, timezone

from backend.config import DATA_DIR

DB_PATH = os.path.join(DATA_DIR, "usage.db")

# 生效时间的默认值：任何价格都必须 >= 它（最早版本）
_EPOCH = "1970-01-01T00:00:00"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """建 pricing 表"""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pricing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT '',
            input_ppm REAL NOT NULL,          -- 元 / 百万 input tokens（非缓存部分）
            output_ppm REAL NOT NULL,         -- 元 / 百万 output tokens
            cache_ppm REAL NOT NULL DEFAULT 0,   -- 元 / 百万缓存命中 input
            reasoning_ppm REAL NOT NULL DEFAULT 0, -- 元 / 百万推理(reasoning) token
            effective_from TEXT NOT NULL,     -- 该价格生效起始时间
            active INTEGER NOT NULL DEFAULT 1,    -- 是否为当前生效版本
            note TEXT DEFAULT ''
        )
    """)
    conn.commit()
    conn.close()


def _row_to_dict(r) -> dict:
    return {
        "model": r["model"],
        "provider": r["provider"],
        "input_ppm": r["input_ppm"],
        "output_ppm": r["output_ppm"],
        "cache_ppm": r["cache_ppm"],
        "reasoning_ppm": r["reasoning_ppm"],
        "effective_from": r["effective_from"],
        "active": bool(r["active"]),
        "note": r["note"],
    }


# ── 种子数据：从旧的硬编码 PRICING（元/1K）折算为 元/百万 ──
_SEED = [
    # model, provider, input_ppm, output_ppm, cache_ppm, reasoning_ppm
    ("deepseek-v4-flash", "deepseek",        1.0,   2.0,   0.1,  2.0),   # 估算，请按官方价更新
    ("deepseek-v4-pro",   "deepseek",        4.0,  16.0,   0.5, 16.0),   # 估算，请按官方价更新
    ("BAAI/bge-m3",       "siliconflow",     0.7,   0.0,   0.0,  0.0),
    ("qwen-vl-plus",      "dashscope",       1.5,   6.0,   0.0,  6.0),
    ("FunAudioLLM/SenseVoiceSmall", "siliconflow", 0.5, 0.0, 0.0, 0.0),
    ("paraformer-v2",     "dashscope",       0.8,   0.0,   0.0,  0.0),
    ("default",           "",                1.0,   2.0,   0.0,  2.0),
]


def init_seed():
    """仅当 pricing 表为空时写入种子数据"""
    conn = _get_conn()
    count = conn.execute("SELECT COUNT(*) FROM pricing").fetchone()[0]
    if count > 0:
        conn.close()
        return
    now = _now()
    for model, provider, i, o, c, r in _SEED:
        conn.execute("""
            INSERT INTO pricing (model, provider, input_ppm, output_ppm, cache_ppm,
                                 reasoning_ppm, effective_from, active, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'seed')
        """, (model, provider, i, o, c, r, now))
    conn.commit()
    conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_price(model: str, timestamp: str = None) -> dict:
    """取某模型在 timestamp 时刻生效的价格。

    timestamp 缺省为「当前」；跨历史回溯时传入当时的调用时间，
    取 effective_from <= timestamp 的最新一条生效记录。
    """
    ts = timestamp or _now()
    conn = _get_conn()
    row = conn.execute("""
        SELECT * FROM pricing
        WHERE model = ? AND active = 1 AND effective_from <= ?
        ORDER BY effective_from DESC, id DESC
        LIMIT 1
    """, (model, ts)).fetchone()
    if row is None:
        # 无精确匹配 → 落到 default 的有效价格
        row = conn.execute("""
            SELECT * FROM pricing
            WHERE model = 'default' AND active = 1 AND effective_from <= ?
            ORDER BY effective_from DESC, id DESC LIMIT 1
        """, (ts,)).fetchone()
    conn.close()

    if row is None:
        return {"input_ppm": 0.0, "output_ppm": 0.0, "cache_ppm": 0.0, "reasoning_ppm": 0.0}
    return {"input_ppm": row["input_ppm"], "output_ppm": row["output_ppm"],
            "cache_ppm": row["cache_ppm"], "reasoning_ppm": row["reasoning_ppm"]}


def compute_cost(model: str, input_tokens: int, output_tokens: int,
                 cached_tokens: int = 0, reasoning_tokens: int = 0,
                 timestamp: str = None) -> dict:
    """按调用时刻价格计算本次费用（人民币）。

    - input 计费：非缓存部分按 input_ppm，缓存命中部分按 cache_ppm
    - output 计费：reasoning token 按 reasoning_ppm，其余按 output_ppm
    """
    price = get_price(model, timestamp)
    i, o, c, r = price["input_ppm"], price["output_ppm"], price["cache_ppm"], price["reasoning_ppm"]

    cache_tokens = min(cached_tokens, input_tokens)
    uncached_input = max(input_tokens - cache_tokens, 0)
    reasoning = min(reasoning_tokens, output_tokens)
    plain_output = max(output_tokens - reasoning, 0)

    input_cost = uncached_input / 1e6 * i + cache_tokens / 1e6 * c
    output_cost = plain_output / 1e6 * o + reasoning / 1e6 * r

    return {
        "input_cost": round(input_cost, 6),
        "output_cost": round(output_cost, 6),
        "cache_cost": round(cache_tokens / 1e6 * c, 6),
        "reasoning_cost": round(reasoning / 1e6 * r, 6),
        "total_cost": round(input_cost + output_cost, 6),
    }


# ── 价格管理 ──────────────────────────────────────────
def get_all() -> list[dict]:
    """查看所有价格版本（含历史）"""
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM pricing ORDER BY model, effective_from DESC, id DESC").fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def update_price(model: str, input_ppm: float, output_ppm: float,
                 cache_ppm: float = 0, reasoning_ppm: float = 0,
                 provider: str = "", note: str = "") -> int:
    """更新某个模型为最新价：把旧版本置为 inactive，插入新版本。返回新 id。"""
    conn = _get_conn()
    now = _now()
    conn.execute("UPDATE pricing SET active = 0 WHERE model = ? AND active = 1", (model,))
    cur = conn.execute("""
        INSERT INTO pricing (model, provider, input_ppm, output_ppm, cache_ppm,
                             reasoning_ppm, effective_from, active, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
    """, (model, provider, input_ppm, output_ppm, cache_ppm, reasoning_ppm, now, note))
    conn.commit()
    conn.close()
    return cur.lastrowid


# ── 自动拉价（预留接口，默认关闭）──────────────────────
def fetch_latest_price(model: str) -> dict | None:
    """尝试从厂商/公开源拉取最新价。

    国内厂商暂无统一公开价格 API，此接口默认返回 None（表示未接入）。
    接入时按需填充，返回 {"input_ppm":..., ...} 即可。
    """
    return None


init_db()
init_seed()
