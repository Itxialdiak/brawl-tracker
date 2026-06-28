/* BrawlSensei · 03-brawlers.js
   apartado Brawlers (rejilla, contadores, rating, Top 13, ficha).
   Se carga como <script src> desde index.html, en orden. El JS de cliente
   SIEMPRE es visible en el navegador: aquí no van secretos. */
/* ============================ BRAWLERS (apartado tipo Brawlify) ============================ */
let brawlersData = null, brawlersPlayer = null, brSort = "winrate", brSearch = "", brRole = null, statLevelSel = 11, recsKind = "community";
const RANK_LABELS = { wood: "Madera", bronze: "Bronce", silver: "Plata", gold: "Oro", p1: "Prestigio 1", p2: "Prestigio 2", p3: "Prestigio 3" };
const RARITY_ORDER = { "Common": 0, "Rare": 1, "Super Rare": 2, "Epic": 3, "Mythic": 4, "Legendary": 5, "Ultra Legendary": 6 };

function rankBadge(band) { return band ? `<span class="rank-badge ${band}" title="${RANK_LABELS[band] || ""}"></span>` : ""; }
function chgIcon(k) { return k === "buff" ? "▲" : k === "nerf" ? "▼" : "↻"; }
function chgLabel(k) { return k === "buff" ? "Buff reciente" : k === "nerf" ? "Nerf reciente" : "Rework reciente"; }
function ratingColor(v) { if (v >= 65) return "var(--win)"; if (v >= 45) return "var(--gold)"; if (v >= 25) return "var(--cyan)"; return "var(--muted)"; }

async function loadBrawlers() {
  if (!currentPlayer) return;
  if (brawlersData && brawlersPlayer === currentPlayer) { renderBrCounters(); renderBrAccount(); renderBrDistrib(); renderTop13(); renderBrRoles(); renderBrGrid(); renderBrTemporary(); loadRecommendations(recsKind); return; }
  showBrawlersGridView();
  $("br-grid").innerHTML = `<div class="empty" style="grid-column:1/-1">Cargando colección…</div>`;
  $("br-counters").innerHTML = "";
  try {
    brawlersData = await getJSON("/api/brawlers?player=" + encodeURIComponent(currentPlayer));
    brawlersPlayer = currentPlayer;
  } catch (e) { $("br-grid").innerHTML = `<div class="empty" style="grid-column:1/-1">No se pudo cargar la colección.</div>`; return; }
  renderBrCounters(); renderBrAccount(); renderBrDistrib(); renderTop13(); renderBrRoles(); renderBrGrid(); renderBrTemporary(); loadRecommendations(recsKind);
}

function renderBrCounters() {
  const c = brawlersData.counters;
  const pct = (o, t) => (t ? Math.round(100 * o / t) : 0);
  const box = (cls, icon, val, total, label, color, title) => `
    <div class="br-counter ${cls}"${title ? ` title="${esc(title)}"` : ""}>
      <div class="ic">${icon}</div>
      <div class="body">
        <div class="v">${val}${total != null ? ` <small>/ ${total}</small>` : ""}</div>
        <div class="k">${label}</div>
        ${total != null ? `<div class="barwrap"><div class="bar" style="width:${pct(val, total)}%;background:${color}"></div></div>` : ""}
      </div>
    </div>`;
  $("br-counters").innerHTML =
    box("col", "🥊", c.brawlers.owned, c.brawlers.total, "Brawlers", "var(--cyan)") +
    box("sp", "★", c.star_powers.owned, c.star_powers.total, "Star Powers", "#ffd75e") +
    box("gd", "◆", c.gadgets.owned, c.gadgets.total, "Gadgets", "var(--win)") +
    box("hc", "⚡", c.hypercharges.owned, c.hypercharges.total, "Hipercargas", "var(--magenta)");
}

function ratingHTML(r) {
  const sub = (label, val) => `<div class="br-sub"><div class="lbl">${label} <b>${val}</b></div>
    <div class="barwrap"><div class="bar" style="width:${val}%"></div></div></div>`;
  return `
    <div class="score">
      <div><span class="num" style="color:${ratingColor(r.overall)}">${r.overall}</span><span class="max">/100</span></div>
      <div class="tier">${esc(r.tier)}</div>
    </div>
    <div class="subs">
      ${sub("Colección", r.collection)}${sub("Maestría", r.mastery)}
      ${sub("Eficiencia", r.efficiency)}${sub("Pushing", r.pushing)}
    </div>`;
}

