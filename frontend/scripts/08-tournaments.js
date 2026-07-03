/* BrawlSensei · 08-tournaments.js
   Ligas y Torneos (eventos, equipos, rondas, clasificación, partidas).
   Se carga como <script src> desde index.html, en orden. El JS de cliente
   SIEMPRE es visible en el navegador: aquí no van secretos. */
/* ============================ LIGAS Y TORNEOS ============================ */
// Idiomas de eventos = los MISMOS del selector de idioma (window.I18N_LANGS, definido en
// 00-i18n.js que carga antes), en el mismo orden, + "Otro idioma" al final.
const EV_LANGS = (Array.isArray(window.I18N_LANGS) && window.I18N_LANGS.length
  ? window.I18N_LANGS.map((l) => ({ c: l.code, n: l.label }))
  : [{ c: "es", n: "Español" }, { c: "en", n: "English" }]
).concat([{ c: "other", n: "Otro idioma" }]);
function evLangName(c) { const x = EV_LANGS.find((l) => l.c === c); return x ? x.n : (c || "—"); }
const EV_KIND_LABEL = { league: "Liga", tournament: "Torneo" };
const EV_MODE_LABEL = { individual: "Individual", teams: "Por equipos" };
const EV_VIS_LABEL = { public: "Público", acceptance: "Con aceptación", private: "Privado" };
const EV_MATCH_LABEL = { bo1: "A 1 combate", bo3: "Al mejor de 3", bo5: "Al mejor de 5" };
const EV_STATUS_LABEL = { open: "Inscripción abierta", ongoing: "En curso", finished: "Finalizado" };
const EV_FORMAT_LABEL = { swiss: "Suizo", mcmahon: "McMahon", roundrobin: "Round Robin", single_elim: "Eliminación directa", double_elim: "Doble eliminación", free: "Liga libre", random_teams: "Equipos aleatorios" };
/* ----- Modos y mapas de Brawl Stars (desplegables dependientes) ----- */
let BS_MODES = [];
async function loadBsModes() {
  if (BS_MODES.length) return BS_MODES;
  try { const r = await getJSON("/api/bs/modes-maps"); BS_MODES = r.modes || []; } catch (e) { BS_MODES = []; }
  return BS_MODES;
}
function bsModeIcon(mode) { const m = BS_MODES.find((x) => x.name === mode); return m ? m.icon : null; }
function bsModeForMap(mp) { const m = BS_MODES.find((x) => (x.maps || []).includes(mp)); return m ? m.name : null; }
function bsMapsOf(mode) { const m = BS_MODES.find((x) => x.name === mode); return m ? m.maps : []; }
function mapImageSearch(mp) { return "https://www.google.com/search?tbm=isch&q=" + encodeURIComponent("Brawl Stars mapa " + mp); }
function fillModeMap(modeId, mapId, curMode, curMap) {
  const ms = $(modeId), ps = $(mapId);
  if (!ms || !ps) return;
  ms.innerHTML = `<option value="">— Modo —</option>` + BS_MODES.map((m) => `<option value="${esc(m.name)}">${esc(m.name)}</option>`).join("");
  ms.value = curMode || "";
  refillMaps(modeId, mapId, curMap);
}
function refillMaps(modeId, mapId, curMap) {
  const ms = $(modeId), ps = $(mapId), mode = ms.value;
  const maps = mode ? bsMapsOf(mode) : BS_MODES.flatMap((m) => m.maps);
  const uniq = [...new Set(maps)];
  ps.innerHTML = `<option value="">— Mapa —</option>` + uniq.map((mp) => `<option value="${esc(mp)}">${esc(mapNameEs(mp))}</option>`).join("");
  ps.value = (curMap && uniq.includes(curMap)) ? curMap : "";
}
function modeChanged(modeId, mapId) { refillMaps(modeId, mapId, $(mapId).value); }
function mapChanged(modeId, mapId) {
  const mp = $(mapId).value; if (!mp) return;
  const mode = bsModeForMap(mp);
  if (mode && $(modeId).value !== mode) { $(modeId).value = mode; refillMaps(modeId, mapId, mp); }
}
function bsAllowedModes(eventMode, showdown) {
  const teams = eventMode === "teams";
  const core = BS_MODES.filter((m) => m.kind === "core").map((m) => m.name);
  const duel = teams ? [] : BS_MODES.filter((m) => m.kind === "duel").map((m) => m.name);
  const sdKinds = teams ? ["sd_trio"] : ["sd_solo", "sd_duo"];
  const sd = BS_MODES.filter((m) => sdKinds.includes(m.kind)).map((m) => m.name);
  const base = core.concat(duel);
  if (showdown === "only") return sd;
  if (showdown === "exclude") return base;
  return base.concat(sd);
}
function allowedModeSet() {
  const d = currentEvent || {};
  return new Set(bsAllowedModes(d.mode, (d.settings || {}).showdown || "exclude"));
}
// Modos elegibles para una RONDA: 3v3, Duelos (individual) y Supervivencia SOLO/DÚO/TRÍO
// (equipos de 1/2/3 jugadores respectivamente; los que sobren descansan esa ronda —bye—).
// En eventos por equipos solo tiene sentido el trío (equipos ya formados de 3).
function roundAllowedModes() {
  const teams = (currentEvent || {}).mode === "teams";
  const core = BS_MODES.filter((m) => m.kind === "core").map((m) => m.name);
  const duel = teams ? [] : BS_MODES.filter((m) => m.kind === "duel").map((m) => m.name);
  const sdKinds = teams ? ["sd_trio"] : ["sd_solo", "sd_duo", "sd_trio"];
  const sd = BS_MODES.filter((m) => sdKinds.includes(m.kind)).map((m) => m.name);
  return new Set(core.concat(duel).concat(sd));
}
/* ----- Selección de modos/mapas por ronda (checkbox) con propuesta + confirmación ----- */
function msCountLabel(n, one, many) { return !n ? "Cualquiera" : (n === 1 ? "1 " + one : n + " " + many); }
// Mapas posibles según los modos elegidos (o de todos los modos permitidos si no hay ninguno).
function roundMapPool(selModes) {
  const allowed = allowedModeSet();
  const modes = (selModes && selModes.length) ? selModes : [...allowed];
  const out = [];
  modes.forEach((mn) => bsMapsOf(mn).forEach((mp) => { if (!out.includes(mp)) out.push(mp); }));
  return out.sort();
}
function roundModeOptsHTML(rn, selModes) {
  const sel = new Set(selModes || []);
  return [...roundAllowedModes()].sort().map((m) => {
    const ic = bsModeIcon(m);
    return `<label class="rc-ms-opt"><input type="checkbox" value="${esc(m)}" ${sel.has(m) ? "checked" : ""} onchange="roundModesChanged(${rn})">${ic ? `<img src="${esc(ic)}" alt="" onerror="this.style.display='none'">` : ""}<span>${esc(m)}</span></label>`;
  }).join("") || `<div class="ms-empty">Sin modos</div>`;
}
function roundMapOptsHTML(rn, selModes, selMaps) {
  const sel = new Set(selMaps || []);
  const pool = roundMapPool(selModes);
  return pool.map((mp) => {
    const img = (typeof mapAsset === "function" ? (mapAsset(mp) || {}).image : "");
    return `<label class="rc-ms-opt"><input type="checkbox" value="${esc(mp)}" ${sel.has(mp) ? "checked" : ""} onchange="roundMapsChanged(${rn})">${img ? `<img src="${esc(img)}" alt="" onerror="this.style.display='none'">` : ""}<span>${esc(mapNameEs(mp))}</span></label>`;
  }).join("") || `<div class="ms-empty">Elige un modo primero</div>`;
}
// Tarjeta de una ronda AÚN NO generada (organizador): fila 1 «Ronda X» centrada; fila 2 el
// desplegable de modos; fila 3 el de mapas (alineados a la izquierda).
function roundPickHTML(rn, rk) {
  const modes = rk.modes || [], maps = rk.maps || [];
  return `<div class="rmm-r">Ronda ${rn}</div>
    <div class="rmm-row"><div class="rc-ms on" data-rmm="modes-${rn}"><button type="button" class="rc-ms-trigger" onclick="retoMsOpen(event,this)">Modos: ${esc(msCountLabel(modes.length, "modo", "modos"))}</button>
      <div class="rc-ms-panel"><div class="rc-ms-opts">${roundModeOptsHTML(rn, modes)}</div></div></div></div>
    <div class="rmm-row"><div class="rc-ms on" data-rmm="maps-${rn}"><button type="button" class="rc-ms-trigger" onclick="retoMsOpen(event,this)">Mapas: ${esc(msCountLabel(maps.length, "mapa", "mapas"))}</button>
      <div class="rc-ms-panel"><div class="rc-ms-search"><input type="text" placeholder="Buscar mapa…" oninput="roundMapFilter(${rn}, this.value)" onclick="event.stopPropagation()"></div><div class="rc-ms-opts">${roundMapOptsHTML(rn, modes, maps)}</div></div></div></div>`;
}
// Filtra en vivo la lista de mapas del desplegable (coincidencia por "contiene", sin distinguir mayúsculas).
function roundMapFilter(rn, q) {
  const box = document.querySelector(`.rc-ms[data-rmm="maps-${rn}"] .rc-ms-opts`);
  if (!box) return;
  const s = (q || "").trim().toLowerCase();
  box.querySelectorAll(".rc-ms-opt").forEach((o) => {
    const nm = ((o.querySelector("span") || {}).textContent || "").toLowerCase();
    o.style.display = (!s || nm.includes(s)) ? "" : "none";
  });
}
// Filas comunes (modo en la 2ª, mapa en la 3ª) de una ronda con modo/mapa ya elegido.
function roundStaticRows(rn, mode, mp, s) {
  const showMode = mapsRevealed(s, "mode"), showMap = mapsRevealed(s, "map");
  let modeRow;
  if (!showMode && !showMap) modeRow = `<span class="evd-reveal">Por revelar</span>`;
  else modeRow = (showMode && mode) ? modeChip(mode) : `<span class="evd-muted">Sin asignar</span>`;
  const mapRow = (showMap && mp) ? mapChip(mp) : "";
  return `<div class="rmm-r">Ronda ${rn}</div><div class="rmm-row">${modeRow}</div>` +
    (mapRow ? `<div class="rmm-row">${mapRow}</div>` : "");
}
// Tarjeta de una ronda YA generada: fila 1 «Ronda X»; fila 2 modo; fila 3 mapa; fila 4 las
// etiquetas de estado (propuesta/confirmado + solicitar cambio) y, aparte, la votación.
function roundResolvedHTML(rn, rk, d) {
  const s = d.settings || {};
  const rows = roundStaticRows(rn, rk.mode, rk.map, s);
  const cr = rk.change_request;
  const pending = cr && cr.status === "pending";
  let badge = "";
  if (rk.status === "proposed") {
    badge = `<span class="rmm-badge prop">Propuesta</span><button class="rmm-confirm" onclick="confirmRoundMM(${rn})">✓ Confirmar</button>`;
  } else if (rk.status === "confirmed") {
    if (pending) badge = `<span class="rmm-badge prop">Cambio pendiente</span>`;
    else {
      badge = `<span class="rmm-badge conf" title="${rk.auto_approved ? "Aprobado automáticamente al pasar 24 h sin oposición" : ""}">🔒 Confirmado${rk.auto_approved ? " · auto" : ""}</span>`;
      if (d.is_owner) badge += `<button class="rmm-req" onclick="openRoundChange(${rn})" title="Pedir un cambio de modo/mapa (los jugadores votan)">Solicitar cambio</button>`;
    }
  }
  let crBlock = "";
  if (pending) {
    const mmNew = [cr.new_mode, cr.new_map].filter(Boolean).join(" · ");
    const uid = window.currentUser && String(currentUser.id);
    const voted = cr.votes && uid && cr.votes[uid];
    let act = "";
    if (d.is_owner) act = `<span class="evd-muted">Esperando el voto de los participantes…</span>`;
    else if (d.relation === "participant") {
      act = voted
        ? `<span class="evd-muted">Ya has votado: ${voted === "accept" ? "aceptado" : "opuesto"}</span>`
        : `<button class="mini-ok" onclick="voteRoundChange(${rn},'accept')">Aceptar</button><button class="mini-no" onclick="voteRoundChange(${rn},'object')">Oponerme</button>`;
    } else {
      act = `<span class="evd-muted">Solo los participantes pueden votar.</span>`;
    }
    crBlock = `<div class="rmm-cr"><b>Cambio propuesto:</b> ${esc(mmNew || "—")} — <i>${esc(cr.reason || "")}</i><div class="rmm-cr-act">${act}</div></div>`;
  }
  return `${rows}${badge ? `<div class="rmm-row badges">${badge}</div>` : ""}${crBlock}`;
}
function rmmSelectedValues(rn, kind) {
  const box = document.querySelector(`.rc-ms[data-rmm="${kind}-${rn}"] .rc-ms-opts`);
  if (!box) return [];
  return [...box.querySelectorAll("input:checked")].map((c) => c.value);
}
function roundModesChanged(rn) {
  // Al cambiar los modos, reconstruye las opciones de mapa (conservando los aún válidos).
  const selModes = rmmSelectedValues(rn, "modes");
  const selMaps = rmmSelectedValues(rn, "maps");
  const mapsBox = document.querySelector(`.rc-ms[data-rmm="maps-${rn}"] .rc-ms-opts`);
  if (mapsBox) mapsBox.innerHTML = roundMapOptsHTML(rn, selModes, selMaps);
  saveRoundMM(rn);
}
function roundMapsChanged(rn) { saveRoundMM(rn); }
async function saveRoundMM(rn) {
  const d = currentEvent;
  const modes = rmmSelectedValues(rn, "modes"), maps = rmmSelectedValues(rn, "maps");
  // Actualiza las etiquetas de los desplegables sin re-renderizar (para no cerrar el panel abierto).
  const tM = document.querySelector(`.rc-ms[data-rmm="modes-${rn}"] .rc-ms-trigger`);
  if (tM) tM.textContent = "Modos: " + msCountLabel(modes.length, "modo", "modos");
  const tP = document.querySelector(`.rc-ms[data-rmm="maps-${rn}"] .rc-ms-trigger`);
  if (tP) tP.textContent = "Mapas: " + msCountLabel(maps.length, "mapa", "mapas");
  const { ok, d: r } = await apiSend(`/api/events/${d.id}/rounds/${rn}/mode-map`, "PUT", { modes, maps });
  if (!ok) { wikiToast(r.error || r.detail || "No se pudo guardar la ronda", "err"); return; }
  d.settings = d.settings || {}; d.settings.round_maps = d.settings.round_maps || {};
  d.settings.round_maps[rn] = Object.assign({}, d.settings.round_maps[rn], { modes, maps });
}
async function confirmRoundMM(rn) {
  const d = currentEvent;
  const { ok, d: r } = await apiSend(`/api/events/${d.id}/rounds/${rn}/confirm`, "POST", {});
  if (!ok) { wikiToast(r.error || r.detail || "No se pudo confirmar", "err"); return; }
  wikiToast(`Ronda ${rn}: modo y mapa confirmados`, "ok");
  await openEvent(d.id);
}
// Crea una ronda vacía (solo aumenta el nº de rondas): el organizador elige sus modos/mapas y
// después la genera desde «Enfrentamientos». No genera cruces ni propone nada.
async function addEmptyRound() {
  const d = currentEvent;
  const { ok, d: r } = await apiSend(`/api/events/${d.id}/rounds/add`, "POST", {});
  if (!ok) { wikiToast(r.error || r.detail || "No se pudo crear la ronda", "err"); return; }
  wikiToast(`Ronda ${r.round_count} creada — elige su modo y mapa`, "ok");
  await openEvent(d.id);
}
/* ----- Solicitud de cambio de modo/mapa (con votación de participantes) ----- */
let roundChangeRn = null;
function openRoundChange(rn) {
  roundChangeRn = rn;
  const d = currentEvent;
  const rk = ((d.settings || {}).round_maps || {})[rn] || ((d.settings || {}).round_maps || {})[String(rn)] || {};
  fillModeMap("rchg-mode", "rchg-map", rk.mode || "", rk.map || "");
  $("rchg-reason").value = "";
  openEvModal("event-roundchange-modal");
}
async function submitRoundChange() {
  const d = currentEvent, mode = $("rchg-mode").value, mp = $("rchg-map").value, reason = $("rchg-reason").value.trim();
  if (!reason) { wikiToast("Explica el motivo del cambio (los jugadores lo verán)", "err"); return; }
  const { ok, d: r } = await apiSend(`/api/events/${d.id}/rounds/${roundChangeRn}/change-request`, "POST", { mode, map: mp, reason });
  if (!ok) { wikiToast(r.error || r.detail || "No se pudo enviar", "err"); return; }
  closeEvModal("event-roundchange-modal");
  wikiToast(`Solicitud enviada a ${r.voters} participante(s)`, "ok");
  await openEvent(d.id);
}
async function voteRoundChange(rn, vote) {
  const d = currentEvent;
  const { ok, d: r } = await apiSend(`/api/events/${d.id}/rounds/${rn}/change-vote`, "POST", { vote });
  if (!ok) { wikiToast(r.error || r.detail || "No se pudo votar", "err"); return; }
  wikiToast(r.status === "applied" ? "Cambio aplicado: se juega el nuevo modo/mapa"
    : r.status === "blocked" ? "Te has opuesto; se mantiene el modo/mapa" : "Voto registrado", "ok");
  await openEvent(d.id);
}
function mapsRevealed(s, kind) {
  if (s.maps_public !== false) return true;
  const dt = kind === "mode" ? s.reveal_mode_date : s.reveal_map_date;
  return dt ? (new Date(dt) <= new Date()) : false;
}
function modeChip(mode) {
  if (!mode) return "";
  const ic = bsModeIcon(mode);
  return `<span class="mm-chip">${ic ? `<img src="${esc(ic)}" alt="" onerror="this.remove()">` : ""}${esc(mode)}</span>`;
}
function mapChip(mp) {
  return mp ? `<a class="mm-map" href="${esc(mapImageSearch(mp))}" target="_blank" rel="noopener" title="Ver imagen del mapa">${esc(mapNameEs(mp))}</a>` : "";
}
function renderModeMap(mode, mp, s, asBlock) {
  const showMode = mapsRevealed(s, "mode"), showMap = mapsRevealed(s, "map");
  if (!showMode && !showMap) {
    const txt = `<span class="evd-reveal">Por revelar</span>`;
    return asBlock ? `<p>${txt}</p>` : txt;
  }
  const parts = [];
  if (showMode && mode) parts.push(modeChip(mode));
  if (showMap && mp) parts.push(mapChip(mp));
  const inner = parts.length ? parts.join(`<span class="mm-sep">·</span>`) : `<span class="evd-muted">Sin asignar</span>`;
  return asBlock ? `<p class="mm-line">${inner}</p>` : inner;
}
const EV_FORMAT_RULES = {
  swiss: "Sistema suizo: cada ronda empareja a jugadores con puntuación similar. No hay eliminación; se juega un número fijo de rondas y gana quien más puntos sume.",
  mcmahon: "Sistema McMahon: como el suizo, pero con puntos iniciales según el nivel o categoría de cada jugador. Ideal para muchos participantes de niveles dispares.",
  roundrobin: "Todos contra todos: cada participante se enfrenta a todos los demás. Gana quien más puntos acumule. Puede jugarse a una o varias vueltas.",
  single_elim: "Eliminación directa: quien pierde queda fuera. Cuadro de rondas hasta la final.",
  double_elim: "Doble eliminación: hay cuadro de ganadores y de perdedores; te eliminan tras dos derrotas.",
  free: "Liga libre: los jugadores concretan sus enfrentamientos libremente dentro del rango de fechas; se acumulan puntos por victoria.",
};
const VIS_HINTS = {
  public: "Cualquiera puede apuntarse al instante.",
  acceptance: "Los jugadores solicitan plaza y tú las aceptas.",
  private: "Solo con contraseña; puedes exigir además tu confirmación.",
};

