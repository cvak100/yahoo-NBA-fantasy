(() => {
  const $ = (sel) => document.querySelector(sel);
  const alertEl = $("#alert");
  const DEFAULT_NEWS_LIMIT = 10;
  let rosterNews = [];

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

  function renderNews(limit) {
    const list = $("#rosterNewsList");
    const empty = $("#rosterNewsEmpty");
    const meta = $("#rosterNewsMeta");
    const items = rosterNews || [];
    if (!items.length) {
      list.innerHTML = "";
      empty.hidden = false;
      meta.textContent = "";
      return;
    }
    empty.hidden = true;
    const shown = visibleNews(items, limit);
    meta.textContent = `${shown.length} of ${items.length}`;
    list.innerHTML = shown.map(newsListItem).join("");
    list.querySelectorAll("[data-open-player]").forEach((btn) => {
      btn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        const id = Number(btn.dataset.openPlayer);
        if (Number.isFinite(id) && window.PlayerDetail) window.PlayerDetail.open(id);
      });
    });
  }

  function slotRow(slot) {
    const player = slot.player;
    const pos = slot.position || "—";
    const group = slot.group || "starter";
    const groupLabel =
      group === "starter" ? "Active" : group === "bench" ? "Bench" : String(group).toUpperCase();

    if (!player) {
      return `
        <tr class="roster-empty-row">
          <td class="pos-col"><span class="roster-pos">${esc(pos)}</span></td>
          <td colspan="13"><span class="muted">Empty slot</span></td>
          <td><span class="slot-tag">${esc(groupLabel)}</span></td>
        </tr>`;
    }

    const dbId = player.db_id;
    const clickable = dbId ? " clickable" : "";
    const idAttr = dbId ? ` data-player-id="${dbId}"` : "";
    const name = player.full_name || player.name || "—";
    const team = player.nba_team || player.team || "";
    const elig = player.eligible_positions || player.positions || "";
    const sub = [team, elig].filter(Boolean).join(" · ");

    return `
      <tr class="${clickable.trim()}"${idAttr}>
        <td class="pos-col"><span class="roster-pos">${esc(pos)}</span></td>
        <td>
          <strong>${esc(name)}</strong>
          <div class="subline">${esc(sub) || "—"}</div>
        </td>
        <td class="num">${cell(player.yahoo_rank)}</td>
        <td class="num">${cell(player.owned_pct)}</td>
        <td class="num">${cell(player.gp)}</td>
        <td class="num">${cell(player.fg_pct)}</td>
        <td class="num">${cell(player.ft_pct)}</td>
        <td class="num">${cell(player.threes)}</td>
        <td class="num">${cell(player.pts)}</td>
        <td class="num">${cell(player.reb)}</td>
        <td class="num">${cell(player.ast)}</td>
        <td class="num">${cell(player.stl)}</td>
        <td class="num">${cell(player.blk)}</td>
        <td class="num">${cell(player.tov)}</td>
        <td><span class="slot-tag">${esc(groupLabel)}</span></td>
      </tr>`;
  }

  function renderRoster(data) {
    const team = data.team || {};
    const slots = data.slots || [];
    $("#rosterTeamName").textContent = team.name || "My Team";
    const bits = [];
    if (team.manager) bits.push(team.manager);
    bits.push(`${team.filled ?? 0}/${team.capacity ?? slots.length} filled`);
    if (team.synced_at) {
      try {
        bits.push(`Synced ${new Date(team.synced_at).toLocaleString("en-US")}`);
      } catch {
        /* ignore */
      }
    }
    $("#rosterSub").textContent = bits.join(" · ") || "—";

    const body = $("#rosterBody");
    if (!slots.length) {
      body.innerHTML = `<tr><td colspan="15">No roster data — run Refresh from Yahoo on the Dashboard.</td></tr>`;
    } else {
      body.innerHTML = slots.map(slotRow).join("");
      body.querySelectorAll("tr[data-player-id]").forEach((tr) => {
        tr.addEventListener("click", () => {
          const id = Number(tr.dataset.playerId);
          if (Number.isFinite(id) && window.PlayerDetail) window.PlayerDetail.open(id);
        });
      });
    }

    rosterNews = data.news || [];
    const limitSel = $("#rosterNewsLimit");
    const limit = limitSel?.value === "all" ? "all" : Number(limitSel?.value) || DEFAULT_NEWS_LIMIT;
    renderNews(limit);
  }

  async function loadRoster() {
    showAlert("");
    try {
      const res = await fetch("/api/roster");
      const data = await res.json();
      if (!data.ok) {
        showAlert(data.error || "Could not load roster.");
        return;
      }
      renderRoster(data);
    } catch (err) {
      showAlert(String(err));
    }
  }

  window.PlayerDetail?.init({
    onAlert: showAlert,
    hideDelete: true,
    onFavouriteChange: () => {},
  });

  $("#btnRefreshRoster")?.addEventListener("click", loadRoster);
  $("#rosterNewsLimit")?.addEventListener("change", () => {
    const v = $("#rosterNewsLimit").value;
    renderNews(v === "all" ? "all" : Number(v) || DEFAULT_NEWS_LIMIT);
  });

  loadRoster();
})();