function renderBrAccount() {
  const a = brawlersData.account || {};
  if (a.trophies == null) { $("br-account").innerHTML = ""; return; }
  const fmt = (n) => (n == null ? "—" : Number(n).toLocaleString("es-ES"));
  const ac = (v, k) => `<div class="ac"><div class="v">${v}</div><div class="k">${k}</div></div>`;
  $("br-account").innerHTML =
    ac(fmt(a.trophies), "Trofeos") + ac(fmt(a.highest_trophies), "Máx. histórico") +
    ac(fmt(a.victories_3v3), "Victorias 3v3") + ac(fmt(a.victories_solo), "Victorias solo") +
    ac(fmt(a.victories_duo), "Victorias dúo") +
    (a.exp_level != null ? ac(fmt(a.exp_level), "Nivel de exp.") : "");
}

function rarColorFor(name) {
  return { "Common": "#b9eaff", "Rare": "#36e0a0", "Super Rare": "#5b9bff", "Epic": "#c64ff0",
           "Mythic": "#ff4d73", "Legendary": "#ffd75e", "Ultra Legendary": "#ff8e3c" }[name] || "var(--neutral)";
}
function dbar(lbl, n, max, color) {
  const pct = max ? Math.round(100 * n / max) : 0;
  return `<div class="dbar"><span class="dlbl">${esc(lbl)}</span>
    <div class="dwrap"><div class="dfill" style="width:${pct}%;background:${color}"></div></div><span class="dn">${n}</span></div>`;
}
function distribPanel(title, body) { return `<div class="panel"><h2><span class="dot"></span>${esc(title)}</h2>${body}</div>`; }

function renderBrDistrib() {
  const owned = brawlersData.brawlers.filter((b) => b.owned);
  if (!owned.length) { $("br-distrib").innerHTML = ""; return; }
  const rarCount = {};
  owned.forEach((b) => { const r = b.rarity?.name || "—"; rarCount[r] = (rarCount[r] || 0) + 1; });
  const rarMax = Math.max(...Object.values(rarCount));
  const rarRows = Object.keys(RARITY_ORDER).filter((r) => rarCount[r])
    .map((r) => dbar(r, rarCount[r], rarMax, rarColorFor(r))).join("");
  const pw = {};
  owned.forEach((b) => { const p = b.power || 0; pw[p] = (pw[p] || 0) + 1; });
  const pwMax = Math.max(...Object.values(pw));
  const pwRows = Object.keys(pw).map(Number).sort((a, b) => a - b)
    .map((p) => dbar("Nivel " + p, pw[p], pwMax, "var(--cyan)")).join("");
  const RANK_BANDS = [["wood", "Madera", "#9c6b3f"], ["bronze", "Bronce", "#cd7f32"], ["silver", "Plata", "#b8c2cf"],
    ["gold", "Oro", "#f5b82a"], ["p1", "Prestigio 1", "#3fa9ff"], ["p2", "Prestigio 2", "#ff4d5e"], ["p3", "Prestigio 3", "#ffe93b"]];
  const bandCount = {};
  owned.forEach((b) => { if (b.rank_band) bandCount[b.rank_band] = (bandCount[b.rank_band] || 0) + 1; });
  const bandMax = Math.max(...RANK_BANDS.map(([k]) => bandCount[k] || 0), 1);
  const bandRows = RANK_BANDS.map(([k, lbl, col]) => dbar(lbl, bandCount[k] || 0, bandMax, col)).join("");
  $("br-distrib").innerHTML =
    distribPanel("Por rareza", rarRows) +
    distribPanel("Por nivel de poder", pwRows) +
    distribPanel("Por trofeos", bandRows);
}

