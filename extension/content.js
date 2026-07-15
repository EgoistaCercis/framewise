/**
 * 帧知 - 浏览器插件
 * 在 B站/YouTube 视频页面注入 AI 问答侧边栏
 */
(function () {
    "use strict";

    const API_BASE = "http://127.0.0.1:8000";

    // ── 平台检测 ──
    const isBilibili = location.hostname.includes("bilibili.com");
    const isYoutube = location.hostname.includes("youtube.com");
    if (!isBilibili && !isYoutube) return;

    // ── 状态 ──
    let videoId = null;
    let isReady = false;
    let isProcessing = false;

    // ── 视频时间获取 ──
    function getCurrentTime() {
        if (isBilibili) {
            // B站播放器
            try {
                if (window.player && window.player.getCurrentTime) {
                    return window.player.getCurrentTime();
                }
            } catch (e) { /* fallback */ }
        }
        // 通用: <video> 元素
        const video = document.querySelector("video");
        return video ? video.currentTime : 0;
    }

    function seekTo(seconds) {
        if (isBilibili) {
            try {
                if (window.player && window.player.seek) {
                    window.player.seek(seconds);
                    return;
                }
            } catch (e) { /* fallback */ }
        }
        const video = document.querySelector("video");
        if (video) video.currentTime = seconds;
    }

    // ── UI 创建 ──
    function createPanel() {
        // 遮罩层
        const overlay = document.createElement("div");
        overlay.id = "framewise-overlay";

        // 面板
        const panel = document.createElement("div");
        panel.id = "framewise-panel";
        panel.innerHTML = `
            <div class="fw-header">
                <span class="fw-logo">🎬 帧知</span>
                <span class="fw-status" id="fw-status"></span>
                <button class="fw-close" id="fw-close">✕</button>
            </div>
            <div class="fw-messages" id="fw-messages">
                <div class="fw-msg fw-system">
                    <p>👋 在此观看视频时可以随时提问</p>
                    <p class="fw-hint">点击 <b>帧知</b> 图标展开面板</p>
                </div>
            </div>
            <div class="fw-input-area">
                <div class="fw-mode-row">
                    <button class="fw-mode-btn" id="fw-mode-text">📝 文本</button>
                    <button class="fw-mode-btn" id="fw-mode-vision">🖼️ 画面</button>
                    <span class="fw-time" id="fw-time">00:00</span>
                </div>
                <div class="fw-send-row">
                    <input type="text" id="fw-input"
                           placeholder="输入问题..." disabled>
                    <button id="fw-send" disabled>发送</button>
                </div>
            </div>
        `;

        // 触发按钮 (浮动圆按钮)
        const trigger = document.createElement("div");
        trigger.id = "fw-trigger";
        trigger.title = "帧知 - AI视频学习助手";
        trigger.innerHTML = "🎬";
        trigger.addEventListener("click", () => {
            overlay.style.display = "block";
            panel.style.display = "flex";
            trigger.style.display = "none";
        });

        // 关闭按钮
        overlay.addEventListener("click", (e) => {
            if (e.target === overlay) {
                overlay.style.display = "none";
                panel.style.display = "none";
                trigger.style.display = "flex";
            }
        });

        document.body.appendChild(overlay);
        document.body.appendChild(panel);
        document.body.appendChild(trigger);

        // 关闭按钮事件
        setTimeout(() => {
            const closeBtn = document.getElementById("fw-close");
            if (closeBtn) {
                closeBtn.addEventListener("click", () => {
                    overlay.style.display = "none";
                    panel.style.display = "none";
                    trigger.style.display = "flex";
                });
            }
        }, 0);

        return { overlay, panel, trigger };
    }

    // ── 初始化 ──
    const { overlay, panel, trigger } = createPanel();
    initVideo();

    async function initVideo() {
        // 从页面获取视频ID (BV号 或 YouTube ID)
        let vid = "";
        if (isBilibili) {
            const m = location.pathname.match(/\/video\/(BV\w+)/) ||
                      location.pathname.match(/\/bangumi\/play\/(\w+)/);
            vid = m ? m[1] : location.href.substring(0, 20);
        } else if (isYoutube) {
            const params = new URLSearchParams(location.search);
            vid = params.get("v") || location.href.substring(0, 20);
        }

        updateStatus("⏳ 正在建立索引...");
        document.getElementById("fw-input").disabled = true;
        document.getElementById("fw-send").disabled = true;

        try {
            const resp = await fetch(`${API_BASE}/api/videos/from_url`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ url: location.href }),
            });
            const data = await resp.json();
            videoId = data.video_id;
            updateStatus("⏳ 语音识别中...");
            await pollStatus();
        } catch (e) {
            updateStatus("❌ 连接失败");
            addMessage("error", `无法连接帧知服务 (${API_BASE})<br>请确认服务已启动`);
        }
    }

    async function pollStatus() {
        const poll = async () => {
            try {
                const resp = await fetch(`${API_BASE}/api/videos/${videoId}`);
                const data = await resp.json();

                if (data.status === "ready") {
                    isReady = true;
                    updateStatus(`✅ 就绪 (${data.chunk_count} 片段)`);
                    document.getElementById("fw-input").disabled = false;
                    document.getElementById("fw-send").disabled = false;
                    document.getElementById("fw-input").focus();
                    return;
                }

                if (data.status === "error") {
                    updateStatus("❌ 失败");
                    return;
                }

                // 更新时间估算
                const elapsed = data.chunk_count ? "索引中..." : "语音识别中...";
                updateStatus(`⏳ ${elapsed}`);
                setTimeout(poll, 3000);

            } catch (e) {
                setTimeout(poll, 5000);
            }
        };
        poll();
    }

    // ── 时间更新 ──
    setInterval(() => {
        if (!isReady) return;
        const t = getCurrentTime();
        const m = Math.floor(t / 60);
        const s = Math.floor(t % 60);
        document.getElementById("fw-time").textContent =
            `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
    }, 500);

    // ── 发送消息 ──
    let isVisionMode = false;
    document.getElementById("fw-mode-text").addEventListener("click", () => {
        isVisionMode = false;
        document.getElementById("fw-mode-text").classList.add("active");
        document.getElementById("fw-mode-vision").classList.remove("active");
    });
    document.getElementById("fw-mode-vision").addEventListener("click", () => {
        isVisionMode = true;
        document.getElementById("fw-mode-text").classList.remove("active");
        document.getElementById("fw-mode-vision").classList.add("active");
    });
    // 默认文本模式
    document.getElementById("fw-mode-text").classList.add("active");

    document.getElementById("fw-send").addEventListener("click", sendQuestion);
    document.getElementById("fw-input").addEventListener("keydown", (e) => {
        if (e.key === "Enter") sendQuestion();
    });

    async function sendQuestion() {
        const input = document.getElementById("fw-input");
        const question = input.value.trim();
        if (!question || !isReady || isProcessing) return;

        isProcessing = true;
        input.disabled = true;
        document.getElementById("fw-send").disabled = true;
        input.value = "";

        const modeLabel = isVisionMode ? "🖼️ [画面] " : "";
        addMessage("user", `${modeLabel}${question}`);

        const t = getCurrentTime();

        try {
            const endpoint = isVisionMode ? "ask_frame" : "ask";
            const body = isVisionMode
                ? { question, timestamp: t }
                : { question, timestamp: t };

            const resp = await fetch(`${API_BASE}/api/videos/${videoId}/${endpoint}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body),
            });
            const data = await resp.json();

            // 渲染回答
            let html = escapeHtml(data.answer);
            if (data.frame_description) {
                html = `<span class="fw-frame-badge">🖼️ 画面分析</span><br>` + html;
            }
            if (data.references && data.references.length > 0) {
                html += '<div class="fw-refs">📖 ';
                const seen = new Set();
                for (const ref of data.references) {
                    const key = `${ref.start_time}-${ref.end_time}`;
                    if (seen.has(key)) continue;
                    seen.add(key);
                    const label = formatTime(ref.start_time) + "~" + formatTime(ref.end_time);
                    html += `<span class="fw-timestamp" data-seek="${ref.start_time}">${label}</span> `;
                }
                html += '</div>';
            }
            addMessage("assistant", html);

            // 绑定时间戳点击
            document.querySelectorAll(".fw-timestamp[data-seek]").forEach(el => {
                el.addEventListener("click", function () {
                    seekTo(parseFloat(this.dataset.seek));
                });
            });

        } catch (e) {
            addMessage("error", `请求失败: ${e.message}`);
        }

        input.disabled = false;
        document.getElementById("fw-send").disabled = false;
        input.focus();
        isProcessing = false;
    }

    // ── 工具函数 ──
    function addMessage(type, content) {
        const container = document.getElementById("fw-messages");
        const div = document.createElement("div");
        div.className = `fw-msg fw-${type}`;
        div.innerHTML = content;
        container.appendChild(div);
        container.scrollTop = container.scrollHeight;
    }

    function updateStatus(text) {
        const el = document.getElementById("fw-status");
        if (el) el.textContent = text;
    }

    function formatTime(s) {
        const m = Math.floor(s / 60);
        const sec = Math.floor(s % 60);
        return `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
    }

    function escapeHtml(text) {
        const div = document.createElement("div");
        div.textContent = text;
        return div.innerHTML;
    }
})();
