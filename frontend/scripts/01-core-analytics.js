/* BrawlSensei · 01-core-analytics.js
   núcleo (helpers, $, recursos visuales) + pestaña Analíticas (overview, paneles, filtros, gráficas, historial, jugador, rotación, cuenta).
   Se carga como <script src> desde index.html, en orden. El JS de cliente
   SIEMPRE es visible en el navegador: aquí no van secretos. */
const $ = (id) => document.getElementById(id);
function goHome() { location.href = "/"; }
let currentPlayer = null;
let currentUser = null;
let rankScope = "global";
let myProfile = null;
let playersById = {};
let activeTab = "stats";
let histOffset = 0, histTotal = 0;
let ASSETS = { brawlers: {}, modes: {}, maps: {} };
let filterSel = { brawler: [], mode: [], map: [], role: [] };  // filtros multi-selección

function esc(s) { return (s == null ? "" : String(s)).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
function pctColor(w) { if (w == null) return "var(--neutral)"; if (w >= 55) return "var(--win)"; if (w < 45) return "var(--loss)"; return "var(--gold)"; }
async function getJSON(u) { const r = await fetch(u); if (r.status === 401) { showLogin(); throw new Error("401"); } return r.json(); }
function fmtTime(t) { const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})/.exec(t || ""); return m ? `${m[3]}/${m[2]} ${m[4]}:${m[5]}` : (t || ""); }
function fmtClock(iso) { try { return new Date(iso).toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" }); } catch { return iso; } }

/* ---------- Recursos visuales (Brawlify) ---------- */
function brawlerPortrait(name) { return name ? ASSETS.brawlers[name.toUpperCase()] || null : null; }
function modeAsset(mode) { return mode ? ASSETS.modes[mode.toLowerCase()] || null : null; }
function mapAsset(map) { return map ? ASSETS.maps[map.toLowerCase()] || null : null; }
function imgTag(url, cls) { return `<img class="${cls}" src="${url}" alt="" loading="lazy" onerror="this.style.display='none'" />`; }

function qs() {
  const p = new URLSearchParams();
  if (currentPlayer) p.set("player", currentPlayer);
  ["brawler", "mode", "map", "role"].forEach((k) => { if (filterSel[k].length) p.set(k, filterSel[k].join(",")); });
  return p.toString();
}

/* ---------- Estadísticas ---------- */
function labelCell(item, kind) {
  const lbl = esc(item.label);
  if (kind === "map") {
    return mapAsset(item.label) ? `<span class="map-link" data-map="${lbl}">${lbl}</span>` : lbl;
  }
  if (kind === "mode") {
    const a = modeAsset(item.label);
    return `${a && a.icon ? imgTag(a.icon, "mode-icon") : ""}<span>${esc(modeName(item.label))}</span>`;
  }
  const url = brawlerPortrait(item.label);
  const prefix = kind === "vs" ? `<span class="vs">vs</span>` : "";
  return `${url ? imgTag(url, "row-portrait") : ""}${prefix}<span>${lbl}</span>`;
}
function rowHTML(item, kind) {
  const isVs = kind === "vs", showStar = kind === "brawler";
  const w = item.winrate, pct = w == null ? "—" : w + "%", color = pctColor(w);
  const barW = w == null ? 0 : Math.max(2, w);
  let extra;
  if (isVs) {
    extra = `${item.total} enc.`;
    if (item.avg_enemy_trophies != null) {
      extra += ` · rival ~${item.avg_enemy_trophies}🏆`;
      if (item.trophy_delta != null) { const s = item.trophy_delta >= 0 ? "+" : ""; extra += ` (Δ ${s}${item.trophy_delta})`; }
    }
  } else {
    extra = `${item.total} part.${item.trophy_delta != null ? " · " + (item.trophy_delta >= 0 ? "+" : "") + item.trophy_delta + " 🏆" : ""}`;
    if (showStar && item.star_rate != null) extra += ` · ⭐ ${item.star_rate}%`;
  }
  return `<div class="row"><div class="name">${labelCell(item, kind)}</div>
    <div class="pct" style="color:${color}">${pct}</div>
    <div class="bar-wrap"><div class="bar" style="width:${barW}%;background:${color}"></div></div>
    <div class="meta">${item.wins}V · ${item.losses}D${item.undecided ? " · " + item.undecided + "E" : ""} — ${extra}</div></div>`;
}
/* ---------- Paneles colapsables (muestran 6 filas; "ver más" despliega) ---------- */
function updateMoreBtn(btn, rowsEl, threshold, collapsed) {
  const n = rowsEl.querySelectorAll(":scope > .row").length;
  btn.textContent = collapsed ? `Ver más (${n - threshold})` : "Ver menos";
}
function applyCollapse(rowsEl, threshold = 6) {
  if (!rowsEl) return;
  let btn = rowsEl.nextElementSibling;
  if (!btn || !btn.classList.contains("more-toggle")) {
    btn = document.createElement("button");
    btn.className = "more-toggle";
    btn.addEventListener("click", () => updateMoreBtn(btn, rowsEl, threshold, rowsEl.classList.toggle("collapsed")));
    rowsEl.after(btn);
  }
  const n = rowsEl.querySelectorAll(":scope > .row").length;
  if (n > threshold) { rowsEl.classList.add("collapsed"); btn.style.display = ""; updateMoreBtn(btn, rowsEl, threshold, true); }
  else { rowsEl.classList.remove("collapsed"); btn.style.display = "none"; }
}
function render(el, data, kind) {
  const min = 1;
  const f = data.filter((d) => d.total >= min);
  // Orden por win rate: "contra cada brawler" de menor a mayor; el resto de mayor a menor.
  const asc = kind === "vs";
  f.sort((a, b) => {
    const wa = a.winrate == null ? (asc ? Infinity : -Infinity) : a.winrate;
    const wb = b.winrate == null ? (asc ? Infinity : -Infinity) : b.winrate;
    return asc ? wa - wb : wb - wa;
  });
  el.innerHTML = f.length ? f.map((d) => rowHTML(d, kind)).join("")
    : `<div class="empty"><span class="big">∅</span>Aún no hay datos suficientes.<br>Deja el tracker corriendo mientras se juega.</div>`;
  applyCollapse(el);
}
async function loadOverview() {
  const o = await getJSON("/api/overview?" + qs());
  const wr = o.winrate;
  let cards = `
    <div class="stat win"><div class="k">Win rate</div><div class="v" style="color:${pctColor(wr)}">${wr == null ? "—" : wr + "<small>%</small>"}</div><div class="sub">${o.wins}V · ${o.losses}D${o.undecided ? " · " + o.undecided + "E" : ""}</div></div>
    <div class="stat"><div class="k">Partidas registradas</div><div class="v">${o.total}</div><div class="sub">acumuladas hasta ahora</div></div>
    <div class="stat star"><div class="k">Jugador estelar</div><div class="v">${o.star_rate == null ? "—" : o.star_rate + "<small>%</small>"}</div><div class="sub">${o.star_players} veces MVP (3v3)</div></div>
    <div class="stat cyan"><div class="k">Balance de trofeos</div><div class="v" style="color:${o.trophy_delta_7d >= 0 ? "var(--win)" : "var(--loss)"}">${o.trophy_delta_7d >= 0 ? "+" : ""}${o.trophy_delta_7d}</div><div class="sub">últimos 7 días</div></div>`;
  cards += `<div class="stat"><div class="k">Última partida</div><div class="v" style="font-size:18px">${o.last_battle ? fmtTime(o.last_battle) : "—"}</div><div class="sub">vista por el tracker</div></div>`;
  $("overview").innerHTML = cards;
}
async function loadPanels() {
  const base = qs();
  const [b, m, mp, vs, roles] = await Promise.all([
    getJSON("/api/winrate?by=brawler&" + base), getJSON("/api/winrate?by=mode&" + base),
    getJSON("/api/winrate?by=map&" + base), getJSON("/api/vs?" + base),
    getJSON("/api/roles?" + base),
  ]);
  render($("r-brawler"), b, "brawler"); render($("r-mode"), m, "mode");
  render($("r-map"), mp, "map"); render($("r-vs"), vs, "vs");
  render($("r-role"), roles, "role");
  renderRoleRadars(roles);
  renderModeDonuts(m);
}
async function loadStats() { await Promise.all([loadOverview(), loadPanels(), loadStatsReport(), loadStatsRating()]); }
async function loadStatsRating() {
  if (!currentPlayer) return;
  const el = $("stats-rating");
  try {
    const d = await getJSON("/api/account-rating?player=" + encodeURIComponent(currentPlayer));
    el.innerHTML = ratingHTML(d.rating); el.style.display = "";
  } catch (e) { el.style.display = "none"; }
}
async function loadStatsReport() {
  if (!currentPlayer) return;
  try {
    const a = await getJSON("/api/report?" + qs());
    renderStreak(a.streak);            // banda de racha (en la cabecera)
    renderHighlights(a.highlights);    // fila de destacados (2ª fila)
    $("trophy-chart").innerHTML = trophyChart(a.trophy_series);
    $("winrate-chart").innerHTML = winrateChart(a.winrate_evolution);
    $("trophy-diff").innerHTML = renderBuckets(a.trophy_diff, true); applyCollapse($("trophy-diff"));
    $("hourly").innerHTML = renderHourly(a.battle_points); applyCollapse($("hourly"));
    render($("ri-allies"), a.allies, "brawler");
    renderValuation(a.by_brawler);
  } catch (e) { /* si falla, se deja lo que haya */ }
}

/* ---------- Historial ---------- */
function bchip(p, me = false) {
  const url = brawlerPortrait(p.brawler);
  const img = url ? imgTag(url, "bc-portrait") : `<div class="bc-portrait"></div>`;
  const tr = p.trophies != null ? `<span class="bc-tr">${p.trophies}🏆</span>` : `<span class="bc-tr" style="color:var(--muted)">—</span>`;
  return `<div class="bc-brawler ${me ? "me" : ""}">${img}<span class="bc-bname">${esc(p.brawler)}</span>${tr}</div>`;
}
function battleCard(b) {
  const resClass = b.is_win === 1 ? "win" : b.is_win === 0 ? "loss" : "draw";
  const resTxt = b.is_win === 1 ? "Victoria" : b.is_win === 0 ? "Derrota" : (b.rank != null ? "Pos. " + b.rank : "Empate");
  const tro = b.trophy_change != null ? `${b.trophy_change >= 0 ? "+" : ""}${b.trophy_change}🏆` : "";
  const ma = modeAsset(b.mode);
  const modeIcon = ma && ma.icon ? imgTag(ma.icon, "bc-mode-icon") : "";
  const mapHtml = mapAsset(b.map) ? `<span class="map-link" data-map="${esc(b.map)}">${esc(b.map)}</span>` : esc(b.map);
  const accent = ma && ma.color ? ` style="border-left-color:${ma.color}"` : "";
  const mine = [bchip({ brawler: b.my_brawler, trophies: b.my_trophies }, true)].concat((b.allies || []).map((a) => bchip(a))).join("");
  const enemies = (b.opponents || []).map((o) => bchip(o)).join("") || `<div class="bc-brawler"><div class="bc-portrait"></div><span class="bc-bname">—</span></div>`;
  const mn = b.manual || {};
  const v = (x) => (x == null ? "" : x);
  return `<div class="battle-card"${accent}>
    <div class="bc-head">
      <span class="bc-result ${resClass}">${resTxt}</span>
      <span class="bc-mode">${modeIcon}${esc(modeName(b.mode))} · ${mapHtml}</span>
      <span class="bc-time">${fmtTime(b.battle_time)}</span>
      <span class="bc-trophy">${tro}</span>
    </div>
    <div class="bc-teams">
      <div class="bc-team mine"><span class="bc-side">Tu equipo</span><div class="bc-brawlers">${mine}</div></div>
      <div class="bc-team enemy"><span class="bc-side">Rivales</span><div class="bc-brawlers">${enemies}</div></div>
    </div>
    <div class="bc-manual" data-id="${b.id}">
      <span class="mlbl">Datos manuales (opcionales, de la pantalla final)</span>
      <input type="number" class="m-kills" placeholder="Asesinatos" value="${v(mn.kills)}" />
      <input type="number" class="m-deaths" placeholder="Muertes" value="${v(mn.deaths)}" />
      <input type="number" class="m-damage" placeholder="Daño" value="${v(mn.damage)}" />
      <input type="number" class="m-healing" placeholder="Curación" value="${v(mn.healing)}" />
      <input type="text" class="m-notes" placeholder="Notas" value="${esc(mn.notes)}" />
      <button onclick="saveManual(this)">Guardar</button>
      <span class="m-status"></span>
    </div>
  </div>`;
}
async function loadHistory(reset) {
  if (reset) { histOffset = 0; $("battle-list").innerHTML = ""; }
  const data = await getJSON(`/api/battles?${qs()}&limit=25&offset=${histOffset}`);
  histTotal = data.total;
  if (reset && data.battles.length === 0) {
    $("battle-list").innerHTML = `<div class="empty"><span class="big">∅</span>No hay partidas registradas con estos filtros todavía.</div>`;
  } else {
    $("battle-list").insertAdjacentHTML("beforeend", data.battles.map(battleCard).join(""));
  }
  histOffset += data.battles.length;
  $("load-more").style.display = histOffset < histTotal ? "" : "none";
}
async function saveManual(btn) {
  const box = btn.closest(".bc-manual"), id = box.dataset.id, st = box.querySelector(".m-status");
  const body = {
    kills: box.querySelector(".m-kills").value, deaths: box.querySelector(".m-deaths").value,
    damage: box.querySelector(".m-damage").value, healing: box.querySelector(".m-healing").value,
    notes: box.querySelector(".m-notes").value,
  };
  btn.disabled = true; st.textContent = "Guardando…";
  try {
    const r = await fetch(`/api/battles/${encodeURIComponent(id)}/manual`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    st.textContent = r.ok ? "✓ guardado" : "error";
    if (r.ok && activeTab === "stats") loadOverview();
  } catch (e) { st.textContent = "error de red"; }
  btn.disabled = false;
  setTimeout(() => { st.textContent = ""; }, 2500);
}
window.saveManual = saveManual;

/* ---------- Lightbox de mapa ---------- */
function showMap(name) {
  const m = mapAsset(name);
  if (!m || !m.image) return;
  $("lb-img").src = m.image; $("lb-cap").textContent = name;
  $("lightbox").classList.add("show");
}
document.addEventListener("click", (e) => {
  const link = e.target.closest(".map-link");
  if (link) { showMap(link.dataset.map); return; }
  const card = e.target.closest(".rot-card[data-map]");
  if (card) showMap(card.dataset.map);
});
$("lb-close").addEventListener("click", () => $("lightbox").classList.remove("show"));
$("lightbox").addEventListener("click", (e) => { if (e.target.id === "lightbox") $("lightbox").classList.remove("show"); });
$("map-modal").addEventListener("click", (e) => { if (e.target.id === "map-modal") closeMapModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { $("lightbox").classList.remove("show"); closeMapModal(); } });

/* ---------- Filtros / jugadores ---------- */
async function loadFilters() {
  const f = await getJSON("/api/filters?player=" + encodeURIComponent(currentPlayer || ""));
  filterSel.brawler = filterSel.brawler.filter((v) => (f.brawlers || []).includes(v));
  filterSel.mode = filterSel.mode.filter((v) => (f.modes || []).includes(v));
  filterSel.map = filterSel.map.filter((v) => (f.maps || []).includes(v));
  filterSel.role = filterSel.role.filter((v) => (f.roles || []).includes(v));
  buildMs("brawler", f.brawlers || []);
  buildMs("mode", f.modes || []);
  buildMs("map", f.maps || []);
  buildMs("role", f.roles || []);
}
/* ---------- Multi-select de filtros (checkboxes + Todos/Ninguno) ---------- */
function msOption(kind, value) {
  let label = value, img = "";
  if (kind === "brawler") { const p = brawlerPortrait(value); if (p) img = `<img src="${p}" alt="" onerror="this.style.display='none'">`; }
  else if (kind === "mode") { const a = modeAsset(value); if (a && a.icon) img = `<img src="${a.icon}" alt="" onerror="this.style.display='none'">`; label = modeName(value); }
  return { label, img };  // mapa y rol: solo el nombre
}
function msTriggerLabel(key) {
  const sel = filterSel[key] || [];
  if (!sel.length) return "Todos";
  if (sel.length === 1) return msOption(key, sel[0]).label;
  return sel.length + " seleccionados";
}
function buildMs(key, values) {
  const el = $("ms-" + key);
  if (!el) return;
  const sel = filterSel[key] || [];
  const opts = (values || []).map((v) => {
    const { label, img } = msOption(key, v);
    return `<label class="ms-opt"><input type="checkbox" value="${esc(v)}" ${sel.includes(v) ? "checked" : ""} onchange="msToggle('${key}', this.value, this.checked)">${img}<span>${esc(label)}</span></label>`;
  }).join("");
  el.innerHTML = `<button class="ms-trigger" type="button" onclick="toggleMs('${key}', event)">${esc(msTriggerLabel(key))}</button>
    <div class="ms-panel">
      <div class="ms-actions"><button type="button" onclick="msAll('${key}', true)">✓ Todos</button><button type="button" onclick="msAll('${key}', false)">Ninguno</button></div>
      <div class="ms-options">${opts || '<div class="ms-empty">Sin opciones todavía</div>'}</div>
    </div>`;
}
function msTrig(key) { const el = $("ms-" + key), t = el && el.querySelector(".ms-trigger"); if (t) t.textContent = msTriggerLabel(key); }
function toggleMs(key, ev) {
  if (ev) ev.stopPropagation();
  const el = $("ms-" + key), open = el.classList.contains("open");
  document.querySelectorAll(".ms.open").forEach((m) => m.classList.remove("open"));
  if (!open) el.classList.add("open");
}
function msToggle(key, value, checked) {
  const sel = filterSel[key], i = sel.indexOf(value);
  if (checked && i < 0) sel.push(value);
  else if (!checked && i >= 0) sel.splice(i, 1);
  msTrig(key); onFilterChange();
}
function msAll(key, all) {
  const checks = $("ms-" + key).querySelectorAll(".ms-options input[type=checkbox]");
  filterSel[key] = all ? Array.from(checks).map((c) => c.value) : [];
  checks.forEach((c) => { c.checked = all; });
  msTrig(key); onFilterChange();
}
function msClearAll() {
  let any = false;
  ["brawler", "mode", "map", "role"].forEach((k) => {
    if (filterSel[k].length) any = true;
    filterSel[k] = [];
    const el = $("ms-" + k);
    if (el) { el.querySelectorAll("input[type=checkbox]").forEach((c) => { c.checked = false; }); msTrig(k); }
  });
  if (any) onFilterChange();
}
document.addEventListener("click", (e) => { if (!e.target.closest(".ms")) document.querySelectorAll(".ms.open").forEach((m) => m.classList.remove("open")); });
function fill(sel, values, labelFn) {
  const c = sel.value;
  sel.innerHTML = '<option value="">Todos</option>' +
    values.map((v) => `<option value="${esc(v)}">${esc(labelFn ? labelFn(v) : v)}</option>`).join("");
  sel.value = values.includes(c) ? c : "";
}
function applyScope() {
  const b = filterSel.brawler, m = filterSel.mode, mp = filterSel.map, ro = filterSel.role;
  // Cada panel de desglose se oculta solo si se filtra UN único valor de esa dimensión.
  $("panel-brawler").style.display = b.length === 1 ? "none" : "";
  $("panel-mode").style.display = m.length === 1 ? "none" : "";
  $("panel-map").style.display = mp.length === 1 ? "none" : "";
  const pr = $("panel-role"); if (pr) pr.style.display = ro.length === 1 ? "none" : "";
  const radars = $("role-radars-row"); if (radars) radars.style.display = b.length === 1 ? "none" : "";
  const donuts = $("mode-donuts-row"); if (donuts) donuts.style.display = m.length === 1 ? "none" : "";
  const parts = [];
  if (b.length) parts.push(b.length === 1 ? "el brawler " + b[0] : b.length + " brawlers");
  if (m.length) parts.push(m.length === 1 ? "el modo " + modeName(m[0]) : m.length + " modos");
  if (mp.length) parts.push(mp.length === 1 ? "el mapa " + mp[0] : mp.length + " mapas");
  if (ro.length) parts.push(ro.length === 1 ? "el rol " + ro[0] : ro.length + " roles");
  const n = $("scope-note");
  if (parts.length) { n.classList.add("show"); n.textContent = `Viendo ${parts.join(" · ")}: el resto del desglose se ajusta a esos filtros.`; }
  else { n.classList.remove("show"); n.textContent = ""; }
}
const ICON_TROPHY = `<svg viewBox="0 0 576 512" fill="currentColor"><path d="M400 0H176c-26.5 0-48.1 21.8-47.1 48.2c.2 5.3 .4 10.6 .7 15.8H24C10.7 64 0 74.7 0 88c0 92.6 33.5 157 78.5 200.7c44.3 43.1 98.3 64.8 138.1 75.8c23.4 6.5 39.4 26 39.4 45.6c0 20.9-17 37.9-37.9 37.9H192c-17.7 0-32 14.3-32 32s14.3 32 32 32H384c17.7 0 32-14.3 32-32s-14.3-32-32-32H359.9C339 448 322 431 322 410.1c0-19.6 16-39.1 39.4-45.6c39.9-11 93.9-32.7 138.2-75.8C544.5 245 578 180.6 578 88c0-13.3-10.7-24-24-24H446.4c.3-5.2 .5-10.5 .7-15.8C448.1 21.8 426.5 0 400 0zM48.9 112h84.4c9.1 90.1 29.2 150.3 51.9 190.6c-24.9-11-50.8-26.5-73.2-48.3c-32-31.1-58-76-63-142.3zM464.1 254.3c-22.4 21.8-48.3 37.3-73.2 48.3c22.7-40.3 42.8-100.5 51.9-190.6h84.4c-5 66.3-31 111.2-63 142.3z"/></svg>`;
const ICON_3V3 = `<svg viewBox="0 0 640 512" fill="currentColor"><path d="M144 0a80 80 0 1 1 0 160A80 80 0 1 1 144 0zM512 0a80 80 0 1 1 0 160A80 80 0 1 1 512 0zM0 298.7C0 239.8 47.8 192 106.7 192h42.7c15.9 0 31 3.5 44.6 9.7c-1.3 7.2-1.9 14.7-1.9 22.3c0 38.2 16.8 72.5 43.3 96c-.2 0-.4 0-.7 0H21.3C9.6 320 0 310.4 0 298.7zM405.3 320l-.7 0c26.6-23.5 43.3-57.8 43.3-96c0-7.6-.7-15-1.9-22.3c13.6-6.3 28.7-9.7 44.6-9.7h42.7C592.2 192 640 239.8 640 298.7c0 11.7-9.6 21.3-21.3 21.3H405.3zM224 224a96 96 0 1 1 192 0 96 96 0 1 1 -192 0zM128 485.3C128 411.7 187.7 352 261.3 352H378.7C452.3 352 512 411.7 512 485.3c0 14.7-11.9 26.7-26.7 26.7H154.7c-14.7 0-26.7-11.9-26.7-26.7z"/></svg>`;
const ICON_SKULL = `<svg viewBox="0 0 512 512" fill="currentColor"><path d="M416 398.9c58.5-41.1 96-104.1 96-174.9C512 100.3 397.4 0 256 0S0 100.3 0 224c0 70.7 37.5 133.8 96 174.9l0 33.1c0 17.7 14.3 32 32 32l32 0 0-32c0-17.7 14.3-32 32-32s32 14.3 32 32l0 32 32 0 0-32c0-17.7 14.3-32 32-32s32 14.3 32 32l0 32 32 0c17.7 0 32-14.3 32-32l0-33.1zM160 256a48 48 0 1 1 0-96 48 48 0 1 1 0 96zm192 0a48 48 0 1 1 0-96 48 48 0 1 1 0 96z"/></svg>`;
function plStatChip(icon, label, value, tone) {
  return `<div class="pl-stat pl-${tone}"><span class="pl-stat-ic">${icon}</span><div class="pl-stat-tx"><div class="pl-stat-v">${value}</div><div class="pl-stat-l">${label}</div></div></div>`;
}
async function loadPlayerStats() {
  const box = $("player-stats"); if (!box) return;
  if (!currentPlayer) { box.innerHTML = ""; return; }
  const tag = currentPlayer;
  let prof = null;
  try { prof = await getJSON("/api/player-profile?player=" + encodeURIComponent(tag)); } catch (e) { prof = null; }
  if (tag !== currentPlayer) return;  // el usuario cambió de jugador mientras cargaba
  if (!prof || prof.error) { box.innerHTML = ""; return; }
  myProfile = prof;
  const n = (v) => v != null ? Number(v).toLocaleString("es-ES") : null;
  const chips = [];
  const ht = n(prof.highest_trophies); if (ht != null) chips.push(plStatChip(ICON_TROPHY, "Copas máx.", ht, "gold"));
  const v3 = n(prof.victories_3v3); if (v3 != null) chips.push(plStatChip(ICON_3V3, "Victorias 3v3", v3, "cyan"));
  // Supervivencia = solo + dúo + trío (la API no expone trío, así que agrega las que da).
  const supParts = [prof.victories_solo, prof.victories_duo, prof.victories_trio].filter((v) => v != null);
  if (supParts.length) chips.push(plStatChip(ICON_SKULL, "Supervivencia", n(supParts.reduce((a, b) => a + Number(b), 0)), "magenta"));
  box.innerHTML = chips.length ? `<div class="pl-stat-group">${chips.join("")}</div>` : "";
}
function updatePlayerHeader() {
  const p = playersById[currentPlayer];
  $("player-name").textContent = p && p.name ? p.name : (currentPlayer || "—");
  $("player-tag").textContent = currentPlayer || "—";
  const club = $("player-club");
  if (p && p.club_name) { club.innerHTML = `Club · <b>${esc(p.club_name)}</b>`; club.style.display = ""; }
  else { club.style.display = "none"; }
  const img = $("player-icon");
  if (p && p.icon_url) { img.src = p.icon_url; img.style.display = ""; img.onerror = () => { img.style.display = "none"; }; }
  else { img.style.display = "none"; }
  $("player-stats").innerHTML = "";  // limpia mientras carga
  loadPlayerStats();
}
async function loadPlayers() {
  const players = await getJSON("/api/players");
  playersById = {}; players.forEach((p) => (playersById[p.tag] = p));
  $("player-select").innerHTML = players.map((p) => `<option value="${p.tag}">${p.name ? esc(p.name) + " " : ""}(${p.tag}) · ${p.battles} part.</option>`).join("");
  const has = players.length > 0;
  $("dashboard").style.display = has ? "" : "none";
  $("no-players").style.display = has ? "none" : "";
  $("remove-player").disabled = !has;
  if (!has) { currentPlayer = null; return; }
  if (!currentPlayer || !players.some((p) => p.tag === currentPlayer)) currentPlayer = players[0].tag;
  $("player-select").value = currentPlayer;
  updatePlayerHeader();
}
async function loadStatus() {
  const s = await getJSON("/api/status");
  const pp = $("proxy-pill"); if (pp) pp.classList.toggle("show", !!s.via_proxy);
  $("coach-hint").textContent = s.coach_configured ? "" : "Configura ANTHROPIC_API_KEY en el .env para activarlo.";
  const lp = s.last_poll || {};
  let txt = `${(s.players || []).length} jugador(es) · sondeo cada <b>${s.poll_interval}s</b>`;
  if (lp.at) txt += ` · <b>${fmtClock(lp.at)}</b>`;
  $("status").innerHTML = txt;
  if (!s.configured) { $("banner").classList.add("show"); $("banner").innerHTML = "Falta <code>BRAWL_API_TOKEN</code> en el <code>.env</code>. Configúralo y reinicia."; }
  else if (lp.error) { $("banner").classList.add("show"); $("banner").innerHTML = "Último sondeo con error: <code>" + esc(lp.error) + "</code>"; }
  else if (lp.not_found && lp.not_found.length) { $("banner").classList.add("show"); $("banner").innerHTML = "Estos tags ya no existen en la API de Brawl Stars y se omiten en el sondeo: <code>" + esc(lp.not_found.join(", ")) + "</code>. Quítalos (en el desplegable de jugador o en Administración → Jugadores) para que no vuelva a aparecer este aviso."; }
  else { $("banner").classList.remove("show"); }
}

async function refreshAll() {
  await loadStatus();
  await loadPlayers();
  if (currentPlayer) {
    await loadFilters(); applyScope(); await loadStats();
    if (activeTab === "report") { await loadReport(); loadRotation(); }
    if (activeTab === "rankings") loadRankings();
    if (activeTab === "history") await loadHistory(true);
    loadReports();
  }
}

/* ---------- "Qué jugar ahora" ---------- */
function prettyMode(m) { return (m || "").replace(/([a-z])([A-Z])/g, "$1 $2").replace(/\b\w/g, (c) => c.toUpperCase()); }
/* Traducción de modos al castellano. El key (gemGrab, hotZone…) se mantiene como
   valor interno (útil con la API y los iconos); solo cambia lo que se muestra.
   Los modos no listados caen a prettyMode (p.ej. wipeout -> "Wipeout"). */
const MODE_ES = {
  gemGrab: "Atrapagemas", brawlBall: "Balón Brawl", bounty: "Caza Estelar",
  heist: "Atraco", hotZone: "Zona Restringida", knockout: "Noqueo", duels: "Duelos",
  soloShowdown: "Supervivencia (solo)", duoShowdown: "Supervivencia (dúo)",
  trioShowdown: "Supervivencia (trío)", brawlHockey: "Brawl Hockey", wipeout: "Destrucción",
  siege: "Arena", Siege: "Arena",
  // Códigos crudos de la API (rotación) que difieren del canónico que guardamos:
  airHockey: "Brawl Hockey", deathmatch5v5: "Destrucción",
};
function modeName(key) { return key ? (MODE_ES[key] || prettyMode(key)) : ""; }
async function loadRotation() {
  const block = $("rotation-block"), grid = $("rotation-grid");
  if (!currentPlayer) { block.style.display = "none"; return; }
  let data;
  try { data = await getJSON("/api/rotation?player=" + encodeURIComponent(currentPlayer)); }
  catch (e) { block.style.display = "none"; return; }
  const events = (data && data.events) || [];
  if (!events.length) { block.style.display = "none"; return; }
  const trophy = events.filter((e) => e.category !== "ranked");
  const ranked = events.filter((e) => e.category === "ranked");
  const section = (label, sub, list) => list.length
    ? `<details class="m-acc2" open><summary class="rot-cat-label">${label}<span>${sub}</span></summary><div class="rotation-grid">${list.map(renderRotCard).join("")}</div></details>`
    : "";
  grid.innerHTML =
    section("🏆 Copas", "Modos y mapas que dan trofeos ahora mismo", trophy) +
    section("⚔️ Competitivo", "Pool de mapas del modo Competitivo (Ranked)", ranked);
  block.style.display = "";
}
function renderRotCard(e) {
  const wr = e.winrate;
  const cls = wr == null ? "" : (wr >= 55 ? "good" : (wr < 45 ? "bad" : ""));
  const ma = mapAsset(e.map), mapImg = ma && ma.image ? ma.image : null;
  const md = modeAsset(e.mode), modeIc = md && md.icon ? md.icon : null;
  const headStyle = mapImg
    ? ` style="background-image:linear-gradient(90deg, var(--bg-elev) 38%, rgba(17,24,36,.55)), url('${mapImg}')"`
    : "";
  const wrHtml = wr == null
    ? `<div class="rot-wr none">sin datos<span>aún no juegas aquí</span></div>`
    : `<div class="rot-wr" style="color:${pctColor(wr)}">${wr}%<span>tu WR · ${e.games} part.</span></div>`;
  const brs = (e.best_brawlers || []).map((b) => {
    const port = brawlerPortrait(b.brawler);
    const img = port ? `<img src="${port}" alt="" onerror="this.style.display='none'">` : "";
    const bwr = b.winrate == null ? "–" : b.winrate + "%";
    return `<div class="rot-br" title="${esc(b.brawler)} · ${bwr} en ${b.games} part.">
              ${img}<div class="rot-br-name">${esc(b.brawler)}</div>
              <div class="rot-br-wr" style="color:${pctColor(b.winrate)}">${bwr}</div>
            </div>`;
  }).join("");
  const brsHtml = brs
    ? `<div class="rot-brawlers"><div class="rot-br-label">Tus mejores brawlers aquí</div><div class="rot-br-row">${brs}</div></div>`
    : `<div class="rot-brawlers"><div class="rot-empty">Juega este mapa para ver tus mejores brawlers.</div></div>`;
  const mapAttr = mapImg ? ` data-map="${esc(e.map)}" title="Ver mapa"` : "";
  return `<div class="rot-card ${cls}"${mapAttr}>
    <div class="rot-head"${headStyle}>
      ${modeIc ? `<img class="rot-mode-ic" src="${modeIc}" alt="" onerror="this.style.display='none'">` : ""}
      <div class="rot-title"><div class="rot-map">${esc(e.map)}</div><div class="rot-mode">${esc(modeName(e.mode))}</div></div>
      ${wrHtml}
    </div>${brsHtml}</div>`;
}

/* ---------- Ventana de cuenta ---------- */
function openAccount() {
  $("account-username").textContent = currentUser && currentUser.username ? "@" + currentUser.username : "";
  const cs = $("um-country"); if (cs) cs.value = (currentUser && currentUser.country) || "";
  const saved = $("country-saved"); if (saved) { saved.textContent = ""; saved.style.color = ""; }
  $("account-modal").classList.add("open");
}
function closeAccount() { $("account-modal").classList.remove("open"); }
let _countrySavedTimer = null;
async function changeCountry() {
  const c = $("um-country").value;
  const saved = $("country-saved"); if (saved) { saved.textContent = ""; saved.style.color = ""; }
  try {
    const r = await fetch("/api/auth/country", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ country: c }) });
    const d = await r.json();
    if (r.ok) {
      if (currentUser) currentUser.country = d.country;
      if (saved) {
        saved.textContent = d.country ? "✓ País guardado (" + d.country.toUpperCase() + ")" : "✓ Sin país (solo global)";
        if (_countrySavedTimer) clearTimeout(_countrySavedTimer);
        _countrySavedTimer = setTimeout(() => { saved.textContent = ""; }, 4000);
      }
      updateScopeUI();
      if (activeTab === "rankings") loadRankings();
    } else if (saved) { saved.textContent = d.error || "No se pudo guardar."; saved.style.color = "var(--loss)"; }
  } catch (e) { if (saved) { saved.textContent = "Error de red."; saved.style.color = "var(--loss)"; } }
}

