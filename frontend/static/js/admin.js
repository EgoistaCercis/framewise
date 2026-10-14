/**
 * 帧知 - 管理页：看试用者的使用情况
 *
 * ⚠️⚠️ **本文件里禁止出现 innerHTML。**
 *
 * 原因是这里渲染的是**别人（试用者）写的提问内容** —— 不可信输入。
 * 而这个页面的 localStorage 里正躺着**管理员密钥**。所以只要有一处用
 * innerHTML 拼字符串，一条 `<img src=x onerror=…>` 的提问就能在本页执行脚本、
 * 把管理员密钥读走 —— 等于把服务器 root 交出去。
 *
 * 全项目只有这一处把"他人输入"喂给前端渲染，所以规则在这里特别写死：
 * 数据一律用 textContent / createElement / appendChild，
 * 换行靠 CSS 的 white-space: pre-wrap，不去拼 <br>。
 */
(function () {
    "use strict";

    var currentWho = null;      // 当前展开的是谁，用来防止旧请求回来覆盖新面板

    // ── DOM ──
    var $ = function (id) { return document.getElementById(id); };

    function showNotice(text, kind) {
        var n = $("notice");
        n.textContent = text;                 // 不用 innerHTML：这句话里可能带后端返回的内容
        n.className = "notice" + (kind ? " notice-" + kind : "");
        n.hidden = false;
    }

    function clearNotice() { $("notice").hidden = true; }

    // ── 格式化 ──
    function fmtTokens(n) {
        n = n || 0;
        if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
        if (n >= 1e3) return Math.round(n / 1e3) + "k";
        return String(n);
    }

    function fmtCost(c) { return "¥" + (c || 0).toFixed(4); }

    function fmtTime(iso) {
        if (!iso) return "—";
        var t = Date.parse(iso);
        if (isNaN(t)) return iso;
        var mins = Math.floor((Date.now() - t) / 60000);
        if (mins < 1) return "刚刚";
        if (mins < 60) return mins + " 分钟前";
        var hours = Math.floor(mins / 60);
        if (hours < 24) return hours + " 小时前";
        return Math.floor(hours / 24) + " 天前";
    }

    // 状态 → 徽章样式。后端给的是中文标签，这里只决定颜色
    function badgeClass(status) {
        if (status === "今日活跃" || status === "我") return "badge badge-ok";
        if (status === "今日超限" || status === "已吊销") return "badge badge-warn";
        if (status.indexOf("沉寂") === 0) return "badge badge-muted";
        return "badge";
    }

    function cell(row, text, cls) {
        var td = document.createElement("td");
        if (cls) td.className = cls;
        td.textContent = text;
        row.appendChild(td);
        return td;
    }

    // ── 总览表 ──
    function renderUsers(data) {
        var tbody = $("userRows");
        tbody.textContent = "";               // 清空（比 innerHTML="" 更明确）
        $("generatedAt").textContent = "更新于 " + fmtTime(data.generated_at);

        data.users.forEach(function (u) {
            var tr = document.createElement("tr");
            tr.className = "row-clickable";

            // 「谁」：本人那两行加个记号，免得以为来了新用户
            var nameTd = document.createElement("td");
            var nameSpan = document.createElement("span");
            nameSpan.textContent = u.who + (u.is_me ? "（我）" : "");
            nameTd.appendChild(nameSpan);
            if (!u.configured && !u.is_me && u.who !== "(未归因)") {
                var gone = document.createElement("span");
                gone.className = "muted";
                gone.textContent = " · 密钥已不在配置里";
                nameTd.appendChild(gone);
            }
            tr.appendChild(nameTd);

            cell(tr, u.calls, "num");
            cell(tr, fmtTokens(u.input_tokens + u.output_tokens), "num");
            cell(tr, fmtCost(u.total_cost), "num cost-cell");
            cell(tr, u.today_tokens ? fmtTokens(u.today_tokens) : "—", "num");

            var when = cell(tr, fmtTime(u.last_at));
            when.title = u.last_at || "";

            var st = document.createElement("td");
            var badge = document.createElement("span");
            badge.className = badgeClass(u.status);
            badge.textContent = u.status;
            st.appendChild(badge);
            tr.appendChild(st);

            tr.onclick = function () { openDetail(u.who, tr); };
            tbody.appendChild(tr);
        });

        $("userTable").hidden = false;
    }

    // ── 下钻 ──
    function renderDetail(d) {
        $("detailWho").textContent = d.who + (d.shared_bucket ? "（本机/共享桶）" : "");
        var t = d.totals || {};
        $("detailMeta").textContent =
            "首次 " + fmtTime(t.first_at) + " · 最后 " + fmtTime(t.last_at) +
            " · 共 " + (t.calls || 0) + " 次调用 · " + fmtCost(t.total_cost);

        // 视频
        var vbody = $("videoRows");
        vbody.textContent = "";
        d.videos.forEach(function (v) {
            var tr = document.createElement("tr");
            var td = document.createElement("td");
            td.textContent = v.title || v.video_id;
            td.title = v.video_id;            // 标题可能被截断，悬浮能看到 id
            tr.appendChild(td);
            cell(tr, v.calls, "num");
            cell(tr, fmtCost(v.cost), "num cost-cell");
            cell(tr, v.msg_count || 0, "num");
            cell(tr, fmtTime(v.last_at));
            vbody.appendChild(tr);
        });
        $("videoTable").hidden = d.videos.length === 0;
        $("videoEmpty").hidden = d.videos.length > 0;

        // 最近提问 —— 这里是**不可信内容**，一律 textContent
        var list = $("questionList");
        list.textContent = "";
        d.recent_questions.forEach(function (q) {
            var item = document.createElement("div");
            item.className = "q-item";

            var meta = document.createElement("div");
            meta.className = "q-meta";
            meta.textContent = fmtTime(q.timestamp) + " · " + (q.title || q.video_id);
            item.appendChild(meta);

            var body = document.createElement("div");
            body.className = "q-text";        // white-space: pre-wrap，靠 CSS 换行
            body.textContent = q.content;     // ★ 唯一正确的方式
            item.appendChild(body);

            list.appendChild(item);
        });
        $("questionEmpty").hidden = d.recent_questions.length > 0;
        $("detailPanel").hidden = false;
    }

    function openDetail(who, tr) {
        currentWho = who;
        $("detailPanel").hidden = false;
        $("detailWho").textContent = who;
        $("detailMeta").textContent = "加载中…";
        $("videoRows").textContent = "";
        $("questionList").textContent = "";
        $("videoTable").hidden = true;
        $("questionEmpty").hidden = true;

        fetch("/api/admin/trial-users/" + encodeURIComponent(who))
            .then(function (r) {
                if (r.status === 403) throw new Error("当前密钥不是管理员密钥");
                if (!r.ok) throw new Error("后端返回 " + r.status);
                return r.json();
            })
            .then(function (d) {
                // 期间用户又点了别人 → 丢掉这次的结果，别覆盖新面板
                if (currentWho !== who) return;
                renderDetail(d);
            })
            .catch(function (e) {
                $("detailMeta").textContent = "";
                showNotice("打开 " + who + " 的详情失败：" + e.message, "warn");
            });
    }

    // ── 权限不足时的补救 ──
    function offerKeyReset() {
        showNotice("当前密钥不是管理员密钥 —— 看不了全站使用情况。", "warn");
        var btn = document.createElement("button");
        btn.className = "btn btn-primary";
        btn.textContent = "更换密钥";
        btn.style.marginTop = "10px";
        btn.onclick = function () {
            localStorage.removeItem("fw_api_key");
            if (window.fwResetAskedKey) window.fwResetAskedKey();
            clearNotice();
            load();      // 会再次 401 → 弹窗问密钥
        };
        $("notice").appendChild(btn);
    }

    function load() {
        clearNotice();
        $("userTable").hidden = true;
        $("detailPanel").hidden = true;
        currentWho = null;
        showNotice("加载中…");

        fetch("/api/admin/trial-users")
            .then(function (r) {
                if (r.status === 403) { offerKeyReset(); return null; }
                if (!r.ok) throw new Error("后端返回 " + r.status);
                return r.json();
            })
            .then(function (d) {
                if (!d) return;
                clearNotice();
                renderUsers(d);
            })
            .catch(function (e) {
                showNotice("加载失败：" + e.message, "warn");
            });
    }

    $("refreshBtn").onclick = load;
    $("closeDetail").onclick = function () {
        currentWho = null;
        $("detailPanel").hidden = true;
    };
    load();
})();
