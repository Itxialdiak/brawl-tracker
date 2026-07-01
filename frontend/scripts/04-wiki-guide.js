/* BrawlSensei · 04-wiki-guide.js
   Guía de Estrategia (wiki: árbol, nodos, editor, reordenar, revisión).
   Se carga como <script src> desde index.html, en orden. El JS de cliente
   SIEMPRE es visible en el navegador: aquí no van secretos. */
/* ============================ WIKI / GUÍA DE ESTRATEGIA ============================ */
let wikiTree = [], wikiActiveId = null, wikiIsAdmin = false, reorderMode = false;
let weMode = null, weEditId = null;       // editor de nodos
let weTrLang = null;                        // idioma destino al traducir
let wikiViewOverride = null;                // versión forzada al ver ('orig' | código de idioma | null)
let justifyCtx = null;                     // contexto para borrar/reordenar

/* Idioma activo de la app y su nombre visible (para el sistema de traducción de la wiki). */
function wLang() { return (typeof currentLang === "function" ? currentLang() : "es"); }
function langLabel(code) {
  const L = (window.I18N_LANGS || []).find((l) => l.code === code);
  return L ? L.label : (code || "").toUpperCase();
}

async function apiSend(url, method, body) {
  const r = await fetch(url, { method, headers: { "Content-Type": "application/json" },
    body: body !== undefined ? JSON.stringify(body) : undefined });
  const d = await r.json().catch(() => ({}));
  return { ok: r.ok, status: r.status, d };
}
function wikiToast(msg, kind) {
  let t = document.getElementById("wiki-toast");
  if (!t) { t = document.createElement("div"); t.id = "wiki-toast"; document.body.appendChild(t); }
  t.textContent = msg; t.className = "show " + (kind || "");
  clearTimeout(t._t); t._t = setTimeout(() => { t.className = ""; }, 3400);
}
// Envuelve las tablas en un contenedor con scroll horizontal (para móvil)
function wrapTables(html) {
  return (html || "").replace(/<table/g, '<div class="table-scroll"><table').replace(/<\/table>/g, "</table></div>");
}

async function loadWikiTree(keepActive) {
  try {
    const d = await getJSON("/api/wiki/tree?lang=" + encodeURIComponent(wLang()));
    wikiTree = d.tree || []; wikiIsAdmin = !!d.is_admin;
    buildWikiNav();
    updatePendingBadge(d.pending || 0);
    if (!keepActive && wikiActiveId == null) {
      const first = wikiTree.find((n) => n.type === "section");
      if (first) loadWikiNode(first.id);
    }
  } catch (e) { $("wiki-nav-list").innerHTML = '<div class="wk-loading">No se pudo cargar.</div>'; }
}

function buildWikiNav() {
  const wrap = $("wiki-nav-list");
  if (!wikiTree.length) { wrap.innerHTML = '<div class="wk-loading">Aún no hay contenido.</div>'; return; }
  let h = "";
  wikiTree.forEach((n) => {
    if (n.type === "separator") {
      h += `<div class="wk-sep wk-top" data-id="${n.id}" data-type="separator"><span class="wk-grip">⠿</span>${esc(n.title)}</div>`;
    } else {
      h += `<div class="wk-group wk-top" data-id="${n.id}">
        <div class="wk-item ${n.id === wikiActiveId ? "active" : ""}" data-id="${n.id}" data-type="section" onclick="onNavClick(event,${n.id})">
          <span class="wk-grip">⠿</span><span class="wk-num">${n.number}</span><span class="wk-label">${esc(n.title)}</span></div>
        <div class="wk-subs" data-parent="${n.id}">`;
      (n.subs || []).forEach((s) => {
        h += `<div class="wk-item wk-sub ${s.id === wikiActiveId ? "active" : ""}" data-id="${s.id}" data-type="subsection" onclick="onNavClick(event,${s.id})">
          <span class="wk-grip">⠿</span><span class="wk-num">${s.number}</span><span class="wk-label">${esc(s.title)}</span></div>`;
      });
      h += `</div></div>`;
    }
  });
  wrap.innerHTML = h;
  if (reorderMode) enableDnD();
}

function onNavClick(e, id) { if (reorderMode) return; loadWikiNode(id); }
/* Cambia la versión mostrada del artículo actual (automática / original / una traducción). */
function wikiViewFrom(val) { wikiViewOverride = val || null; loadWikiNode(wikiActiveId, true); }

