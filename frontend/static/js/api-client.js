/**
 * 帧知 - 前端共用的 API 客户端（带访问密钥）
 *
 * ★ 为什么单独一个文件：这段逻辑**两份必然走偏** —— 它管的是"密钥怎么带、
 *   401 怎么补救"，改了主页那页的文案或重放逻辑却忘了另一页，症状是
 *   "某个页面密钥填了不生效"，而那种问题很难往"两份代码不同步"上想。
 *   这个项目已经在这类事上吃过一次亏（`format_memories_with_prompt` 改名没同步，
 *   Web 页静默不注入记忆且不报错）。所以两页共用这一份。
 *
 * 用法：在页面自己的脚本**之前**引入本文件，之后照常写 `fetch("/api/...")`。
 * 它 monkey-patch `window.fetch`，只对 `/api/` 开头的请求生效。
 */
(function () {
    "use strict";

    var _rawFetch = window.fetch.bind(window);
    var _askedKey = false;   // 每次页面加载只问一次，避免连环弹窗

    function apiKey() {
        return localStorage.getItem("fw_api_key") || "";
    }

    window.fetch = function (url, opts) {
        var u = typeof url === "string" ? url : (url && url.url) || "";
        // 非 API 请求（静态资源、favicon）原样透传
        if (!u.startsWith("/api/")) return _rawFetch(url, opts);

        opts = Object.assign({}, opts);
        var headers = Object.assign({}, opts.headers || {});
        var k = apiKey();
        if (k) headers["X-API-Key"] = k;
        opts.headers = headers;

        return _rawFetch(url, opts).then(function (resp) {
            // 401 且本次还没问过 → 问一次、存下来、重放这次请求
            //
            // 只在 401 时问（不是 403）：403 是"密钥是对的，但你没这个权限" ——
            // 那时再弹一次输入框只会让人以为密钥填错了。
            if (resp.status === 401 && !_askedKey) {
                _askedKey = true;
                var input = window.prompt(
                    "后端已开启访问鉴权，请输入访问密钥\n" +
                    "（服务端 .env 里的 API_AUTH_KEY，或 API_AUTH_KEYS 里分给你的那个；\n" +
                    " 管理页需要 API_ADMIN_KEY）");
                if (input && input.trim()) {
                    localStorage.setItem("fw_api_key", input.trim());
                    return window.fetch(url, opts);   // 走一遍包装，这次带上密钥
                }
            }
            return resp;
        });
    };

    // 换密钥后要能重新弹窗：没有这个的话，用户在弹窗里填错一次，
    // 之后整页刷新之前都不会再问，看起来像"密钥改了没生效"
    window.fwResetAskedKey = function () { _askedKey = false; };
})();
