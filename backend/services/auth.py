"""
帧知 - 访问鉴权 + 配额

**共享密钥**方案：所有 `/api/*` 请求需带 `X-API-Key` 头。

## 密钥有两种配法（可共存）

- `API_AUTH_KEY`：单个密钥，**本地自己用**。记账时归属到 `"default"`。
- `API_AUTH_KEYS`：`名字:密钥,名字:密钥`，**给多人用时每人发一个**。

**为什么建议每人一个而不是大家共享一个**：

| | 共享一个 | 每人一个 |
|---|---|---|
| 用量归因 | ❌ 全混在一起 | ✅ |
| 吊销某人 | 换密钥 = 所有人重配 | ✅ 只删他那条 |
| 单独限额 | ❌ | ✅ |

## 三条设计要点

1. **未配置密钥时的行为是按监听地址决定的**（`require_key` 里那段）：
   - 只监听本机（127.0.0.1 / localhost）→ 放行。只有本机能访问，本来就是安全的。
   - 监听 `0.0.0.0` 等外部地址 → **拒绝所有 /api 请求**。

   这条的意图是「**你把它暴露出去，就必须设密码**」——
   用户不需要理解安全细节，配置错了会被**立刻挡住**，而不是静默裸奔
   直到某天发现额度被刷光。

2. **用中间件而不是逐端点加 `Depends`**：保证**以后新增的端点默认受保护**。
   依赖是"每个端点都要记得加"，而人会忘 —— 那样漏掉的那一个就是后门。

3. **用 `hmac.compare_digest` 而不是 `==`**：前者是常数时间比较，防时序攻击。

## ⚠️ 改动这里时最容易踩的坑：CORS 预检

加了 `X-API-Key` 之后，浏览器对跨域请求会**先发一个 OPTIONS 预检**，
而按规范**预检不携带自定义头** —— 如果鉴权不放过 OPTIONS，预检就会 401，
浏览器进而**拦掉真正的请求**。

症状特别有迷惑性：插件那侧报的是「**连不上后端**」（网络错误），
而不是 401 —— 完全联想不到是鉴权。

**而且这个 bug 用 curl 测不出来**（curl 不做预检）。
必须显式模拟一次预检才能发现：

    curl -i -X OPTIONS -H "Origin: https://www.bilibili.com" \\
         -H "Access-Control-Request-Method: GET" \\
         -H "Access-Control-Request-Headers: x-api-key" \\
         http://127.0.0.1:8123/api/memory

## 身份怎么传到记账那一层

记账点在 `gateway._record`，往上隔着 `tools → Agent → 路由` 好几层。
为它给每一层加一个参数不划算，所以用 **ContextVar**：
中间件校验通过后把调用者写进当前请求的上下文，网关深处直接读。
asyncio 的 ContextVar 天然按任务隔离，**并发请求不会串**。
（项目里 `eval_v4_agent.py` 用的是同一个机制，解决同一类问题。）
"""
import hmac
from contextvars import ContextVar

from fastapi import Request
from fastapi.responses import JSONResponse
from loguru import logger

from backend import config

# 无需鉴权的路径。`/api/health` 是探活探针：它不泄露任何东西，
# 而插件要用它验证「地址填对了没有」—— 让它也需要密钥，验证流程反而更绕。
PUBLIC_PATHS = {"/api/health"}

# 视为「只监听本机」的地址
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

# 鉴权只作用于 API；静态资源（前端页面）本身不放行任何数据
_API_PREFIX = "/api/"

# 本机直连（免鉴权）时记在账上的名字，与"没配密钥直接放行"区分开
LOCAL_CALLER = "local"
# 没有请求上下文的调用（评测脚本、定时任务）归到这里
_NO_CALLER = ""

_current_caller: ContextVar[str] = ContextVar("fw_caller", default=_NO_CALLER)


def current_caller() -> str:
    """当前请求的调用者名（供 usage 记账用）。

    不在请求上下文里时返回空串（如评测脚本直接调 gateway）。
    """
    return _current_caller.get()


def current_scope() -> str:
    """数据分区键 —— 用来把「谁的对话 / 记忆 / 笔记」分开存放。

    与 `current_caller()` 的区别**只在"本机"那条路上**：

    - 具名远程调用者 → 返回名字，各看各的
    - 本机直连（`LOCAL_CALLER`）/ 无请求上下文 → 返回**空串**，沿用历史共享数据

    本机返回空串是刻意的：这个项目一直是单人本地用的，已有的对话和笔记都在
    "没有分区"的那个桶里。给本机新开一个 `local/` 分区，老用户打开就是空的 ——
    数据明明还在磁盘上，只是没被去找。所以本机必须继续读老桶。

    调用方约定：拿到空串就用「不分区」的路径（根目录 / 不筛 caller 的查询）。
    """
    caller = current_caller()
    return "" if caller in (_NO_CALLER, LOCAL_CALLER) else caller


def _load_keys() -> dict:
    """解析出 {调用者名: 密钥}。

    `API_AUTH_KEYS` 的格式是 `名字:密钥,名字:密钥`。
    格式不对的条目跳过并告警 —— 一条写错不该让整个服务起不来，
    但**必须让它可见**，否则表现为"某个人的密钥怎么都不对"。
    """
    keys = {}
    if config.API_AUTH_KEY:
        keys["default"] = config.API_AUTH_KEY
    for raw in (config.API_AUTH_KEYS or "").split(","):
        item = raw.strip()
        if not item:
            continue
        if ":" not in item:
            logger.warning(f"API_AUTH_KEYS 里的条目缺少 ':'，已跳过：{item[:20]}…")
            continue
        name, key = item.split(":", 1)
        name, key = name.strip(), key.strip()
        if not name or not key:
            logger.warning(f"API_AUTH_KEYS 里有空名字或空密钥，已跳过：{item[:20]}…")
            continue
        keys[name] = key
    return keys