function renderTop13() {
  const el = $("br-top10");
  const top = brawlersData && brawlersData.top_brawlers;
  if (!el || !top || !top.length) { if (el) el.innerHTML = ""; return; }
  const podium = top.slice(0, 3).map((b, i) => ({ ...b, pos: i + 1 }));
  const order = [podium[1], podium[0], podium[2]].filter(Boolean);  // 2 · 1 · 3
  const podiumHtml = order.map((b) => {
    const src = b.image_full || b.portrait;
    const img = src ? `<img src="${src}" alt="" onerror="this.style.display='none'">` : "";
    return `<div class="podium-col pos${b.pos}" onclick="showBrawlerDetail(${b.id})" title="Ver ficha">
      <div class="podium-img">${img}</div>
      <div class="podium-base"><span class="podium-pos">${b.pos}</span>
        <span class="podium-name">${esc(b.name)}</span>
        <span class="podium-tro">🏆 ${(b.trophies || 0).toLocaleString("es-ES")}</span></div></div>`;
  }).join("");
  // Extras (solo PC): rendimiento de los 3 ganadores + gráfica de eficiencia.
  const winnersMini = podium.map((b) => {
    const img = b.portrait ? `<img src="${b.portrait}" alt="" onerror="this.style.display='none'">` : "";
    const wr = b.your_winrate;
    return `<div class="winner-row" onclick="showBrawlerDetail(${b.id})" title="Ver ficha">
      <span class="wm-pos">${b.pos}</span>${img}
      <div class="wm-tx"><span class="wm-name">${esc(b.name)}</span>
        <span class="wm-sub">${wr == null ? "sin partidas" : `<b style="color:${pctColor(wr)}">${wr}%</b> WR · ${b.your_battles}p`}</span></div></div>`;
  }).join("");
  const effRows = podium.map((b) => {
    const wr = b.your_winrate, w = wr == null ? 0 : Math.max(3, wr);
    return `<div class="eff-row"><span class="eff-name">${esc(b.name)}</span>
      <div class="eff-bar-wrap"><div class="eff-bar" style="width:${w}%;background:${pctColor(wr)}"></div></div>
      <span class="eff-val" style="color:${pctColor(wr)}">${wr == null ? "—" : wr + "%"}</span></div>`;
  }).join("");
  const restHtml = top.slice(3, 13).map((b, i) => {
    const img = b.portrait ? `<img src="${b.portrait}" alt="" onerror="this.style.display='none'">` : "";
    return `<div class="top-mini" onclick="showBrawlerDetail(${b.id})" title="Ver ficha">
      <span class="top-mini-pos">${i + 4}</span>${img}
      <div class="top-mini-tx"><span class="top-mini-name">${esc(b.name)}</span>
        <span class="top-mini-tro">🏆 ${(b.trophies || 0).toLocaleString("es-ES")}</span></div></div>`;
  }).join("");
  el.innerHTML = `<div class="top10-panel">
    <h2><span class="dot"></span>Top 13 Brawlers</h2>
    <div class="top13-main">
      <div class="podium-extra extra-left"><div class="extra-title">Rendimiento</div>${winnersMini}</div>
      <div class="podium">${podiumHtml}</div>
      <div class="podium-extra extra-right"><div class="extra-title">Eficiencia · win rate</div>${effRows}</div>
    </div>
    ${restHtml ? `<div class="top-mini-row">${restHtml}</div>` : ""}</div>`;
}

/* ==== Tier Lists (sección) ==== */
const TL_TIERS = ["S", "A", "B", "C", "D", "F"];
async function loadTierlist(kind) {
  document.querySelectorAll(".tl-tab").forEach((b) => b.classList.toggle("active", b.dataset.tl === kind));
  const board = $("tl-board");
  if (board) board.innerHTML = renderTierBoard({});  // pinta ya la plantilla vacía (6 filas)
  let d = null;
  try { d = await getJSON("/api/tierlist?kind=" + kind); } catch (e) { d = null; }
  const tiers = (d && d.tiers) || {};
  if ($("tl-sub")) {
    $("tl-sub").textContent = (d && (d.criteria || d.note))
      || (d ? "" : "No se pudo cargar la tier list. Si acabas de actualizar, reinicia el servidor.");
  }
  if (board) board.innerHTML = renderTierBoard(tiers);  // rellena lo que haya (aunque falten brawlers)
}
function renderTierBoard(tiers) {
  return TL_TIERS.map((t) => {
    const cells = (tiers[t] || []).map((b) => {
      const p = brawlerPortrait(b.name);
      const img = p ? `<img src="${p}" alt="" onerror="this.style.display='none'">` : `<span class="tl-noimg">${esc((b.name || "?")[0])}</span>`;
      const tip = b.winrate != null ? `${b.name} · ${b.winrate}% WR · uso ${b.pick_rate}%` : b.name;
      return `<div class="tl-brawler" title="${esc(tip)}">${img}</div>`;
    }).join("");
    return `<div class="tl-row tier-${t}"><div class="tl-label">${t}</div><div class="tl-cells">${cells || '<span class="tl-empty-row">—</span>'}</div></div>`;
  }).join("");
}

