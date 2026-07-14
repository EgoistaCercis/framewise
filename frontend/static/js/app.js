/**
 * 帧知 - 视频学习Agent 前端逻辑
 */
(function () {
    "use strict";

    // ── DOM元素 ──
    const videoFile = document.getElementById("videoFile");
    const uploadBtn = document.getElementById("uploadBtn");
    const uploadStatus = document.getElementById("uploadStatus");
    const videoPlaceholder = document.getElementById("videoPlaceholder");
    const videoContainer = document.getElementById("videoContainer");
    const videoPlayer = document.getElementById("videoPlayer");
    const processingOverlay = document.getElementById("processingOverlay");
    const processingDetail = document.getElementById("processingDetail");
    const chatMessages = document.getElementById("chatMessages");
    const questionInput = document.getElementById("questionInput");
    const sendBtn = document.getElementById("sendBtn");
    const modeToggle = document.getElementById("modeToggle");
    const videoName = document.getElementById("videoName");
    const subtitlesOverlay = document.getElementById("subtitlesOverlay");

    // ── 状态 ──
    let currentVideoId = null;
    let currentSubtitles = [];
    let isVisionMode = false;
    let isPolling = false;

    // ── 上传 ──
    videoFile.addEventListener("change", async (e) => {
        const file = e.target.files[0];
        if (!file) return;
        await uploadVideo(file);
    });

    uploadBtn.addEventListener("click", () => {
        videoFile.click();
    });

    async function uploadVideo(file) {
        const formData = new FormData();
        formData.append("file", file);

        uploadBtn.disabled = true;
        uploadStatus.textContent = "上传中...";
        showProcessing("上传中...", "正在上传视频文件");

        try {
            const resp = await fetch("/api/videos/upload", {
                method: "POST",
                body: formData,
            });

            if (!resp.ok) {
                const err = await resp.json();
                throw new Error(err.detail || "上传失败");
            }

            const data = await resp.json();
            currentVideoId = data.video_id;
            videoName.textContent = file.name;

            // 显示视频播放器
            videoPlayer.src = `/api/videos/${currentVideoId}/file`;
            videoPlayer.load();
            videoPlaceholder.style.display = "none";
            videoContainer.style.display = "flex";

            // 开始轮询处理状态
            pollProcessingStatus();

        } catch (err) {
            addMessage("error", `❌ 上传失败：${err.message}`);
            uploadBtn.disabled = false;
            uploadStatus.textContent = "";
            hideProcessing();
        }
    }

    // ── 处理状态轮询 ──
    async function pollProcessingStatus() {
        if (isPolling || !currentVideoId) return;
        isPolling = true;
        showProcessing("正在分析视频...", "语音识别中（faster-whisper），请稍候");

        const poll = async () => {
            try {
                const resp = await fetch(`/api/videos/${currentVideoId}`);
                const data = await resp.json();

                if (data.status === "ready") {
                    hideProcessing();
                    uploadBtn.disabled = false;
                    uploadStatus.textContent = `✅ 就绪 (${data.chunk_count} 个知识片段)`;
                    questionInput.disabled = false;
                    sendBtn.disabled = false;
                    questionInput.placeholder = "输入你的问题...";
                    questionInput.focus();

                    // 加载字幕
                    await loadSubtitles();
                    isPolling = false;
                    return;
                }

                if (data.status === "error") {
                    hideProcessing();
                    uploadBtn.disabled = false;
                    uploadStatus.textContent = "❌ 处理失败";
                    addMessage("error", "❌ 视频处理失败，请重试");
                    isPolling = false;
                    return;
                }

                // 更新处理提示
                processingDetail.textContent = "语音识别中（faster-whisper），请稍候...";
                setTimeout(poll, 2000);

            } catch (err) {
                processingDetail.textContent = "处理中...";
                setTimeout(poll, 3000);
            }
        };

        poll();
    }

    async function loadSubtitles() {
        try {
            const resp = await fetch(`/api/videos/${currentVideoId}/subtitles`);
            const data = await resp.json();
            currentSubtitles = data.subtitles || [];
        } catch (err) {
            console.error("Failed to load subtitles:", err);
        }
    }

    // ── 视频字幕同步 ──
    videoPlayer.addEventListener("timeupdate", () => {
        if (!currentSubtitles.length) return;

        const currentTime = videoPlayer.currentTime;
        let currentText = "";

        for (const seg of currentSubtitles) {
            if (currentTime >= seg.start && currentTime <= seg.end) {
                currentText = seg.text;
                break;
            }
        }

        subtitlesOverlay.textContent = currentText;
    });

    // ── 发送消息 ──
    sendBtn.addEventListener("click", () => sendQuestion());
    questionInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendQuestion();
        }
    });

    async function sendQuestion() {
        const question = questionInput.value.trim();
        if (!question || !currentVideoId) return;

        // 禁用输入
        questionInput.disabled = true;
        sendBtn.disabled = true;
        questionInput.value = "";

        // 显示用户消息
        const modeLabel = isVisionMode ? "🖼️ [画面提问]" : "";
        addMessage("user", `${modeLabel} ${question}`);

        const currentTime = videoPlayer.currentTime;

        try {
            if (isVisionMode) {
                // 画面提问
                const resp = await fetch(`/api/videos/${currentVideoId}/ask_frame`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        question: question,
                        timestamp: currentTime,
                    }),
                });
                const data = await resp.json();
                displayAnswer(data, true);
            } else {
                // 文本提问
                const resp = await fetch(`/api/videos/${currentVideoId}/ask`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        question: question,
                        timestamp: currentTime,
                    }),
                });
                const data = await resp.json();
                displayAnswer(data, false);
            }
        } catch (err) {
            addMessage("error", `❌ 请求失败：${err.message}`);
        }

        // 恢复输入
        questionInput.disabled = false;
        sendBtn.disabled = false;
        questionInput.focus();
    }

    function displayAnswer(data, hasFrame) {
        let html = "";

        if (hasFrame && data.frame_description) {
            html += `<span class="frame-badge">🖼️ 画面分析</span><br>`;
        }

        html += escapeHtml(data.answer);

        // 添加时间戳引用
        if (data.references && data.references.length > 0) {
            html += '<br><div style="margin-top:10px;font-size:12px;color:var(--text-secondary)">📖 参考片段：</div>';

            const seen = new Set();
            for (const ref of data.references) {
                const key = `${ref.start_time}-${ref.end_time}`;
                if (seen.has(key)) continue;
                seen.add(key);

                const timeLabel = formatTime(ref.start_time) + "~" + formatTime(ref.end_time);
                html += ` <span class="timestamp-link" onclick="window.seekTo(${ref.start_time})" title="${escapeHtml(ref.text.substring(0, 100))}">${timeLabel}</span>`;
            }
        }

        addMessage("assistant", html);
    }

    // ── 时间跳转 ──
    window.seekTo = function (seconds) {
        videoPlayer.currentTime = seconds;
        videoPlayer.play();
    };

    // ── 模式切换 ──
    modeToggle.addEventListener("click", () => {
        isVisionMode = !isVisionMode;
        if (isVisionMode) {
            modeToggle.textContent = "🖼️ 画面提问";
            modeToggle.classList.add("vision-mode");
            questionInput.placeholder = "暂停视频后，对当前画面提问...";
        } else {
            modeToggle.textContent = "📝 文本提问";
            modeToggle.classList.remove("vision-mode");
            questionInput.placeholder = "输入你的问题...";
        }
    });

    // ── 工具函数 ──
    function addMessage(type, content) {
        const div = document.createElement("div");
        div.className = `message ${type}-msg`;
        div.innerHTML = content;
        chatMessages.appendChild(div);
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    function formatTime(seconds) {
        const m = Math.floor(seconds / 60);
        const s = Math.floor(seconds % 60);
        return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
    }

    function escapeHtml(text) {
        const div = document.createElement("div");
        div.textContent = text;
        return div.innerHTML;
    }

    function showProcessing(title, detail) {
        processingOverlay.style.display = "flex";
        processingDetail.textContent = detail;
    }

    function hideProcessing() {
        processingOverlay.style.display = "none";
    }

    // ── 费用统计 ──
    const costDisplay = document.getElementById("costDisplay");

    async function refreshCost() {
        try {
            const resp = await fetch("/api/usage/today");
            const data = await resp.json();
            costDisplay.textContent = `💰 ¥${data.total_cost.toFixed(4)}`;
            costDisplay.title = `今日调用: ${data.calls}次\n输入: ${data.total_input_tokens} tokens\n输出: ${data.total_output_tokens} tokens`;
        } catch (e) {
            // 静默处理
        }
    }

    // 点击显示详情
    costDisplay.addEventListener("click", async () => {
        try {
            const [todayResp, totalResp, modelResp] = await Promise.all([
                fetch("/api/usage/today").then(r => r.json()),
                fetch("/api/usage/total").then(r => r.json()),
                fetch("/api/usage/by_model").then(r => r.json()),
            ]);

            let msg = `📊 **今日用量**\n`;
            msg += `调用: ${todayResp.calls}次 | `;
            msg += `输入: ${(todayResp.total_input_tokens/1000).toFixed(1)}K | `;
            msg += `输出: ${(todayResp.total_output_tokens/1000).toFixed(1)}K\n`;
            msg += `费用: **¥${todayResp.total_cost.toFixed(4)}**\n\n`;

            msg += `📈 **累计**\n`;
            msg += `调用: ${totalResp.calls}次 | 费用: ¥${totalResp.total_cost.toFixed(4)}\n\n`;

            msg += `🔧 **按模型**\n`;
            for (const m of modelResp) {
                msg += `${m.provider}/${m.model}: ${m.calls}次, ¥${m.total_cost.toFixed(4)}\n`;
            }

            addMessage("system", msg.replace(/\n/g, "<br>").replace(/\*\*(.+?)\*\*/g, "<b>$1</b>"));
        } catch (e) {
            addMessage("error", "获取费用统计失败");
        }
    });

    // 页面加载后获取费用，并定时刷新
    refreshCost();
    setInterval(refreshCost, 30000);  // 每30秒刷新
})();
