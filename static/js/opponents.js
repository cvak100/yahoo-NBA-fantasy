(() => {
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => [...document.querySelectorAll(sel)];
  const alertEl = $("#alert");
  let selectedId = null;
  let pollTimer = null;
  let lastData = null;

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
    return esc(v) || "—";
  }

  function edgeLabel(w) {
    if (w === "mine") return "You";
    if (w === "theirs") return "Them";
    if (w === "tie") return "Tie";
    return "—";
  }

  function setTab(name) {
    $$(".opp-tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
    $$(".opp-panel").forEach((p) => {
      p.hidden = p.dataset.panel !== name;
    });
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

  function renderForm(data) {
    const form = data.form || {};
    const rows = form.rows || [];
    $("#formSub").textContent = form.note || "Form comparison";
    const risks = form.risk_cats || [];
    $("#riskStrip").innerHTML = risks.length
      ? `<p class="edge-summary">Likely to lose: <strong>${esc(risks.join(", "))}</strong></p>`
      : `<p class="muted">No clear risk cats yet (need stats / recent matchups).</p>`;
    $("#formBody").innerHTML = rows.length
      ? rows
          .map((c) => {
            const cls =
              c.winner === "mine" ? "edge-mine" : c.winner === "theirs" ? "edge-theirs" : "";
            return `<tr class="${cls}">
              <td><strong>${esc(c.label)}</strong></td>
              <td class="num">${esc(c.mine_display)}</td>
              <td class="num">${esc(c.theirs_display)}</td>
              <td>${esc(edgeLabel(c.winner))}</td>
              <td class="muted">${esc((c.source || "").replaceAll("_", " "))}</td>
            </tr>`;
          })
          .join("")
      : `<tr><td colspan="5" class="muted">No form data.</td></tr>`;
  }

  function renderTargets(data) {
    const t = data.weekly_targets || {};
    $("#targetsSub").textContent = [
      t.note,
      t.week_label,
      t.my_games_left != null ? `your games left ${t.my_games_left}` : null,
      t.their_games_left != null ? `their games left ${t.their_games_left}` : null,
    ]
      .filter(Boolean)
      .join(" · ");
    const risk = t.at_risk || [];
    $("#targetsRisk").innerHTML = risk.length
      ? `<p class="edge-summary">At risk now: <strong>${esc(risk.join(", "))}</strong></p>`
      : `<p class="muted">${t.ok === false ? esc(t.note || "No live matchup.") : "Holding or unknown — check after games tip."}</p>`;
    const rows = t.targets || [];
    $("#targetsBody").innerHTML = rows.length
      ? rows
          .map((c) => {
            const cls =
              c.status === "winning"
                ? "edge-mine"
                : c.status === "losing"
                  ? "edge-theirs"
                  : "";
            return `<tr class="${cls}">
              <td><strong>${esc(c.label)}</strong></td>
              <td class="num">${esc(c.mine_display)}</td>
              <td class="num">${esc(c.theirs_display)}</td>
              <td>${esc(c.status)}</td>
              <td class="num">${c.need_display != null ? (c.lower_better ? "↓" : "+") + esc(c.need_display) : "—"}</td>
              <td class="why-cell">${esc(c.advice) || "—"}</td>
            </tr>`;
          })
          .join("")
      : `<tr><td colspan="6" class="muted">No targets yet.</td></tr>`;
  }

  function renderHistory(data) {
    const list = $("#historyList");
    const hist = data.history || [];
    $("#historySub").textContent = data.history_note || "Matchup history";
    if (!hist.length) {
      list.innerHTML = `<p class="muted">No stored matchups for this team yet.</p>`;
      return;
    }
    list.innerHTML = hist
      .slice()
      .reverse()
      .map((h) => {
        const score =
          h.their_cats_won != null && h.foe_cats_won != null
            ? `${h.their_cats_won}–${h.foe_cats_won}`
            : "—";
        const cats = (h.categories || [])
          .map(
            (c) =>
              `<span class="cat-mini"><b>${esc(c.stat)}</b> ${esc(c.theirs)}${
                c.mine != null ? ` · you ${esc(c.mine)}` : ""
              }</span>`
          )
          .join("");
        return `<article class="history-card">
          <header>
            <strong>Week ${esc(h.week)}</strong>
            <span class="hist-result hist-${esc((h.result || "").toLowerCase())}">${esc(h.result || "—")}</span>
            <span class="muted">vs ${esc(h.opponent_name) || "—"} · cats ${esc(score)}${
              h.vs_me ? " · vs you" : ""
            }</span>
          </header>
          <div class="cat-mini-row">${cats || `<span class="muted">No category breakdown</span>`}</div>
        </article>`;
      })
      .join("");
  }

  function renderRoster(data) {
    const opp = data.opponent || {};
    $("#oppSub").textContent = [
      opp.name || "—",
      opp.has_roster ? `roster ${opp.filled ?? 0}` : "no roster — Refresh roster",
      opp.synced_at ? `synced ${new Date(opp.synced_at).toLocaleString("en-US")}` : null,
    ]
      .filter(Boolean)
      .join(" · ");
    $("#oppRosterTitle").textContent = opp.name ? `${opp.name} · Roster` : "Full roster";
    $("#oppRosterSub").textContent = opp.has_roster
      ? `${opp.filled ?? 0} players`
      : "Refresh roster with Chrome debug open.";

    const keys = data.key_players || [];
    $("#keyPlayers").innerHTML = keys.length
      ? keys
          .map(
            (p) => `<div class="key-card">
              <strong>${esc(p.name)}</strong>
              <div class="subline">${esc(p.positions) || "—"} · ${esc(p.nba_team) || "—"} · ${esc(p.why)}</div>
            </div>`
          )
          .join("")
      : `<p class="muted">Refresh opponent roster first.</p>`;

    const inj = data.injuries || [];
    $("#oppInjuries").innerHTML = inj.length
      ? inj
          .map(
            (p) => `<div class="key-card injury">
              <strong>${esc(p.name)}</strong>
              <div class="subline">${esc(p.injury_status || p.injury) || "Injured"} · ${esc(p.nba_team) || ""}</div>
            </div>`
          )
          .join("")
      : `<p class="muted">No linked injuries on their roster.</p>`;

    const slots = opp.slots || [];
    $("#oppRosterBody").innerHTML = slots.length
      ? slots
          .map((s) => {
            const p = s.player;
            if (!p) {
              return `<tr><td class="pos-col">${esc(s.slot_position || "—")}</td><td colspan="11" class="muted">Empty</td></tr>`;
            }
            const name = p.db_id
              ? `<button type="button" class="linkish" data-open-player="${p.db_id}">${esc(p.name)}</button>`
              : esc(p.name);
            return `<tr>
              <td class="pos-col">${esc(s.slot_position || "—")}</td>
              <td><strong>${name}</strong><div class="subline">${esc(p.positions) || "—"} · ${esc(p.nba_team) || "—"}</div></td>
              <td class="num">${p.yahoo_rank ?? "—"}</td>
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
          .join("")
      : `<tr><td colspan="12" class="muted">No roster.</td></tr>`;

    $$("[data-open-player]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = Number(btn.dataset.openPlayer);
        if (Number.isFinite(id) && window.PlayerDetail) window.PlayerDetail.open(id);
      });
    });
  }

  function renderAll(data) {
    lastData = data;
    renderSelect(data);
    renderForm(data);
    renderTargets(data);
    renderHistory(data);
    renderRoster(data);
  }

  async function load() {
    showAlert("");
    const lastN = $("#formWindow")?.value || "3";
    const q = new URLSearchParams();
    if (selectedId) q.set("team_id", selectedId);
    q.set("last_n", lastN);
    try {
      const res = await fetch(`/api/opponents?${q}`);
      const data = await res.json();
      if (!data.ok) {
        showAlert(data.error || "Could not load opponents.");
        return;
      }
      renderAll(data);
    } catch (err) {
      showAlert(String(err));
    }
  }

  async function scrape() {
    if (!selectedId) {
      showAlert("Pick an opponent first.");
      return;
    }
    const btn = $("#btnScrapeOpp");
    const label = btn?.querySelector(".btn-label");
    if (btn) btn.disabled = true;
    if (label) label.textContent = "Scraping…";
    showAlert("");
    try {
      const res = await fetch(`/api/scouting/${selectedId}/refresh`, { method: "POST" });
      const data = await res.json();
      if (!data.ok) {
        showAlert(data.error || "Refresh failed.");
        return;
      }
      if (data.job_id) {
        $("#jobBar").hidden = false;
        $("#jobBarLabel").textContent = "Opponent roster";
        const poll = async () => {
          const pr = await fetch(`/api/jobs/${data.job_id}`);
          const pj = await pr.json();
          const job = pj.job || {};
          $("#jobBarText").textContent = job.message || job.status || "…";
          $("#jobBarMeta").textContent = job.status || "";
          if (job.status === "done" || job.status === "error" || job.status === "cancelled") {
            clearInterval(pollTimer);
            pollTimer = null;
            $("#jobBar").hidden = true;
            if (job.status === "done") await load();
            else showAlert(job.error || job.message || "Job failed.");
          }
        };
        pollTimer = setInterval(poll, 1500);
        await poll();
      } else {
        await load();
      }
    } catch (err) {
      showAlert(String(err));
    } finally {
      if (btn) btn.disabled = false;
      if (label) label.textContent = "Refresh roster";
    }
  }

  $$(".opp-tab").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));
  $("#opponentSelect")?.addEventListener("change", (e) => {
    selectedId = e.target.value;
    load();
  });
  $("#formWindow")?.addEventListener("change", load);
  $("#btnReloadOpp")?.addEventListener("click", load);
  $("#btnScrapeOpp")?.addEventListener("click", scrape);
  window.PlayerDetail?.init({ onAlert: showAlert, hideDelete: true });
  setTab("form");
  load();
})();