function brComparator(a, b) {
  const byTro = (b.trophies || 0) - (a.trophies || 0);
  switch (brSort) {
    case "winrate": return ((b.your_winrate ?? -1) - (a.your_winrate ?? -1)) || byTro;
    case "power": return ((b.power || 0) - (a.power || 0)) || byTro;
    case "name": return (a.name || "").localeCompare(b.name || "");
    case "rarity": return ((RARITY_ORDER[a.rarity?.name] ?? 9) - (RARITY_ORDER[b.rarity?.name] ?? 9)) || byTro;
    case "role": return (a.role || "zzz").localeCompare(b.role || "zzz") || byTro;
    default: return byTro;
  }
}

function renderBrGrid() {
  let list = brawlersData.brawlers.slice();
  const q = brSearch.trim().toUpperCase();
  if (q) list = list.filter((b) => (b.name || "").toUpperCase().includes(q));
  if (brRole) list = list.filter((b) => b.role === brRole || b.role_secondary === brRole);
  list.sort(brComparator);
  $("br-grid").innerHTML = list.length
    ? list.map(renderBrawlerCard).join("")
    : `<div class="empty" style="grid-column:1/-1">Sin resultados.</div>`;
}
function renderBrTemporary() {
  const el = $("br-temporary");
  if (!el || !brawlersData) return;
  const list = brawlersData.temporary || [];
  if (!list.length) { el.innerHTML = ""; return; }
  el.innerHTML = `
    <div class="br-temp-head"><h3>⏳ Brawlers temporales</h3>
      <p>Colaboraciones limitadas que ya no se pueden conseguir ni usar. No cuentan para la colección ni el meta — solo un recuerdo de lo que pasó.</p></div>
    <div class="br-grid">${list.map(renderBrawlerCard).join("")}</div>`;
}
async function loadRecommendations(kind) {
  recsKind = kind || recsKind;
  document.querySelectorAll(".rec-tab").forEach((b) => b.classList.toggle("active", b.dataset.rec === recsKind));
  const host = $("br-recs");
  if (!host || !currentPlayer) return;
  host.innerHTML = `<div class="empty">Cargando recomendaciones…</div>`;
  let d = null;
  try { d = await getJSON("/api/recommendations?kind=" + recsKind + "&player=" + encodeURIComponent(currentPlayer)); }
  catch (e) { host.innerHTML = `<div class="empty">No se pudieron cargar las recomendaciones.</div>`; return; }
  if ($("br-recs-source")) $("br-recs-source").textContent = d.source || "";
  const groups = d.groups || [];
  const grid = groups.filter((g) => g.key !== "to_max");
  const toMax = groups.find((g) => g.key === "to_max");
  host.innerHTML = `<div class="recs-grid">${grid.map(renderRecGroup).join("")}</div>${toMax ? renderRecGroup(toMax) : ""}`;
}
function renderRecGroup(g) {
  const numbered = !!g.numbered;
  const cards = (g.brawlers || []).map((b, i) => {
    const por = b.portrait ? `<img src="${b.portrait}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">` : "";
    const tier = b.tier ? `<span class="rec-tier tier-${b.tier}">${b.tier}</span>` : "";
    const chgFlag = b.change ? `<span class="chg-flag sm ${b.change.kind}" title="${esc(b.change.note || "")}">${chgIcon(b.change.kind)}</span>` : "";
    const num = numbered ? `<span class="rec-num">${i + 1}</span>` : "";
    return `<div class="br-rec-card" onclick="showBrawlerDetail(${b.id})" title="${esc(b.name)}">
      <div class="por">${num}${por}${tier}${chgFlag}</div>
      <div class="nm">${esc(b.name)}</div>
      <div class="nt">${esc(b.note || "")}</div>
    </div>`;
  }).join("");
  const body = cards || `<div class="rec-empty">Aún no hay datos suficientes aquí. Juega más partidas y se recalculará.</div>`;
  return `<div class="br-rec-group${numbered ? " full numbered" : ""}">
    <div class="br-rec-gh"><h4>${esc(g.title)}</h4><p>${esc(g.subtitle)}</p></div>
    <div class="br-rec-cards">${body}</div>
  </div>`;
}

