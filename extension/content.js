/**
 * 帧知 - 浏览器插件 v3
 * 统一迷你浮动窗口 + 悬浮按钮 + 后台ServiceWorker字幕拦截
 * 设计系统：CSS 变量驱动深浅色主题，SVG 图标，统一圆角/间距/过渡
 */
(function () {
    "use strict";

    var API_BASE = "http://127.0.0.1:8123";
    var smartMode = false;  // 「智能模型」开关：使用高阶模型
    var host = location.hostname;
    if (!host.includes("bilibili.com") && !host.includes("youtube.com")) return;

    // ── 设计系统（CSS 变量 + 主题）─────────────────────
    var styleEl = document.createElement("style");
    styleEl.id = "fw-style";
    styleEl.textContent =
        ".fw-widget{" +
        "  --fw-bg:#14142b; --fw-surface:#1e1e3e; --fw-surface-2:#26264a;" +
        "  --fw-border:rgba(255,255,255,0.10);" +
        "  --fw-text:#e8eaf3; --fw-text-2:#9aa0b8; --fw-text-3:#6b7188;" +
        "  --fw-primary:#7c6cf0; --fw-primary-strong:#6b5ae0;" +
        "  --fw-accent:#2dd4bf; --fw-danger:#f87171;" +
        "  --fw-shadow:0 12px 40px rgba(0,0,0,0.55);" +
        "  background:var(--fw-bg); color:var(--fw-text);" +
        "}" +
        ".fw-widget.fw-light{" +
        "  --fw-bg:#f6f7fb; --fw-surface:#ffffff; --fw-surface-2:#eceef6;" +
        "  --fw-border:#e3e6ef;" +
        "  --fw-text:#0f172a; --fw-text-2:#475569; --fw-text-3:#94a3b8;" +
        "  --fw-primary:#6b5ae0; --fw-primary-strong:#5a49d6;" +
        "  --fw-accent:#0d9488; --fw-danger:#dc2626;" +
        "  --fw-shadow:0 12px 40px rgba(15,23,42,0.18);" +
        "}" +
        ".fw-widget *{box-sizing:border-box;margin:0;padding:0;}" +
        ".fw-iconbtn{" +
        "  display:flex;align-items:center;justify-content:center;width:28px;height:28px;" +
        "  border-radius:8px;border:none;background:transparent;color:var(--fw-text-2);" +
        "  cursor:pointer;transition:background .18s ease,color .18s ease;flex-shrink:0;" +
        "}" +
        ".fw-iconbtn:hover{background:var(--fw-surface-2);color:var(--fw-text);}" +
        ".fw-iconbtn:focus-visible{outline:2px solid var(--fw-primary);outline-offset:1px;}" +
        ".fws-item{" +
        "  display:flex;align-items:center;gap:10px;padding:9px 10px;border-radius:8px;" +
        "  color:var(--fw-text);cursor:pointer;transition:background .18s ease;font-size:12px;" +
        "}" +
        ".fws-item:hover{background:var(--fw-surface-2);}" +
        ".fws-state{margin-left:auto;font-size:11px;color:var(--fw-text-3);transition:color .18s ease;}" +
        ".fws-state.on{color:var(--fw-accent);}" +
        ".fws-label{color:var(--fw-text-2);}" +
        ".fws-hint{color:var(--fw-text-3);}" +
        ".fw-msg{padding:9px 11px;border-radius:10px;margin-bottom:8px;font-size:12.5px;" +
        "  line-height:1.55;word-break:break-word;min-width:0;max-width:100%;}" +
        ".fw-msg[data-role=system]{background:var(--fw-surface);color:var(--fw-text-2);" +
        "  border:1px solid var(--fw-border);text-align:center;}" +
        ".fw-msg[data-role=user]{background:var(--fw-primary);color:#fff;" +
        "  align-self:flex-end;max-width:85%;}" +
        ".fw-msg[data-role=assistant]{background:var(--fw-surface);border:1px solid var(--fw-border);" +
        "  align-self:stretch;max-width:100%;}" +
        ".fw-msg[data-role=error]{background:var(--fw-danger);color:#fff;align-self:flex-start;max-width:85%;}" +
        ".fw-msg code{background:var(--fw-surface-2);padding:1px 5px;border-radius:4px;font-size:11.5px;}" +
        ".fw-ref-chip{" +
        "  display:inline-block;background:var(--fw-accent);color:#0f172a;padding:1px 7px;" +
        "  border-radius:5px;font-size:10.5px;margin:2px 2px 0 0;cursor:pointer;" +
        "  font-weight:600;transition:transform .12s ease,opacity .18s ease;" +
        "}" +
        ".fw-ref-chip:hover{opacity:.85;transform:translateY(-1px);}" +
        ".fw-progress-track{background:var(--fw-surface-2);border-radius:10px;height:8px;overflow:hidden;max-width:280px;margin:0 auto;}" +
        ".fw-progress-bar{background:linear-gradient(90deg,var(--fw-primary),var(--fw-accent));height:100%;border-radius:10px;transition:width .5s ease;}" +
        "@media (prefers-reduced-motion:reduce){" +
        "  .fw-iconbtn,.fws-item,.fws-state,.fw-ref-chip,.fw-progress-bar{transition:none;}" +
        "}" +
        "#fw-msgs{scrollbar-width:thin;scrollbar-color:var(--fw-primary) transparent;}" +
        "#fw-msgs::-webkit-scrollbar{width:6px;}" +
        "#fw-msgs::-webkit-scrollbar-track{background:transparent;}" +
        "#fw-msgs::-webkit-scrollbar-thumb{background:var(--fw-primary);border-radius:3px;}" +
        "#fw-msgs::-webkit-scrollbar-thumb:hover{background:var(--fw-primary-strong);}";
    document.head.appendChild(styleEl);

    // ── SVG 图标（Heroicons/Lucide 风格）────────────────
    function icon(name, size) {
        size = size || 16;
        var paths = {
            film: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 8h4M3 12h4M3 16h4M17 8h4M17 12h4M17 16h4"/>',
            clock: '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l4 2"/>',
            help: '<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><path d="M12 17h.01"/>',
            gear: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9L17 7M7 17l-2.1 2.1"/>',
            minus: '<path d="M6 12h12"/>',
            close: '<path d="M18 6 6 18M6 6l12 12"/>',
            zap: '<path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/>',
            spark: '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/>',
            refresh: '<path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/><path d="M3 21v-5h5"/>',
            copy: '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
            chart: '<path d="M3 3v18h18"/><path d="M7 15l4-4 4 3 5-6"/>',
            moon: '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
            folder: '<path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2z"/>',
            send: '<path d="M22 2 11 13M22 2l-7 20-4-9-9-4z"/>'
        };
        return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" style="width:' + size + 'px;height:' + size + 'px;display:block;">' + (paths[name] || '') + '</svg>';
    }

    // ── 浮动按钮 ──
    var trigger = document.createElement("div");
    trigger.id = "fw-trigger";
    trigger.innerHTML = icon('film', 22);
    trigger.style.cssText = "position:fixed;right:16px;bottom:120px;width:48px;height:48px;" +
        "background:linear-gradient(135deg,#7c6cf0,#2dd4bf);border-radius:50%;" +
        "display:flex;align-items:center;justify-content:center;color:#fff;" +
        "cursor:grab;z-index:2147483647;box-shadow:0 6px 20px rgba(108,92,231,0.5);" +
        "user-select:none;transition:transform .18s ease,box-shadow .18s ease;";
    trigger.addEventListener("mouseenter", function () { trigger.style.transform = "scale(1.06)"; trigger.style.boxShadow = "0 8px 26px rgba(108,92,231,0.6)"; });
    trigger.addEventListener("mouseleave", function () { trigger.style.transform = "scale(1)"; trigger.style.boxShadow = "0 6px 20px rgba(108,92,231,0.5)"; });
    document.body.appendChild(trigger);

    // ── 触发按钮拖拽 ──
    var dragState = null, dragRaf = null, hasDragged = false;
    trigger.addEventListener("mousedown", function (e) {
        if (e.button !== 0) return;
        var rect = trigger.getBoundingClientRect();
        dragState = { sx: e.clientX, sy: e.clientY, x: rect.left, y: rect.top };
        hasDragged = false; e.preventDefault();
    });
    document.addEventListener("mousemove", function (e) {
        if (!dragState || dragRaf) return;
        dragRaf = requestAnimationFrame(function () {
            dragRaf = null;
            var dx = e.clientX - dragState.sx, dy = e.clientY - dragState.sy;
            if (Math.abs(dx) > 3 || Math.abs(dy) > 3) hasDragged = true;
            if (!hasDragged) return;
            var l = Math.max(0, Math.min(window.innerWidth - 48, dragState.x + dx));
            var t = Math.max(0, Math.min(window.innerHeight - 48, dragState.y + dy));
            trigger.style.left = l + "px"; trigger.style.top = t + "px";
            trigger.style.right = "auto"; trigger.style.bottom = "auto";
        });
    });
    document.addEventListener("mouseup", function () {
        if (dragState && !hasDragged) showMini();
        dragState = null;
    });

    // ── 迷你窗口 ──
    var mini = document.createElement("div");
    mini.id = "fw-mini";
    mini.className = "fw-widget";
    mini.style.cssText = "display:none;position:fixed;right:12px;top:80px;width:340px;" +
        "height:400px;min-height:200px;border:1px solid var(--fw-border);border-radius:14px;z-index:2147483646;" +
        "flex-direction:column;overflow:hidden;font:13px system-ui,'PingFang SC',sans-serif;" +
        "box-shadow:var(--fw-shadow);";
    mini.innerHTML =
        '<div id="fw-head" style="display:flex;align-items:center;gap:6px;padding:9px 12px;border-bottom:1px solid var(--fw-border);cursor:move;user-select:none;flex-shrink:0;">' +
        '<span style="display:flex;align-items:center;gap:7px;font-weight:700;pointer-events:none;color:var(--fw-primary);">' + icon('film', 16) + '帧知</span>' +
        '<span id="fw-status" style="flex:1;font-size:11px;color:var(--fw-text-2);pointer-events:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"></span>' +
        '<button class="fw-iconbtn" id="fw-proc" title="手动处理">' + icon('refresh') + '</button>' +
        '<button class="fw-iconbtn" id="fw-hist" title="历史">' + icon('clock') + '</button>' +
        '<button class="fw-iconbtn" id="fw-quiz" title="考考我">' + icon('help') + '</button>' +
        '<button class="fw-iconbtn" id="fw-settings-btn" title="设置">' + icon('gear') + '</button>' +
        '<button class="fw-iconbtn" id="fw-min" title="缩小">' + icon('minus') + '</button>' +
        '<button class="fw-iconbtn" id="fw-cls" title="关闭">' + icon('close') + '</button>' +
        '</div>' +
        '<div id="fw-msgs" style="flex:1;overflow-y:auto;overflow-x:hidden;padding:12px;display:flex;flex-direction:column;max-height:360px;min-height:60px;"></div>' +
        '<div style="display:flex;align-items:center;gap:6px;padding:4px 12px;border-top:1px solid var(--fw-border);flex-shrink:0;">' +
        '<span id="fw-mode-lbl" style="font-size:10px;color:var(--fw-text-3);font-weight:600;">文本</span>' +
        '<span id="fw-time" style="margin-left:auto;font-size:11px;color:var(--fw-accent);font-family:monospace;">00:00</span>' +
        '</div>' +
        '<div style="display:flex;gap:6px;padding:8px 12px 12px;flex-shrink:0;">' +
        '<textarea id="fw-input" placeholder="输入问题..." disabled rows="1" style="flex:1;padding:9px 11px;background:var(--fw-surface);border:1px solid var(--fw-border);border-radius:9px;color:var(--fw-text);font-size:12.5px;outline:none;resize:none;max-height:80px;line-height:1.4;font-family:inherit;overflow:hidden;transition:border-color .18s ease;"></textarea>' +
        '<button id="fw-send" disabled style="padding:0 14px;background:var(--fw-primary);border:none;border-radius:9px;color:#fff;font-weight:600;font-size:12.5px;cursor:pointer;white-space:nowrap;transition:background .18s ease,opacity .18s ease;">发送</button>' +
        '</div>' +
        '<div id="fw-rsz" style="position:absolute;right:0;bottom:0;width:14px;height:14px;cursor:se-resize;overflow:hidden;">' +
        '<svg viewBox="0 0 16 16" style="width:14px;height:14px;display:block;"><path d="M0 16L16 0v16H0z" fill="var(--fw-text-3)"/></svg></div>';
    document.body.appendChild(mini);
    mini.addEventListener("wheel", function (e) { e.stopPropagation(); });
    mini.querySelector("#fw-input").addEventListener("focus", function () { this.style.borderColor = "var(--fw-primary)"; });
    mini.querySelector("#fw-input").addEventListener("blur", function () { this.style.borderColor = "var(--fw-border)"; });
    var sendBtn = mini.querySelector("#fw-send");
    sendBtn.addEventListener("mouseenter", function () { if (!sendBtn.disabled) sendBtn.style.background = "var(--fw-primary-strong)"; });
    sendBtn.addEventListener("mouseleave", function () { if (!sendBtn.disabled) sendBtn.style.background = "var(--fw-primary)"; });

    // ── 窗口拖拽 + 缩放 ──
    (function () { var d = null, r = null, m = false; document.getElementById("fw-head").addEventListener("mousedown", function (e) { if (e.target.closest(".fw-iconbtn")) return; var rect = mini.getBoundingClientRect(); d = { sx: e.clientX, sy: e.clientY, x: rect.left, y: rect.top }; m = false; e.preventDefault(); }); document.addEventListener("mousemove", function (e) { if (!d || r) return; r = requestAnimationFrame(function () { r = null; var dx = e.clientX - d.sx, dy = e.clientY - d.sy; if (Math.abs(dx) > 2 || Math.abs(dy) > 2) m = true; if (!m) return; var l = Math.max(0, Math.min(window.innerWidth - 340, d.x + dx)); var t = Math.max(0, Math.min(window.innerHeight - 100, d.y + dy)); mini.style.right = "auto"; mini.style.top = "auto"; mini.style.left = l + "px"; mini.style.top = t + "px"; }); }); document.addEventListener("mouseup", function () { d = null; }); })();
    (function () { var d = null, r = null; document.getElementById("fw-rsz").addEventListener("mousedown", function (e) { d = { sx: e.clientX, sy: e.clientY, w: mini.offsetWidth, h: mini.offsetHeight }; e.preventDefault(); e.stopPropagation(); }); document.addEventListener("mousemove", function (e) { if (!d || r) return; r = requestAnimationFrame(function () { r = null; var w = Math.max(260, Math.min(600, d.w + (e.clientX - d.sx))); var h = Math.max(200, Math.min(window.innerHeight * 0.9, d.h + (e.clientY - d.sy))); mini.style.width = w + "px"; mini.style.height = h + "px"; mini.style.maxHeight = "none"; var m = document.getElementById("fw-msgs"); if (m) m.style.maxHeight = "none"; }); }); document.addEventListener("mouseup", function () { d = null; }); })();

    // ── 全屏处理 ──
    document.addEventListener("fullscreenchange", function () {
        if (document.fullscreenElement) { document.fullscreenElement.appendChild(trigger); document.fullscreenElement.appendChild(mini); trigger.style.right = "12px"; trigger.style.bottom = "80px"; trigger.style.left = "auto"; trigger.style.top = "auto"; }
        else { document.body.appendChild(trigger); if (mini.parentNode !== document.body) document.body.appendChild(mini); }
    });

    function showMini() { if (window._fwReady) { loadHistory(); } else if (!videoId) { initVideo(); } else { var c = document.getElementById("fw-msgs"); if (c && !c.innerHTML.trim()) c.innerHTML = '<div style="text-align:center;padding:30px 20px;color:var(--fw-text-2);font-size:12px;line-height:2;">💡 点击播放器的 <b style="color:#00a1d6;">AI字幕</b>，获取精准回答</div>'; } mini.style.display = "flex"; trigger.style.display = "none"; }
    function hideMini() { mini.style.display = "none"; trigger.style.display = "flex"; }

    // ── 事件绑定 ──
    document.getElementById("fw-proc").onclick = doManualProcess;
    document.getElementById("fw-cls").onclick = hideMini;
    document.getElementById("fw-min").onclick = hideMini;
    document.getElementById("fw-quiz").onclick = function () { if (!window._fwReady) { addMsg("system", "⏳ 等待就绪"); return; } addMsg("system", "🤔 出题中..."); fetch(API_BASE + "/api/videos/" + videoId + "/quiz", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ timestamp: getCurrentTime() }) }).then(function (r) { return r.json(); }).then(function (data) { var h = '<b>📝 小测验 (' + data.context_time + ')：</b><br><br>'; data.questions.forEach(function (q, i) { h += '<div style="margin-bottom:8px;"><b>' + (i + 1) + '. ' + esc(q.question) + '</b>'; h += '<div style="margin-top:3px;cursor:pointer;color:var(--fw-accent);font-size:11px;" onclick="this.nextElementSibling.style.display=this.nextElementSibling.style.display==\'block\'?\'none\':\'block\'">💡 查看答案</div>'; h += '<div style="display:none;background:var(--fw-surface-2);padding:6px 10px;border-radius:6px;margin-top:3px;font-size:12px;border-left:3px solid var(--fw-accent);">' + esc(q.answer) + '</div></div>'; }); addMsg("assistant", h); }).catch(function (e) { addMsg("error", e.message || "请求失败"); }); };
    var isHistView = false;
    document.getElementById("fw-hist").onclick = function () {
        if (isHistView) {
            var _c = document.getElementById("fw-msgs"); _c.innerHTML = "";
            isHistView = false;
            if (window._fwReady && videoId) { loadHistory(); }
            else if (videoId) { _c.innerHTML = '<div style="text-align:center;padding:30px 20px;color:var(--fw-text-2);font-size:12px;">⏳ 视频处理中...</div>'; }
            else { _c.innerHTML = '<div style="text-align:center;padding:30px 20px;color:var(--fw-text-2);font-size:12px;">💡 点击 AI字幕 建立索引</div>'; }
            return;
        }
        isHistView = true;
        fetch(API_BASE + "/api/conversations").then(function (r) { return r.json(); }).then(function (data) { var c = document.getElementById("fw-msgs"); c.innerHTML = ""; if (!data.length) { addMsg("system", "暂无历史"); return; } var h = '<div style="font-size:13px;font-weight:600;padding:0 0 8px;border-bottom:1px solid var(--fw-border);margin-bottom:8px;">📋 历史对话</div>'; data.forEach(function (cv) { h += '<div data-vid="' + esc(cv.video_id) + '" style="padding:9px 11px;border-radius:8px;background:var(--fw-surface);border:1px solid var(--fw-border);cursor:pointer;margin-bottom:6px;transition:background .18s ease;" onmouseenter="this.style.background=\'var(--fw-surface-2)\'" onmouseleave="this.style.background=\'var(--fw-surface)\'"><div style="font-size:12px;">' + esc(cv.title) + '</div><div style="font-size:10px;color:var(--fw-text-3);margin-top:2px;">' + cv.msg_count + '条 · ' + (cv.last_time || '').slice(0, 10) + '</div></div>'; }); c.innerHTML = h; c.scrollTop = 0; c.querySelectorAll("[data-vid]").forEach(function (el) { el.onclick = function () { window._fwReady = false; videoId = this.dataset.vid; fetch(API_BASE + "/api/videos/" + videoId).then(function (r) { return r.json(); }).then(function (info) { if (info.status === "ready") { window._fwReady = true; updateStatus("✅ 就绪"); document.getElementById("fw-input").disabled = false; document.getElementById("fw-send").disabled = false; loadHistory(); } else { updateStatus("⏳ 重新处理..."); initVideo(); } }); }; }); }).catch(function () { addMsg("error", "加载失败"); }); };
    document.getElementById("fw-send").onclick = sendQuestion;
    var smartConfigured = null;  // null=未知，true/false=后端告知（面板智能模型开关用）

    // ── 独立可移动「设置」窗口 ──
    var settingsBtn = document.getElementById("fw-settings-btn");
    var setwin = document.createElement("div");
    setwin.id = "fw-setwin";
    setwin.className = "fw-widget";
    setwin.style.cssText = "position:fixed;z-index:2147483645;border:1px solid var(--fw-border);border-radius:12px;box-shadow:var(--fw-shadow);display:none;width:240px;font-size:12px;";
    setwin.innerHTML =
        '<div id="fw-set-head" style="display:flex;align-items:center;gap:6px;padding:9px 12px;border-bottom:1px solid var(--fw-border);cursor:move;user-select:none;">' +
        '<span style="display:flex;align-items:center;gap:7px;font-weight:600;">' + icon('gear', 15) + '设置</span><span style="flex:1;"></span>' +
        '<button class="fw-iconbtn" id="fw-set-close" title="关闭">' + icon('close', 14) + '</button></div>' +
        '<div style="padding:6px;">' +
        '<div class="fws-item" data-act="smart">' + icon('spark') + '<span>智能模型</span><span class="fws-state" data-for="smart">关</span></div>' +
        '<div id="fw-smart-hint" style="display:none;padding:2px 10px 8px;font-size:10.5px;color:var(--fw-danger);line-height:1.4;">⚠️ 智能模型未配置（请去 .env 配置 SMART_LLM_API_KEY），本次仍使用默认模型</div>' +
        '<div class="fws-item" data-act="auto">' + icon('zap') + '<span>自动处理</span><span class="fws-state" data-for="auto">关</span></div>' +
        '<div class="fws-item" data-act="copy">' + icon('copy') + '<span>复制对话</span></div>' +
        '<div class="fws-item" data-act="usage">' + icon('chart') + '<span>当前视频用量</span></div>' +
        '<div class="fws-item" data-act="usage_today">' + icon('chart') + '<span>当天用量</span></div>' +
        '<div class="fws-item" data-act="theme">' + icon('moon') + '<span>主题切换</span></div>' +
        '<div style="padding:10px;border-top:1px solid var(--fw-border);margin-top:4px;">' +
        '<div class="fws-label" style="margin-bottom:6px;display:flex;align-items:center;gap:7px;">' + icon('folder', 14) + '笔记保存目录<span id="fw-note-dir" style="margin-left:auto;color:var(--fw-accent);word-break:break-all;max-width:55%;text-align:right;">加载中…</span></div>' +
        '<div style="display:flex;gap:4px;">' +
        '<input id="fw-note-input" placeholder="后端文件路径" style="flex:1;padding:7px 8px;background:var(--fw-surface);border:1px solid var(--fw-border);border-radius:8px;color:var(--fw-text);font-size:11.5px;outline:none;">' +
        '<button id="fw-note-save" style="padding:7px 10px;background:var(--fw-primary);border:none;border-radius:8px;color:#fff;cursor:pointer;font-size:11.5px;">保存</button></div>' +
        '<div class="fws-hint" style="margin-top:6px;font-size:10px;">笔记由 Agent 写入后端笔记目录（write_file 工具）</div></div>' +
        '</div>';
    setwin.style.left = (window.innerWidth - 280) + "px";
    setwin.style.top = "110px";
    document.body.appendChild(setwin);
    var noteDir = document.getElementById("fw-note-dir");
    var noteInput = document.getElementById("fw-note-input");
    // 加载后端当前 NOTE_DIR
    fetch(API_BASE + "/api/note_dir").then(function (r) { return r.json(); })
        .then(function (d) { noteDir.textContent = d.note_dir; noteInput.value = d.note_dir; })
        .catch(function () { noteDir.textContent = "获取失败"; });
    document.getElementById("fw-note-save").onclick = function (ev) {
        ev.stopPropagation();
        var path = noteInput.value.trim();
        if (!path) { addMsg("error", "路径不能为空"); return; }
        fetch(API_BASE + "/api/note_dir", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ note_dir: path }) })
            .then(function (r) { return r.json(); })
            .then(function (d) { noteDir.textContent = d.note_dir; addMsg("system", "📁 笔记保存目录已设置：" + d.note_dir); })
            .catch(function () { addMsg("error", "设置失败"); });
    };

    function setAutoState() {
        var s = setwin.querySelector('.fws-state[data-for="auto"]');
        if (s) { s.textContent = autoMode ? "开" : "关"; s.style.color = autoMode ? "var(--fw-accent)" : "var(--fw-text-3)"; s.classList.toggle("on", autoMode); }
    }
    function toggleAuto() {
        autoMode = !autoMode;
        localStorage.setItem("fw_auto", autoMode);
        setAutoState();
        addMsg("system", autoMode ? "🤖 自动处理：已开启" : "🤖 自动处理：已关闭");
    }
    function setSmartState() {
        var s = setwin.querySelector('.fws-state[data-for="smart"]');
        var hint = document.getElementById("fw-smart-hint");
        if (smartConfigured === false) {
            if (s) { s.textContent = "未配置"; s.style.color = "var(--fw-danger)"; s.classList.remove("on"); }
            if (hint) hint.style.display = "block";
        } else {
            if (s) { s.textContent = smartMode ? "开" : "关"; s.style.color = smartMode ? "var(--fw-accent)" : "var(--fw-text-3)"; s.classList.toggle("on", smartMode); }
            if (hint) hint.style.display = "none";
        }
    }
    function toggleSmart() {
        function apply() {
            if (!smartConfigured) {
                addMsg("error", "⚠️ 智能模型未配置（请去 .env 配置 SMART_LLM_API_KEY），本次仍使用默认模型");
                setSmartState();
                return;
            }
            smartMode = !smartMode;
            setSmartState();
            addMsg("system", smartMode ? "🧠 已切换到智能模型" : "📝 已切回默认模型");
        }
        if (smartConfigured === null) {
            addMsg("error", "⚠️ 无法获取智能模型配置，请确认服务已启动");
            return;
        }
        apply();
    }
    function doManualProcess() {
        videoId = null; window._fwReady = false;
        updateStatus("⏳ 手动处理...");
        document.getElementById("fw-input").disabled = true;
        document.getElementById("fw-send").disabled = true;
        document.getElementById("fw-msgs").innerHTML = "";
        lastUrl = cleanUrl(location.href);
        initVideo(true);
    }

    var isLight = false;
    function applyTheme() {
        mini.classList.toggle("fw-light", isLight);
        setwin.classList.toggle("fw-light", isLight);
    }
    function toggleTheme() {
        isLight = !isLight;
        applyTheme();
        addMsg("system", isLight ? "🌗 已切换浅色主题" : "🌙 已切回深色主题");
    }

    // 设置窗口开关 + 拖拽 + 项分发
    settingsBtn.onclick = function (ev) {
        ev.stopPropagation();
        if (setwin.style.display === "block") { setwin.style.display = "none"; return; }
        setAutoState(); setSmartState();
        setwin.style.display = "block";
    };
    document.getElementById("fw-set-close").onclick = function () { setwin.style.display = "none"; };

    (function () {
        var d = null, r = null;
        document.getElementById("fw-set-head").addEventListener("mousedown", function (e) {
            if (e.target.closest(".fw-iconbtn")) return;
            d = { sx: e.clientX, sy: e.clientY, x: setwin.offsetLeft, y: setwin.offsetTop };
            e.preventDefault();
        });
        document.addEventListener("mousemove", function (e) {
            if (!d || r) return;
            r = requestAnimationFrame(function () { r = null;
                setwin.style.left = (d.x + (e.clientX - d.sx)) + "px";
                setwin.style.top = (d.y + (e.clientY - d.sy)) + "px";
            });
        });
        document.addEventListener("mouseup", function () { d = null; });
    })();

    setwin.querySelectorAll(".fws-item").forEach(function (it) {
        it.onclick = function (ev) {
            ev.stopPropagation();
            var act = it.getAttribute("data-act");
            if (act === "auto") toggleAuto();
            else if (act === "smart") toggleSmart();
            else if (act === "copy") {
                var body = document.getElementById("fw-msgs").innerText;
                if (!body.trim()) { addMsg("system", "暂无对话可复制"); return; }
                addMsg("system", "📄 复制中…");
                var done = function () { addMsg("system", "✅ 已复制当前对话"); };
                var fail = function () { addMsg("error", "复制失败，请手动选择复制"); };
                (navigator.clipboard ? navigator.clipboard.writeText("[帧知对话记录]\n" + body) : Promise.reject()).then(done).catch(fail);
            } else if (act === "usage") {
                if (!videoId) { addMsg("error", "当前没有视频"); return; }
                fetch(API_BASE + "/api/usage/video/" + videoId).then(function (r) { return r.json(); })
                    .then(function (d) { addMsg("system", "📊 当前视频用量：" + (d.calls || 0) + " 次 · " +
                        (d.total_input_tokens || 0) + " in / " + (d.total_output_tokens || 0) + " out tokens · ¥" + (d.total_cost || 0)); })
                    .catch(function () { addMsg("error", "获取用量失败"); });
            } else if (act === "usage_today") {
                fetch(API_BASE + "/api/usage/today").then(function (r) { return r.json(); })
                    .then(function (d) { addMsg("system", "📊 当天用量：" + (d.calls || 0) + " 次 · " +
                        (d.total_input_tokens || 0) + " in / " + (d.total_output_tokens || 0) + " out tokens · ¥" + (d.total_cost || 0)); })
                    .catch(function () { addMsg("error", "获取用量失败"); });
            } else if (act === "theme") toggleTheme();
        };
    });
    // 点窗口外关闭
    document.addEventListener("click", function (e) {
        if (setwin.style.display === "block" && !setwin.contains(e.target) && e.target.id !== "fw-settings-btn") {
            setwin.style.display = "none";
        }
    });

    var inp = document.getElementById("fw-input");
    inp.onkeydown = function (e) { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendQuestion(); } };
    inp.oninput = function () { inp.style.height = "auto"; inp.style.height = Math.min(inp.scrollHeight, 80) + "px"; };

    // ── 状态变量 ──
    var videoId = null, autoMode = localStorage.getItem("fw_auto") !== "false", isProcessing = false;
    var lastUrl = cleanUrl(location.href);
    window._fwReady = false;
    setAutoState();  // 初始化顶栏「自动处理」按钮状态

    // 页面加载时预读 smart 配置一次，之后点击智能模型不再发请求验证
    fetch(API_BASE + "/api/llm_config").then(function (r) { return r.json(); })
        .then(function (d) { smartConfigured = !!d.smart_configured; })
        .catch(function () {});

    // ── 初始化 ──
    if (autoMode) { initVideo(); } else { addMsg("system", '<div style="text-align:center;">💡 点击 <b style="color:#00a1d6;">AI字幕</b>，获取精准回答<br/>自动处理已关闭，点击顶栏 🔄 手动处理</div>'); }

    // ── B站字幕采集 ──
    var _subDone = false;

    chrome.runtime.sendMessage({ type: "inject-interceptor" }, function (resp) {
        console.log("[帧知] MAIN world 注入:", resp);
    });

    document.addEventListener("fw-subtitle", function (e) {
        // 字幕拦截到只上传缓存，不自动触发索引（等用户手动或autoMode）
        var url = e.detail;
        if (_subDone) return;
        if (!videoId) { initVideo(); }
        console.log("[帧知] 字幕已拦截，上传缓存:", url.substring(0,80)+"...");
        // 携带真实页面 Referer，否则后端下载 ai_subtitle 会 403
        uploadSubtitleUrl(url, cleanUrl(location.href));
    });

    function uploadSubtitleUrl(url, referer) {
        if (!url || _subDone) return;
        _subDone = true;
        if (!videoId) { initVideo(); }
        (function waitAndUpload() {
            if (!videoId) { setTimeout(waitAndUpload, 500); return; }
            fetch(API_BASE + "/api/videos/" + videoId + "/captured_subtitles_url", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ subtitle_url: url, referer: referer || cleanUrl(location.href) }),
            }).then(function () {
                updateStatus("📝 字幕已缓存");
            }).catch(function () {});
        })();
    }

    // 3. DOM script 标签扫描（兜底）
    setInterval(function () {
        var scripts = document.querySelectorAll("script");
        for (var i = 0; i < scripts.length; i++) {
            var t = scripts[i].textContent || scripts[i].innerHTML || "";
            if (!t) continue;
            var m = t.match(/https?:\/\/[^\s\"<>]*(?:subtitle|ai_subtitle)\/[^\s\"<>]*\?auth_key=[^\s\"<>]+/g);
            if (m) for (var j = 0; j < m.length; j++) uploadSubtitleUrl(m[j]);
        }
    }, 2000);

    // ── 工具函数 ──
    function cleanUrl(url) { try { var u = new URL(url); var s = ""; ["v", "p"].forEach(function (k) { if (u.searchParams.has(k)) s += (s ? "&" : "") + k + "=" + u.searchParams.get(k); }); return u.origin + u.pathname + (s ? "?" + s : ""); } catch (e) { return url; } }
    function getCurrentTime() { try { if (window.player && window.player.getCurrentTime) return window.player.getCurrentTime(); } catch (e) { } var v = document.querySelector("video"); return v ? v.currentTime : 0; }
    function updateStatus(t) { var e = document.getElementById("fw-status"); if (e) e.textContent = t; }
    function esc(t) { var d = document.createElement("div"); d.textContent = t; return d.innerHTML; }
    function renderMd(t) {
        // 简单Markdown渲染
        t = esc(t);
        t = t.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');                 // **加粗**
        t = t.replace(/^- (.+)$/gm, '<li style="margin-left:16px;">$1</li>');  // 列表
        t = t.replace(/`([^`]+)`/g, '<code>$1</code>');
        t = t.replace(/\n\n/g, '<br><br>');
        return t;
    }
    function fmt(s) { var m = Math.floor(s / 60), sec = Math.floor(s % 60); return String(m).padStart(2, "0") + ":" + String(sec).padStart(2, "0"); }
    function addMsg(type, content) { var c = document.getElementById("fw-msgs"), div = document.createElement("div"); div.className = "fw-msg"; div.setAttribute("data-role", type); div.innerHTML = content; c.appendChild(div); c.scrollTop = c.scrollHeight; return div; }

    // ── 定时任务 ──
    setInterval(function () { if (!window._fwReady) return; var t = getCurrentTime(); document.getElementById("fw-time").textContent = fmt(t); var paused = (document.querySelector("video") || {}).paused; var lbl = document.getElementById("fw-mode-lbl"); if (lbl) { lbl.textContent = paused ? "画面" : "文本"; lbl.style.color = paused ? "var(--fw-accent)" : "var(--fw-text-3)"; } }, 1000);
    setInterval(function () { var cur = cleanUrl(location.href); if (cur !== lastUrl) { lastUrl = cur; if (autoMode) { videoId = null; window._fwReady = false; updateStatus("⏳ 新视频..."); document.getElementById("fw-input").disabled = true; document.getElementById("fw-send").disabled = true; document.getElementById("fw-msgs").innerHTML = ""; initVideo(); } else { addMsg("system", "🔔 检测到新视频，点击顶栏 🔄 手动处理"); } } }, 2000);
    var isOffline = false; setInterval(function () { fetch(API_BASE + "/api/health").then(function () { if (isOffline) { isOffline = false; updateStatus("✅ 已重连"); } }).catch(function () { if (!isOffline) { isOffline = true; updateStatus("⚠️ 断线"); } }); }, 10000);

    // ── 视频处理 ──
    function initVideo(force) { updateStatus("⏳ 建立索引..."); updateProgress({ progress: 2, progress_text: "连接服务..." }); var body = { url: lastUrl }; if (force) body.force = true; fetch(API_BASE + "/api/videos/from_url", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then(function (r) { return r.json(); }).then(function (data) { videoId = data.video_id; if (data.status === "ready") { readyState(data); return; } if (data.status === "subtitles") { updateStatus("📝 字幕已缓存"); updateProgress({progress: 30, progress_text: "字幕已缓存，点击 ⚙️ 处理"}); return; } updateStatus("⏳ 处理中..."); pollStatus(); }).catch(function (e) { updateStatus("❌ 连接失败"); }); }
    function pollStatus() { (function check() { if (!videoId) { setTimeout(check, 3000); return; } fetch(API_BASE + "/api/videos/" + videoId).then(function (r) { return r.json(); }).then(function (data) { if (data.status === "ready") { readyState(data); return; } if (data.status === "error") { updateStatus("❌ 失败"); return; } if (data.status === "subtitles") { updateStatus("📝 字幕已缓存"); updateProgress({progress: 30, progress_text: "字幕已缓存，点击 ⚙️ 处理"}); setTimeout(check, 2000); return; } if (data.progress) updateProgress(data); setTimeout(check, 2000); }).catch(function () { setTimeout(check, 5000); }); })(); }
    function readyState(data) { window._fwReady = true; updateStatus("✅ 就绪 (" + (data.chunk_count || "?") + "片段)"); document.getElementById("fw-input").disabled = false; document.getElementById("fw-send").disabled = false; document.getElementById("fw-msgs").innerHTML = '<div style="color:var(--fw-accent);text-align:center;padding:20px 0;">✅ 视频已就绪，开始提问吧！</div>'; loadHistory(); }
    function updateProgress(data) { var pct = data.progress || 0; var text = data.progress_text || "处理中..."; updateStatus(text + " " + pct + "%"); var c = document.getElementById("fw-msgs"); if (c) { var hint = pct < 40 ? '<div style="text-align:center;font-size:11px;color:var(--fw-text-3);margin-bottom:8px;">💡 点击 <b style="color:#00a1d6;">AI字幕</b>，获取精准回答</div>' : ''; c.innerHTML = '<div style="text-align:center;padding:20px 0;">' + hint + '<div style="font-size:13px;color:var(--fw-text-2);margin-bottom:10px;">' + text + '</div><div class="fw-progress-track"><div class="fw-progress-bar" style="width:' + pct + '%;"></div></div><div style="font-size:12px;color:var(--fw-text-3);margin-top:6px;">' + pct + '%</div></div>'; } }
    function loadHistory() { isHistView = false; fetch(API_BASE + "/api/videos/" + videoId + "/history?limit=30").then(function (r) { return r.json(); }).then(function (data) { if (!data || !data.length) { var _m = document.getElementById("fw-msgs"); if (!_m.innerHTML.trim()) _m.innerHTML = '<div style="text-align:center;padding:30px 20px;color:var(--fw-text-2);font-size:12px;">暂无对话，开始提问吧</div>'; return; } var c = document.getElementById("fw-msgs"); c.innerHTML = ""; data.forEach(function (m) { if (m.role === "user") { addMsg("user", esc(m.content)); } else if (m.role === "assistant") { addMsg("assistant", renderMd(m.content) + renderRefs(m.references)); } }); }).catch(function () { }); }

    // ── 发送问题 ──
    function renderRefs(refs) {
        if (!refs || !refs.length) return "";
        var h = '<div style="margin-top:8px;font-size:11px;color:var(--fw-text-3);">📖 ';
        var seen = {};
        refs.forEach(function (ref) {
            var k = ref.start_time + "-" + ref.end_time;
            if (seen[k]) return;
            seen[k] = true;
            var s = ref.start_time;
            h += '<span class="fw-ref-chip" onclick="try{if(window.player&&window.player.seek)window.player.seek(' + s + ');else{var v=document.querySelector(\'video\');if(v)v.currentTime=' + s + ';}}catch(e){}">' + fmt(ref.start_time) + "~" + fmt(ref.end_time) + "</span> ";
        });
        return h + "</div>";
    }

    function sendQuestion() {
        if (isHistView) { document.getElementById("fw-msgs").innerHTML = ""; isHistView = false; }
        if (!videoId) return;
        var inp = document.getElementById("fw-input"), q = inp.value.trim();
        if (!q || !window._fwReady || isProcessing) return;
        isProcessing = true;
        inp.disabled = true;
        document.getElementById("fw-send").disabled = true;
        inp.value = "";
        inp.style.height = "auto";
        var paused = (document.querySelector("video") || {}).paused;
        var isVision = paused, t = getCurrentTime();
        addMsg("user", (isVision ? "🖼️ " : "") + q);

        function finish() { inp.disabled = false; document.getElementById("fw-send").disabled = false; inp.focus(); isProcessing = false; }

        // 走 agent 流式接口（SSE：token / tool / done / error）
        var body2 = { question: q, timestamp: t, smart: smartMode };
        var bubble = addMsg("assistant", '<span style="color:var(--fw-text-3);">🤖 Agent 思考中...</span>');
        var full = "", toolLog = [];
        function renderAgent() {
            var h = "";
            if (toolLog.length) h += '<div style="font-size:10.5px;color:var(--fw-text-3);margin-bottom:6px;">🛠️ ' + esc(toolLog.join(" → ")) + '</div>';
            h += renderMd(full);
            return h;
        }
        fetch(API_BASE + "/api/videos/" + videoId + "/ask_agent_stream", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body2) })
            .then(function (r) {
                if (!r.ok) { throw new Error("HTTP " + r.status); }
                var reader = r.body.getReader();
                var decoder = new TextDecoder();
                var buffer = "";
                function pump() {
                    return reader.read().then(function (result) {
                        if (result.done) { finish(); return; }
                        buffer += decoder.decode(result.value, { stream: true });
                        var lines = buffer.split("\n");
                        buffer = lines.pop();
                        for (var i = 0; i < lines.length; i++) {
                            var line = lines[i];
                            if (line.indexOf("data: ") !== 0) continue;
                            try {
                                var d = JSON.parse(line.substring(6));
                                if (d.token) { full += d.token; bubble.innerHTML = renderAgent(); }
                                else if (d.tool) { toolLog.push(d.tool); bubble.innerHTML = renderAgent(); }
                                else if (d.done) { bubble.innerHTML = renderAgent(); }
                                else if (d.error) { bubble.innerHTML = "❌ " + esc(d.error); }
                            } catch (e) {}
                        }
                        return pump();
                    });
                }
                return pump();
            })
            .catch(function (e) { bubble.innerHTML = "❌ " + ((e && e.message) || "请求失败"); finish(); });
    }
    function captureFrame() { try { var v = document.querySelector("video"); if (!v || v.videoWidth === 0) return null; var c = document.createElement("canvas"); c.width = v.videoWidth; c.height = v.videoHeight; c.getContext("2d").drawImage(v, 0, 0); return c.toDataURL("image/jpeg", 0.7).replace(/^data:image\/jpeg;base64,/, ""); } catch (e) { return null; } }

    console.log("[帧知] v3 loaded (with ServiceWorker subtitle capture)");
})();
