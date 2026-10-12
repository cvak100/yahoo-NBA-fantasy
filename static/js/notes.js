(() => {
  if (window.__notesInit) return;
  window.__notesInit = true;

  const $ = (sel, root = document) => root.querySelector(sel);
  const modal = $("#notesModal");
  if (!modal) return;

  // Keep modal out of header overflow clipping / flex width.
  if (modal.parentElement !== document.body) {
    document.body.appendChild(modal);
  }

  let notes = [];
  let options = { players: [], nba_teams: [], fantasy_teams: [] };
  let currentId = null;
  let draftLinks = [];

  function esc(s) {
    return String(s ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function stripHtml(html) {
    const tmp = document.createElement("div");
    tmp.innerHTML = String(html || "");
    return (tmp.textContent || tmp.innerText || "").replace(/\s+/g, " ").trim();
  }

  function looksLikeHtml(s) {
    return /<\/?[a-z][\s\S]*>/i.test(String(s || ""));
  }

  function bodyEditor() {
    return modal.querySelector("#noteBody");
  }

  function setBodyHtml(html) {
    const el = bodyEditor();
    if (!el) return;
    const raw = String(html || "");
    if (!raw) {
      el.innerHTML = "";
      return;
    }
    if (looksLikeHtml(raw)) {
      el.innerHTML = raw;
    } else {
      el.textContent = raw;
    }
  }

  function getBodyHtml() {
    const el = bodyEditor();
    if (!el) return "";
    const html = el.innerHTML.trim();
    if (!html || html === "<br>" || html === "<div><br></div>") return "";
    return html
      .replace(/<\/?(?!b|strong|i|em|u|ul|ol|li|div|p|br|span|font)([a-z][a-z0-9]*)\b[^>]*>/gi, "")
      .replace(/\son\w+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi, "");
  }

  function runFormat(cmd, value) {
    const el = bodyEditor();
    if (!el) return;
    el.focus();
    try {
      document.execCommand(cmd, false, value ?? null);
    } catch (_) {
      /* ignore unsupported */
    }
  }

  function openModal() {
    modal.hidden = false;
    document.body.classList.add("modal-open");
    loadAll();
  }

  function closeModal() {
    modal.hidden = true;
    document.body.classList.remove("modal-open");
  }

  function linkTypeLabel(t) {
    if (t === "player") return "Player";
    if (t === "nba_team") return "NBA";
    if (t === "fantasy_team") return "Fantasy";
    return t;
  }

  function renderList() {
    const list = $("#notesList");
    const empty = $("#notesEmpty");
    if (!notes.length) {
      list.innerHTML = "";
      empty.hidden = false;
      return;
    }
    empty.hidden = true;
    list.innerHTML = notes
      .map((n) => {
        const title = n.title || "Untitled";
        const preview = stripHtml(n.body || "").slice(0, 80);
        const active = n.id === currentId ? " active" : "";
        const chips = (n.links || [])
          .slice(0, 3)
          .map((l) => `<span class="notes-mini-chip">${esc(l.link_label)}</span>`)
          .join("");
        return `<button type="button" class="notes-list-item${active}" data-note-id="${n.id}">
          <strong>${esc(title)}</strong>
          <span class="subline">${esc(preview) || "—"}</span>
          <div class="notes-mini-chips">${chips}</div>
        </button>`;
      })
      .join("");
    list.querySelectorAll("[data-note-id]").forEach((btn) => {
      btn.addEventListener("click", () => selectNote(Number(btn.dataset.noteId)));
    });
  }

  function renderLinks() {
    const root = $("#noteLinks");
    if (!draftLinks.length) {
      root.innerHTML = `<span class="muted">No links.</span>`;
      return;
    }
    root.innerHTML = draftLinks
      .map(
        (l, i) => `<span class="notes-chip" data-link-idx="${i}">
          <em>${esc(linkTypeLabel(l.link_type))}</em> ${esc(l.link_label)}
          <button type="button" class="notes-chip-x" data-remove-link="${i}" aria-label="Remove">×</button>
        </span>`
      )
      .join("");
    root.querySelectorAll("[data-remove-link]").forEach((btn) => {
      btn.addEventListener("click", () => {
        draftLinks.splice(Number(btn.dataset.removeLink), 1);
        renderLinks();
      });
    });
  }

  function showEditor(show) {
    $("#notesEditor").hidden = !show;
    $("#notesEditorEmpty").hidden = show;
  }

  function selectNote(id) {
    const note = notes.find((n) => n.id === id);
    currentId = note ? note.id : null;
    if (!note) {
      showEditor(false);
      renderList();
      return;
    }
    showEditor(true);
    $("#noteTitle").value = note.title || "";
    setBodyHtml(note.body || "");
    draftLinks = (note.links || []).map((l) => ({ ...l }));
    $("#noteSaveMeta").textContent = note.updated_at
      ? `Updated ${new Date(note.updated_at).toLocaleString("en-US")}`
      : "";
    renderLinks();
    renderList();
  }

  function optionListForType(type) {
    if (type === "player") return options.players || [];
    if (type === "nba_team") return options.nba_teams || [];
    if (type === "fantasy_team") return options.fantasy_teams || [];
    return [];
  }

  function refreshSuggestions() {
    const type = $("#noteLinkType").value;
    const q = ($("#noteLinkQuery").value || "").trim().toLowerCase();
    const list = optionListForType(type)
      .filter((o) => !q || String(o.label).toLowerCase().includes(q) || String(o.id).toLowerCase().includes(q))
      .slice(0, 40);
    $("#noteLinkSuggestions").innerHTML = list
      .map((o) => `<option value="${esc(o.label)}" data-id="${esc(o.id)}"></option>`)
      .join("");
  }

  function addLinkFromInput() {
    const type = $("#noteLinkType").value;
    const raw = ($("#noteLinkQuery").value || "").trim();
    if (!raw) return;
    const opts = optionListForType(type);
    let match =
      opts.find((o) => String(o.label).toLowerCase() === raw.toLowerCase()) ||
      opts.find((o) => String(o.label).toLowerCase().includes(raw.toLowerCase()));
    if (!match && type === "nba_team") {
      match = { id: raw.toUpperCase(), label: raw.toUpperCase() };
    }
    if (!match) {
      alert("No match — pick a suggestion from the list.");
      return;
    }
    if (draftLinks.some((l) => l.link_type === type && String(l.link_id) === String(match.id))) {
      $("#noteLinkQuery").value = "";
      return;
    }
    draftLinks.push({
      link_type: type,
      link_id: String(match.id),
      link_label: match.label,
    });
    $("#noteLinkQuery").value = "";
    renderLinks();
  }

  async function loadAll() {
    try {
      const [notesRes, optRes] = await Promise.all([
        fetch("/api/notes"),
        fetch("/api/notes/link-options"),
      ]);
      const notesData = await notesRes.json();
      const optData = await optRes.json();
      if (notesData.ok) notes = notesData.notes || [];
      if (optData.ok) options = optData;
      if (currentId && !notes.some((n) => n.id === currentId)) {
        currentId = null;
        showEditor(false);
      }
      renderList();
      if (currentId && notes.some((n) => n.id === currentId)) {
        selectNote(currentId);
      } else if (!notes.length) {
        currentId = null;
        draftLinks = [];
        const titleEl = modal.querySelector("#noteTitle");
        if (titleEl) titleEl.value = "";
        setBodyHtml("");
        showEditor(true);
        renderLinks();
      } else {
        showEditor(false);
      }
      refreshSuggestions();
    } catch (err) {
      console.error(err);
    }
  }

  async function createNote() {
    try {
      const res = await fetch("/api/notes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: "", body: "", links: [] }),
      });
      const data = await res.json();
      if (!data.ok) {
        alert(data.error || "Could not create note.");
        return;
      }
      notes.unshift(data.note);
      selectNote(data.note.id);
      modal.querySelector("#noteTitle")?.focus();
    } catch (err) {
      alert(String(err));
    }
  }

  async function saveNote() {
    const titleEl = modal.querySelector("#noteTitle");
    const metaEl = modal.querySelector("#noteSaveMeta");
    const payload = {
      title: titleEl?.value || "",
      body: getBodyHtml(),
      links: draftLinks,
    };
    try {
      let data;
      if (!currentId) {
        const res = await fetch("/api/notes", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        data = await res.json();
        if (!data.ok) {
          alert(data.error || "Save failed.");
          return;
        }
        notes.unshift(data.note);
      } else {
        const res = await fetch(`/api/notes/${currentId}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        data = await res.json();
        if (!data.ok) {
          alert(data.error || "Save failed.");
          return;
        }
        const idx = notes.findIndex((n) => n.id === currentId);
        if (idx >= 0) notes[idx] = data.note;
        else notes.unshift(data.note);
      }
      selectNote(data.note.id);
      if (metaEl) metaEl.textContent = "Saved.";
    } catch (err) {
      alert(String(err));
    }
  }

  async function deleteNote() {
    if (!currentId) return;
    if (!confirm("Delete this note?")) return;
    const res = await fetch(`/api/notes/${currentId}`, { method: "DELETE" });
    const data = await res.json();
    if (!data.ok) {
      alert(data.error || "Delete failed.");
      return;
    }
    notes = notes.filter((n) => n.id !== currentId);
    currentId = null;
    showEditor(false);
    renderList();
  }

  $("#btnOpenNotes")?.addEventListener("click", openModal);
  modal.querySelectorAll("[data-close-notes]").forEach((el) => {
    el.addEventListener("click", closeModal);
  });
  $("#btnNewNote")?.addEventListener("click", createNote);
  $("#btnSaveNote")?.addEventListener("click", saveNote);
  $("#btnDeleteNote")?.addEventListener("click", deleteNote);
  $("#btnAddNoteLink")?.addEventListener("click", addLinkFromInput);
  $("#noteLinkType")?.addEventListener("change", () => {
    $("#noteLinkQuery").value = "";
    refreshSuggestions();
  });
  $("#noteLinkQuery")?.addEventListener("input", refreshSuggestions);
  $("#noteLinkQuery")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      addLinkFromInput();
    }
  });

  modal.querySelector(".notes-toolbar")?.addEventListener("mousedown", (e) => {
    // Keep selection in the editor when clicking toolbar buttons.
    if (e.target.closest(".notes-tool")) e.preventDefault();
  });
  modal.querySelector(".notes-toolbar")?.addEventListener("click", (e) => {
    const btn = e.target.closest(".notes-tool");
    if (!btn) return;
    const fontSize = btn.getAttribute("data-font-size");
    if (fontSize) {
      runFormat("fontSize", fontSize);
      return;
    }
    const cmd = btn.getAttribute("data-cmd");
    if (cmd) runFormat(cmd);
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !modal.hidden) closeModal();
  });
})();
