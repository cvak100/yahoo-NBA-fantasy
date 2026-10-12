(() => {
  const $ = (sel) => document.querySelector(sel);
  const alertEl = $("#alert");
  const DB_COLS = 18;
  const LEAGUE_ID =
    document.body?.dataset?.leagueId ||
    document.querySelector("[data-league-id]")?.dataset?.leagueId ||
    "";

  function yahooPlayerSearchUrl(name) {
    const q = encodeURIComponent(String(name || "").trim());
    return `https://basketball.fantasysports.yahoo.com/nba/${LEAGUE_ID}/playersearch?search=${q}`;
  }
  const SEARCH_COLS = 16;

  function showAlert(msg) {
    alertEl.hidden = !msg;
    alertEl.textContent = msg || "";
  }

  function setBusy(btn, on, idleLabel, busyLabel) {
    const label = btn.querySelector(".btn-label");
    const spinner = btn.querySelector(".btn-spinner");
    btn.disabled = on;
    if (label) label.textContent = on ? busyLabel : idleLabel;
    if (spinner) spinner.hidden = !on;
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

  function statCells(p) {
    return `
      <td class="num">${cell(p.gp)}</td>
      <td class="num">${cell(p.fg_pct)}</td>
      <td class="num">${cell(p.ft_pct)}</td>
      <td class="num">${cell(p.threes)}</td>
      <td class="num">${cell(p.pts)}</td>
      <td class="num">${cell(p.reb)}</td>
      <td class="num">${cell(p.ast)}</td>
      <td class="num">${cell(p.stl)}</td>
      <td class="num">${cell(p.blk)}</td>
      <td class="num">${cell(p.tov ?? p.to)}</td>`;
  }

  function selectedPlayerIds() {
    return [...document.querySelectorAll("#dbBody input.row-check:checked")].map((el) =>
      Number(el.value)
    );
  }

  function updateSelectionUi() {
    const ids = selectedPlayerIds();
    const n = ids.length;
    const btnRefresh = $("#btnRefreshSelected");
    const btnDelete = $("#btnDeleteSelected");
    if (btnRefresh) btnRefresh.disabled = n === 0;
    if (btnDelete) btnDelete.disabled = n === 0;
    if (btnRefresh) {
      const label = btnRefresh.querySelector(".btn-label");
      if (label && !btnRefresh.disabled) {
        label.textContent = n ? `Refresh selected (${n})` : "Refresh selected";
      } else if (label) {
        label.textContent = "Refresh selected";
      }
    }
    if (btnDelete) {
      const label = btnDelete.querySelector(".btn-label");
      if (label && !btnDelete.disabled) {
        label.textContent = n ? `Delete selected (${n})` : "Delete selected";
      } else if (label) {
        label.textContent = "Delete selected";
      }
    }
    const all = [...document.querySelectorAll("#dbBody input.row-check")];
    all.forEach((c) => {
      c.closest("tr")?.classList.toggle("selected-row", c.checked);
    });
    const master = $("#chkSelectAll");
    if (master) {
      master.checked = all.length > 0 && all.every((c) => c.checked);
      master.indeterminate = all.some((c) => c.checked) && !master.checked;
    }
  }

  let allDbPlayers = [];
  const SHOW_LIMIT_KEY = "yahoonba_db_show_limit";
  const HIDE_TAKEN_KEY = "yahoonba_hide_taken";
  const POS_FILTER_KEY = "yahoonba_db_pos_filters";

  function getShowLimit() {
    const sel = $("#dbShowLimit");
    const raw = sel?.value || localStorage.getItem(SHOW_LIMIT_KEY) || "20";
    if (raw === "all") return null;
    const n = Number(raw);
    return Number.isFinite(n) && n > 0 ? n : 20;
  }

  function parsePositions(positions) {
    if (!positions) return [];
    const out = [];
    for (const part of String(positions).replaceAll("/", ",").split(",")) {
      const pos = part.trim();
      if (pos && !out.includes(pos)) out.push(pos);
    }
    return out;
  }

  function getSelectedPositions() {
    return [...document.querySelectorAll("#posFilters [data-pos].is-on")].map(
      (el) => el.dataset.pos
    );
  }

  function multiPosOnly() {
    return !!$("#posFilterMulti")?.classList.contains("is-on");
  }

  function hasActivePosFilter() {
    return getSelectedPositions().length > 0 || multiPosOnly();
  }

  function playerMatchesPositions(p) {
    const positions = parsePositions(p.positions);
    if (multiPosOnly() && positions.length < 2) return false;
    const selected = getSelectedPositions();
    if (!selected.length) return true;
    return selected.some((pos) => positions.includes(pos));
  }

  function persistPosFilters() {
    try {
      localStorage.setItem(
        POS_FILTER_KEY,
        JSON.stringify({
          positions: getSelectedPositions(),
          multi: multiPosOnly(),
        })
      );
    } catch (_) {
      /* ignore */
    }
  }

  function restorePosFilters() {
    const root = $("#posFilters");
    if (!root) return;
    let saved = null;
    try {
      saved = JSON.parse(localStorage.getItem(POS_FILTER_KEY) || "null");
    } catch (_) {
      saved = null;
    }
    if (!saved || typeof saved !== "object") return;
    const selected = new Set(
      Array.isArray(saved.positions) ? saved.positions.map(String) : []
    );
    root.querySelectorAll("[data-pos]").forEach((btn) => {
      const on = selected.has(btn.dataset.pos);
      btn.classList.toggle("is-on", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
    });
    const multi = $("#posFilterMulti");
    if (multi) {
      const on = !!saved.multi;
      multi.classList.toggle("is-on", on);
      multi.setAttribute("aria-pressed", on ? "true" : "false");
    }
  }

  function setPosFilterPressed(btn, on) {
    btn.classList.toggle("is-on", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  }

  function normalizeSearch(s) {
    return String(s ?? "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .replace(/[^a-z0-9\s'-]/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function getPlayerSearchQuery() {
    return normalizeSearch($("#dbPlayerSearch")?.value || "");
  }

  function playerMatchesQuery(p, q) {
    if (!q) return true;
    const hay = normalizeSearch(
      [
        p.full_name,
        p.first_name,
        p.last_name,
        p.nba_team,
        p.positions,
        p.yahoo_rank,
        p.fantasy_team,
        p.fantasy_is_mine ? "you mine" : "",
      ]
        .filter((x) => x != null && x !== "")
        .join(" ")
    );
    const tokens = q.split(" ").filter(Boolean);
    return tokens.every((t) => hay.includes(t));
  }

  function hideTakenEnabled() {
    return Boolean($("#btnHideTaken")?.classList.contains("is-on"));
  }

  function setHideTaken(on) {
    const btn = $("#btnHideTaken");
    if (!btn) return;
    btn.classList.toggle("is-on", !!on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  }

  function playerIsTaken(p) {
    if (p.fantasy_is_mine) return true;
    if (p.fantasy_is_taken) return true;
    const owner = String(p.fantasy_team || "").trim();
    if (!owner) return false;
    const u = owner.toUpperCase();
    return !(u === "FA" || u === "W" || u === "WA" || u.startsWith("W (") || u === "WAIVERS");
  }

  function ownerLabel(p) {
    if (p.fantasy_is_mine) return "You";
    const owner = String(p.fantasy_team || "").trim();
    return owner || "—";
  }

  function filteredDbPlayers() {
    const q = getPlayerSearchQuery();
    const hideTaken = hideTakenEnabled();
    return allDbPlayers.filter(
      (p) =>
        playerMatchesQuery(p, q) &&
        playerMatchesPositions(p) &&
        (!hideTaken || !playerIsTaken(p))
    );
  }

  function visibleDbPlayers() {
    const filtered = filteredDbPlayers();
    const limit = getShowLimit();
    if (limit == null) return filtered;
    return filtered.slice(0, limit);
  }

  function renderDbPlayers() {
    const players = visibleDbPlayers();
    const total = allDbPlayers.length;
    const filtered = filteredDbPlayers();
    const q = getPlayerSearchQuery();
    const posOn = hasActivePosFilter();
    const body = $("#dbBody");
    const sub = $("#dbSub");
    const limit = getShowLimit();
    if (!total) {
      sub.textContent = "0 players saved locally";
    } else if (q || posOn || hideTakenEnabled()) {
      const bits = [];
      if (q) bits.push(`“${$("#dbPlayerSearch")?.value.trim() || q}”`);
      const selected = getSelectedPositions();
      if (selected.length) bits.push(selected.join("/"));
      if (multiPosOnly()) bits.push("multi-pos");
      if (hideTakenEnabled()) bits.push("hide taken");
      const shown =
        players.length < filtered.length
          ? `Showing ${players.length} of ${filtered.length} matches`
          : `${filtered.length} match${filtered.length === 1 ? "" : "es"}`;
      sub.textContent = `${shown} · ${bits.join(" · ")} · ${total} in DB`;
    } else if (limit != null && players.length < total) {
      sub.textContent = `Showing ${players.length} of ${total} players`;
    } else {
      sub.textContent = `${total} player${total === 1 ? "" : "s"} saved locally`;
    }

    if (!total) {
      body.innerHTML = `<tr><td colspan="${DB_COLS}" class="muted">Database is empty. Search Yahoo and add players.</td></tr>`;
      const master = $("#chkSelectAll");
      if (master) {
        master.checked = false;
        master.indeterminate = false;
      }
      updateSelectionUi();
      return;
    }

    if (!players.length) {
      body.innerHTML = `<tr><td colspan="${DB_COLS}" class="muted">No players match these filters.</td></tr>`;
      updateSelectionUi();
      return;
    }

    body.innerHTML = players
      .map((p) => {
        const name = esc(p.full_name);
        const ownerClass = p.fantasy_is_mine
          ? "owner-mine"
          : playerIsTaken(p)
            ? "owner-taken"
            : "owner-free";
        const rowClass = p.fantasy_is_mine
          ? "is-mine"
          : playerIsTaken(p)
            ? "is-taken"
            : "is-free";
        const yahooHref = yahooPlayerSearchUrl(p.full_name);
        return `
          <tr class="clickable ${rowClass}" data-db-id="${p.id}">
            <td class="col-check">
              <input type="checkbox" class="row-check" value="${p.id}" aria-label="Select ${name}">
            </td>
            <td class="num">${p.yahoo_rank ?? "—"}</td>
            <td>
              <strong>${name}</strong>
            </td>
            <td>${esc(p.positions) || "—"}</td>
            <td>${esc(p.nba_team) || "—"}</td>
            <td class="${ownerClass}"><span class="owner-pill">${esc(ownerLabel(p))}</span></td>
            <td class="num">${cell(p.owned_pct)}</td>
            ${statCells(p)}
            <td class="ta-end">
              <div class="row-actions">
                <button type="button" class="btn tiny ghost fav-btn ${p.is_favourite ? "is-on" : ""}" data-fav="${p.id}" aria-pressed="${p.is_favourite ? "true" : "false"}" title="${p.is_favourite ? "Remove from watchlist" : "Add to watchlist"}">${p.is_favourite ? "★" : "☆"}</button>
                <button type="button" class="btn tiny ghost row-icon-btn" data-refresh="${p.id}" title="Refresh player data" aria-label="Refresh player data">
                  <svg class="icon-refresh" viewBox="0 0 24 24" width="13" height="13" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"></path>
                    <path d="M3 3v5h5"></path>
                    <path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"></path>
                    <path d="M16 16h5v5"></path>
                  </svg>
                </button>
                <a class="btn tiny ghost yahoo-row-btn" href="${esc(yahooHref)}" target="_blank" rel="noopener noreferrer" title="Open on Yahoo" aria-label="Open on Yahoo"><img class="icon-yahoo" src="https://s.yimg.com/cv/apiv2/sports/logo/app-icon-60x60.jpg" alt="" width="13" height="13" decoding="async"></a>
              </div>
            </td>
          </tr>`;
      })
      .join("");

    body.querySelectorAll("tr[data-db-id]").forEach((tr) => {
      tr.addEventListener("click", (e) => {
        if (e.target.closest("input, button, a, .row-actions, .col-check")) return;
        openDetail(Number(tr.dataset.dbId));
      });
    });
    body.querySelectorAll(".row-check").forEach((chk) => {
      chk.addEventListener("click", (e) => e.stopPropagation());
      chk.addEventListener("change", updateSelectionUi);
    });
    body.querySelectorAll("[data-fav]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        toggleFavourite(Number(btn.dataset.fav), btn);
      });
    });
    body.querySelectorAll("[data-refresh]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        refreshPlayer(Number(btn.dataset.refresh), btn);
      });
    });
    updateSelectionUi();
  }

  function setFavButton(btn, on) {
    if (!btn) return;
    btn.classList.toggle("is-on", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    const isDetail = btn.id === "btnFavPlayer";
    if (isDetail) {
      btn.textContent = on ? "★ Watchlist" : "☆ Watchlist";
      btn.title = on ? "Remove from watchlist" : "Add to watchlist";
    } else {
      btn.textContent = on ? "★" : "☆";
      btn.title = on ? "Remove from watchlist" : "Add to watchlist";
    }
  }

  async function toggleFavourite(playerId, btn) {
    const on = btn?.classList.contains("is-on") || btn?.getAttribute("aria-pressed") === "true";
    const method = on ? "DELETE" : "POST";
    if (btn) btn.disabled = true;
    try {
      const res = await fetch(`/api/db/players/${playerId}/favourite`, { method });
      const data = await res.json();
      if (!data.ok) {
        showAlert(data.error || "Could not update watchlist.");
        return;
      }
      const next = !!data.is_favourite;
      const row = allDbPlayers.find((p) => p.id === playerId);
      if (row) row.is_favourite = next ? 1 : 0;
      setFavButton(btn, next);
      document.querySelectorAll(`[data-fav="${playerId}"]`).forEach((el) => setFavButton(el, next));
      if (window.PlayerDetail?.getCurrentId?.() === playerId) {
        window.PlayerDetail.setFavouriteButton(next);
      }
    } catch (err) {
      showAlert(String(err));
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function currentDetailId() {
    return window.PlayerDetail?.getCurrentId?.() ?? null;
  }

  function closeDetail() {
    window.PlayerDetail?.close?.();
  }

  function openDetail(playerId) {
    return window.PlayerDetail?.open?.(playerId);
  }

  function networkErrorMessage(err) {
    const msg = String(err || "");
    if (msg.includes("Failed to fetch") || err?.name === "TypeError") {
      return (
        "Connection lost while talking to the server. Keep Chrome debug open, " +
        "leave this tab open, and try again (long scrapes can take 1–2 min)."
      );
    }
    return msg;
  }

  async function readJson(res) {
    const text = await res.text();
    try {
      return JSON.parse(text);
    } catch {
      const snippet = text.trim().slice(0, 80).replace(/\s+/g, " ");
      throw new Error(
        `Server returned non-JSON (HTTP ${res.status}). ` +
          `Restart the Flask app (python main.py), then try again. ` +
          `(${snippet || "empty body"})`
      );
    }
  }

  const SCRAPE_BTNS = [
    "#btnAutoAdd",
    "#btnAutoAddConfirm",
    "#btnRefreshAll",
    "#btnRefreshSelected",
    "#btnSearch",
  ];

  let scrapeJobRunning = false;

  function setScrapeUiBusy(on) {
    scrapeJobRunning = on;
    SCRAPE_BTNS.forEach((sel) => {
      const el = $(sel);
      if (el) el.disabled = on;
    });
    if (!on) updateSelectionUi();
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  let activeJobId = null;
  let hideControlsJobId = null;
  let jobTimerInterval = null;
  let jobTimerStartedAt = 0;
  let jobTimerPausedAccum = 0;
  let jobTimerPauseStarted = null;

  function formatElapsed(sec) {
    const s = Math.max(0, Math.floor(Number(sec) || 0));
    const m = Math.floor(s / 60);
    const r = s % 60;
    return `${m}:${String(r).padStart(2, "0")}`;
  }

  function updateJobTimerDisplay(sec) {
    const el = $("#jobBarTimer");
    if (el) el.textContent = formatElapsed(sec);
  }

  function stopJobTimer() {
    if (jobTimerInterval) {
      clearInterval(jobTimerInterval);
      jobTimerInterval = null;
    }
    jobTimerPauseStarted = null;
  }

  function startJobTimer(startedAt, elapsedSec) {
    stopJobTimer();
    jobTimerStartedAt =
      typeof startedAt === "number" && Number.isFinite(startedAt)
        ? startedAt
        : Date.now() / 1000;
    if (elapsedSec != null && Number.isFinite(Number(elapsedSec))) {
      jobTimerPausedAccum = Date.now() / 1000 - jobTimerStartedAt - Number(elapsedSec);
    } else {
      jobTimerPausedAccum = 0;
    }
    const tick = () => {
      const now = Date.now() / 1000;
      let paused = jobTimerPausedAccum;
      if (jobTimerPauseStarted != null) paused += now - jobTimerPauseStarted;
      updateJobTimerDisplay(now - jobTimerStartedAt - paused);
    };
    tick();
    jobTimerInterval = setInterval(tick, 250);
  }

  function formatStepTime(sec) {
    if (sec == null || !Number.isFinite(Number(sec))) return "";
    const s = Math.max(0, Math.round(Number(sec)));
    const m = Math.floor(s / 60);
    const r = s % 60;
    return `${m}:${String(r).padStart(2, "0")}`;
  }

  function renderJobSteps(steps) {
    const root = $("#jobSteps");
    if (!root) return;
    if (!steps?.length) {
      root.hidden = true;
      root.innerHTML = "";
      return;
    }
    root.hidden = false;
    root.innerHTML = steps
      .map((s) => {
        const status = s.status || "pending";
        const mark =
          status === "done"
            ? "✓"
            : status === "running"
              ? "●"
              : status === "error"
                ? "!"
                : status === "cancelled"
                  ? "✕"
                  : status === "skipped"
                    ? "–"
                    : "○";
        const detail = s.detail
          ? `<div class="job-step-detail">${esc(s.detail)}</div>`
          : "";
        let timeLabel = "";
        if (status === "done" && s.elapsed_sec != null) {
          timeLabel = formatStepTime(s.elapsed_sec);
        } else if (status === "running" && s.elapsed_sec != null) {
          timeLabel = formatStepTime(s.elapsed_sec);
        }
        const timeHtml = timeLabel
          ? `<span class="job-step-time">${esc(timeLabel)}</span>`
          : "";
        return `<li class="job-step is-${status}">
          <span class="job-step-mark" aria-hidden="true">${mark}</span>
          <span class="job-step-body">
            <span class="job-step-main">
              <span>${esc(s.label || s.key)}</span>
              ${timeHtml}
            </span>
            ${detail}
          </span>
        </li>`;
      })
      .join("");
  }

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
      const data = await readJson(res);
      if (!data.ok) {
        showAlert(data.error || `Could not ${action}.`);
        return;
      }
      if (data.job) updateJobBar(data.job);
      if (msg) showAlert(msg);
    } catch (err) {
      showAlert(networkErrorMessage(err));
    }
  }

  async function cancelActiveJob() {
    if (!activeJobId) return;
    hideControlsJobId = activeJobId;
    setJobControls({ pauseResume: null, showStop: false, stopping: false });
    await postJobAction("cancel", "Job cancelled.");
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
    updateJobBar(job);
  }

  function updateJobBar(job) {
    if (!job) return;
    if (job.id) activeJobId = job.id;
    if (job.id && job.id !== hideControlsJobId) hideControlsJobId = null;
    const label = $("#jobBarLabel");
    const meta = $("#jobBarMeta");
    const text = $("#jobBarText");
    const fill = $("#jobBarFill");
    if (label) label.textContent = job.label || "Yahoo job";
    const total = Number(job.total || 0);
    const progress = Number(job.progress || 0);
    const steps = job.steps || [];
    if (meta) {
      meta.textContent =
        total > 0
          ? `${progress}/${total}`
          : steps.length
            ? `${Math.min(progress + 1, steps.length)}/${steps.length}`
            : job.status || "running";
    }
    if (text) text.textContent = job.message || "Working…";
    if (fill) {
      const pct = total
        ? Math.min(100, Math.round((progress / total) * 100))
        : steps.length
          ? Math.min(
              100,
              Math.round(
                ((progress + (job.status === "done" ? 0 : 0.35)) / steps.length) * 100
              )
            )
          : 8;
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
    const paused = !!job.paused || job.status === "paused";
    $("#jobBar")?.classList.toggle("is-paused", paused);
    if (active) {
      if (!jobTimerInterval) startJobTimer(job.started_at, job.elapsed_sec);
      else if (job.elapsed_sec != null) updateJobTimerDisplay(job.elapsed_sec);
      if (paused) {
        if (jobTimerPauseStarted == null) jobTimerPauseStarted = Date.now() / 1000;
      } else if (jobTimerPauseStarted != null) {
        jobTimerPausedAccum += Date.now() / 1000 - jobTimerPauseStarted;
        jobTimerPauseStarted = null;
      }
      if (paused) {
        setJobControls({ pauseResume: "resume", showStop: true, stopping: false });
      } else {
        setJobControls({ pauseResume: "pause", showStop: true, stopping: false });
      }
    } else {
      if (job.elapsed_sec != null) {
        stopJobTimer();
        updateJobTimerDisplay(job.elapsed_sec);
      }
      setJobControls({ pauseResume: null, showStop: false, stopping: false });
    }
    renderJobSteps(steps);
  }

  function hideJobBarSoon() {
    const bar = $("#jobBar");
    if (!bar) return;
    setJobControls({ pauseResume: null, showStop: false, stopping: false });
    bar.classList.remove("is-paused");
    setTimeout(() => {
      if (!scrapeJobRunning) {
        stopJobTimer();
        bar.hidden = true;
        activeJobId = null;
        const steps = $("#jobSteps");
        if (steps) {
          steps.hidden = true;
          steps.innerHTML = "";
        }
      }
    }, 5000);
  }

  function setBtnBusy(btn, on, idleLabel, busyLabel) {
    if (!btn) return;
    if (btn.querySelector && btn.querySelector(".btn-label")) {
      setBusy(btn, on, idleLabel, busyLabel);
      return;
    }
    // Icon-only row buttons — don't wipe the SVG.
    if (btn.querySelector && btn.querySelector(".icon-refresh")) {
      btn.disabled = on;
      btn.classList.toggle("is-busy", on);
      return;
    }
    btn.disabled = on;
    btn.textContent = on ? busyLabel : idleLabel;
  }

  async function watchJob(jobId, { summarize } = {}) {
    activeJobId = jobId;
    setScrapeUiBusy(true);
    while (true) {
      await sleep(1500);
      const poll = await readJson(await fetch(`/api/jobs/${jobId}`));
      if (!poll.ok || !poll.job) {
        showAlert(poll.error || "Lost job status.");
        break;
      }
      updateJobBar(poll.job);
      if (
        poll.job.status === "running" ||
        poll.job.status === "queued" ||
        poll.job.status === "paused"
      ) {
        continue;
      }
      if (poll.job.status === "error") {
        showAlert(poll.job.error || "Job failed.");
        break;
      }
      if (poll.job.status === "cancelled") {
        showAlert("Job cancelled.");
        await loadDbPlayers();
        hideJobBarSoon();
        break;
      }
      const result = poll.job.result || {};
      showAlert(summarize ? summarize(result, poll.job) : `${poll.job.label || "Job"} finished.`);
      await loadDbPlayers();
      hideJobBarSoon();
      break;
    }
    setScrapeUiBusy(false);
  }

  async function runBackgroundJob({
    url,
    body,
    btn,
    idleLabel,
    busyLabel,
    summarize,
  }) {
    if (scrapeJobRunning) {
      showAlert("A Yahoo job is already running — wait for it to finish.");
      return;
    }
    setScrapeUiBusy(true);
    setBtnBusy(btn, true, idleLabel, busyLabel);
    showAlert("");
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });
      const data = await readJson(res);
      if (!data.ok) {
        showAlert(data.error || "Could not start job.");
        setScrapeUiBusy(false);
        return;
      }
      showJobBar(data.job || { label: idleLabel, status: "running", progress: 0, total: 0 });
      // release local busy flag inside watchJob; keep btn busy until watch ends
      scrapeJobRunning = false;
      await watchJob(data.job_id, { summarize });
    } catch (err) {
      showAlert(networkErrorMessage(err));
      setScrapeUiBusy(false);
    } finally {
      setBtnBusy(btn, false, idleLabel, busyLabel);
    }
  }

  async function resumeActiveJobIfAny() {
    try {
      const data = await readJson(await fetch("/api/jobs/active"));
      if (
        data.job &&
        (data.job.status === "running" ||
          data.job.status === "queued" ||
          data.job.status === "paused")
      ) {
        showJobBar(data.job);
        showAlert(
          data.job.status === "paused"
            ? `${data.job.label} is paused — Resume or Stop.`
            : `${data.job.label} is still running — you can browse the page.`
        );
        await watchJob(data.job.id);
      }
    } catch (_) {
      /* ignore */
    }
  }

  async function refreshPlayer(playerId, btn) {
    await runBackgroundJob({
      url: `/api/db/players/${playerId}/refresh`,
      btn,
      idleLabel: "Refresh",
      busyLabel: "…",
      summarize: (result) => {
        if (currentDetailId() === playerId) openDetail(playerId);
        return `Refreshed ${result.full_name || "player"}.`;
      },
    });
  }

  async function bulkAddPlayers(limit, btn) {
    const all = limit === "all" || limit === "*" || limit == null;
    const idle = btn.querySelector(".btn-label")?.textContent || "Add Batch";
    await runBackgroundJob({
      url: "/api/bulk-add-players",
      body: all ? { limit: "all", all: true } : { limit },
      btn,
      idleLabel: idle,
      busyLabel: all ? "Adding all…" : `Adding ${limit}…`,
      summarize: (result) => {
        const n = result.added_count ?? 0;
        const pool = result.pool_size != null ? ` Yahoo list ${result.pool_size}.` : "";
        return n
          ? `Added ${n} new player${n === 1 ? "" : "s"} with full details (skipped ${result.skipped_existing ?? 0} already in DB).${pool}`
          : `No new players to add — candidates already in the DB.${pool}`;
      },
    });
  }

  function summarizePlayerRefresh(result, kindLabel) {
    const fail = result.failed_count ?? 0;
    const own = result.ownership || {};
    const ownBit =
      own.owners != null
        ? ` · ${own.owners} owners`
        : own.owners_updated != null
          ? ` · ${own.owners_updated} owners`
          : "";
    const timeBit =
      result.elapsed_sec != null ? ` · ${formatElapsed(result.elapsed_sec)}` : "";
    return (
      `Refreshed ${result.refreshed ?? 0}/${result.total ?? 0} ${kindLabel}` +
      (fail ? ` · ${fail} failed` : "") +
      ownBit +
      timeBit +
      "."
    );
  }

  async function refreshAllPlayers(btn) {
    await runBackgroundJob({
      url: "/api/refresh-all-players",
      btn,
      idleLabel: "Refresh all",
      busyLabel: "Refreshing…",
      summarize: (result) => summarizePlayerRefresh(result, "players"),
    });
  }

  async function refreshSelectedPlayers(btn) {
    const ids = selectedPlayerIds();
    if (!ids.length) {
      showAlert("Select at least one player first.");
      return;
    }
    const idle = btn.querySelector(".btn-label")?.textContent || "Refresh selected";
    await runBackgroundJob({
      url: "/api/db/players/refresh-selected",
      body: { ids },
      btn,
      idleLabel: idle,
      busyLabel: `Refreshing ${ids.length}…`,
      summarize: (result) => summarizePlayerRefresh(result, "selected"),
    });
  }

  async function deleteSelectedPlayers(btn) {
    const ids = selectedPlayerIds();
    if (!ids.length) {
      showAlert("Select at least one player first.");
      return;
    }
    if (
      !window.confirm(
        `Delete ${ids.length} selected player${ids.length === 1 ? "" : "s"} from the local database?`
      )
    ) {
      return;
    }
    const idle = btn.querySelector(".btn-label")?.textContent || "Delete selected";
    setBusy(btn, true, idle, "Deleting…");
    showAlert("");
    try {
      const res = await fetch("/api/db/players/delete-selected", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids }),
      });
      const data = await readJson(res);
      if (!data.ok) {
        showAlert(data.error || "Delete selected failed.");
        return;
      }
      if (currentDetailId() != null && ids.includes(currentDetailId())) closeDetail();
      showAlert(`Deleted ${data.deleted_count ?? 0} player${(data.deleted_count ?? 0) === 1 ? "" : "s"}.`);
      await loadDbPlayers();
    } catch (err) {
      showAlert(networkErrorMessage(err));
    } finally {
      setBusy(btn, false, "Delete selected", "Deleting…");
      updateSelectionUi();
    }
  }

  async function deletePlayer(playerId, { skipConfirm = false } = {}) {
    if (!skipConfirm && !window.confirm("Delete this player from the local database?")) {
      return;
    }
    showAlert("");
    try {
      const res = await fetch(`/api/db/players/${playerId}`, { method: "DELETE" });
      const data = await readJson(res);
      if (!data.ok) {
        showAlert(data.error || "Could not delete player.");
        return;
      }
      if (currentDetailId() === playerId) closeDetail();
      await loadDbPlayers();
    } catch (err) {
      showAlert(networkErrorMessage(err));
    }
  }

  function renderSearchRows(players) {
    const body = $("#searchBody");
    if (!players.length) {
      body.innerHTML = `<tr><td colspan="${SEARCH_COLS}" class="muted">No players found.</td></tr>`;
      return;
    }

    body.innerHTML = players
      .map((p) => {
        const name = esc(p.name);
        const team = p.nba_team ? esc(p.nba_team) : "";
        const payload = encodeURIComponent(
          JSON.stringify({
            player_id: p.player_id,
            name: p.name,
            positions: p.positions,
            nba_team: p.nba_team,
            profile_url: p.profile_url,
            rank: p.rank,
            owned_pct: p.owned_pct,
            gp: p.gp,
            fg_pct: p.fg_pct,
            ft_pct: p.ft_pct,
            threes: p.threes,
            pts: p.pts,
            reb: p.reb,
            ast: p.ast,
            stl: p.stl,
            blk: p.blk,
            to: p.to,
          })
        );
        const action = p.in_db
          ? `<button type="button" class="btn tiny ghost" data-view="${p.db_id}">In DB</button>`
          : `<button type="button" class="btn tiny primary" data-add="${payload}">Add</button>`;
        return `
          <tr class="${p.in_db ? "in-db" : ""}">
            <td>
              <strong>${name}</strong>
              <div class="subline">${team}</div>
            </td>
            <td>${esc(p.positions) || "—"}</td>
            <td><span class="status ${p.available ? "fa" : ""}">${esc(p.status) || "—"}</span></td>
            <td class="num">${cell(p.rank)}</td>
            <td class="num">${cell(p.owned_pct)}</td>
            ${statCells(p)}
            <td class="ta-end">${action}</td>
          </tr>`;
      })
      .join("");

    body.querySelectorAll("[data-view]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        openDetail(Number(btn.dataset.view));
      });
    });

    body.querySelectorAll("[data-add]").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const raw = JSON.parse(decodeURIComponent(btn.getAttribute("data-add")));
        await runBackgroundJob({
          url: "/api/db/players/add",
          body: { ...raw, scrape_details: true },
          btn,
          idleLabel: "Add",
          busyLabel: "Adding…",
          summarize: (result) => {
            const pid = result.player_id;
            if (pid) {
              btn.textContent = "In DB";
              btn.className = "btn tiny ghost";
              btn.removeAttribute("data-add");
              btn.dataset.view = String(pid);
              btn.addEventListener("click", (ev) => {
                ev.stopPropagation();
                openDetail(Number(btn.dataset.view));
              });
              openDetail(pid);
            }
            return result.already_existed
              ? `${result.full_name || "Player"} is already in the DB.`
              : `Added ${result.full_name || "player"}.`;
          },
        });
      });
    });
  }

  async function loadDbPlayers() {
    try {
      const res = await fetch("/api/db/players");
      const data = await res.json();
      if (!data.ok) {
        showAlert(data.error || "Could not load database players.");
        return;
      }
      allDbPlayers = data.players || [];
      renderDbPlayers();
    } catch (err) {
      showAlert(String(err));
    }
  }

  async function runSearch(q) {
    const btn = $("#btnSearch");
    setBusy(btn, true, "Search Yahoo", "Searching…");
    showAlert("");
    try {
      const res = await fetch(`/api/players/search?q=${encodeURIComponent(q)}`);
      const data = await res.json();
      if (!data.ok) {
        showAlert(data.error || "Search failed.");
        return;
      }
      renderSearchRows(data.players || []);
    } catch (err) {
      showAlert(String(err));
    } finally {
      setBusy(btn, false, "Search Yahoo", "Searching…");
    }
  }

  $("#searchForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const q = $("#searchInput").value.trim();
    if (q.length >= 2) runSearch(q);
  });

  const dbShowLimit = $("#dbShowLimit");
  if (dbShowLimit) {
    // Always open on 20 (ignore stale localStorage like "all").
    dbShowLimit.value = "20";
    localStorage.setItem(SHOW_LIMIT_KEY, "20");
    dbShowLimit.addEventListener("change", () => {
      localStorage.setItem(SHOW_LIMIT_KEY, dbShowLimit.value);
      renderDbPlayers();
    });
  }
  const btnHideTaken = $("#btnHideTaken");
  if (btnHideTaken) {
    setHideTaken(localStorage.getItem(HIDE_TAKEN_KEY) === "1");
    btnHideTaken.addEventListener("click", () => {
      const on = !hideTakenEnabled();
      setHideTaken(on);
      localStorage.setItem(HIDE_TAKEN_KEY, on ? "1" : "0");
      renderDbPlayers();
    });
  }
  const dbPlayerSearch = $("#dbPlayerSearch");
  if (dbPlayerSearch) {
    let searchTimer = null;
    dbPlayerSearch.addEventListener("input", () => {
      window.clearTimeout(searchTimer);
      searchTimer = window.setTimeout(renderDbPlayers, 120);
    });
    dbPlayerSearch.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        dbPlayerSearch.value = "";
        renderDbPlayers();
      }
    });
  }
  restorePosFilters();
  $("#posFilters")?.addEventListener("click", (e) => {
    const btn = e.target.closest(".pos-filter");
    if (!btn || !e.currentTarget.contains(btn)) return;
    setPosFilterPressed(btn, !btn.classList.contains("is-on"));
    persistPosFilters();
    renderDbPlayers();
  });
  $("#btnAddPlayerJump")?.addEventListener("click", () => {
    const section = $("#findAddSection");
    section?.scrollIntoView({ behavior: "smooth", block: "start" });
    const input = $("#searchInput");
    if (input) {
      window.setTimeout(() => input.focus(), 350);
    }
  });
  function openAutoAddModal() {
    const modal = $("#autoAddModal");
    if (!modal || scrapeJobRunning) return;
    modal.hidden = false;
    document.body.classList.add("modal-open");
  }

  function closeAutoAddModal() {
    const modal = $("#autoAddModal");
    if (modal) modal.hidden = true;
    if ($("#detailModal")?.hidden && $("#gameLogModal")?.hidden) {
      document.body.classList.remove("modal-open");
    }
  }

  $("#btnAutoAdd")?.addEventListener("click", openAutoAddModal);
  document.querySelectorAll("[data-close-autoadd]").forEach((el) => {
    el.addEventListener("click", closeAutoAddModal);
  });
  $("#btnAutoAddConfirm")?.addEventListener("click", async () => {
    const picked = document.querySelector('input[name="autoAddLimit"]:checked');
    const raw = (picked?.value || "10").trim().toLowerCase();
    const limit = raw === "all" ? "all" : Number(raw);
    closeAutoAddModal();
    await bulkAddPlayers(
      limit === "all" || Number.isFinite(limit) ? limit : 10,
      $("#btnAutoAdd")
    );
  });

  const btnRefreshAll = $("#btnRefreshAll");
  if (btnRefreshAll) {
    btnRefreshAll.addEventListener("click", () => refreshAllPlayers(btnRefreshAll));
  }
  const btnRefreshSelected = $("#btnRefreshSelected");
  if (btnRefreshSelected) {
    btnRefreshSelected.addEventListener("click", () =>
      refreshSelectedPlayers(btnRefreshSelected)
    );
  }
  const btnDeleteSelected = $("#btnDeleteSelected");
  if (btnDeleteSelected) {
    btnDeleteSelected.addEventListener("click", () =>
      deleteSelectedPlayers(btnDeleteSelected)
    );
  }
  const chkSelectAll = $("#chkSelectAll");
  if (chkSelectAll) {
    chkSelectAll.addEventListener("change", () => {
      document.querySelectorAll("#dbBody input.row-check").forEach((c) => {
        c.checked = chkSelectAll.checked;
      });
      updateSelectionUi();
    });
  }
  window.PlayerDetail?.init({
    onAlert: showAlert,
    onDelete: (playerId) => deletePlayer(playerId),
    onFavouriteChange: (playerId, isFav) => {
      const row = allDbPlayers.find((p) => p.id === playerId);
      if (row) row.is_favourite = isFav ? 1 : 0;
      document.querySelectorAll(`[data-fav="${playerId}"]`).forEach((el) => setFavButton(el, isFav));
    },
  });

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if ($("#autoAddModal") && !$("#autoAddModal").hidden) closeAutoAddModal();
  });

  loadDbPlayers().then(() => {
    const params = new URLSearchParams(window.location.search);
    const openId = Number(params.get("player") || "");
    if (Number.isFinite(openId) && openId > 0) openDetail(openId);
    const q = params.get("q");
    if (q && dbPlayerSearch) {
      dbPlayerSearch.value = q;
      renderDbPlayers();
    }
  });
  $("#btnPauseResume")?.addEventListener("click", togglePauseResume);
  $("#btnCancelJob")?.addEventListener("click", cancelActiveJob);
  resumeActiveJobIfAny();
})();
