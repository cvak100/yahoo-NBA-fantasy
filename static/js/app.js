(() => {
  const $ = (sel) => document.querySelector(sel);

  const alertEl = $("#alert");
  const btnSync = $("#btnSync");
  const btnLabel = btnSync.querySelector(".btn-label");
  const btnSpinner = btnSync.querySelector(".btn-spinner");

  function showAlert(msg) {
    alertEl.hidden = !msg;
    alertEl.textContent = msg || "";
  }

  function setSyncing(on) {
    btnSync.disabled = on;
    btnLabel.textContent = on ? "Refreshing…" : "Refresh from Yahoo";
    btnSpinner.hidden = !on;
  }

  function initials(name) {
    if (!name) return "?";
    const parts = name.trim().split(/\s+/);
    return (parts[0][0] + (parts[1]?.[0] || parts[0][1] || "")).toUpperCase();
  }

  function fmtTime(iso) {
    if (!iso) return "not synced yet";
    try {
      return new Date(iso).toLocaleString("en-US");
    } catch {
      return iso;
    }
  }

  function renderRoster(myTeam) {
    const roster = $("#roster");
    const teamSub = $("#teamSub");
    const rosterMeta = $("#rosterMeta");
    const logo = $("#teamLogo");
    const initialsEl = $("#teamInitials");

    const name = myTeam?.name || "My Team";
    teamSub.textContent = myTeam?.manager
      ? `${name} · ${myTeam.manager}`
      : name;

    initialsEl.textContent = initials(name);
    if (myTeam?.logo) {
      logo.src = myTeam.logo;
      logo.alt = name;
      logo.hidden = false;
      initialsEl.hidden = true;
    } else {
      logo.hidden = true;
      initialsEl.hidden = false;
    }

    const slots = myTeam?.slots || [];
    const filled = myTeam?.filled ?? slots.filter((s) => s.player).length;
    rosterMeta.innerHTML = `
      <span class="pill accent">${filled}/${slots.length} filled</span>
      <span class="pill">${myTeam?.note ? "Pre-draft" : "Roster"}</span>
    `;

    if (!slots.length) {
      roster.innerHTML = `<div class="slot"><div class="pos">—</div><div class="slot-main"><p class="slot-title">No data</p><p class="slot-empty">Click Refresh from Yahoo</p></div></div>`;
      return;
    }

    roster.innerHTML = slots
      .map((slot) => {
        const group = slot.group || "starter";
        const player = slot.player;
        const title = player?.name || "Empty slot";
        const empty = player
          ? `${player.team || ""} · ${player.positions || ""}`.trim()
          : slot.label || slot.position;
        return `
          <article class="slot ${group}">
            <div class="pos">${slot.position}</div>
            <div class="slot-main">
              <p class="slot-title">${title}</p>
              <p class="slot-empty">${empty}</p>
            </div>
            <div class="slot-tag">${group === "starter" ? "Active" : group}</div>
          </article>
        `;
      })
      .join("");
  }

  function renderStandings(standings) {
    const body = $("#standingsBody");
    const sub = $("#standingsSub");
    const rows = standings?.rows || [];

    sub.textContent = standings?.pre_draft
      ? `Pre-draft · ${rows.length} teams`
      : `${rows.length} teams`;

    if (!rows.length) {
      body.innerHTML = `<tr><td colspan="4">No data — refresh from Yahoo.</td></tr>`;
      return;
    }

    body.innerHTML = rows
      .map((row) => {
        const logo = row.logo
          ? `<img src="${row.logo}" alt="">`
          : "";
        return `
          <tr class="${row.is_mine ? "mine" : ""}">
            <td><span class="rank">${row.rank}</span></td>
            <td>
              <div class="team-cell">${logo}<span>${row.name}</span></div>
            </td>
            <td>${row.manager || "—"}</td>
            <td>${row.record || "—"}</td>
          </tr>
        `;
      })
      .join("");
  }

  const SCORING_STATS = new Set([
    "FG%", "FT%", "3PTM", "PTS", "REB", "AST", "ST", "BLK", "TO",
  ]);

  function renderMatchups(matchups) {
    const sub = $("#matchupSub");
    const myBox = $("#myMatchup");
    const catGrid = $("#catGrid");
    const list = $("#matchupList");

    const week = matchups?.week;
    const rows = matchups?.matchups || [];
    const mine = matchups?.my_matchup || {};

    sub.textContent = week
      ? `Week ${week} · ${rows.length} games`
      : rows.length
        ? `${rows.length} games`
        : "No data";

    const home = mine.home || {};
    const away = mine.away || {};
    const hs = home.categories_won ?? "—";
    const as_ = away.categories_won ?? "—";

    const myRow = rows.find((r) => r.is_mine);
    const homeMgr = myRow?.home?.manager || "";
    const awayMgr = myRow?.away?.manager || "";

    if (home.name || away.name) {
      myBox.innerHTML = `
        <div class="match-side">
          <p class="name">${home.name || "—"}</p>
          <p class="mgr">${homeMgr || " "}</p>
        </div>
        <div class="match-score">
          <div class="nums">${hs} – ${as_}</div>
          <div class="vs">CATS</div>
        </div>
        <div class="match-side away">
          <p class="name">${away.name || "—"}</p>
          <p class="mgr">${awayMgr || " "}</p>
        </div>
      `;
    } else {
      myBox.innerHTML = `<div class="match-side"><p class="name">No matchup</p><p class="mgr">Refresh from Yahoo</p></div>`;
    }

    const cats = (mine.categories || []).filter((c) => SCORING_STATS.has(c.stat));
    catGrid.innerHTML = cats
      .map((c) => {
        const cls =
          c.winner === "home" ? "home-win" : c.winner === "away" ? "away-win" : "";
        return `
          <div class="cat-pill ${cls}">
            <div class="stat">${c.stat}</div>
            <div class="vals">${c.home} · ${c.away}</div>
          </div>
        `;
      })
      .join("");

    if (!rows.length) {
      list.innerHTML = `<div class="match-row"><span class="side">No matchups</span></div>`;
      return;
    }

    list.innerHTML = rows
      .map((m) => {
        const score =
          m.score && (m.score.home != null || m.score.away != null)
            ? `${m.score.home ?? 0}–${m.score.away ?? 0}`
            : "vs";
        return `
          <div class="match-row ${m.is_mine ? "mine" : ""}">
            <span class="side">${m.home?.name || "—"}</span>
            <span class="mid">${score}</span>
            <span class="side right">${m.away?.name || "—"}</span>
          </div>
        `;
      })
      .join("");
  }

  const RULE_KEYS = [
    "Draft Type",
    "Draft Time",
    "Scoring Type",
    "Max Teams",
    "Live Draft Pick Time",
    "Max Acquisitions per Week",
    "Waiver Type",
    "Waiver Time",
    "Trade End Date",
    "Trade Review",
    "Playoffs",
    "Playoff Tie-Breaker",
    "Playoff Reseeding",
    "Weekly Deadline",
    "Roster Positions",
  ];

  function renderRules(rules) {
    const chips = $("#statChips");
    const grid = $("#rulesGrid");
    const settings = rules?.settings || {};
    const cats = rules?.stat_categories || [];

    chips.innerHTML = cats
      .map((c) => {
        const short = c.match(/\(([^)]+)\)/);
        return `<span class="chip">${short ? short[1] : c}</span>`;
      })
      .join("");

    const items = RULE_KEYS.filter((k) => settings[k]).map(
      (k) => `
        <div>
          <dt>${k}</dt>
          <dd>${settings[k]}</dd>
        </div>
      `
    );
    grid.innerHTML =
      items.join("") ||
      "<div><dt>Rules</dt><dd>Not synced yet</dd></div>";
  }

  function renderDashboard(data) {
    const meta = data.meta || {};
    const rules = data.rules || {};
    const standings = data.standings || {};
    const myTeam = data.my_team || {};
    const matchups = data.matchups || {};

    $("#leagueName").textContent =
      meta.league_name || rules.league_name || "Snopcministri";

    const bits = [];
    if (meta.draft_time) bits.push(`Draft: ${meta.draft_time}`);
    if (meta.week) bits.push(`Week ${meta.week}`);
    if (meta.scoring_type) bits.push(meta.scoring_type);
    bits.push(`Synced: ${fmtTime(meta.synced_at || myTeam.synced_at)}`);
    $("#leagueMeta").textContent = bits.join(" · ");

    renderRoster(myTeam);
    renderStandings(standings);
    renderMatchups(matchups);
    renderRules(rules);
  }

  async function loadDashboard() {
    const res = await fetch("/api/dashboard");
    const data = await res.json();
    renderDashboard(data);
    if (!data.has_data) {
      showAlert(
        "No saved data yet. Open Chrome with start_chrome_debug.bat, then click Refresh from Yahoo."
      );
    } else {
      showAlert("");
    }
  }

  async function syncNow() {
    setSyncing(true);
    showAlert("");
    try {
      const res = await fetch("/api/sync", { method: "POST" });
      const data = await res.json();
      if (!data.ok) {
        showAlert(data.error || "Sync failed.");
        return;
      }
      renderDashboard(data);
      showAlert("");
    } catch (err) {
      showAlert(String(err));
    } finally {
      setSyncing(false);
    }
  }

  btnSync.addEventListener("click", syncNow);
  loadDashboard();
})();
