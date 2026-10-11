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

  function teamLogoHtml(g) {
    const src = g.logo || window.NbaTeams?.resolve(g.team)?.logo || "";
    if (!src) return "";
    return `<img class="nba-team-logo" src="${esc(src)}" alt="" width="40" height="40" loading="lazy" decoding="async">`;
  }

  function playerRow(p) {
    return `
      <tr class="clickable" data-player-id="${p.id}">
        <td class="num">${p.yahoo_rank ?? "—"}</td>
        <td>
          <strong>${esc(p.full_name)}</strong>
          <div class="subline">${esc(p.positions) || "—"}</div>
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
      </tr>`;
  }

  const DEFAULT_NEWS_LIMIT = 10;
  let teamsData = null;

  function newsListItem(item) {
    const name = item.matched_name || item.player_name || "Unknown";
    const when = item.published_at || item.published_iso || "—";
    const title = item.title || "";
    const body = item.body || "";
    const meta = [when, item.source || null].filter(Boolean).join(" · ");
    const nameHtml = item.player_id
      ? `<button type="button" class="news-player-link" data-open-player="${item.player_id}">${esc(name)}</button>`
      : `<strong>${esc(name)}</strong>`;
    return `<li class="team-news-item${item.player_id ? " is-linked" : ""}">
      <div class="team-news-item-top">
        <span class="team-news-player">${nameHtml}</span>
      </div>
      ${title ? `<p class="team-news-title">${esc(title)}</p>` : ""}
      <div class="subline">${esc(meta)}</div>
      ${body ? `<p class="team-news-body">${esc(body)}</p>` : ""}
    </li>`;
  }

  function visibleNews(items, limit) {
    if (limit === "all") return items;
    const n = Number(limit);
    if (!Number.isFinite(n) || n <= 0) return items.slice(0, DEFAULT_NEWS_LIMIT);
    return items.slice(0, n);
  }

  function teamNewsBlock(g, limit = DEFAULT_NEWS_LIMIT) {
    const items = g.news || [];
    const teamKey = esc(g.team);
    if (!items.length) {
      return `<div class="team-news-block" data-team-news="${teamKey}">
        <div class="team-news-head">
          <h3 class="team-section-title">News</h3>
        </div>
        <p class="muted">No news for this team yet.</p>
      </div>`;
    }
    const shown = visibleNews(items, limit);
    const limitVal = limit === "all" ? "all" : String(limit);
    return `<div class="team-news-block" data-team-news="${teamKey}">
      <div class="team-news-head">
        <h3 class="team-section-title">News <span class="panel-sub">${shown.length} of ${items.length}</span></h3>
        <label class="show-limit" for="team-news-limit-${teamKey}">
          <span>Show</span>
          <select class="team-news-limit" id="team-news-limit-${teamKey}" data-team="${teamKey}" aria-label="News limit">
            <option value="10"${limitVal === "10" ? " selected" : ""}>10</option>
            <option value="all"${limitVal === "all" ? " selected" : ""}>All</option>
          </select>
        </label>
      </div>
      <ul class="team-news-list">${shown.map(newsListItem).join("")}</ul>
    </div>`;
  }

  function rebindNewsControls(root) {
    root.querySelectorAll(".team-news-limit").forEach((sel) => {
      sel.addEventListener("change", () => {
        const team = sel.dataset.team;
        const group = (teamsData?.groups || []).find((g) => g.team === team);
        const block = root.querySelector(`[data-team-news="${CSS.escape(team)}"]`);
        if (!group || !block) return;
        const limit = sel.value === "all" ? "all" : Number(sel.value) || DEFAULT_NEWS_LIMIT;
        block.outerHTML = teamNewsBlock(group, limit);
        const panel = root.querySelector(`.team-accordion[data-team="${CSS.escape(team)}"]`);
        if (panel) {
          bindPlayerClicks(panel);
          rebindNewsControls(panel);
        }
      });
    });
  }

  function bindPlayerClicks(root) {
    root.querySelectorAll("tr[data-player-id]").forEach((tr) => {
      tr.addEventListener("click", () => {
        const id = Number(tr.dataset.playerId);
        if (Number.isFinite(id) && window.PlayerDetail) window.PlayerDetail.open(id);
      });
    });
    root.querySelectorAll("[data-open-player]").forEach((btn) => {
      btn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        const id = Number(btn.dataset.openPlayer);
        if (Number.isFinite(id) && window.PlayerDetail) window.PlayerDetail.open(id);
      });
    });
  }

  function renderTeams(data) {
    const root = $("#teamGroups");
    const sub = $("#teamsSub");
    const playerCount = data.player_count || 0;
    const teamCount = data.team_count || 0;
    sub.textContent = playerCount
      ? `${playerCount} players across ${teamCount} team${teamCount === 1 ? "" : "s"}`
      : "No players in the database yet.";

    const groups = data.groups || [];
    if (!groups.length) {
      root.innerHTML = `<section class="panel"><p class="muted">Add players on the Players page first.</p></section>`;
      return;
    }

    root.innerHTML = groups
      .map((g) => {
        const rows = (g.players || []).map(playerRow).join("");
        const teamId = esc(g.team).replace(/\s+/g, "-");
        const title = g.display_name || g.team;
        const logo = teamLogoHtml(g);
        return `
          <section class="panel team-accordion" data-team="${esc(g.team)}">
            <button type="button" class="team-accordion-toggle" aria-expanded="false" aria-controls="team-body-${teamId}">
              <div class="team-accordion-label">
                <div class="team-accordion-head">
                  ${logo}
                  <div>
                    <h2 class="team-title">${esc(title)}</h2>
                    <p class="panel-sub">${esc(g.abbr || g.team)} · ${g.count} player${g.count === 1 ? "" : "s"} · ${g.news_count || 0} news</p>
                  </div>
                </div>
              </div>
              <span class="team-accordion-chevron" aria-hidden="true">▾</span>
            </button>
            <div class="team-accordion-body" id="team-body-${teamId}" hidden>
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
                    </tr>
                  </thead>
                  <tbody>${rows}</tbody>
                </table>
              </div>
              ${teamNewsBlock(g)}
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

    bindPlayerClicks(root);
    rebindNewsControls(root);
  }

  async function loadTeams() {
    showAlert("");
    try {
      const res = await fetch("/api/db/teams");
      const data = await res.json();
      if (!data.ok) {
        showAlert(data.error || "Could not load teams.");
        return;
      }
      teamsData = data;
      renderTeams(data);
    } catch (err) {
      showAlert(String(err));
    }
  }

  window.PlayerDetail?.init({
    onAlert: showAlert,
    hideDelete: true,
    onFavouriteChange: () => {},
  });

  $("#btnRefreshTeams")?.addEventListener("click", loadTeams);
  loadTeams();
})();