function findNavMeta(id) {
  for (const n of wikiTree) {
    if (n.id === id) return { number: n.number, type: n.type, title: n.title };
    for (const s of (n.subs || [])) if (s.id === id) return { number: s.number, type: "subsection", title: s.title };
  }
  return null;
}

async function loadWikiNode(id, keepView) {
  if (!keepView) wikiViewOverride = null;   // al cambiar de artículo, versión automática
  wikiActiveId = id; buildWikiNav();
  const cont = $("wiki-content");
  try {
    let url = "/api/wiki/node/" + id + "?lang=" + encodeURIComponent(wLang());
    if (wikiViewOverride) url += "&view=" + encodeURIComponent(wikiViewOverride);
    const n = await getJSON(url);
    renderWikiNode(n);
  } catch (e) { cont.innerHTML = '<div class="wiki-welcome"><p>No se pudo cargar el apartado.</p></div>'; }
}

function renderWikiNode(n) {
  const meta = findNavMeta(n.id);
  const eyebrow = n.type === "separator" ? "Separador"
    : (n.type === "subsection" ? "Subsección " + (meta ? meta.number : "")
      : "Apartado " + (meta ? meta.number : ""));
  const origLang = n.orig_lang || "es";
  const avail = n.available_langs || [];
  // Barra de traducción SIEMPRE encima del bloque: mensaje de idioma original + selector de versión.
  const cur = wikiViewOverride || "";
  const optSel = (v) => (cur === v ? " selected" : "");
  const opts = [`<option value=""${optSel("")}>Automático (tu idioma)</option>`,
    `<option value="orig"${optSel("orig")}>Original — ${esc(langLabel(origLang))}</option>`]
    .concat(avail.map((l) => `<option value="${esc(l)}"${optSel(l)}>${esc(langLabel(l))}</option>`)).join("");
  const msg = n.is_translation
    ? `Estás viendo una traducción (<b>${esc(langLabel(n.shown_lang))}</b>). Artículo original en <b>${esc(langLabel(origLang))}</b>.`
    : (avail.length ? `Artículo original en <b>${esc(langLabel(origLang))}</b>. Hay traducciones disponibles.`
      : `Artículo original en <b>${esc(langLabel(origLang))}</b>.`);
  const trBar = `<div class="wk-tr-bar">
     <span class="wk-tr-msg">🌐 ${msg}</span>
     <label class="wk-tr-pick">Versión: <select class="wk-tr-sel" onchange="wikiViewFrom(this.value)">${opts}</select></label>
   </div>`;
  $("wiki-content").innerHTML =
    `${trBar}
     <div class="wc-head">
       <div><div class="wc-eyebrow">${esc(eyebrow)}</div><div class="wc-title">${esc(n.title)}</div></div>
       <div class="wc-tools">
         <button class="wc-tr" onclick="openTranslateNode(${n.id})">🌐 Traducir</button>
         <button class="wc-edit" onclick="openEditNode(${n.id})">✎ Editar</button>
         <button class="wc-del" onclick="openDeleteNode(${n.id})">🗑 Eliminar</button>
       </div>
     </div>
     <div class="wc-divider"></div>
     <div class="wiki-body">${wrapTables(n.body) || '<p style="color:var(--muted)">(Sin contenido todavía. Pulsa «Editar» para añadirlo.)</p>'}</div>`;
}

