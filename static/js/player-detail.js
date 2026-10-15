(() => {
  const $ = (sel) => document.querySelector(sel);

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

  let currentDetailId = null;
  let currentGameLogs = [];
  let currentPlayerName = "";
  let currentNews = [];
  let currentNewsLimit = "5";
  const DEFAULT_NEWS_LIMIT = 5;
  let opts = {
    onAlert: () => {},
    onFavouriteChange: null,
    onDelete: null,
    hideDelete: false,
  };

  function leagueId() {
    return (
      $("#btnYahooPlayer")?.dataset?.leagueId ||
      document.body?.dataset?.leagueId ||
      document.querySelector("[data-league-id]")?.dataset?.leagueId ||
      ""
    );
  }

  function yahooPlayerSearchUrl(name) {
    const q = encodeURIComponent(String(name || "").trim());
    const lid = leagueId();
    if (!lid || !q) return "";
    return `https://basketball.fantasysports.yahoo.com/nba/${lid}/playersearch?search=${q}`;
  }

  function setYahooPlayerLink(name) {
    const a = $("#btnYahooPlayer");
    if (!a) return;
    const href = yahooPlayerSearchUrl(name);
    if (!href) {
      a.hidden = true;
      a.removeAttribute("href");
      return;
    }
    a.href = href;
    a.hidden = false;
  }

  function setFavButton(btn, on) {
    if (!btn) return;
    btn.classList.toggle("is-on", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.textContent = on ? "★ Watchlist" : "☆ Watchlist";
    btn.title = on ? "Remove from watchlist" : "Add to watchlist";
  }

  function renderSeasonTable(rows, { showGp }) {
    if (!rows.length) return `<p class="muted">No data yet — use Refresh.</p>`;
    const head = `
      <thead><tr>
        <th class="tal">Season</th>
        ${showGp ? "<th>GP</th>" : ""}
        <th>Rank</th>
        <th>FG%</th>
        <th>FT%</th>
        <th>3PT</th>
        <th>PTS</th>
        <th>REB</th>
        <th>AST</th>
        <th>STL</th>
        <th>BLK</th>
        <th>TO</th>
      </tr></thead>`;
    const body = rows
      .map(
        (s) => `
      <tr>
        <td class="tal"><span class="stat-year">${esc(s.season)}</span></td>
        ${showGp ? `<td>${cell(s.games_played)}</td>` : ""}
        <td>${cell(s.season_rank)}</td>
        <td>${cell(s.fg_pct)}</td>
        <td>${cell(s.ft_pct)}</td>
        <td>${cell(s.threes)}</td>
        <td>${cell(s.pts)}</td>
        <td>${cell(s.reb)}</td>
        <td>${cell(s.ast)}</td>
        <td>${cell(s.stl)}</td>
        <td>${cell(s.blk)}</td>
        <td>${cell(s.tov)}</td>
      </tr>`
      )
      .join("");
    return `<div class="table-wrap"><table class="stat-table">${head}<tbody>${body}</tbody></table></div>`;
  }

  function renderDepthTable(rows) {
    if (!rows.length) return `<p class="muted">No depth chart yet.</p>`;
    const body = rows
      .map(
        (d) => `
      <tr>
        <td><span class="depth-rank">#${esc(d.depth_rank)}</span></td>
        <td>${esc(d.depth_player_name)}</td>
        <td>${esc(d.position) || "—"}</td>
        <td>${d.yahoo_rank != null ? esc(d.yahoo_rank) : "—"}</td>
      </tr>`
      )
      .join("");
    return `<div class="table-wrap"><table class="stat-table depth-table">
      <thead><tr><th class="tal">#</th><th class="tal">Player</th><th>Pos</th><th>Rank</th></tr></thead>
      <tbody>${body}</tbody>
    </table></div>`;
  }

  function renderGameLogTable(rows, { previewLimit = null } = {}) {
    if (!rows.length) return `<p class="muted">No game log yet.</p>`;
    const shown = previewLimit != null ? rows.slice(0, previewLimit) : rows;
    const head = `
      <thead><tr>
        <th class="tal">Date</th>
        <th class="tal">Opp</th>
        <th>MIN</th>
        <th>FG</th>
        <th>FT</th>
        <th>3PT</th>
        <th>PTS</th>
        <th>REB</th>
        <th>AST</th>
        <th>STL</th>
        <th>BLK</th>
        <th>TO</th>
      </tr></thead>`;
    const body = shown
      .map((g) => {
        const matchup = `${g.is_home ? "vs" : "@"} ${esc(g.opponent || "—")}`;
        return `
        <tr>
          <td class="tal"><span class="stat-date">${esc(g.game_date || "—")}</span></td>
          <td class="tal"><span class="stat-opp">${matchup}</span></td>
          <td>${cell(g.minutes)}</td>
          <td>${cell(g.fgm_a || g.fg_pct)}</td>
          <td>${cell(g.ftm_a || g.ft_pct)}</td>
          <td>${cell(g.threes)}</td>
          <td>${cell(g.pts)}</td>
          <td>${cell(g.reb)}</td>
          <td>${cell(g.ast)}</td>
          <td>${cell(g.stl)}</td>
          <td>${cell(g.blk)}</td>
          <td>${cell(g.tov)}</td>
        </tr>`;
      })
      .join("");
    return `<table class="stat-table">${head}<tbody>${body}</tbody></table>`;
  }

  function visibleNews(items, limit) {
    if (limit === "all") return items;
    const n = Number(limit);
    if (!Number.isFinite(n) || n <= 0) return items.slice(0, DEFAULT_NEWS_LIMIT);
    return items.slice(0, n);
  }

  function renderNewsCards(items, limit = currentNewsLimit) {
    if (!items.length) return `<p class="muted">No news yet.</p>`;
    return visibleNews(items, limit)
      .map((n) => {
        const body = n.body || "";
        return `<article class="news-card">
          <h4>${esc(n.title || "Update")}</h4>
          <div class="subline">${esc(n.published_at || n.published_iso || "—")}${n.source ? ` · ${esc(n.source)}` : ""}</div>
          ${body ? `<p>${esc(body)}</p>` : ""}
        </article>`;
      })
      .join("");
  }

  function paintDetailNews() {
    const items = currentNews || [];
    const wrap = $("#detailNewsLimitWrap");
    const meta = $("#detailNewsMeta");
    const sel = $("#detailNewsLimit");
    const root = $("#detailNews");
    if (!root) return;

    if (!items.length) {
      if (wrap) wrap.hidden = true;
      if (meta) {
        meta.hidden = true;
        meta.textContent = "";
      }
      root.innerHTML = `<p class="muted">No news yet.</p>`;
      return;
    }

    if (wrap) wrap.hidden = false;
    if (sel && sel.value !== String(currentNewsLimit)) {
      sel.value = currentNewsLimit === "all" ? "all" : String(currentNewsLimit);
    }
    const shown = visibleNews(items, currentNewsLimit);
    if (meta) {
      meta.hidden = false;
      meta.textContent = `${shown.length} of ${items.length}`;
    }
    root.innerHTML = renderNewsCards(items, currentNewsLimit);
  }

  function openGameLogHistory(e) {
    if (e) {
      e.preventDefault();
      e.stopPropagation();
    }
    if (!currentGameLogs.length) return;
    const modal = $("#gameLogModal");
    if (!modal) return;
    $("#gameLogTitle").textContent = `${currentPlayerName || "Player"} · Game log`;
    $("#gameLogMeta").textContent = `${currentGameLogs.length} game${currentGameLogs.length === 1 ? "" : "s"}`;
    $("#gameLogFull").innerHTML = renderGameLogTable(currentGameLogs);
    modal.hidden = false;
    modal.removeAttribute("hidden");
    document.body.classList.add("modal-open");
  }

  function closeGameLogHistory(e) {
    if (e) {
      e.preventDefault();
      e.stopPropagation();
    }
    const modal = $("#gameLogModal");
    if (modal) {
      modal.hidden = true;
      modal.setAttribute("hidden", "");
    }
    if ($("#detailModal")?.hidden) document.body.classList.remove("modal-open");
  }

  function closeDetail() {
    currentDetailId = null;
    currentGameLogs = [];
    currentPlayerName = "";
    setYahooPlayerLink("");
    const modal = $("#detailModal");
    if (modal) modal.hidden = true;
    const gl = $("#gameLogModal");
    if (gl) gl.hidden = true;
    document.body.classList.remove("modal-open");
  }

  async function openDetail(playerId) {
    const modal = $("#detailModal");
    if (!modal) return;
    currentDetailId = playerId;
    modal.hidden = false;
    document.body.classList.add("modal-open");
    $("#detailName").textContent = "Loading…";
    $("#detailMeta").textContent = "";
    setYahooPlayerLink("");
    $("#detailSeasonTotal").innerHTML = "Loading…";
    $("#detailSeasonAvg").innerHTML = "Loading…";
    $("#detailDepth").innerHTML = "Loading…";
    $("#detailGames").innerHTML = "Loading…";
    $("#detailNews").innerHTML = "Loading…";
    const btnFull = $("#btnOpenGameLog");
    if (btnFull) btnFull.hidden = true;

    try {
      const res = await fetch(`/api/db/players/${playerId}`);
      const data = await res.json();
      if (!data.ok) {
        opts.onAlert(data.error || "Player not found.");
        closeDetail();
        return;
      }
      const p = data.player;
      currentPlayerName = p.full_name || "";
      $("#detailName").textContent = p.full_name;
      $("#detailMeta").textContent = [
        p.nba_team,
        p.positions,
        p.yahoo_rank != null ? `Rank ${p.yahoo_rank}` : null,
        p.owned_pct ? `${p.owned_pct} owned` : null,
      ]
        .filter(Boolean)
        .join(" · ");
      setYahooPlayerLink(p.full_name);
      setFavButton($("#btnFavPlayer"), !!p.is_favourite);

      const seasonAll = data.season || [];
      const seasonTotals = seasonAll
        .filter((s) => s.record_type === "season_total")
        .sort((a, b) => String(b.season).localeCompare(String(a.season)));
      const seasonAvgs = seasonAll
        .filter((s) => s.record_type === "season_avg")
        .sort((a, b) => String(b.season).localeCompare(String(a.season)));

      $("#detailSeasonTotal").innerHTML = renderSeasonTable(seasonTotals, { showGp: true });
      $("#detailSeasonAvg").innerHTML = renderSeasonTable(seasonAvgs, { showGp: false });
      $("#detailDepth").innerHTML = renderDepthTable(data.depth || []);

      currentGameLogs = data.game_logs || [];
      const previewLimit = 5;
      if (!currentGameLogs.length) {
        $("#detailGames").innerHTML = `<p class="muted">No game log yet.</p>`;
        if (btnFull) btnFull.hidden = true;
      } else {
        const tableHtml = renderGameLogTable(currentGameLogs, { previewLimit });
        const more =
          currentGameLogs.length > previewLimit
            ? `<div class="stat-preview-foot">Last ${previewLimit} of ${currentGameLogs.length} — click for full history</div>`
            : `<div class="stat-preview-foot">Click for full history (${currentGameLogs.length})</div>`;
        $("#detailGames").innerHTML = `<div class="stat-preview" id="gameLogPreview" role="button" tabindex="0" title="Open full game log"><div class="table-wrap">${tableHtml}</div>${more}</div>`;
        if (btnFull) btnFull.hidden = false;
      }

      currentNews = data.news || [];
      currentNewsLimit = "5";
      paintDetailNews();
    } catch (err) {
      opts.onAlert(String(err));
      closeDetail();
    }
  }

  async function toggleFavourite() {
    if (currentDetailId == null) return;
    const btn = $("#btnFavPlayer");
    const on = btn?.classList.contains("is-on") || btn?.getAttribute("aria-pressed") === "true";
    const method = on ? "DELETE" : "POST";
    if (btn) btn.disabled = true;
    try {
      const res = await fetch(`/api/db/players/${currentDetailId}/favourite`, { method });
      const data = await res.json();
      if (!data.ok) {
        opts.onAlert(data.error || "Could not update watchlist.");
        return;
      }
      const next = !!data.is_favourite;
      setFavButton(btn, next);
      if (typeof opts.onFavouriteChange === "function") {
        opts.onFavouriteChange(currentDetailId, next);
      }
    } catch (err) {
      opts.onAlert(String(err));
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function init(options = {}) {
    opts = { ...opts, ...options };
    const delBtn = $("#btnDeletePlayer");
    if (delBtn) {
      delBtn.hidden = !!opts.hideDelete;
      delBtn.addEventListener("click", () => {
        if (currentDetailId != null && typeof opts.onDelete === "function") {
          opts.onDelete(currentDetailId);
        }
      });
    }
    $("#btnFavPlayer")?.addEventListener("click", toggleFavourite);
    $("#detailNewsLimit")?.addEventListener("change", (e) => {
      const val = e.target.value;
      currentNewsLimit = val === "all" ? "all" : String(Number(val) || DEFAULT_NEWS_LIMIT);
      paintDetailNews();
    });
    document.querySelectorAll("[data-close-modal]").forEach((el) => {
      el.addEventListener("click", closeDetail);
    });

    document.querySelectorAll("[data-close-gamelog]").forEach((el) => {
      el.addEventListener("click", closeGameLogHistory);
    });
    $("#btnOpenGameLog")?.addEventListener("click", openGameLogHistory);
    $("#detailGames")?.addEventListener("click", (e) => {
      if (e.target.closest("#gameLogPreview, .stat-preview")) openGameLogHistory(e);
    });
    $("#detailGames")?.addEventListener("keydown", (e) => {
      if ((e.key === "Enter" || e.key === " ") && e.target.closest("#gameLogPreview")) {
        openGameLogHistory(e);
      }
    });
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      if ($("#gameLogModal") && !$("#gameLogModal").hidden) closeGameLogHistory();
      else if ($("#detailModal") && !$("#detailModal").hidden) closeDetail();
    });
  }

  window.PlayerDetail = {
    init,
    open: openDetail,
    close: closeDetail,
    getCurrentId: () => currentDetailId,
    setFavouriteButton: (on) => setFavButton($("#btnFavPlayer"), on),
  };
})();
