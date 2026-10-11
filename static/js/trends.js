(() => {
  const $ = (sel) => document.querySelector(sel);
  const alertEl = $("#alert");

  let trendsData = null;
  /** @type {Record<string, { key: string, dir: 'asc' | 'desc' }>} */
  const sortState = {
    cbs_viewed: { key: "today_views", dir: "desc" },
    yahoo_transactions: { key: "total", dir: "desc" },
  };

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

  function cell(v) {
    const s = esc(v);
    return s || "—";
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

  function parseNum(v) {
    if (v == null || v === "") return null;
    const n = Number(String(v).replace(/[,%]/g, "").trim());
    return Number.isFinite(n) ? n : null;
  }

  function sortPlayers(players, key, dir) {
    const mult = dir === "asc" ? 1 : -1;
    return [...players].sort((a, b) => {
      if (key === "player_name") {
        const an = String(a.matched_name || a.player_name || "").toLowerCase();
        const bn = String(b.matched_name || b.player_name || "").toLowerCase();
        return an.localeCompare(bn) * mult;
      }
      if (key === "roster_status" || key === "positions" || key === "nba_team") {
        return String(a[key] || "").localeCompare(String(b[key] || "")) * mult;
      }
      const av = parseNum(a[key]);
      const bv = parseNum(b[key]);
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (av === bv) {
        return (parseNum(a.rank) || 0) - (parseNum(b.rank) || 0);
      }
      return (av - bv) * mult;
    });
  }

  function sortTh(source, key, label, { num = false } = {}) {
    const state = sortState[source] || {};
    const active = state.key === key;
    const arrow = !active ? "" : state.dir === "asc" ? " ▲" : " ▼";
    const cls = ["sortable-th", num ? "num" : "", active ? "is-sorted" : ""]
      .filter(Boolean)
      .join(" ");
    return `<th class="${cls}" data-sort-source="${esc(source)}" data-sort-key="${esc(key)}" role="columnheader" tabindex="0" title="Sort by ${esc(label)}">${esc(label)}${arrow}</th>`;
  }

  function playerCell(p) {
    const name = p.matched_name || p.player_name || "—";
    const nameHtml = p.player_id
      ? `<a class="news-player-link" href="/players?player=${p.player_id}">${esc(name)}</a>`
      : `<strong>${esc(name)}</strong>`;
    const meta = [p.positions, p.nba_team].filter(Boolean).join(" · ");
    return `${nameHtml}${meta ? `<div class="subline">${esc(meta)}</div>` : ""}`;
  }

  function cbsTable(players, source) {
    const state = sortState[source] || { key: "today_views", dir: "desc" };
    const sorted = sortPlayers(players, state.key, state.dir);
    const rows = sorted
      .map(
        (p) => `<tr>
          <td class="num">${cell(p.rank)}</td>
          <td>${playerCell(p)}</td>
          <td class="num">${cell(p.recent_views)}</td>
          <td class="num">${cell(p.today_views)}</td>
        </tr>`
      )
      .join("");
    return `<div class="table-wrap">
      <table class="players-table sortable-table">
        <thead>
          <tr>
            ${sortTh(source, "rank", "#", { num: true })}
            ${sortTh(source, "player_name", "Player")}
            ${sortTh(source, "recent_views", "Recent views", { num: true })}
            ${sortTh(source, "today_views", "Today's views", { num: true })}
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
  }

  function yahooTable(players, source) {
    const state = sortState[source] || { key: "total", dir: "desc" };
    const sorted = sortPlayers(players, state.key, state.dir);
    const rows = sorted
      .map(
        (p) => `<tr>
          <td class="num">${cell(p.rank)}</td>
          <td>${playerCell(p)}</td>
          <td>${cell(p.roster_status)}</td>
          <td class="num">${cell(p.owned_pct)}</td>
          <td class="num">${cell(p.start_pct)}</td>
          <td class="num">${cell(p.adds)}</td>
          <td class="num">${cell(p.drops)}</td>
          <td class="num">${cell(p.trades)}</td>
          <td class="num">${cell(p.total)}</td>
        </tr>`
      )
      .join("");
    return `<div class="table-wrap">
      <table class="players-table sortable-table">
        <thead>
          <tr>
            ${sortTh(source, "rank", "#", { num: true })}
            ${sortTh(source, "player_name", "Player")}
            ${sortTh(source, "roster_status", "Status")}
            ${sortTh(source, "owned_pct", "% Ros", { num: true })}
            ${sortTh(source, "start_pct", "% Start", { num: true })}
            ${sortTh(source, "adds", "Adds", { num: true })}
            ${sortTh(source, "drops", "Drops", { num: true })}
            ${sortTh(source, "trades", "Trades", { num: true })}
            ${sortTh(source, "total", "Total", { num: true })}
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
  }

  function renderSection(section) {
    const players = section.players || [];
    const label = section.source_label || section.source || "Trends";
    const url = section.source_url || "";
    const scraped = section.scraped_at ? ` · scraped ${section.scraped_at}` : "";
    const source = section.source || "unknown";
    const isCbs = source === "cbs_viewed";
    const table = players.length
      ? isCbs
        ? cbsTable(players, source)
        : yahooTable(players, source)
      : `<p class="muted">No rows yet.</p>`;
    const sourceHtml = url
      ? `<a class="trends-source-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>`
      : esc(label);
    return `<section class="panel trends-panel" data-trends-source="${esc(source)}">
      <div class="panel-head">
        <div>
          <h2>${sourceHtml}</h2>
          <p class="panel-sub">Top ${players.length}${scraped}</p>
        </div>
      </div>
      ${table}
    </section>`;
  }

  function bindSortHeaders(root) {
    root.querySelectorAll(".sortable-th").forEach((th) => {
      const activate = () => {
        const source = th.dataset.sortSource;
        const key = th.dataset.sortKey;
        if (!source || !key) return;
        const prev = sortState[source] || { key: "", dir: "desc" };
        if (prev.key === key) {
          sortState[source] = { key, dir: prev.dir === "desc" ? "asc" : "desc" };
        } else {
          // Numeric cols default to desc; names/status to asc.
          const ascDefault = key === "player_name" || key === "roster_status" || key === "rank";
          sortState[source] = { key, dir: ascDefault ? "asc" : "desc" };
        }
        if (trendsData) renderTrends(trendsData);
      };
      th.addEventListener("click", activate);
      th.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          activate();
        }
      });
    });
  }

  function renderTrends(data) {
    const root = $("#trendsSections");
    const sub = $("#trendsSub");
    const sections = data.sections || [];
    const count = data.player_count || 0;
    sub.textContent = sections.length
      ? `${sections.length} sources · ${count} players`
      : "No trends saved yet. Click Refresh trends.";

    if (!sections.length) {
      root.innerHTML = `<section class="panel"><p class="muted">Fetch CBS Most Viewed and Yahoo Transaction Trends to populate this page.</p></section>`;
      return;
    }
    root.innerHTML = sections.map(renderSection).join("");
    bindSortHeaders(root);
  }

  async function loadTrends() {
    showAlert("");
    try {
      const res = await fetch("/api/db/trends");
      const data = await res.json();
      if (!data.ok) {
        showAlert(data.error || "Could not load trends.");
        return;
      }
      trendsData = data;
      renderTrends(data);
    } catch (err) {
      showAlert(String(err));
    }
  }

  async function refreshTrends() {
    const btn = $("#btnRefreshTrends");
    setBusy(btn, true, "Refresh trends", "Fetching…");
    showAlert("");
    try {
      const res = await fetch("/api/db/trends/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      const data = await res.json();
      if (!data.ok) {
        showAlert(data.error || "Could not start trends refresh.");
        hideJobBar();
        return;
      }
      const jobId = data.job?.id || data.job_id;
      if (jobId) {
        const job = await pollJob(jobId);
        if (job.status === "cancelled") {
          showAlert("Trends refresh cancelled.");
        } else {
          const r = job.result || {};
          const cbs = r.by_source?.cbs?.inserted ?? "—";
          const yahoo = r.by_source?.yahoo?.inserted ?? "—";
          showAlert(`Saved CBS ${cbs} + Yahoo ${yahoo} (matched ${r.matched ?? 0} to DB).`);
        }
      }
      await loadTrends();
    } catch (err) {
      showAlert(String(err));
    } finally {
      setBusy(btn, false, "Refresh trends", "Fetching…");
      hideJobBar();
    }
  }

  $("#btnPauseResume")?.addEventListener("click", togglePauseResume);
  $("#btnCancelJob")?.addEventListener("click", cancelActiveJob);
  $("#btnRefreshTrends")?.addEventListener("click", refreshTrends);
  $("#btnReloadTrends")?.addEventListener("click", loadTrends);
  loadTrends();
})();