/* ---------- Editor de nodos (editar / crear) ---------- */
function fillParentSelect(selected) {
  const sel = $("we-parent"); sel.innerHTML = "";
  wikiTree.filter((n) => n.type === "section").forEach((n) => {
    const o = document.createElement("option");
    o.value = n.id; o.textContent = n.number + ". " + n.title;
    if (selected && n.id === selected) o.selected = true;
    sel.appendChild(o);
  });
}
function openEditNode(id) {
  // Editar SIEMPRE el contenido original (?view=orig), no una traducción.
  getJSON("/api/wiki/node/" + id + "?view=orig").then((n) => {
    weMode = "edit"; weEditId = id;
    $("we-title").textContent = "Editar: " + n.title;
    $("we-sub").textContent = "Tus cambios se enviarán a revisión y se publicarán cuando un administrador los apruebe.";
    $("we-source-row").style.display = "none";
    $("we-trlang-row").style.display = "none";
    $("we-parent-row").style.display = "none";
    $("we-name").value = n.title;
    const isSep = n.type === "separator";
    $("we-body-row").style.display = isSep ? "none" : "";
    $("we-body").innerHTML = isSep ? "" : (n.body || "");
    $("we-summary").value = ""; $("we-justify").value = ""; $("we-err").textContent = "";
    $("wiki-edit-modal").classList.add("open"); $("we-name").focus();
  });
}
function openCreateNode(type) {
  weMode = "create_" + type; weEditId = null;
  const titles = { section: "Nueva sección", subsection: "Nueva subsección", separator: "Nuevo separador" };
  $("we-title").textContent = titles[type];
  const subs = { section: "Se añadirá como última sección del índice. Pasa por revisión.",
                 subsection: "Indica a qué sección pertenece. Pasa por revisión.",
                 separator: "Un separador organiza un bloque temático. Pasa por revisión." };
  $("we-sub").textContent = subs[type];
  $("we-source-row").style.display = "none";
  $("we-trlang-row").style.display = "none";
  $("we-parent-row").style.display = type === "subsection" ? "" : "none";
  if (type === "subsection") fillParentSelect(wikiActiveId);
  $("we-body-row").style.display = type === "separator" ? "none" : "";
  $("we-name").value = ""; $("we-body").innerHTML = "";
  $("we-summary").value = ""; $("we-justify").value = ""; $("we-err").textContent = "";
  $("wiki-edit-modal").classList.add("open"); $("we-name").focus();
}
function closeWikiEdit() { $("wiki-edit-modal").classList.remove("open"); }

/* ---------- Traducir un artículo (a cualquier idioma; pasa por revisión) ---------- */
let weTrSrc = null;   // nodo original (referencia) cacheado mientras se traduce
async function openTranslateNode(id) {
  let src;
  try {
    src = await getJSON("/api/wiki/node/" + id + "?view=orig");
  } catch (e) { wikiToast("No se pudo cargar el artículo.", "err"); return; }
  weTrSrc = src; weMode = "translate"; weEditId = id;
  const orig = src.orig_lang || "es";
  // idiomas destino = todos menos el original; por defecto el idioma activo (si no es el original) o inglés.
  const langs = (window.I18N_LANGS || []).filter((l) => l.code !== orig);
  const target = (wLang() !== orig ? wLang() : "en");
  $("we-tr-lang").innerHTML = langs.map((l) =>
    `<option value="${l.code}"${l.code === target ? " selected" : ""}>${esc(l.label)}${l.soon ? " (próximamente)" : ""}</option>`).join("");
  $("we-sub").textContent = "La traducción se enviará a revisión y se publicará cuando un administrador la apruebe.";
  $("we-trlang-row").style.display = "";
  $("we-source-row").style.display = "";
  $("we-source-title").textContent = src.title;
  $("we-source-body").innerHTML = wrapTables(src.body) || '<p style="color:var(--muted)">(sin contenido)</p>';
  $("we-parent-row").style.display = "none";
  $("we-body-row").style.display = src.type === "separator" ? "none" : "";
  $("we-justify").value = ""; $("we-err").textContent = "";
  await loadTrTarget(target);
  $("wiki-edit-modal").classList.add("open"); $("we-name").focus();
}
/* Carga en los campos la traducción existente del idioma elegido (o vacío para empezar). */
async function loadTrTarget(lang) {
  weTrLang = lang;
  let cur = null;
  try {
    if (weTrSrc && (weTrSrc.available_langs || []).includes(lang))
      cur = await getJSON("/api/wiki/node/" + weEditId + "?view=" + encodeURIComponent(lang));
  } catch (e) { /* sin traducción previa */ }
  $("we-title").textContent = "Traducir a " + langLabel(lang) + ": " + (weTrSrc ? weTrSrc.title : "");
  $("we-name").value = cur ? cur.title : "";
  $("we-body").innerHTML = cur ? (cur.body || "") : "";
  $("we-summary").value = cur ? ("Actualizo la traducción " + lang.toUpperCase()) : ("Traduzco a " + langLabel(lang));
}
function weTrLangChanged() { if (weMode === "translate") loadTrTarget($("we-tr-lang").value); }

