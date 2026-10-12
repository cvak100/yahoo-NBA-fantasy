(() => {
  const $ = (sel) => document.querySelector(sel);
  const alertEl = $("#alert");
  const DEFAULT_NEWS_LIMIT = 10;
  let rosterNews = [];
  let lockoutWeek = null;
  let lockoutWeekCurrent = null;
  let lockoutWeekMax = 21;
  let lockoutLoading = false;

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

  function strengthClass(s) {
    if (s === "strong") return "is-strong";
    if (s === "weak") return "is-weak";
    if (s === "average") return "is-average";
    return "is-unknown";
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

  function syncLockoutNav(avail) {
    if (avail?.week != null) lockoutWeek = Number(avail.week);
    if (avail?.week_current != null) lockoutWeekCurrent = Number(avail.week_current);
    if (avail?.week_max != null) lockoutWeekMax = Number(avail.week_max);
    const prev = $("#btnLockoutPrev");
    const next = $("#btnLockoutNext");
    const nowBtn = $("#btnLockoutNow");
    const canPrev = avail?.can_prev != null ? !!avail.can_prev : (lockoutWeek || 1) > 1;
    const canNext =
      avail?.can_next != null
        ? !!avail.can_next
        : (lockoutWeek || 1) < lockoutWeekMax;
    if (prev) prev.disabled = lockoutLoading || !canPrev;
    if (next) next.disabled = lockoutLoading || !canNext;
    if (nowBtn) {
      nowBtn.disabled = lockoutLoading;
      const onCurrent =
        lockoutWeek != null &&
        lockoutWeekCurrent != null &&
        lockoutWeek === lockoutWeekCurrent;
      nowBtn.classList.toggle("is-on", onCurrent);
    }
  }

  function renderAvailability(avail) {
    const sub = $("#availSub");
    const week = $("#lockoutWeek");
    syncLockoutNav(avail);
    if (!avail) {
      sub.textContent = "No schedule data.";
      week.innerHTML = "";
      return;
    }
    const sum = avail.summary || {};
    const range =
      avail.window_start && avail.window_end
        ? `${avail.window_start.slice(5)} → ${avail.window_end.slice(5)}`
        : "";
    const bits = [
      avail.week_label || "Matchup",
      range,
      avail.is_all_star ? "All-Star (14 days)" : null,
      avail.is_playoff ? "Playoffs" : null,
      `${(avail.days || []).length || "—"} days`,
      `${avail.slot_cap ?? "—"} slots`,
      `${sum.days_with_overflow ?? 0} congested`,
    ].filter(Boolean);
    if (!avail.schedule_ok) bits.push("schedule partial");
    sub.textContent = bits.join(" · ");

    const days = avail.days || [];
    if (!days.length) {
      week.innerHTML = `<p class="muted">No games in this matchup week.</p>`;
      return;
    }

    week.style.setProperty("--lockout-days", String(days.length));
    week.classList.toggle("is-long-week", days.length > 7);
    week.innerHTML = days
      .map((d) => {
        const hot = d.has_overflow ? " is-lockout" : "";
        const today = d.is_today ? " is-today" : "";
        const names = (d.overflow || [])
          .map((p) => {
            const open = p.db_id ? ` data-player-id="${p.db_id}"` : "";
            const click = p.db_id ? " clickable" : "";
            return `<button type="button" class="roster-lockout-name${click}"${open}>${esc(p.name)}</button>`;
          })
          .join("");
        const body = d.has_overflow
          ? `<div class="roster-lockout-names">${names}</div>`
          : `<div class="roster-lockout-ok">All fit</div>`;
        return `<div class="roster-lockout-day${hot}${today}">
          <div class="roster-lockout-dayhead">
            <span class="roster-lockout-wd">${esc(d.weekday)}</span>
            <span class="roster-lockout-meta">${d.with_games ?? 0}/${d.slot_cap}</span>
          </div>
          ${
            d.has_overflow
              ? `<div class="roster-lockout-count">+${d.overflow_count} on BN</div>`
              : ""
          }
          ${body}
        </div>`;
      })
      .join("");

    week.querySelectorAll("[data-player-id]").forEach((el) => {
      el.addEventListener("click", (ev) => {
        ev.stopPropagation();
        const id = Number(el.dataset.playerId);
        if (Number.isFinite(id) && window.PlayerDetail) window.PlayerDetail.open(id);
      });
    });
  }

  async function loadLockoutWeek(weekNum) {
    if (lockoutLoading) return;
    const n = Number(weekNum);
    if (!Number.isFinite(n) || n < 1) return;
    lockoutLoading = true;
    syncLockoutNav({
      week: lockoutWeek,
      week_current: lockoutWeekCurrent,
      week_max: lockoutWeekMax,
      can_prev: n > 1,
      can_next: n < lockoutWeekMax,
    });
    const weekEl = $("#lockoutWeek");
    if (weekEl) weekEl.classList.add("is-loading");
    try {
      const res = await fetch(`/api/roster/lockouts?week=${n}`);
      const data = await res.json();
      if (!data.ok) {
        showAlert(data.error || "Could not load lockouts.");
        return;
      }
      renderAvailability(data.availability);
    } catch (err) {
      showAlert(String(err));
    } finally {
      lockoutLoading = false;
      if (weekEl) weekEl.classList.remove("is-loading");
      syncLockoutNav({
        week: lockoutWeek,
        week_current: lockoutWeekCurrent,
        week_max: lockoutWeekMax,
        can_prev: (lockoutWeek || 1) > 1,
        can_next: (lockoutWeek || 1) < lockoutWeekMax,
      });
    }
  }

  function renderCoverage(rows) {
    const grid = $("#coverageGrid");
    if (!rows?.length) {
      grid.innerHTML = `<p class="muted">No coverage data.</p>`;
      return;
    }
    grid.innerHTML = rows
      .map((r) => {
        const thin = r.thin ? " is-thin" : r.ok ? " is-ok" : "";
        return `<div class="roster-coverage-card${thin}">
          <div class="roster-coverage-slot">${esc(r.slot)}</div>
          <div class="roster-coverage-need">${r.required ? `${r.total}/${r.required}` : r.total} eligible</div>
          <div class="roster-coverage-split">
            <span title="Active"><b>${r.active}</b> Act</span>
            <span title="Bench"><b>${r.bench}</b> BN</span>
            <span title="IL"><b>${r.il}</b> IL</span>
          </div>
        </div>`;
      })
      .join("");
  }

  function renderContribution(contrib) {
    const chips = $("#strengthChips");
    const sub = $("#contribSub");
    if (!contrib) {
      chips.innerHTML = "";
      return;
    }
    const sum = contrib.summary || {};
    sub.textContent = `Active lineup vs league · strong ${sum.strong ?? 0} · avg ${sum.average ?? 0} · weak ${sum.weak ?? 0}`;
    chips.innerHTML = (contrib.strength || [])
      .map(
        (s) => `<div class="roster-strength-chip ${strengthClass(s.strength)}" title="You ${esc(s.my_avg_display)} · League ${esc(s.league_avg_display)}">
          <span class="roster-strength-label">${esc(s.label)}</span>
          <span class="roster-strength-badge">${esc(s.strength)}</span>
          <span class="subline">${esc(s.my_avg_display)}</span>
        </div>`
      )
      .join("");
  }

  function badgeHtml(badges) {
    if (!badges?.length) return "";
    return `<div class="roster-cat-badges">${badges
      .map(
        (b) =>
          `<span class="roster-cat-badge is-${esc(b.kind)}">${b.kind === "help" ? "+" : "−"}${esc(b.label)}</span>`
      )
      .join("")}</div>`;
  }

  function lockoutCell(player) {
    const days = player.overflow_weekdays || [];
    if (player.is_out) {
      return `<td class="roster-tonight is-out"><span class="roster-tonight-pill">Out</span><div class="subline">${esc(player.injury_status || "—")}</div></td>`;
    }
    if (!days.length) {
      const wk = player.week_games != null ? `${player.week_games}g wk` : "—";
      return `<td class="roster-tonight"><span class="roster-tonight-pill">OK</span><div class="subline">${esc(wk)}</div></td>`;
    }
    return `<td class="roster-tonight is-out"><span class="roster-tonight-pill">BN ${days.length}d</span><div class="subline">${esc(days.join(" · "))}</div></td>`;
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
          <td colspan="14"><span class="muted">Empty slot</span></td>
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
          ${badgeHtml(player.cat_badges)}
        </td>
        ${lockoutCell(player)}
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

    renderAvailability(data.availability);
    renderCoverage(data.coverage);
    renderContribution(data.contribution);

    const body = $("#rosterBody");
    if (!slots.length) {
      body.innerHTML = `<tr><td colspan="16">No roster data — run Refresh on the Dashboard.</td></tr>`;
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
  $("#btnLockoutPrev")?.addEventListener("click", () => {
    if (lockoutWeek == null) return;
    loadLockoutWeek(lockoutWeek - 1);
  });
  $("#btnLockoutNext")?.addEventListener("click", () => {
    if (lockoutWeek == null) return;
    loadLockoutWeek(lockoutWeek + 1);
  });
  $("#btnLockoutNow")?.addEventListener("click", () => {
    const target = lockoutWeekCurrent || 1;
    loadLockoutWeek(target);
  });

  loadRoster();
})();