def is_local_only() -> bool:
    """服务是否只监听本机。"""
    return config.HOST in _LOCAL_HOSTS


def startup_check() -> None:
    """启动自检：把鉴权与配额的实际状态打进日志。

    这里只**报告**（真正的拦截在 require_key 里）—— 目的是让配置问题
    在启动时就可见，而不是等第一个请求进来才发现服务用不了。
    """
    keys = _load_keys()
    if keys:
        names = "、".join(keys)
        logger.info(f"🔒 访问鉴权已开启（X-API-Key）：{len(keys)} 个调用者 —— {names}")
        if config.API_DAILY_TOKEN_LIMIT:
            logger.info(f"📊 每人每日上限：{config.API_DAILY_TOKEN_LIMIT:,} token")
        else:
            logger.warning("⚠️  未设置 API_DAILY_TOKEN_LIMIT —— 拿到密钥的人可无限消耗额度")
        return
    if is_local_only():
        logger.info(f"🔓 未配置访问密钥，但只监听 {config.HOST}（本机），无需鉴权")
        return
    logger.warning(
        f"⚠️  监听 {config.HOST}（对外可访问）但**未配置任何访问密钥** —— "
        f"所有 /api 请求会被拒绝。请在 .env 里设置 API_AUTH_KEY，"
        f"并在插件的「设置 → 访问密钥」里填一样的值。"
        f"（若只想本机使用，把 .env 的 HOST 改回 127.0.0.1）")


def _reject(request: Request, status: int, message: str) -> JSONResponse:
    """拒绝请求，并**留下日志**。

    单独记一笔是必要的：鉴权中间件注册在访问日志中间件之外，
    被它拦下的请求不会走到那条日志里 —— 于是"有人在扫我的服务"
    这件事完全不可见。
    """
    client = request.client.host if request.client else "?"
    logger.warning(f"⛔ 拒绝 {request.method} {request.url.path} ← {client}（{status}）")
    return JSONResponse({"detail": message}, status_code=status)


def _match_caller(provided: str, keys: dict) -> str:
    """返回密钥对应的调用者名；不匹配返回空串。

    逐个比对（而不是先查表）是必须的：字典查找的耗时依赖 key 是否存在，
    会泄漏"某个前缀对不对"。这里用常数时间比较，且**不比提前返回**，
    让所有条目都走一遍。
    """
    matched = _NO_CALLER
    for name, key in keys.items():
        if hmac.compare_digest(provided, key):
            matched = name
    return matched


async def require_key(request: Request):
    """FastAPI 中间件：校验 X-API-Key，并做每日配额检查。

    返回 None 表示放行（`call_next` 继续）；返回 Response 表示直接拒绝。
    """
    path = request.url.path
    # 非 API 路径（静态页、首页）放行 —— 它们不返回任何用户数据
    if not path.startswith(_API_PREFIX) or path in PUBLIC_PATHS:
        return None

    # ★ CORS 预检必须放行。
    #
    # 浏览器对「跨域 + 自定义头（X-API-Key）」的请求会**先发一个 OPTIONS 预检**，
    # 而按规范**预检请求不携带自定义头** —— 于是这里看不到密钥，一律 401；
    # 浏览器见预检失败就**拦掉真正的请求**，插件那侧表现为"连不上后端"
    # （不是 401，是网络错误，所以特别难联想到鉴权）。
    #
    # 放行是安全的：预检是「我能不能发这个请求」的能力查询，不读任何数据；
    # CORS 中间件会直接应答它，**不会走到任何路由处理函数**。
    if request.method == "OPTIONS":
        return None

    keys = _load_keys()

    if not keys:
        if is_local_only():
            _current_caller.set(LOCAL_CALLER)
            return None
        # fail-closed：对外监听却没配密钥 → 一律拒绝，而不是"当成没开鉴权"
        return _reject(request, 503,
                       "服务监听在非本机地址，但未配置访问密钥，已拒绝请求。"
                       "请在 .env 设置 API_AUTH_KEY（并在插件设置里填入相同的值）；"
                       "若只想本机使用，把 HOST 改回 127.0.0.1。")

    provided = request.headers.get("X-API-Key", "")
    caller = _match_caller(provided, keys)
    if not caller:
        return _reject(request, 401, "无效的 API Key（请在插件的「设置 → 访问密钥」里填写）")

    # 配额：只对"已经过鉴权的调用者"生效（本机免鉴权那条路不受限，
    # 否则自己调试时会被自己的限额挡住）
    limit = config.API_DAILY_TOKEN_LIMIT
    if limit > 0:
        from backend.services.llm.cost_service import tokens_used_today
        used = tokens_used_today(caller)
        if used >= limit:
            logger.warning(f"📊 {caller} 今日已用 {used:,} token，超过上限 {limit:,}，拒绝")
            return _reject(request, 429,
                           f"今日额度已用完（{used:,}/{limit:,} token）。"
                           f"请明天再试，或联系管理员调整上限。")

    _current_caller.set(caller)
    return None