async function submitWikiEdit() {
  const err = $("we-err"); err.textContent = "";
  const title = $("we-name").value.trim();
  const summary = $("we-summary").value.trim();
  const justification = $("we-justify").value.trim();
  if (!title) { err.textContent = "Ponle un título."; return; }
  if (!summary) { err.textContent = "Describe brevemente el cambio."; return; }
  if (!justification) { err.textContent = "Justifica el cambio."; return; }
  let kind, node_id = null, data = {};
  if (weMode === "edit") { kind = "edit"; node_id = weEditId; data = { title, body: $("we-body").innerHTML }; }
  else if (weMode === "create_section") { kind = "create_section"; data = { title, body: $("we-body").innerHTML }; }
  else if (weMode === "create_subsection") {
    kind = "create_subsection";
    data = { title, body: $("we-body").innerHTML, parent_id: parseInt($("we-parent").value, 10) };
  } else if (weMode === "create_separator") { kind = "create_separator"; data = { title }; }
  else if (weMode === "translate") { kind = "translate"; node_id = weEditId; data = { lang: $("we-tr-lang").value, title, body: $("we-body").innerHTML }; }
  const btn = $("we-save"); btn.disabled = true;
  const { ok, d } = await apiSend("/api/wiki/proposals", "POST", { kind, node_id, data, summary, justification });
  btn.disabled = false;
  if (!ok) { err.textContent = d.error || d.detail || "No se pudo enviar."; return; }
  closeWikiEdit();
  wikiToast("Cambio enviado a revisión ✓", "ok");
  if (wikiIsAdmin) loadWikiTree(true);
}

/* ---------- Borrar (con justificación) ---------- */
function openDeleteNode(id) {
  const meta = findNavMeta(id);
  justifyCtx = { kind: "delete", node_id: id };
  $("wj-title").textContent = "Eliminar apartado";
  $("wj-sub").textContent = "Propones eliminar «" + (meta ? meta.title : "este apartado") + "»"
    + (meta && meta.type === "section" ? " y todas sus subsecciones." : ".") + " Pasa por revisión.";
  $("wj-summary").value = ""; $("wj-justify").value = ""; $("wj-err").textContent = "";
  $("wiki-justify-modal").classList.add("open"); $("wj-summary").focus();
}
function closeJustify() { $("wiki-justify-modal").classList.remove("open"); }
async function submitJustify() {
  const err = $("wj-err"); err.textContent = "";
  const summary = $("wj-summary").value.trim(), justification = $("wj-justify").value.trim();
  if (!summary) { err.textContent = "Describe brevemente el cambio."; return; }
  if (!justification) { err.textContent = "Justifica el cambio."; return; }
  if (!justifyCtx) return;
  const body = { kind: justifyCtx.kind, summary, justification, data: justifyCtx.payload || {} };
  if (justifyCtx.node_id) body.node_id = justifyCtx.node_id;
  const { ok, d } = await apiSend("/api/wiki/proposals", "POST", body);
  if (!ok) { err.textContent = d.error || d.detail || "No se pudo enviar."; return; }
  closeJustify();
  if (justifyCtx.kind === "reorder") cancelReorder();
  justifyCtx = null;
  wikiToast("Cambio enviado a revisión ✓", "ok");
  if (wikiIsAdmin) loadWikiTree(true);
}

