(() => {
  const $ = (sel) => document.querySelector(sel);
  const alertEl = $("#alert");

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

  $("#btnClearDb").addEventListener("click", clearDatabase);
  loadCount();
})();
