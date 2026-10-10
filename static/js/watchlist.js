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

  function playerRow(p) {
    return `
      <tr>
        <td class="num">${p.yahoo_rank ?? "—"}</td>
        <td>
          <strong>${esc(p.full_name)}</strong>
          <div class="subline">${esc(p.positions) || "—"} · ${esc(p.nba_team) || "—"}</div>
        </td>
        <td class="num">${cell(p.owned_pct)}</td>
        <td class="num">${cell(p.gp)}</td>
        <td class="num">${cell(p.fg_pct)}</td>
        <td class="num">${cell(p.ft_pct)}</td>
        <td class="num">${cell(p.threes)}</td>
        <td class="num">${cell(p.pts)}</td>
        <td class="num">${cell(p.reb)}</td>
        <td class="num">${cell(p.ast)}</td>
        <td class="num">${cell(p.stl)}</td>
        <td class="num">${cell(p.blk)}</td>
        <td class="num">${cell(p.tov)}</td>
        <td class="ta-end">
          <button type="button" class="btn tiny ghost fav-btn is-on" data-unfav="${p.id}" title="Remove from watchlist">★</button>
        </td>
      </tr>`;
  }

  function renderGroups(data) {
    const root = $("#watchGroups");
    const sub = $("#watchSub");
    const count = data.count || 0;
    sub.textContent = count
      ? `${count} favourite${count === 1 ? "" : "s"} · listed under each eligible position`
      : "No favourites yet — star players on the Players page.";

    const groups = (data.groups || []).filter((g) => (g.players || []).length);
    if (!groups.length) {
      root.innerHTML = `<section class="panel"><p class="muted">Watchlist is empty. Open Players, then star someone.</p></section>`;
      return;
    }

    root.innerHTML = groups
      .map((g) => {
        const rows = g.players.map(playerRow).join("");
        return `
          <section class="panel watch-pos-panel">
            <div class="panel-head">
              <div>
                <h2 class="watch-pos-title">${esc(g.position)}</h2>
                <p class="panel-sub">${g.count} player${g.count === 1 ? "" : "s"}</p>
              </div>
            </div>
            <div class="table-wrap">
              <table class="players-table wide-stats">
                <thead>
                  <tr>
                    <th class="num">Rank</th>
                    <th>Player</th>
                    <th class="num">Owned</th>
                    <th class="num">GP</th>
                    <th class="num">FG%</th>
                    <th class="num">FT%</th>
                    <th class="num">3PT</th>
                    <th class="num">PTS</th>
                    <th class="num">REB</th>
                    <th class="num">AST</th>
                    <th class="num">STL</th>
                    <th class="num">BLK</th>
                    <th class="num">TO</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>${rows}</tbody>
              </table>
            </div>
          </section>`;
      })
      .join("");

    root.querySelectorAll("[data-unfav]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const id = Number(btn.dataset.unfav);
        btn.disabled = true;
        try {
          const res = await fetch(`/api/db/players/${id}/favourite`, { method: "DELETE" });
          const data = await res.json();
          if (!data.ok) {
            showAlert(data.error || "Could not remove favourite.");
            btn.disabled = false;
            return;
          }
          await loadWatchlist();
        } catch (err) {
          showAlert(String(err));
          btn.disabled = false;
        }
      });
    });
  }

  async function loadWatchlist() {
    showAlert("");
    try {
      const res = await fetch("/api/db/watchlist");
      const data = await res.json();
      if (!data.ok) {
        showAlert(data.error || "Could not load watchlist.");
        return;
      }
      renderGroups(data);
    } catch (err) {
      showAlert(String(err));
    }
  }

  $("#btnRefreshWatchlist")?.addEventListener("click", loadWatchlist);
  $("#btnClearWatchlist")?.addEventListener("click", async () => {
    if (!window.confirm("Clear the entire watchlist?")) return;
    const btn = $("#btnClearWatchlist");
    if (btn) btn.disabled = true;
    showAlert("");
    try {
      const res = await fetch("/api/db/watchlist", { method: "DELETE" });
      const data = await res.json();
      if (!data.ok) {
        showAlert(data.error || "Could not clear watchlist.");
        return;
      }
      const n = data.cleared ?? 0;
      showAlert(n ? `Cleared ${n} favourite${n === 1 ? "" : "s"}.` : "Watchlist was already empty.");
      await loadWatchlist();
    } catch (err) {
      showAlert(String(err));
    } finally {
      if (btn) btn.disabled = false;
    }
  });
  loadWatchlist();
})();
