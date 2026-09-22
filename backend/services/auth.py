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
import re
from contextvars import ContextVar

from fastapi import Request
from fastapi.responses import JSONResponse
from loguru import logger

from backend import config

# 分区名允许的字符：数字、字母、下划线、连字符、CJK。
# ★ 校验与清洗共用同一份字符类（这里定义，tools.note_dir 引用）——
#   两边各写一份的话，很容易出现"校验放行了、清洗后又撞到别人"的缝。
_SCOPE_DISALLOWED = re.compile(r"[^0-9A-Za-z_一-鿿-]")
_SCOPE_NAME_RE = re.compile(r"^[0-9A-Za-z_一-鿿-]{1,32}$")

# `API_AUTH_KEY`（单个共享密钥）在内部用的名字
SHARED_KEY_NAME = "default"


def sanitize_scope_name(name: str) -> str:
    """把调用者名字规整成安全的目录名。

    这是**清洗**不是**校验**：`alice.x` 与 `alice_x` 都会被清成 `alice_x`，
    两个不同的人会静默落进同一份数据。所以名字的合法性在 `_validate_names`
    里单独把关，这里的清洗只作纵深防御。
    """
    return _SCOPE_DISALLOWED.sub("_", (name or "").strip())[:32]


def scope_name_ok(name: str) -> bool:
    """名字是否合规（合规的名字清洗后不变，也就不会和别人撞车）"""
    return bool(_SCOPE_NAME_RE.match(name or ""))

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


def is_admin() -> bool:
    """当前请求是不是「服务器主人」（本机直连 / 无请求上下文）。

    用来把**改动服务器级配置**的能力收在本机：改笔记目录、改定价表。
    远程具名调用者一律不是 —— 它们各自的数据是隔离的，但服务器配置是所有人的。

    注意与 `is_local_only()` 的区别：那个看的是**服务绑定在哪个地址**
    （决定"没配密钥时要不要 fail-closed"），这个看的是**当前请求是谁**。
    """
    return not current_scope()


def _parse_keys() -> dict:
    """解析 `API_AUTH_KEY` + `API_AUTH_KEYS`，**不做名字校验**（见 _validate_names）。"""
    keys = {}
    if config.API_AUTH_KEY:
        keys[SHARED_KEY_NAME] = config.API_AUTH_KEY
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
        if name == SHARED_KEY_NAME and config.API_AUTH_KEY:
            # 不拦就会**静默**夺走共享密钥的身份（字典后写覆盖先写），
            # 表现是"我的那把钥匙突然不管用了"
            logger.error(f"⛔ API_AUTH_KEYS 里的名字 {name!r} 与内置共享密钥重名，"
                         f"该条目已忽略，请改名")
            continue
        keys[name] = key
    return keys


def _validate_names(keys: dict) -> dict:
    """校验名字段，丢掉"清洗后会撞车"的条目。

    ★ 为什么不能只靠清洗：`alice.x` 与 `alice_x` 清洗后都是 `alice_x`，
    两个不同的人会**静默**共用同一份笔记；超过 32 字符的名字截断后同样会撞。
    名字是人手写进 .env 的，所以这里以「丢掉 + 报错」为主 ——
    和"对外监听却没配密钥就一律拒绝"是同一个取舍：配置错了要**立刻可见**，
    而不是让它悄悄串数据。被丢掉的人表现为 401，一眼能查到来由。
    """
    kept, seen = {}, {}
    for name, key in keys.items():
        if not scope_name_ok(name):
            logger.error(f"⛔ API_AUTH_KEYS 的名字 {name!r} 不合法（只允许数字/字母/"
                         f"下划线/连字符/中文，且 ≤32 字符），该条目已忽略，请改名")
            continue
        sanitized = sanitize_scope_name(name)
        if sanitized in seen:
            logger.error(f"⛔ API_AUTH_KEYS 里 {seen[sanitized]!r} 与 {name!r} 会落到"
                         f"同一份数据（都规整为 {sanitized!r}），后者已忽略，请改名")
            continue
        seen[sanitized] = name
        kept[name] = key
    return kept


