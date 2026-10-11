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

  function injuryRow(p) {
    const name = p.matched_name || p.player_name || "—";
    const nameHtml = p.player_id
      ? `<a class="news-player-link" href="/players?player=${p.player_id}">${esc(name)}</a>`
      : `<strong>${esc(name)}</strong>`;
    return `
      <tr>
        <td>${nameHtml}<div class="subline">${esc(p.position) || "—"}</div></td>
        <td>${cell(p.updated_at)}</td>
        <td>${cell(p.injury)}</td>
        <td>${cell(p.injury_status)}</td>
      </tr>`;
  }

  function teamLogoHtml(g) {
    const src = g.logo || window.NbaTeams?.resolve(g.team)?.logo || "";
    if (!src) return "";
    return `<img class="nba-team-logo" src="${esc(src)}" alt="" width="40" height="40" loading="lazy" decoding="async">`;
  }

  function renderInjuries(data) {
    const root = $("#injuryGroups");
    const sub = $("#injuriesSub");
    const playerCount = data.player_count || 0;
    const teamCount = data.team_count || 0;
    const scraped = data.scraped_at ? ` · scraped ${data.scraped_at}` : "";
    sub.textContent = playerCount
      ? `${playerCount} players across ${teamCount} team${teamCount === 1 ? "" : "s"}${scraped}`
      : "No injuries saved yet. Click Refresh injuries.";

    const groups = data.groups || [];
    if (!groups.length) {
      root.innerHTML = `<section class="panel"><p class="muted">Fetch the CBS Sports injury report to populate this page.</p></section>`;
      return;
    }

    root.innerHTML = groups
      .map((g) => {
        const rows = (g.players || []).map(injuryRow).join("");
        const teamId = esc(g.team).replace(/\s+/g, "-");
        const title = g.display_name || g.team;
        const logo = teamLogoHtml(g);
        const abbr = g.abbr ? `${esc(g.abbr)} · ` : "";
        return `
          <section class="panel team-accordion" data-team="${esc(g.team)}">
            <button type="button" class="team-accordion-toggle" aria-expanded="false" aria-controls="inj-body-${teamId}">
              <div class="team-accordion-label">
                <div class="team-accordion-head">
                  ${logo}
                  <div>
                    <h2 class="team-title">${esc(title)}</h2>
                    <p class="panel-sub">${abbr}${g.count} player${g.count === 1 ? "" : "s"}</p>
                  </div>
                </div>
              </div>
              <span class="team-accordion-chevron" aria-hidden="true">▾</span>
            </button>
            <div class="team-accordion-body" id="inj-body-${teamId}" hidden>
              <div class="table-wrap">
                <table class="players-table">
                  <thead>
                    <tr>
                      <th>Player</th>
                      <th>Updated</th>
                      <th>Injury</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>${rows}</tbody>
                </table>
              </div>
            </div>
          </section>`;
      })
      .join("");

    root.querySelectorAll(".team-accordion-toggle").forEach((btn) => {
      btn.addEventListener("click", () => {
        const panel = btn.closest(".team-accordion");
        const body = panel?.querySelector(".team-accordion-body");
        if (!body) return;
        const open = body.hidden;
        body.hidden = !open;
        btn.setAttribute("aria-expanded", open ? "true" : "false");
        panel.classList.toggle("is-open", open);
      });
    });
  }

  async function loadInjuries() {
    showAlert("");
    try {
      const res = await fetch("/api/db/injuries");
      const data = await res.json();
      if (!data.ok) {
        showAlert(data.error || "Could not load injuries.");
        return;
      }
      renderInjuries(data);
    } catch (err) {
      showAlert(String(err));
    }
  }

  async function refreshInjuries() {
    const btn = $("#btnRefreshInjuries");
    setBusy(btn, true, "Refresh injuries", "Fetching…");
    showAlert("");
    try {
      const res = await fetch("/api/db/injuries/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      const data = await res.json();
      if (!data.ok) {
        showAlert(data.error || "Could not start injuries refresh.");
        hideJobBar();
        return;
      }
      const jobId = data.job?.id || data.job_id;
      if (jobId) {
        const job = await pollJob(jobId);
        if (job.status === "cancelled") {
          showAlert("Injuries refresh cancelled.");
        } else {
          const r = job.result || {};
          showAlert(
            `Saved ${r.inserted ?? r.fetched ?? 0} injuries across ${r.team_count ?? "?"} teams (${r.matched ?? 0} linked to DB).`
          );
        }
      }
      await loadInjuries();
    } catch (err) {
      showAlert(String(err));
    } finally {
      setBusy(btn, false, "Refresh injuries", "Fetching…");
      hideJobBar();
    }
  }

  $("#btnPauseResume")?.addEventListener("click", togglePauseResume);
  $("#btnCancelJob")?.addEventListener("click", cancelActiveJob);
  $("#btnRefreshInjuries")?.addEventListener("click", refreshInjuries);
  $("#btnReloadInjuries")?.addEventListener("click", loadInjuries);
  loadInjuries();
})();
