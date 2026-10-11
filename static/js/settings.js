(() => {
  const $ = (sel) => document.querySelector(sel);
  const alertEl = $("#alert");

  const STEP_KEYS = [
    "league",
    "roster",
    "news",
    "injuries",
    "trends",
    "available",
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

  async function loadCount() {
    const sub = $("#dbCountSub");
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

  async function loadRefreshSettings() {
    try {
      const res = await fetch("/api/app-settings");
      const data = await res.json();
      if (!data.ok) return;
      const s = data.settings || {};
      const count = $("#refreshAvailableCount");
      if (count) count.value = String(s.refresh_available_count ?? 100);
      writeRefreshSteps(s.refresh_steps);
      await populateTeams(s.my_team_id || "");
    } catch (err) {
      showAlert(String(err));
    }
  }

  async function saveRefreshSettings(e) {
    e?.preventDefault?.();
    const btn = $("#btnSaveRefreshSettings");
    setBusy(btn, true, "Save", "Saving…");
    showAlert("");
    try {
      const teamVal = $("#myTeamSelect")?.value || "";
      const refresh_steps = readRefreshSteps();
      const res = await fetch("/api/app-settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          refresh_available_count: Number($("#refreshAvailableCount")?.value || 100),
          refresh_steps,
          my_team_id: teamVal || null,
        }),
      });
      const data = await res.json();
      if (!data.ok) {
        showAlert(data.error || "Could not save settings.");
        return;
      }
      const s = data.settings || {};
      writeRefreshSteps(s.refresh_steps);
      const onCount = STEP_KEYS.filter(
        (k) => (s.refresh_steps || {})[k] !== false
      ).length;
      const teamLabel =
        $("#myTeamSelect")?.selectedOptions?.[0]?.textContent || "auto";
      const dashName = data.dashboard?.my_team?.name;
      showAlert(
        `Saved · team: ${dashName || teamLabel} · ${onCount}/6 steps · FA ${s.refresh_available_count}` +
          (dashName ? " · dashboard updated" : "")
      );
    } catch (err) {
      showAlert(String(err));
    } finally {
      setBusy(btn, false, "Save", "Saving…");
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

  $("#refreshSettingsForm")?.addEventListener("submit", saveRefreshSettings);
  $("#btnClearDb")?.addEventListener("click", clearDatabase);
  loadCount();
  loadRefreshSettings();
})();
