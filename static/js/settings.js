(() => {
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));
  const alertEl = $("#alert");

  const STEP_KEYS = [
    "league",
    "roster",
    "news",
    "injuries",
    "trends",
    "available",
  ];
  const FREE_W_KEYS = ["l7_stats", "l14_stats", "adds", "rank_l7"];
  const FA_W_KEYS = [
    "fg_pct",
    "ft_pct",
    "threes",
    "pts",
    "reb",
    "ast",
    "stl",
    "blk",
    "tov",
  ];

  function showAlert(msg) {
    alertEl.hidden = !msg;
    alertEl.textContent = msg || "";
  }

  function setBusy(btn, on, idleLabel, busyLabel) {
    const label = btn?.querySelector(".btn-label");
    const spinner = btn?.querySelector(".btn-spinner");
    if (btn) btn.disabled = on;
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

  function numOr(el, fallback) {
    const n = Number(el?.value);
    return Number.isFinite(n) ? n : fallback;
  }

  function readRefreshSteps() {
    const steps = {};
    for (const key of STEP_KEYS) {
      const el = document.querySelector(`[data-refresh-step="${key}"]`);
      steps[key] = el ? !!el.checked : true;
    }
    return steps;
  }

  function writeRefreshSteps(steps) {
    const src = steps && typeof steps === "object" ? steps : {};
    for (const key of STEP_KEYS) {
      const el = document.querySelector(`[data-refresh-step="${key}"]`);
      if (el) el.checked = src[key] !== false;
    }
  }

  function readWeightMap(attr, keys) {
    const out = {};
    for (const key of keys) {
      const el = document.querySelector(`[${attr}="${key}"]`);
      out[key] = numOr(el, 1);
    }
    return out;
  }

  function writeWeightMap(attr, weights) {
    const src = weights && typeof weights === "object" ? weights : {};
    for (const key of Object.keys(src)) {
      const el = document.querySelector(`[${attr}="${key}"]`);
      if (el && src[key] != null) el.value = String(src[key]);
    }
  }

  function setTab(tabId) {
    $$(".settings-tab").forEach((btn) => {
      const on = btn.dataset.tab === tabId;
      btn.classList.toggle("active", on);
      btn.setAttribute("aria-selected", on ? "true" : "false");
    });
    $$("[data-tab-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.tabPanel !== tabId;
    });
    const saveBar = $("[data-save-bar]");
    const panel = document.querySelector(`[data-tab-panel="${tabId}"]`);
    if (saveBar && panel && tabId !== "database") {
      panel.appendChild(saveBar);
      saveBar.hidden = false;
    } else if (saveBar) {
      saveBar.hidden = true;
    }
    try {
      localStorage.setItem("settings_tab", tabId);
    } catch (_) {
      /* ignore */
    }
  }

  function bindTabs() {
    $$(".settings-tab").forEach((btn) => {
      btn.addEventListener("click", () => setTab(btn.dataset.tab));
    });
    let start = "league";
    try {
      start = localStorage.getItem("settings_tab") || "league";
    } catch (_) {
      /* ignore */
    }
    if (!document.querySelector(`[data-tab-panel="${start}"]`)) start = "league";
    setTab(start);
  }

  async function loadCount() {
    const sub = $("#dbCountSub");
    if (!sub) return;
    try {
      const res = await fetch("/api/db/players");
      const data = await res.json();
      if (!data.ok) {
        sub.textContent = "Could not load player count.";
        return;
      }
      const n = data.count ?? (data.players || []).length;
      sub.textContent = `${n} player${n === 1 ? "" : "s"} in local database`;
    } catch (err) {
      sub.textContent = String(err);
    }
  }

  async function populateTeams(selectedId) {
    const sel = $("#myTeamSelect");
    if (!sel) return;
    try {
      const res = await fetch("/api/dashboard");
      const data = await res.json();
      const rows = data.standings?.rows || [];
      const list = rows
        .map((t) => ({
          id: String(t.team_id ?? ""),
          name: t.name || `Team ${t.team_id}`,
          manager: t.manager || "",
        }))
        .filter((t) => t.id);

      sel.innerHTML =
        `<option value="">Detect from Yahoo login…</option>` +
        list
          .map((t) => {
            const label = t.manager ? `${t.name} · ${t.manager}` : t.name;
            const selected =
              selectedId && String(selectedId) === t.id ? " selected" : "";
            return `<option value="${esc(t.id)}"${selected}>${esc(label)}</option>`;
          })
          .join("");
    } catch (_) {
      /* keep default option */
    }
  }

  function applySettings(s, effective) {
    const eff = effective || {};
    const league = $("#leagueIdInput");
    if (league) {
      league.value = s.league_id || eff.league_id || "";
    }
    const count = $("#refreshAvailableCount");
    if (count) count.value = String(s.refresh_available_count ?? 100);
    writeRefreshSteps(s.refresh_steps);

    const pool = $("#freePool");
    if (pool) pool.value = String(s.free_players_pool ?? 100);
    const top = $("#freeTop");
    if (top) top.value = String(s.free_players_top ?? 10);
    writeWeightMap("data-free-w", s.free_players_score_weights);

    const stream = $("#streamTop");
    if (stream) stream.value = String(s.streaming_top_n ?? 10);
    const faLim = $("#faLimit");
    if (faLim) faLim.value = String(s.fa_ranking_limit ?? 25);
    const anTop = $("#analyticsTop");
    if (anTop) anTop.value = String(s.analytics_top_n ?? 5);
    writeWeightMap("data-fa-w", s.fa_default_weights);

    const dbg = $("#chromeDbg");
    if (dbg) {
      dbg.value =
        s.chrome_debugger_address ||
        eff.chrome_debugger_address ||
        "127.0.0.1:9222";
    }
    const wait = $("#pageWait");
    if (wait) {
      wait.value = String(
        s.page_load_wait_sec ?? eff.page_load_wait_sec ?? 4
      );
    }
  }

  function collectPayload() {
    return {
      league_id: ($("#leagueIdInput")?.value || "").trim() || null,
      my_team_id: $("#myTeamSelect")?.value || null,
      refresh_available_count: numOr($("#refreshAvailableCount"), 100),
      refresh_steps: readRefreshSteps(),
      free_players_pool: numOr($("#freePool"), 100),
      free_players_top: numOr($("#freeTop"), 10),
      free_players_score_weights: readWeightMap("data-free-w", FREE_W_KEYS),
      streaming_top_n: numOr($("#streamTop"), 10),
      fa_ranking_limit: numOr($("#faLimit"), 25),
      fa_default_weights: readWeightMap("data-fa-w", FA_W_KEYS),
      analytics_top_n: numOr($("#analyticsTop"), 5),
      chrome_debugger_address: ($("#chromeDbg")?.value || "").trim(),
      page_load_wait_sec: numOr($("#pageWait"), 4),
    };
  }

  async function loadSettings() {
    try {
      const res = await fetch("/api/app-settings");
      const data = await res.json();
      if (!data.ok) return;
      const s = data.settings || {};
      applySettings(s, data.effective || {});
      await populateTeams(s.my_team_id || "");
    } catch (err) {
      showAlert(String(err));
    }
  }

  async function saveSettings(e) {
    e?.preventDefault?.();
    const btn = $("#btnSaveSettings");
    setBusy(btn, true, "Save settings", "Saving…");
    showAlert("");
    try {
      const payload = collectPayload();
      const res = await fetch("/api/app-settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!data.ok) {
        showAlert(data.error || "Could not save settings.");
        return;
      }
      const s = data.settings || {};
      applySettings(s, data.effective || {});
      writeRefreshSteps(s.refresh_steps);
      const onCount = STEP_KEYS.filter(
        (k) => (s.refresh_steps || {})[k] !== false
      ).length;
      const teamLabel =
        $("#myTeamSelect")?.selectedOptions?.[0]?.textContent || "auto";
      const dashName = data.dashboard?.my_team?.name;
      showAlert(
        `Saved · team ${dashName || teamLabel} · refresh ${onCount}/6 · FA pool ${s.free_players_pool}/${s.free_players_top}` +
          (s.league_id ? ` · league ${s.league_id}` : "")
      );
    } catch (err) {
      showAlert(String(err));
    } finally {
      setBusy(btn, false, "Save settings", "Saving…");
    }
  }

  async function clearDatabase() {
    if (
      !window.confirm(
        "Delete all players from the local database? This cannot be undone."
      )
    ) {
      return;
    }
    const btn = $("#btnClearDb");
    setBusy(btn, true, "Delete all players", "Deleting…");
    showAlert("");
    try {
      const res = await fetch("/api/db/players/clear", { method: "POST" });
      const data = await res.json();
      if (!data.ok) {
        showAlert(data.error || "Could not delete players.");
        return;
      }
      showAlert(
        `Deleted ${data.players_deleted ?? 0} players from the local database.`
      );
      await loadCount();
    } catch (err) {
      showAlert(String(err));
    } finally {
      setBusy(btn, false, "Delete all players", "Deleting…");
    }
  }

  bindTabs();
  $("#settingsForm")?.addEventListener("submit", saveSettings);
  $("#btnClearDb")?.addEventListener("click", clearDatabase);
  loadCount();
  loadSettings();
})();
