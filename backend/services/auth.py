"""
帧知 - 访问鉴权

**共享密钥**方案：所有 `/api/*` 请求需带 `X-API-Key` 头，值等于 `config.API_AUTH_KEY`。

为什么是这个方案：本项目是**单用户个人部署**，不是多租户服务。
一个共享密钥就够了 —— 无状态、插件里填一次、不引入 session/JWT 的复杂度。
如果将来要做多用户，再换成账号体系，但那时整个产品形态也不一样了。

## 三条设计要点

1. **未配置密钥时的行为是按监听地址决定的**（`require_key` 里的那段）：
   - 只监听本机（127.0.0.1 / localhost）→ 放行。只有本机能访问，本来就是安全的。
   - 监听 `0.0.0.0` 等外部地址 → **拒绝所有 /api 请求**。

   这条的意图是「**你把它暴露出去，就必须设密码**」——
   用户不需要理解安全细节，配置错了会被**立刻挡住**，而不是静默裸奔
   直到某天发现额度被刷光。

2. **用中间件而不是逐端点加 `Depends`**：保证**以后新增的端点默认受保护**。
   依赖是"每个端点都要记得加"，而人会忘 —— 那样漏掉的那一个就是后门。

3. **用 `hmac.compare_digest` 而不是 `==`**：前者是常数时间比较，防时序攻击。
   （密钥短、且攻击者需要先知道长度，实际风险不高，但这行代码不贵。）
"""
import hmac

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


def is_local_only() -> bool:
    """服务是否只监听本机。"""
    return config.HOST in _LOCAL_HOSTS


def startup_check() -> None:
    """启动自检：对外监听却没配密钥时大声警告。

    真正的拦截在 `require_key` 里（返回 503）。这里只是让它在**启动日志**里
    就可见 —— 而不是等第一个请求进来才发现服务用不了。
    """
    if config.API_AUTH_KEY:
        logger.info(f"🔒 访问鉴权已开启（X-API-Key，{len(config.API_AUTH_KEY)} 位）")
        return
    if is_local_only():
        logger.info(f"🔓 未配置 API_AUTH_KEY，但只监听 {config.HOST}（本机），无需鉴权")
        return
    logger.warning(
        f"⚠️  监听 {config.HOST}（对外可访问）但**未配置 API_AUTH_KEY** —— "
        f"所有 /api 请求会被拒绝。请在 .env 里设置一个密钥，"
        f"并在插件的「设置 → 访问密钥」里填一样的值。"
        f"（若只想本机使用，把 .env 的 HOST 改回 127.0.0.1）")


def _reject(request: Request, status: int, message: str) -> JSONResponse:
    """拒绝请求，并**留下日志**。

    单独记一笔是必要的：鉴权中间件注册在访问日志中间件之外，
    被它拦下的请求不会走到那条日志里 —— 于是"有人在扫我的服务"
    这件事完全不可见。
    """
    client = request.client.host if request.client else "?"
    logger.warning(f"⛔ 鉴权拒绝 {request.method} {request.url.path} ← {client}（{status}）")
    return JSONResponse({"detail": message}, status_code=status)


async def require_key(request: Request):
    """FastAPI 中间件：校验 X-API-Key。

    返回 None 表示放行（`call_next` 继续）；返回 Response 表示直接拒绝。
    """
    path = request.url.path
    # 非 API 路径（静态页、首页）放行 —— 它们不返回任何用户数据
    if not path.startswith(_API_PREFIX) or path in PUBLIC_PATHS:
        return None

    if not config.API_AUTH_KEY:
        if is_local_only():
            return None
        # fail-closed：对外监听却没配密钥 → 一律拒绝，而不是"当成没开鉴权"
        return _reject(request, 503,
                       "服务监听在非本机地址，但未配置 API_AUTH_KEY，已拒绝请求。"
                       "请在 .env 设置 API_AUTH_KEY（并在插件设置里填入相同的值）；"
                       "若只想本机使用，把 HOST 改回 127.0.0.1。")

    provided = request.headers.get("X-API-Key", "")
    if not hmac.compare_digest(provided, config.API_AUTH_KEY):
        return _reject(request, 401, "无效的 API Key（请在插件的「设置 → 访问密钥」里填写）")
    return None
