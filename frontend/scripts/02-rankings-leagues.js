/* BrawlSensei · 02-rankings-leagues.js
   Rankings + liguillas (rankings personalizados, drag&drop, compartir/importar) + navegación de secciones y pestañas.
   Se carga como <script src> desde index.html, en orden. El JS de cliente
   SIEMPRE es visible en el navegador: aquí no van secretos. */
/* ---------- Rankings ---------- */
function rankNorm(t) { return (t || "").toUpperCase(); }
function toggleCollapse(headEl) { const p = headEl.closest(".panel"); if (p) p.classList.toggle("collapsed"); }
function applyScopeButtons() { document.querySelectorAll(".rscope").forEach((b) => b.classList.toggle("active", b.dataset.scope === rankScope)); }
function updateScopeUI() {
  const hasCountry = !!(currentUser && currentUser.country);
  $("rscope-national").disabled = !hasCountry;
  $("rank-country-hint").textContent = hasCountry
    ? "Tu país: " + currentUser.country.toUpperCase()
    : "Elige tu país en el menú de usuario (arriba a la derecha) para ver rankings nacionales.";
  if (!hasCountry && rankScope === "national") { rankScope = "global"; applyScopeButtons(); }
}
document.querySelectorAll(".rscope").forEach((b) => b.addEventListener("click", () => {
  if (b.disabled) return;
  rankScope = b.dataset.scope; applyScopeButtons();
  loadPlayerRanking(); loadClubRanking(); loadBrawlerRanking();
}));
$("rank-brawler-sel").addEventListener("change", loadBrawlerRanking);

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
  loadPlayerRanking(); loadClubRanking(); loadBrawlerRanking(); loadClubInternal();
  await renderCustomRankings();
  await applySavedOrder();
  setupRankingsDnD();
}
async function loadPlayerRanking() {
  const el = $("rank-player"); el.innerHTML = '<div class="rk-loading">Cargando…</div>';
  let d; try { d = await getJSON(`/api/rankings?kind=players&scope=${rankScope}`); }
  catch (e) { el.innerHTML = '<div class="rk-empty">No se pudo cargar el ranking.</div>'; return; }
  renderRanking(el, $("rank-player-hint"), d, currentPlayer, "players");
}
async function loadBrawlerRanking() {
  const el = $("rank-brawler"), hint = $("rank-brawler-hint"); const bid = $("rank-brawler-sel").value;
  if (!bid) { el.innerHTML = '<div class="rk-empty">No hay brawler seleccionado.</div>'; hint.textContent = ""; return; }
  el.innerHTML = '<div class="rk-loading">Cargando…</div>';
  let d; try { d = await getJSON(`/api/rankings?kind=brawlers&brawler_id=${encodeURIComponent(bid)}&scope=${rankScope}`); }
  catch (e) { el.innerHTML = '<div class="rk-empty">No se pudo cargar el ranking.</div>'; return; }
  renderRanking(el, hint, d, currentPlayer, "brawlers");
}
async function loadClubRanking() {
  const el = $("rank-clubs"); el.innerHTML = '<div class="rk-loading">Cargando…</div>';
  let d; try { d = await getJSON(`/api/rankings?kind=clubs&scope=${rankScope}`); }
  catch (e) { el.innerHTML = '<div class="rk-empty">No se pudo cargar el ranking.</div>'; return; }
  const myClubTag = myProfile && myProfile.club ? myProfile.club.tag : null;
  renderRanking(el, $("rank-clubs-hint"), d, myClubTag, "clubs");
}
async function loadClubInternal() {
  const el = $("rank-internal"), hint = $("rank-internal-hint");
  const clubTag = myProfile && myProfile.club ? myProfile.club.tag : null;
  if (!clubTag) { el.innerHTML = '<div class="rk-empty">Este jugador no está en ningún club.</div>'; hint.textContent = ""; return; }
  el.innerHTML = '<div class="rk-loading">Cargando…</div>';
  let d; try { d = await getJSON("/api/club?tag=" + encodeURIComponent(clubTag.replace("#", ""))); }
  catch (e) { el.innerHTML = '<div class="rk-empty">No se pudo cargar el club.</div>'; return; }
  const members = (d.members || []).map((m, i) => ({ ...m, rank: i + 1 }));
  const mePos = members.findIndex((m) => rankNorm(m.tag) === rankNorm(currentPlayer));
  hint.innerHTML = `${esc(d.name || "Club")} · ${d.member_count} miembros` + (mePos >= 0 ? ` · vas <b>#${mePos + 1}</b>` : "");
  el.innerHTML = `<div class="rk-list">${members.map((m) => rankRow(m, rankNorm(m.tag) === rankNorm(currentPlayer), "members")).join("")}</div>`;
}
function renderRanking(el, hint, d, highlightTag, kind) {
  const items = (d && d.items) || [];
  if (!items.length) { el.innerHTML = '<div class="rk-empty">Sin datos.</div>'; if (hint) hint.textContent = ""; return; }
  const meIdx = highlightTag ? items.findIndex((it) => rankNorm(it.tag) === rankNorm(highlightTag)) : -1;
  const scopeTxt = d.scope === "national" ? "de tu país (" + (d.country || "").toUpperCase() + ")" : "mundial";
  if (hint) {
    if (meIdx >= 0) hint.innerHTML = `Estás en el puesto <b>#${items[meIdx].rank}</b> del top 200 ${scopeTxt}.`;
    else hint.innerHTML = highlightTag ? `Fuera del top 200 ${scopeTxt}.` : `Top 200 ${scopeTxt}.`;
  }
  const TOP = 25;
  let rows = items.slice(0, TOP).map((it) => rankRow(it, rankNorm(it.tag) === rankNorm(highlightTag), kind)).join("");
  if (meIdx >= TOP) rows += '<div class="rk-sep">· · ·</div>' + rankRow(items[meIdx], true, kind);
  el.innerHTML = `<div class="rk-list">${rows}</div>`;
}
function prettyRole(r) { return ({ president: "Presidente", vicePresident: "Vicepresidente", senior: "Veterano", member: "Miembro" })[r] || r; }
function rankRow(item, isMe, kind) {
  const rank = item.rank != null ? item.rank : "";
  const iconId = item.icon_id != null ? item.icon_id : (item.icon && item.icon.id);
  const icon = (kind !== "clubs" && iconId) ? `<img class="rk-icon" src="https://cdn.brawlify.com/profile-icons/regular/${iconId}.png" onerror="this.style.visibility='hidden'">` : "";
  const clubName = item.club && (typeof item.club === "string" ? item.club : item.club.name);
  const club = clubName ? `<span class="rk-club">${esc(clubName)}</span>` : "";
  const role = item.role ? `<span class="rk-club">${esc(prettyRole(item.role))}</span>` : "";
  const tro = item.trophies != null ? `${Number(item.trophies).toLocaleString("es-ES")}🏆` : "";
  return `<div class="rk-row ${isMe ? "me" : ""}"><span class="rk-rank">${rank}</span>${icon}<span class="rk-name">${esc(item.name || "")}${club}${role}</span><span class="rk-tro">${tro}</span></div>`;
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
function showSection(name) {
  document.querySelectorAll(".app-section").forEach((s) => s.classList.toggle("active", s.id === "section-" + name));
  document.querySelectorAll(".snav").forEach((b) => b.classList.toggle("active", b.dataset.section === name));
  window.scrollTo({ top: 0, behavior: "smooth" });
  if (name === "guide") loadWikiTree();
  if (name === "admin") { loadAdminPending(); }
  if (name === "leagues") loadLeagues();
  if (name === "tierlists") loadTierlist("community");
}

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