let evMyEvents = [], evMineFilter = "all", currentEvent = null, evLeaguesInit = false, evPosterUrl = null, editingMatch = null;

function openEvModal(id) { $(id).classList.add("open"); }
function closeEvModal(id) { $(id).classList.remove("open"); }

function loadLeagues() {
  if (!evLeaguesInit) { initLeaguesUI(); evLeaguesInit = true; }
  // Si no hay una página de detalle abierta, mostrar siempre el listado (evita quedarse
  // en una ficha vacía al volver a la sección).
  const page = $("event-page");
  if (eventView !== "page" || !page || !page.innerHTML.trim()) { eventView = "modal"; showEventPageView(false); }
  loadMyEvents(); loadBoard();
}
function initLeaguesUI() {
  const opts = EV_LANGS.map((l) => `<option value="${l.c}">${esc(l.n)}</option>`).join("");
  $("ec-lang").innerHTML = opts; $("ee-lang").innerHTML = opts; $("ec-lang").value = "es";
  $("lang-filter-chks").innerHTML = EV_LANGS.map((l) =>
    `<label class="chk"><input type="checkbox" value="${l.c}" data-fil="langs" checked onchange="loadBoard()"><span>${esc(l.n)}</span></label>`).join("");
  initSegToggle("ec-kind"); initSegToggle("ec-mode");
  initSegToggle("ec-vis", (v) => { $("ec-vis-hint").textContent = VIS_HINTS[v] || ""; });
  initSegToggle("em-res-bo1");
  initSegToggle("em-multi-seg", emMultiSeg);
  // Cierra cualquier dropdown de filtro al pulsar fuera (dentro no se cierra).
  document.addEventListener("click", (e) => { if (!e.target.closest(".filter-dd")) closeAllFilterDD(); });
  updateFilterCounts();
}
function initSegToggle(id, onChange) {
  $(id).querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    $(id).querySelectorAll("button").forEach((x) => x.classList.remove("active"));
    b.classList.add("active"); if (onChange) onChange(b.dataset.v);
  }));
}
function segValue(id) { const a = $(id).querySelector("button.active"); return a ? a.dataset.v : null; }

/* ----- Crear evento ----- */
function openCreateEvent() {
  $("ec-name").value = ""; $("ec-lang").value = "es";
  ["ec-kind", "ec-mode", "ec-vis"].forEach((id) =>
    $(id).querySelectorAll("button").forEach((x, i) => x.classList.toggle("active", i === 0)));
  $("ec-vis-hint").textContent = VIS_HINTS.public;
  openEvModal("event-create-modal");
}
async function submitCreateEvent() {
  const name = $("ec-name").value.trim();
  if (!name) { wikiToast("Pon un nombre al evento", "err"); return; }
  const body = { name, kind: segValue("ec-kind"), mode: segValue("ec-mode"), visibility: segValue("ec-vis"), language: $("ec-lang").value };
  const { ok, d } = await apiSend("/api/events", "POST", body);
  if (!ok) { wikiToast(d.error || d.detail || "No se pudo crear", "err"); return; }
  closeEvModal("event-create-modal"); wikiToast("Evento creado", "ok");
  await loadMyEvents(); openEvent(d.id);
}

/* ----- Mis eventos ----- */
async function loadMyEvents() {
  try { const d = await getJSON("/api/events/mine"); evMyEvents = d.events || []; }
  catch (e) { evMyEvents = []; }
  renderMyEvents();
}
function filterMine(f) {
  evMineFilter = f;
  document.querySelectorAll(".lgtab").forEach((t) => t.classList.toggle("active", t.dataset.lgtab === f));
  renderMyEvents();
}
function renderMyEvents() {
  const grid = $("my-events-grid"), empty = $("my-events-empty");
  let list = evMyEvents;
  if (evMineFilter !== "all") list = list.filter((e) => e.relation === evMineFilter);
  if (!list.length) { grid.innerHTML = ""; empty.style.display = "block"; return; }
  empty.style.display = "none";
  grid.innerHTML = list.map((e) => eventCardHTML(e, true)).join("");
}

/* ----- Tablón ----- */
function filterValues(name) {
  return Array.from(document.querySelectorAll(`input[data-fil="${name}"]:checked`)).map((c) => c.value);
}
async function loadBoard() {
  updateFilterCounts();
  const types = filterValues("types"), acc = filterValues("acceptance"), langs = filterValues("langs");
  const grid = $("board-events-grid"), empty = $("board-events-empty");
  // Si se ha desmarcado por completo alguna categoría, ningún evento puede coincidir.
  if (!types.length || !acc.length || !langs.length) {
    grid.innerHTML = ""; empty.textContent = "Marca al menos una opción en cada filtro (tipo, aceptación e idioma) para ver eventos.";
    empty.style.display = "block"; return;
  }
  const qs = new URLSearchParams();
  qs.set("types", types.join(",")); qs.set("acceptance", acc.join(",")); qs.set("langs", langs.join(","));
  try {
    const d = await getJSON("/api/events/board?" + qs.toString());
    const list = d.events || [];
    if (!list.length) { grid.innerHTML = ""; empty.textContent = "No hay eventos que coincidan con los filtros."; empty.style.display = "block"; return; }
    empty.style.display = "none";
    grid.innerHTML = list.map((e) => eventCardHTML(e, false)).join("");
  } catch (e) { grid.innerHTML = ""; empty.style.display = "block"; }
}
function setGroupFilters(group, state) {
  document.querySelectorAll(`input[data-fil="${group}"]`).forEach((c) => { c.checked = state; });
  updateFilterCounts(); loadBoard();
}
function clearFilters() {
  document.querySelectorAll("input[data-fil]").forEach((c) => { c.checked = true; });
  closeAllFilterDD(); updateFilterCounts(); loadBoard();
}
function toggleFilterDD(ev, group) {
  ev.stopPropagation();
  const panel = $("ddp-" + group), wasOpen = panel.classList.contains("open");
  closeAllFilterDD();
  if (!wasOpen) { panel.classList.add("open"); panel.closest(".filter-dd").querySelector(".filter-dd-trigger").classList.add("active"); }
}
function closeAllFilterDD() {
  document.querySelectorAll(".filter-dd-panel.open").forEach((p) => p.classList.remove("open"));
  document.querySelectorAll(".filter-dd-trigger.active").forEach((t) => t.classList.remove("active"));
}
function updateFilterCounts() {
  ["types", "acceptance", "langs"].forEach((g) => {
    const el = $("ddc-" + g); if (!el) return;
    const all = document.querySelectorAll(`input[data-fil="${g}"]`).length;
    const sel = document.querySelectorAll(`input[data-fil="${g}"]:checked`).length;
    el.textContent = !all ? "" : (sel === all ? "Todos" : (sel === 0 ? "Ninguno" : String(sel)));
  });
}

