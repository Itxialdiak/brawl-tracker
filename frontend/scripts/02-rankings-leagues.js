/* BrawlSensei · 02-rankings-leagues.js
   Rankings + liguillas (rankings personalizados, drag&drop, compartir/importar) + navegación de secciones y pestañas.
   Se carga como <script src> desde index.html, en orden. El JS de cliente
   SIEMPRE es visible en el navegador: aquí no van secretos. */
/* ---------- Rankings ---------- */
function rankNorm(t) { return (t || "").toUpperCase(); }
function toggleCollapse(headEl) { const p = headEl.closest(".panel"); if (p) p.classList.toggle("collapsed"); }
function applyScopeButtons() {
  document.querySelectorAll(".rscope[data-scope]").forEach((b) => b.classList.toggle("active", b.dataset.scope === rankScope));
  const nat = $("rscope-national"); if (nat) nat.classList.toggle("active", rankScope === "national");
}
function updateScopeUI() { /* el país nacional se elige con el desplegable; nada que preparar */ }
// Global/Comunitaria: botones con data-scope. Nacional: se activa al elegir país en el desplegable.
document.querySelectorAll(".rscope[data-scope]").forEach((b) => b.addEventListener("click", () => {
  rankScope = b.dataset.scope; applyScopeButtons();
  loadMainRanking(); loadBrawlerRanking();
}));
$("rank-brawler-sel").addEventListener("change", loadBrawlerRanking);

/* --- País del ranking nacional (desplegable "País") --- */
var RANK_COUNTRIES = [
  { code: "es", name: "España" }, { code: "pt", name: "Portugal" }, { code: "fr", name: "Francia" },
  { code: "gb", name: "Reino Unido" }, { code: "ie", name: "Irlanda" }, { code: "de", name: "Alemania" },
  { code: "it", name: "Italia" }, { code: "nl", name: "Países Bajos" }, { code: "be", name: "Bélgica" },
  { code: "ch", name: "Suiza" }, { code: "at", name: "Austria" }, { code: "pl", name: "Polonia" },
  { code: "cz", name: "Chequia" }, { code: "se", name: "Suecia" }, { code: "no", name: "Noruega" },
  { code: "dk", name: "Dinamarca" }, { code: "fi", name: "Finlandia" }, { code: "gr", name: "Grecia" },
  { code: "ro", name: "Rumanía" }, { code: "hu", name: "Hungría" }, { code: "tr", name: "Turquía" },
  { code: "ru", name: "Rusia" }, { code: "ua", name: "Ucrania" }, { code: "us", name: "Estados Unidos" },
  { code: "ca", name: "Canadá" }, { code: "mx", name: "México" }, { code: "br", name: "Brasil" },
  { code: "ar", name: "Argentina" }, { code: "cl", name: "Chile" }, { code: "co", name: "Colombia" },
  { code: "pe", name: "Perú" }, { code: "ve", name: "Venezuela" }, { code: "ec", name: "Ecuador" },
  { code: "uy", name: "Uruguay" }, { code: "jp", name: "Japón" }, { code: "kr", name: "Corea del Sur" },
  { code: "cn", name: "China" }, { code: "in", name: "India" }, { code: "id", name: "Indonesia" },
  { code: "ph", name: "Filipinas" }, { code: "th", name: "Tailandia" }, { code: "vn", name: "Vietnam" },
  { code: "au", name: "Australia" }, { code: "nz", name: "Nueva Zelanda" }, { code: "sa", name: "Arabia Saudí" },
  { code: "ae", name: "Emiratos Árabes Unidos" }, { code: "eg", name: "Egipto" }, { code: "ma", name: "Marruecos" },
  { code: "za", name: "Sudáfrica" },
];
let rankCountry = null;
function rankCountryName(code) { const c = RANK_COUNTRIES.find((x) => x.code === (code || "").toLowerCase()); return c ? c.name : (code || "").toUpperCase(); }
function toggleCountryMenu(e) {
  e.stopPropagation();
  const m = $("country-menu"); if (!m) return;
  if (!m.dataset.built) { buildCountryMenu(); m.dataset.built = "1"; }
  m.classList.toggle("open");
}
function buildCountryMenu() {
  const list = RANK_COUNTRIES.slice();
  const mine = (typeof currentUser !== "undefined" && currentUser && currentUser.country) ? currentUser.country.toLowerCase() : null;
  if (mine) { const i = list.findIndex((c) => c.code === mine); if (i > 0) { const [m] = list.splice(i, 1); list.unshift(m); } }
  $("country-menu").innerHTML = list.map((c) =>
    `<button class="country-opt ${rankCountry === c.code ? "active" : ""}" onclick="selectRankCountry('${c.code}')">
      <img class="rsc-flag" src="https://flagcdn.com/w40/${c.code}.png" alt="" loading="lazy" onerror="this.style.visibility='hidden'"><span>${esc(c.name)}${c.code === mine ? ' <span class="country-mine">· tu país</span>' : ''}</span></button>`).join("");
}
function selectRankCountry(code) {
  rankCountry = code; rankScope = "national";
  $("rscope-country-flag").innerHTML = `<img class="rsc-flag" src="https://flagcdn.com/w40/${code}.png" alt="" onerror="this.style.display='none'">`;
  $("country-menu").classList.remove("open");
  buildCountryMenu();
  applyScopeButtons();
  loadMainRanking(); loadBrawlerRanking();
}
document.addEventListener("click", (e) => {
  const w = $("rscope-country-wrap");
  if (w && !w.contains(e.target)) { const m = $("country-menu"); if (m) m.classList.remove("open"); }
});

