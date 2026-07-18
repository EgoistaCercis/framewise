/**
 * 帧知 - 浏览器插件 v2
 * 统一迷你浮动窗口 + 悬浮按钮，正常/全屏模式共用
 */
(function () {
    "use strict";

    var API_BASE = "http://127.0.0.1:8000";
    var host = location.hostname;
    if (!host.includes("bilibili.com") && !host.includes("youtube.com")) return;

    // ── 浮动按钮 ──
    var trigger = document.createElement("div");
    trigger.id = "fw-trigger";
    trigger.textContent = "🎬";
    trigger.style.cssText = "position:fixed;right:16px;bottom:120px;width:48px;height:48px;" +
        "background:linear-gradient(135deg,#6c5ce7,#00d2a0);border-radius:50%;" +
        "display:flex;align-items:center;justify-content:center;font-size:22px;" +
        "cursor:grab;z-index:2147483647;box-shadow:0 4px 16px rgba(108,92,231,0.5);user-select:none;";
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
        if (dragState) {
            if (!hasDragged) showMini();  // 没拖动 = 点击
            dragState = null;
        }
    });

    // ── 迷你窗口 ──
    var mini = document.createElement("div");
    mini.id = "fw-mini";
    mini.style.cssText = "display:none;position:fixed;right:12px;top:80px;width:340px;" +
        "height:400px;min-height:200px;background:rgba(22,22,42,0.97);" +
        "border:1px solid rgba(108,92,231,0.4);border-radius:12px;z-index:2147483646;" +
        "flex-direction:column;overflow:hidden;font:13px system-ui,'PingFang SC',sans-serif;" +
        "color:#e0e0f0;box-shadow:0 8px 32px rgba(0,0,0,0.6);backdrop-filter:blur(8px);";
    mini.innerHTML =
        '<div id="fw-head" style="display:flex;align-items:center;padding:8px 12px;border-bottom:1px solid rgba(255,255,255,0.1);cursor:move;user-select:none;flex-shrink:0;">' +
        '<span style="font-weight:700;pointer-events:none;">🎬 帧知</span>' +
        '<span id="fw-status" style="flex:1;margin-left:8px;font-size:11px;color:#999;pointer-events:none;"></span>' +
        '<button id="fw-hist" title="历史" style="background:none;border:1px solid rgba(255,255,255,0.15);color:#999;font-size:12px;cursor:pointer;padding:2px 6px;border-radius:4px;margin-right:3px;">📋</button>' +
        '<button id="fw-auto" title="自动处理" style="background:#2a2a55;border:1px solid #6c5ce7;color:#e0e0f0;font-size:12px;cursor:pointer;padding:2px 6px;border-radius:4px;margin-right:3px;">🤖</button>' +
        '<button id="fw-proc" title="重新处理" style="background:none;border:1px solid rgba(255,255,255,0.15);color:#999;font-size:12px;cursor:pointer;padding:2px 6px;border-radius:4px;margin-right:3px;">🔄</button>' +
        '<button id="fw-quiz" title="考考我" style="background:none;border:1px solid rgba(255,255,255,0.15);color:#999;font-size:12px;cursor:pointer;padding:2px 6px;border-radius:4px;margin-right:3px;">❓</button>' +
        '<button id="fw-min" title="缩小" style="background:none;border:none;color:#999;font-size:18px;cursor:pointer;padding:2px 6px;line-height:1;">−</button>' +
        '<button id="fw-cls" title="关闭" style="background:none;border:none;color:#999;font-size:14px;cursor:pointer;">✕</button>' +
        '</div>' +
        '<div id="fw-msgs" style="flex:1;overflow-y:auto;overflow-x:hidden;padding:8px 12px;max-height:360px;min-height:60px;"></div>' +
        '<div style="display:flex;align-items:center;gap:4px;padding:3px 12px;border-top:1px solid rgba(255,255,255,0.08);flex-shrink:0;">' +
        '<span id="fw-mode-lbl" style="font-size:10px;color:#999;">📝</span>' +
        '<span id="fw-time" style="margin-left:auto;font-size:11px;color:#00d2a0;font-family:monospace;">00:00</span>' +
        '</div>' +
        '<div style="display:flex;gap:6px;padding:6px 12px 10px;flex-shrink:0;">' +
        '<textarea id="fw-input" placeholder="输入问题..." disabled rows="1" style="flex:1;padding:8px 10px;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12);border-radius:6px;color:#e0e0f0;font-size:12px;outline:none;resize:none;max-height:80px;line-height:1.4;font-family:inherit;"></textarea>' +
        '<button id="fw-send" disabled style="padding:8px 14px;background:#6c5ce7;border:none;border-radius:6px;color:#fff;font-weight:600;font-size:12px;cursor:pointer;white-space:nowrap;">发送</button>' +
        '</div>' +
        '<div id="fw-rsz" style="position:absolute;right:0;bottom:0;width:14px;height:14px;cursor:se-resize;overflow:hidden;">' +
        '<svg viewBox="0 0 16 16" style="width:14px;height:14px;display:block;"><path d="M0 16L16 0v16H0z" fill="rgba(255,255,255,0.2)"/></svg></div>';
    document.body.appendChild(mini);

    // 滚轮不穿透
    mini.addEventListener("wheel", function (e) { e.stopPropagation(); });

    // ── 窗口拖拽 ──
    (function () {
        var d = null, r = null, m = false;
        document.getElementById("fw-head").addEventListener("mousedown", function (e) {
            if (e.target.tagName === "BUTTON") return;
            var rect = mini.getBoundingClientRect();
            d = { sx: e.clientX, sy: e.clientY, x: rect.left, y: rect.top };
            m = false; e.preventDefault();
        });
        document.addEventListener("mousemove", function (e) {
            if (!d || r) return;
            r = requestAnimationFrame(function () {
                r = null;
                var dx = e.clientX - d.sx, dy = e.clientY - d.sy;
                if (Math.abs(dx) > 2 || Math.abs(dy) > 2) m = true;
                if (!m) return;
                var l = Math.max(0, Math.min(window.innerWidth - 340, d.x + dx));
                var t = Math.max(0, Math.min(window.innerHeight - 100, d.y + dy));
                mini.style.right = "auto"; mini.style.top = "auto";
                mini.style.left = l + "px"; mini.style.top = t + "px";
            });
        });
        document.addEventListener("mouseup", function () { d = null; });
    })();

    // ── 窗口缩放 ──
    (function () {
        var d = null, r = null;
        document.getElementById("fw-rsz").addEventListener("mousedown", function (e) {
            d = { sx: e.clientX, sy: e.clientY, w: mini.offsetWidth, h: mini.offsetHeight };
            e.preventDefault(); e.stopPropagation();
        });
        document.addEventListener("mousemove", function (e) {
            if (!d || r) return;
            r = requestAnimationFrame(function () {
                r = null;
                var w = Math.max(260, Math.min(600, d.w + (e.clientX - d.sx)));
                var h = Math.max(200, Math.min(window.innerHeight * 0.9, d.h + (e.clientY - d.sy)));
                mini.style.width = w + "px";
                mini.style.height = h + "px";
                mini.style.maxHeight = "none";
                var m = document.getElementById("fw-msgs");
                if (m) m.style.maxHeight = "none";
            });
        });
        document.addEventListener("mouseup", function () { d = null; });
    })();

    // ── 全屏处理 ──
    document.addEventListener("fullscreenchange", function () {
        if (document.fullscreenElement) {
            document.fullscreenElement.appendChild(trigger);
            document.fullscreenElement.appendChild(mini);
            trigger.style.right = "12px"; trigger.style.bottom = "80px";
            trigger.style.left = "auto"; trigger.style.top = "auto";
        } else {
            document.body.appendChild(trigger);
            if (mini.parentNode !== document.body) document.body.appendChild(mini);
        }
    });

    function showMini() {
        if (window._fwReady) loadHistory();
        mini.style.display = "flex";
        trigger.style.display = "none";
    }

    function hideMini() {
        mini.style.display = "none";
        trigger.style.display = "flex";
    }

    // ── 事件 ──
    // ── 状态变量（必须在按钮初始化之前） ──
    var videoId = null, autoMode = localStorage.getItem("fw_auto") !== "false", isProcessing = false;
    var lastUrl = cleanUrl(location.href);
    window._fwReady = false;

    document.getElementById("fw-cls").onclick = hideMini;
    document.getElementById("fw-min").onclick = hideMini;
    // 自动处理按钮
    var autoBtn = document.getElementById("fw-auto");
    function updateAutoBtn(state) {
        autoMode = state;
        autoBtn.style.background = state ? "#2a2a55" : "rgba(255,255,255,0.06)";
        autoBtn.style.borderColor = state ? "#6c5ce7" : "rgba(255,255,255,0.15)";
        autoBtn.style.color = state ? "#e0e0f0" : "#999";
    }
    console.log("[帧知] init autoMode:", autoMode, "autoBtn:", !!autoBtn);
    updateAutoBtn(autoMode);
    console.log("[帧知] after updateAutoBtn, bg:", autoBtn.style.background);
    autoBtn.onclick = function () {
        updateAutoBtn(!autoMode);
        localStorage.setItem("fw_auto", autoMode);
        console.log("[帧知] clicked, saved:", autoMode);
    };
    document.getElementById("fw-proc").onclick = function () {
        if (videoId && window._fwReady) { addMsg("system", "✅ 已处理"); return; }
        videoId = null; window._fwReady = false; updateStatus("⏳ 处理中...");
        document.getElementById("fw-input").disabled = true;
        document.getElementById("fw-send").disabled = true;
        initVideo();
    };
    document.getElementById("fw-quiz").onclick = function () {
        if (!window._fwReady) { addMsg("system", "⏳ 等待就绪"); return; }
        addMsg("system", "🤔 出题中...");
        fetch(API_BASE + "/api/videos/" + videoId + "/quiz", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ timestamp: getCurrentTime() }),
        }).then(function (r) { return r.json(); }).then(function (data) {
            var h = '<b>📝 小测验 (' + data.context_time + ')：</b><br><br>';
            data.questions.forEach(function (q, i) {
                h += '<div style="margin-bottom:8px;"><b>' + (i+1) + '. ' + esc(q.question) + '</b>';
                h += '<div style="margin-top:3px;cursor:pointer;color:#00d2a0;font-size:11px;" onclick="this.nextElementSibling.style.display=this.nextElementSibling.style.display==\'block\'?\'none\':\'block\'">💡 查看答案</div>';
                h += '<div style="display:none;background:#1a1a2e;padding:6px 10px;border-radius:4px;margin-top:3px;font-size:12px;border-left:3px solid #00d2a0;">' + esc(q.answer) + '</div></div>';
            });
            addMsg("assistant", h);
        }).catch(function (e) { addMsg("error", e.message); });
    };
    document.getElementById("fw-hist").onclick = function () {
        fetch(API_BASE + "/api/conversations").then(function (r) { return r.json(); }).then(function (data) {
            var c = document.getElementById("fw-msgs"); c.innerHTML = "";
            if (!data.length) { addMsg("system", "暂无历史"); return; }
            var h = '<div style="font-size:13px;font-weight:600;padding:0 0 8px;border-bottom:1px solid rgba(255,255,255,0.1);margin-bottom:8px;">📋 历史对话</div>';
            data.forEach(function (cv) {
                h += '<div data-vid="' + esc(cv.video_id) + '" style="padding:8px 10px;border-radius:6px;background:rgba(255,255,255,0.05);cursor:pointer;margin-bottom:4px;">' +
                    '<div style="font-size:12px;">' + esc(cv.title) + '</div>' +
                    '<div style="font-size:10px;color:#999;">' + cv.msg_count + '条 · ' + (cv.last_time||'').slice(0,10) + '</div></div>';
            });
            c.innerHTML = h;
            c.querySelectorAll("[data-vid]").forEach(function (el) {
                el.onclick = function () {
                    window._fwReady = false; videoId = this.dataset.vid;
                    fetch(API_BASE + "/api/videos/" + videoId).then(function (r) { return r.json(); }).then(function (info) {
                        if (info.status === "ready") { window._fwReady = true; updateStatus("✅ 就绪"); document.getElementById("fw-input").disabled = false; document.getElementById("fw-send").disabled = false; loadHistory(); }
                        else { updateStatus("⏳ 重新处理..."); initVideo(); }
                    });
                };
            });
        }).catch(function () { addMsg("error", "加载失败"); });
    };

    document.getElementById("fw-send").onclick = sendQuestion;
    var inp = document.getElementById("fw-input");
    inp.onkeydown = function (e) { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendQuestion(); } };
    inp.oninput = function () { inp.style.height = "auto"; inp.style.height = Math.min(inp.scrollHeight, 80) + "px"; };

    // ── 初始化 ──
    initVideo();

    // ── 辅助函数 ──
    function cleanUrl(url) {
        try { var u = new URL(url); var s = ""; ["v","p"].forEach(function (k) { if (u.searchParams.has(k)) s += (s?"&":"") + k + "=" + u.searchParams.get(k); }); return u.origin + u.pathname + (s ? "?" + s : ""); }
        catch (e) { return url; }
    }

    function getCurrentTime() {
        try { if (window.player && window.player.getCurrentTime) return window.player.getCurrentTime(); } catch (e) {}
        var v = document.querySelector("video"); return v ? v.currentTime : 0;
    }

    function updateStatus(t) { var e = document.getElementById("fw-status"); if (e) e.textContent = t; }

    function esc(t) { var d = document.createElement("div"); d.textContent = t; return d.innerHTML; }

    function fmt(s) { var m = Math.floor(s/60), sec = Math.floor(s%60); return String(m).padStart(2,"0") + ":" + String(sec).padStart(2,"0"); }

    function addMsg(type, content) {
        var c = document.getElementById("fw-msgs"), div = document.createElement("div");
        var colors = { system: "rgba(255,255,255,0.04);color:#999", user: "#6c5ce7;color:#fff;align-self:flex-end;max-width:85%", assistant: "rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.1);max-width:85%", error: "#e74c3c;color:#fff" };
        div.style.cssText = (colors[type]||colors.system) + ";padding:8px 10px;border-radius:6px;margin-bottom:6px;font-size:12px;line-height:1.5;word-break:break-word;min-width:0;max-width:100%;";
        div.innerHTML = content;
        c.appendChild(div); c.scrollTop = c.scrollHeight;
    }

    // ── 时间 + 模式 + URL检测 + 重连 ──
    setInterval(function () {
        if (!window._fwReady) return;
        var t = getCurrentTime();
        document.getElementById("fw-time").textContent = fmt(t);
        var paused = (document.querySelector("video") || {}).paused;
        var lbl = document.getElementById("fw-mode-lbl");
        if (lbl) { lbl.textContent = paused ? "🖼️ 画面" : "📝 文本"; lbl.style.color = paused ? "#00d2a0" : "#999"; }
    }, 1000);

    setInterval(function () {
        var cur = cleanUrl(location.href);
        if (cur !== lastUrl) {
            lastUrl = cur;
            if (autoMode) { videoId = null; window._fwReady = false; updateStatus("⏳ 新视频..."); document.getElementById("fw-input").disabled = true; document.getElementById("fw-send").disabled = true; document.getElementById("fw-msgs").innerHTML = ""; initVideo(); }
            else { addMsg("system", "🔔 检测到新视频，点击 🔄 处理"); }
        }
    }, 2000);

    var isOffline = false;
    setInterval(function () {
        fetch(API_BASE + "/api/health").then(function () {
            if (isOffline) { isOffline = false; updateStatus("✅ 已重连"); }
        }).catch(function () { if (!isOffline) { isOffline = true; updateStatus("⚠️ 断线"); } });
    }, 10000);

    // ── 视频处理 ──
    function initVideo() {
        updateStatus("⏳ 建立索引...");
        updateProgress({progress: 2, progress_text: "连接服务..."});
        fetch(API_BASE + "/api/videos/from_url", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url: lastUrl }),
        }).then(function (r) { return r.json(); }).then(function (data) {
            videoId = data.video_id;
            if (data.status === "ready") { readyState(data); }
            else { updateStatus("⏳ ASR中..."); pollStatus(); }
        }).catch(function (e) { updateStatus("❌ 连接失败"); });
    }

    function pollStatus() {
        (function check() {
            fetch(API_BASE + "/api/videos/" + videoId).then(function (r) { return r.json(); }).then(function (data) {
                if (data.status === "ready") { readyState(data); return; }
                if (data.status === "error") { updateStatus("❌ 失败"); return; }
                if (data.progress) updateProgress(data);
                setTimeout(check, 2000);
            }).catch(function () { setTimeout(check, 5000); });
        })();
    }

    function readyState(data) {
        window._fwReady = true;
        updateStatus("✅ 就绪 (" + (data.chunk_count||"?") + "片段)");
        document.getElementById("fw-input").disabled = false;
        document.getElementById("fw-send").disabled = false;
        document.getElementById("fw-msgs").innerHTML =
            '<div style="color:#00d2a0;text-align:center;padding:20px 0;">✅ 视频已就绪，开始提问吧！</div>';
        loadHistory();
    }

    function updateProgress(data) {
        var pct = data.progress || 0;
        var text = data.progress_text || "处理中...";
        updateStatus(text + " " + pct + "%");
        var c = document.getElementById("fw-msgs");
        if (c) {
            c.innerHTML =
                '<div style="text-align:center;padding:20px 0;">' +
                '<div style="font-size:13px;color:#999;margin-bottom:10px;">' + text + '</div>' +
                '<div style="background:rgba(255,255,255,0.08);border-radius:10px;height:8px;overflow:hidden;max-width:280px;margin:0 auto;">' +
                '<div style="background:linear-gradient(90deg,#6c5ce7,#00d2a0);height:100%;width:' + pct + '%;border-radius:10px;transition:width 1s;"></div></div>' +
                '<div style="font-size:12px;color:#666;margin-top:6px;">' + pct + '%</div></div>';
        }
    }

    function loadHistory() {
        fetch(API_BASE + "/api/videos/" + videoId + "/history?limit=30").then(function (r) { return r.json(); }).then(function (data) {
            if (!data || !data.length) return;
            var c = document.getElementById("fw-msgs"); c.innerHTML = "";
            data.forEach(function (m) {
                if (m.role === "user") addMsg("user", esc(m.content));
                else if (m.role === "assistant") addMsg("assistant", esc(m.content));
            });
        }).catch(function () {});
    }

    // ── 发送问题 ──
    function sendQuestion() {
        var inp = document.getElementById("fw-input"), q = inp.value.trim();
        if (!q || !window._fwReady || isProcessing) return;
        isProcessing = true; inp.disabled = true; document.getElementById("fw-send").disabled = true; inp.value = ""; inp.style.height = "auto";

        var paused = (document.querySelector("video") || {}).paused;
        var isVision = paused, t = getCurrentTime();
        addMsg("user", (isVision ? "🖼️ " : "") + q);

        var endpoint = isVision ? "ask_frame" : "ask";
        var body = { question: q, timestamp: t };
        if (isVision) {
            var frame = captureFrame();
            if (frame) body.frame_base64 = frame;
        }

        fetch(API_BASE + "/api/videos/" + videoId + "/" + endpoint, {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
        }).then(function (r) { return r.json(); }).then(function (data) {
            var h = esc(data.answer);
            if (data.frame_description) h = '<span style="background:#e17055;color:#fff;padding:1px 6px;border-radius:3px;font-size:10px;">🖼️ 画面</span><br>' + h;
            if (data.references && data.references.length > 0) {
                h += '<div style="margin-top:8px;font-size:11px;color:#999;">📖 ';
                var seen = {};
                data.references.forEach(function (ref) {
                    var k = ref.start_time + "-" + ref.end_time; if (seen[k]) return; seen[k] = true;
                    h += '<span style="background:#00d2a0;color:#0f0f1a;padding:1px 6px;border-radius:3px;font-size:10px;margin:1px;cursor:pointer;" onclick="' +
                        'try{if(window.player&&window.player.seek)window.player.seek(' + ref.start_time + ');else{var v=document.querySelector(\'video\');if(v)v.currentTime=' + ref.start_time + ';}}catch(e){}">' +
                        fmt(ref.start_time) + '~' + fmt(ref.end_time) + '</span> ';
                });
                h += '</div>';
            }
            addMsg("assistant", h);
        }).catch(function (e) { addMsg("error", e.message); }).finally(function () {
            inp.disabled = false; document.getElementById("fw-send").disabled = false; inp.focus(); isProcessing = false;
        });
    }

    function captureFrame() {
        try {
            var v = document.querySelector("video"); if (!v || v.videoWidth === 0) return null;
            var c = document.createElement("canvas"); c.width = v.videoWidth; c.height = v.videoHeight;
            c.getContext("2d").drawImage(v, 0, 0);
            return c.toDataURL("image/jpeg", 0.7).replace(/^data:image\/jpeg;base64,/, "");
        } catch (e) { return null; }
    }

    console.log("[帧知] v2 loaded");
})();
