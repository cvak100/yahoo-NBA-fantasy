(() => {
  const $ = (sel) => document.querySelector(sel);

  const alertEl = $("#alert");
  const btnSync = $("#btnSync");
  const btnLabel = btnSync.querySelector(".btn-label");
  const btnSpinner = btnSync.querySelector(".btn-spinner");

  function showAlert(msg) {
    alertEl.hidden = !msg;
    alertEl.textContent = msg || "";
  }

  function setSyncing(on) {
    btnSync.disabled = on;
    btnLabel.textContent = on ? "Refreshing…" : "Refresh";
    btnSpinner.hidden = !on;
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  let jobTimerInterval = null;
  let jobTimerStartedAt = null;
  let jobTimerPausedAccum = 0;
  let jobTimerPauseStarted = null;
  let activeJobId = null;
  let hideControlsJobId = null;

  function setJobControls({ pauseResume, showStop, stopping }) {
    // pauseResume: "pause" | "resume" | null (hidden)
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
    const btn = $("#btnPauseResume");
    const mode = btn?.dataset?.mode || "pause";
    if (mode === "resume") await resumeActiveJob();
    else await pauseActiveJob();
  }

  async function postJobAction(action, busyMsg) {
    if (!activeJobId) return;
    try {
      const res = await fetch(`/api/jobs/${activeJobId}/${action}`, { method: "POST" });
      const data = await res.json();
      if (!data.ok) {
        showAlert(data.error || `Could not ${action}.`);
        return;
      }
      if (data.job) showJobBar(data.job);
      if (busyMsg) showAlert(busyMsg);
    } catch (err) {
      showAlert(String(err));
    }
  }

  async function cancelActiveJob() {
    if (!activeJobId) return;
    // Cancel is immediate — hide Pause/Stop right away (don't leave "Stopping…").
    hideControlsJobId = activeJobId;
    setJobControls({ pauseResume: null, showStop: false, stopping: false });
    await postJobAction("cancel", "Refresh cancelled.");
  }

  async function pauseActiveJob() {
    await postJobAction("pause", "Paused — click Resume to continue.");
  }

  async function resumeActiveJob() {
    await postJobAction("resume", "Resuming refresh…");
  }

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
    // Prefer server elapsed so pause time is excluded.
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

  function showJobBar(job) {
    const bar = $("#jobBar");
    if (!bar) return;
    bar.hidden = false;
    if (job.id) activeJobId = job.id;
    if (job.id && job.id !== hideControlsJobId) hideControlsJobId = null;
    const paused = !!job.paused || job.status === "paused";
    bar.classList.toggle("is-paused", paused);
    $("#jobBarLabel").textContent = job.label || "Main refresh";
    const total = job.total || 0;
    const prog = job.progress || 0;
    const steps = job.steps || [];
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
    $("#jobBarMeta").textContent = steps.length
      ? `${Math.min(prog + 1, steps.length)}/${steps.length}`
      : total
        ? `${prog}/${total}`
        : "…";
    $("#jobBarText").textContent = job.message || "Working…";
    const fill = $("#jobBarFill");
    if (fill) {
      const pct = total
        ? Math.min(100, Math.round((prog / total) * 100))
        : steps.length
          ? Math.min(100, Math.round(((prog + (job.status === "done" ? 0 : 0.35)) / steps.length) * 100))
          : 12;
      fill.style.width = `${Math.max(pct, active ? 8 : 0)}%`;
    }
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

  function hideJobBar() {
    stopJobTimer();
    setJobControls({ pauseResume: null, showStop: false, stopping: false });
    activeJobId = null;
    const bar = $("#jobBar");
    if (bar) {
      bar.hidden = true;
      bar.classList.remove("is-paused");
    }
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
          ? `<div class="job-step-detail">${escapeHtml(s.detail)}</div>`
          : "";
        let timeLabel = "";
        if (status === "done" && s.elapsed_sec != null) {
          timeLabel = formatStepTime(s.elapsed_sec);
        } else if (status === "running" && s.elapsed_sec != null) {
          const avg = formatStepTime(s.avg_sec);
          timeLabel = avg
            ? `${formatStepTime(s.elapsed_sec)} / ~${avg}`
            : formatStepTime(s.elapsed_sec);
        } else if (s.avg_sec != null) {
          timeLabel = `avg ~${formatStepTime(s.avg_sec)}`;
        }
        const timeHtml = timeLabel
          ? `<span class="job-step-time">${escapeHtml(timeLabel)}</span>`
          : "";
        return `<li class="job-step is-${status}">
          <span class="job-step-mark" aria-hidden="true">${mark}</span>
          <span class="job-step-body">
            <span class="job-step-main">
              <span>${escapeHtml(s.label || s.key)}</span>
              ${timeHtml}
            </span>
            ${detail}
          </span>
        </li>`;
      })
      .join("");
  }

  function escapeHtml(s) {
    return String(s ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
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
      // running | queued | paused → keep polling
      await sleep(900);
    }
  }

  function initials(name) {
    if (!name) return "?";
    const parts = name.trim().split(/\s+/);
    return (parts[0][0] + (parts[1]?.[0] || parts[0][1] || "")).toUpperCase();
  }

  function fmtTime(iso) {
    if (!iso) return "not synced yet";
    try {
      return new Date(iso).toLocaleString("en-US");
    } catch {
      return iso;
    }
  }

  function renderRoster(myTeam) {
    const roster = $("#roster");
    const teamSub = $("#teamSub");
    const rosterMeta = $("#rosterMeta");
    const logo = $("#teamLogo");
    const initialsEl = $("#teamInitials");

    const name = myTeam?.name || "My Team";
    teamSub.textContent = myTeam?.manager
      ? `${name} · ${myTeam.manager}`
      : name;

    initialsEl.textContent = initials(name);
    if (myTeam?.logo) {
      logo.src = myTeam.logo;
      logo.alt = name;
      logo.hidden = false;
      initialsEl.hidden = true;
    } else {
      logo.hidden = true;
      initialsEl.hidden = false;
    }

    const slots = myTeam?.slots || [];
    const filled = myTeam?.filled ?? slots.filter((s) => s.player).length;
    rosterMeta.innerHTML = `
      <span class="pill accent">${filled}/${slots.length} filled</span>
      <span class="pill">${myTeam?.note ? "Pre-draft" : "Roster"}</span>
    `;

    if (!slots.length) {
      roster.innerHTML = `<div class="slot"><div class="pos">—</div><div class="slot-main"><p class="slot-title">No data</p><p class="slot-empty">Click Refresh</p></div></div>`;
      return;
    }

    roster.innerHTML = slots
      .map((slot) => {
        const group = slot.group || "starter";
        const player = slot.player;
        const title = escapeHtml(player?.name || "Empty slot");
        const empty = escapeHtml(
          player
            ? `${player.team || ""} · ${player.positions || ""}`.trim()
            : slot.label || slot.position
        );
        const dbId = player?.db_id;
        const clickable = dbId ? " clickable" : "";
        const idAttr = dbId ? ` data-player-id="${dbId}"` : "";
        return `
          <article class="slot ${group}${clickable}"${idAttr}${dbId ? ' role="button" tabindex="0"' : ""}>
            <div class="pos">${escapeHtml(slot.position)}</div>
            <div class="slot-main">
              <p class="slot-title">${title}</p>
              <p class="slot-empty">${empty}</p>
            </div>
            <div class="slot-tag">${group === "starter" ? "Active" : escapeHtml(group)}</div>
          </article>
        `;
      })
      .join("");

    roster.querySelectorAll(".slot[data-player-id]").forEach((el) => {
      const open = () => {
        const id = Number(el.dataset.playerId);
        if (Number.isFinite(id) && window.PlayerDetail) window.PlayerDetail.open(id);
      };
      el.addEventListener("click", open);
      el.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          open();
        }
      });
    });
  }

  function renderStandings(standings) {
    const body = $("#standingsBody");
    const sub = $("#standingsSub");
    const rows = standings?.rows || [];

    sub.textContent = standings?.pre_draft
      ? `Pre-draft · ${rows.length} teams`
      : `${rows.length} teams`;

    if (!rows.length) {
      body.innerHTML = `<tr><td colspan="4">No data — refresh from Yahoo.</td></tr>`;
      return;
    }

    body.innerHTML = rows
      .map((row) => {
        const logo = row.logo
          ? `<img src="${row.logo}" alt="">`
          : "";
        const name = (row.name || "").trim() || `Team ${row.team_id || "—"}`;
        return `
          <tr class="${row.is_mine ? "mine" : ""}">
            <td><span class="rank">${row.rank}</span></td>
            <td>
              <div class="team-cell">${logo}<span>${escapeHtml(name)}</span></div>
            </td>
            <td>${escapeHtml(row.manager || "—")}</td>
            <td>${escapeHtml(row.record || "—")}</td>
          </tr>
        `;
      })
      .join("");
  }

  const SCORING_STATS = new Set([
    "FG%", "FT%", "3PTM", "PTS", "REB", "AST", "ST", "BLK", "TO",
  ]);

  let teamLogoById = {};

  function rememberTeamLogos(standings, myTeam) {
    const map = {};
    for (const row of standings?.rows || []) {
      const id = String(row.team_id || "").trim();
      if (id && row.logo) map[id] = row.logo;
    }
    const myId = String(myTeam?.team_id || "").trim();
    if (myId && myTeam?.logo) map[myId] = myTeam.logo;
    teamLogoById = map;
  }

  function sideLogo(side) {
    const id = String(side?.team_id || "").trim();
    const src = side?.logo || (id ? teamLogoById[id] : "") || "";
    if (!src) {
      const initials = String(side?.name || "?")
        .trim()
        .slice(0, 2)
        .toUpperCase();
      return `<span class="match-logo fallback" aria-hidden="true">${escapeHtml(initials)}</span>`;
    }
    return `<img class="match-logo" src="${escapeHtml(src)}" alt="" loading="lazy" decoding="async">`;
  }

  let matchupState = {
    schedule: [],
    index: 0,
    leagueWeek: null,
    leagueRows: [],
  };

  function teamIdsInMatch(entry) {
    return new Set(
      [entry?.home?.team_id, entry?.away?.team_id]
        .filter((id) => id != null && String(id) !== "")
        .map(String)
    );
  }

  function buildSchedule(matchups, myTeamId) {
    const mine = matchups?.my_matchup || {};
    const myRow = (matchups?.matchups || []).find((r) => r.is_mine) || {};
    const myId = String(
      myTeamId ||
        matchups?.my_schedule_team_id ||
        ""
    );

    let schedule = Array.isArray(matchups?.my_schedule)
      ? [...matchups.my_schedule]
      : [];
    if (myId) {
      schedule = schedule.filter((entry) => teamIdsInMatch(entry).has(myId));
    }

    // Ensure current my_matchup is the week shown for the league week.
    if (mine.home?.name || mine.away?.name) {
      const week = mine.week ?? matchups?.week;
      const entry = {
        week,
        url: mine.url,
        home: {
          ...(mine.home || {}),
          manager: mine.home?.manager || myRow.home?.manager || "",
        },
        away: {
          ...(mine.away || {}),
          manager: mine.away?.manager || myRow.away?.manager || "",
        },
        categories: mine.categories || [],
        score: mine.score || {
          home: mine.home?.categories_won,
          away: mine.away?.categories_won,
        },
        is_mine: true,
        is_current: true,
      };
      const idx = schedule.findIndex(
        (s) => week != null && Number(s.week) === Number(week)
      );
      if (idx >= 0) schedule[idx] = { ...schedule[idx], ...entry };
      else schedule.push(entry);
    }

    schedule.sort((a, b) => (Number(a.week) || 0) - (Number(b.week) || 0));
    return schedule;
  }

  function scorePair(entry) {
    const home = entry?.home || {};
    const away = entry?.away || {};
    const hs =
      entry?.score?.home ?? home.categories_won;
    const as_ =
      entry?.score?.away ?? away.categories_won;
    return { hs: hs ?? "—", as_: as_ ?? "—" };
  }

  function renderMyMatchupEntry(entry) {
    const myBox = $("#myMatchup");
    const catGrid = $("#catGrid");
    if (!entry) {
      myBox.innerHTML = `<div class="match-side"><p class="name">No matchup</p><p class="mgr">Click Refresh</p></div>`;
      catGrid.innerHTML = "";
      return;
    }
    const home = entry.home || {};
    const away = entry.away || {};
    const { hs, as_ } = scorePair(entry);
    myBox.innerHTML = `
      <div class="match-side">
        <div class="match-side-head">
          ${sideLogo(home)}
          <div class="match-side-text">
            <p class="name">${escapeHtml(home.name || "—")}</p>
            <p class="mgr">${escapeHtml(home.manager || " ")}</p>
          </div>
        </div>
      </div>
      <div class="match-score">
        <div class="nums">${escapeHtml(hs)} – ${escapeHtml(as_)}</div>
        <div class="vs" title="Categories won (H2H)">cats won</div>
      </div>
      <div class="match-side away">
        <div class="match-side-head">
          <div class="match-side-text">
            <p class="name">${escapeHtml(away.name || "—")}</p>
            <p class="mgr">${escapeHtml(away.manager || " ")}</p>
          </div>
          ${sideLogo(away)}
        </div>
      </div>
    `;
    const cats = (entry.categories || []).filter((c) => SCORING_STATS.has(c.stat));
    catGrid.innerHTML = cats
      .map((c) => {
        const cls =
          c.winner === "home" ? "home-win" : c.winner === "away" ? "away-win" : "";
        return `
          <div class="cat-pill ${cls}">
            <div class="stat">${escapeHtml(c.stat)}</div>
            <div class="vals">${escapeHtml(c.home)} · ${escapeHtml(c.away)}</div>
          </div>
        `;
      })
      .join("");
  }

  function updateMatchupNav() {
    const nav = $("#matchupNav");
    const label = $("#matchupNavLabel");
    const prev = $("#btnMatchupPrev");
    const next = $("#btnMatchupNext");
    const all = $("#btnMatchupAll");
    const { schedule, index } = matchupState;
    if (!nav) return;
    if (!schedule.length) {
      nav.hidden = true;
      return;
    }
    nav.hidden = false;
    const entry = schedule[index] || {};
    const week = entry.week != null ? `Week ${entry.week}` : "Matchup";
    const cur = entry.is_current ? " · now" : "";
    if (label) label.textContent = `${week}${cur}`;
    if (prev) prev.disabled = index <= 0;
    if (next) next.disabled = index >= schedule.length - 1;
    if (all) all.disabled = false;
  }

  function showScheduleAt(index) {
    const { schedule } = matchupState;
    if (!schedule.length) return;
    matchupState.index = Math.max(0, Math.min(schedule.length - 1, index));
    renderMyMatchupEntry(schedule[matchupState.index]);
    updateMatchupNav();
    const sub = $("#matchupSub");
    if (sub) {
      const entry = schedule[matchupState.index];
      const leagueWeek = matchupState.leagueWeek;
      const n = matchupState.leagueRows.length;
      const bits = [];
      if (entry?.week != null) bits.push(`Viewing week ${entry.week}`);
      if (leagueWeek != null) bits.push(`League week ${leagueWeek}`);
      if (n) bits.push(`${n} games`);
      sub.textContent = bits.join(" · ") || "No data";
    }
  }

  function openScheduleModal() {
    const modal = $("#scheduleModal");
    const list = $("#scheduleList");
    const sub = $("#scheduleSub");
    if (!modal || !list) return;
    const { schedule, index } = matchupState;
    if (sub) {
      sub.textContent = schedule.length
        ? `${schedule.length} week${schedule.length === 1 ? "" : "s"}`
        : "No schedule yet — run Refresh";
    }
    list.innerHTML = schedule.length
      ? schedule
          .map((entry, i) => {
            const { hs, as_ } = scorePair(entry);
            const cls = [
              entry.is_current ? "is-current" : "",
              i === index ? "is-active" : "",
            ]
              .filter(Boolean)
              .join(" ");
            return `
              <button type="button" class="schedule-row ${cls}" data-schedule-index="${i}">
                <span class="schedule-week">W${escapeHtml(entry.week ?? "—")}</span>
                <span class="schedule-side">${escapeHtml(entry.home?.name || "—")}</span>
                <span class="schedule-score">${escapeHtml(hs)}–${escapeHtml(as_)}</span>
                <span class="schedule-side away">${escapeHtml(entry.away?.name || "—")}</span>
              </button>
            `;
          })
          .join("")
      : `<p class="panel-sub">No matchups saved yet.</p>`;
    modal.hidden = false;
    document.body.classList.add("modal-open");
  }

  function closeScheduleModal() {
    const modal = $("#scheduleModal");
    if (modal) modal.hidden = true;
    document.body.classList.remove("modal-open");
  }

  function renderMatchups(matchups, myTeamId) {
    const sub = $("#matchupSub");
    const list = $("#matchupList");

    const week = matchups?.week;
    const rows = matchups?.matchups || [];
    const schedule = buildSchedule(matchups, myTeamId);

    matchupState.schedule = schedule;
    matchupState.leagueWeek = week;
    matchupState.leagueRows = rows;

    let startIndex = schedule.findIndex((r) => r.is_current);
    if (startIndex < 0 && week != null) {
      startIndex = schedule.findIndex((r) => Number(r.week) === Number(week));
    }
    if (startIndex < 0) startIndex = 0;
    matchupState.index = startIndex;

    if (!schedule.length) {
      sub.textContent = week
        ? `Week ${week} · ${rows.length} games`
        : rows.length
          ? `${rows.length} games`
          : "No data";
      renderMyMatchupEntry(null);
      updateMatchupNav();
    } else {
      showScheduleAt(startIndex);
    }

    if (!rows.length) {
      list.innerHTML = `<div class="match-row"><span class="side">No matchups</span></div>`;
      return;
    }

    list.innerHTML = rows
      .map((m) => {
        const score =
          m.score && (m.score.home != null || m.score.away != null)
            ? `${m.score.home ?? 0}–${m.score.away ?? 0}`
            : "vs";
        return `
          <div class="match-row ${m.is_mine ? "mine" : ""}">
            <span class="side">
              <span class="match-side-inline">${sideLogo(m.home)}<span>${escapeHtml(m.home?.name || "—")}</span></span>
            </span>
            <span class="mid">${escapeHtml(score)}</span>
            <span class="side right">
              <span class="match-side-inline reverse">${sideLogo(m.away)}<span>${escapeHtml(m.away?.name || "—")}</span></span>
            </span>
          </div>
        `;
      })
      .join("");
  }

  const RULE_KEYS = [
    "Draft Type",
    "Draft Time",
    "Scoring Type",
    "Max Teams",
    "Live Draft Pick Time",
    "Max Acquisitions per Week",
    "Waiver Type",
    "Waiver Time",
    "Trade End Date",
    "Trade Review",
    "Playoffs",
    "Playoff Tie-Breaker",
    "Playoff Reseeding",
    "Weekly Deadline",
    "Roster Positions",
  ];

  function renderRules(rules) {
    const chips = $("#statChips");
    const grid = $("#rulesGrid");
    const settings = rules?.settings || {};
    const cats = rules?.stat_categories || [];

    chips.innerHTML = cats
      .map((c) => {
        const short = String(c).match(/\(([^)]+)\)/);
        return `<span class="chip">${short ? short[1] : c}</span>`;
      })
      .join("");

    const items = RULE_KEYS.filter((k) => settings[k]).map(
      (k) => `
        <div>
          <dt>${k}</dt>
          <dd>${settings[k]}</dd>
        </div>
      `
    );
    grid.innerHTML =
      items.join("") ||
      "<div><dt>Rules</dt><dd>Not synced yet</dd></div>";
  }

  function renderDashboard(data) {
    const meta = data.meta || {};
    const rules = data.rules || {};
    const standings = data.standings || {};
    const myTeam = data.my_team || {};
    const matchups = data.matchups || {};

    $("#leagueName").textContent =
      meta.league_name || rules.league_name || "Snopcministri";

    const bits = [];
    if (meta.draft_time) bits.push(`Draft: ${meta.draft_time}`);
    if (meta.week) bits.push(`Week ${meta.week}`);
    if (meta.scoring_type) bits.push(meta.scoring_type);
    bits.push(`Synced: ${fmtTime(meta.synced_at || myTeam.synced_at)}`);
    $("#leagueMeta").textContent = bits.join(" · ");

    rememberTeamLogos(standings, myTeam);
    renderRoster(myTeam);
    renderStandings(standings);
    renderMatchups(matchups, myTeam.team_id || meta.my_team_id);
    renderRules(rules);
  }

  function summarizeResult(r) {
    if (!r) return "Refresh complete.";
    const parts = [];
    const avail = r.steps?.available;
    const roster = r.steps?.roster;
    const news = r.steps?.news;
    const inj = r.steps?.injuries;
    const trends = r.steps?.trends;
    if (roster && !roster.skipped) {
      parts.push(
        `roster ${roster.refreshed ?? 0}↻ ${roster.added ?? 0}+`
      );
    }
    if (news) parts.push(`news +${news.inserted ?? 0}`);
    if (inj) parts.push(`inj ${inj.inserted ?? 0}`);
    if (trends) parts.push(`trends ${trends.inserted ?? 0}`);
    if (avail) {
      parts.push(
        `FA ${avail.refreshed ?? 0}↻ ${avail.added ?? 0}+ (of ${avail.count ?? "—"})`
      );
    }
    const took = formatStepTime(r.timing?.total_sec);
    const avg = formatStepTime(r.timing?.avg_total_sec);
    if (took) parts.push(`time ${took}${avg ? ` · avg ${avg}` : ""}`);
    return parts.length ? `Done — ${parts.join(" · ")}` : "Refresh complete.";
  }

  async function loadDashboard() {
    const res = await fetch("/api/dashboard");
    const data = await res.json();
    renderDashboard(data);
    if (!data.has_data) {
      showAlert(
        "No saved data yet. Open Chrome with start_chrome_debug.bat, then click Refresh."
      );
    } else {
      showAlert("");
    }
  }

  async function syncNow() {
    setSyncing(true);
    showAlert("");
    try {
      const res = await fetch("/api/sync", { method: "POST" });
      const data = await res.json();
      if (!data.ok) {
        showAlert(data.error || "Sync failed.");
        hideJobBar();
        return;
      }
      const jobId = data.job?.id || data.job_id;
      if (jobId) {
        const job = await pollJob(jobId);
        if (job.status === "cancelled") {
          showAlert("Refresh cancelled.");
        } else {
          showAlert(summarizeResult(job.result));
        }
      }
      await loadDashboard();
    } catch (err) {
      showAlert(String(err));
    } finally {
      setSyncing(false);
      window.setTimeout(() => {
        if (!$("#btnSync")?.disabled) hideJobBar();
      }, 8000);
    }
  }

  async function resumeActiveJobIfAny() {
    try {
      const data = await (await fetch("/api/jobs/active")).json();
      if (
        data.job &&
        (data.job.status === "running" ||
          data.job.status === "queued" ||
          data.job.status === "paused")
      ) {
        setSyncing(true);
        showJobBar(data.job);
        const job = await pollJob(data.job.id);
        if (job.status === "cancelled") {
          showAlert("Refresh cancelled.");
        } else {
          showAlert(summarizeResult(job.result));
        }
        await loadDashboard();
      }
    } catch (_) {
      /* ignore */
    } finally {
      setSyncing(false);
    }
  }

  window.PlayerDetail?.init({
    onAlert: showAlert,
    hideDelete: true,
    onFavouriteChange: () => {},
  });

  btnSync.addEventListener("click", syncNow);
  $("#btnPauseResume")?.addEventListener("click", togglePauseResume);
  $("#btnCancelJob")?.addEventListener("click", cancelActiveJob);
  $("#btnMatchupPrev")?.addEventListener("click", () => {
    showScheduleAt(matchupState.index - 1);
  });
  $("#btnMatchupNext")?.addEventListener("click", () => {
    showScheduleAt(matchupState.index + 1);
  });
  $("#btnMatchupAll")?.addEventListener("click", openScheduleModal);
  document.querySelectorAll("[data-close-schedule]").forEach((el) => {
    el.addEventListener("click", closeScheduleModal);
  });
  $("#scheduleList")?.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-schedule-index]");
    if (!btn) return;
    const i = Number(btn.getAttribute("data-schedule-index"));
    if (!Number.isFinite(i)) return;
    showScheduleAt(i);
    closeScheduleModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("#scheduleModal")?.hidden) closeScheduleModal();
  });

  loadDashboard().then(resumeActiveJobIfAny);
})();