/* ---------- Lista de buffs y nerfs (bajo la Tier List) ---------- */
const TARGET_ICON = { attack: "💥", super: "✦", gadget: "◆", starpower: "★", hypercharge: "⚡", stats: "🛡" };
const TARGET_LABEL = { attack: "Ataque", super: "Súper", gadget: "Gadget", starpower: "Estelar", hypercharge: "Hipercarga", stats: "Características" };
function titleCaseName(s) { return String(s || "").replace(/\w\S*/g, (t) => t.charAt(0).toUpperCase() + t.substr(1).toLowerCase()); }
async function loadBuffsList() {
  const host = $("buffs-section");
  if (!host) return;
  host.innerHTML = `<div class="empty">Cargando cambios de balance…</div>`;
  let d = null;
  try { d = await getJSON("/api/buffs"); } catch (e) { d = null; }
  if (!d) { host.innerHTML = ""; return; }
  const up = d.upcoming || [];
  host.innerHTML = `
    <h2 class="section-title" style="margin-top:34px">Buffs y nerfs</h2>
    <p class="hint" style="margin:2px 0 14px;max-width:760px">Los cambios de balance de Brawl Stars de un vistazo: a la izquierda las mejoras (verde), a la derecha los recortes (rojo).</p>
    <h4 class="buffs-h">Vigentes ahora</h4>
    ${renderBuffsCols(d.current || [], "Recopilando los cambios de balance recientes…")}
    <h4 class="buffs-h">Próximos cambios confirmados</h4>
    ${up.length ? renderBuffsCols(up, "") : `<div class="buffs-none">No hay cambios de balance programados.</div>`}`;
}
function renderBuffsCols(list, emptyMsg) {
  if (!list.length) return `<div class="buffs-none">${esc(emptyMsg || "Sin datos por ahora.")}</div>`;
  const buffs = list.filter((e) => e.kind !== "nerf");
  const nerfs = list.filter((e) => e.kind === "nerf");
  return `<div class="buffs-cols">
    <div class="buffs-block">
      <div class="buffs-block-h buff">▲ Buffs</div>
      <div class="buffs-grid">${buffs.map(buffEntry).join("") || '<div class="buffs-none sm">Sin buffs.</div>'}</div>
    </div>
    <div class="buffs-block">
      <div class="buffs-block-h nerf">▼ Nerfs</div>
      <div class="buffs-grid">${nerfs.map(buffEntry).join("") || '<div class="buffs-none sm">Sin nerfs.</div>'}</div>
    </div>
  </div>`;
}
function buffEntry(e) {
  const por = (typeof brawlerPortrait === "function") ? brawlerPortrait(e.brawler) : null;
  const img = por ? `<img src="${por}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">` : "";
  const ti = TARGET_ICON[e.target] || "•";
  const cls = e.kind === "nerf" ? "nerf" : e.kind === "rework" ? "rework" : "buff";
  return `<div class="buff-entry ${cls}" title="${esc(e.note || "")}">
    <div class="be-face">${img}<span class="be-target" title="${TARGET_LABEL[e.target] || ""}">${ti}</span></div>
    <div class="be-body">
      <div class="be-name">${esc(titleCaseName(e.brawler))}${e.date ? ` <small>${esc(e.date)}</small>` : ""}</div>
      <div class="be-note">${esc(e.note || TARGET_LABEL[e.target] || "")}</div>
    </div>
  </div>`;
}
function goToBuffs() {
  showSection("tierlists");
  setTimeout(() => {
    const el = $("buffs-section");
    if (el) { el.scrollIntoView({ behavior: "smooth", block: "start" }); el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 1600); }
  }, 220);
}
function renderBrRoles() {
  const el = $("br-roles");
  if (!el || !brawlersData) return;
  const roles = new Set();
  brawlersData.brawlers.forEach((b) => { if (b.role) roles.add(b.role); if (b.role_secondary) roles.add(b.role_secondary); });
  el.innerHTML = [...roles].sort((a, b) => a.localeCompare(b))
    .map((r) => `<button class="br-role-chip ${brRole === r ? "active" : ""}" data-role="${esc(r)}">${esc(r)}</button>`).join("");
  el.querySelectorAll(".br-role-chip").forEach((c) => c.addEventListener("click", () => {
    brRole = brRole === c.dataset.role ? null : c.dataset.role;
    renderBrRoles(); renderBrGrid();
  }));
}