let rankView = "players";   // "players" | "clubs" (switch del panel unificado de rankings)
function setRankView(v) {
  rankView = (v === "clubs") ? "clubs" : "players";
  document.querySelectorAll(".rvs-btn").forEach((b) => b.classList.toggle("active", b.dataset.view === rankView));
  const t = $("rank-main-title"); if (t) t.textContent = rankView === "clubs" ? "Ranking de clubs" : "Ranking de jugador";
  loadMainRanking();
}

async function loadRankings() {
  if (!currentPlayer) return;
  try { myProfile = await getJSON("/api/player-profile?player=" + encodeURIComponent(currentPlayer)); }
  catch (e) { myProfile = null; }
  const sel = $("rank-brawler-sel");
  if (myProfile && myProfile.brawlers && myProfile.brawlers.length) {
    const prev = sel.value;
    sel.innerHTML = myProfile.brawlers.map((b) => `<option value="${b.id}">${esc(b.name)} · ${b.trophies}🏆</option>`).join("");
    if (prev && myProfile.brawlers.some((b) => String(b.id) === prev)) sel.value = prev;
  } else { sel.innerHTML = `<option value="">—</option>`; }
  updateScopeUI();
  loadMainRanking(); loadBrawlerRanking();
  await renderCustomRankings();
  await applySavedOrder();
  setupRankingsDnD();
}
// Panel unificado: jugadores o clubs según el switch, con el scope activo (incl. Comunitaria).
async function loadMainRanking() {
  const el = $("rank-main"); if (!el) return;
  el.innerHTML = '<div class="rk-loading">Cargando…</div>';
  const cq = (rankScope === "national" && rankCountry) ? "&country=" + rankCountry : "";
  let d; try { d = await getJSON(`/api/rankings?kind=${rankView}&scope=${rankScope}${cq}`); }
  catch (e) { el.innerHTML = '<div class="rk-empty">No se pudo cargar el ranking.</div>'; return; }
  let highlight = null;
  if (rankView === "clubs") {
    // El ranking global/nacional de clubs resalta por tag; el comunitario agrupa por nombre.
    highlight = myProfile && myProfile.club ? (rankScope === "community" ? myProfile.club.name : myProfile.club.tag) : null;
  } else {
    highlight = currentPlayer;
  }
  renderRanking(el, $("rank-main-hint"), d, highlight, rankView);
}
async function loadBrawlerRanking() {
  const el = $("rank-brawler"), hint = $("rank-brawler-hint"); const bid = $("rank-brawler-sel").value;
  if (!bid) { el.innerHTML = '<div class="rk-empty">No hay brawler seleccionado.</div>'; hint.textContent = ""; return; }
  el.innerHTML = '<div class="rk-loading">Cargando…</div>';
  const cq = (rankScope === "national" && rankCountry) ? "&country=" + rankCountry : "";
  let d; try { d = await getJSON(`/api/rankings?kind=brawlers&brawler_id=${encodeURIComponent(bid)}&scope=${rankScope}${cq}`); }
  catch (e) { el.innerHTML = '<div class="rk-empty">No se pudo cargar el ranking.</div>'; return; }
  renderRanking(el, hint, d, currentPlayer, "brawlers");
}
function renderRanking(el, hint, d, highlight, kind) {
  const items = (d && d.items) || [];
  if (!items.length) { el.innerHTML = '<div class="rk-empty">Sin datos todavía.</div>'; if (hint) hint.textContent = ""; return; }
  const key = (it) => rankNorm(it.tag || it.name);   // clubs comunitarios no tienen tag: se emparejan por nombre
  const meIdx = highlight ? items.findIndex((it) => key(it) === rankNorm(highlight)) : -1;
  const community = d.scope === "community";
  const scopeTxt = community ? "de la comunidad"
    : d.scope === "national" ? "de " + rankCountryName(d.country) : "mundial";
  if (hint) {
    const where = community ? `ranking ${scopeTxt}` : `top 200 ${scopeTxt}`;
    if (meIdx >= 0) hint.innerHTML = `Estás en el puesto <b>#${items[meIdx].rank}</b> del ${where}.`;
    else if (highlight) hint.innerHTML = `No apareces en el ${where}.`;
    else hint.innerHTML = community
      ? (kind === "clubs" ? "Clubs de la comunidad, por trofeos de sus miembros en la plataforma."
                          : "Jugadores principales de las cuentas de la plataforma (se rellena con secundarios).")
      : `Top 200 ${scopeTxt}.`;
  }
  const TOP = 25;
  let rows = items.slice(0, TOP).map((it) => rankRow(it, key(it) === rankNorm(highlight), kind)).join("");
  if (meIdx >= TOP) rows += '<div class="rk-sep">· · ·</div>' + rankRow(items[meIdx], true, kind);
  el.innerHTML = `<div class="rk-list">${rows}</div>`;
}
function prettyRole(r) { return ({ president: "Presidente", vicePresident: "Vicepresidente", senior: "Veterano", member: "Miembro" })[r] || r; }
function rankRow(item, isMe, kind) {
  const rank = item.rank != null ? item.rank : "";
  const iconId = item.icon_id != null ? item.icon_id : (item.icon && item.icon.id);
  const icon = (kind !== "clubs" && iconId) ? `<img class="rk-icon" src="https://cdn.brawlify.com/profile-icons/regular/${iconId}.png" loading="lazy" onerror="this.style.visibility='hidden'">` : "";
  const clubName = item.club && (typeof item.club === "string" ? item.club : item.club.name);
  const club = clubName ? `<span class="rk-club">${esc(clubName)}</span>` : "";
  const role = item.role ? `<span class="rk-club">${esc(prettyRole(item.role))}</span>` : "";
  const membersN = (kind === "clubs" && (item.members != null || item.member_count != null))
    ? `<span class="rk-club">${item.members != null ? item.members : item.member_count} miembros</span>` : "";
  const sec = item.is_secondary ? `<span class="rk-club rk-sec">2.º jugador</span>` : "";
  const tro = item.trophies != null ? `${Number(item.trophies).toLocaleString("es-ES")}🏆` : "";
  // Los clubs con tag abren su página al pulsarlos.
  const clickable = kind === "clubs" && item.tag ? ` rk-clickable" onclick="openClub(${esc(JSON.stringify(item.tag))})"` : '"';
  return `<div class="rk-row ${isMe ? "me" : ""}${clickable}><span class="rk-rank">${rank}</span>${icon}<span class="rk-name">${esc(item.name || "")}${club}${membersN}${role}${sec}</span><span class="rk-tro">${tro}</span></div>`;
}