/* ----- Tarjeta ----- */
function relationLabel(r) { return { owner: "Organizas", participant: "Participas", follower: "Sigues" }[r] || ""; }
function eventCardHTML(e, showRelation) {
  const poster = e.poster_url ? `style="background-image:url('${esc(e.poster_url)}')"` : "";
  let relHTML = "";
  if (showRelation && e.relation) {
    relHTML = `<span class="ec-relation ${e.relation}">${relationLabel(e.relation)}</span>`;
    if (e.relation === "owner" && e.pending) relHTML += `<span class="ec-pending-dot">${e.pending}</span>`;
  }
  return `<div class="event-card" onclick="openEvent(${e.id})">
    <div class="ec-poster" ${poster}>
      <span class="ec-kindtag">${EV_KIND_LABEL[e.kind] || ""}</span>
      <span class="ec-vistag ${e.visibility}">${EV_VIS_LABEL[e.visibility] || ""}</span>
    </div>
    <div class="ec-body">
      <div class="ec-name">${esc(e.name)}</div>
      <div class="ec-sub"><span>${EV_MODE_LABEL[e.mode] || ""}</span><span>${esc(evLangName(e.language))}</span>${e.status && e.status !== "open" ? `<span>${EV_STATUS_LABEL[e.status] || ""}</span>` : ""}</div>
      <div class="ec-foot">
        <span class="ec-chip">👥 ${e.participants || 0}/${e.max_participants || 12}</span>
        <span class="ec-chip">★ ${e.followers || 0}</span>
        <span style="display:flex;gap:5px;align-items:center">${relHTML}</span>
      </div>
    </div>
  </div>`;
}

/* ----- Ficha de evento ----- */
function num(v, dflt) { const n = parseInt(v, 10); return isNaN(n) ? dflt : n; }
function fmtDateRange(a, b) {
  const f = (x) => { if (!x) return ""; const p = String(x).split("-"); return p.length === 3 ? `${p[2]}/${p[1]}/${p[0]}` : x; };
  return a && b ? `${f(a)} – ${f(b)}` : f(a || b);
}
function statBox(k, v) { return `<div class="evd-stat"><div class="evd-stat-k">${esc(k)}</div><div class="evd-stat-v">${esc(v)}</div></div>`; }

// Vista activa del evento: "modal" (ficha rápida) o "page" (página de detalle a pantalla completa).
// openEvent() refresca la que esté activa, para que las acciones (resultados, equipos, rondas…)
// recarguen la página cuando estás en ella en lugar de reabrir el modal por encima.
let eventView = "modal";
async function openEvent(eid) {
  try {
    const d = await getJSON("/api/events/" + eid);
    if (d.error || d.detail) { wikiToast(d.error || d.detail, "err"); return; }
    await loadBsModes();
    currentEvent = d;
    if (eventView === "page" && $("event-page") && $("event-page").style.display !== "none") {
      renderEventPage(d);
    } else {
      eventView = "modal"; renderEventDetail(d); openEvModal("event-detail-modal");
    }
  } catch (e) { /* 401 gestionado por getJSON */ }
}
// ¿Está activo AHORA el detector automático para este evento? (no finalizado y dentro de la
// ventana de fechas; sin fechas = siempre activo). Espeja app.main._active_event_ids.
function detectorActive(d) {
  if (!d || d.status === "finished") return false;
  const now = Date.now();
  const s = d.date_start ? Date.parse(d.date_start) : NaN;
  const e = d.date_end ? Date.parse(d.date_end) : NaN;
  if (!isNaN(s) && now < s) return false;
  if (!isNaN(e) && now > e) return false;
  return true;
}
function eventBadges(d) {
  const act = detectorActive(d);
  return [
    `<span class="evd-badge">${EV_KIND_LABEL[d.kind] || ""}</span>`,
    `<span class="evd-badge">${EV_MODE_LABEL[d.mode] || ""}</span>`,
    `<span class="evd-badge vis-${d.visibility}">${EV_VIS_LABEL[d.visibility] || ""}</span>`,
    `<span class="evd-badge">${esc(evLangName(d.language))}</span>`,
    (d.status && d.status !== "open") ? `<span class="evd-badge">${EV_STATUS_LABEL[d.status] || ""}</span>` : "",
    `<span class="evd-badge detbadge ${act ? "on" : "off"}" title="Cada pocos minutos el sistema cruza las partidas pendientes con las amistosas de los participantes mientras el evento está en curso.">🔄 Detección automática de resultados: ${act ? "activa" : "inactiva"}</span>`,
  ].join("");
}
// Cuerpo reutilizable de la ficha (estadísticas, descripción, mapas por ronda, solicitudes,
// participantes, equipos y resultados). Lo usan tanto el modal como la página de detalle.
function eventBodyHTML(d, skipMeta) {
  const s = d.settings || {};
  const stats = [];
  stats.push(statBox("Participantes", `${d.participants || 0} / ${d.max_participants || 12}`));
  stats.push(statBox("Seguidores", String(d.followers || 0)));
  if (d.format) stats.push(statBox("Formato", EV_FORMAT_LABEL[d.format] || d.format));
  stats.push(statBox("Enfrentamiento", EV_MATCH_LABEL[d.match_type] || "A 1 combate"));
  if (d.date_start || d.date_end) stats.push(statBox("Fechas", fmtDateRange(d.date_start, d.date_end)));

  let mapsBlock = "";
  {
    const ms = d.matches || [];
    const gen = ms.length ? Math.max.apply(null, ms.map((m) => m.round || 1)) : 0;
    // Nº de rondas = las creadas con «+» (round_count); en ligas, las vueltas.
    const roundByRound = ["swiss", "mcmahon", "random_teams"].includes(d.format);
    // El flujo de «+» (crear rondas vacías) está disponible en formatos por ronda y en ligas/
    // eventos sin formato; en esos casos las rondas creadas (round_count) SÍ deben mostrarse.
    const canAddRounds = roundByRound || !d.format;
    const cfg = (s.round_count || s.rounds || 0);
    const perRoundN = canAddRounds ? Math.max(cfg, gen) : gen;
    const head = "Modos y mapas por ronda";
    const summary = d.is_owner
      ? `<p class="evd-muted">Crea rondas con «+» y elige sus modos y mapas (marca varios o ninguno). Luego, en «Enfrentamientos», pulsa «Generar ronda»: si dejaste el modo/mapa sin fijar, se propone uno al azar (aprobado automáticamente a las 24 h si nadie responde).</p>`
      : "";
    let rows = "";
    for (let rn = 1; rn <= perRoundN; rn++) {
      const rk = (s.round_maps || {})[rn] || (s.round_maps || {})[String(rn)] || {};
      const fm = ms.find((m) => (m.round || 1) === rn && (m.mode || m.map));
      const generated = ms.some((m) => (m.round || 1) === rn);
      if (d.is_owner && !generated) {                 // organizador, ronda sin generar: elegir permitidos
        rows += `<div class="evd-rmm evd-rmm-edit">${roundPickHTML(rn, rk)}</div>`;
      } else if (generated) {                          // ronda generada: resuelto + confirmar/votar
        rows += `<div class="evd-rmm">${roundResolvedHTML(rn, rk, d)}</div>`;
      } else {                                          // no organizador, ronda aún sin generar
        const curMode = rk.mode || (fm && fm.mode) || "", curMap = rk.map || (fm && fm.map) || "";
        rows += `<div class="evd-rmm">${roundStaticRows(rn, curMode, curMap, s)}</div>`;
      }
    }
    const list = perRoundN ? `<div class="evd-rmm-list">${rows}</div>` : (d.is_owner ? "" : `<p class="evd-muted">Aún no hay rondas.</p>`);
    const addRound = (d.is_owner && canAddRounds) ? `<button class="evd-addround" onclick="addEmptyRound()" title="Crear una ronda vacía (elegirás su modo y mapa; luego la generas en «Enfrentamientos»)">+</button>` : "";
    mapsBlock = `<div class="evd-section"><h4 class="evd-h4-row">${head}${addRound}</h4>${summary}${list}</div>`;
  }

  const parts = d.participants_list || [];
  const partItems = parts.length ? parts.map((p) => {
    const team = (d.mode === "teams" && p.team_name) ? `<span class="evd-team">${esc(p.team_name)}</span>` : "";
    const rm = d.is_owner ? `<button class="evd-x" onclick="removeParticipant(${p.id})" title="Quitar">✕</button>` : "";
    return `<div class="evd-part"><span class="evd-part-name">${esc(p.player_name || p.player_tag)}${team}</span><span class="evd-part-tag">${esc(p.player_tag)}</span>${rm}</div>`;
  }).join("") : `<p class="evd-muted">Aún no hay participantes.</p>`;
  const partsLimited = parts.length > 10;
  const partsSection = `<div class="evd-section"><h4>Participantes (${parts.length}/${d.max_participants || 12})</h4>
    <div class="evd-parts${partsLimited ? " limited" : ""}">${partItems}</div>
    ${partsLimited ? `<button class="evp-seeall" data-more="Ver lista completa (${parts.length})" onclick="evToggleList(this)">Ver lista completa (${parts.length})</button>` : ""}</div>`;
  const standSection = eventStandingsHTML(d);
  // Participantes y Clasificación en dos columnas (si hay clasificación); si no, participantes solo.
  const partsAndStand = standSection ? `<div class="evp-cols2">${partsSection}${standSection}</div>` : partsSection;

  // Organizadores: el propietario ve la lista + añadir/quitar co-organizadores (solo él gestiona
  // la lista). El resto solo la ve si hay co-organizadores (transparencia).
  const orgs = d.organizers || [];
  let orgSection = "";
  if (orgs.length || d.is_real_owner) {
    const chips = orgs.length
      ? orgs.map((o) => `<span class="evd-org"><span class="evd-org-name">@${esc(o.username)}</span>${d.is_real_owner ? `<button class="evd-x" onclick="removeOrganizer(${o.id})" title="Quitar co-organizador">✕</button>` : ""}</span>`).join("")
      : `<span class="evd-muted">Solo tú. Añade amigos como co-organizadores para gestionar juntos el evento.</span>`;
    const addBtn = d.is_real_owner ? `<button class="evd-addround" onclick="openAddOrganizer()" title="Añadir un amigo como co-organizador">+</button>` : "";
    orgSection = `<div class="evd-section"><h4 class="evd-h4-row">Organizadores${addBtn}</h4><div class="evd-orgs">${chips}</div></div>`;
  }

  let reqHTML = "";
  if (d.is_owner && d.requests && d.requests.length) {
    reqHTML = `<div class="evd-section"><h4>Solicitudes pendientes (${d.requests.length})</h4>` +
      d.requests.map((r) => `<div class="evd-req">
        <span>${esc(r.player_name || r.player_tag)} <span class="evd-part-tag">${esc(r.player_tag)}</span>${r.team_name ? " · " + esc(r.team_name) : ""} <span class="evd-muted">(${esc(r.username || "")})</span></span>
        <span class="evd-req-actions"><button class="mini-ok" onclick="acceptRequest(${r.id})">Aceptar</button><button class="mini-no" onclick="rejectRequest(${r.id})">Rechazar</button></span>
      </div>`).join("") + `</div>`;
  }

  return `
    ${d.hidden ? `<p class="evd-muted" style="margin:-8px 0 16px">🔒 Evento oculto del tablón: solo se accede con este enlace.</p>` : ""}
    ${skipMeta ? "" : `<div class="evd-stats">${stats.join("")}</div>`}
    ${skipMeta || !d.description ? "" : `<div class="evd-section"><h4>Descripción</h4><p class="evd-desc">${esc(d.description)}</p></div>`}
    ${mapsBlock}
    ${orgSection}
    ${reqHTML}
    ${partsAndStand}
    ${renderTeamsBlock(d)}
    ${renderResultsBlock(d)}`;
}
// Toggle genérico "Ver lista completa / Ver menos" (participantes y clasificación).
function evToggleList(btn) {
  const sec = btn.closest(".evd-section") || btn.parentElement;
  const el = sec.querySelector(".evd-parts, .stand-table");
  if (!el) return;
  const limited = el.classList.toggle("limited");
  btn.textContent = limited ? btn.dataset.more : "Ver menos";
}
// Clasificación como sección propia (para poder ponerla junto a Participantes).
function eventStandingsHTML(d) {
  if (d.format === "single_elim") return "";
  const teamsMode = d.mode === "teams";
  const standings = d.standings || [];
  if (!standings.length) return "";
  const rnd = d.format === "random_teams";
  const swiss = (d.format === "swiss" || d.format === "mcmahon");
  const showCups = swiss && !teamsMode;
  const noDiff = rnd;
  const limited = standings.length > 10;
  const rows = standings.map((s, i) => `<tr>
    <td>${i + 1}</td><td class="stand-name">${esc(s.name)}</td>
    <td>${s.pj}</td><td>${s.g}</td><td>${s.e}</td><td>${s.p}</td>
    ${noDiff ? "" : `<td>${s.dif > 0 ? "+" + s.dif : s.dif}</td>`}${showCups ? `<td class="stand-cups">${s.seed_cups != null ? Number(s.seed_cups).toLocaleString("es-ES") : "—"}</td>` : ""}<td class="stand-pts">${s.pts}</td>
  </tr>`).join("");
  return `<div class="evd-section"><h4>Clasificación${rnd ? " (individual)" : ""}</h4>
    <div class="table-scroll"><table class="stand-table${limited ? " limited" : ""}">
      <thead><tr><th>#</th><th>${teamsMode ? "Equipo" : "Jugador"}</th><th>PJ</th><th>G</th><th>E</th><th>P</th>${noDiff ? "" : "<th>Dif</th>"}${showCups ? "<th>Copas</th>" : ""}<th>Pts</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    ${limited ? `<button class="evp-seeall" data-more="Ver lista completa (${standings.length})" onclick="evToggleList(this)">Ver lista completa (${standings.length})</button>` : ""}
    ${d.format === "mcmahon" ? `<p class="ec-hint" style="margin-top:8px">McMahon: los puntos incluyen la ventaja inicial por copas (snapshot al emparejar la 1.ª ronda).</p>` : ""}</div>`;
}
function renderEventDetail(d) {
  const actions = [];
  if (d.is_owner) {
    actions.push(`<button class="btn" onclick="openEventPageEditFromModal()">✎ Editar evento</button>`);
    actions.push(`<button class="ghost" onclick="openEventPageFromModal()">🔎 Ver en detalle</button>`);
    if ((d.participants || 0) < (d.max_participants || 12)) actions.push(`<button class="ghost" onclick="openInvite()">+ Añadir jugador</button>`);
  } else {
    actions.push(d.is_following
      ? `<button class="ghost" onclick="unfollowEvent()">★ Siguiendo</button>`
      : `<button class="ghost" onclick="followEvent()">☆ Seguir</button>`);
    if (d.relation === "participant") actions.push(`<span class="evd-joined">✓ Ya participas</span>`);
    else if (d.my_request) actions.push(`<span class="evd-joined pending">⏳ Solicitud pendiente</span>`);
    else if (!d.status || d.status === "open") {
      actions.push(`<button class="btn" onclick="openJoin()">${d.visibility === "acceptance" ? "Solicitar plaza" : "Apuntarse"}</button>`);
    }
    actions.push(`<button class="ghost" onclick="openEventPageFromModal()">🔎 Ver en detalle</button>`);
  }
  actions.push(`<button class="ghost" onclick="shareEvent(${d.id}, ${esc(JSON.stringify(d.name || ''))})" title="Compartir en redes">📣 Compartir</button>`);

  $("event-detail-body").innerHTML = `
    ${d.poster_url ? `<div class="evd-poster" style="background-image:url('${esc(d.poster_url)}')"></div>` : ""}
    <h2 class="evd-title">${esc(d.name)}</h2>
    <div class="evd-badges">${eventBadges(d)}</div>
    <div class="evd-actions">${actions.join("")}</div>
    ${eventBodyHTML(d)}`;
  if (d.is_owner) populateRoundSelectors(d);
}
// Los selectores de ronda ahora se renderizan directamente en el HTML (checkbox por ronda),
// así que no hace falta rellenarlos después. Se mantiene por compatibilidad de llamadas.
function populateRoundSelectors(_d) { /* no-op: el HTML ya trae los desplegables */ }

