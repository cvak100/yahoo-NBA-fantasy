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

  function strengthClass(s) {
    if (s === "strong") return "is-strong";
    if (s === "weak") return "is-weak";
    if (s === "average") return "is-average";
    return "is-unknown";
  }

  function playerItem(p, { showSlot = false, mineBadge = false } = {}) {
    if (!p) return `<li class="analytics-player is-empty">—</li>`;
    const click = p.db_id ? ` data-player-id="${p.db_id}"` : "";
    const subBits = [];
    if (showSlot && p.slot_position) subBits.push(p.slot_position);
    if (p.nba_team) subBits.push(p.nba_team);
    if (mineBadge && p.on_my_roster) subBits.push("yours");
    return `<li class="analytics-player${p.db_id ? " clickable" : ""}${p.on_my_roster ? " is-mine" : ""}"${click}>
      <strong>${esc(p.name)}</strong>
      <span class="analytics-stat">${esc(p.display)}</span>
      <span class="subline">${esc(subBits.join(" · ")) || "—"}</span>
    </li>`;
  }

  function bindPlayerClicks(root) {
    root.querySelectorAll("[data-player-id]").forEach((el) => {
      el.addEventListener("click", () => {
        const id = Number(el.dataset.playerId);
        if (Number.isFinite(id) && window.PlayerDetail) window.PlayerDetail.open(id);
      });
    });
  }

  function render(data) {
    const team = data.team || {};
    const topN = data.top_n || 5;
    $("#analyticsTeamName").textContent = team.name
      ? `${team.name} · Category strength`
      : "Category strength";
    const sum = data.summary || {};
    $("#analyticsSub").textContent = [
      `${team.player_count ?? 0} roster players`,
      `pool ${data.pool_count ?? 0}`,
      `top ${topN} per cat`,
      `strong ${sum.strong ?? 0}`,
      `avg ${sum.average ?? 0}`,
      `weak ${sum.weak ?? 0}`,
    ].join(" · ");

    const strip = $("#strengthStrip");
    strip.innerHTML = (data.strength || [])
      .map((s) => {
        const tops = s.my_top || [];
        const rows = Array.from({ length: topN }, (_, i) =>
          playerItem(tops[i], { showSlot: true })
        ).join("");
        return `<div class="strength-card ${strengthClass(s.strength)}">
          <div class="strength-label">${esc(s.label)}</div>
          <div class="strength-badge">${esc(s.strength)}</div>
          <div class="strength-vals">
            <span>You ${esc(s.my_avg_display)}</span>
            <span>League ${esc(s.league_avg_display)}</span>
          </div>
          <ol class="strength-top-list">${rows}</ol>
        </div>`;
      })
      .join("");
    bindPlayerClicks(strip);

    const cols = $("#analyticsColumns");
    const columns = data.columns || [];
    if (!columns.length) {
      cols.innerHTML = `<p class="muted">No data — fill your roster and add players to the DB.</p>`;
      return;
    }
    cols.innerHTML = columns
      .map((c) => {
        const players = c.players || [];
        const rows = Array.from({ length: topN }, (_, i) =>
          playerItem(players[i], { mineBadge: true })
        ).join("");
        return `<div class="analytics-col ${strengthClass(c.strength)}">
          <header>
            <h3>${esc(c.label)}</h3>
            <span class="strength-badge">${esc(c.strength)}</span>
            <p class="subline">You ${esc(c.my_avg_display)} · League ${esc(c.league_avg_display)}</p>
          </header>
          <ol>${rows}</ol>
        </div>`;
      })
      .join("");
    bindPlayerClicks(cols);
  }

  async function load() {
    showAlert("");
    try {
      const res = await fetch("/api/analytics");
      const data = await res.json();
      if (!data.ok) {
        showAlert(data.error || "Could not load analytics.");
        return;
      }
      render(data);
      if (!(data.team?.player_count)) {
        showAlert("Roster is empty — run Refresh on the Dashboard first.");
      }
    } catch (err) {
      showAlert(String(err));
    }
  }

  window.PlayerDetail?.init({
    onAlert: showAlert,
    hideDelete: true,
    onFavouriteChange: () => {},
  });

  $("#btnRefreshAnalytics")?.addEventListener("click", load);
  load();
})();