function renderBrawlerCard(b) {
  const rar = "rar-" + ((b.rarity && b.rarity.name ? b.rarity.name : "Common").toLowerCase().replace(/\s+/g, "-"));
  const inner = b.portrait
    ? `<img class="por" src="${b.portrait}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">`
    : `<div class="por"></div>`;
  const por = `<div class="por-frame ${rar}">${inner}</div>`;
  const pw = b.owned ? `<div class="pw">${b.power}</div>` : "";
  const badge = b.owned ? rankBadge(b.rank_band) : "";
  const prestige = b.owned && b.prestige ? `<span class="prestige-pip" title="Prestigio ${b.prestige}">${b.prestige}</span>` : "";
  const wr = b.your_winrate == null ? "" : `<span class="wr" style="color:${pctColor(b.your_winrate)}">${b.your_winrate}%</span>`;
  const tro = b.owned && b.trophies != null ? `<span>🏆 ${b.trophies}</span>` : "";
  let loadout = "";
  if (b.owned) {
    const ic = (a) => a.icon ? `<img class="lo ${a.owned ? "" : "off"}" src="${a.icon}" alt="" loading="lazy" onerror="this.style.display='none'">` : "";
    let items = (b.star_powers || []).map(ic).join("") + (b.gadgets || []).map(ic).join("");
    if (b.has_hypercharge) {
      const cls = b.owns_hypercharge ? "" : "off";
      items += b.hypercharge_icon
        ? `<img class="lo ${cls}" src="${b.hypercharge_icon}" alt="HC" loading="lazy" onerror="this.style.display='none'" title="Hipercarga">`
        : `<span class="lo hcic ${cls}" title="Hipercarga">⚡</span>`;
    }
    loadout = items ? `<div class="br-loadout">${items}</div>` : "";
  }
  const chg = b.change;
  const buffFlag = chg ? `<span class="chg-flag ${chg.kind}" title="${esc(chg.note || chgLabel(chg.kind))}">${chgIcon(chg.kind)}</span>` : "";
  return `<div class="br-card ${b.owned ? "" : "locked"}" onclick="showBrawlerDetail(${b.id})">
    ${pw}<div class="topright">${buffFlag}${badge}${prestige}</div>
    ${por}
    <div class="nm">${esc(b.name)}</div>
    <div class="role">${b.role ? esc(b.role.toUpperCase()) : "—"}</div>
    ${b.role_secondary ? `<div class="role2">${esc(b.role_secondary)}</div>` : ""}
    <div class="meta">${tro}${wr}</div>
    ${loadout}
  </div>`;
}

function showBrawlersGridView() { $("brawlers-grid-view").style.display = ""; $("brawler-detail-view").style.display = "none"; }

async function showBrawlerDetail(id) {
  $("brawlers-grid-view").style.display = "none"; $("brawler-detail-view").style.display = "";
  $("br-detail").innerHTML = `<div class="empty">Cargando ficha…</div>`;
  window.scrollTo({ top: 0, behavior: "smooth" });
  let d;
  try { d = await getJSON(`/api/brawler/${id}?player=` + encodeURIComponent(currentPlayer)); }
  catch (e) { $("br-detail").innerHTML = `<div class="empty">No se pudo cargar la ficha.</div>`; return; }
  renderBrawlerDetail(d);
}

function abilName(list, id) { const a = (list || []).find((x) => x.id === id); return a ? a.name : null; }
function buildRefs(bld, d) {
  const parts = [];
  const sp = abilName(d.star_powers, bld.star_power_id); if (sp) parts.push("★ " + esc(sp));
  const gd = abilName(d.gadgets, bld.gadget_id); if (gd) parts.push("◆ " + esc(gd));
  if (bld.gear) parts.push("⚙ " + esc(bld.gear));
  return parts.join(" · ") || "Configuración recomendada";
}

function lvlButtons() {
  return Array.from({ length: 11 }, (_, i) =>
    `<button class="lvl-btn ${i + 1 === statLevelSel ? "active" : ""}" onclick="setStatLevel(${i + 1})">${i + 1}</button>`).join("");
}
function lvlStats(stats) {
  const val = (v) => (Array.isArray(v) ? (v[statLevelSel - 1] ?? v[v.length - 1]) : v);
  const labels = { health: "Vida", damage: "Daño", speed: "Velocidad", range: "Alcance", reload: "Recarga", super: "Súper" };
  return Object.entries(stats).map(([k, v]) =>
    `<div class="br-d-stat"><div class="k">${labels[k] || esc(k)}</div><div class="v">${esc(String(val(v)))}</div></div>`).join("");
}
function setStatLevel(n) {
  statLevelSel = n;
  const lt = $("lvl-table"), ls = $("lvl-stats");
  if (lt) lt.innerHTML = lvlButtons();
  if (ls) ls.innerHTML = lvlStats(window.__lvlStats || {});
}

