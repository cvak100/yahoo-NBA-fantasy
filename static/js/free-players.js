(() => {
  const $ = (sel) => document.querySelector(sel);
  const alertEl = $("#alert");
  const COLS = 14;
  const LEAGUE_ID = document.body?.dataset?.leagueId || "49605";

  let freeData = null;
  /** @type {{ key: string, dir: 'asc' | 'desc' }} */
  let sortState = { key: "score", dir: "desc" };

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

  function fmtWhen(iso) {
    if (!iso) return "";
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  }

  function scorePartsTip(p) {
    const parts = p.score_parts || {};
    const bits = [
      ["L7 stats", parts.l7_stats],
      ["L14 stats", parts.l14_stats],
      ["Adds", parts.adds],
      ["L7 rank", parts.rank_l7],
    ]
      .filter(([, v]) => v != null)
      .map(([k, v]) => `${k} ${Number(v).toFixed(0)}`);
    return bits.length ? bits.join(" · ") : "";
  }

  function yahooSearchUrl(p) {
    const name = String(p.name || "").trim();
    if (!name) return "";
    return (
      `https://basketball.fantasysports.yahoo.com/nba/${encodeURIComponent(LEAGUE_ID)}` +
      `/playersearch?&search=${encodeURIComponent(name)}`
    );
  }

  function parseNum(v) {
    if (v == null || v === "") return null;
    const n = Number(String(v).replace(/[,%]/g, "").trim());
    return Number.isFinite(n) ? n : null;
  }

  function sortValue(p, key) {
    if (key === "name" || key === "positions") return String(p[key] || "");
    if (key === "l7_pts") return parseNum(p.l7?.pts);
    if (key === "l7_reb") return parseNum(p.l7?.reb);
    if (key === "l7_ast") return parseNum(p.l7?.ast);
    if (key === "l14_pts") return parseNum(p.l14?.pts);
    if (key === "l14_reb") return parseNum(p.l14?.reb);
    if (key === "l14_ast") return parseNum(p.l14?.ast);
    return parseNum(p[key]);
  }

  function sortPlayers(players, key, dir) {
    const mult = dir === "asc" ? 1 : -1;
    return [...players].sort((a, b) => {
      if (key === "name" || key === "positions") {
        return (
          String(sortValue(a, key)).localeCompare(String(sortValue(b, key))) * mult
        );
      }
      const av = sortValue(a, key);
      const bv = sortValue(b, key);
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (av === bv) {
        const as = parseNum(a.score) ?? 0;
        const bs = parseNum(b.score) ?? 0;
        return bs - as;
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
    const table = $("#freeTable");
    if (!table) return;
    table.querySelectorAll(".sortable-th").forEach((th) => {
      const activate = () => {
        const key = th.dataset.sortKey;
        if (!key) return;
        if (sortState.key === key) {
          sortState = { key, dir: sortState.dir === "desc" ? "asc" : "desc" };
        } else {
          const ascDefault =
            key === "name" ||
            key === "positions" ||
            key === "rank_l7" ||
            key === "season_rank" ||
            key === "pool_rank";
          sortState = { key, dir: ascDefault ? "asc" : "desc" };
        }
        if (freeData) renderFreePlayers(freeData);
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

  function renderFreePlayers(data) {
    freeData = data;
    const body = $("#freeBody");
    const head = $("#freeTable thead tr");
    const sub = $("#freeSub");
    const note = $("#freeNote");
    const players = data.players || [];
    const when = fmtWhen(data.scraped_at);
    if (!players.length) {
      sub.textContent = data.note || "No analysis yet — click Analyze.";
      if (note) {
        note.hidden = true;
        note.textContent = "";
      }
      if (head) {
        head.innerHTML = `
          <th class="num">#</th>
          <th>Player</th>
          <th>Pos</th>
          <th class="num score-col">Score</th>
          <th class="num">L7 Rank</th>
          <th class="num">L7 Pts</th>
          <th class="num">L7 Reb</th>
          <th class="num">L7 Ast</th>
          <th class="num">L14 Pts</th>
          <th class="num">L14 Reb</th>
          <th class="num">L14 Ast</th>
          <th class="num">Adds</th>
          <th class="num">Season AR</th>
          <th></th>`;
      }
      body.innerHTML = `<tr><td colspan="${COLS}" class="muted">No Free Players analysis yet. Open Chrome debug, then click Analyze.</td></tr>`;
      return;
    }
    sub.textContent = when
      ? `Top ${players.length} of ${data.pool_count || "—"} available · ${when}`
      : `Top ${players.length} free agents`;
    if (note) {
      note.hidden = !data.note;
      note.textContent = data.note || "";
    }

    if (head) {
      head.innerHTML = `
        ${sortTh("pool_rank", "#", { num: true })}
        ${sortTh("name", "Player")}
        ${sortTh("positions", "Pos")}
        ${sortTh("score", "Score", { num: true })}
        ${sortTh("rank_l7", "L7 Rank", { num: true })}
        ${sortTh("l7_pts", "L7 Pts", { num: true })}
        ${sortTh("l7_reb", "L7 Reb", { num: true })}
        ${sortTh("l7_ast", "L7 Ast", { num: true })}
        ${sortTh("l14_pts", "L14 Pts", { num: true })}
        ${sortTh("l14_reb", "L14 Reb", { num: true })}
        ${sortTh("l14_ast", "L14 Ast", { num: true })}
        ${sortTh("adds", "Adds", { num: true })}
        ${sortTh("season_rank", "Season AR", { num: true })}
        <th></th>`;
    }

    const sorted = sortPlayers(players, sortState.key, sortState.dir);
    body.innerHTML = sorted
      .map((p, idx) => {
        const l7 = p.l7 || {};
        const l14 = p.l14 || {};
        const tip = scorePartsTip(p);
        const meta = [p.nba_team, p.status].filter(Boolean).join(" · ");
        const dbId = Number(p.db_id);
        const clickable = Number.isFinite(dbId) && dbId > 0;
        const yahooHref = yahooSearchUrl(p);
        const yahooLink = yahooHref
          ? `<a class="btn tiny ghost yahoo-search-link" href="${esc(yahooHref)}" target="_blank" rel="noopener noreferrer" title="Open Yahoo player search">Yahoo</a>`
          : "";
        return `
          <tr class="${clickable ? "clickable" : ""}" ${clickable ? `data-db-id="${dbId}"` : ""}>
            <td class="num">${cell(p.pool_rank ?? idx + 1)}</td>
            <td>
              <strong>${esc(p.name)}</strong>
              <div class="subline">${esc(meta)}${clickable ? "" : " · not in local DB"}</div>
            </td>
            <td>${cell(p.positions)}</td>
            <td class="num score-col" title="${esc(tip)}">
              <span class="free-score">${cell(p.score)}</span>
            </td>
            <td class="num">${cell(p.rank_l7)}</td>
            <td class="num">${cell(l7.pts)}</td>
            <td class="num">${cell(l7.reb)}</td>
            <td class="num">${cell(l7.ast)}</td>
            <td class="num">${cell(l14.pts)}</td>
            <td class="num">${cell(l14.reb)}</td>
            <td class="num">${cell(l14.ast)}</td>
            <td class="num">${cell(p.adds)}</td>
            <td class="num">${cell(p.season_rank)}</td>
            <td class="ta-end">${yahooLink}</td>
          </tr>`;
      })
      .join("");

    body.querySelectorAll("tr[data-db-id]").forEach((tr) => {
      tr.addEventListener("click", (e) => {
        if (e.target.closest("a, button")) return;
        const id = Number(tr.dataset.dbId);
        if (Number.isFinite(id) && window.PlayerDetail) window.PlayerDetail.open(id);
      });
    });
    bindSortHeaders();
  }

  async function loadFreePlayers() {
    const res = await fetch("/api/free-players");
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "Failed to load.");
    renderFreePlayers(data);
  }

  async function analyzeNow() {
    const btn = $("#btnAnalyzeFree");
    setBusy(btn, true, "Analyze", "Analyzing…");
    showAlert("");
    try {
      const res = await fetch("/api/free-players/refresh", { method: "POST" });
      const data = await res.json();
      if (!data.ok) {
        showAlert(data.error || "Could not start analysis.");
        return;
      }
      const jobId = data.job?.id || data.job_id;
      if (jobId) {
        showJobBar(data.job || { id: jobId, label: "Free Players analysis", status: "running" });
        const job = await pollJob(jobId);
        if (job.status === "cancelled") {
          showAlert("Analysis cancelled.");
        } else {
          showAlert(
            `Done — top ${(job.result && job.result.count) || 10} free agents scored.`
          );
        }
      }
      await loadFreePlayers();
    } catch (err) {
      showAlert(String(err));
    } finally {
      setBusy(btn, false, "Analyze", "Analyzing…");
      window.setTimeout(() => {
        if (!$("#btnAnalyzeFree")?.disabled) hideJobBar();
      }, 6000);
    }
  }

  async function resumeActiveJobIfAny() {
    try {
      const data = await (await fetch("/api/jobs/active")).json();
      if (
        data.job &&
        data.job.kind === "free_players" &&
        (data.job.status === "running" ||
          data.job.status === "queued" ||
          data.job.status === "paused")
      ) {
        setBusy($("#btnAnalyzeFree"), true, "Analyze", "Analyzing…");
        showJobBar(data.job);
        const job = await pollJob(data.job.id);
        if (job.status === "cancelled") showAlert("Analysis cancelled.");
        await loadFreePlayers();
      }
    } catch (_) {
      /* ignore */
    } finally {
      setBusy($("#btnAnalyzeFree"), false, "Analyze", "Analyzing…");
    }
  }

  $("#btnAnalyzeFree")?.addEventListener("click", analyzeNow);
  $("#btnReloadFree")?.addEventListener("click", () => {
    loadFreePlayers().catch((err) => showAlert(String(err)));
  });
  $("#btnPauseResume")?.addEventListener("click", togglePauseResume);
  $("#btnCancelJob")?.addEventListener("click", cancelActiveJob);

  window.PlayerDetail?.init({
    onAlert: showAlert,
    hideDelete: true,
  });

  loadFreePlayers()
    .catch((err) => showAlert(String(err)))
    .then(resumeActiveJobIfAny);
})();
