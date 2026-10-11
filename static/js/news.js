(() => {
  const $ = (sel) => document.querySelector(sel);
  const alertEl = $("#alert");

  function showAlert(msg) {
    alertEl.hidden = !msg;
    alertEl.textContent = msg || "";
  }

  function esc(s) {
    return String(s ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function setBusy(btn, on, idleLabel, busyLabel) {
    const label = btn?.querySelector(".btn-label");
    const spinner = btn?.querySelector(".btn-spinner");
    if (btn) btn.disabled = on;
    if (label) label.textContent = on ? busyLabel : idleLabel;
    if (spinner) spinner.hidden = !on;
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  let activeJobId = null;
  let hideControlsJobId = null;

  function setJobControls({ pauseResume, showStop, stopping }) {
    const toggleBtn = $("#btnPauseResume");
    const stopBtn = $("#btnCancelJob");
    if (toggleBtn) {
      if (pauseResume === "pause" || pauseResume === "resume") {
        toggleBtn.hidden = false;
        toggleBtn.disabled = false;
        toggleBtn.dataset.mode = pauseResume;
        toggleBtn.textContent = pauseResume === "resume" ? "Resume" : "Pause";
      } else {
        toggleBtn.hidden = true;
        toggleBtn.disabled = true;
      }
    }
    if (stopBtn) {
      stopBtn.hidden = !showStop && !stopping;
      stopBtn.disabled = !!stopping || !showStop;
      stopBtn.textContent = stopping ? "Stopping…" : "Stop";
    }
  }

  async function togglePauseResume() {
    const mode = $("#btnPauseResume")?.dataset?.mode || "pause";
    if (mode === "resume") await resumeActiveJob();
    else await pauseActiveJob();
  }

  async function postJobAction(action, msg) {
    if (!activeJobId) return;
    try {
      const res = await fetch(`/api/jobs/${activeJobId}/${action}`, { method: "POST" });
      const data = await res.json();
      if (!data.ok) {
        showAlert(data.error || `Could not ${action}.`);
        return;
      }
      if (data.job) showJobBar(data.job);
      if (msg) showAlert(msg);
    } catch (err) {
      showAlert(String(err));
    }
  }

  async function cancelActiveJob() {
    if (!activeJobId) return;
    hideControlsJobId = activeJobId;
    setJobControls({ pauseResume: null, showStop: false, stopping: false });
    await postJobAction("cancel", "Cancelled.");
  }

  async function pauseActiveJob() {
    await postJobAction("pause", "Paused — click Resume to continue.");
  }

  async function resumeActiveJob() {
    await postJobAction("resume", "Resuming…");
  }

  function showJobBar(job) {
    const bar = $("#jobBar");
    if (!bar) return;
    bar.hidden = false;
    if (job.id) activeJobId = job.id;
    if (job.id && job.id !== hideControlsJobId) hideControlsJobId = null;
    const paused = !!job.paused || job.status === "paused";
    bar.classList.toggle("is-paused", paused);
    $("#jobBarLabel").textContent = job.label || "Job";
    const total = job.total || 0;
    const prog = job.progress || 0;
    $("#jobBarMeta").textContent = total ? `${prog}/${total}` : "…";
    $("#jobBarText").textContent = job.message || "Working…";
    const fill = $("#jobBarFill");
    if (fill) {
      const pct = total ? Math.min(100, Math.round((prog / total) * 100)) : 12;
      fill.style.width = `${pct}%`;
    }
    const terminal =
      job.status === "done" ||
      job.status === "cancelled" ||
      job.status === "error" ||
      !!job.cancel_requested ||
      (!!job.id && job.id === hideControlsJobId);
    const active =
      !terminal &&
      (job.status === "running" ||
        job.status === "queued" ||
        job.status === "paused");
    if (active && paused) {
      setJobControls({ pauseResume: "resume", showStop: true, stopping: false });
    } else if (active) {
      setJobControls({ pauseResume: "pause", showStop: true, stopping: false });
    } else {
      setJobControls({ pauseResume: null, showStop: false, stopping: false });
    }
  }

  function hideJobBar() {
    setJobControls({ pauseResume: null, showStop: false, stopping: false });
    activeJobId = null;
    const bar = $("#jobBar");
    if (bar) {
      bar.hidden = true;
      bar.classList.remove("is-paused");
    }
  }

  async function pollJob(jobId) {
    activeJobId = jobId;
    for (;;) {
      const res = await fetch(`/api/jobs/${jobId}`);
      const data = await res.json();
      if (!data.ok || !data.job) throw new Error(data.error || "Job lost.");
      const job = data.job;
      showJobBar(job);
      if (job.status === "done" || job.status === "cancelled") return job;
      if (job.status === "error") throw new Error(job.error || "Job failed.");
      await sleep(900);
    }
  }

  function selectedSources() {
    return [...document.querySelectorAll('input[name="newsSource"]:checked')].map(
      (el) => el.value
    );
  }

  function syncSourceCheckboxes(available) {
    const box = $("#newsSourceFilters");
    if (!box || !Array.isArray(available) || !available.length) return;
    const existing = new Set(
      [...box.querySelectorAll('input[name="newsSource"]')].map((el) => el.value)
    );
    for (const src of available) {
      if (!src || existing.has(src)) continue;
      const label = document.createElement("label");
      label.className = "check-inline";
      label.innerHTML = `<input type="checkbox" name="newsSource" value="${esc(src)}" checked><span>${esc(src)}</span>`;
      box.appendChild(label);
      label.querySelector("input")?.addEventListener("change", loadNews);
    }
  }

  function renderNews(items, counts) {
    const root = $("#newsFeed");
    const sub = $("#newsSub");
    const total = counts?.total ?? items.length;
    const matched = counts?.matched ?? items.filter((n) => n.player_id).length;
    const unmatched = counts?.unmatched ?? Math.max(0, total - matched);
    sub.textContent = `${items.length} shown · ${total} saved · ${matched} matched · ${unmatched} unmatched`;

    if (!items.length) {
      root.innerHTML = `<section class="panel"><p class="muted">No news for this filter. Click Refresh news (Chrome debug must be open).</p></section>`;
      return;
    }

    root.innerHTML = items
      .map((n) => {
        const name = n.matched_name || n.player_name || "Unknown player";
        const meta = [
          n.published_at || n.published_iso || "—",
          n.team_name || n.nba_team || null,
          n.source || null,
          n.player_id ? "linked to player" : "not in DB",
        ]
          .filter(Boolean)
          .join(" · ");
        const nameHtml = n.player_id
          ? `<a class="news-player-link" href="/players?player=${n.player_id}">${esc(name)}</a>`
          : esc(name);
        return `<article class="news-card news-feed-card${n.player_id ? " is-linked" : ""}">
          <div class="news-feed-head">
            <h3>${nameHtml}</h3>
            ${n.title ? `<p class="news-headline">${esc(n.title)}</p>` : ""}
          </div>
          <div class="subline">${esc(meta)}</div>
          ${n.body ? `<p>${esc(n.body)}</p>` : ""}
        </article>`;
      })
      .join("");
  }

  async function loadNews() {
    showAlert("");
    const filter = $("#newsFilter")?.value || "all";
    const sources = selectedSources();
    const qs = new URLSearchParams({ limit: "200" });
    if (filter === "matched") qs.set("match", "matched");
    if (filter === "unmatched") qs.set("match", "unmatched");
    if (sources.length) qs.set("sources", sources.join(","));
    else {
      renderNews([], { total: 0, matched: 0, unmatched: 0 });
      showAlert("Select at least one news source.");
      return;
    }
    try {
      const res = await fetch(`/api/db/news?${qs}`);
      const data = await res.json();
      if (!data.ok) {
        showAlert(data.error || "Could not load news.");
        return;
      }
      syncSourceCheckboxes(data.available_sources || data.sources || []);
      renderNews(data.news || [], data);
    } catch (err) {
      showAlert(String(err));
    }
  }

  async function refreshNews() {
    const btn = $("#btnRefreshNews");
    setBusy(btn, true, "Refresh news", "Fetching…");
    showAlert("");
    try {
      const res = await fetch("/api/db/news/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nba: true, cbs: true }),
      });
      const data = await res.json();
      if (!data.ok) {
        showAlert(data.error || "Could not start news refresh.");
        hideJobBar();
        return;
      }
      const jobId = data.job?.id || data.job_id;
      if (jobId) {
        const job = await pollJob(jobId);
        if (job.status === "cancelled") {
          showAlert("News refresh cancelled.");
        } else {
          const r = job.result || {};
          showAlert(
            `Fetched ${r.fetched ?? 0}: ${r.inserted ?? 0} new, ${r.skipped ?? 0} already known, ${r.player_news_inserted ?? 0} added to player details.`
          );
        }
      }
      await loadNews();
    } catch (err) {
      showAlert(String(err));
    } finally {
      setBusy(btn, false, "Refresh news", "Fetching…");
      hideJobBar();
    }
  }

  async function syncNewsToPlayers() {
    const btn = $("#btnSyncNews");
    setBusy(btn, true, "Sync to players", "Syncing…");
    showAlert("");
    try {
      const res = await fetch("/api/db/news/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      const data = await res.json();
      if (!data.ok) {
        showAlert(data.error || "Could not sync news.");
        return;
      }
      const linked = data.relinked ?? 0;
      const toPlayers = data.player_news_synced ?? 0;
      const left = data.still_unmatched ?? data.unmatched ?? 0;
      showAlert(
        linked || toPlayers
          ? `Linked ${linked} news item${linked === 1 ? "" : "s"} to DB players · ${toPlayers} added to player details · ${left} still unmatched.`
          : `No new links — ${left} news item${left === 1 ? "" : "s"} still unmatched.`
      );
      await loadNews();
    } catch (err) {
      showAlert(String(err));
    } finally {
      setBusy(btn, false, "Sync to players", "Syncing…");
    }
  }

  $("#btnPauseResume")?.addEventListener("click", togglePauseResume);
  $("#btnCancelJob")?.addEventListener("click", cancelActiveJob);
  $("#btnRefreshNews")?.addEventListener("click", refreshNews);
  $("#btnSyncNews")?.addEventListener("click", syncNewsToPlayers);
  $("#btnReloadNews")?.addEventListener("click", loadNews);
  $("#newsFilter")?.addEventListener("change", loadNews);
  document.querySelectorAll('input[name="newsSource"]').forEach((el) => {
    el.addEventListener("change", loadNews);
  });
  loadNews();
})();