/* ----- Fase 1: clasificación y enfrentamientos ----- */
function renderTeamsBlock(d) {
  if (d.mode !== "teams") return "";
  const teams = d.teams || [], parts = d.participants_list || [];
  const byTeam = {}; teams.forEach((t) => { byTeam[t.id] = []; });
  const noTeam = [];
  parts.forEach((p) => { if (p.team_id && byTeam[p.team_id]) byTeam[p.team_id].push(p); else noTeam.push(p); });
  const teamOpts = `<option value="">Sin equipo</option>` + teams.map((t) => `<option value="${t.id}">${esc(t.name || ("Equipo " + t.id))}</option>`).join("");
  const cards = teams.map((t) => {
    const mem = byTeam[t.id] || [];
    const logo = t.logo_url
      ? `<img class="team-logo" src="${esc(t.logo_url)}" alt="">`
      : `<div class="team-logo team-logo-ph">${esc((t.name || "E").trim().slice(0, 1).toUpperCase())}</div>`;
    const ctrl = d.is_owner ? `<div class="team-card-ctrl"><button class="evd-x" title="Editar" onclick="openTeamModal(${t.id})">✎</button><button class="evd-x" title="Borrar" onclick="deleteTeam(${t.id})">✕</button></div>` : "";
    const mems = mem.length
      ? mem.map((p) => `<span class="team-mem">${esc(p.player_name || p.player_tag)}${d.is_owner ? `<button class="team-mem-x" title="Sacar del equipo" onclick="assignTeam(${p.id}, '')">×</button>` : ""}</span>`).join("")
      : `<span class="evd-muted" style="font-size:12px">Sin jugadores</span>`;
    return `<div class="team-card"><div class="team-card-h">${logo}<div class="team-card-name">${esc(t.name || ("Equipo " + t.id))}<span class="team-card-count">${mem.length}</span></div>${ctrl}</div><div class="team-mems">${mems}</div></div>`;
  }).join("");
  let unassigned = "";
  if (d.is_owner && noTeam.length) {
    unassigned = `<div class="team-unassigned"><div class="team-unassigned-h">Sin equipo (${noTeam.length})</div>${noTeam.map((p) => `<div class="team-ua-row"><span>${esc(p.player_name || p.player_tag)} <span class="evd-part-tag">${esc(p.player_tag)}</span></span><select class="team-assign-sel" onchange="assignTeam(${p.id}, this.value)">${teamOpts}</select></div>`).join("")}</div>`;
  }
  const bar = d.is_owner ? `<div class="evd-mbar"><button class="btn" onclick="openTeamModal()">+ Crear equipo</button></div>` : "";
  const empty = !teams.length ? `<p class="evd-muted">Aún no hay equipos.${d.is_owner ? " Crea equipos y asígnales jugadores." : ""}</p>` : "";
  return `<div class="evd-section"><h4>Equipos y plantillas (${teams.length})</h4>${bar}${empty}<div class="team-grid">${cards}</div>${unassigned}</div>`;
}
let editingTeam = null, teamLogoUrl = null;
function openTeamModal(tid) {
  const d = currentEvent;
  editingTeam = tid ? (d.teams || []).find((t) => t.id === tid) : null;
  $("et-title").textContent = editingTeam ? "Editar equipo" : "Nuevo equipo";
  $("et-name").value = editingTeam ? (editingTeam.name || "") : "";
  teamLogoUrl = editingTeam ? (editingTeam.logo_url || null) : null;
  renderTeamLogoPreview();
  openEvModal("event-team-modal");
}
function renderTeamLogoPreview() {
  const img = $("et-logo-preview"), clr = $("et-logo-clear");
  if (teamLogoUrl) { img.src = teamLogoUrl; img.style.display = ""; clr.style.display = ""; }
  else { img.style.display = "none"; clr.style.display = "none"; }
}
async function uploadTeamLogo(input) {
  const file = input.files && input.files[0]; input.value = "";
  if (!file) return;
  try {
    const dataUrl = await new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(file); });
    const { ok, d } = await apiSend("/api/events/upload-image", "POST", { data: dataUrl, mime: file.type });
    if (ok && d.url) { teamLogoUrl = d.url; renderTeamLogoPreview(); } else wikiToast((d && d.error) || "No se pudo subir el logo", "err");
  } catch (e) { wikiToast("No se pudo subir el logo", "err"); }
}
function clearTeamLogo() { teamLogoUrl = null; renderTeamLogoPreview(); }
async function submitTeam() {
  const d = currentEvent, name = $("et-name").value.trim();
  if (!name) { wikiToast("Pon un nombre al equipo", "err"); return; }
  const body = { name, logo_url: teamLogoUrl || "" };
  const r = editingTeam
    ? await apiSend(`/api/events/${d.id}/teams/${editingTeam.id}`, "PATCH", body)
    : await apiSend(`/api/events/${d.id}/teams`, "POST", body);
  if (!r.ok) { wikiToast((r.d && (r.d.error || r.d.detail)) || "No se pudo guardar", "err"); return; }
  closeEvModal("event-team-modal"); wikiToast(editingTeam ? "Equipo actualizado" : "Equipo creado", "ok"); await openEvent(d.id);
}
async function deleteTeam(tid) {
  const d = currentEvent;
  if (!confirm("¿Borrar este equipo? Sus jugadores quedarán sin equipo.")) return;
  const { ok, d: r } = await apiSend(`/api/events/${d.id}/teams/${tid}`, "DELETE");
  if (!ok) { wikiToast((r && r.error) || "No se pudo borrar", "err"); return; }
  wikiToast("Equipo borrado", "ok"); await openEvent(d.id);
}
async function assignTeam(pid, teamId) {
  const d = currentEvent;
  const { ok, d: r } = await apiSend(`/api/events/${d.id}/participants/${pid}/team`, "POST", { team_id: teamId || null });
  if (!ok) { wikiToast((r && r.error) || "No se pudo asignar", "err"); return; }
  await openEvent(d.id);
}
function renderResultsBlock(d) {
  if (d.format === "single_elim") return renderBracket(d);
  const teamsMode = d.mode === "teams";
  const matches = d.matches || [];
  const rrOk = (!d.format || d.format === "roundrobin" || d.format === "free");
  const swiss = (d.format === "swiss" || d.format === "mcmahon");
  const rnd = (d.format === "random_teams");
  const roundByRound = swiss || rnd;
  const roundWord = roundByRound ? "Ronda" : "Jornada";
  let ownerBar = "";
  if (d.is_owner) {
    if (roundByRound) {
      const allPlayed = matches.length > 0 && matches.every((m) => m.status === "played");
      const nextN = matches.length ? Math.max.apply(null, matches.map((m) => m.round || 1)) + 1 : 1;
      let gen;
      if (!matches.length) gen = `<button class="btn" onclick="generateMatches()">⚙ Generar ronda 1</button>`;
      else if (allPlayed) gen = `<button class="btn" onclick="generateMatches()">⚙ Generar ronda ${nextN}</button>`;
      else gen = `<span class="ec-hint" style="align-self:center">Completa los resultados de la ronda actual para generar la siguiente.</span>`;
      const restart = matches.length ? `<button class="ghost" onclick="restartPairings()">↻ Reiniciar<span class="mbar-extra"> rondas</span></button>` : "";
      const closeBtn = (matches.length && !allPlayed) ? `<button class="ghost" onclick="closeRound()">⛌ Cerrar ronda<span class="mbar-extra"> actual</span></button>` : "";
      ownerBar = `<div class="evd-mbar">${gen}${closeBtn}${restart}</div>`;
      if (rnd) ownerBar += `<p class="ec-hint" style="margin:-2px 0 12px">Cada ronda forma equipos al azar del tamaño del modo (3 en 3v3, 2 en dúo, 1 en Duelos); los sobrantes descansan esa ronda.</p>`;
    } else if (rrOk) {
      ownerBar = `<div class="evd-mbar"><button class="btn" onclick="openMatchModal()">+ Añadir<span class="mbar-extra"> enfrentamiento</span></button><button class="ghost" onclick="generateMatches()">⚙ Generar cruces<span class="mbar-extra"> (todos contra todos)</span></button></div>`;
    } else {
      ownerBar = `<div class="evd-mbar"><button class="btn" onclick="openMatchModal()">+ Añadir<span class="mbar-extra"> enfrentamiento</span></button></div><div class="ec-hint" style="margin-bottom:14px">El emparejamiento automático para <b>${esc(EV_FORMAT_LABEL[d.format] || d.format)}</b> llegará en una fase posterior; de momento añade los enfrentamientos a mano.</div>`;
    }
    // El poller cruza cada hora las partidas pendientes con las amistosas de los participantes;
    // además el organizador puede lanzarlo YA con este botón (el estado activo/inactivo se ve
    // arriba, en las etiquetas del evento).
    if (matches.length && matches.some((m) => m.status !== "played")) {
      const det = `<button class="ghost" onclick="detectResults()" title="Fuerza ahora el cruce de las partidas pendientes con las amistosas de los participantes (además del automático cada pocos minutos).">🔎 Detectar resultados<span class="mbar-extra"> ahora</span></button>`;
      ownerBar = ownerBar.includes("evd-mbar") ? ownerBar.replace("</div>", det + "</div>") : `<div class="evd-mbar">${det}</div>`;
    }
    // Fase 6: resumen IA para seguidores cuando ya hay resultados
    if (matches.some((m) => m.status === "played")) {
      const sm = `<button class="ghost" onclick="sendSummary()" title="Claude redacta un resumen breve de los resultados y la clasificación y lo envía como aviso a seguidores y apuntados.">📣 Resumen<span class="mbar-extra"> para seguidores</span></button>`;
      ownerBar = ownerBar.includes("evd-mbar") ? ownerBar.replace("</div>", sm + "</div>") : `<div class="evd-mbar">${sm}</div>`;
    }
  }
  let matchesHTML;
  if (!matches.length) {
    const hint = roundByRound ? " Genera la primera ronda con los participantes actuales." : (rrOk ? " Añádelos a mano o genera todos los cruces con los participantes." : " Añádelos a mano.");
    matchesHTML = `<div class="evd-empty-table">Aún no hay enfrentamientos.${d.is_owner ? hint : " El organizador aún no los ha publicado."}</div>`;
  } else {
    const byRound = {};
    matches.forEach((m) => { (byRound[m.round] = byRound[m.round] || []).push(m); });
    // Rondas de la MÁS RECIENTE a la más antigua (la ronda 1 queda al final del torneo).
    // Cada ronda es un acordeón; sus enfrentamientos van en una fila que se desliza
    // (carrusel) si no caben, sin ocupar todo el ancho cada uno.
    const nums = Object.keys(byRound).map(Number).sort((a, b) => b - a);
    matchesHTML = nums.map((r) =>
      `<details class="evd-round" open><summary class="evd-round-h">${roundWord} ${r}</summary>
        <div class="evd-round-cards">${byRound[r].map((m) => matchRowHTML(m, d)).join("")}</div></details>`).join("");
  }
  return `<div class="evd-section"><h4>Enfrentamientos</h4>${ownerBar}${matchesHTML}</div>`;
}
function bracketRoundLabel(n) {
  return n === 1 ? "Final" : n === 2 ? "Semifinales" : n === 4 ? "Cuartos de final" : n === 8 ? "Octavos" : n === 16 ? "Dieciseisavos" : ("Ronda de " + n * 2);
}
function renderBracket(d) {
  const matches = d.matches || [];
  let ownerBar = "";
  if (d.is_owner) {
    ownerBar = `<div class="evd-mbar"><button class="btn" onclick="generateMatches()">${matches.length ? "↻ Regenerar<span class='mbar-extra'> cuadro</span>" : "⚙ Generar<span class='mbar-extra'> cuadro de eliminación</span>"}</button></div>`;
  }
  if (!matches.length) {
    return `<div class="evd-section"><h4>Cuadro de eliminación</h4>${ownerBar}
      <div class="evd-empty-table">Aún no hay cuadro.${d.is_owner ? " Genéralo con los participantes actuales." : " El organizador aún no lo ha generado."}</div></div>`;
  }
  // Árbol de enfrentamientos: cada ronda es una columna; los partidos se agrupan en parejas y
  // se dibujan las "llaves" (líneas) que llevan de cada pareja a su partido de la ronda siguiente,
  // hasta la final.
  const byRound = {};
  matches.forEach((m) => { (byRound[m.round] = byRound[m.round] || []).push(m); });
  const roundNums = Object.keys(byRound).map(Number).sort((a, b) => a - b);
  const cols = roundNums.map((r, ri) => {
    const ms = byRound[r].sort((a, b) => (a.bracket_pos || 0) - (b.bracket_pos || 0));
    const last = ri === roundNums.length - 1;
    let cells = "";
    for (let i = 0; i < ms.length; i += 2) {
      const pair = ms.slice(i, i + 2);
      cells += `<div class="brk-pair${pair.length === 1 ? " solo" : ""}">${pair.map((m) => `<div class="brk-cell">${matchRowHTML(m, d)}</div>`).join("")}</div>`;
    }
    return `<div class="brk-round${last ? " final" : ""}"><div class="brk-round-h">${bracketRoundLabel(ms.length)}</div><div class="brk-round-body">${cells}</div></div>`;
  }).join("");
  return `<div class="evd-section"><h4>Cuadro de eliminación</h4>${ownerBar}<div class="brk-wrap">${cols}</div></div>`;
}
// Clase del lado de un enfrentamiento jugado: verde (ganador) / rojo (perdedor).
function mSideClass(played, winner, side) {
  if (!played || winner === "void") return "";
  if (winner === side) return "win";
  if (winner === "a" || winner === "b") return "loss";
  return "";  // empate
}
function matchRowHTML(m, d) {
  const teamsMode = d.mode === "teams";
  const eviBadge = m.evidence_battle_id ? `<span class="m-evi" title="Resultado detectado automáticamente desde una amistosa (battlelog). Puedes editarlo o borrarlo.">🔎 auto</span>` : "";
  if (m.roster_a || m.roster_b) {  // partido de equipos aleatorios (roster por partida)
    const played0 = m.status === "played";
    const nameOf = (tag) => { const p = (d.participants_list || []).find((x) => x.player_tag === tag); return esc(p ? (p.player_name || p.player_tag) : tag); };
    const ra = (m.roster_a || []).map(nameOf).join(" · "), rb = (m.roster_b || []).map(nameOf).join(" · ");
    const mid0 = !played0 ? `<span class="m-vs">vs</span>`
      : (m.winner === "void" ? `<span class="m-void">No jugado</span>`
        : `<span class="m-score ${m.winner === "a" ? "win" : ""}">${m.score_a != null ? m.score_a : "✓"}</span>–<span class="m-score ${m.winner === "b" ? "win" : ""}">${m.score_b != null ? m.score_b : "✓"}</span>`);
    const edit0 = d.is_owner ? `<button class="m-edit" onclick="openMatchModal(${m.id})" title="Resultado">✎</button>` : "";
    const meta0 = (m.mode || m.map) ? renderModeMap(m.mode, m.map, d.settings || {}, false) : "";
    return `<div class="evd-match${played0 ? " played" : ""}">
      <div class="m-line">
        <span class="m-side ${mSideClass(played0, m.winner, "a")}">Equipo A</span>
        <span class="m-mid">${mid0}</span>
        <span class="m-side b ${mSideClass(played0, m.winner, "b")}">Equipo B</span>${edit0}
      </div>
      <div class="m-roster"><b>A:</b>&nbsp;${ra || "—"} &nbsp;·&nbsp; <b>B:</b>&nbsp;${rb || "—"}</div>
      ${(meta0 || eviBadge) ? `<div class="m-meta">${meta0}${eviBadge}</div>` : ""}</div>`;
  }
  const aSet = teamsMode ? !!m.a_team : !!m.a_tag, bSet = teamsMode ? !!m.b_team : !!m.b_tag;
  const aName = aSet ? esc(teamsMode ? (m.a_team_name || "Equipo") : (m.a_name || m.a_tag)) : null;
  const bName = bSet ? esc(teamsMode ? (m.b_team_name || "Equipo") : (m.b_name || m.b_tag)) : null;
  const played = m.status === "played";
  const tbd = `<span class="m-tbd">Por determinar</span>`;
  if (played && (aSet !== bSet)) {  // bye: jugado con un solo lado presente
    return `<div class="evd-match"><div class="m-line">
      <span class="m-side win">${aName || bName}</span>
      <span class="m-mid"><span class="m-vs">bye</span></span>
      <span class="m-side b m-tbd">—</span></div></div>`;
  }
  const mid = !played ? `<span class="m-vs">vs</span>`
    : (m.winner === "void" ? `<span class="m-void">No jugado</span>`
      : `<span class="m-score ${m.winner === "a" ? "win" : ""}">${m.score_a}</span>–<span class="m-score ${m.winner === "b" ? "win" : ""}">${m.score_b}</span>`);
  const meta = (m.mode || m.map) ? renderModeMap(m.mode, m.map, d.settings || {}, false) : "";
  const edit = (d.is_owner && aSet && bSet) ? `<button class="m-edit" onclick="openMatchModal(${m.id})" title="Editar / resultado">✎</button>` : "";
  return `<div class="evd-match${played ? " played" : ""}">
    <div class="m-line">
      <span class="m-side ${mSideClass(played, m.winner, "a")}">${aName || tbd}</span>
      <span class="m-mid">${mid}</span>
      <span class="m-side b ${mSideClass(played, m.winner, "b")}">${bName || tbd}</span>
      ${edit}
    </div>${(meta || eviBadge) ? `<div class="m-meta">${meta}${eviBadge}</div>` : ""}</div>`;
}
async function openMatchModal(mid) {
  const d = currentEvent, teamsMode = d.mode === "teams", elim = d.format === "single_elim";
  editingMatch = mid ? (d.matches || []).find((m) => m.id === mid) : null;
  if (elim && !editingMatch) return;  // el cuadro no admite altas manuales
  await loadBsModes();
  const roster = !!(editingMatch && (editingMatch.roster_a || editingMatch.roster_b));
  const opts = teamsMode
    ? (d.teams || []).map((t) => `<option value="${t.id}">${esc(t.name || ("Equipo " + t.id))}</option>`).join("")
    : (d.participants_list || []).map((p) => `<option value="${esc(p.player_tag)}">${esc(p.player_name || p.player_tag)} (${esc(p.player_tag)})</option>`).join("");
  if (!elim && !roster && !opts) { wikiToast("Primero añade participantes al evento", "err"); return; }
  $("em-a").innerHTML = opts; $("em-b").innerHTML = opts;
  $("em-a-lbl").textContent = teamsMode ? "Equipo A" : "Jugador A";
  $("em-b-lbl").textContent = teamsMode ? "Equipo B" : "Jugador B";
  const isBo1 = (d.match_type || "bo1") === "bo1";
  $("em-res-bo1").style.display = isBo1 ? "flex" : "none";
  $("em-res-multi").style.display = isBo1 ? "none" : "block";
  const drawBtn = $("em-res-bo1").querySelector('[data-r="draw"]'); if (drawBtn) drawBtn.style.display = elim ? "none" : "";
  const voidBtn = $("em-res-bo1").querySelector('[data-r="void"]'); if (voidBtn) voidBtn.style.display = elim ? "none" : "";
  $("em-grid-rab").style.display = (elim || roster) ? "none" : "grid";
  $("em-fixed-wrap").style.display = elim ? "block" : "none";
  $("em-roster-wrap").style.display = roster ? "flex" : "none";
  const s = d.settings || {}, fixed = s.map_policy === "fixed";
  if (editingMatch) {
    const m = editingMatch;
    $("em-title").textContent = (elim || roster) ? "Resultado del enfrentamiento" : "Editar enfrentamiento";
    $("em-round").value = m.round || 1;
    if (elim) {
      const aN = teamsMode ? (m.a_team_name || "?") : (m.a_name || m.a_tag || "?");
      const bN = teamsMode ? (m.b_team_name || "?") : (m.b_name || m.b_tag || "?");
      $("em-fixed").textContent = aN + "   vs   " + bN;
    } else if (roster) {
      const nameOf = (tag) => { const p = (d.participants_list || []).find((x) => x.player_tag === tag); return esc(p ? (p.player_name || p.player_tag) : tag); };
      $("em-roster-a").innerHTML = (m.roster_a || []).map((t) => `<span>${nameOf(t)}</span>`).join("") || "<span>—</span>";
      $("em-roster-b").innerHTML = (m.roster_b || []).map((t) => `<span>${nameOf(t)}</span>`).join("") || "<span>—</span>";
    } else {
      $("em-a").value = teamsMode ? m.a_team : m.a_tag;
      $("em-b").value = teamsMode ? m.b_team : m.b_tag;
    }
    const mm = [m.mode, m.map].filter(Boolean).join(" · ");
    $("em-mm-hint").textContent = mm ? `Modo y mapa de la ronda: ${mm} (se cambian arriba, en la lista de rondas).` : "El modo y el mapa se eligen por ronda, arriba en la ficha.";
    setEmResult(isBo1, m);
    $("em-delete").style.display = (elim || roster) ? "none" : "inline-block";
    const bothSet = teamsMode ? (!!(m.a_team || (m.roster_a || []).length) && !!(m.b_team || (m.roster_b || []).length)) : (!!m.a_tag && !!m.b_tag);
    $("em-detect").style.display = bothSet ? "inline-block" : "none";
  } else {
    $("em-title").textContent = "Nuevo enfrentamiento";
    const rounds = (d.matches || []).map((m) => m.round || 1);
    $("em-round").value = rounds.length ? Math.max.apply(null, rounds) : 1;
    $("em-mm-hint").textContent = "El modo y el mapa se eligen por ronda, arriba en la ficha.";
    setEmResult(isBo1, null);
    $("em-delete").style.display = "none";
    $("em-detect").style.display = "none";
  }
  openEvModal("event-match-modal");
}
function setEmResult(isBo1, m) {
  if (isBo1) {
    const w = m && m.status === "played" ? (m.winner || "") : "";
    $("em-res-bo1").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.r === w));
  } else {
    const played = !!(m && m.status === "played");
    const v = m && m.winner === "void" ? "void" : (played ? "score" : "");
    $("em-multi-seg").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.r === v));
    $("em-sa").value = m && m.score_a != null && m.winner !== "void" ? m.score_a : "";
    $("em-sb").value = m && m.score_b != null && m.winner !== "void" ? m.score_b : "";
    $("em-score-wrap").style.display = v === "score" ? "flex" : "none";
  }
}
function emMultiSeg() { const a = $("em-multi-seg").querySelector("button.active"); $("em-score-wrap").style.display = (a && a.dataset.r === "score") ? "flex" : "none"; }
function emResultBo1() { const a = $("em-res-bo1").querySelector("button.active"); return a ? a.dataset.r : ""; }
function emResultMulti() { const a = $("em-multi-seg").querySelector("button.active"); return a ? a.dataset.r : ""; }
async function submitMatch() {
  const d = currentEvent, teamsMode = d.mode === "teams", elim = d.format === "single_elim", isBo1 = (d.match_type || "bo1") === "bo1";
  const roster = !!(editingMatch && (editingMatch.roster_a || editingMatch.roster_b));
  let result;
  if (isBo1) {
    const w = emResultBo1();
    if (elim && w === "draw") { wikiToast("En eliminación no puede haber empate", "err"); return; }
    result = w ? { winner: w } : { clear_result: true };
  } else {
    const v = emResultMulti();
    if (v === "void") {
      result = { winner: "void" };
    } else if (v === "score") {
      if ($("em-sa").value === "" || $("em-sb").value === "") { wikiToast("Indica el marcador de ambos lados", "err"); return; }
      if (elim && Number($("em-sa").value) === Number($("em-sb").value)) { wikiToast("En eliminación no puede haber empate", "err"); return; }
      result = { score_a: $("em-sa").value, score_b: $("em-sb").value };
    } else {
      result = { clear_result: true };
    }
  }
  if (elim || roster) {  // solo resultado; el cruce está fijado y el modo/mapa es por ronda
    const { ok, d: r } = await apiSend(`/api/events/${d.id}/matches/${editingMatch.id}`, "PUT", result);
    if (!ok) { wikiToast(r.error || r.detail || "No se pudo guardar", "err"); return; }
    closeEvModal("event-match-modal"); await openEvent(d.id); return;
  }
  const a = $("em-a").value, b = $("em-b").value;
  if (!a || !b || a === b) { wikiToast("Elige dos " + (teamsMode ? "equipos" : "jugadores") + " distintos", "err"); return; }
  const base = { round: parseInt($("em-round").value, 10) || 1 };
  if (teamsMode) { base.a_team = parseInt(a, 10); base.b_team = parseInt(b, 10); } else { base.a_tag = a; base.b_tag = b; }
  if (editingMatch) {
    const { ok, d: r } = await apiSend(`/api/events/${d.id}/matches/${editingMatch.id}`, "PUT", Object.assign({}, base, result));
    if (!ok) { wikiToast(r.error || r.detail || "No se pudo guardar", "err"); return; }
  } else {
    const { ok, d: r } = await apiSend(`/api/events/${d.id}/matches`, "POST", base);
    if (!ok) { wikiToast(r.error || r.detail || "No se pudo crear", "err"); return; }
    if (r.id && !result.clear_result) await apiSend(`/api/events/${d.id}/matches/${r.id}`, "PUT", result);
  }
  closeEvModal("event-match-modal"); await openEvent(d.id);
}
async function deleteMatch() {
  if (!editingMatch) return;
  if (!confirm("¿Eliminar este enfrentamiento?")) return;
  const { ok } = await apiSend(`/api/events/${currentEvent.id}/matches/${editingMatch.id}`, "DELETE");
  if (ok) { closeEvModal("event-match-modal"); await openEvent(currentEvent.id); }
}
async function generateMatches() {
  const d = currentEvent, has = (d.matches || []).length > 0, elim = d.format === "single_elim", swiss = (d.format === "swiss" || d.format === "mcmahon"), rnd = (d.format === "random_teams");
  if (swiss || rnd) {  // suizo/McMahon/aleatorios: generar la SIGUIENTE ronda (no reemplaza)
    const nextN = has ? Math.max.apply(null, d.matches.map((m) => m.round || 1)) + 1 : 1;
    const ts = (d.settings && d.settings.team_size) || 3;
    const msg = rnd
      ? `¿Generar la ronda ${nextN} con equipos aleatorios de ${ts} jugadores?`
      : (nextN === 1 ? "¿Emparejar la ronda 1? Se tomará el snapshot de copas de cada cuenta." : `¿Emparejar la ronda ${nextN} según la clasificación actual?`);
    if (!confirm(msg)) return;
    const { ok, d: r } = await apiSend(`/api/events/${d.id}/matches/generate`, "POST", {});
    if (!ok) { wikiToast(r.error || r.detail || "No se pudo generar", "err"); return; }
    const extra = rnd ? (r.benched ? ` (${r.benched} descansan)` : "") : (r.bye ? " (con bye)" : "");
    wikiToast(`Ronda ${r.round} generada${extra}`, "ok"); await openEvent(d.id); return;
  }
  const what = elim ? "el cuadro de eliminación" : "todos los cruces (todos contra todos)";
  const msg = has ? `Esto generará ${what} y REEMPLAZARÁ lo que haya ahora. ¿Continuar?` : `¿Generar ${what} con los participantes actuales?`;
  if (!confirm(msg)) return;
  const { ok, d: r } = await apiSend(`/api/events/${d.id}/matches/generate`, "POST", { replace: has });
  if (!ok) { wikiToast(r.error || r.detail || "No se pudo generar", "err"); return; }
  wikiToast(elim ? "Cuadro generado" : `${r.created} enfrentamientos generados`, "ok"); await openEvent(d.id);
}
async function restartPairings() {
  const d = currentEvent;
  if (!confirm("Esto borrará TODAS las rondas y resultados y empezará de cero (nuevo snapshot de copas). ¿Continuar?")) return;
  const { ok, d: r } = await apiSend(`/api/events/${d.id}/matches/generate`, "POST", { replace: true });
  if (!ok) { wikiToast(r.error || r.detail || "No se pudo reiniciar", "err"); return; }
  wikiToast(`Ronda ${r.round} emparejada`, "ok"); await openEvent(d.id);
}
async function closeRound() {
  const d = currentEvent;
  if (!confirm("¿Cerrar la ronda actual? Los enfrentamientos pendientes se marcarán como NO JUGADOS (nulos, 0 puntos para ambos).")) return;
  const { ok, d: r } = await apiSend(`/api/events/${d.id}/matches/close-round`, "POST");
  if (!ok) { wikiToast(r.error || r.detail || "No se pudo cerrar la ronda", "err"); return; }
  wikiToast(r.closed ? `Ronda cerrada · ${r.closed} sin jugar` : "No quedaban pendientes", "ok");
  await openEvent(d.id);
}
async function detectResults() {
  const d = currentEvent;
  wikiToast("Buscando en las amistosas de los participantes…", "ok");
  const { ok, d: r } = await apiSend(`/api/events/${d.id}/matches/detect`, "POST", {});
  if (!ok) { wikiToast(r.error || r.detail || "No se pudo detectar", "err"); return; }
  if (r.detected > 0) wikiToast(`Detectados ${r.detected} de ${r.checked} pendientes. Revísalos por si hay errores.`, "ok");
  else wikiToast(`No se encontró ninguna amistosa que cruzar (${r.checked} pendientes revisadas).`, "err");
  await openEvent(d.id);
}
async function detectMatch(mid) {
  const d = currentEvent;
  const { ok, d: r } = await apiSend(`/api/events/${d.id}/matches/detect`, "POST", { match_id: mid, force: true });
  if (!ok) { wikiToast(r.error || r.detail || "No se pudo detectar", "err"); return; }
  if (r.detected > 0) { wikiToast("Resultado detectado desde una amistosa.", "ok"); closeEvModal("event-match-modal"); await openEvent(d.id); }
  else wikiToast("No se encontró una amistosa para este enfrentamiento (mismos jugadores, en fechas del evento).", "err");
}
async function sendSummary() {
  const d = currentEvent;
  if (!confirm("Claude redactará un resumen de los resultados y lo enviará como aviso a los seguidores y apuntados del evento. ¿Continuar?")) return;
  wikiToast("Claude está redactando el resumen…", "ok");
  const { ok, d: r } = await apiSend(`/api/events/${d.id}/summary`, "POST", {});
  if (!ok) { wikiToast(r.error || r.detail || "No se pudo generar el resumen", "err"); return; }
  wikiToast(`Resumen enviado a ${r.sent} persona(s).`, "ok");
  alert("Resumen enviado a seguidores y apuntados:\n\n" + (r.text || ""));
}

