(() => {
  const $ = (sel) => document.querySelector(sel);
  const alertEl = $("#alert");

  const STAT_COLS = [
    { key: "fgm_a", label: "FGM/A*" },
    { key: "fg_pct", label: "FG%" },
    { key: "ftm_a", label: "FTM/A*" },
    { key: "ft_pct", label: "FT%" },
    { key: "threes", label: "3PTM" },
    { key: "pts", label: "PTS" },
    { key: "reb", label: "REB" },
    { key: "ast", label: "AST" },
    { key: "stl", label: "ST" },
    { key: "blk", label: "BLK" },
    { key: "tov", label: "TO" },
  ];

  let standingsData = null;
  /** @type {'total' | 'avg'} */
  let statsView = "total";
  /** @type {{ key: string, dir: 'asc' | 'desc' }} */
  let sortState = { key: "rank", dir: "asc" };

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
    if (v == null) return "—";
    const s = esc(String(v).trim());
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

  function fmtWhen(iso) {
    if (!iso) return "";
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  }

  function parseNum(v) {
    if (v == null || v === "") return null;
    const s = String(v).replace(/[,%]/g, "").trim();
    // FGM/A style → use made or percentage of made/attempted for sort? use made.
    if (s.includes("/")) {
      const left = Number(s.split("/")[0]);
      return Number.isFinite(left) ? left : null;
    }
    const n = Number(s);
    return Number.isFinite(n) ? n : null;
  }

  function statValue(row, key) {
    if (statsView === "avg") {
      // Only show true averages — do not fall back to season totals.
      return row[`avg_${key}`] ?? "";
    }
    return row[`total_${key}`] ?? row[key] ?? "";
  }

  function sortValue(row, key) {
    if (key === "name" || key === "manager") return String(row[key] || "");
    if (key === "wlt" || key === "last_week") return String(row[key] || "");
    if (STAT_COLS.some((c) => c.key === key)) return parseNum(statValue(row, key));
    return parseNum(row[key]);
  }

  function sortRows(rows, key, dir) {
    const mult = dir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      if (key === "name" || key === "manager" || key === "wlt" || key === "last_week") {
        return String(sortValue(a, key)).localeCompare(String(sortValue(b, key))) * mult;
      }
      const av = sortValue(a, key);
      const bv = sortValue(b, key);
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (av === bv) {
        return (parseNum(a.rank) || 0) - (parseNum(b.rank) || 0);
      }
      return (av - bv) * mult;
    });
  }

  function sortTh(key, label, { num = false } = {}) {
    const active = sortState.key === key;
    const arrow = !active ? "" : sortState.dir === "asc" ? " ▲" : " ▼";
    const cls = ["sortable-th", num ? "num" : "", active ? "is-sorted" : ""]
      .filter(Boolean)
      .join(" ");
    return `<th class="${cls}" data-sort-key="${esc(key)}" role="columnheader" tabindex="0" title="Sort by ${esc(label)}">${esc(label)}${arrow}</th>`;
  }

  function bindSortHeaders() {
    const table = $("#standingsDetailTable");
    if (!table) return;
    table.querySelectorAll(".sortable-th").forEach((th) => {
      const activate = () => {
        const key = th.dataset.sortKey;
        if (!key) return;
        if (sortState.key === key) {
          sortState = { key, dir: sortState.dir === "desc" ? "asc" : "desc" };
        } else {
          const ascDefault =
            key === "rank" ||
            key === "name" ||
            key === "wlt" ||
            key === "waiver" ||
            key === "tov";
          sortState = { key, dir: ascDefault ? "asc" : "desc" };
        }
        if (standingsData) renderStandings(standingsData);
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

  function setViewButtons() {
    $("#btnViewTotal")?.classList.toggle("is-on", statsView === "total");
    $("#btnViewAvg")?.classList.toggle("is-on", statsView === "avg");
  }

  function renderStandings(data) {
    standingsData = data;
    setViewButtons();
    const head = $("#standingsDetailTable thead tr");
    const body = $("#standingsDetailBody");
    const sub = $("#standingsDetailSub");
    const rows = data.rows || [];
    const when = fmtWhen(data.scraped_at);
    const viewLabel = statsView === "avg" ? "averages" : "totals";

    if (!rows.length) {
      sub.textContent = data.note || "No standings yet — click Refresh standings.";
      if (head) head.innerHTML = "";
      body.innerHTML = `<tr><td class="muted">No detailed standings yet. Open Chrome debug, then click Refresh standings.</td></tr>`;
      return;
    }

    const hasRealStats = rows.some((r) =>
      STAT_COLS.some((c) => {
        const v = String(statValue(r, c.key) || "").trim();
        return v && v !== "-";
      })
    );
    const bits = [`${rows.length} teams`, `category ${viewLabel}`];
    if (!hasRealStats) bits.push("stats “-” until games start");
    if (when) bits.push(when);
    sub.textContent = bits.join(" · ");

    if (head) {
      head.innerHTML = `
        ${sortTh("rank", "#", { num: true })}
        ${sortTh("name", "Team")}
        ${sortTh("wlt", "WLT")}
        ${sortTh("pct", "PCT", { num: true })}
        ${sortTh("waiver", "Waiver", { num: true })}
        ${sortTh("moves", "Moves", { num: true })}
        ${STAT_COLS.map((c) => sortTh(c.key, c.label, { num: true })).join("")}
      `;
    }

    const sorted = sortRows(rows, sortState.key, sortState.dir);
    body.innerHTML = sorted
      .map((r) => {
        const name = (r.name || "").trim() || `Team ${r.team_id || "—"}`;
        const logo = r.logo ? `<img src="${esc(r.logo)}" alt="">` : "";
        const href = r.url || "";
        const nameHtml = href
          ? `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(name)}</a>`
          : esc(name);
        const stats = STAT_COLS.map(
          (c) => `<td class="num">${cell(statValue(r, c.key))}</td>`
        ).join("");
        return `<tr class="${r.is_mine ? "mine" : ""}">
          <td class="num">${cell(r.rank)}</td>
          <td><div class="team-cell">${logo}<span>${nameHtml}</span></div></td>
          <td>${cell(r.wlt)}</td>
          <td class="num">${cell(r.pct)}</td>
          <td class="num">${cell(r.waiver)}</td>
          <td class="num">${cell(r.moves)}</td>
          ${stats}
        </tr>`;
      })
      .join("");

    bindSortHeaders();
  }

  async function loadStandings() {
    const res = await fetch("/api/standings-detail");
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "Failed to load standings.");
    renderStandings(data);
  }

  async function refreshNow() {
    const btn = $("#btnRefreshStandings");
    setBusy(btn, true, "Refresh standings", "Refreshing…");
    showAlert("");
    try {
      const res = await fetch("/api/standings-detail/refresh", { method: "POST" });
      const data = await res.json();
      if (!data.ok) {
        showAlert(data.error || "Could not start refresh.");
        return;
      }
      const jobId = data.job?.id || data.job_id;
      if (jobId) {
        showJobBar(data.job || { id: jobId, label: "Refresh Standings", status: "running" });
        const job = await pollJob(jobId);
        if (job.status === "cancelled") showAlert("Standings refresh cancelled.");
        else showAlert(`Standings updated — ${(job.result && job.result.count) || ""} teams.`);
      }
      await loadStandings();
    } catch (err) {
      showAlert(String(err));
    } finally {
      setBusy(btn, false, "Refresh standings", "Refreshing…");
      window.setTimeout(() => {
        if (!$("#btnRefreshStandings")?.disabled) hideJobBar();
      }, 6000);
    }
  }

  async function resumeActiveJobIfAny() {
    try {
      const data = await (await fetch("/api/jobs/active")).json();
      if (
        data.job &&
        data.job.kind === "standings_detail" &&
        (data.job.status === "running" ||
          data.job.status === "queued" ||
          data.job.status === "paused")
      ) {
        setBusy($("#btnRefreshStandings"), true, "Refresh standings", "Refreshing…");
        showJobBar(data.job);
        await pollJob(data.job.id);
        await loadStandings();
      }
    } catch (_) {
      /* ignore */
    } finally {
      setBusy($("#btnRefreshStandings"), false, "Refresh standings", "Refreshing…");
    }
  }

  $("#btnRefreshStandings")?.addEventListener("click", refreshNow);
  $("#btnReloadStandings")?.addEventListener("click", () => {
    loadStandings().catch((err) => showAlert(String(err)));
  });
  $("#btnViewTotal")?.addEventListener("click", () => {
    statsView = "total";
    if (standingsData) renderStandings(standingsData);
  });
  $("#btnViewAvg")?.addEventListener("click", () => {
    statsView = "avg";
    if (standingsData) renderStandings(standingsData);
  });
  $("#btnPauseResume")?.addEventListener("click", togglePauseResume);
  $("#btnCancelJob")?.addEventListener("click", cancelActiveJob);

  loadStandings()
    .catch((err) => showAlert(String(err)))
    .then(resumeActiveJobIfAny);
})();
