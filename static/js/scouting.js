(() => {
  const $ = (sel) => document.querySelector(sel);
  const alertEl = $("#alert");
  let selectedId = null;
  let pollTimer = null;

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

  function edgeLabel(winner) {
    if (winner === "mine") return "You";
    if (winner === "theirs") return "Them";
    if (winner === "tie") return "Tie";
    return "—";
  }

  function renderSelect(data) {
    const sel = $("#opponentSelect");
    const teams = data.teams || [];
    const current = data.selected_team_id || data.current_opponent_id || teams[0]?.team_id;
    selectedId = current ? String(current) : null;
    sel.innerHTML = teams
      .map((t) => {
        const label = `${t.name}${t.record ? ` (${t.record})` : ""}`;
        const selAttr = String(t.team_id) === String(selectedId) ? " selected" : "";
        return `<option value="${esc(t.team_id)}"${selAttr}>${esc(label)}</option>`;
      })
      .join("");
  }

  function renderCompare(data) {
    const edges = data.edges || [];
    const strip = $("#edgeStrip");
    if (!edges.length) {
      strip.innerHTML = `<p class="muted">No edges yet (missing stats or opponent roster).</p>`;
    } else {
      strip.innerHTML = `<p class="edge-summary">You can beat them in: <strong>${esc(edges.join(", "))}</strong> (${edges.length}/9)</p>
        <div class="edge-pills">${edges.map((e) => `<span class="pill accent">${esc(e)}</span>`).join("")}</div>`;
    }

    const opp = data.opponent || {};
    $("#scoutingSub").textContent = [
      opp.name || "—",
      opp.has_roster ? `roster ${opp.filled ?? 0}` : "no roster yet — click Refresh roster",
      opp.synced_at ? `synced ${new Date(opp.synced_at).toLocaleString("en-US")}` : null,
    ]
      .filter(Boolean)
      .join(" · ");

    $("#compareBody").innerHTML = (data.comparisons || [])
      .map((c) => {
        const cls =
          c.winner === "mine" ? "edge-mine" : c.winner === "theirs" ? "edge-theirs" : "";
        return `<tr class="${cls}">
          <td><strong>${esc(c.label)}</strong>${c.lower_better ? ' <span class="subline">↓ better</span>' : ""}</td>
          <td class="num">${esc(c.mine_display)}</td>
          <td class="num">${esc(c.theirs_display)}</td>
          <td>${esc(edgeLabel(c.winner))}</td>
        </tr>`;
      })
      .join("");
  }

  function renderRoster(data) {
    const opp = data.opponent || {};
    $("#oppRosterTitle").textContent = opp.name ? `${opp.name} · Roster` : "Their roster";
    $("#oppRosterSub").textContent = opp.has_roster
      ? `${opp.filled ?? 0} players`
      : "No saved roster yet — Refresh roster (Chrome debug).";

    const body = $("#oppRosterBody");
    const slots = opp.slots || [];
    if (!slots.length) {
      body.innerHTML = `<tr><td colspan="11">No data.</td></tr>`;
      return;
    }
    body.innerHTML = slots
      .map((s) => {
        const p = s.player;
        if (!p) {
          return `<tr class="roster-empty-row">
            <td class="pos-col"><span class="roster-pos">${esc(s.slot_position || "—")}</span></td>
            <td colspan="10"><span class="muted">Empty</span></td>
          </tr>`;
        }
        const click = p.db_id ? ` class="clickable" data-player-id="${p.db_id}"` : "";
        return `<tr${click}>
          <td class="pos-col"><span class="roster-pos">${esc(s.slot_position || "—")}</span></td>
          <td>
            <strong>${esc(p.name)}</strong>
            <div class="subline">${esc([p.nba_team, p.positions].filter(Boolean).join(" · "))}</div>
          </td>
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
      })
      .join("");

    body.querySelectorAll("tr[data-player-id]").forEach((tr) => {
      tr.addEventListener("click", () => {
        const id = Number(tr.dataset.playerId);
        if (Number.isFinite(id) && window.PlayerDetail) window.PlayerDetail.open(id);
      });
    });
  }

  async function load(teamId) {
    showAlert("");
    const q = teamId ? `?team_id=${encodeURIComponent(teamId)}` : "";
    try {
      const res = await fetch(`/api/scouting${q}`);
      const data = await res.json();
      if (!data.ok) {
        showAlert(data.error || "Could not load scouting.");
        return;
      }
      renderSelect(data);
      renderCompare(data);
      renderRoster(data);
    } catch (err) {
      showAlert(String(err));
    }
  }

  async function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }

  async function pollJob(jobId) {
    for (;;) {
      const res = await fetch(`/api/jobs/${jobId}`);
      const data = await res.json();
      if (!data.ok || !data.job) throw new Error(data.error || "Job lost.");
      const job = data.job;
      if (job.status === "done" || job.status === "cancelled") return job;
      if (job.status === "error") throw new Error(job.error || "Job failed.");
      await sleep(900);
    }
  }

  async function scrapeOpponent() {
    const tid = $("#opponentSelect")?.value || selectedId;
    if (!tid) {
      showAlert("Select an opponent.");
      return;
    }
    showAlert("Scraping Yahoo roster…");
    try {
      const res = await fetch(`/api/scouting/${encodeURIComponent(tid)}/refresh`, {
        method: "POST",
      });
      const data = await res.json();
      if (!data.ok) {
        showAlert(data.error || "Refresh failed.");
        return;
      }
      const jobId = data.job?.id || data.job_id;
      if (jobId) {
        const job = await pollJob(jobId);
        if (job.status === "cancelled") {
          showAlert("Cancelled.");
          return;
        }
        if (job.status === "error") {
          showAlert(job.error || "Failed.");
          return;
        }
      }
      await load(tid);
      showAlert("Opponent roster updated.");
    } catch (err) {
      showAlert(String(err));
    }
  }

  window.PlayerDetail?.init({
    onAlert: showAlert,
    hideDelete: true,
    onFavouriteChange: () => {},
  });

  $("#opponentSelect")?.addEventListener("change", () => {
    load($("#opponentSelect").value);
  });
  $("#btnRefreshScoutingPage")?.addEventListener("click", () => {
    load($("#opponentSelect")?.value || selectedId);
  });
  $("#btnScrapeOpponent")?.addEventListener("click", scrapeOpponent);

  load();
})();