/* ---------- Ventana de contraseña ---------- */
let pwBusy = false;            // true mientras se actualiza: bloquea cierres accidentales
let pwAutoCloseTimer = null;
function openPassword() {
  $("pw-cur").value = ""; $("pw-new").value = ""; $("pw-new2").value = "";
  $("pw-err").textContent = "";
  showPwState("form");
  $("password-modal").classList.add("open");
  setTimeout(() => $("pw-cur").focus(), 60);
}
function closePassword() {
  if (pwBusy) return;
  if (pwAutoCloseTimer) { clearTimeout(pwAutoCloseTimer); pwAutoCloseTimer = null; }
  $("password-modal").classList.remove("open");
}
function showPwState(state) {
  $("pw-form").style.display = state === "form" ? "block" : "none";
  $("pw-loading").style.display = state === "loading" ? "flex" : "none";
  $("pw-success").style.display = state === "success" ? "flex" : "none";
}
async function submitPassword() {
  const cur = $("pw-cur").value, neu = $("pw-new").value, neu2 = $("pw-new2").value;
  const err = $("pw-err"); err.textContent = "";
  if (!cur || !neu || !neu2) { err.textContent = "Rellena todos los campos."; return; }
  if (neu.length < 6) { err.textContent = "La nueva contraseña debe tener al menos 6 caracteres."; return; }
  if (neu !== neu2) { err.textContent = "La nueva contraseña y su confirmación no coinciden."; return; }
  showPwState("loading"); pwBusy = true;
  const t0 = Date.now();
  let r = null, d = {}, netErr = false;
  try {
    r = await fetch("/api/auth/password", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ current: cur, new: neu }) });
    d = await r.json().catch(() => ({}));
  } catch (e) { netErr = true; }
  const minWait = Math.max(0, 800 - (Date.now() - t0));   // el spinner se ve al menos 0,8 s
  setTimeout(() => {
    pwBusy = false;
    if (netErr) { showPwState("form"); err.textContent = "Error de red. Inténtalo de nuevo."; return; }
    if (!r.ok) { showPwState("form"); err.textContent = d.error || d.detail || "No se pudo actualizar la contraseña."; return; }
    showPwState("success");
    pwAutoCloseTimer = setTimeout(closePassword, 10000);   // autocierre a los 10 s
  }, minWait);
}

