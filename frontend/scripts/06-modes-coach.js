/* BrawlSensei · 06-modes-coach.js
   manejadores de la app + Modos de Juego (heatmap, Hub de Modos, modal de mapa) + Consejos (informes IA).
   Se carga como <script src> desde index.html, en orden. El JS de cliente
   SIEMPRE es visible en el navegador: aquí no van secretos. */
/* ---------- Eventos ---------- */
$("player-select").addEventListener("change", async () => {
  currentPlayer = $("player-select").value;
  filterSel = { brawler: [], mode: [], map: [], role: [] };
  showCoachListView();
  updatePlayerHeader();
  await loadFilters(); applyScope(); await loadStats();
  if (activeTab === "report") { await loadReport(); loadRotation(); }
  if (activeTab === "rankings") loadRankings();
  if (activeTab === "history") await loadHistory(true);
  brawlersData = null; if (activeTab === "brawlers") loadBrawlers();
  senseiSel = { brawler: [], mode: [], map: [], role: [] }; loadSenseiQuiz(); loadReports();
});
async function onFilterChange() {
  applyScope(); await loadStats();
  if (activeTab === "report") await loadReport();
  if (activeTab === "history") await loadHistory(true);
}
$("load-more").addEventListener("click", () => loadHistory(false));

$("add-player").addEventListener("click", addPlayer);
$("add-tag").addEventListener("keydown", (e) => { if (e.key === "Enter") addPlayer(); });
async function addPlayer() {
  const tag = $("add-tag").value.trim(); if (!tag) return;
  const btn = $("add-player"); const word = btn.querySelector(".add-word");
  btn.disabled = true; if (word) word.textContent = " Buscando…";
  try {
    const r = await fetch("/api/players", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tag }) });
    const data = await r.json();
    if (data.error) alert(data.error);
    else { $("add-tag").value = ""; currentPlayer = data.tag; await refreshAll(); }
  } catch (e) { alert("Error de red al añadir el jugador."); }
  btn.disabled = false; if (word) word.textContent = " Añadir";
}
$("remove-player").addEventListener("click", async () => {
  if (!currentPlayer) return;
  if (!confirm(`¿Dejar de seguir a ${currentPlayer} y borrar sus datos?`)) return;
  await fetch("/api/players/" + encodeURIComponent(currentPlayer.replace("#", "")), { method: "DELETE" });
  currentPlayer = null; await refreshAll();
});
$("refresh").addEventListener("click", async () => {
  const btn = $("refresh"); btn.disabled = true; btn.textContent = "Sondeando…";
  try {
    const url = currentPlayer ? "/api/poll?player=" + encodeURIComponent(currentPlayer) : "/api/poll";
    const r = await fetch(url, { method: "POST" }); const data = await r.json();
    if (data.error) alert("No se pudo consultar la API:\n" + data.error);
  } catch (e) { alert("Error de red al sondear."); }
  await refreshAll(); btn.disabled = false; btn.textContent = "Actualizar ahora";
});
$("coach-btn").addEventListener("click", () => generateReport());
$("coach-back").addEventListener("click", () => backToReportList());

/* ---------- Informe (cálculos derivados) ---------- */
function fmtDateTime(iso) { try { return new Date(iso).toLocaleString("es-ES", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }); } catch { return iso; } }

async function loadReport() {  // pestaña "Modos de Juego": podio versátil + Hub + heatmap
  if (!currentPlayer) return;
  const a = await getJSON("/api/report?" + qs());
  $("heatmap").innerHTML = heatmap(a.crosstab);
  renderHubButtons(a.by_mode || []);
  try { renderVersatileTop13((await getJSON("/api/versatile?" + qs())).versatile || []); }
  catch (e) { /* 401 gestionado por getJSON */ }
}

/* Top 13 brawlers más versátiles (mismo podio que Brawlers, ordenado por win rate
   medio entre modos en vez de por copas). */
function renderVersatileTop13(top) {
  const el = $("versatile-top13");
  if (!el) return;
  if (!top.length) { el.innerHTML = ""; return; }
  const podium = top.slice(0, 3).map((b, i) => ({ ...b, pos: i + 1 }));
  const order = [podium[1], podium[0], podium[2]].filter(Boolean);  // 2 · 1 · 3
  const podiumHtml = order.map((b) => {
    const src = b.image_full || b.portrait;
    const img = src ? `<img src="${src}" alt="" onerror="this.style.display='none'">` : "";
    return `<div class="podium-col pos${b.pos}" onclick="gotoBrawler(${b.id})" title="Ver ficha">
      <div class="podium-img">${img}</div>
      <div class="podium-base"><span class="podium-pos">${b.pos}</span>
        <span class="podium-name">${esc(b.name)}</span>
        <span class="podium-tro" style="color:${pctColor(b.avg_winrate)}">Rend. ${b.avg_winrate}</span></div></div>`;
  }).join("");
  const winnersMini = podium.map((b) => {
    const img = b.portrait ? `<img src="${b.portrait}" alt="" onerror="this.style.display='none'">` : "";
    return `<div class="winner-row" onclick="gotoBrawler(${b.id})" title="Ver ficha">
      <span class="wm-pos">${b.pos}</span>${img}
      <div class="wm-tx"><span class="wm-name">${esc(b.name)}</span>
        <span class="wm-sub"><b style="color:${pctColor(b.avg_winrate)}">Rend. ${b.avg_winrate}</b> · ${b.modes_played} modos</span></div></div>`;
  }).join("");
  const effRows = podium.map((b) => {
    const wr = b.avg_winrate, w = Math.max(3, wr);
    return `<div class="eff-row"><span class="eff-name">${esc(b.name)}</span>
      <div class="eff-bar-wrap"><div class="eff-bar" style="width:${w}%;background:${pctColor(wr)}"></div></div>
      <span class="eff-val" style="color:${pctColor(wr)}">${wr}%</span></div>`;
  }).join("");
  const restHtml = top.slice(3, 13).map((b, i) => {
    const img = b.portrait ? `<img src="${b.portrait}" alt="" onerror="this.style.display='none'">` : "";
    return `<div class="top-mini" onclick="gotoBrawler(${b.id})" title="Ver ficha">
      <span class="top-mini-pos">${i + 4}</span>${img}
      <div class="top-mini-tx"><span class="top-mini-name">${esc(b.name)}</span>
        <span class="top-mini-tro" style="color:${pctColor(b.avg_winrate)}">Rend. ${b.avg_winrate}</span></div></div>`;
  }).join("");
  el.innerHTML = `<div class="top10-panel">
    <h2><span class="dot"></span>Top 13 Brawlers más versátiles</h2>
    <p class="hint" style="margin:-4px 0 14px">Tus brawlers con mejor <b>rendimiento medio</b> entre los modos que juegas — ajustado a la dificultad del entorno (trofeos), no solo win rate.</p>
    <div class="top13-main">
      <div class="podium-extra extra-left"><div class="extra-title">Versatilidad</div>${winnersMini}</div>
      <div class="podium">${podiumHtml}</div>
      <div class="podium-extra extra-right"><div class="extra-title">Win rate medio</div>${effRows}</div>
    </div>
    ${restHtml ? `<div class="top-mini-row">${restHtml}</div>` : ""}</div>`;
}

