(() => {
  const $ = (sel) => document.querySelector(sel);
  const STEP_KEYS = [
    "league",
    "roster",
    "news",
    "injuries",
    "trends",
    "available",
  ];

  let activeJobId = null;
  let hideControlsJobId = null;
  let running = false;

  function alertEl() {
    return $("#alert");
  }

  function showAlert(msg) {
    const el = alertEl();
    if (!el) return;
    el.hidden = !msg;
    el.textContent = msg || "";
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

  function readSteps() {
    const steps = {};
    for (const key of STEP_KEYS) {
      const el = document.querySelector(`[data-gr-step="${key}"]`);
      steps[key] = el ? !!el.checked : true;
    }
    return steps;
  }

  function writeSteps(steps) {
    const src = steps && typeof steps === "object" ? steps : {};
    for (const key of STEP_KEYS) {
      const el = document.querySelector(`[data-gr-step="${key}"]`);
      if (el) el.checked = src[key] !== false;
    }
  }

  function mountToBody(el) {
    if (el && el.parentElement !== document.body) {
      document.body.appendChild(el);
    }
  }

  function openModal() {
    const modal = $("#globalRefreshModal");
    if (!modal) return;
    mountToBody(modal);
    modal.hidden = false;
    document.body.classList.add("modal-open");
    loadDefaults();
  }

  function closeModal() {
    const modal = $("#globalRefreshModal");
    if (modal) modal.hidden = true;
    document.body.classList.remove("modal-open");
  }

  async function loadDefaults() {
    try {
      const res = await fetch("/api/app-settings");
      const data = await res.json();
      if (!data.ok) return;
      const s = data.settings || {};
      writeSteps(s.refresh_steps);
      const count = $("#grAvailableCount");
      if (count) count.value = String(s.refresh_available_count ?? 100);
    } catch (_) {
      /* keep HTML defaults */
    }
  }

  function ensureJobBar() {
    let bar = $("#jobBar");
    if (bar) return bar;
    const html = `
      <div id="jobBar" class="job-bar" hidden>
        <div class="job-bar-head">
          <strong id="jobBarLabel">Main refresh</strong>
          <div class="job-bar-head-meta">
            <button type="button" id="btnPauseResume" class="btn ghost tiny job-pause-resume" hidden data-mode="pause">Pause</button>
            <button type="button" id="btnCancelJob" class="btn ghost tiny job-cancel" hidden>Stop</button>
            <span id="jobBarMeta" class="job-bar-meta">—</span>
          </div>
        </div>
        <p id="jobBarText" class="job-bar-text">Starting…</p>
        <div class="job-bar-track" aria-hidden="true">
          <div class="job-bar-fill" id="jobBarFill"></div>
        </div>
        <ol id="jobSteps" class="job-steps" hidden></ol>
        <p class="job-bar-hint">Keep Chrome debug open. Pause / Resume / Stop control the whole run.</p>
      </div>`;
    const wrap = document.createElement("div");
    wrap.innerHTML = html.trim();
    bar = wrap.firstElementChild;
    const alert = $("#alert");
    const app = document.querySelector(".app");
    if (alert?.parentNode) {
      alert.parentNode.insertBefore(bar, alert.nextSibling);
    } else if (app) {
      const header = app.querySelector("header.top");
      if (header?.nextSibling) app.insertBefore(bar, header.nextSibling);
      else app.prepend(bar);
    } else {
      document.body.prepend(bar);
    }
    $("#btnPauseResume")?.addEventListener("click", togglePauseResume);
    $("#btnCancelJob")?.addEventListener("click", cancelActiveJob);
    return bar;
  }

  function jobBarEls() {
    ensureJobBar();
    return {
      bar: $("#jobBar"),
      label: $("#jobBarLabel"),
      meta: $("#jobBarMeta"),
      text: $("#jobBarText"),
      fill: $("#jobBarFill"),
      steps: $("#jobSteps"),
      pause: $("#btnPauseResume"),
      stop: $("#btnCancelJob"),
      timer: $("#jobBarTimer"),
    };
  }

  function setJobControls({ pauseResume, showStop, stopping }) {
    const { pause, stop } = jobBarEls();
    if (pause) {
      if (pauseResume === "pause" || pauseResume === "resume") {
        pause.hidden = false;
        pause.disabled = false;
        pause.dataset.mode = pauseResume;
        pause.textContent = pauseResume === "resume" ? "Resume" : "Pause";
      } else {
        pause.hidden = true;
        pause.disabled = true;
      }
    }
    if (stop) {
      stop.hidden = !showStop && !stopping;
      stop.disabled = !!stopping || !showStop;
      stop.textContent = stopping ? "Stopping…" : "Stop";
    }
  }

  function renderSteps(steps) {
    const { steps: list } = jobBarEls();
    if (!list) return;
    if (!Array.isArray(steps) || !steps.length) {
      list.hidden = true;
      list.innerHTML = "";
      return;
    }
    list.hidden = false;
    list.innerHTML = steps
      .map((s) => {
        const st = s.status || "pending";
        const mark =
          st === "done"
            ? "✓"
            : st === "running"
              ? "●"
              : st === "error"
                ? "!"
                : "○";
        const detail = s.detail
          ? `<div class="job-step-detail">${escapeHtml(s.detail)}</div>`
          : "";
        return `<li class="job-step is-${st}">
          <span class="job-step-mark" aria-hidden="true">${mark}</span>
          <span class="job-step-body">
            <span class="job-step-main">
              <span>${escapeHtml(s.label || s.key || "Step")}</span>
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

  function showJobBar(job) {
    const els = jobBarEls();
    if (!els.bar) return;
    els.bar.hidden = false;
    if (job.id) activeJobId = job.id;
    if (job.id && job.id !== hideControlsJobId) hideControlsJobId = null;
    const paused = !!job.paused || job.status === "paused";
    els.bar.classList.toggle("is-paused", paused);
    if (els.label) els.label.textContent = job.label || "Main refresh";
    const total = job.total || 0;
    const prog = job.progress || 0;
    if (els.meta) els.meta.textContent = total ? `${prog}/${total}` : "…";
    if (els.text) els.text.textContent = job.message || "Working…";
    if (els.fill) {
      const pct = total ? Math.min(100, Math.round((prog / total) * 100)) : 12;
      els.fill.style.width = `${pct}%`;
    }
    if (job.steps) renderSteps(job.steps);
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
    const els = jobBarEls();
    if (els.bar) {
      els.bar.hidden = true;
      els.bar.classList.remove("is-paused");
    }
  }

  async function postJobAction(action, msg) {
    if (!activeJobId) return;
    try {
      const res = await fetch(`/api/jobs/${activeJobId}/${action}`, {
        method: "POST",
      });
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

  async function togglePauseResume() {
    const els = jobBarEls();
    const mode = els.pause?.dataset?.mode || "pause";
    if (mode === "resume") await postJobAction("resume", "Resuming…");
    else await postJobAction("pause", "Paused — click Resume to continue.");
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

  function summarizeResult(r) {
    if (!r) return "Refresh complete.";
    const parts = [];
    const avail = r.steps?.available;
    const roster = r.steps?.roster;
    const news = r.steps?.news;
    const inj = r.steps?.injuries;
    const trends = r.steps?.trends;
    if (roster && !roster.skipped) {
      parts.push(`roster ${roster.refreshed ?? 0}↻ ${roster.added ?? 0}+`);
    }
    if (news) parts.push(`news +${news.inserted ?? 0}`);
    if (inj) parts.push(`inj ${inj.inserted ?? 0}`);
    if (trends) parts.push(`trends ${trends.inserted ?? 0}`);
    if (avail) {
      parts.push(
        `FA ${avail.refreshed ?? 0}↻ ${avail.added ?? 0}+ (of ${avail.count ?? "—"})`
      );
    }
    return parts.length ? `Done — ${parts.join(" · ")}` : "Refresh complete.";
  }

  async function startRefresh(e) {
    e?.preventDefault?.();
    if (running) return;
    const steps = readSteps();
    if (!STEP_KEYS.some((k) => steps[k])) {
      showAlert("Pick at least one refresh step.");
      return;
    }
    const count = Number($("#grAvailableCount")?.value || 100);
    const remember = !!$("#grRemember")?.checked;
    const btn = $("#btnStartGlobalRefresh");
    const headerBtn = $("#btnGlobalRefresh");
    running = true;
    setBusy(btn, true, "Start refresh", "Starting…");
    if (headerBtn) headerBtn.disabled = true;
    showAlert("");
    closeModal();
    try {
      const res = await fetch("/api/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          refresh_steps: steps,
          refresh_available_count: count,
          remember,
        }),
      });
      const data = await res.json();
      if (!data.ok) {
        showAlert(data.error || "Could not start refresh.");
        hideJobBar();
        return;
      }
      const jobId = data.job?.id || data.job_id;
      if (jobId) {
        showJobBar(data.job || { id: jobId, label: "Main refresh", status: "running" });
        const job = await pollJob(jobId);
        if (job.status === "cancelled") {
          showAlert("Refresh cancelled.");
        } else {
          showAlert(summarizeResult(job.result));
        }
        window.dispatchEvent(
          new CustomEvent("yahoonba:main-refresh-done", { detail: { job } })
        );
      }
    } catch (err) {
      showAlert(String(err));
    } finally {
      running = false;
      setBusy(btn, false, "Start refresh", "Starting…");
      if (headerBtn) headerBtn.disabled = false;
      window.setTimeout(() => {
        if (!running) hideJobBar();
      }, 8000);
    }
  }

  async function resumeActiveJobIfAny() {
    try {
      const data = await (await fetch("/api/jobs/active")).json();
      if (
        data.job &&
        data.job.kind === "main_refresh" &&
        (data.job.status === "running" ||
          data.job.status === "queued" ||
          data.job.status === "paused")
      ) {
        running = true;
        const headerBtn = $("#btnGlobalRefresh");
        if (headerBtn) headerBtn.disabled = true;
        showJobBar(data.job);
        const job = await pollJob(data.job.id);
        if (job.status === "cancelled") showAlert("Refresh cancelled.");
        else showAlert(summarizeResult(job.result));
        window.dispatchEvent(
          new CustomEvent("yahoonba:main-refresh-done", { detail: { job } })
        );
      }
    } catch (_) {
      /* ignore */
    } finally {
      running = false;
      const headerBtn = $("#btnGlobalRefresh");
      if (headerBtn) headerBtn.disabled = false;
    }
  }

  // Escape header stacking/overflow — same pattern as Notes modal.
  mountToBody($("#globalRefreshModal"));

  $("#btnGlobalRefresh")?.addEventListener("click", openModal);
  document.querySelectorAll("[data-close-global-refresh]").forEach((el) => {
    el.addEventListener("click", closeModal);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("#globalRefreshModal")?.hidden) closeModal();
  });
  $("#globalRefreshForm")?.addEventListener("submit", startRefresh);
  $("#btnGrSelectAll")?.addEventListener("click", () => {
    writeSteps(Object.fromEntries(STEP_KEYS.map((k) => [k, true])));
  });
  $("#btnGrSelectNone")?.addEventListener("click", () => {
    writeSteps(Object.fromEntries(STEP_KEYS.map((k) => [k, false])));
  });
  $("#btnPauseResume")?.addEventListener("click", togglePauseResume);
  $("#btnCancelJob")?.addEventListener("click", cancelActiveJob);

  window.GlobalRefresh = {
    open: openModal,
    close: closeModal,
    isRunning: () => running,
  };

  resumeActiveJobIfAny();
})();
