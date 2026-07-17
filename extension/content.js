/**
 * 帧知 - 浏览器插件
 * 在 B站/YouTube 视频页面注入 AI 问答侧边栏
 */
(function () {
    "use strict";

    const API_BASE = "http://127.0.0.1:8000";

    // ── 平台检测 ──
    const host = location.hostname;
    const isBilibili = host.includes("bilibili.com");
    const isYoutube = host.includes("youtube.com");
    console.log("[帧知] 平台检测:", host, "B站:", isBilibili, "YouTube:", isYoutube);

    if (!isBilibili && !isYoutube) {
        console.log("[帧知] 非目标平台，跳过");
        return;
    }

    // ── 先创建浮动按钮（不依赖任何异步操作） ──
    try {
        var trigger = document.createElement("div");
        trigger.id = "fw-trigger";
        trigger.textContent = "🎬";
        trigger.title = "帧知 - AI视频学习助手";
        trigger.style.cssText =
            "position:fixed;right:16px;bottom:120px;" +
            "width:48px;height:48px;background:linear-gradient(135deg,#6c5ce7,#00d2a0);" +
            "border-radius:50%;display:flex;align-items:center;justify-content:center;" +
            "font-size:22px;cursor:grab;z-index:2147483647;" +
            "box-shadow:0 4px 16px rgba(108,92,231,0.5);user-select:none;";
        // 拖拽移动（GPU加速 + RAF节流）
        var dragState = null, dragRaf = null, hasDragged = false;
        trigger.addEventListener("mousedown", function (e) {
            if (e.button !== 0) return;
            var rect = trigger.getBoundingClientRect();
            dragState = {
                startX: e.clientX, startY: e.clientY,
                left: rect.left, top: rect.top,
            };
            hasDragged = false;
            e.preventDefault();
        });
        document.addEventListener("mousemove", function (e) {
            if (!dragState || dragRaf) return;
            dragRaf = requestAnimationFrame(function () {
                dragRaf = null;
                var dx = e.clientX - dragState.startX;
                var dy = e.clientY - dragState.startY;
                if (Math.abs(dx) > 3 || Math.abs(dy) > 3) hasDragged = true;
                if (!hasDragged) return;  // 没拖动就不更新位置
                var l = Math.max(0, Math.min(window.innerWidth - 48, dragState.left + dx));
                var t = Math.max(0, Math.min(window.innerHeight - 48, dragState.top + dy));
                trigger.style.transform = "translate3d(" + l + "px," + t + "px,0)";
            });
        });
        document.addEventListener("mouseup", function () {
            if (dragState) {
                if (hasDragged) {
                    // 提交最终位置
                    var parts = (trigger.style.transform || "").match(/translate3d\(([^,]+)px,\s*([^,]+)px/);
                    if (parts) {
                        trigger.style.left = parts[1] + "px";
                        trigger.style.top = parts[2] + "px";
                    }
                    trigger.style.transform = "";
                    trigger.style.right = "auto";
                    trigger.style.bottom = "auto";
                }
                // 没拖动 = 点击
                if (!hasDragged) {
                    var p = document.getElementById("fw-panel");
                    if (p) p.style.display = "flex";
                    trigger.style.display = "none";
                }
                dragState = null;
            }
        });
        // 触摸事件
        trigger.addEventListener("touchstart", function (e) {
            var rect = trigger.getBoundingClientRect();
            var t = e.touches[0];
            dragState = {
                startX: t.clientX, startY: t.clientY,
                left: rect.left, top: rect.top,
            };
            hasDragged = false;
        }, { passive: false });
        document.addEventListener("touchmove", function (e) {
            if (!dragState || dragRaf) return;
            var t = e.touches[0];
            dragRaf = requestAnimationFrame(function () {
                dragRaf = null;
                var dx = t.clientX - dragState.startX;
                var dy = t.clientY - dragState.startY;
                if (Math.abs(dx) > 3 || Math.abs(dy) > 3) hasDragged = true;
                if (!hasDragged) return;
                var l = Math.max(0, Math.min(window.innerWidth - 48, dragState.left + dx));
                var tp = Math.max(0, Math.min(window.innerHeight - 48, dragState.top + dy));
                trigger.style.transform = "translate3d(" + l + "px," + tp + "px,0)";
            });
        });
        document.addEventListener("touchend", function () {
            if (dragState) {
                if (hasDragged) {
                    var parts = (trigger.style.transform || "").match(/translate3d\(([^,]+)px,\s*([^,]+)px/);
                    if (parts) {
                        trigger.style.left = parts[1] + "px";
                        trigger.style.top = parts[2] + "px";
                    }
                    trigger.style.transform = "";
                    trigger.style.right = "auto";
                    trigger.style.bottom = "auto";
                }
                if (!hasDragged) {
                    var p = document.getElementById("fw-panel");
                    if (p) p.style.display = "flex";
                    trigger.style.display = "none";
                }
                dragState = null;
            }
        });

        document.body.appendChild(trigger);
        console.log("[帧知] 浮动按钮已创建");
    } catch (e) {
        console.error("[帧知] 创建按钮失败:", e);
        return;
    }

    // ── 延迟初始化面板（等 DOM 就绪） ──
    function init() {
        try {
            buildPanel();
            bindEvents();
            initVideo();
        } catch (e) {
            console.error("[帧知] 初始化失败:", e);
            trigger.textContent = "⚠️";
            trigger.title = "帧知初始化失败: " + e.message;
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }

    // ═══════════════════════════════════════════
    // UI 构建
    // ═══════════════════════════════════════════

    function buildPanel() {
        // 面板
        var panel = document.createElement("div");
        panel.id = "fw-panel";
        panel.style.cssText =
            "position:fixed;top:0;right:0;width:380px;height:100vh;" +
            "background:#1a1a2e;border-left:1px solid #2a2a45;" +
            "z-index:2147483646;display:none;flex-direction:column;" +
            "box-shadow:-4px 0 24px rgba(0,0,0,0.5);" +
            "font-family:system-ui,'PingFang SC','Microsoft YaHei',sans-serif;" +
            "font-size:14px;color:#e0e0f0;";
        panel.innerHTML =
            '<div style="display:flex;align-items:center;padding:14px 16px;' +
            'border-bottom:1px solid #2a2a45;background:#16162a;flex-shrink:0;">' +
            '<span style="font-size:16px;font-weight:700;' +
            'background:linear-gradient(135deg,#6c5ce7,#00d2a0);' +
            '-webkit-background-clip:text;-webkit-text-fill-color:transparent;">' +
            '🎬 帧知</span>' +
            '<span id="fw-status" style="flex:1;margin-left:10px;font-size:12px;color:#999;"></span>' +
            '<button id="fw-history" title="历史记录" style="background:#252540;border:1px solid #2a2a45;' +
            'color:#999;font-size:14px;cursor:pointer;padding:2px 6px;border-radius:4px;margin-right:4px;">📋</button>' +
            '<button id="fw-auto" title="自动处理新视频" style="background:#2a2a55;border:1px solid #6c5ce7;' +
            'color:#e0e0f0;font-size:14px;cursor:pointer;padding:2px 6px;border-radius:4px;margin-right:4px;">🤖</button>' +
            '<button id="fw-process" title="手动处理当前视频" style="background:#252540;border:1px solid #2a2a45;' +
            'color:#999;font-size:14px;cursor:pointer;padding:2px 6px;border-radius:4px;margin-right:4px;">🔄</button>' +
            '<button id="fw-quiz" title="考考我" style="background:#252540;border:1px solid #2a2a45;' +
            'color:#999;font-size:14px;cursor:pointer;padding:2px 6px;border-radius:4px;margin-right:4px;">❓</button>' +
            '<button id="fw-minimize" title="缩小" style="background:none;border:none;color:#999;' +
            'font-size:20px;cursor:pointer;padding:2px 8px;line-height:1;">−</button>' +
            '<button id="fw-close" title="关闭" style="background:none;border:none;color:#999;' +
            'font-size:18px;cursor:pointer;padding:4px 8px;">✕</button>' +
            '</div>' +
            '<div id="fw-messages" style="flex:1;overflow-y:auto;overflow-x:hidden;' +
            'padding:12px;display:flex;flex-direction:column;gap:10px;">' +
            '<div style="background:#252540;color:#999;padding:10px 14px;' +
            'border-radius:8px;font-size:13px;">' +
            '👋 正在连接帧知服务...</div>' +
            '</div>' +
            '<div style="padding:10px 12px;border-top:1px solid #2a2a45;' +
            'background:#16162a;flex-shrink:0;">' +
            '<div style="display:flex;align-items:center;gap:6px;margin-bottom:6px;">' +
            '<span id="fw-mode-label" style="font-size:11px;color:#999;">📝 提问</span>' +
            '<span id="fw-time" style="margin-left:auto;font-size:12px;color:#00d2a0;' +
            'font-family:monospace;">00:00</span>' +
            '</div>' +
            '<div style="display:flex;gap:6px;">' +
            '<textarea id="fw-input" placeholder="输入问题..." disabled rows="1" ' +
            'style="flex:1;padding:8px 12px;background:#252540;border:1px solid #2a2a45;' +
            'border-radius:6px;color:#e0e0f0;font-size:13px;outline:none;' +
            'resize:none;max-height:100px;line-height:1.4;font-family:inherit;"></textarea>' +
            '<button id="fw-send" disabled ' +
            'style="padding:8px 16px;background:#00d2a0;border:none;border-radius:6px;' +
            'color:#0f0f1a;font-weight:600;cursor:pointer;">发送</button>' +
            '</div>' +
            '</div>';

        document.body.appendChild(panel);
        console.log("[帧知] 面板已创建");
    }

    function hidePanel() {
        var p = document.getElementById("fw-panel");
        if (p) p.style.display = "none";
        if (trigger) trigger.style.display = "flex";
    }

    function minimizePanel() {
        hidePanel();
    }

    // ═══════════════════════════════════════════
    // 事件绑定
    // ═══════════════════════════════════════════

    var isVisionMode = false;

    var autoMode = true;  // 默认自动处理
    var lastUrl = cleanUrl(location.href);

    function bindEvents() {
        var closeBtn = document.getElementById("fw-close");
        if (closeBtn) closeBtn.onclick = hidePanel;

        var histBtn = document.getElementById("fw-history");
        if (histBtn) histBtn.onclick = function () {
            fetch(API_BASE + "/api/conversations")
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    var container = document.getElementById("fw-messages");
                    if (!container) return;
                    container.innerHTML = "";
                    if (!data.length) {
                        addMsg("system", "暂无历史对话记录");
                        return;
                    }
                    var html = '<div style="font-size:14px;font-weight:600;color:#e0e0f0;padding:4px 0 12px;border-bottom:1px solid #2a2a45;margin-bottom:8px;">📋 历史对话</div>';
                    data.forEach(function (conv) {
                        html += '<div class="fw-hist-item" data-vid="' + esc(conv.video_id) +
                            '" style="padding:10px 12px;border-radius:8px;background:#252540;cursor:pointer;margin-bottom:6px;">' +
                            '<div style="font-size:13px;color:#e0e0f0;">' + esc(conv.title) + '</div>' +
                            '<div style="font-size:11px;color:#999;">' + conv.msg_count + ' messages - ' + (conv.last_time || '').slice(0, 10) + '</div>' +
                            '</div>';
                    });
                    container.innerHTML = html;
                    container.querySelectorAll(".fw-hist-item").forEach(function (el) {
                        el.onclick = function () {
                            window._fwReady = false;
                            videoId = this.dataset.vid;
                            // 检查状态，如果ready直接加载历史
                            fetch(API_BASE + "/api/videos/" + videoId)
                                .then(function (r) { return r.json(); })
                                .then(function (info) {
                                    if (info.status === "ready") {
                                        window._fwReady = true;
                                        updateStatus("✅ 就绪 (" + (info.chunk_count || "?") + " 片段)");
                                        var inp = document.getElementById("fw-input");
                                        var snd = document.getElementById("fw-send");
                                        if (inp) inp.disabled = false;
                                        if (snd) snd.disabled = false;
                                        loadHistory();
                                    } else {
                                        updateStatus("⏳ 视频需重新处理");
                                        initVideo();
                                    }
                                });
                        };
                    });
                }).catch(function (e) {
                    addMsg("error", "加载失败: " + e.message);
                });
        };

        var minBtn = document.getElementById("fw-minimize");
        if (minBtn) minBtn.onclick = minimizePanel;

        var autoBtn = document.getElementById("fw-auto");
        if (autoBtn) autoBtn.onclick = function () {
            autoMode = !autoMode;
            autoBtn.style.background = autoMode ? "#2a2a55" : "#252540";
            autoBtn.style.borderColor = autoMode ? "#6c5ce7" : "#2a2a45";
            autoBtn.style.color = autoMode ? "#e0e0f0" : "#999";
            autoBtn.title = autoMode ? "自动处理：开" : "自动处理：关";
        };

        var procBtn = document.getElementById("fw-process");
        if (procBtn) procBtn.onclick = function () {
            if (videoId && window._fwReady) {
                addMsg("system", "✅ 当前视频已处理");
                return;
            }
            procBtn.disabled = true;
            procBtn.style.opacity = "0.5";
            videoId = null;
            window._fwReady = false;
            updateStatus("⏳ 手动处理中...");
            var inp = document.getElementById("fw-input");
            if (inp) { inp.disabled = true; inp.value = ""; }
            document.getElementById("fw-send").disabled = true;
            initVideo();  // pollStatus 中会自动恢复按钮状态
        };

        var quizBtn = document.getElementById("fw-quiz");
        if (quizBtn) quizBtn.onclick = function () {
            if (!window._fwReady) {
                addMsg("system", "⏳ 视频还在处理中...");
                return;
            }
            quizBtn.disabled = true;
            quizBtn.style.opacity = "0.5";
            addMsg("system", "🤔 正在出题...");
            var t = getCurrentTime();
            fetch(API_BASE + "/api/videos/" + videoId + "/quiz", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ timestamp: t }),
            }).then(function (r) { return r.json(); }).then(function (data) {
                var html = '<b>📝 视频 ' + data.context_time + ' 处小测验：</b><br><br>';
                data.questions.forEach(function (q, i) {
                    html += '<div style="margin-bottom:10px;"><b>' + (i + 1) + '. ' + esc(q.question) + '</b>';
                    html += '<div style="margin-top:4px;cursor:pointer;color:#00d2a0;font-size:12px;" ' +
                            'onclick="var a=this.nextElementSibling;a.style.display=a.style.display==\'block\'?\'none\':\'block\';">' +
                            '💡 点击查看答案</div>';
                    html += '<div style="display:none;background:#1a1a2e;padding:8px 12px;border-radius:6px;' +
                            'margin-top:4px;font-size:13px;border-left:3px solid #00d2a0;">' +
                            esc(q.answer) + '</div></div>';
                });
                addMsg("assistant", html);
            }).catch(function (e) {
                addMsg("error", "出题失败: " + e.message);
            }).finally(function () {
                quizBtn.disabled = false;
                quizBtn.style.opacity = "1";
            });
        };

        var sendBtn = document.getElementById("fw-send");
        if (sendBtn) sendBtn.onclick = sendQuestion;

        var input = document.getElementById("fw-input");
        if (input) {
            input.onkeydown = function (e) {
                if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    sendQuestion();
                }
            };
            input.oninput = function () {
                input.style.height = "auto";
                input.style.height = Math.min(input.scrollHeight, 100) + "px";
            };
        }

        // 时间更新 + 自动检测暂停状态 + URL变化检测 + 连接检测
        setInterval(updateTime, 1000);
        setInterval(checkUrlChange, 2000);
        setInterval(checkConnection, 10000);
    }

    // ── 断线重连 ──
    var isOffline = false;
    function checkConnection() {
        fetch(API_BASE + "/api/health")
            .then(function (r) { return r.json(); })
            .then(function () {
                if (isOffline) {
                    isOffline = false;
                    updateStatus("✅ 已重连");
                    // 重连后恢复，如果有视频ID则检查状态
                    if (videoId && !window._fwReady && !isPolling) {
                        pollStatus();
                    }
                }
            })
            .catch(function () {
                if (!isOffline) {
                    isOffline = true;
                    updateStatus("⚠️ 连接断开");
                }
            });
    }

    // ── URL 变化检测（合集/列表自动跳转） ──
    function checkUrlChange() {
        var cur = cleanUrl(location.href);
        if (cur !== lastUrl) {
            console.log("[帧知] URL changed:", lastUrl, "→", cur);
            lastUrl = cur;
            if (autoMode) {
                videoId = null;
                window._fwReady = false;
                updateStatus("⏳ 检测到新视频...");
                var inp = document.getElementById("fw-input");
                if (inp) { inp.disabled = true; }
                document.getElementById("fw-send").disabled = true;
                // 清理旧消息
                var msgs = document.getElementById("fw-messages");
                if (msgs) msgs.innerHTML = '<div style="background:#252540;color:#999;padding:10px 14px;border-radius:8px;font-size:13px;">🔄 检测到新视频，自动处理中...</div>';
                initVideo();
            } else {
                addMsg("system", "🔔 检测到新视频，点击 🔄 按钮手动处理");
            }
        }
    }

    // ═══════════════════════════════════════════
    // 视频时间
    // ═══════════════════════════════════════════

    function getCurrentTime() {
        try {
            if (isBilibili && window.player && window.player.getCurrentTime) {
                return window.player.getCurrentTime();
            }
        } catch (e) { /* fallback */ }
        var v = document.querySelector("video");
        return v ? v.currentTime : 0;
    }

    function captureFrame() {
        try {
            var v = document.querySelector("video");
            if (!v) return null;
            var canvas = document.createElement("canvas");
            canvas.width = v.videoWidth;
            canvas.height = v.videoHeight;
            var ctx = canvas.getContext("2d");
            ctx.drawImage(v, 0, 0);
            // 压缩为 JPEG，质量 0.7
            return canvas.toDataURL("image/jpeg", 0.7).replace(/^data:image\/jpeg;base64,/, "");
        } catch (e) {
            console.error("[帧知] 截图失败:", e);
            return null;
        }
    }

    function seekTo(seconds) {
        try {
            if (isBilibili && window.player && window.player.seek) {
                window.player.seek(seconds);
                return;
            }
        } catch (e) { /* fallback */ }
        var v = document.querySelector("video");
        if (v) v.currentTime = seconds;
    }

    function isVideoPaused() {
        var v = document.querySelector("video");
        return v ? v.paused : false;
    }

    function updateTime() {
        if (!window._fwReady) return;
        var t = getCurrentTime();
        var el = document.getElementById("fw-time");
        if (!el) return;
        var m = Math.floor(t / 60);
        var s = Math.floor(t % 60);
        el.textContent = String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");

        // 自动切换模式标签
        var paused = isVideoPaused();
        isVisionMode = paused;
        var label = document.getElementById("fw-mode-label");
        if (label) {
            label.textContent = paused ? "🖼️ 画面提问" : "📝 提问";
            label.style.color = paused ? "#00d2a0" : "#999";
        }
    }

    // ═══════════════════════════════════════════
    // 视频处理
    // ═══════════════════════════════════════════

    var videoId = null;
    window._fwReady = false;

    function cleanUrl(url) {
        // 去除跟踪参数
        try {
            var u = new URL(url);
            var keep = ["v", "list"];  // 保留的参数
            var newSearch = "";
            keep.forEach(function (k) {
                if (u.searchParams.has(k)) newSearch += (newSearch ? "&" : "") + k + "=" + u.searchParams.get(k);
            });
            return u.origin + u.pathname + (newSearch ? "?" + newSearch : "");
        } catch (e) { return url; }
    }

    async function initVideo() {
        updateStatus("⏳ 建立索引...");
        var clean = cleanUrl(location.href);
        try {
            console.log("[帧知] 请求 from_url:", clean);
            var resp = await fetch(API_BASE + "/api/videos/from_url", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ url: clean }),
            });
            if (!resp.ok) throw new Error("HTTP " + resp.status);
            var data = await resp.json();
            videoId = data.video_id;
            console.log("[帧知] videoId:", videoId);
            updateStatus("⏳ 语音识别中...");
            pollStatus();
        } catch (e) {
            console.error("[帧知] 连接失败:", e);
            updateStatus("❌ 无法连接帧知");
            addMsg("error", "无法连接帧知服务<br>请确认：<br>1. 帧知后端已启动<br>2. 地址: " + API_BASE);
        }
    }

    function pollStatus() {
        var check = async function () {
            try {
                var resp = await fetch(API_BASE + "/api/videos/" + videoId);
                var data = await resp.json();
                console.log("[帧知] 状态:", data.status);

                if (data.status === "ready") {
                    window._fwReady = true;
                    updateStatus("✅ 就绪 (" + data.chunk_count + " 片段)");
                    var inp = document.getElementById("fw-input");
                    var snd = document.getElementById("fw-send");
                    var proc = document.getElementById("fw-process");
                    if (inp) { inp.disabled = false; inp.focus(); }
                    if (snd) snd.disabled = false;
                    if (proc) { proc.disabled = false; proc.style.opacity = "1"; }
                    loadHistory();
                    return;
                }
                if (data.status === "error") {
                    updateStatus("❌ 处理失败");
                    return;
                }
                setTimeout(check, 3000);
            } catch (e) {
                console.error("[帧知] 轮询失败:", e);
                setTimeout(check, 5000);
            }
        };
        check();
    }

    // ── 加载聊天历史 ──
    function loadHistory() {
        fetch(API_BASE + "/api/videos/" + videoId + "/history?limit=50")
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (!data || !data.length) return;
                var container = document.getElementById("fw-messages");
                if (!container) return;
                container.innerHTML = "";
                data.forEach(function (msg) {
                    if (msg.role === "user") {
                        addMsg("user", esc(msg.content));
                    } else if (msg.role === "assistant") {
                        addMsg("assistant", esc(msg.content));
                    }
                });
            }).catch(function (e) {
                console.error("[帧知] Failed to load history:", e);
            });
    }

    // ═══════════════════════════════════════════
    // 发送问题
    // ═══════════════════════════════════════════

    var isProcessing = false;

    async function sendQuestion() {
        var inp = document.getElementById("fw-input");
        if (!inp) return;
        var question = inp.value.trim();
        if (!question || !window._fwReady || isProcessing) return;

        isProcessing = true;
        inp.disabled = true;
        document.getElementById("fw-send").disabled = true;
        inp.value = "";

        var label = isVisionMode ? "🖼️ [画面] " : "";
        addMsg("user", label + question);

        var t = getCurrentTime();
        try {
            var endpoint = isVisionMode ? "ask_frame" : "ask";
            var body = { question: question, timestamp: t };

            // 画面模式：从 video 元素截图
            if (isVisionMode) {
                var frameB64 = captureFrame();
                if (frameB64) body.frame_base64 = frameB64;
            }

            var resp = await fetch(API_BASE + "/api/videos/" + videoId + "/" + endpoint, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body),
            });
            var data = await resp.json();

            var html = esc(data.answer);
            if (data.frame_description) {
                html = '<span style="background:#e17055;color:#fff;padding:2px 8px;' +
                       'border-radius:3px;font-size:11px;">🖼️ 画面分析</span><br>' + html;
            }
            if (data.references && data.references.length > 0) {
                html += '<div style="margin-top:10px;font-size:12px;color:#999;">📖 ';
                var seen = {};
                data.references.forEach(function (ref) {
                    var key = ref.start_time + "-" + ref.end_time;
                    if (seen[key]) return;
                    seen[key] = true;
                    var label = fmt(ref.start_time) + "~" + fmt(ref.end_time);
                    html += '<span class="fw-ts" data-s="' + ref.start_time +
                            '" style="display:inline-block;background:#00d2a0;color:#0f0f1a;' +
                            'padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;' +
                            'cursor:pointer;margin:1px;">' + label + "</span> ";
                });
                html += "</div>";
            }
            var msgDiv = addMsg("assistant", html);

            // 绑定时间戳点击
            setTimeout(function () {
                var tss = msgDiv.querySelectorAll(".fw-ts");
                tss.forEach(function (el) {
                    el.onclick = function () {
                        seekTo(parseFloat(this.getAttribute("data-s")));
                    };
                });
            }, 100);

        } catch (e) {
            addMsg("error", "请求失败: " + e.message);
        }

        inp.disabled = false;
        document.getElementById("fw-send").disabled = false;
        inp.style.height = "auto";  // reset textarea height
        inp.focus();
        isProcessing = false;
    }

    // ═══════════════════════════════════════════
    // 工具函数
    // ═══════════════════════════════════════════

    function addMsg(type, content) {
        var container = document.getElementById("fw-messages");
        if (!container) return document.createElement("div");
        var div = document.createElement("div");

        var styles = {
            system: "background:#252540;color:#999;",
            user: "background:#6c5ce7;color:#fff;align-self:flex-end;max-width:85%;",
            assistant: "background:#252540;border:1px solid #2a2a45;max-width:85%;",
            error: "background:#e74c3c;color:#fff;",
        };
        div.style.cssText = (styles[type] || styles.system) +
            "padding:10px 14px;border-radius:8px;line-height:1.6;font-size:13px;" +
            "word-break:break-word;overflow-wrap:anywhere;white-space:pre-wrap;" +
            "min-width:0;max-width:100%;";
        div.innerHTML = content;
        container.appendChild(div);
        container.scrollTop = container.scrollHeight;
        return div;
    }

    function updateStatus(text) {
        var el = document.getElementById("fw-status");
        if (el) el.textContent = text;
    }

    function fmt(s) {
        var m = Math.floor(s / 60);
        var sec = Math.floor(s % 60);
        return String(m).padStart(2, "0") + ":" + String(sec).padStart(2, "0");
    }

    function esc(text) {
        var div = document.createElement("div");
        div.textContent = text;
        return div.innerHTML;
    }

    console.log("[帧知] 插件加载完成");
})();
