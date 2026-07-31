/**
 * 帧知 - 后台 Service Worker
 * 注入 MAIN world 拦截器（等同 Tampermonkey unsafeWindow）
 */
chrome.runtime.onMessage.addListener(function (msg, sender, sendResponse) {
    if (msg.type !== "inject-interceptor" || !sender.tab || !sender.tab.id) return;

    chrome.scripting.executeScript({
        target: { tabId: sender.tab.id },
        world: "MAIN",
        func: function () {
            console.log("[帧知·MAIN] 拦截器启动");
            var _xhr = XMLHttpRequest.prototype.open;
            XMLHttpRequest.prototype.open = function (m, url) {
                if (url && (url.includes("subtitle") || url.includes("ai_subtitle")) &&
                    url.includes("auth_key") && !url.includes("api.bilibili.com")) {
                    console.log("[帧知·MAIN] 拦截:", url);
                    document.dispatchEvent(new CustomEvent("fw-subtitle", { detail: url }));
                }
                return _xhr.apply(this, arguments);
            };
            var _fetch = window.fetch;
            window.fetch = function (input, init) {
                var url = typeof input === "string" ? input : (input && input.url);
                if (url && (url.includes("subtitle") || url.includes("ai_subtitle")) &&
                    url.includes("auth_key") && !url.includes("api.bilibili.com")) {
                    console.log("[帧知·MAIN] 拦截:", url);
                    document.dispatchEvent(new CustomEvent("fw-subtitle", { detail: url }));
                }
                return _fetch.call(this, input, init);
            };
        }
    }).then(function () {
        console.log("[帧知·BG] MAIN world 注入成功, tab:", sender.tab.id);
        sendResponse({ ok: true });
    }).catch(function (e) {
        console.error("[帧知·BG] 注入失败:", e.message);
        sendResponse({ ok: false, error: e.message });
    });
    return true;
});
