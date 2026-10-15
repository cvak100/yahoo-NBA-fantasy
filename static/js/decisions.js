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

  function pct(v) {
    if (v == null || Number.isNaN(Number(v))) return "—";
    return `${Math.round(Number(v) * 1000) / 10}%`;
  }

  function renderWeak(weak) {
    const chips = $("#weakChips");
    const sub = $("#weakSub");
    const rows = weak?.weak || [];
    const source = weak?.source || "none";
    sub.textContent = rows.length
      ? `${rows.length} focus cat${rows.length === 1 ? "" : "s"} · source: ${source.replaceAll("_", " ")}`
      : "No weak cats detected yet — run Dashboard Refresh after games start.";
    if (!rows.length) {
      chips.innerHTML = `<span class="muted">Nothing flagged. Streaming will use volume defaults (ST/BLK/3s/REB).</span>`;
      return;
    }
    chips.innerHTML = rows
      .map(
        (w) =>
          `<span class="weak-chip" title="${esc(w.source || "")}">${esc(w.label || w.key)}</span>`
      )
      .join("");
  }

  function renderStreaming(stream) {
    const body = $("#streamBody");
    const sub = $("#streamSub");
    const targets = stream?.targets || [];
    sub.textContent = stream?.note || "FA / waivers · games left × weak cats";
    if (stream?.week_label) {
      sub.textContent += ` · ${stream.week_label}`;
    }
    if (!targets.length) {
      body.innerHTML = `<tr><td colspan="5" class="muted">No streaming targets — need FA in DB and a schedule window.</td></tr>`;
      return;
    }
    body.innerHTML = targets
      .map((p, i) => {
        const fills = Object.keys(p.fill || {})
          .map((k) => k.replace("fg_pct", "FG%").replace("ft_pct", "FT%").replace("threes", "3PTM").replace("stl", "ST").replace("tov", "TO").replace("pts", "PTS").replace("reb", "REB").replace("ast", "AST").replace("blk", "BLK"))
          .join(", ");
        const name = p.db_id
          ? `<button type="button" class="linkish" data-open-player="${p.db_id}">${esc(p.name)}</button>`
          : esc(p.name);
        return `<tr>
          <td class="num">${i + 1}</td>
          <td>
            <strong>${name}</strong>
            <div class="subline">${esc(p.positions) || "—"} · ${esc(p.nba_team) || "—"} · ${esc(p.status) || "FA"}</div>
          </td>
          <td class="num">${p.games_left ?? "—"}</td>
          <td>${esc(fills) || "—"}</td>
          <td class="why-cell">${(p.why || []).map((x) => esc(x)).join(" · ")}</td>
        </tr>`;
      })
      .join("");
  }

  function verdictBadge(v) {
    const cls = v === "keep" ? "keep" : v === "cut" ? "cut" : "hold";
    return `<span class="verdict-badge ${cls}">${esc(String(v || "hold").toUpperCase())}</span>`;
  }

  function renderKeepCut(kc) {
    const body = $("#keepCutBody");
    const sub = $("#keepCutSub");
    const players = kc?.players || [];
    sub.textContent = kc?.note || "Bench + IL";
    if (!players.length) {
      body.innerHTML = `<tr><td colspan="5" class="muted">No BN/IL players on My Team yet.</td></tr>`;
      return;
    }
    body.innerHTML = players
      .map((p) => {
        const name = p.db_id
          ? `<button type="button" class="linkish" data-open-player="${p.db_id}">${esc(p.name)}</button>`
          : esc(p.name);
        return `<tr>
          <td>${verdictBadge(p.verdict)}</td>
          <td>
            <strong>${name}</strong>
            <div class="subline">${esc(p.positions) || "—"} · ${esc(p.nba_team) || "—"}</div>
          </td>
          <td>${esc(p.slot)} <span class="muted">(${esc(p.bucket)})</span></td>
          <td class="num">${p.yahoo_rank ?? "—"}</td>
          <td class="why-cell">${(p.reasons || []).map((x) => esc(x)).join(" · ")}</td>
        </tr>`;
      })
      .join("");
  }

  function renderDeficitTable(deficit) {
    const body = $("#deficitBody");
    const sub = $("#deficitSub");
    const cats = deficit?.categories || [];
    sub.textContent = deficit?.note
      || `${deficit?.weeks_decided || 0} decided week(s) · ${deficit?.weeks_tracked || 0} stored`;
    if (!cats.length) {
      body.innerHTML = `<tr><td colspan="6" class="muted">No category history yet.</td></tr>`;
      return;
    }
    body.innerHTML = cats
      .map((c) => {
        const pain = c.pain != null && c.decided ? " pain-row" : "";
        return `<tr class="${pain}" data-cat-key="${esc(c.key)}">
          <td><strong>${esc(c.label)}</strong></td>
          <td class="num">${c.wins}</td>
          <td class="num">${c.losses}</td>
          <td class="num">${c.ties}</td>
          <td class="num">${pct(c.win_pct)}</td>
          <td class="num">${c.avg_margin == null ? "—" : esc(c.avg_margin)}</td>
        </tr>`;
      })
      .join("");
  }

  function renderRadar(cats) {
    const el = $("#deficitRadar");
    const usable = (cats || []).filter((c) => c.radar != null);
    if (usable.length < 3) {
      el.innerHTML = `<p class="muted">Radar needs finished matchup weeks.</p>`;
      return;
    }
    const cx = 120;
    const cy = 120;
    const r = 88;
    const n = usable.length;
    const pts = usable.map((c, i) => {
      const ang = -Math.PI / 2 + (i * 2 * Math.PI) / n;
      const rr = (Math.max(0, Math.min(100, Number(c.radar))) / 100) * r;
      return [cx + rr * Math.cos(ang), cy + rr * Math.sin(ang)];
    });
    const poly = pts.map((p) => p.join(",")).join(" ");
    const rings = [0.25, 0.5, 0.75, 1]
      .map((f) => `<circle cx="${cx}" cy="${cy}" r="${r * f}" class="radar-ring" />`)
      .join("");
    const axes = usable
      .map((c, i) => {
        const ang = -Math.PI / 2 + (i * 2 * Math.PI) / n;
        const x = cx + r * Math.cos(ang);
        const y = cy + r * Math.sin(ang);
        const lx = cx + (r + 16) * Math.cos(ang);
        const ly = cy + (r + 16) * Math.sin(ang);
        return `<line x1="${cx}" y1="${cy}" x2="${x}" y2="${y}" class="radar-axis" />
          <text x="${lx}" y="${ly}" class="radar-label" text-anchor="middle" dominant-baseline="middle">${esc(c.label)}</text>`;
      })
      .join("");
    el.innerHTML = `<svg viewBox="0 0 240 240" width="240" height="240" role="img">
      ${rings}${axes}
      <polygon points="${poly}" class="radar-poly" />
      ${pts.map(([x, y]) => `<circle cx="${x}" cy="${y}" r="3.5" class="radar-dot" />`).join("")}
    </svg>`;
  }

  function renderBars(cats) {
    const el = $("#deficitBars");
    const rows = cats || [];
    if (!rows.length) {
      el.innerHTML = `<p class="muted">No data.</p>`;
      return;
    }
    const maxN = Math.max(1, ...rows.map((c) => (c.wins || 0) + (c.losses || 0) + (c.ties || 0)));
    el.innerHTML = rows
      .map((c) => {
        const w = c.wins || 0;
        const l = c.losses || 0;
        const t = c.ties || 0;
        const total = w + l + t || 1;
        return `<div class="def-bar-row">
          <div class="def-bar-label">${esc(c.label)}</div>
          <div class="def-bar-track" title="W ${w} · L ${l} · T ${t}">
            <span class="def-bar-w" style="width:${(w / maxN) * 100}%"></span>
            <span class="def-bar-l" style="width:${(l / maxN) * 100}%"></span>
            <span class="def-bar-t" style="width:${(t / maxN) * 100}%"></span>
          </div>
          <div class="def-bar-meta">${w}–${l}${t ? `–${t}` : ""}</div>
        </div>`;
      })
      .join("");
  }

  function renderWeekSpark(cats) {
    const wrap = $("#deficitWeekChart");
    const painful = (cats || []).find((c) => c.pain != null && (c.weekly || []).length);
    if (!painful || !(painful.weekly || []).length) {
      wrap.hidden = true;
      wrap.innerHTML = "";
      return;
    }
    const weeks = painful.weekly;
    const vals = weeks.map((w) => {
      if (w.result === "win") return 1;
      if (w.result === "loss") return -1;
      if (w.result === "tie") return 0;
      return null;
    });
    const w = 420;
    const h = 80;
    const step = weeks.length > 1 ? w / (weeks.length - 1) : w;
    const mid = h / 2;
    let d = "";
    weeks.forEach((wk, i) => {
      const v = vals[i];
      if (v == null) return;
      const x = i * step;
      const y = mid - v * (h * 0.35);
      d += d ? ` L ${x},${y}` : `M ${x},${y}`;
    });
    wrap.hidden = false;
    wrap.innerHTML = `
      <h3 class="section-label">Trend · ${esc(painful.label)} (most painful)</h3>
      <svg viewBox="0 0 ${w} ${h}" width="100%" height="${h}" class="week-spark">
        <line x1="0" y1="${mid}" x2="${w}" y2="${mid}" class="spark-mid" />
        <path d="${d}" class="spark-line" fill="none" />
      </svg>
      <p class="muted">+ win · 0 tie · − loss across stored weeks</p>
    `;
  }

  function bindPlayerLinks(root) {
    root.querySelectorAll("[data-open-player]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = Number(btn.dataset.openPlayer);
        if (Number.isFinite(id) && window.PlayerDetail) window.PlayerDetail.open(id);
      });
    });
  }

  const CAT_KEYS = [
    ["fg_pct", "FG%"],
    ["ft_pct", "FT%"],
    ["threes", "3PTM"],
    ["pts", "PTS"],
    ["reb", "REB"],
    ["ast", "AST"],
    ["stl", "ST"],
    ["blk", "BLK"],
    ["tov", "TO"],
  ];

  function contribRowsHtml(rows) {
    if (!rows.length) {
      return `<tr><td colspan="5" class="muted">No data.</td></tr>`;
    }
    return rows
      .map((p) => {
        const name = p.db_id
          ? `<button type="button" class="linkish" data-open-player="${p.db_id}">${esc(p.name)}</button>`
          : esc(p.name);
        const top = (p.categories || [])
          .filter((c) => c.z != null)
          .sort((a, b) => Math.abs(b.z) - Math.abs(a.z))
          .slice(0, 3)
          .map((c) => {
            const share =
              c.team_share != null ? ` · ${Math.round(c.team_share * 100)}%` : "";
            return `${esc(c.label)} ${c.z > 0 ? "+" : ""}${c.z}${share}`;
          })
          .join(" · ");
        return `<tr>
          <td>
            <strong>${name}</strong>
            <div class="subline">${esc(p.slot) || "—"} · ${esc(p.bucket) || p.group || ""} · ${esc(p.nba_team) || ""}</div>
          </td>
          <td class="num">${p.avg_z ?? "—"}</td>
          <td>${(p.saves || []).map(esc).join(", ") || "—"}</td>
          <td>${(p.hurts || []).map(esc).join(", ") || "—"}</td>
          <td class="why-cell">${top || "—"}</td>
        </tr>`;
      })
      .join("");
  }

  function renderNeed(targets) {
    const body = $("#needBody");
    const sub = $("#needSub");
    const risk = $("#needRisk");
    if (!body) return;
    const t = targets || {};
    if (sub) {
      sub.textContent = [
        t.note,
        t.week_label,
        t.my_games_left != null ? `your games left ${t.my_games_left}` : null,
        t.their_games_left != null ? `their games left ${t.their_games_left}` : null,
      ]
        .filter(Boolean)
        .join(" · ");
    }
    if (risk) {
      const at = t.at_risk || [];
      risk.innerHTML = at.length
        ? `<p class="edge-summary">At risk: <strong>${esc(at.join(", "))}</strong></p>`
        : `<p class="muted">${t.ok === false ? esc(t.note || "No live matchup.") : "Holding or waiting on live cats."}</p>`;
    }
    const rows = t.targets || [];
    body.innerHTML = rows.length
      ? rows
          .map((c) => {
            const cls =
              c.status === "winning"
                ? "edge-mine"
                : c.status === "losing"
                  ? "edge-theirs"
                  : "";
            const need =
              c.need_display != null
                ? `${c.lower_better ? "↓" : "+"}${esc(c.need_display)}`
                : "—";
            return `<tr class="${cls}">
              <td><strong>${esc(c.label)}</strong></td>
              <td class="num">${esc(c.mine_display)}</td>
              <td class="num">${esc(c.theirs_display)}</td>
              <td>${esc(c.status)}</td>
              <td class="num">${need}</td>
              <td class="why-cell">${esc(c.advice) || "—"}</td>
            </tr>`;
          })
          .join("")
      : `<tr><td colspan="6" class="muted">No weekly targets yet — sync matchup on Dashboard.</td></tr>`;
  }

  function renderContrib(contrib) {
    const sub = $("#contribSub");
    if (sub) sub.textContent = contrib?.note || "Player contribution";
    const body = $("#contribBody");
    const faBody = $("#contribFaBody");
    const faSub = $("#contribFaSub");
    if (body) body.innerHTML = contribRowsHtml(contrib?.roster || []);
    if (faBody) faBody.innerHTML = contribRowsHtml(contrib?.free_agents || []);
    if (faSub) {
      faSub.textContent = `Top ${(contrib?.free_agents || []).length} available by avg z · weak: ${(
        contrib?.weak_keys || []
      ).join(", ") || "—"}`;
    }
  }

  function ensureWeightInputs(weights) {
    const wrap = $("#faWeightRow");
    if (!wrap) return;
    if (wrap.dataset.ready === "1") {
      // sync values from server defaults if empty
      CAT_KEYS.forEach(([key]) => {
        const input = wrap.querySelector(`[data-w="${key}"]`);
        if (input && !input.value && weights && weights[key] != null) {
          input.value = String(weights[key]);
        }
      });
      return;
    }
    wrap.innerHTML = CAT_KEYS.map(
      ([key, lab]) => `<label class="fa-w">
        <span>${esc(lab)}</span>
        <input type="number" step="0.1" min="0" max="3" data-w="${key}" value="${
          weights && weights[key] != null ? esc(weights[key]) : ""
        }" placeholder="0">
      </label>`
    ).join("");
    wrap.dataset.ready = "1";
  }

  function renderFaCustom(fa) {
    const body = $("#faCustomBody");
    const sub = $("#faCustomSub");
    if (sub) {
      const w = fa?.weights || {};
      const bits = Object.entries(w)
        .map(([k, v]) => `${k}=${v}`)
        .join(", ");
      sub.textContent = fa?.note || "Custom FA ranking";
      if (bits) sub.textContent += ` · ${bits}`;
    }
    ensureWeightInputs(fa?.weights || {});
    const players = fa?.players || [];
    if (!body) return;
    if (!players.length) {
      body.innerHTML = `<tr><td colspan="5" class="muted">No FA matches for these weights.</td></tr>`;
      return;
    }
    body.innerHTML = players
      .map((p, i) => {
        const name = p.db_id
          ? `<button type="button" class="linkish" data-open-player="${p.db_id}">${esc(p.name)}</button>`
          : esc(p.name);
        const parts = Object.entries(p.parts || {})
          .map(([k, v]) => `${k}:${v}`)
          .join(" · ");
        return `<tr>
          <td class="num">${i + 1}</td>
          <td>
            <strong>${name}</strong>
            <div class="subline">${esc(p.positions) || "—"} · ${esc(p.nba_team) || "—"} · ${esc(p.status)}${
              p.pos_boost ? " · pos boost" : ""
            }</div>
          </td>
          <td class="num">${p.score ?? "—"}</td>
          <td class="num">${p.games_left ?? "—"}</td>
          <td class="why-cell">${esc(parts) || "—"}</td>
        </tr>`;
      })
      .join("");
  }

  function decisionsQuery() {
    const q = new URLSearchParams();
    const wrap = $("#faWeightRow");
    if (wrap) {
      wrap.querySelectorAll("[data-w]").forEach((input) => {
        const v = input.value.trim();
        if (v !== "" && Number(v) > 0) q.set(`w_${input.dataset.w}`, v);
      });
    }
    const pos = $("#faPosPrefer")?.value?.trim();
    if (pos) q.set("pos", pos);
    const s = q.toString();
    return s ? `?${s}` : "";
  }

  async function load() {
    showAlert("");
    const btn = $("#btnRefreshDecisions");
    if (btn) btn.disabled = true;
    try {
      const res = await fetch(`/api/decisions${decisionsQuery()}`);
      const data = await res.json();
      if (!data.ok) {
        showAlert(data.error || "Could not load decisions.");
        return;
      }
      renderWeak(data.weak);
      renderStreaming(data.streaming);
      renderKeepCut(data.keep_cut);
      const deficit = data.deficit || {};
      renderDeficitTable(deficit);
      renderRadar(deficit.categories || []);
      renderBars(deficit.categories || []);
      renderWeekSpark(deficit.categories || []);
      renderNeed(data.weekly_targets);
      renderContrib(data.contributions);
      renderFaCustom(data.fa_custom);
      bindPlayerLinks(document.querySelector(".decisions-layout"));
    } catch (err) {
      showAlert(String(err));
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  window.PlayerDetail?.init({ onAlert: showAlert, hideDelete: true });
  $("#btnRefreshDecisions")?.addEventListener("click", load);
  $("#btnFaCustomApply")?.addEventListener("click", load);
  load();
})();