/* ----- Seguir ----- */
async function followEvent() { const { ok } = await apiSend(`/api/events/${currentEvent.id}/follow`, "POST"); if (ok) { await openEvent(currentEvent.id); loadMyEvents(); } }
async function unfollowEvent() { const { ok } = await apiSend(`/api/events/${currentEvent.id}/follow`, "DELETE"); if (ok) { await openEvent(currentEvent.id); loadMyEvents(); } }

/* ----- Apuntarse ----- */
function openJoin() {
  const d = currentEvent, tags = d.my_tags || [];
  if (!tags.length) { wikiToast("Añade primero un ID en Brawl Sensei", "err"); return; }
  $("ejoin-tag").innerHTML = tags.map((t) => `<option value="${esc(t.tag)}">${esc(t.name || t.tag)} (${esc(t.tag)})</option>`).join("");
  $("ejoin-pw-wrap").style.display = d.visibility === "private" ? "block" : "none";
  $("ejoin-team-wrap").style.display = d.mode === "teams" ? "block" : "none";
  $("ejoin-pw").value = ""; $("ejoin-team").value = "";
  const solicit = d.visibility === "acceptance";
  $("ejoin-title").textContent = solicit ? "Solicitar plaza" : "Apuntarse al evento";
  $("ejoin-submit").textContent = solicit ? "Solicitar plaza" : "Apuntarse";
  openEvModal("event-join-modal");
}
async function submitJoin() {
  const d = currentEvent, body = { player_tag: $("ejoin-tag").value };
  if (d.visibility === "private") body.password = $("ejoin-pw").value;
  if (d.mode === "teams" && $("ejoin-team").value.trim()) body.team_name = $("ejoin-team").value.trim();
  const { ok, d: res } = await apiSend(`/api/events/${d.id}/join`, "POST", body);
  if (!ok) { wikiToast(res.error || res.detail || "No se pudo apuntar", "err"); return; }
  closeEvModal("event-join-modal");
  wikiToast(res.requested ? "Solicitud enviada" : "¡Apuntado!", "ok");
  await openEvent(d.id); loadMyEvents();
}

