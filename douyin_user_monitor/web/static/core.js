const request = window.ShortDramaAPI.request;
    const app = document.getElementById("app");
    const notice = document.getElementById("notice");
    const route = location.pathname;
    let showOptions = [];
    let editingAccountId = null;
    let selectedReviewIds = new Set();
    let accountRefreshTimer = null;
    const showLibraryFilters = {accountId:"", ignored:"normal", q:"", sort:"recent"};

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>'"]/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;","\"":"&quot;"}[char]));
    }
    function safeMediaUrl(value) {
      try {
        const url = new URL(String(value || ""));
        return ["http:", "https:"].includes(url.protocol) ? url.href : "";
      } catch (_) {
        return "";
      }
    }
    function mediaThumb(url, label, variant = "") {
      const safeUrl = safeMediaUrl(url);
      const fallback = Array.from(String(label || "?").trim())[0] || "?";
      const classes = `media-thumb ${variant}`.trim();
      if (!safeUrl) return `<span class="${classes} media-fallback" aria-hidden="true">${escapeHtml(fallback)}</span>`;
      return `<span class="${classes}"><img src="${escapeHtml(safeUrl)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.hidden=true;this.nextElementSibling.hidden=false" /><span class="media-fallback" hidden aria-hidden="true">${escapeHtml(fallback)}</span></span>`;
    }
    function formatTime(value, fallback = "-") {
      if (!value) return fallback;
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return value;
      return new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(date);
    }
    function message(text, isError = false) {
      notice.textContent = text;
      notice.className = isError ? "notice error" : "notice";
      notice.style.display = "block";
      setTimeout(() => { notice.style.display = "none"; }, 3500);
    }
    function nav(name, title) {
      document.querySelectorAll("[data-nav]").forEach(item => item.classList.toggle("active", item.dataset.nav === name));
      document.querySelectorAll("[data-bottom-nav]").forEach(item => item.classList.toggle("active", item.dataset.bottomNav === name));
      document.getElementById("topTitle").textContent = title;
    }
    async function refreshStatus() {
      try {
        const [status, updates] = await Promise.all([request("/status"), request("/updates?following_only=false&page_size=1")]);
        document.getElementById("reviewCount").textContent = status.pending_review;
        document.getElementById("updateCount").textContent = updates.unread_count;
        document.getElementById("sidebarStatus").textContent = `调度：${status.scheduler} · 上次巡检：${formatTime(status.last_check_at, "未开始")}`;
        document.getElementById("systemState").textContent = `${status.enabled_accounts}/${status.accounts} 个账号启用`;
        return status;
      } catch (error) {
        document.getElementById("sidebarStatus").textContent = "系统状态不可用";
        return null;
      }
    }
    function empty(text) { return `<div class="empty">${escapeHtml(text)}</div>`; }
    function statusBadge(status) { return `<span class="badge ${escapeHtml(status)}">${escapeHtml({updating:"追更中", completed:"已完结", paused:"已暂停"}[status] || status)}</span>`; }
    function classificationBadge(status) { return `<span class="badge ${escapeHtml(status)}">${escapeHtml({matched:"已匹配", ignored:"已忽略", review:"待审核"}[status] || status)}</span>`; }
    function historyStatusBadge(status) { return `<span class="badge ${escapeHtml(status)}">${escapeHtml({idle:"未开始", pending:"等待执行", running:"补全中", paused:"已暂停", completed:"历史扫描完成", failed:"补全失败"}[status] || status)}</span>`; }
    function historyProgress(history) {
      const status = String(history.status || "idle");
      const processedPages = Math.max(0, Number(history.processed_pages) || 0);
      const scannedItems = Math.max(0, Number(history.scanned_items) || 0);
      const newVideos = Math.max(0, Number(history.new_videos) || 0);
      const completed = status === "completed" || (status !== "idle" && history.has_more === false);
      const progressStatus = completed ? "completed" : ["pending", "running", "paused", "failed"].includes(status) ? status : "idle";
      const state = completed ? "已完成" : ({idle:"未开始", pending:"等待执行", running:"总页数未知，仍有下一页", paused:"已暂停，可继续", failed:"补全失败，可继续重试"}[status] || status);
      const detail = status === "idle"
        ? "尚未开始历史扫描"
        : `${completed ? "共扫描" : "已扫描"} ${processedPages} 页 · ${scannedItems} 条作品 · 新增 ${newVideos} 条${completed ? "" : "；抖音未提供历史总页数"}`;
      const ariaText = `历史补全${state}，${detail}`;
      const progressLabel = completed ? "100%" : status === "idle" ? "0%" : "总量未知";
      const ariaValue = completed ? " aria-valuemin=\"0\" aria-valuemax=\"100\" aria-valuenow=\"100\"" : status === "idle" ? " aria-valuemin=\"0\" aria-valuemax=\"100\" aria-valuenow=\"0\"" : " aria-valuetext=\"总页数未知\"";
      return `<div class="history-progress" data-history-progress="${escapeHtml(progressStatus)}"><div class="history-progress-head"><strong>${progressLabel}</strong><span>${escapeHtml(state)}</span></div><div class="history-progress-track" role="progressbar" aria-label="${escapeHtml(ariaText)}"${ariaValue}><span class="history-progress-bar ${escapeHtml(progressStatus)}"></span></div><div class="history-progress-detail">${escapeHtml(detail)}</div></div>`;
    }
    function contentTypeLabel(type) { return {episode:"正式剧集", trailer:"预告/先行", show_content:"短剧内容", unknown:"待判断", non_drama:"普通视频"}[type] || type || "-"; }
    function videoDescription(video) {
      const displayTitle = String(video.display_title || "").trim();
      const description = String(video.description || "").trim();
      const title = displayTitle || description || "（无描述）";
      const rawDescription = displayTitle && description && displayTitle !== description ? `<span class="muted">描述：${escapeHtml(description)}</span>` : "";
      return `<div class="video-description"><a class="display-title" href="${escapeHtml(video.video_url)}" target="_blank" rel="noreferrer">${escapeHtml(title)}</a>${rawDescription}<span class="muted">${escapeHtml(video.hashtags.join(" #"))}</span></div>`;
    }
    function parserEvidence(evidence) {
      const entries = [["剧名", evidence && evidence.show], ["集数", evidence && evidence.episode]].filter(([, item]) => item && item.value);
      if (!entries.length) return `<span class="muted">暂无证据</span>`;
      return `<div class="parser-evidence">${entries.map(([label, item]) => `<span><strong>${escapeHtml(label)}：</strong>${escapeHtml(item.value)}<br />来源：${escapeHtml(item.source_field || "-")}</span>`).join("")}</div>`;
    }
    function reviewJudgements(video) {
      const evidence = video.parser_evidence || {};
      const regex = evidence.regex_result || {};
      const ai = evidence.llm_result || {};
      const regexText = regex.reason || (video.parser_method === "llm" ? "-" : video.parser_reason) || "-";
      const aiEpisode = ai.episode_number === null || ai.episode_number === undefined ? "" : ` · 第${ai.episode_number}集`;
      const aiText = ai.error ? `调用失败（${ai.error}）` : ai.show_title ? `${ai.show_title}${aiEpisode}` : contentTypeLabel(ai.content_type);
      const confidence = typeof ai.confidence === "number" ? `${Math.round(ai.confidence * 100)}%` : "-";
      return `<div class="parser-evidence"><span><strong>规则：</strong>${escapeHtml(regexText)}</span><span><strong>AI：</strong>${escapeHtml(aiText || "-")}<br /><strong>置信度：</strong>${escapeHtml(confidence)}<br /><strong>原因：</strong>${escapeHtml(ai.reason || ai.error || "-")}</span></div>`;
    }