/* ---------- Reordenar (drag & drop, pasa por revisión) ---------- */
function toggleReorder() { reorderMode ? cancelReorder() : startReorder(); }
function startReorder() {
  reorderMode = true;
  $("wiki-nav").classList.add("reorder");
  $("wiki-reorder-btn").classList.add("on");
  $("wiki-reorder-bar").style.display = "flex";
  buildWikiNav();
}
function cancelReorder() {
  reorderMode = false;
  $("wiki-nav").classList.remove("reorder");
  $("wiki-reorder-btn").classList.remove("on");
  $("wiki-reorder-bar").style.display = "none";
  loadWikiTree(true);
}
let dragEl = null;
function enableDnD() {
  const nav = $("wiki-nav-list");
  nav.querySelectorAll(":scope > .wk-top").forEach((el) => {
    el.setAttribute("draggable", "true");
    el.addEventListener("dragstart", (e) => { if (e.target.closest(".wk-sub")) return; dragEl = el; el.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; });
    el.addEventListener("dragend", dndEnd);
  });
  nav.addEventListener("dragover", (e) => dndOver(e, nav, ":scope > .wk-top"));
  nav.querySelectorAll(".wk-subs").forEach((c) => {
    c.querySelectorAll(":scope > .wk-sub").forEach((el) => {
      el.setAttribute("draggable", "true");
      el.addEventListener("dragstart", (e) => { e.stopPropagation(); dragEl = el; el.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; });
      el.addEventListener("dragend", dndEnd);
    });
    c.addEventListener("dragover", (e) => { e.stopPropagation(); dndOver(e, c, ":scope > .wk-sub"); });
  });
}
function dndEnd() { if (dragEl) dragEl.classList.remove("dragging"); dragEl = null; }
function dndOver(e, container, sel) {
  if (!dragEl || dragEl.parentElement !== container) return;
  e.preventDefault();
  const items = [...container.querySelectorAll(sel)].filter((x) => x !== dragEl);
  let after = null;
  for (const it of items) {
    const box = it.getBoundingClientRect();
    if (e.clientY < box.top + box.height / 2) { after = it; break; }
  }
  if (after == null) container.appendChild(dragEl);
  else container.insertBefore(dragEl, after);
}
async function submitReorder() {
  const nav = $("wiki-nav-list");
  const top = [...nav.querySelectorAll(":scope > .wk-top")].map((el) => parseInt(el.dataset.id, 10));
  const subs = {};
  nav.querySelectorAll(".wk-subs").forEach((c) => {
    subs[c.dataset.parent] = [...c.querySelectorAll(":scope > .wk-sub")].map((el) => parseInt(el.dataset.id, 10));
  });
  justifyCtx = { kind: "reorder", payload: { top, subs } };
  $("wj-title").textContent = "Reordenar el índice";
  $("wj-sub").textContent = "Propones una nueva organización del índice. Pasa por revisión.";
  $("wj-summary").value = "Reordenación del índice"; $("wj-justify").value = ""; $("wj-err").textContent = "";
  $("wiki-justify-modal").classList.add("open"); $("wj-justify").focus();
}

/* ---------- Mini-editor de formato ---------- */
function fmt(e, cmd) { e.preventDefault(); document.execCommand(cmd, false, null); $("we-body").focus(); }
function block(e, tag) { e.preventDefault(); $("we-body").focus(); document.execCommand("formatBlock", false, tag); }
function insertBox(e) {
  e.preventDefault(); $("we-body").focus();
  document.execCommand("insertHTML", false,
    '<table class="wk-callout"><tbody><tr><td>Título del cuadro</td></tr><tr><td>Contenido del cuadro…</td></tr></tbody></table><p><br></p>');
}
function insertTable(e) {
  e.preventDefault(); $("we-body").focus();
  document.execCommand("insertHTML", false,
    '<table class="wk-data"><thead><tr><th>Columna 1</th><th>Columna 2</th></tr></thead><tbody><tr><td>—</td><td>—</td></tr><tr><td>—</td><td>—</td></tr></tbody></table><p><br></p>');
}
let savedRange = null;
function saveSel() {
  const s = window.getSelection();
  if (s && s.rangeCount && $("we-body").contains(s.anchorNode)) savedRange = s.getRangeAt(0).cloneRange();
  else savedRange = null;
}
function restoreSel() {
  $("we-body").focus();
  if (savedRange) { const s = window.getSelection(); s.removeAllRanges(); s.addRange(savedRange); }
}
function pickImage(e) { e.preventDefault(); saveSel(); $("we-image-input").click(); }
async function handleImageFile(input) {
  const file = input.files && input.files[0]; input.value = "";
  if (!file) return;
  if (!/^image\//.test(file.type)) { wikiToast("Eso no es una imagen", "err"); return; }
  if (file.size > 6 * 1024 * 1024) { wikiToast("La imagen supera los 6 MB", "err"); return; }
  wikiToast("Subiendo imagen…", "");
  try {
    const dataUrl = await new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(file); });
    const { ok, d } = await apiSend("/api/wiki/upload-image", "POST", { data: dataUrl, mime: file.type });
    if (!ok) { wikiToast(d.error || "No se pudo subir la imagen", "err"); return; }
    restoreSel();
    document.execCommand("insertHTML", false, '<img src="' + d.url + '" alt=""><p><br></p>');
    wikiToast("Imagen añadida ✓", "ok");
  } catch (e) { wikiToast("Error al subir la imagen", "err"); }
}