/* ---------- Página de un club (descripción editable + ranking interno) ---------- */
let _clubData = null;
function closeClub() { $("club-modal").classList.remove("open"); }
async function openClub(tag) {
  const t = String(tag || "").replace(/^#/, "");
  if (!t) return;
  $("club-modal").classList.add("open");
  const box = $("club-body"); box.innerHTML = '<div class="wk-loading">Cargando club…</div>';
  let d; try { d = await getJSON("/api/club?tag=" + encodeURIComponent(t)); }
  catch (e) { box.innerHTML = '<div class="rk-empty">No se pudo cargar el club.</div>'; return; }
  if (d.error) { box.innerHTML = `<div class="rk-empty">${esc(d.error)}</div>`; return; }
  _clubData = d; renderClub();
}
function renderClub() {
  const d = _clubData, p = d.page || {};
  const members = (d.members || []).map((m, i) => ({ ...m, rank: i + 1 }));
  const meIdx = currentPlayer ? members.findIndex((m) => rankNorm(m.tag) === rankNorm(currentPlayer)) : -1;
  const badge = (lbl, val) => `<div class="club-stat"><div class="cs-v">${val}</div><div class="cs-l">${esc(lbl)}</div></div>`;
  const header = `<div class="club-head">
      <div class="club-id"><div class="club-name">${esc(d.name || "Club")}</div><div class="club-tag">${esc(d.tag || "")}</div></div>
      <div class="club-stats">
        ${badge("Trofeos", Number(d.trophies || 0).toLocaleString("es-ES"))}
        ${badge("Miembros", d.member_count || members.length)}
        ${badge("Requisito", Number(d.required_trophies || 0).toLocaleString("es-ES"))}
      </div></div>`;
  const desc = p.description || "";
  const descBlock = `<div class="club-section"><div class="club-sec-head"><h4>Descripción</h4>
      ${p.can_edit ? `<button class="ghost mini-btn" onclick="editClubDesc()">✎ Editar</button>` : ""}</div>
      <div id="club-desc" class="club-desc">${desc
        ? esc(desc).replace(/\n/g, "<br>")
        : `<span class="evd-muted">Este club aún no tiene descripción.${p.can_edit ? " Pulsa «Editar» para presentarlo a la comunidad." : ""}</span>`}</div></div>`;
  let mgmt = "";
  if (p.can_manage) {
    const managed = members.filter((m) => m.role !== "president" && m.role !== "vicePresident");
    const eds = (p.editors || []).map(rankNorm);
    mgmt = `<div class="club-section"><div class="club-sec-head"><h4>Permisos de edición <span class="evd-muted">(presidente)</span></h4></div>
      <div class="club-policy">
        <label><input type="radio" name="clubpol" ${p.edit_policy !== "managers" ? "checked" : ""} onclick="setClubPolicy('members')"> Cualquier miembro puede editar</label>
        <label><input type="radio" name="clubpol" ${p.edit_policy === "managers" ? "checked" : ""} onclick="setClubPolicy('managers')"> Solo cargos y editores designados</label>
      </div>
      ${p.edit_policy === "managers" ? `<div class="club-editors">${managed.map((m) => `<label class="club-editor-row"><input type="checkbox" ${eds.includes(rankNorm(m.tag)) ? "checked" : ""} onclick="setClubEditor(${esc(JSON.stringify(m.tag))}, this.checked)"> ${esc(m.name)}</label>`).join("") || '<span class="evd-muted">No hay más miembros.</span>'}</div>` : ""}
    </div>`;
  }
  const ranking = `<div class="club-section"><div class="club-sec-head"><h4>Ranking del Club</h4>
      ${meIdx >= 0 ? `<span class="evd-muted">Vas #${meIdx + 1}</span>` : ""}</div>
      <div class="rk-list">${members.map((m) => rankRow(m, meIdx >= 0 && rankNorm(m.tag) === rankNorm(currentPlayer), "members")).join("")}</div></div>`;
  $("club-body").innerHTML = header + descBlock + mgmt + ranking;
}
function editClubDesc() {
  const box = $("club-desc"); if (!box) return;
  const cur = (_clubData.page || {}).description || "";
  box.innerHTML = `<textarea id="club-desc-ta" class="club-desc-ta" maxlength="2000" placeholder="Presenta tu club a la comunidad: estilo de juego, requisitos, ambiente, idioma…">${esc(cur)}</textarea>
    <div class="club-desc-acts"><button class="btn mini-btn" onclick="saveClubDesc()">Guardar</button>
    <button class="ghost mini-btn" onclick="renderClub()">Cancelar</button></div>`;
  const ta = $("club-desc-ta"); if (ta) ta.focus();
}
async function saveClubDesc() {
  const val = $("club-desc-ta").value;
  const tag = (_clubData.tag || "").replace(/^#/, "");
  const { ok, d } = await apiSend("/api/club/" + encodeURIComponent(tag) + "/description", "POST", { description: val });
  if (!ok) { wikiToast(d.error || "No se pudo guardar", "err"); return; }
  _clubData.page.description = d.description;
  wikiToast("Descripción guardada ✓", "ok"); renderClub();
}
async function setClubPolicy(pol) {
  const tag = (_clubData.tag || "").replace(/^#/, "");
  const { ok, d } = await apiSend("/api/club/" + encodeURIComponent(tag) + "/policy", "POST", { policy: pol });
  if (!ok) { wikiToast(d.error || "No se pudo", "err"); return; }
  _clubData.page.edit_policy = pol; renderClub();
}
async function setClubEditor(ptag, granted) {
  const tag = (_clubData.tag || "").replace(/^#/, "");
  const { ok, d } = await apiSend("/api/club/" + encodeURIComponent(tag) + "/editor", "POST", { player_tag: ptag, granted });
  if (!ok) { wikiToast(d.error || "No se pudo", "err"); return; }
  const eds = _clubData.page.editors || [], n = rankNorm(ptag);
  _clubData.page.editors = granted ? [...eds, ptag] : eds.filter((e) => rankNorm(e) !== n);
}

/* ---------- Liguillas (rankings personalizados) ---------- */
async function renderCustomRankings() {
  const grid = $("rankings-grid");
  grid.querySelectorAll(".panel.cr-panel").forEach((p) => p.remove());
  let list = [];
  try { const d = await getJSON("/api/custom-rankings"); list = (d && d.rankings) || []; }
  catch (e) { list = []; }
  list.forEach((cr) => grid.appendChild(buildCustomPanel(cr)));
  list.forEach((cr) => loadCustomStandings(cr.id));
}
function buildCustomPanel(cr) {
  const p = document.createElement("div");
  p.className = "panel collapsible cr-panel";
  p.dataset.cat = "custom:" + cr.id;
  p.style.gridColumn = "1/-1";
  const ownerTag = cr.owned ? "" : `<span class="cr-owner-tag">importada</span>`;
  const editBtn = cr.owned ? `<button title="Editar jugadores" onclick="event.stopPropagation();editLigilla(${cr.id})">✎</button>` : "";
  const delTitle = cr.owned ? "Borrar liguilla" : "Quitar de mi cuenta";
  const actions =
    `<button title="Compartir" onclick="event.stopPropagation();shareLigilla(${cr.id})">⤴</button>` +
    editBtn +
    `<button class="danger" title="${delTitle}" onclick="event.stopPropagation();deleteLigilla(${cr.id},${cr.owned ? 1 : 0})">🗑</button>`;
  const nJug = cr.count + " jugador" + (cr.count === 1 ? "" : "es");
  p.innerHTML =
    `<h2 class="collapse-head" draggable="true" onclick="toggleCollapse(this)">` +
      `<span class="dot" style="background:var(--cyan)"></span>${esc(cr.name)}${ownerTag}` +
      `<span class="cr-actions">${actions}</span>` +
      `<span class="caret">▸</span>` +
    `</h2>` +
    `<div class="collapse-body">` +
      `<div class="hint">Liguilla${cr.owned ? "" : " importada"} · ${nJug} · ordenada por trofeos.</div>` +
      `<div id="cr-standings-${cr.id}"><div class="rk-loading">Cargando…</div></div>` +
    `</div>`;
  return p;
}
async function loadCustomStandings(id) {
  const el = $("cr-standings-" + id);
  if (!el) return;
  let d;
  try { d = await getJSON("/api/custom-rankings/" + id + "/standings"); }
  catch (e) { el.innerHTML = '<div class="rk-empty">No se pudo cargar.</div>'; return; }
  if (d && d.error) { el.innerHTML = '<div class="rk-empty">' + esc(d.error) + "</div>"; return; }
  const players = (d && d.players) || [];
  if (!players.length) { el.innerHTML = '<div class="rk-empty">Sin jugadores válidos todavía.</div>'; }
  else {
    let html = `<div class="rk-list">${players.map((pp) => rankRow(pp, rankNorm(pp.tag) === rankNorm(currentPlayer), "players")).join("")}</div>`;
    if (d.missing && d.missing.length) html += `<div class="cr-missing">No encontrados: ${d.missing.map(esc).join(", ")}</div>`;
    el.innerHTML = html;
  }
}

/* ---------- Drag & drop de categorías ---------- */
function setupRankingsDnD() {
  const grid = $("rankings-grid");
  if (grid._dnd) return;
  grid._dnd = true;
  grid.addEventListener("dragstart", (e) => {
    const head = e.target.closest(".collapse-head");
    if (!head || !grid.contains(head)) return;
    const panel = head.closest(".panel");
    if (!panel) return;
    panel.classList.add("dragging", "collapsed");
    e.dataTransfer.effectAllowed = "move";
    try { e.dataTransfer.setData("text/plain", panel.dataset.cat || ""); } catch (_) {}
  });
  grid.addEventListener("dragover", (e) => {
    const dragging = grid.querySelector(".panel.dragging");
    if (!dragging) return;
    e.preventDefault();
    const after = getDragAfterElement(grid, e.clientY);
    if (after == null) grid.appendChild(dragging);
    else grid.insertBefore(dragging, after);
  });
  grid.addEventListener("drop", (e) => { if (grid.querySelector(".panel.dragging")) e.preventDefault(); });
  grid.addEventListener("dragend", () => {
    const dragging = grid.querySelector(".panel.dragging");
    if (dragging) dragging.classList.remove("dragging");
    saveRankingsOrder();
  });
}
function getDragAfterElement(grid, y) {
  const els = [...grid.querySelectorAll(".panel:not(.dragging)")];
  let closest = { offset: -Infinity, el: null };
  for (const child of els) {
    const box = child.getBoundingClientRect();
    const offset = y - box.top - box.height / 2;
    if (offset < 0 && offset > closest.offset) closest = { offset, el: child };
  }
  return closest.el;
}
function currentOrder() {
  return [...$("rankings-grid").querySelectorAll(".panel")].map((p) => p.dataset.cat).filter(Boolean);
}
async function saveRankingsOrder() {
  try {
    await fetch("/api/rankings-order", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ order: currentOrder() }) });
  } catch (_) {}
}
async function applySavedOrder() {
  let order = null;
  try { const d = await getJSON("/api/rankings-order"); order = d && d.order; } catch (_) {}
  if (!order || !order.length) return;
  const grid = $("rankings-grid");
  const panels = [...grid.querySelectorAll(".panel")];
  const byCat = {};
  panels.forEach((p) => { byCat[p.dataset.cat] = p; });
  // Si el orden guardado es de una maquetación antigua (menciona categorías fijas que ya no
  // existen, p. ej. "player"/"clubs"/"internal"), se ignora y se mantiene el orden por defecto
  // (Ranking primero). Al reordenar a mano se guarda un orden nuevo y válido.
  const fixedSaved = order.filter((c) => c && !c.startsWith("custom:"));
  if (fixedSaved.some((c) => !byCat[c])) return;
  const used = new Set();
  order.forEach((cat) => { if (byCat[cat]) { grid.appendChild(byCat[cat]); used.add(cat); } });
  panels.forEach((p) => { if (!used.has(p.dataset.cat)) grid.appendChild(p); });
}

/* ---------- Modal crear/editar liguilla ---------- */
let ligillaEditId = null;
function openLigilla() {
  ligillaEditId = null;
  $("ligilla-title").textContent = "Crear liguilla";
  $("ligilla-name").value = "";
  $("ligilla-tags").value = "";
  $("ligilla-err").textContent = "";
  $("ligilla-save").disabled = false;
  $("ligilla-modal").classList.add("open");
  setTimeout(() => $("ligilla-name").focus(), 60);
}
async function editLigilla(id) {
  ligillaEditId = id;
  $("ligilla-title").textContent = "Editar liguilla";
  $("ligilla-err").textContent = "";
  $("ligilla-name").value = "";
  $("ligilla-tags").value = "";
  $("ligilla-save").disabled = false;
  $("ligilla-modal").classList.add("open");
  try {
    const d = await getJSON("/api/custom-rankings/" + id);
    if (d && !d.error) {
      $("ligilla-name").value = d.name || "";
      $("ligilla-tags").value = (d.players || []).join("\n");
    }
  } catch (e) {}
}
function closeLigilla() { $("ligilla-modal").classList.remove("open"); }
async function saveLigilla() {
  const name = $("ligilla-name").value.trim();
  const players = $("ligilla-tags").value;
  const err = $("ligilla-err");
  err.textContent = "";
  if (!name) { err.textContent = "Ponle un nombre a la liguilla."; return; }
  if (!players.trim()) { err.textContent = "Añade al menos un player ID."; return; }
  const btn = $("ligilla-save");
  btn.disabled = true;
  try {
    const editing = ligillaEditId != null;
    const url = editing ? "/api/custom-rankings/" + ligillaEditId : "/api/custom-rankings";
    const r = await fetch(url, { method: editing ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, players }) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { err.textContent = d.error || d.detail || "No se pudo guardar."; btn.disabled = false; return; }
    closeLigilla();
    await renderCustomRankings();
    await applySavedOrder();
  } catch (e) { err.textContent = "Error de red."; btn.disabled = false; }
}
async function deleteLigilla(id, owned) {
  const msg = owned ? "¿Borrar esta liguilla? Desaparecerá también para quienes la hayan importado." : "¿Quitar esta liguilla importada de tu cuenta?";
  if (!confirm(msg)) return;
  try { await fetch("/api/custom-rankings/" + id, { method: "DELETE" }); } catch (e) {}
  await renderCustomRankings();
  await applySavedOrder();
}

/* ---------- Compartir liguilla ---------- */
async function shareLigilla(id) {
  let token = null;
  try { const d = await getJSON("/api/custom-rankings/" + id); if (d && !d.error) token = d.share_token; } catch (e) {}
  if (!token) { return; }
  $("share-link").value = location.origin + "/?ligilla=" + encodeURIComponent(token);
  $("share-msg").textContent = "";
  $("share-modal").classList.add("open");
  setTimeout(() => $("share-link").select(), 60);
}
function closeShare() { $("share-modal").classList.remove("open"); }
async function copyShareLink() {
  const link = $("share-link").value;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) await navigator.clipboard.writeText(link);
    else { $("share-link").select(); document.execCommand("copy"); }
    $("share-msg").textContent = "✓ Enlace copiado al portapapeles.";
  } catch (e) { $("share-msg").textContent = "Selecciónalo y cópialo a mano (Ctrl+C)."; }
}

/* ---------- Importar liguilla por enlace ---------- */
let pendingImportToken = null;
function checkImportParam() {
  const token = new URLSearchParams(location.search).get("ligilla");
  if (token) openImport(token);
}
function checkEventParam() {
  const id = new URLSearchParams(location.search).get("event");
  if (id && /^\d+$/.test(id)) { showSection("leagues"); openEvent(parseInt(id, 10)); }
}
// Enlace de una publicación → perfil público de su autor (?user=<id>). Funciona logueado o invitado.
function checkUserParam() {
  const id = new URLSearchParams(location.search).get("user");
  if (id && /^\d+$/.test(id)) openPublicProfile(parseInt(id, 10));
}
function copyEventLink(id) {
  const url = location.origin + "/?event=" + id;
  const done = () => wikiToast("Enlace copiado", "ok");
  const fail = () => wikiToast(url, "");
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(url).then(done, fail);
  } else {
    const ta = document.createElement("textarea"); ta.value = url; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); done(); } catch (e) { fail(); }
    document.body.removeChild(ta);
  }
}
async function openImport(token) {
  pendingImportToken = token;
  $("import-err").textContent = "";
  $("import-sub").textContent = "Cargando…";
  $("import-btn").style.display = "none";
  $("import-modal").classList.add("open");
  let d;
  try { d = await getJSON("/api/shared-ranking?token=" + encodeURIComponent(token)); }
  catch (e) { d = { error: "No se pudo cargar el enlace." }; }
  if (!d || d.error) { $("import-sub").textContent = ""; $("import-err").textContent = (d && d.error) || "Enlace no válido o liguilla borrada."; return; }
  if (d.already || d.owned) {
    $("import-sub").innerHTML = `La liguilla <b>${esc(d.name)}</b> ya está en tu cuenta.`;
    $("import-btn").style.display = "none";
  } else {
    $("import-sub").innerHTML = `¿Añadir la liguilla <b>${esc(d.name)}</b> (${d.count} jugador${d.count === 1 ? "" : "es"}) a tu cuenta? Si el dueño la actualiza, verás los cambios.`;
    $("import-btn").style.display = "";
  }
}
function closeImport() {
  $("import-modal").classList.remove("open");
  if (location.search.indexOf("ligilla=") >= 0) history.replaceState(null, "", location.pathname);
  pendingImportToken = null;
}
async function confirmImport() {
  if (!pendingImportToken) return;
  $("import-err").textContent = "";
  const btn = $("import-btn");
  btn.disabled = true;
  try {
    const r = await fetch("/api/custom-rankings/import", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token: pendingImportToken }) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { $("import-err").textContent = d.error || "No se pudo importar."; btn.disabled = false; return; }
    closeImport();
    btn.disabled = false;
    switchTab("rankings");
  } catch (e) { $("import-err").textContent = "Error de red."; btn.disabled = false; }
}

/* ---------- Secciones de nivel superior ---------- */
// Secciones que un INVITADO (sin cuenta) puede ver.
const GUEST_SECTIONS = ["community", "tierlists", "guide", "actualizaciones", "leagues"];
function showSection(name) {
  // Guardia de permisos (defensa en profundidad): nadie sin rol admin ve el panel de
  // administración aunque llegue aquí. Los datos ya están protegidos en el servidor (403).
  if (name === "admin" && !(typeof currentUser !== "undefined" && currentUser && (currentUser.is_admin || currentUser.is_translator))) name = "brawlytics";
  const guest = document.body.classList.contains("guest");
  if (guest) {
    // Invitado: solo secciones públicas; cualquier otra cae en "Comunidad".
    if (!GUEST_SECTIONS.includes(name)) name = "community";
    const gv = $("guest-view");
    if (name === "community") {
      document.querySelectorAll(".app-section").forEach((s) => s.classList.remove("active", "guest-open"));
      if (gv) gv.style.display = "block";
    } else {
      if (gv) gv.style.display = "none";
      document.querySelectorAll(".app-section").forEach((s) => {
        const on = s.id === "section-" + name;
        s.classList.toggle("active", on); s.classList.toggle("guest-open", on);
      });
    }
    document.querySelectorAll(".snav").forEach((b) => b.classList.toggle("active", b.dataset.section === name));
    window.scrollTo({ top: 0, behavior: "smooth" });
  } else {
  document.querySelectorAll(".app-section").forEach((s) => s.classList.toggle("active", s.id === "section-" + name));
  document.querySelectorAll(".snav").forEach((b) => b.classList.toggle("active", b.dataset.section === name));
  window.scrollTo({ top: 0, behavior: "smooth" });
  }
  if (name === "guide") loadWikiTree();
  if (name === "admin") openDefaultAdminTab();
  if (name === "leagues") loadLeagues();
  if (name === "actualizaciones") loadActualizaciones();
  if (name === "servers") loadServerStatus();
  if (name === "tierlists") { loadTierlist("community"); loadMetaGlobal(); loadBuffsList(); }
  // Historial: cada navegación de sección apila una entrada para que "Atrás" vuelva dentro
  // de la app (a la sección anterior) en vez de salir de la página. La primera (al cargar)
  // reemplaza para no dejar una entrada fantasma.
  if (!_navPop) {
    const st = { nav: "section", section: name };
    if (history.state && history.state.nav) history.pushState(st, "", "#" + name);
    else history.replaceState(st, "", "#" + name);
  }
}

/* "Atrás" del navegador → navega DENTRO de la app (sección anterior / cierra la ficha). */
window.addEventListener("popstate", function (e) {
  _navPop = true;
  try {
    const st = e.state || {};
    if (st.nav === "brawler" && st.brawler && typeof showBrawlerDetail === "function") {
      showSection("brawlytics"); switchTab("brawlers"); showBrawlerDetail(st.brawler);
    } else {
      const sec = st.section || "brawlytics";
      showSection(sec);
      if (sec === "brawlytics" && typeof showBrawlersGridView === "function") showBrawlersGridView();
    }
  } finally { _navPop = false; }
});

/* ---------- Pestañas ---------- */
const FILTER_TABS = { stats: "tab-stats" };  // filtros compartidos solo en Analíticas (el Sensei tiene su propio cuestionario)
function placeFilters(name) {
  const fb = $("filters-bar");
  if (!fb) return;
  const targetId = FILTER_TABS[name];
  if (targetId) {
    const title = $(targetId).querySelector(".section-title");
    if (title) title.insertAdjacentElement("afterend", fb);  // bajo el título de la sección
    fb.style.display = "";
  } else {
    fb.style.display = "none";
  }
}
function switchTab(name) {
  activeTab = name;
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".tab-content").forEach((c) => c.classList.toggle("active", c.id === "tab-" + name));
  placeFilters(name);
  if (name === "history") loadHistory(true);
  if (name === "report") { loadReport(); loadRotation(); }
  if (name === "rankings") loadRankings();
  if (name === "coach") { showCoachListView(); loadSenseiQuiz(); loadReports(); }
  if (name === "brawlers") loadBrawlers();
  if (name === "retos") loadRetos();
}
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));