async function gotoBrawler(id) {
  if (!id) return;
  switchTab("brawlers");
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === "brawlers"));
  try { await loadBrawlers(); } catch (e) { /* ignore */ }
  if (typeof showBrawlerDetail === "function") showBrawlerDetail(id);
}

/* ---------- Hub de Modos ---------- */
let hubMode = null;
function renderHubButtons(byMode) {
  const block = $("hub-block"), btns = $("hub-buttons");
  if (!block || !btns) return;
  const modes = (byMode || []).filter((m) => m.label && m.total > 0).sort((x, y) => y.total - x.total);
  if (!modes.length) { block.style.display = "none"; hubMode = null; $("hub-content").innerHTML = ""; return; }
  block.style.display = "";
  btns.innerHTML = modes.map((m) => {
    const a = modeAsset(m.label), color = modeColor(m.label, 0);
    const ic = a && a.icon ? `<img src="${a.icon}" alt="" onerror="this.style.display='none'">` : "";
    return `<button class="hub-btn ${hubMode === m.label ? "active" : ""}" data-mode="${esc(m.label)}" style="--mc:${color}">
      ${ic}<span class="hub-btn-name">${esc(modeName(m.label))}</span>
      <span class="hub-btn-meta">${m.total}p · ${m.winrate == null ? "—" : m.winrate + "%"}</span></button>`;
  }).join("");
  btns.querySelectorAll(".hub-btn").forEach((b) => b.addEventListener("click", () => selectHubMode(b.dataset.mode)));
  if (hubMode && modes.some((m) => m.label === hubMode)) selectHubMode(hubMode, true);
  else { hubMode = null; $("hub-content").innerHTML = ""; }
}
async function selectHubMode(mode, force) {
  if (hubMode === mode && !force) {  // toggle: cerrar
    hubMode = null;
    document.querySelectorAll(".hub-btn").forEach((b) => b.classList.remove("active"));
    $("hub-content").innerHTML = "";
    return;
  }
  hubMode = mode;
  document.querySelectorAll(".hub-btn").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
  $("hub-content").innerHTML = `<div class="empty" style="padding:34px">Cargando ${esc(modeName(mode))}…</div>`;
  try {
    const d = await getJSON(`/api/mode-hub?player=${encodeURIComponent(currentPlayer)}&mode=${encodeURIComponent(mode)}`);
    if (hubMode !== mode) return;  // el usuario cambió mientras cargaba
    $("hub-content").innerHTML = renderModeHub(d);
    $("hub-content").querySelectorAll(".hub-map-card").forEach((c) =>
      c.addEventListener("click", () => openMapModal(c.dataset.map, c.dataset.mode)));
  } catch (e) { $("hub-content").innerHTML = `<div class="empty" style="padding:30px">No se pudo cargar el modo.</div>`; }
}
function brawlerStatRow(name, valueText, color) {
  const port = brawlerPortrait(name);
  const img = port ? `<img src="${port}" alt="" onerror="this.style.display='none'">` : "";
  return `<div class="meta-row">${img}<span class="meta-name">${esc(name)}</span><span class="meta-val" style="color:${color}">${valueText}</span></div>`;
}
function miniSpark(series) {
  const pts = (series || []).map((p) => p.cumulative);
  if (pts.length < 2) return `<div class="empty" style="padding:8px;font-size:12px">Pocas partidas para la gráfica.</div>`;
  const min = Math.min(...pts, 0), max = Math.max(...pts, 0), range = (max - min) || 1, W = 260, H = 60;
  const xy = pts.map((v, i) => [((i / (pts.length - 1)) * W).toFixed(1), (H - ((v - min) / range) * H).toFixed(1)]);
  const line = xy.map((c) => c.join(",")).join(" ");
  const col = pts[pts.length - 1] >= (pts[0] || 0) ? "var(--win)" : "var(--loss)";
  return `<svg viewBox="0 0 ${W} ${H}" class="hub-spark-svg" preserveAspectRatio="none">
    <polyline points="0,${H} ${line} ${W},${H}" fill="${col}" opacity="0.12"/>
    <polyline points="${line}" fill="none" stroke="${col}" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>`;
}
function renderModeHub(d) {
  const wr = d.your.winrate, g = d.guide || {};
  const bestB = (d.your.best_brawlers || []).slice(0, 5)
    .map((b) => brawlerStatRow(b.label, b.winrate == null ? "—" : b.winrate + "%", pctColor(b.winrate))).join("") || `<div class="empty">Aún sin datos.</div>`;
  const statsRow = `<div class="hub-stats">
    <div class="hub-stat-main">
      <div class="hub-wr" style="color:${pctColor(wr)}">${wr == null ? "—" : wr + "%"}</div>
      <div class="hub-wr-sub">tu win rate · ${d.your.total} partidas${d.your.trophy_delta != null ? ` · <b style="color:${d.your.trophy_delta >= 0 ? "var(--win)" : "var(--loss)"}">${d.your.trophy_delta >= 0 ? "+" : ""}${d.your.trophy_delta}🏆</b>` : ""}</div>
      <div class="hub-spark">${miniSpark(d.your.trophy_series)}</div>
    </div>
    <div class="hub-stat-side"><div class="hub-side-title">Tus mejores brawlers aquí</div>${bestB}</div>
  </div>`;
  const intro = g.intro ? `<div class="hub-intro"><p>${esc(g.intro)}</p>
    ${g.objective ? `<p><b>Objetivo:</b> ${esc(g.objective)}</p>` : ""}
    ${(g.tips || []).length ? `<ul class="hub-tips">${g.tips.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>` : ""}</div>` : "";
  return `${statsRow}${intro}
    <h3 class="hub-h3"><span class="dot"></span>Meta Comunitario</h3>
    <div class="hub-hint">Tier list propio de BrawlSensei (datos reales de todos los jugadores), y tu desviación respecto a la media.</div>
    ${renderMetaComunitario(d)}
    <h3 class="hub-h3"><span class="dot"></span>Mapas</h3>
    ${renderHubMaps(d.mode, d.maps)}`;
}
function renderMetaComunitario(d) {
  const c = d.community || {};
  const pickL = (c.by_pick || []).slice(0, 6).map((b) => brawlerStatRow(b.brawler, b.pick_rate + "%", "var(--cyan)")).join("") || `<div class="empty">Sin datos de la comunidad.</div>`;
  const wrL = (c.by_winrate || []).slice(0, 6).map((b) => brawlerStatRow(b.brawler, b.winrate == null ? "—" : b.winrate + "%", pctColor(b.winrate))).join("") || `<div class="empty">Sin datos.</div>`;
  const ins = (d.insights || []).map((i) => `<li>${esc(i)}</li>`).join("");
  return `<div class="hub-meta">
    <div class="hub-meta-col"><div class="hub-side-title">Más usados (pick rate)</div>${pickL}</div>
    <div class="hub-meta-col"><div class="hub-side-title">Mejor win rate comunitario</div>${wrL}</div>
    <div class="hub-meta-col hub-insights"><div class="hub-side-title">Tu desviación</div>${ins ? `<ul>${ins}</ul>` : `<div class="empty">Juega más partidas para compararte con la media.</div>`}</div>
  </div>`;
}
function hubMapCard(m, mode) {
  const badge = m.category === "trophy" ? `<span class="map-cat trophy" title="En rotación · Copas">🏆</span>`
    : m.category === "ranked" ? `<span class="map-cat ranked" title="En rotación · Competitivo">🏅</span>` : "";
  const img = m.image ? `<img src="${m.image}" alt="" loading="lazy" onerror="this.style.display='none'">` : `<div class="hub-map-noimg">🗺️</div>`;
  const wr = m.your_winrate == null ? `<span class="map-wr none">—</span>` : `<span class="map-wr" style="color:${pctColor(m.your_winrate)}">${m.your_winrate}%</span>`;
  return `<div class="hub-map-card ${m.active ? "" : "inactive"}" data-map="${esc(m.name)}" data-mode="${esc(mode)}">
    <div class="hub-map-img">${img}${badge}</div>
    <div class="hub-map-foot"><span class="hub-map-name">${esc(m.name)}</span>${wr}</div></div>`;
}
function renderHubMaps(mode, maps) {
  const rot = (maps.rotation || []).map((m) => hubMapCard(m, mode)).join("");
  const oth = (maps.others || []).map((m) => hubMapCard(m, mode)).join("");
  return `${rot ? `<div class="hub-map-sub">Mapas en rotación</div><div class="hub-map-grid">${rot}</div>` : ""}
    ${oth ? `<div class="hub-map-sub">Otros mapas</div><div class="hub-map-grid">${oth}</div>` : ""}
    ${!rot && !oth ? `<div class="empty">Sin mapas para este modo.</div>` : ""}`;
}
function draftRow(x) {
  const port = brawlerPortrait(x.brawler);
  const img = port ? `<img src="${port}" alt="" onerror="this.style.display='none'">` : "";
  const you = x.your_winrate == null ? "" : `<span class="draft-you" title="tu win rate aquí">tú ${x.your_winrate}%</span>`;
  return `<div class="draft-row">${img}<span class="meta-name">${esc(x.brawler)}</span>
    <span class="draft-comm" title="win rate del meta comunitario">meta ${Math.round(x.community_winrate)}%</span>${you}</div>`;
}
async function openMapModal(map, mode) {
  const modal = $("map-modal"), card = $("map-modal-card");
  card.innerHTML = `<button class="map-modal-close" onclick="closeMapModal()" aria-label="Cerrar">&times;</button><div class="empty" style="padding:46px">Cargando ${esc(map)}…</div>`;
  modal.classList.add("show");
  try {
    const d = await getJSON(`/api/map-detail?player=${encodeURIComponent(currentPlayer)}&map=${encodeURIComponent(map)}&mode=${encodeURIComponent(mode || "")}`);
    card.innerHTML = renderMapModal(d);
  } catch (e) { card.innerHTML = `<button class="map-modal-close" onclick="closeMapModal()">&times;</button><div class="empty" style="padding:46px">No se pudo cargar el mapa.</div>`; }
}
function renderMapModal(d) {
  const wr = d.your.winrate;
  const list = (items) => (items || []).length
    ? items.map((x) => brawlerStatRow(x.label || x.brawler, x.winrate == null ? "—" : x.winrate + "%", pctColor(x.winrate))).join("")
    : `<div class="empty">Sin datos todavía.</div>`;
  const draft = (d.draft || []).map(draftRow).join("") || `<div class="empty">Juega o espera más datos de la comunidad.</div>`;
  const tips = (d.tips || []).map((t) => `<li>${esc(t)}</li>`).join("");
  const headBg = d.image ? ` style="background-image:linear-gradient(180deg,rgba(12,12,30,.4),rgba(12,12,30,.94)),url('${d.image}')"` : "";
  return `<button class="map-modal-close" onclick="closeMapModal()" aria-label="Cerrar">&times;</button>
    <div class="mm-head"${headBg}>
      <h2>${esc(d.map)}</h2>
      <div class="mm-wr">${wr == null ? "Aún no juegas aquí" : `Tu win rate: <b style="color:${pctColor(wr)}">${wr}%</b> · ${d.your.total} partidas`}</div>
    </div>
    <div class="mm-body">
      <div class="mm-col">
        <div class="mm-block"><div class="mm-title">⚔️ Ayudante de draft</div>
          <div class="mm-hint">Mejores brawlers aquí cruzando el meta comunitario con tu historial.</div>${draft}</div>
        ${tips ? `<div class="mm-block"><div class="mm-title">💡 Consejos</div><ul class="hub-tips">${tips}</ul></div>` : ""}
      </div>
      <div class="mm-col">
        <div class="mm-block"><div class="mm-title">Tus mejores brawlers</div>${list(d.your.best_brawlers)}</div>
        <div class="mm-block"><div class="mm-title">Mejores aliados</div>${list(d.your.best_allies)}</div>
        <div class="mm-block"><div class="mm-title">Rivales más duros</div>${list(d.your.worst_enemies)}</div>
      </div>
    </div>`;
}
function closeMapModal() { $("map-modal").classList.remove("show"); }
window.closeMapModal = closeMapModal;
function renderStreak(s) {
  const el = $("streak-banner");
  if (!el) return;
  if (!s || !s.count) { el.style.display = "none"; return; }
  el.style.display = "";
  const win = s.type === "win";
  el.className = "streak-banner " + (win ? "win" : "loss");
  const noun = win ? (s.count === 1 ? "victoria seguida" : "victorias seguidas")
                   : (s.count === 1 ? "derrota seguida" : "derrotas seguidas");
  el.innerHTML = `${win ? "🔥" : "❄️"} Racha actual: <b>${s.count}</b> ${noun}`;
}
function analyticsBar(label, winrate, total, note) {
  const color = pctColor(winrate);
  const pct = winrate == null ? "—" : winrate + "%";
  const barW = winrate == null ? 0 : Math.max(2, winrate);
  return `<div class="row"><div class="name">${label}</div>
    <div class="pct" style="color:${color}">${pct}</div>
    <div class="bar-wrap"><div class="bar" style="width:${barW}%;background:${color}"></div></div>
    <div class="meta">${total} ${total === 1 ? "partida" : "partidas"}${note ? " · " + note : ""}</div></div>`;
}
function renderBuckets(buckets, hideEmpty) {
  const rows = hideEmpty ? (buckets || []).filter((b) => b.total > 0) : (buckets || []);
  if (!rows.length) return `<div class="empty">Sin datos suficientes todavía.</div>`;
  return rows.map((b) => analyticsBar(esc(b.label), b.winrate, b.total)).join("");
}
function renderHourly(points) {
  const fr = [
    { label: "Madrugada (00–06)", lo: 0, hi: 6, w: 0, t: 0 },
    { label: "Mañana (06–12)", lo: 6, hi: 12, w: 0, t: 0 },
    { label: "Tarde (12–18)", lo: 12, hi: 18, w: 0, t: 0 },
    { label: "Noche (18–24)", lo: 18, hi: 24, w: 0, t: 0 },
  ];
  for (const p of points || []) {
    if (p.is_win == null) continue;
    const h = new Date(p.time).getHours();
    if (isNaN(h)) continue;
    const b = fr.find((x) => h >= x.lo && h < x.hi);
    if (b) { b.t++; if (p.is_win === 1) b.w++; }
  }
  if (!fr.some((b) => b.t > 0)) return `<div class="empty">Sin datos suficientes todavía.</div>`;
  return fr.map((b) => analyticsBar(b.label, b.t ? Math.round((b.w / b.t) * 100) : null, b.t)).join("");
}
function winrateChart(series) {
  if (!series || series.length < 2) return `<div class="empty">Necesitas algunas partidas más para ver la forma reciente.</div>`;
  const W = 500, H = 240, pad = 26;
  const X = (i) => pad + (W - 2 * pad) * (i / (series.length - 1));
  const Y = (v) => H - pad - (H - 2 * pad) * (v / 100);
  const line = series.map((p, i) => `${X(i).toFixed(1)},${Y(p.winrate).toFixed(1)}`).join(" ");
  const last = series[series.length - 1].winrate, color = pctColor(last), y50 = Y(50).toFixed(1);
  return `<svg viewBox="0 0 ${W} ${H}" class="trophy-svg">
    <line x1="${pad}" y1="${y50}" x2="${W - pad}" y2="${y50}" stroke="var(--border)" stroke-dasharray="4 4" />
    <polyline points="${line}" fill="none" stroke="${color}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round" />
  </svg>
  <div class="chart-cap">Win rate en ventana móvil. Ahora mismo: <b style="color:${color}">${last}%</b></div>`;
}
function renderValuation(byBrawler) {
  const under = $("val-under"), over = $("val-over");
  if (!under || !over) return;
  const elig = (byBrawler || []).filter((b) => b.winrate != null && b.total >= 2);
  if (!elig.length) {
    const msg = `<div class="empty">Sin datos suficientes todavía.</div>`;
    under.innerHTML = msg; over.innerHTML = msg; return;
  }
  const avg = elig.reduce((s, b) => s + b.total, 0) / elig.length;
  const u = elig.filter((b) => b.winrate >= 55 && b.total < avg).sort((a, b) => b.winrate - a.winrate).slice(0, 5);
  const o = elig.filter((b) => b.winrate < 45 && b.total >= avg).sort((a, b) => a.winrate - b.winrate).slice(0, 5);
  under.innerHTML = u.length ? u.map((b) => rowHTML(b, "brawler")).join("") : `<div class="empty">Nada destacable: no tienes brawlers con buen win rate y pocas partidas.</div>`;
  over.innerHTML = o.length ? o.map((b) => rowHTML(b, "brawler")).join("") : `<div class="empty">Nada que revisar: no abusas de ningún brawler flojo.</div>`;
  applyCollapse(under); applyCollapse(over);
}
function hlCard(icon, label, item, kind, valueFn, cat) {
  let inner = icon, name = "—", value = "—";
  if (item) {
    value = valueFn(item); name = esc(item.label);
    if (kind === "brawler" && brawlerPortrait(item.label)) inner = imgTag(brawlerPortrait(item.label), "hl-portrait");
    else if (kind === "mode") { name = esc(modeName(item.label)); const a = modeAsset(item.label); if (a) inner = imgTag(a.icon, "hl-portrait"); }
    else if (kind === "map" && mapAsset(item.label)) name = `<span class="map-link" data-map="${esc(item.label)}">${esc(item.label)}</span>`;
  }
  const open = cat && item
    ? `<div class="hl-card hl-clickable" onclick="openHlModal('${cat}')" title="Ver el Top 15">`
    : `<div class="hl-card">`;
  return `${open}<div class="hl-visual">${inner}</div><div class="hl-body"><div class="hl-label">${label}</div><div class="hl-name">${name}</div><div class="hl-value">${value}</div></div></div>`;
}
function renderHighlights(h) {
  $("highlights").innerHTML = [
    hlCard("🎯", "Más jugado", h.most_played, "brawler", (v) => `${v.total} partidas`, "most_played"),
    hlCard("🏆", "Mejor rendimiento", h.best_brawler, "brawler", (v) => `Rend. ${adjVal(v)} · ${v.total}p`, "best"),
    hlCard("⚠️", "Peor rendimiento", h.worst_brawler, "brawler", (v) => `Rend. ${adjVal(v)} · ${v.total}p`, "worst"),
    hlCard("🤝", "Mejor aliado", h.best_ally, "brawler", (v) => `${v.winrate}% (${v.total}p)`, "best_ally"),
    hlCard("😈", "Rival más duro", h.hardest_vs, "brawler", (v) => `${v.winrate}% (${v.total}p)`, "hardest_vs"),
    hlCard("😎", "Rival más fácil", h.easiest_vs, "brawler", (v) => `${v.winrate}% (${v.total}p)`, "easiest_vs"),
    hlCard("🧭", "Mejor modo", h.best_mode, "mode", (v) => `${v.winrate}% (${v.total}p)`, "best_mode"),
    hlCard("🗺️", "Mejor mapa", h.best_map, "map", (v) => `${v.winrate}% (${v.total}p)`, "best_map"),
  ].join("");
}

/* Modal Top 16 al pulsar cualquier tarjeta de destacado (Analíticas, 2ª fila). */
function adjVal(r) { return r && r.adj_score != null ? r.adj_score : (r ? r.winrate : null); }
const HL_CATS = {
  most_played: { title: "Más jugados", sub: "Tus brawlers por número de partidas", url: "/api/winrate?by=brawler", kind: "brawler", sort: (a, b) => b.total - a.total, val: (v) => `${v.total} partidas`, color: false },
  best: { title: "Mejor rendimiento", sub: "Tus brawlers por rendimiento ajustado a la dificultad (mín. 3 partidas)", url: "/api/winrate?by=brawler", kind: "brawler", min: 3, adj: true, sort: (a, b) => adjVal(b) - adjVal(a), val: (v) => `Rend. ${adjVal(v)} · ${v.total}p`, color: true },
  worst: { title: "Peor rendimiento", sub: "Tus brawlers por rendimiento ajustado a la dificultad (mín. 3 partidas)", url: "/api/winrate?by=brawler", kind: "brawler", min: 3, adj: true, sort: (a, b) => adjVal(a) - adjVal(b), val: (v) => `Rend. ${adjVal(v)} · ${v.total}p`, color: true },
  best_ally: { title: "Mejores aliados", sub: "Win rate cuando van en tu equipo (mín. 2 partidas)", url: "/api/allies", kind: "brawler", min: 2, sort: (a, b) => b.winrate - a.winrate, val: (v) => `${v.winrate}% · ${v.total}p`, color: true },
  hardest_vs: { title: "Rivales más duros", sub: "Contra los que peor win rate tienes (mín. 2 partidas)", url: "/api/vs", kind: "brawler", min: 2, sort: (a, b) => a.winrate - b.winrate, val: (v) => `${v.winrate}% · ${v.total}p`, color: true },
  easiest_vs: { title: "Rivales más fáciles", sub: "Contra los que mejor win rate tienes (mín. 2 partidas)", url: "/api/vs", kind: "brawler", min: 2, sort: (a, b) => b.winrate - a.winrate, val: (v) => `${v.winrate}% · ${v.total}p`, color: true },
  best_mode: { title: "Tus modos · win rate", sub: "Tu rendimiento por modo de juego", url: "/api/winrate?by=mode", kind: "mode", min: 1, sort: (a, b) => b.winrate - a.winrate, val: (v) => `${v.winrate}% · ${v.total}p`, color: true, single: true },
  best_map: { title: "Mejores mapas", sub: "Tu win rate por mapa (mín. 2 partidas)", url: "/api/winrate?by=map", kind: "map", min: 2, sort: (a, b) => b.winrate - a.winrate, val: (v) => `${v.winrate}% · ${v.total}p`, color: true },
};
async function openHlModal(cat) {
  const c = HL_CATS[cat];
  if (!c) return;
  let rows;
  const sep = c.url.includes("?") ? "&" : "?";
  try { rows = await getJSON(c.url + sep + qs()); } catch (e) { return; }
  let list = (rows || []).filter((r) => (r.total || 0) > 0 && r.winrate != null);
  if (c.min) list = list.filter((r) => (r.total || 0) >= c.min);
  list.sort(c.sort);
  list = list.slice(0, 16);
  $("hl-modal-title").textContent = c.title;
  $("hl-modal-sub").textContent = c.sub;
  const body = $("hl-modal-body");
  body.className = "hl-list" + (c.single ? " single" : "");
  body.innerHTML = list.length
    ? list.map((r, i) => {
        let vis = "", nm = esc(r.label);
        if (c.kind === "mode") { const a = modeAsset(r.label); vis = a && a.icon ? imgTag(a.icon, "hl-row-ic") : ""; nm = esc(modeName(r.label)); }
        else if (c.kind === "brawler") { const p = brawlerPortrait(r.label); vis = p ? `<img src="${p}" alt="" onerror="this.style.display='none'">` : ""; }
        const color = c.color ? pctColor(c.adj ? adjVal(r) : r.winrate) : "var(--text)";
        return `<div class="hl-row"><span class="hl-rank">${i + 1}</span>${vis}
          <span class="hl-row-name">${nm}</span>
          <span class="hl-row-val" style="color:${color}">${c.val(r)}</span></div>`;
      }).join("")
    : `<div class="hint">Sin datos en este ámbito todavía.</div>`;
  openEvModal("hl-modal");
}
function trophyChart(series) {
  if (!series || series.length < 2) return `<div class="empty">Necesitas algunas partidas más para ver la evolución.</div>`;
  const vals = series.map((p) => p.cumulative);
  const min = Math.min(0, ...vals), max = Math.max(0, ...vals), span = (max - min) || 1;
  const W = 500, H = 240, pad = 26;
  const X = (i) => pad + (W - 2 * pad) * (i / (series.length - 1));
  const Y = (v) => H - pad - (H - 2 * pad) * ((v - min) / span);
  const line = series.map((p, i) => `${X(i).toFixed(1)},${Y(p.cumulative).toFixed(1)}`).join(" ");
  const area = `${X(0).toFixed(1)},${Y(min).toFixed(1)} ${line} ${X(series.length - 1).toFixed(1)},${Y(min).toFixed(1)}`;
  const last = vals[vals.length - 1], color = last >= 0 ? "var(--win)" : "var(--loss)", zeroY = Y(0).toFixed(1);
  return `<svg viewBox="0 0 ${W} ${H}" class="trophy-svg">
    <polygon points="${area}" fill="${color}" opacity="0.08" />
    <line x1="${pad}" y1="${zeroY}" x2="${W - pad}" y2="${zeroY}" stroke="var(--border)" stroke-dasharray="4 4" />
    <polyline points="${line}" fill="none" stroke="${color}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round" />
  </svg>
  <div class="chart-cap">Balance acumulado: <b style="color:${color}">${last >= 0 ? "+" : ""}${last} 🏆</b> en ${series.length} partidas</div>`;
}

/* ---------- Donuts de modos (en la pestaña Estadísticas) ---------- */
function modeColor(label, i) {
  const a = modeAsset(label);
  if (a && a.color) return a.color;  // color real del modo (Brawlify) si lo hay
  const pal = ["#5b54ff", "#3fe1ff", "#c64ff0", "#36e0a0", "#f5b82a", "#ff4d73", "#ff8e3c", "#b388ff"];
  return pal[i % pal.length];
}
function donutChart(items, centerLabel) {
  const total = items.reduce((s, it) => s + it.value, 0) || 1;
  const r = 56, cx = 75, cy = 75, C = 2 * Math.PI * r;
  let off = 0;
  const arcs = items.map((it) => {
    const len = (it.value / total) * C;
    const seg = `<circle r="${r}" cx="${cx}" cy="${cy}" fill="none" stroke="${it.color}" stroke-width="20" stroke-linecap="butt" stroke-dasharray="${len.toFixed(2)} ${(C - len).toFixed(2)}" stroke-dashoffset="${(-off).toFixed(2)}" transform="rotate(-90 ${cx} ${cy})"><title>${esc(modeName(it.label))}: ${it.display}</title></circle>`;
    off += len; return seg;
  }).join("");
  const legend = items.map((it) => {
    const a = modeAsset(it.label);
    const ic = a && a.icon ? `<img src="${a.icon}" alt="" onerror="this.style.display='none'">` : "";
    return `<div class="dleg-row"><span class="dleg-dot" style="background:${it.color}"></span>${ic}<span class="dleg-name">${esc(modeName(it.label))}</span><span class="dleg-val">${it.display}</span></div>`;
  }).join("");
  return `<div class="donut-wrap"><svg viewBox="0 0 150 150" class="donut-svg">${arcs}
    <text x="${cx}" y="${cy - 3}" class="donut-center-n">${items.length}</text>
    <text x="${cx}" y="${cy + 14}" class="donut-center-l">${esc(centerLabel)}</text></svg>
    <div class="donut-legend">${legend}</div></div>`;
}
function renderModeDonuts(modeData) {
  const playEl = $("mode-play-donut"), wrEl = $("mode-wr-donut");
  if (!playEl || !wrEl) return;
  const min = 1;
  const data = (modeData || []).filter((d) => d.label && d.total >= min);
  if (!data.length) {
    const e = `<div class="empty">Sin datos suficientes todavía.</div>`;
    playEl.innerHTML = e; wrEl.innerHTML = e; return;
  }
  const byPlay = data.slice().sort((a, b) => b.total - a.total)
    .map((d, i) => ({ label: d.label, value: d.total, display: d.total + "p", color: modeColor(d.label, i) }));
  playEl.innerHTML = donutChart(byPlay, "modos");
  const byWr = data.filter((d) => d.winrate != null).sort((a, b) => b.winrate - a.winrate)
    .map((d, i) => ({ label: d.label, value: Math.max(1, d.winrate), display: d.winrate + "%", color: modeColor(d.label, i) }));
  wrEl.innerHTML = byWr.length ? donutChart(byWr, "win rate") : `<div class="empty">Sin win rate por modo todavía.</div>`;
}

/* ---------- Radares de rol (Preferencia de rol / Estilo de juego) ---------- */
// Orden alrededor del radar pensado para que roles parecidos queden contiguos:
// frontline -> cuerpo a cuerpo -> daño -> rango -> lanzado -> control -> apoyo.
const ROLE_ORDER = ["Tanque", "Agresor", "Asesino", "Destructor", "Tiro de Élite",
                    "Artillería", "Lanzador", "Invocador", "Control", "Apoyo", "Curador"];
const ROLE_SHORT = { "Tiro de Élite": "T. Élite" };
function roleRadar(data, key, color, gid) {
  const by = {}; (data || []).forEach((d) => { by[d.label] = d; });
  const N = ROLE_ORDER.length, cx = 180, cy = 162, R = 98;
  const vals = ROLE_ORDER.map((r) => { const d = by[r]; return d && d[key] != null ? +d[key] : 0; });
  const maxV = key === "winrate" ? 100 : Math.max(...vals, 1);
  const ang = (i) => -Math.PI / 2 + i * 2 * Math.PI / N;
  const pt = (i, f) => [cx + R * f * Math.cos(ang(i)), cy + R * f * Math.sin(ang(i))];
  const fmt = (i) => { const d = by[ROLE_ORDER[i]]; if (!d) return ""; return key === "winrate" ? (d.winrate == null ? "—" : d.winrate + "%") : d.usage_pct + "%"; };
  let rings = "";
  for (let g = 1; g <= 4; g++) {
    const pts = ROLE_ORDER.map((_, i) => pt(i, g / 4).map((v) => v.toFixed(1)).join(",")).join(" ");
    rings += `<polygon points="${pts}" fill="none" stroke="rgba(255,255,255,${g === 4 ? 0.14 : 0.06})" stroke-width="1"/>`;
  }
  let spokes = "", labels = "";
  ROLE_ORDER.forEach((role, i) => {
    const [ex, ey] = pt(i, 1);
    spokes += `<line x1="${cx}" y1="${cy}" x2="${ex.toFixed(1)}" y2="${ey.toFixed(1)}" stroke="rgba(255,255,255,0.05)"/>`;
    const [lx, ly] = pt(i, 1.16);
    const c = Math.cos(ang(i));
    const anchor = Math.abs(c) < 0.34 ? "middle" : (c > 0 ? "start" : "end");
    const v = fmt(i);
    labels += `<text x="${lx.toFixed(1)}" y="${ly.toFixed(1)}" text-anchor="${anchor}" class="radar-lbl">${esc(ROLE_SHORT[role] || role)}${v ? `<tspan x="${lx.toFixed(1)}" dy="11" class="radar-val">${v}</tspan>` : ""}</text>`;
  });
  const poly = vals.map((v, i) => pt(i, v / maxV).map((x) => x.toFixed(1)).join(",")).join(" ");
  const dots = vals.map((v, i) => { if (v <= 0) return ""; const [x, y] = pt(i, v / maxV); return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.6" fill="${color}"/>`; }).join("");
  return `<svg viewBox="0 0 360 320" class="radar-svg">
    <defs><radialGradient id="${gid}" cx="50%" cy="46%" r="62%">
      <stop offset="0%" stop-color="${color}" stop-opacity="0.5"/><stop offset="100%" stop-color="${color}" stop-opacity="0.06"/>
    </radialGradient></defs>
    ${rings}${spokes}
    <polygon points="${poly}" fill="url(#${gid})" stroke="${color}" stroke-width="2" stroke-linejoin="round"/>
    ${dots}${labels}</svg>`;
}
function renderRoleRadars(roles) {
  const pref = $("role-pref-radar"), style = $("role-style-radar");
  if (!pref || !style) return;
  const data = roles || [];
  if (!data.some((d) => d.total > 0)) {
    const e = `<div class="empty">Sin datos suficientes todavía.</div>`;
    pref.innerHTML = e; style.innerHTML = e; return;
  }
  pref.innerHTML = roleRadar(data, "usage_pct", "#3dd9e8", "radGradPref");
  style.innerHTML = roleRadar(data, "winrate", "#f5b82a", "radGradStyle");
}

function heatBg(wr) { if (wr >= 55) return "rgba(65,224,138,0.14)"; if (wr < 45) return "rgba(255,93,108,0.14)"; return "rgba(255,200,61,0.12)"; }
function heatmap(ct) {
  if (!ct.brawlers.length || !ct.modes.length) return `<div class="empty">Sin datos para la tabla cruzada todavía.</div>`;
  // Orden por win rate medio entre los modos que ha jugado (mayor a menor), aunque ese valor no se muestre.
  const avgWr = (b) => {
    const vs = ct.modes.map((m) => ct.cells[`${b}|${m}`]).filter((c) => c && c.winrate != null).map((c) => c.winrate);
    return vs.length ? vs.reduce((a, x) => a + x, 0) / vs.length : -1;
  };
  const brawlers = ct.brawlers.slice().sort((a, b) => avgWr(b) - avgWr(a));
  let h = `<table class="heat"><thead><tr><th></th>`;
  h += ct.modes.map((m) => { const a = modeAsset(m); return `<th>${a && a.icon ? imgTag(a.icon, "heat-mode") : ""}<span>${esc(modeName(m))}</span></th>`; }).join("");
  h += `</tr></thead><tbody>`;
  for (const b of brawlers) {
    const port = brawlerPortrait(b);
    h += `<tr><th class="heat-brawler">${port ? imgTag(port, "heat-port") : ""}<span>${esc(b)}</span></th>`;
    for (const m of ct.modes) {
      const cell = ct.cells[`${b}|${m}`];
      h += (!cell || cell.winrate == null)
        ? `<td class="heat-cell empty">·</td>`
        : `<td class="heat-cell" style="background:${heatBg(cell.winrate)}" title="${cell.total} partidas"><span style="color:${pctColor(cell.winrate)}">${cell.winrate}%</span><small>${cell.total}p</small></td>`;
    }
    h += `</tr>`;
  }
  return h + `</tbody></table>`;
}

/* ---------- Sensei: informes + retos generados + candado de entrenamiento ---------- */
let reportPollTimer = null;
let senseiGate = null;

function senseiImgFallback(img) {
  // Si no existe media/sensei.png, deja un dojo con emoji en su lugar.
  const ph = document.createElement("div");
  ph.className = "dojo-img fallback";
  ph.textContent = "🥋";
  img.replaceWith(ph);
}

/* ----- Cuestionario del Sensei (dropdowns propios, no los filtros compartidos) ----- */
let senseiSel = { brawler: [], mode: [], map: [], role: [] };

async function loadSenseiQuiz() {
  if (!currentPlayer) return;
  let f;
  try { f = await getJSON("/api/filters?player=" + encodeURIComponent(currentPlayer)); } catch (e) { return; }
  const av = { brawler: f.brawlers || [], mode: f.modes || [], map: f.maps || [], role: f.roles || [] };
  ["brawler", "mode", "map", "role"].forEach((k) => {
    senseiSel[k] = (senseiSel[k] || []).filter((v) => av[k].includes(v));
    buildSenseiMs(k, av[k]);
  });
}
function senseiMsLabel(kind) {
  const s = senseiSel[kind] || [];
  if (!s.length) return kind === "brawler" ? "Todos" : "Cualquiera";
  return s.length === 1 ? msOption(kind, s[0]).label : s.length + " seleccionados";
}
function buildSenseiMs(kind, values) {
  const el = $("sq-" + kind);
  if (!el) return;
  const sel = senseiSel[kind] || (senseiSel[kind] = []);
  const opts = (values || []).map((v) => {
    const o = msOption(kind, v);
    return `<label class="ms-opt"><input type="checkbox" value="${esc(v)}" ${sel.includes(v) ? "checked" : ""} onchange="senseiMsToggle('${kind}', this.value, this.checked)">${o.img}<span>${esc(o.label)}</span></label>`;
  }).join("");
  const panelCls = kind === "brawler" ? "sq-brawler-panel" : (kind === "mode" ? "sq-ms-panel narrow" : "sq-ms-panel");
  const optsCls = kind === "brawler" ? "sq-brawler-opts" : (kind === "mode" ? "sq-opts-1" : "sq-opts-2");
  el.innerHTML = `<button class="ms-trigger" type="button" onclick="toggleSenseiMs('${kind}', event)">${esc(senseiMsLabel(kind))}</button>
    <div class="ms-panel ${panelCls}">
      <div class="ms-actions"><button type="button" onclick="senseiMsAll('${kind}', true)">✓ Todos</button><button type="button" onclick="senseiMsAll('${kind}', false)">Ninguno</button></div>
      <div class="ms-options ${optsCls}">${opts || '<div class="ms-empty">Sin datos todavía</div>'}</div>
    </div>`;
}
function toggleSenseiMs(kind, ev) {
  if (ev) ev.stopPropagation();
  const el = $("sq-" + kind), open = el.classList.contains("open");
  document.querySelectorAll(".ms.open").forEach((m) => m.classList.remove("open"));
  if (!open) el.classList.add("open");
}
function senseiMsToggle(kind, v, ch) {
  const s = senseiSel[kind], i = s.indexOf(v);
  if (ch && i < 0) s.push(v); else if (!ch && i >= 0) s.splice(i, 1);
  const t = $("sq-" + kind).querySelector(".ms-trigger"); if (t) t.textContent = senseiMsLabel(kind);
}
function senseiMsAll(kind, all) {
  const checks = $("sq-" + kind).querySelectorAll(".ms-options input");
  senseiSel[kind] = all ? Array.from(checks).map((c) => c.value) : [];
  checks.forEach((c) => { c.checked = all; });
  const t = $("sq-" + kind).querySelector(".ms-trigger"); if (t) t.textContent = senseiMsLabel(kind);
}

/* ----- Modal de confirmación genérico ----- */
function confirmModal(message, onYes, okLabel) {
  $("confirm-msg").textContent = message;
  const ok = $("confirm-ok");
  ok.textContent = okLabel || "Borrar";
  ok.onclick = () => { closeConfirm(); if (onYes) onYes(); };
  openEvModal("confirm-modal");
}
function closeConfirm() { closeEvModal("confirm-modal"); }

async function loadReports() {
  if (!currentPlayer) return;
  let reports, status;
  try {
    [reports, status] = await Promise.all([
      getJSON("/api/reports?player=" + encodeURIComponent(currentPlayer)),
      getJSON("/api/sensei/status").catch(() => null),
    ]);
  } catch (e) { return; }
  senseiGate = status && status.gate ? status.gate : null;
  renderReportList(reports);
  const generating = reports.some((r) => r.status === "generating");
  setCoachButton(generating);
  renderSenseiGate(generating);
  if (generating) startReportPolling(); else stopReportPolling();
}

function renderSenseiGate(generating) {
  const g = $("dojo-gate");
  if (!g) return;
  if (generating || !senseiGate || senseiGate.can_generate) { g.style.display = "none"; g.innerHTML = ""; return; }
  g.style.display = "";
  let html = `<span class="gate-msg">🔒 El maestro no da otra lección hasta que practiques: tienes <b>${senseiGate.active}</b> tareas pendientes (deben bajar a ${senseiGate.threshold}).</span>`;
  if (senseiGate.can_reset) html += ` <button class="ghost danger gate-reset" onclick="resetSenseiTraining()">Resetear misiones</button>`;
  g.innerHTML = html;
}
function renderReportList(reports) {
  const el = $("report-list");
  if (!el) return;
  if (!reports.length) { el.innerHTML = `<div class="empty"><span class="big">✎</span>Aún no has generado ningún informe.<br>Pulsa "Analizar mis datos" para crear el primero.</div>`; return; }
  el.innerHTML = reports.map(reportItem).join("");
}
function reportItem(r) {
  const date = fmtDateTime(r.created_at);
  if (r.status === "generating")
    return `<div class="report-item generating"><span class="report-date">${date}</span><span class="report-name gen">El Sensei medita<span class="dots"></span></span></div>`;
  const isErr = r.status === "error";
  const name = isErr
    ? `<span class="report-name err">Error al generar · ver detalle</span>`
    : `<span class="report-name">${esc(r.name || r.scope_label || "Informe")}</span>`;
  return `<div class="report-item ${isErr ? "error" : "ready"}">
    <div class="report-item-main" onclick="openReport(${r.id})"><span class="report-date">${date}</span>${name}</div>
    <div class="report-item-actions">
      ${isErr ? "" : `<button class="ri-btn dl" title="Descargar informe maquetado" onclick="event.stopPropagation();downloadReport(${r.id})">⭳</button>`}
      <button class="ri-btn del" title="Borrar informe" onclick="event.stopPropagation();askDeleteReport(${r.id})">🗑</button>
    </div>
  </div>`;
}
async function openReport(id) {
  let r;
  try { r = await getJSON("/api/reports/" + id); } catch (e) { return; }
  $("coach-list-view").style.display = "none";
  $("coach-detail-view").style.display = "";
  const body = r.status === "error"
    ? `<div class="coach-error">No se pudo generar este informe: ${esc(r.error || "error desconocido")}</div>`
    : `<div class="advice">${esc(r.content || "").replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")}</div>`;
  $("report-detail").innerHTML = `<div class="report-detail-head"><h3>${esc(r.name || r.scope_label || "Informe")}</h3><div class="report-detail-meta">${esc(r.scope_label || "")} · ${fmtDateTime(r.created_at)}</div></div>${body}`;
}
window.openReport = openReport;
function showCoachListView() { const d = $("coach-detail-view"), l = $("coach-list-view"); if (d) d.style.display = "none"; if (l) l.style.display = ""; }
function backToReportList() { showCoachListView(); loadReports(); }
async function generateReport() {
  if (!currentPlayer) return;
  const body = {
    player: currentPlayer,
    brawler: senseiSel.brawler.join(",") || null,
    mode: senseiSel.mode.join(",") || null,
    map: senseiSel.map.join(",") || null,
    role: senseiSel.role.join(",") || null,
  };
  setCoachButton(true);
  try {
    const r = await fetch("/api/reports", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await r.json();
    if (!r.ok) { wikiToast(data.error || "No se pudo consultar al Sensei.", "err"); await loadReports(); return; }
  } catch (e) { wikiToast("Error de red al consultar al Sensei.", "err"); setCoachButton(false); return; }
  wikiToast("El Sensei medita tu informe y tus retos…", "ok");
  await loadReports();
}
function setCoachButton(generating) {
  const btn = $("coach-btn");
  if (!btn) return;
  const gated = senseiGate && !senseiGate.can_generate && !generating;
  btn.disabled = generating || gated;
  btn.classList.toggle("gated", !!gated);
  btn.innerHTML = generating ? `<span class="spinner"></span><span class="btn-label">Preparando</span>`
    : gated ? "Tienes tareas" : "Preparar lección";
}

async function downloadReport(id) {
  let r;
  try { r = await getJSON("/api/reports/" + id); } catch (e) { return; }
  const title = r.name || r.scope_label || "Informe del Sensei";
  const bodyHTML = esc(r.content || "").replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/\n/g, "<br>");
  const html = `<!doctype html><html lang="es"><head><meta charset="utf-8"><title>${esc(title)} · Brawl Sensei</title>`
    + `<style>body{font-family:system-ui,Segoe UI,sans-serif;max-width:760px;margin:40px auto;padding:0 20px;line-height:1.65;color:#1a1a2e;background:#f6f5ff}`
    + `h1{font-size:25px;color:#5b54ff;margin:0 0 4px} .meta{color:#8a86a8;font-size:13px;margin-bottom:24px}`
    + `strong{color:#c64ff0} .card{background:#fff;border:1px solid #ececff;border-radius:16px;padding:30px 34px;box-shadow:0 6px 26px rgba(80,60,200,.08)}</style>`
    + `</head><body><div class="card"><h1>🥋 ${esc(title)}</h1>`
    + `<div class="meta">${esc(r.scope_label || "")} · ${fmtDateTime(r.created_at)} · Brawl Sensei</div><div>${bodyHTML}</div></div></body></html>`;
  const blob = new Blob([html], { type: "text/html;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "sensei-" + (title.replace(/[^\wáéíóúñ]+/gi, "_").toLowerCase().slice(0, 40) || "informe") + ".html";
  a.click();
  URL.revokeObjectURL(a.href);
}

function askDeleteReport(id) {
  confirmModal("¿Borrar este informe del Sensei? No se puede deshacer (los retos que generó seguirán en la sección Retos).", async () => {
    const r = await fetch("/api/reports/" + id, { method: "DELETE" });
    if (!r.ok) { wikiToast("No se pudo borrar el informe.", "err"); return; }
    loadReports();
  }, "Borrar informe");
}

async function resetSenseiTraining() {
  if (!confirm("¿Resetear el entrenamiento? Se abandonarán tus retos del Sensei activos para poder pedir un informe nuevo.")) return;
  const r = await fetch("/api/sensei/reset", { method: "POST" });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) { wikiToast(d.error || "No se pudo resetear.", "err"); return; }
  wikiToast("Entrenamiento reseteado. El Sensei te espera.", "ok");
  loadReports();
}
function startReportPolling() { if (!reportPollTimer) reportPollTimer = setInterval(loadReports, 3000); }
function stopReportPolling() { if (reportPollTimer) { clearInterval(reportPollTimer); reportPollTimer = null; } }

async function loadAssets() { try { ASSETS = await getJSON("/api/assets"); } catch (e) { /* sin imágenes, se muestran solo nombres */ } }