function renderBrawlerDetail(d) {
  const rar = d.rarity || {}, rarColor = rar.color || "var(--muted)";
  const img = d.image_full ? `<img src="${d.image_full}" alt="${esc(d.name)}" onerror="this.style.display='none'">` : "";
  const badges = [
    d.skin && d.skin.name ? `<span class="br-tagline skin">🎭 ${esc(d.skin.name)}</span>` : "",
    d.prestige_level ? `<span class="br-tagline prestige">Prestigio ${d.prestige_level}</span>` : "",
  ].join("");

  let yourStats;
  if (d.owned) {
    yourStats = `<div class="br-d-stats">
      <div class="br-d-stat"><div class="k">Nivel de poder</div><div class="v">${d.power}</div></div>
      <div class="br-d-stat"><div class="k">Rango</div><div class="v"><span class="rank-chip">${rankBadge(d.rank_band)}<span class="lbl">${RANK_LABELS[d.rank_band] || ""}</span></span></div></div>
      <div class="br-d-stat"><div class="k">Trofeos</div><div class="v">${d.trophies ?? "—"}<small style="color:var(--muted);font-size:12px"> · máx ${d.highest_trophies ?? "—"}</small></div></div>
      ${d.your.battles ? `<div class="br-d-stat"><div class="k">Tu win rate</div><div class="v" style="color:${pctColor(d.your.winrate)}">${d.your.winrate == null ? "—" : d.your.winrate + "%"}<small style="color:var(--muted);font-size:12px"> · ${d.your.battles}p</small></div></div>` : ""}
      ${d.your.adj_score != null ? `<div class="br-d-stat"><div class="k">Rendimiento ajustado <small style="font-size:10px;opacity:.55">a la dificultad</small></div><div class="v" style="color:${pctColor(d.your.adj_score)}">${d.your.adj_score}<small style="color:var(--muted);font-size:12px"> · ~${d.your.level ?? "—"} cp</small></div></div>` : ""}
    </div>`;
  } else {
    yourStats = `<div class="hint">Aún no tienes este brawler.</div>`;
  }

  const ability = (a) => `<div class="ability ${a.owned ? "owned" : ""}">
    ${a.icon ? `<img src="${a.icon}" alt="" onerror="this.style.display='none'">` : ""}
    <div><div class="nm">${esc(a.name)} ${a.owned ? '<span class="tag">TIENES</span>' : ""}</div>
    <div class="ds">${esc(a.description || "")}</div></div></div>`;
  const sps = (d.star_powers || []).map(ability).join("") || '<div class="empty">—</div>';
  const gds = (d.gadgets || []).map(ability).join("") || '<div class="empty">—</div>';

  let attackHtml = "";
  if (d.attack && (d.attack.name || d.attack.description)) {
    attackHtml = `<div class="br-section"><h3>🎯 Ataque${d.attack.name ? " · " + esc(d.attack.name) : ""}</h3>
      <div class="feature-card"><div class="ds">${esc(d.attack.description || "")}</div></div></div>`;
  }
  let passiveHtml = "";
  if (d.passive) {
    passiveHtml = `<div class="br-section"><h3>🧬 Pasiva / Atributo</h3>
      <div class="feature-card"><div class="ds">${esc(d.passive)}</div></div></div>`;
  }
  let superHtml = "";
  if (d.super && (d.super.name || d.super.description)) {
    superHtml = `<div class="br-section"><h3>✦ Súper${d.super.name ? " · " + esc(d.super.name) : ""}</h3>
      <div class="feature-card super"><div class="ds">${esc(d.super.description || "")}</div></div></div>`;
  }

  let hcHtml = "";
  if (d.hypercharge) {
    const hc = d.hypercharge;
    const ic = hc.icon ? `<img src="${hc.icon}" style="width:46px;height:46px;object-fit:contain" onerror="this.parentElement.textContent='⚡'">` : "⚡";
    hcHtml = `<div class="br-section"><h3>⚡ Hipercarga${hc.name ? " · " + esc(hc.name) : ""}${d.owns_hypercharge ? ' <span class="tag">TIENES</span>' : ""}</h3>
      <div class="hc-card"><div class="hcic">${ic}</div>
      <div><div class="ds">${esc(hc.description || "")}</div>
      ${hc.multiplier ? `<div class="hc-mult">Carga de súper extra: <b>${esc(hc.multiplier)}</b></div>` : ""}</div></div></div>`;
  }

  let statsHtml = "";
  if (d.stats_by_level && Object.keys(d.stats_by_level).length) {
    window.__lvlStats = d.stats_by_level;
    statLevelSel = Math.min(11, Math.max(1, d.owned ? (d.power || 11) : 11));
    statsHtml = `<div class="br-section"><h3>📊 Estadísticas por nivel</h3>
      <div class="lvl-table" id="lvl-table">${lvlButtons()}</div>
      <div class="br-d-stats" id="lvl-stats">${lvlStats(d.stats_by_level)}</div></div>`;
  }

  let buildsHtml = "";
  if (d.builds && d.builds.length) {
    buildsHtml = `<div class="br-section"><h3>🛠️ Builds recomendadas</h3><div class="ability-grid">` +
      d.builds.map((bld) => `<div class="build-card"><div class="bn">${esc(bld.name || "Build")}${bld.win_rate ? ` <span class="bwr">${bld.win_rate}% WR</span>` : ""}</div>
        <div class="ds" style="color:var(--muted);font-size:12px;margin-top:5px">${buildRefs(bld, d)}</div>
        ${bld.source ? `<a href="${esc(bld.source)}" target="_blank" rel="noopener" class="src-link">Fuente: Brawl Time Ninja ↗</a>` : ""}</div>`).join("") +
      `</div></div>`;
  }

  let modeHtml = "";
  if (d.your && d.your.by_mode && d.your.by_mode.length) {
    modeHtml = `<div class="br-section"><h3>Tu rendimiento por modo</h3><div class="rows">` +
      d.your.by_mode.map((m) => `<div class="row"><div class="name">${esc(modeName(m.mode))}</div>
        <div class="pct" style="color:${pctColor(m.winrate)}">${m.winrate == null ? "—" : m.winrate + "%"}</div>
        <div class="meta" style="grid-column:1/-1">${m.battles} partidas</div></div>`).join("") +
      `</div></div>`;
  }

  const chg = d.change;
  const chgHtml = chg ? `<div class="br-change ${chg.kind}"><span class="chg-flag ${chg.kind}">${chgIcon(chg.kind)}</span> <b>${chgLabel(chg.kind)}</b>${chg.note ? " · " + esc(chg.note) : ""}${chg.date ? ` <small>(${esc(chg.date)})</small>` : ""}</div>` : "";
  $("br-detail").innerHTML = `
    <div class="br-d-top">
      <div class="br-d-img"><div class="br-d-rarity" style="background:${rarColor};color:#0a0a1f">${esc(rar.name || "")}</div>${img}${d.skin && d.skin.name ? `<div class="br-d-skin">🎨 ${esc(d.skin.name)}</div>` : ""}</div>
      <div class="br-d-info">
        <h1>${esc(d.name)}</h1>
        <div class="br-d-role">${d.role ? esc(d.role.toUpperCase()) : "—"}${d.role_secondary ? ` <span class="role2">· ${esc(d.role_secondary)}</span>` : ""}</div>
        ${badges ? `<div class="br-taglines">${badges}</div>` : ""}
        <div class="br-d-desc">${esc(d.description || "")}</div>
        ${yourStats}
      </div>
    </div>
    ${chgHtml}
    ${attackHtml}${passiveHtml}${superHtml}
    ${statsHtml}
    <div class="br-section"><h3>★ Star Powers</h3><div class="ability-grid">${sps}</div></div>
    <div class="br-section"><h3>◆ Gadgets</h3><div class="ability-grid">${gds}</div></div>
    ${hcHtml}${buildsHtml}${modeHtml}
    <div style="margin-top:22px"><button class="ghost" onclick="goBrawlerRanking('${esc(d.name).replace(/'/g, "\\'")}')">Ver ranking de ${esc(d.name)} ↗</button></div>`;
}

function goBrawlerRanking(name) {
  switchTab("rankings");
  setTimeout(() => {
    const sel = $("rank-brawler-sel");
    if (sel) {
      const opt = [...sel.options].find((o) => o.textContent.trim().toUpperCase() === String(name).toUpperCase());
      if (opt) { sel.value = opt.value; sel.dispatchEvent(new Event("change")); }
    }
    const panel = document.querySelector('.collapsible[data-cat="brawler"]');
    if (panel) { panel.classList.remove("collapsed"); panel.scrollIntoView({ behavior: "smooth", block: "start" }); }
  }, 250);
}

(function initBrawlersUI() {
  const s = $("br-search");
  if (s) s.addEventListener("input", () => { brSearch = s.value; if (brawlersData) renderBrGrid(); });
  document.querySelectorAll("#br-sorts .br-sort").forEach((b) => b.addEventListener("click", () => {
    brSort = b.dataset.sort;
    document.querySelectorAll("#br-sorts .br-sort").forEach((x) => x.classList.toggle("active", x === b));
    if (brawlersData) renderBrGrid();
  }));
  const back = $("br-back");
  if (back) back.addEventListener("click", showBrawlersGridView);
})();