/* ----- Editar (organizador) ----- */
async function openEdit() {
  const d = currentEvent, s = d.settings || {};
  await loadBsModes();
  $("ee-name").value = d.name || ""; $("ee-status").value = d.status || "open";
  $("ee-kind").value = d.kind; $("ee-mode").value = d.mode; $("ee-vis").value = d.visibility;
  $("ee-lang").value = d.language || "es"; $("ee-max").value = d.max_participants || 12;
  $("ee-match").value = d.match_type || "bo1"; $("ee-format").value = d.format || "";
  $("ee-rounds").value = s.rounds || ""; $("ee-rules").value = s.rules || "";
  $("ee-teamsize").value = s.team_size || 3;
  $("ee-pwin").value = (s.points && s.points.win != null) ? s.points.win : 3;
  $("ee-pdraw").value = (s.points && s.points.draw != null) ? s.points.draw : 1;
  $("ee-ploss").value = (s.points && s.points.loss != null) ? s.points.loss : 0;
  $("ee-mappol").value = s.map_policy || "fixed"; fillModeMap("ee-fixmode", "ee-fixmap", s.fixed_mode || "", s.fixed_map || "");
  $("ee-showdown").value = s.showdown || "exclude";
  $("ee-showdown-hint").textContent = d.mode === "teams"
    ? "En eventos de equipos (3 vs 3) la Supervivencia se juega en trío."
    : "En eventos individuales puedes usar Supervivencia solo o dúo.";
  $("ee-mapspublic").checked = s.maps_public !== false;
  $("ee-revmode").value = s.reveal_mode_date || ""; $("ee-revmap").value = s.reveal_map_date || "";
  $("ee-dstart").value = d.date_start || ""; $("ee-dend").value = d.date_end || "";
  $("ee-desc").value = d.description || ""; evPosterUrl = d.poster_url || null; updatePosterPreview();
  $("ee-pw").value = ""; $("ee-confirm").checked = d.require_confirmation !== 0; $("ee-hidden").checked = !!d.hidden;
  eeVisChange(); eeMapPolChange(); eeMapsPublicChange(); eeFormatChange();
  // Borrar el evento es exclusivo del propietario; los co-organizadores no ven el botón.
  const delBtn = $("ee-delete-btn"); if (delBtn) delBtn.style.display = d.is_real_owner ? "" : "none";
  closeEvModal("event-detail-modal"); openEvModal("event-edit-modal");
}
function eeVisChange() { $("ee-private-box").style.display = $("ee-vis").value === "private" ? "block" : "none"; }
function eeFormatChange() {
  const f = $("ee-format").value;
  if (f && EV_FORMAT_RULES[f] && !$("ee-rules").value.trim()) $("ee-rules").value = EV_FORMAT_RULES[f];
  $("ee-teamsize-wrap").style.display = f === "random_teams" ? "" : "none";
}
function eeMapPolChange() { $("ee-fixed-wrap").style.display = $("ee-mappol").value === "fixed" ? "block" : "none"; }
function eeMapsPublicChange() { $("ee-reveal-wrap").style.display = $("ee-mapspublic").checked ? "none" : "grid"; }
function updatePosterPreview() {
  const img = $("ee-poster-preview"), clr = $("ee-poster-clear");
  if (evPosterUrl) { img.src = evPosterUrl; img.style.display = "block"; clr.style.display = "inline-block"; }
  else { img.style.display = "none"; clr.style.display = "none"; }
}
function clearPoster() { evPosterUrl = null; updatePosterPreview(); }
async function uploadEventPoster(input) {
  const file = input.files && input.files[0]; input.value = "";
  if (!file) return;
  if (file.size > 6 * 1024 * 1024) { wikiToast("La imagen supera los 6 MB", "err"); return; }
  const reader = new FileReader();
  reader.onload = async () => {
    const { ok, d } = await apiSend("/api/events/upload-image", "POST", { data: reader.result, mime: file.type });
    if (!ok) { wikiToast(d.error || d.detail || "No se pudo subir", "err"); return; }
    evPosterUrl = d.url; updatePosterPreview(); wikiToast("Cartel subido", "ok");
  };
  reader.readAsDataURL(file);
}
async function submitEditEvent() {
  const d = currentEvent;
  const fmt = $("ee-format").value;
  let rules = $("ee-rules").value.trim();
  if (!rules && fmt && EV_FORMAT_RULES[fmt]) rules = EV_FORMAT_RULES[fmt];  // en blanco → normas estándar del modelo
  const settings = Object.assign({}, d.settings || {}, {
    rounds: parseInt($("ee-rounds").value, 10) || null, rules: rules,
    points: { win: num($("ee-pwin").value, 3), draw: num($("ee-pdraw").value, 1), loss: num($("ee-ploss").value, 0) },
    map_policy: $("ee-mappol").value, fixed_mode: $("ee-fixmode").value.trim(), fixed_map: $("ee-fixmap").value.trim(), showdown: $("ee-showdown").value,
    maps_public: $("ee-mapspublic").checked, reveal_mode_date: $("ee-revmode").value || null, reveal_map_date: $("ee-revmap").value || null,
    team_size: fmt === "random_teams" ? num($("ee-teamsize").value, 3) : null,
  });
  const body = {
    name: $("ee-name").value.trim(), status: $("ee-status").value, kind: $("ee-kind").value, mode: $("ee-mode").value,
    visibility: $("ee-vis").value, language: $("ee-lang").value, max_participants: num($("ee-max").value, 12),
    match_type: $("ee-match").value, format: $("ee-format").value, date_start: $("ee-dstart").value || null,
    date_end: $("ee-dend").value || null, description: $("ee-desc").value.trim(), poster_url: evPosterUrl || null, settings,
  };
  if ($("ee-vis").value === "private") {
    if ($("ee-pw").value.trim()) body.password = $("ee-pw").value.trim();
    body.require_confirmation = $("ee-confirm").checked;
    body.hidden = $("ee-hidden").checked;
  }
  const { ok, d: res } = await apiSend(`/api/events/${d.id}`, "PUT", body);
  if (!ok) { wikiToast(res.error || res.detail || "No se pudo guardar", "err"); return; }
  closeEvModal("event-edit-modal"); wikiToast("Cambios guardados", "ok");
  await openEvent(d.id); loadMyEvents();
}
async function deleteEvent() {
  if (!confirm("¿Eliminar este evento? Esta acción no se puede deshacer.")) return;
  const { ok } = await apiSend(`/api/events/${currentEvent.id}`, "DELETE");
  if (ok) { closeEvModal("event-edit-modal"); wikiToast("Evento eliminado", "ok"); loadMyEvents(); }
}