# (配置签名, 解析结果)。config 变了签名就变，缓存自然失效。
_keys_cache: tuple = (None, None)


def _load_keys() -> dict:
    """解析并校验出 {调用者名: 密钥}（按配置内容缓存）。

    `API_AUTH_KEYS` 的格式是 `名字:密钥,名字:密钥`。
    格式不对的条目跳过并告警 —— 一条写错不该让整个服务起不来，
    但**必须让它可见**，否则表现为"某个人的密钥怎么都不对"。

    缓存有两个理由：① 每个请求都会走到这里，重复解析纯属浪费；
    ② 校验里的 ERROR 只该在配置变化时喊一次，而不是每个请求喊一次。
    """
    global _keys_cache
    sig = (config.API_AUTH_KEY or "", config.API_AUTH_KEYS or "")
    if _keys_cache[0] == sig:
        return _keys_cache[1]
    keys = _validate_names(_parse_keys())
    _keys_cache = (sig, keys)
    return keys


def quota_state(caller: str) -> tuple:
    """返回 `(是否已超额, 今日已用 token)`。

    入口检查（`require_key`）与网关复查（`gateway._check_quota`）共用这一份判断 ——
    两边各写一遍的话，很容易一边按 caller 算、一边按 scope 算，口径就分叉了。

    限额未开（`API_DAILY_TOKEN_LIMIT=0`）、无调用者、或本机免鉴权那条路 → `(False, 0)`。
    """
    limit = config.API_DAILY_TOKEN_LIMIT
    if limit <= 0 or not caller or caller == LOCAL_CALLER:
        return (False, 0)
    from backend.services.llm.cost_service import tokens_used_today
    used = tokens_used_today(caller)
    return (used >= limit, used)


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
    return JSONResponse(
        {"detail": message}, status_code=status,
        # ★ 必须手动补 CORS 头。鉴权中间件注册在 CORSMiddleware **之外**，
        #   被它拒掉的响应不经过 CORS 处理 —— 浏览器于是只看到「网络错误」，
        #   而不是那句 401/429 的说明文字（正是本模块开头警告过的那类迷惑症状：
        #   预检放行了，被拒的真请求却没带 CORS 头）。
        #   正常响应由 CORSMiddleware 补，这里补的是它够不到的那条路。
        headers={"Access-Control-Allow-Origin": "*"},
    )


def _match_caller(provided: str, keys: dict) -> str:
    """返回密钥对应的调用者名；不匹配返回空串。

    逐个比对（而不是先查表）是必须的：字典查找的耗时依赖 key 是否存在，
    会泄漏"某个前缀对不对"。这里用常数时间比较，且**不比提前返回**，
    让所有条目都走一遍。

    ★ 比较前转成 bytes：HTTP 头是按 latin-1 解码的，攻击者塞一个高位字节
    （如 0xFF）就能得到一个非 ASCII 的 str，而 str 版的 `compare_digest`
    遇到非 ASCII 会抛 `TypeError` → **一个请求换一条 500 日志**。
    这不能绕过鉴权（异常发生在比对时，不是通过时），但足以灌满日志。
    """
    matched = _NO_CALLER
    provided_b = (provided or "").encode()
    for name, key in keys.items():
        if hmac.compare_digest(provided_b, key.encode()):
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
    #
    # ⚠️ 这里只是**入口检查**，管不住并发也管不住单请求超烧：
    #    额度用到 99% 时同时发 50 个请求，它们全都在入账**之前**通过了这道检查；
    #    而每个请求是 Agent 循环（最多 8 轮 × 64K 输入预算），一次能烧几十万 token。
    #    真正的兜底在 `gateway._check_quota`（每次模型调用前复查）。
    over, used = quota_state(caller)
    if over:
        logger.warning(f"📊 {caller} 今日已用 {used:,} token，超过上限 "
                       f"{config.API_DAILY_TOKEN_LIMIT:,}，拒绝")
        return _reject(request, 429,
                       f"今日额度已用完（{used:,}/{config.API_DAILY_TOKEN_LIMIT:,} token）。"
                       f"请明天再试，或联系管理员调整上限。")

    _current_caller.set(caller)
    return None