/* ============================ Página de detalle del evento ============================
   Vista a pantalla completa (como la ficha de un brawler): toda la info reorganizada y, para
   el organizador, un MODO EDICIÓN in-situ (botón "Editar" → campos editables + Guardar/Cancelar)
   en lugar del antiguo formulario en modal. La lista de "Eventos" se oculta mientras está abierta. */
let eventPageEdit = false;

function showEventPageView(on) {
  const list = $("events-list-view"), page = $("event-page");
  if (list) list.style.display = on ? "none" : "";
  if (page) page.style.display = on ? "" : "none";
}
function openEventPageFromModal(edit) {
  if (!currentEvent) return;
  closeEvModal("event-detail-modal");
  $("event-detail-body").innerHTML = "";   // evita IDs duplicados (rmm-*) entre modal y página
  eventView = "page"; eventPageEdit = !!edit;
  showEventPageView(true);
  renderEventPage(currentEvent);
  window.scrollTo({ top: 0, behavior: "smooth" });
}
// "Editar evento" (ficha rápida) → abre la página de detalle directamente en modo edición
// (ya no hay formulario en modal).
function openEventPageEditFromModal() { openEventPageFromModal(true); }
function closeEventPage() {
  eventView = "modal"; eventPageEdit = false;
  const page = $("event-page"); if (page) page.innerHTML = "";
  showEventPageView(false);
  loadMyEvents(); loadBoard();
  window.scrollTo({ top: 0, behavior: "smooth" });
}
function eventPageActions(d) {
  const a = [];
  if (d.is_owner) {
    a.push(`<button class="btn" onclick="toggleEventPageEdit(true)">✎ Editar</button>`);
    if ((d.participants || 0) < (d.max_participants || 12)) a.push(`<button class="ghost" onclick="openInvite()">+ Añadir jugador</button>`);
  } else {
    a.push(d.is_following
      ? `<button class="ghost" onclick="unfollowEventPage()">★ Siguiendo</button>`
      : `<button class="ghost" onclick="followEventPage()">☆ Seguir</button>`);
    if (d.relation === "participant") a.push(`<span class="evd-joined">✓ Ya participas</span>`);
    else if (d.my_request) a.push(`<span class="evd-joined pending">⏳ Solicitud pendiente</span>`);
    else if (!d.status || d.status === "open") a.push(`<button class="btn" onclick="openJoin()">${d.visibility === "acceptance" ? "Solicitar plaza" : "Apuntarse"}</button>`);
  }
  a.push(`<button class="ghost" onclick="shareEvent(${d.id}, ${esc(JSON.stringify(d.name || ''))})" title="Compartir en redes">📣 Compartir</button>`);
  return a;
}
function renderEventPage(d) {
  const page = $("event-page");
  if (!page) return;
  const editing = eventPageEdit && d.is_owner;
  // En edición: los metadatos (título, etiquetas, fechas, descripción, reglas, puntos) se editan
  // en la propia página; debajo se mantiene el cuerpo interactivo (rondas con «+», participantes,
  // clasificación y enfrentamientos) para no perder esos controles.
  const main = editing ? (eventPageEditHTML(d) + eventBodyHTML(d, true)) : eventBodyHTML(d);
  const actions = editing ? "" : `<div class="evp-actions">${eventPageActions(d).join("")}</div>`;
  page.innerHTML = `
    <div class="evp-topbar">
      <button class="ghost evp-back" onclick="closeEventPage()">← Volver a Eventos</button>
      ${actions}
    </div>
    ${editing ? "" : `${d.poster_url ? `<div class="evp-poster" style="background-image:url('${esc(d.poster_url)}')"></div>` : ""}
    <h1 class="evp-title">${esc(d.name)}</h1>
    <div class="evd-badges">${eventBadges(d)}</div>`}
    ${main}`;
  if (editing) { initEventPageEditWidgets(d); populateRoundSelectors(d); }
  else if (d.is_owner) populateRoundSelectors(d);
}
async function followEventPage() {
  const { ok } = await apiSend(`/api/events/${currentEvent.id}/follow`, "POST");
  if (ok) { await openEvent(currentEvent.id); loadMyEvents(); }
}
async function unfollowEventPage() {
  const { ok } = await apiSend(`/api/events/${currentEvent.id}/follow`, "DELETE");
  if (ok) { await openEvent(currentEvent.id); loadMyEvents(); }
}
function toggleEventPageEdit(on) {
  eventPageEdit = !!on;
  renderEventPage(currentEvent);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

/* ----- Edición in-situ sobre la propia página (organizador) ----- */
// Modelos de competición según el tipo de evento: los de torneo (suizo, McMahon, eliminación…)
// solo con "Torneo"; los de liga solo con "Liga".
const FORMATS_BY_KIND = {
  tournament: ["swiss", "mcmahon", "single_elim", "double_elim", "roundrobin", "random_teams"],
  league: ["free", "roundrobin", "random_teams"],
};
function eventPageEditHTML(d) {
  const langOpts = EV_LANGS.map((l) => `<option value="${l.c}">${esc(l.n)}</option>`).join("");
  return `<div class="evp-edit">
    <div class="evp-editbar">
      <span class="evp-edit-note">✎ Estás editando el evento. Cambia los campos directamente y pulsa Guardar.</span>
      <div class="evp-editbar-btns">
        ${d.is_real_owner ? `<button class="danger-ghost" type="button" onclick="deleteEventFromPage()">Eliminar</button>` : ""}
        <button class="ghost" type="button" onclick="toggleEventPageEdit(false)">Cancelar</button>
        <button class="btn" type="button" onclick="submitEventPageEdit()">✓ Guardar cambios</button>
      </div>
    </div>
    <input type="text" id="pe-name" class="evp-edit-title" placeholder="Nombre del evento" />
    <div class="evp-edit-tags">
      <label class="evp-tag-edit"><span>Tipo</span><select id="pe-kind" onchange="peKindChange()"><option value="league">Liga</option><option value="tournament">Torneo</option></select></label>
      <label class="evp-tag-edit"><span>Modalidad</span><select id="pe-mode"><option value="individual">Individual</option><option value="teams">Por equipos</option></select></label>
      <label class="evp-tag-edit"><span>Visibilidad</span><select id="pe-vis" onchange="peVisChange()"><option value="public">Público</option><option value="acceptance">Con aceptación</option><option value="private">Privado</option></select></label>
      <label class="evp-tag-edit"><span>Idioma</span><select id="pe-lang">${langOpts}</select></label>
      <label class="evp-tag-edit"><span>Estado</span><select id="pe-status"><option value="open">Inscripción abierta</option><option value="ongoing">En curso</option><option value="finished">Finalizado</option></select></label>
    </div>
    <div class="ec-hint" id="pe-mode-note" style="display:none;color:var(--gold)">La modalidad no se puede cambiar con jugadores apuntados: quítalos primero (indicando el motivo) y que se vuelvan a apuntar.</div>
    <div class="evp-edit-grid">
      <label class="ee-field"><span class="ec-lbl">Nº máximo de participantes</span><input type="number" id="pe-max" min="2" max="512" /></label>
      <label class="ee-field"><span class="ec-lbl">Tipo de enfrentamiento</span><select id="pe-match"><option value="bo1">A 1 combate</option><option value="bo3">Al mejor de 3</option><option value="bo5">Al mejor de 5</option></select></label>
      <label class="ee-field"><span class="ec-lbl">Fecha de inicio</span><input type="date" id="pe-dstart" /></label>
      <label class="ee-field"><span class="ec-lbl">Fecha de fin</span><input type="date" id="pe-dend" /></label>
    </div>
    <div class="evd-section"><h4>Descripción</h4>
      <textarea id="pe-desc" class="ee-textarea" rows="3" placeholder="Presenta tu evento: reglas, premios, requisitos…"></textarea></div>
    <div class="evd-section"><h4>Reglas del evento</h4>
      <div class="evp-edit-grid">
        <label class="ee-field"><span class="ec-lbl">Modelo de competición</span><select id="pe-format" onchange="peFormatChange()"></select></label>
        <label class="ee-field" id="pe-rounds-wrap"><span class="ec-lbl" id="pe-rounds-lbl">Número de vueltas</span><input type="number" id="pe-rounds" min="1" max="30" placeholder="1" /><span class="ec-mini" id="pe-rounds-hint">Veces que cada pareja de equipos se vuelve a enfrentar antes de acabar la liga.</span></label>
      </div>
      <textarea id="pe-rules" class="ee-textarea" rows="4" placeholder="Se rellenan solas con el modelo elegido; edítalas si quieres."></textarea>
      <div class="ee-grid ee-grid-3" style="margin-top:10px">
        <div class="ee-field"><label class="ec-lbl">Puntos victoria</label><input type="number" id="pe-pwin" /></div>
        <div class="ee-field"><label class="ec-lbl">Puntos empate</label><input type="number" id="pe-pdraw" /></div>
        <div class="ee-field"><label class="ec-lbl">Puntos derrota</label><input type="number" id="pe-ploss" /></div>
      </div>
      <div class="ec-hint" style="margin-top:8px">Los modos y mapas de cada ronda se eligen con el botón «+» de «Modos y mapas por ronda», más abajo.</div>
    </div>
    <div class="evd-section"><h4>Cartel del evento</h4>
      <div class="ee-poster-row">
        <img id="pe-poster-preview" class="ee-poster-preview" style="display:none" />
        <label class="ghost ee-upload">Subir imagen<input type="file" id="pe-poster-file" accept="image/*" style="display:none" onchange="uploadEventPosterPage(this)"></label>
        <button type="button" class="ghost" id="pe-poster-clear" style="display:none" onclick="clearPosterPage()">Quitar</button>
      </div>
    </div>
    <div class="evd-section" id="pe-private-box" style="display:none"><h4>Ajustes de evento privado</h4>
      <div class="ee-field"><label class="ec-lbl">Contraseña <span class="ec-mini">(vacía = no cambiarla)</span></label><input type="text" id="pe-pw" placeholder="Contraseña para apuntarse" /></div>
      <label class="chk ee-chk"><input type="checkbox" id="pe-confirm"><span>Requiere confirmación del organizador (si lo desactivas, cualquiera con la contraseña entra directo)</span></label>
      <label class="chk ee-chk"><input type="checkbox" id="pe-hidden"><span>Ocultar del tablón — solo se entra con el enlace que compartas</span></label>
    </div>
    <div class="evp-edit-foot">
      <button class="ghost" type="button" onclick="toggleEventPageEdit(false)">Cancelar</button>
      <button class="btn" type="button" onclick="submitEventPageEdit()">✓ Guardar cambios</button>
    </div>
  </div>`;
}
function initEventPageEditWidgets(d) {
  const s = d.settings || {};
  $("pe-name").value = d.name || ""; $("pe-status").value = d.status || "open";
  $("pe-kind").value = d.kind; $("pe-mode").value = d.mode; $("pe-vis").value = d.visibility;
  $("pe-lang").value = d.language || "es"; $("pe-max").value = d.max_participants || 12;
  $("pe-match").value = d.match_type || "bo1";
  $("pe-rounds").value = s.rounds || ""; $("pe-rules").value = s.rules || "";
  $("pe-pwin").value = (s.points && s.points.win != null) ? s.points.win : 3;
  $("pe-pdraw").value = (s.points && s.points.draw != null) ? s.points.draw : 1;
  $("pe-ploss").value = (s.points && s.points.loss != null) ? s.points.loss : 0;
  $("pe-dstart").value = d.date_start || ""; $("pe-dend").value = d.date_end || "";
  $("pe-desc").value = d.description || ""; evPosterUrl = d.poster_url || null; updatePosterPreviewPage();
  $("pe-pw").value = ""; $("pe-confirm").checked = d.require_confirmation !== 0; $("pe-hidden").checked = !!d.hidden;
  peKindChange(d.format);      // rellena los modelos según Torneo/Liga y fija el actual
  peVisChange();
  // La modalidad (individual/equipos) solo es editable si no hay nadie apuntado.
  const nParts = d.participants != null ? d.participants : (d.participants_list || []).length;
  const modeSel = $("pe-mode"), modeNote = $("pe-mode-note");
  if (modeSel) modeSel.disabled = nParts > 0;
  if (modeNote) modeNote.style.display = nParts > 0 ? "block" : "none";
}
// Rellena el desplegable de modelo de competición según el tipo (Torneo/Liga).
function peKindChange(keepFormat) {
  const sel = $("pe-format"); if (!sel) return;
  const cur = keepFormat != null ? keepFormat : sel.value;
  const kind = $("pe-kind").value;
  const allowed = FORMATS_BY_KIND[kind] || [];
  sel.innerHTML = `<option value="">— Elegir —</option>` +
    allowed.map((f) => `<option value="${f}">${esc(EV_FORMAT_LABEL[f] || f)}</option>`).join("");
  sel.value = allowed.includes(cur) ? cur : "";
  // El nº de "vueltas" es solo de LIGAS. En torneos las rondas se crean con «+» (no en el form).
  const rw = $("pe-rounds-wrap");
  if (rw) rw.style.display = (kind === "league") ? "" : "none";
  peFormatChange();
}
function peVisChange() { const b = $("pe-private-box"); if (b) b.style.display = $("pe-vis").value === "private" ? "" : "none"; }
function peFormatChange() {
  const f = $("pe-format").value;
  if (f && EV_FORMAT_RULES[f] && !$("pe-rules").value.trim()) $("pe-rules").value = EV_FORMAT_RULES[f];
}
function updatePosterPreviewPage() {
  const img = $("pe-poster-preview"), clr = $("pe-poster-clear");
  if (!img) return;
  if (evPosterUrl) { img.src = evPosterUrl; img.style.display = "block"; clr.style.display = "inline-block"; }
  else { img.style.display = "none"; clr.style.display = "none"; }
}
function clearPosterPage() { evPosterUrl = null; updatePosterPreviewPage(); }
async function uploadEventPosterPage(input) {
  const file = input.files && input.files[0]; input.value = "";
  if (!file) return;
  if (file.size > 6 * 1024 * 1024) { wikiToast("La imagen supera los 6 MB", "err"); return; }
  const reader = new FileReader();
  reader.onload = async () => {
    const { ok, d } = await apiSend("/api/events/upload-image", "POST", { data: reader.result, mime: file.type });
    if (!ok) { wikiToast(d.error || d.detail || "No se pudo subir", "err"); return; }
    evPosterUrl = d.url; updatePosterPreviewPage(); wikiToast("Cartel subido", "ok");
  };
  reader.readAsDataURL(file);
}
async function submitEventPageEdit() {
  const d = currentEvent;
  const fmt = $("pe-format").value;
  let rules = $("pe-rules").value.trim();
  if (!rules && fmt && EV_FORMAT_RULES[fmt]) rules = EV_FORMAT_RULES[fmt];
  // Se preservan los ajustes de modos/mapas (map_policy, showdown, fixed_*, reveal_*, team_size,
  // round_count): ya no se editan en este formulario, se gestionan en la sección de rondas.
  // "rounds" (vueltas) solo aplica a ligas; en torneos las rondas se crean con «+».
  const isLeague = $("pe-kind").value === "league";
  const settings = Object.assign({}, d.settings || {}, {
    rounds: isLeague ? (parseInt($("pe-rounds").value, 10) || null) : ((d.settings || {}).rounds || null),
    rules: rules,
    points: { win: num($("pe-pwin").value, 3), draw: num($("pe-pdraw").value, 1), loss: num($("pe-ploss").value, 0) },
  });
  const body = {
    name: $("pe-name").value.trim(), status: $("pe-status").value, kind: $("pe-kind").value, mode: $("pe-mode").value,
    visibility: $("pe-vis").value, language: $("pe-lang").value, max_participants: num($("pe-max").value, 12),
    match_type: $("pe-match").value, format: $("pe-format").value, date_start: $("pe-dstart").value || null,
    date_end: $("pe-dend").value || null, description: $("pe-desc").value.trim(), poster_url: evPosterUrl || null, settings,
  };
  if ($("pe-vis").value === "private") {
    if ($("pe-pw").value.trim()) body.password = $("pe-pw").value.trim();
    body.require_confirmation = $("pe-confirm").checked;
    body.hidden = $("pe-hidden").checked;
  }
  if (!body.name) { wikiToast("Pon un nombre al evento", "err"); return; }
  const { ok, d: res } = await apiSend(`/api/events/${d.id}`, "PUT", body);
  if (!ok) { wikiToast(res.error || res.detail || "No se pudo guardar", "err"); return; }
  wikiToast("Cambios guardados", "ok");
  eventPageEdit = false;
  await openEvent(d.id); loadMyEvents();
}
async function deleteEventFromPage() {
  if (!confirm("¿Eliminar este evento? Esta acción no se puede deshacer.")) return;
  const { ok } = await apiSend(`/api/events/${currentEvent.id}`, "DELETE");
  if (ok) { wikiToast("Evento eliminado", "ok"); closeEventPage(); }
}

/* ----- Invitar / solicitudes / participantes (organizador) ----- */
function openInvite() {
  $("einv-tags").value = ""; $("einv-team").value = "";
  $("einv-team-wrap").style.display = currentEvent.mode === "teams" ? "block" : "none";
  renderInvitePicklist();
  openEvModal("event-invite-modal");
}
// Lista de los jugadores que el organizador tiene registrados en su cuenta: al pinchar uno,
// su ID se añade a la caja de IDs (más cómodo que copiar/pegar).
function renderInvitePicklist() {
  const box = $("einv-picklist");
  if (!box) return;
  const mine = Object.values(playersById || {});
  if (!mine.length) { box.innerHTML = ""; return; }
  box.innerHTML = `<div class="einv-pick-h">Tus jugadores <span>(pincha para añadir su ID)</span></div>
    <div class="einv-pick-chips">${mine.slice().sort((a, b) => (a.name || a.tag).localeCompare(b.name || b.tag))
      .map((p) => `<button type="button" class="einv-chip" data-tag="${esc(p.tag)}" onclick="pickInvitePlayer(this)">${esc(p.name || p.tag)}</button>`).join("")}</div>`;
}
function pickInvitePlayer(btn) {
  const tag = btn.dataset.tag;
  const ta = $("einv-tags");
  const norm = (t) => t.replace(/^#/, "").toUpperCase();
  const have = ta.value.split(/[\s,]+/).filter(Boolean).map(norm);
  if (!have.includes(norm(tag))) ta.value = ta.value.trim() ? ta.value.trim() + ", " + tag : tag;
  btn.classList.add("used"); btn.disabled = true;
}
/* ----- Co-organizadores (fase D): elige entre tus amigos ----- */
async function openAddOrganizer() {
  openEvModal("event-organizer-modal");
  const box = $("eorg-picklist");
  box.innerHTML = `<p class="evd-muted" style="padding:10px">Cargando amigos…</p>`;
  let r;
  try { r = await getJSON("/api/friends"); } catch (e) { box.innerHTML = `<p class="evd-muted" style="padding:10px">No se pudo cargar tu lista de amigos.</p>`; return; }
  const already = new Set((currentEvent.organizers || []).map((o) => o.id));
  const cand = (r.friends || []).filter((u) => !already.has(u.id));
  if (!cand.length) {
    box.innerHTML = `<p class="evd-muted" style="padding:10px">${(r.friends || []).length ? "Todos tus amigos ya son organizadores." : "Aún no tienes amigos. Añádelos desde el menú de usuario → Amigos."}</p>`;
    return;
  }
  box.innerHTML = `<div class="einv-pick-h">Tus amigos <span>(pincha para añadir como organizador)</span></div>
    <div class="einv-pick-chips">${cand.sort((a, b) => a.username.localeCompare(b.username))
      .map((u) => `<button type="button" class="einv-chip" data-uid="${u.id}" onclick="addOrganizer(${u.id}, this)">@${esc(u.username)}</button>`).join("")}</div>`;
}
async function addOrganizer(uid, btn) {
  const { ok, d } = await apiSend(`/api/events/${currentEvent.id}/organizers`, "POST", { user_id: uid });
  if (!ok) { wikiToast(d.error || d.detail || "No se pudo añadir", "err"); return; }
  if (btn) { btn.classList.add("used"); btn.disabled = true; }
  currentEvent.organizers = d.organizers || [];
  wikiToast("Co-organizador añadido", "ok");
  await openEvent(currentEvent.id);
}
async function removeOrganizer(uid) {
  if (!confirm("¿Quitar a este co-organizador? Dejará de poder gestionar el evento.")) return;
  const { ok, d } = await apiSend(`/api/events/${currentEvent.id}/organizers/${uid}`, "DELETE");
  if (!ok) { wikiToast(d.error || d.detail || "No se pudo quitar", "err"); return; }
  currentEvent.organizers = d.organizers || [];
  wikiToast("Co-organizador quitado", "ok");
  await openEvent(currentEvent.id);
}
async function submitInvite() {
  const raw = $("einv-tags").value.trim();
  if (!raw) { wikiToast("Pega al menos un ID de jugador", "err"); return; }
  const body = { player_tags: raw };
  if (currentEvent.mode === "teams" && $("einv-team").value.trim()) body.team_name = $("einv-team").value.trim();
  const { ok, d } = await apiSend(`/api/events/${currentEvent.id}/participants/bulk`, "POST", body);
  if (!ok) { wikiToast(d.error || d.detail || "No se pudo añadir", "err"); return; }
  closeEvModal("event-invite-modal");
  let msg = d.added === 1 ? "1 jugador añadido" : `${d.added} jugadores añadidos`;
  const extra = [];
  if (d.duplicates) extra.push(`${d.duplicates} ya estaban`);
  if (d.no_space) extra.push(`${d.no_space} sin plaza`);
  if (extra.length) msg += ` (${extra.join(", ")})`;
  wikiToast(msg, d.added ? "ok" : "err"); await openEvent(currentEvent.id);
}
async function acceptRequest(rid) {
  const { ok, d } = await apiSend(`/api/events/${currentEvent.id}/requests/${rid}/accept`, "POST");
  if (!ok) { wikiToast(d.error || d.detail || "Error", "err"); return; }
  await openEvent(currentEvent.id);
}
async function rejectRequest(rid) {
  const { ok } = await apiSend(`/api/events/${currentEvent.id}/requests/${rid}/reject`, "POST");
  if (ok) await openEvent(currentEvent.id);
}
async function removeParticipant(pid) {
  const reason = prompt("¿Por qué quitas a este jugador? Se le enviará una notificación con el motivo.");
  if (reason === null) return;                 // canceló el diálogo
  const r = (reason || "").trim();
  if (!r) { wikiToast("Debes indicar un motivo para quitar al jugador", "err"); return; }
  const { ok, d } = await apiSend(`/api/events/${currentEvent.id}/participants/${pid}?reason=${encodeURIComponent(r)}`, "DELETE");
  if (!ok) { wikiToast((d && (d.error || d.detail)) || "No se pudo quitar al jugador", "err"); return; }
  wikiToast("Jugador quitado; se le ha notificado el motivo", "ok");
  await openEvent(currentEvent.id);
}

