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

let brawlersLoading = false;
function renderBrawlersAll() {
  renderBrCounters(); renderBrAccount(); renderBrDistrib(); renderTop13();
  renderBrRoles(); renderBrGrid(); renderBrTemporary(); renderBrUpcoming();
  loadRecommendations(recsKind);
}
async function loadBrawlers() {
  if (brawlersData && brawlersPlayer === currentPlayer) { renderBrawlersAll(); return; }
  // Aún no hay jugador (arranque): muestra spinner y NO salgas en vacío — refreshAll() nos
  // re-llamará en cuanto currentPlayer esté fijado, evitando el spinner eterno.
  showBrawlersGridView();
  if (!currentPlayer) {
    $("br-grid").innerHTML = `<div class="empty" style="grid-column:1/-1">Cargando colección…</div>`;
    return;
  }
  if (brawlersLoading) return;          // ya hay una carga en curso: no dupliques
  brawlersLoading = true;
  $("br-grid").innerHTML = `<div class="empty" style="grid-column:1/-1">Cargando colección…</div>`;
  $("br-counters").innerHTML = "";
  try {
    brawlersData = await getJSON("/api/brawlers?player=" + encodeURIComponent(currentPlayer));
    brawlersPlayer = currentPlayer;
  } catch (e) {
    $("br-grid").innerHTML = `<div class="empty" style="grid-column:1/-1">No se pudo cargar la colección. <button class="ghost" onclick="loadBrawlers()">Reintentar</button></div>`;
    return;
  } finally { brawlersLoading = false; }
  renderBrawlersAll();
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

/* Colapso móvil compartido por TODOS los podios (Brawlers, Modos, rol): en móvil solo se
   ve el podio (top 3) y las posiciones 4–13 se despliegan con "Ver más". En PC se ven todas. */
function collapsibleRest(restHtml, count) {
  if (!restHtml) return "";
  return `<div class="top-rest">
    <button class="top-more-btn" onclick="toggleTopRest(this)">Ver más (${count})</button>
    <div class="top-mini-row">${restHtml}</div>
  </div>`;
}
function toggleTopRest(btn) {
  const wrap = btn.parentElement;
  const open = wrap.classList.toggle("open");
  const n = wrap.querySelectorAll(".top-mini").length;
  btn.textContent = open ? "Ver menos" : `Ver más (${n})`;
}

let brTopRole = null;  // null = "General" (Top 13 de toda la cuenta)
function renderTop13() {
  const el = $("br-top10");
  if (!el || !brawlersData) { if (el) el.innerHTML = ""; return; }
  const byRole = brawlersData.top_by_role || {};
  const order = (typeof ROLE_ORDER !== "undefined" ? ROLE_ORDER : []);
  const roles = Object.keys(byRole).sort((a, b) => {
    const ia = order.indexOf(a), ib = order.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
  });
  if (brTopRole && !byRole[brTopRole]) brTopRole = null;      // rol ya no disponible → General
  const general = brawlersData.top_brawlers || [];
  if (!general.length && !roles.length) { el.innerHTML = ""; return; }
  const top = brTopRole ? (byRole[brTopRole] || []) : general;
  // Filtros de rol: "General" (por defecto) + un chip por rol disponible.
  const chips = `<div class="br-role-chips">
    <button class="br-role-chip ${!brTopRole ? "active" : ""}" data-trole="">General</button>
    ${roles.map((r) => `<button class="br-role-chip ${brTopRole === r ? "active" : ""}" data-trole="${esc(r)}">${esc(r)}</button>`).join("")}
  </div>`;
  const podium = top.slice(0, 3).map((b, i) => ({ ...b, pos: i + 1 }));
  const ord = [podium[1], podium[0], podium[2]].filter(Boolean);  // 2 · 1 · 3
  const podiumHtml = ord.map((b) => {
    const src = b.image_full || b.portrait;   // SIEMPRE cuerpo entero (skin equipada) en el podio
    const img = src ? `<img src="${src}" alt="" onerror="this.style.display='none'">` : "";
    return `<div class="podium-col pos${b.pos}" onclick="showBrawlerDetail(${b.id})" title="Ver ficha">
      <div class="podium-img">${img}</div>
      <div class="podium-base"><span class="podium-pos">${b.pos}</span>
        <span class="podium-name">${esc(b.name)}</span>
        <span class="podium-tro">🏆 ${(b.trophies || 0).toLocaleString("es-ES")}</span></div></div>`;
  }).join("");
  // Extras (solo PC): rendimiento de los 3 del podio + gráfica de eficiencia.
  const winnersMini = podium.map((b) => {
    const img = b.portrait ? `<img src="${b.portrait}" alt="" onerror="this.style.display='none'">` : "";
    const adj = b.your_adj;
    return `<div class="winner-row" onclick="showBrawlerDetail(${b.id})" title="Ver ficha">
      <span class="wm-pos">${b.pos}</span>${img}
      <div class="wm-tx"><span class="wm-name">${esc(b.name)}</span>
        <span class="wm-sub">${adj == null ? "sin partidas" : `<b style="color:${pctColor(adj)}">${adj}</b> rend · ${b.your_battles}p`}</span></div></div>`;
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
  const body = top.length
    ? `<div class="top13-main">
        <div class="podium-extra extra-left"><div class="extra-title">Rendimiento</div>${winnersMini}</div>
        <div class="podium">${podiumHtml}</div>
        <div class="podium-extra extra-right"><div class="extra-title">Eficiencia · win rate</div>${effRows}</div>
      </div>
      ${collapsibleRest(restHtml, top.slice(3, 13).length)}`
    : `<div class="empty" style="padding:22px">No tienes brawlers de este rol en tu colección.</div>`;
  el.innerHTML = `<div class="top10-panel">
    <h2><span class="dot"></span>Top 13 Brawlers${chips}</h2>
    ${body}</div>`;
  el.querySelectorAll(".br-role-chip").forEach((c) => c.addEventListener("click", () => {
    brTopRole = c.dataset.trole || null; renderTop13();
  }));
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
      const go = `goBrawlerByName('${esc(b.name || "").replace(/'/g, "\\'")}')`;
      return `<div class="tl-brawler clickable" title="${esc(tip)}" onclick="${go}">${img}</div>`;
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

/* ---------- Brawlers "Próximamente" (anunciados, aún no en el juego) ---------- */
function renderUpcomingCard(u, i) {
  const img = u.image
    ? `<img src="${u.image}" class="por" alt="" loading="lazy" onerror="this.style.visibility='hidden'">`
    : `<div class="por up-noimg">🔮</div>`;
  return `<div class="br-card locked up-card" onclick="showUpcomingDetail(${i})" title="${esc(u.name)}">
    <span class="up-badge">Próximamente</span>
    <div class="por-frame">${img}</div>
    <div class="nm">${esc(u.name)}</div>
    <div class="role">${esc(u.release || "")}</div>
  </div>`;
}
function renderBrUpcoming() {
  const el = $("br-upcoming");
  if (!el || !brawlersData) return;
  const list = brawlersData.upcoming || [];
  if (!list.length) { el.innerHTML = ""; return; }
  el.innerHTML = `
    <div class="br-temp-head"><h3>🔮 Próximos brawlers</h3>
      <p>Brawlers anunciados que aún no han salido al juego. No cuentan para la colección ni el meta; su ficha se completa según Supercell publica la información oficial.</p></div>
    <div class="br-grid">${list.map(renderUpcomingCard).join("")}</div>`;
}
function showUpcomingDetail(i) {
  const u = ((brawlersData && brawlersData.upcoming) || [])[i];
  if (!u) return;
  $("brawlers-grid-view").style.display = "none"; $("brawler-detail-view").style.display = "";
  const acts = $("br-detail-actions"); if (acts) acts.innerHTML = "";   // los próximos no tienen histórico/ranking
  window.scrollTo({ top: 0, behavior: "smooth" });
  const heroImg = u.image_full || u.image;
  const img = heroImg ? `<img src="${esc(heroImg)}" alt="" onerror="this.src='${esc(u.image || "")}'">` : `<div class="empty" style="font-size:64px;margin:0">🔮</div>`;
  const ab = (u.abilities || []).length
    ? `<div class="br-section"><h3>Habilidades (anticipo)</h3><div class="ability-grid">${u.abilities.map((a) => `<div class="ability"><div class="be-body"><div class="nm">${esc(a.name)}</div><div class="ds">${esc(a.note || "")}</div></div></div>`).join("")}</div></div>`
    : "";
  $("br-detail").innerHTML = `
    <div class="br-d-top">
      <div class="br-d-img">${img}<span class="up-badge lg">Próximamente</span></div>
      <div class="br-d-info">
        <h1>${esc(u.name)}</h1>
        <div class="br-d-role">${esc(u.role)} · ${esc(u.rarity)}</div>
        <div class="br-taglines"><span class="br-tagline prestige">🔮 ${esc(u.release || "Próximamente")}</span></div>
        <p class="br-d-desc">${esc(u.description || "Información por confirmar.")}</p>
        ${u.source ? `<a class="src-link" href="${esc(u.source)}" target="_blank" rel="noopener">Fuente oficial ↗</a>` : ""}
        ${(typeof currentUser !== "undefined" && currentUser && currentUser.is_admin) ? `
          <div class="up-admin">
            <button class="btn mini-btn" onclick="markBrawlerAvailable(${i})">✔ Marcar como disponible</button>
            <p class="hint" style="margin:7px 0 0;max-width:520px">La app lo detecta sola en cuanto algún jugador trackeado lo consigue. Usa esto solo si ya salió y no se ha detectado: pasará a la lista común y contará para colección y analíticas.</p>
          </div>` : ""}
      </div>
    </div>${ab}`;
}
async function markBrawlerAvailable(i) {
  const u = ((brawlersData && brawlersData.upcoming) || [])[i];
  if (!u) return;
  if (!confirm(`¿Marcar a ${u.name} como disponible? Pasará a la lista de brawlers y contará para la colección y las analíticas.`)) return;
  const { ok, d } = await apiSend("/api/admin/brawler-available", "POST", { name: u.name });
  if (!ok) { wikiToast(d.error || d.detail || "No se pudo marcar como disponible.", "err"); return; }
  wikiToast(`${esc(u.name)} marcado como disponible.`, "ok");
  showBrawlersGridView();
  await loadBrawlers();   // recarga la rejilla (ya sin él en «Próximos»)
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
      <div class="br-rec-body">
        <div class="nm">${esc(b.name)}</div>
        <div class="nt">${esc(b.note || "")}</div>
        ${b.reliability != null ? `<div class="rel-bar" title="Fiabilidad ${b.reliability}% · según partidas jugadas"><span style="width:${b.reliability}%"></span></div>` : ""}
      </div>
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
  // Solo vigentes: los próximos cambios, futuros brawlers y novedades viven en "Actualizaciones".
  host.innerHTML = `
    <h2 class="section-title" style="margin-top:34px">Buffs y nerfs vigentes</h2>
    <p class="hint" style="margin:2px 0 14px;max-width:760px">Los cambios de balance ya aplicados (mejoras en verde, recortes en rojo). Los próximos cambios, futuros brawlers y novedades están en la sección <b>Actualizaciones</b>.</p>
    ${renderBuffsCols(d.current || [], "Recopilando los cambios de balance recientes…")}`;
}

/* ---------- Sección "Actualizaciones" (balance fiable + novedades + historial) ---------- */
async function loadActualizaciones() {
  const host = $("act-content");
  if (!host) return;
  host.innerHTML = `<div class="empty">Cargando novedades…</div>`;
  let d = null, b = null;
  try { d = await getJSON("/api/changelog"); } catch (e) { d = null; }
  try { b = await getJSON("/api/buffs"); } catch (e) { b = null; }
  d = d || {}; b = b || {};
  const updates = d.updates || [], latest = (d.latest || []).filter((c) => c.kind !== "neutral");
  const up = d.upcoming || [], announced = b.upcoming || [];
  const modes = d.modes || [], other = d.other || [];
  const latestLbl = updates[0] ? bhDate({ date: updates[0].date }) : "";
  host.innerHTML = `
    <h3 class="buffs-h">Cambios de balance · última actualización${latestLbl ? ` · <span style="color:var(--gold)">${esc(latestLbl)}</span>` : ""}</h3>
    ${renderBuffsCols(latest, "Sin cambios de balance recientes.")}
    <button class="btn-buffs" style="margin-top:16px" onclick="openChangelog()">📜 Ver historial completo de cambios</button>
    ${announced.length ? `<h3 class="buffs-h">Próximos cambios (anunciados)</h3>${renderBuffsCols(announced, "")}` : ""}
    <h3 class="section-title" style="font-size:15px;margin:30px 0 10px">Próximos brawlers</h3>
    ${up.length ? `<div class="act-list">${up.map(actUpcoming).join("")}</div>` : `<div class="buffs-none">Sin brawlers anunciados por ahora.</div>`}
    ${modes.length ? `<h3 class="section-title" style="font-size:15px;margin:30px 0 10px">Nuevos modos y eventos${d.update ? ` <span style="color:var(--muted);font-size:11px;font-weight:400">· ${esc(d.update)}</span>` : ""}</h3>${renderActList(modes)}` : ""}
    ${other.length ? `<h3 class="section-title" style="font-size:15px;margin:26px 0 10px">Otros cambios y ajustes</h3>${renderActList(other)}` : ""}`;
}
function actUpcoming(u) {
  const img = u.image ? `<img src="${esc(u.image)}" alt="" loading="lazy" style="width:34px;height:34px;border-radius:8px;object-fit:cover;float:left;margin-right:10px" onerror="this.style.display='none'">` : "";
  return `<div class="act-item">${img}<div class="act-item-h">${esc(u.name)} <span style="color:var(--gold);font-size:11px">${esc(u.release || "")}</span></div>
    <div class="act-item-n">${esc([u.rarity, u.role].filter((x) => x && x !== "Por confirmar").join(" · "))}${u.description ? " — " + esc(u.description) : ""}</div></div>`;
}
function renderActList(items, empty) {
  if (!items || !items.length) return `<div class="buffs-none">${esc(empty || "Sin datos por ahora.")}</div>`;
  return `<div class="act-list">${items.map((it) => `<div class="act-item">
    <div class="act-item-h">${esc(it.name || "")}</div>
    ${it.note ? `<div class="act-item-n">${esc(it.note)}</div>` : ""}</div>`).join("")}</div>`;
}

/* ---------- Modal: historial COMPLETO de cambios (timeline de la wiki, maquetado) ---------- */
let _clUpdates = [];
function clDateLabel(u) {
  const m = /^(\d{2})\/(\d{2})\/(\d{2})$/.exec(u.date || "");
  return m ? `${+m[1]} ${BH_MONTHS[(+m[2] - 1) % 12] || ""} 20${m[3]}` : (u.date || "—");
}
async function openChangelog() {
  const m = $("changelog-modal"), body = $("changelog-body");
  if (!m || !body) return;
  body.innerHTML = `<div class="modal-title">Historial de cambios</div><div class="empty">Cargando…</div>`;
  m.classList.add("open");
  try { _clUpdates = (await getJSON("/api/changelog")).updates || []; } catch (e) { _clUpdates = []; }
  body.innerHTML = `<div class="modal-title">Historial de cambios</div>
    <div class="modal-sub">Todos los cambios de balance del juego, actualización a actualización. Pulsa una fecha para ver sus buffs y nerfs.</div>
    ${_clUpdates.length
      ? `<div class="cl-list">${_clUpdates.map((u, i) => `<button class="cl-item" onclick="openChangelogEntry(${i})">
          <span class="cl-name">${esc(clDateLabel(u))}</span>
          <span class="cl-counts">${u.buff ? `<span class="cl-c buff">▲ ${u.buff}</span>` : ""}${u.nerf ? `<span class="cl-c nerf">▼ ${u.nerf}</span>` : ""}${u.rework ? `<span class="cl-c rework">↻ ${u.rework}</span>` : ""}${u.neutral && !(u.buff || u.nerf || u.rework) ? `<span class="cl-c neutral">● ${u.neutral}</span>` : ""}</span></button>`).join("")}</div>`
      : `<div class="empty">No hay historial cargado.<br><small style="opacity:.7">Genera el dataset con <code>python scrape_changes.py</code>.</small></div>`}`;
}
function openChangelogEntry(i) {
  const u = _clUpdates[i], body = $("changelog-body");
  if (!u || !body) return;
  const back = `<button class="ghost" style="margin-bottom:12px" onclick="openChangelog()">‹ Volver al historial</button>`;
  const cards = (u.changes || []).filter((c) => c.kind !== "neutral");
  const otros = (u.changes || []).filter((c) => c.kind === "neutral");
  body.innerHTML = `${back}<div class="modal-title">${esc(clDateLabel(u))}</div>
    <div class="modal-sub">Cambios de balance de esta actualización.</div>
    ${renderBuffsCols(cards, "Sin buffs ni nerfs ese día.")}
    ${otros.length ? `<h3 class="buffs-h" style="margin-top:16px">Otros ajustes</h3><div class="buffs-grid">${otros.map(buffEntry).join("")}</div>` : ""}`;
}
function closeChangelog() { const m = $("changelog-modal"); if (m) m.classList.remove("open"); }

/* ---------- Top del meta global (brawltime.ninja) ---------- */
async function loadMetaGlobal() {
  const el = $("meta-global");
  if (!el) return;
  el.innerHTML = `<div class="empty">Cargando meta global…</div>`;
  let d = null;
  try { d = await getJSON("/api/meta-global"); } catch (e) { d = null; }
  const list = (d && d.brawlers) || [];
  if (!list.length) { el.innerHTML = ""; return; }
  el.innerHTML = `
    <h2 class="section-title" style="margin-top:34px">Top del meta global</h2>
    <p class="hint" style="margin:2px 0 14px;max-width:760px">Los brawlers con mejor win rate ajustado del meta global (y su tasa de uso). Fuente: <a href="https://brawltime.ninja/es" target="_blank" rel="noopener" style="color:var(--cyan)">brawltime.ninja</a>.</p>
    <div class="mg-list">${list.map(mgRow).join("")}</div>`;
}
function mgRow(b) {
  const por = (typeof brawlerPortrait === "function") ? brawlerPortrait(b.name) : null;
  const img = por ? `<img src="${por}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">` : `<span class="mg-noimg"></span>`;
  return `<div class="mg-row"><span class="mg-rank">${b.rank}</span>${img}
    <span class="mg-name">${esc(b.name)}</span>
    <span class="mg-wr">${esc(b.win_rate)}%<small> WR</small></span>
    <span class="mg-use">${esc(b.use_rate)}%<small> uso</small></span></div>`;
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
  const cls = e.kind === "nerf" ? "nerf" : e.kind === "rework" ? "rework" : e.kind === "neutral" ? "neutral" : "buff";
  const meta = e.status === "confirmed"
    ? ` <span class="be-status conf">Confirmado${e.date ? ` · <i>${esc(e.date)}</i>` : ""}</span>`
    : e.status === "announced"
      ? ` <span class="be-status ann">Anunciado</span>`
      : (e.date ? ` <small>${esc(e.date)}</small>` : "");
  const go = `goBrawlerByName('${esc(e.brawler || "").replace(/'/g, "\\'")}')`;
  return `<div class="buff-entry ${cls} clickable" title="${esc(e.note || "")}" onclick="${go}">
    <div class="be-face">${img}<span class="be-target" title="${TARGET_LABEL[e.target] || ""}">${ti}</span></div>
    <div class="be-body">
      <div class="be-name">${esc(titleCaseName(e.brawler))}${meta}</div>
      <div class="be-note">${esc(e.note || TARGET_LABEL[e.target] || "")}</div>
    </div>
  </div>`;
}
function goToBuffs() {
  showSection("actualizaciones");
  setTimeout(() => {
    const el = $("act-content");
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

function showBrawlersGridView() {
  if (typeof _clearInsightTimer === "function") _clearInsightTimer();  // corta reintentos de IA al salir del detalle
  _brScene = null;
  $("brawlers-grid-view").style.display = ""; $("brawler-detail-view").style.display = "none";
}

async function showBrawlerDetail(id) {
  if (!_navPop) history.pushState({ nav: "brawler", section: "brawlytics", brawler: id }, "", "#brawler-" + id);
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
      ${d.your.reliability != null && d.your.battles ? `<div class="br-d-stat"><div class="k">Fiabilidad del dato</div><div class="v">${d.your.reliability}<small style="color:var(--muted);font-size:13px">%</small></div><div class="rel-bar" style="margin-top:7px"><span style="width:${d.your.reliability}%"></span></div></div>` : ""}
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
    ${buildsHtml
      ? `<div class="br-2col"><div class="br-section"><h3>★ Star Powers</h3><div class="ability-grid">${sps}</div></div>${buildsHtml}</div>`
      : `<div class="br-section"><h3>★ Star Powers</h3><div class="ability-grid">${sps}</div></div>`}
    ${hcHtml
      ? `<div class="br-2col"><div class="br-section"><h3>◆ Gadgets</h3><div class="ability-grid">${gds}</div></div>${hcHtml}</div>`
      : `<div class="br-section"><h3>◆ Gadgets</h3><div class="ability-grid">${gds}</div></div>`}
    <div id="br-scene" class="br-scene"></div>`;
  // Botones de acción ARRIBA, junto a «Volver a los brawlers».
  const acts = $("br-detail-actions");
  if (acts) acts.innerHTML = `
    <button class="ghost" onclick="openBrawlerHistory(${d.id}, '${esc(d.name).replace(/'/g, "\\'")}')">📜 Histórico de cambios</button>
    <button class="ghost" onclick="goBrawlerRanking('${esc(d.name).replace(/'/g, "\\'")}')">Ver ranking de ${esc(d.name)} ↗</button>`;
  loadBrawlerScene(d.id, d.name);
}
/* Cajas "Tu rendimiento por modo": una al lado de otra, con el icono del modo. */
function modeBoxesHtml(byMode) {
  return (byMode || []).map((m) => {
    const a = typeof modeAsset === "function" ? modeAsset(m.mode) : null;
    const ic = a && a.icon ? `<img src="${a.icon}" alt="" onerror="this.style.display='none'">` : "";
    const g = m.games != null ? m.games : (m.battles || 0);
    const rel = m.reliability != null ? `<div class="bmb-rel">${relChip(m.reliability)}</div>` : "";
    return `<div class="br-mode-box">
      <div class="bmb-top">${ic}<span class="bmb-name">${esc(modeName(m.mode))}</span></div>
      <div class="bmb-pct" style="color:${pctColor(m.winrate)}">${m.winrate == null ? "—" : m.winrate + "%"}</div>
      <div class="bmb-meta">${g} ${g === 1 ? "partida" : "partidas"}</div>${rel}</div>`;
  }).join("");
}
/* ---------- "Mejores Modos / Mejores Mapas" (datos comunitarios + tu rendimiento + reflexión IA) ---------- */
let _brScene = null, _insightTimer = null;
function _clearInsightTimer() { if (_insightTimer) { clearTimeout(_insightTimer); _insightTimer = null; } }
async function loadBrawlerScene(id, name) {
  _clearInsightTimer();          // cancela reintentos del brawler anterior (evita timeouts huérfanos)
  const el = $("br-scene");
  if (!el) return;
  el.innerHTML = `<div class="br-section"><div class="empty" style="padding:18px">Cargando modos y mapas…</div></div>`;
  let s;
  try { s = await getJSON(`/api/brawler/${id}/scene?player=` + encodeURIComponent(currentPlayer || "")); }
  catch (e) { el.innerHTML = ""; return; }
  _brScene = { id: id, name: name, data: s, insight: null };
  el.innerHTML = renderBrawlerScene(s, name, null, false);
  loadBrawlerInsight(id, name);
}
// Reflexiones del Sensei (IA): generación perezosa e INCREMENTAL. Cada reflexión (estilo, encaje de
// cada modo, final, inesperados) es una petición IA aparte; mientras se completan, reintenta UNA vez
// (timer único, cancelable) para no acumular setTimeouts huérfanos al cambiar de brawler/pestaña.
async function loadBrawlerInsight(id, name) {
  let d;
  try { d = await getJSON(`/api/brawler/${id}/insight?player=` + encodeURIComponent(currentPlayer || "")); }
  catch (e) { return; }
  if (!_brScene || _brScene.id !== id) return;   // el usuario ya cambió de brawler
  if (!d || !d.configured) return;               // sin IA configurada: nos quedamos con los datos
  const el = $("br-scene");
  if (!el) return;
  _brScene.insight = d;
  el.innerHTML = renderBrawlerScene(_brScene.data, name, d, d.generating);
  _clearInsightTimer();
  if (d.generating) _insightTimer = setTimeout(() => { if (_brScene && _brScene.id === id) loadBrawlerInsight(id, name); }, 9000);
}
// Fiabilidad del dato, MUY visible: punto de color + porcentaje (rojo <40 · amarillo 40-75 · verde >75).
function relChip(rel) {
  const r = rel == null ? 0 : rel;
  const c = typeof reliabilityColor === "function" ? reliabilityColor(r) : "var(--muted)";
  return `<span class="scn-rel" title="Fiabilidad del dato: ${r}%"><span class="scn-rel-dot" style="background:${c}"></span>Fiab. ${r}%</span>`;
}
function scnModeRow(m, kind, reflection) {
  const a = typeof modeAsset === "function" ? modeAsset(m.mode) : null;
  const ic = a && a.icon ? `<img class="scn-mode-ic" src="${a.icon}" alt="" onerror="this.style.display='none'">` : "";
  const cw = m.community.winrate, yw = m.your.winrate;
  const refl = reflection ? `<div class="scn-reflect">${esc(reflection)}</div>` : "";
  const yours = m.your.games
    ? `<div class="scn-you">Tú <b style="color:${pctColor(yw)}">${yw == null ? "—" : yw + "%"}</b> <small>${m.your.games}p</small> ${relChip(m.your.reliability)}</div>`
    : `<div class="scn-you muted">Aún no lo juegas aquí</div>`;
  return `<div class="scn-mode ${kind || ""}">
    <div class="scn-mode-head">${ic}<span class="scn-mode-name">${esc(modeName(m.mode))}</span></div>
    ${refl}
    <div class="scn-stats">
      <div class="scn-comm">Comunidad <b style="color:${pctColor(cw)}">${cw == null ? "—" : cw + "%"}</b> <small>${m.community.games}p</small> ${relChip(m.community.reliability)}</div>
      ${yours}
    </div>
  </div>`;
}
function renderBrawlerScene(s, name, insight, generating) {
  const best = s.best_modes || [], unexp = s.unexpected_modes || [], maps = s.maps || [], byMode = s.your_by_mode || [];
  if (!best.length && !maps.length && !byMode.length) return "";
  insight = insight || {};
  const modeWhy = insight.modes || {}, unexpWhy = insight.unexpected || {};
  let styleHtml = "";
  if (insight.style) styleHtml = `<div class="scn-style">🥷 ${esc(insight.style)}</div>`;
  else if (generating) styleHtml = `<div class="scn-style generating">🥷 El Sensei está reflexionando sobre ${esc(name)}…</div>`;
  const bestHtml = best.length
    ? `<h4 class="scn-sub">Mejores modos para ${esc(name)}</h4>
       <p class="scn-note">Los modos donde este brawler mejor rinde según la comunidad, por qué encaja su estilo, y tu propio rendimiento con su fiabilidad.</p>
       <div class="scn-mode-grid">${best.map((m) => scnModeRow(m, null, modeWhy[m.mode])).join("")}</div>` : "";
  const finalHtml = insight.final ? `<div class="scn-final">🥷 ${esc(insight.final)}</div>` : "";
  const unexpHtml = unexp.length
    ? `<h4 class="scn-sub">Sorpresas — te funciona donde no debería</h4>
       <p class="scn-note">Modos donde la comunidad rinde flojo con este brawler, pero <b>tú sacas buen resultado</b>: el Sensei analiza si ciertos mapas, tu habilidad o la suerte lo explican.</p>
       <div class="scn-mode-grid">${unexp.map((m) => scnModeRow(m, "surprise", unexpWhy[m.mode])).join("")}</div>` : "";
  const boxesHtml = byMode.length
    ? `<h4 class="scn-sub">Tu rendimiento por modo</h4><div class="br-mode-boxes">${modeBoxesHtml(byMode)}</div>` : "";
  const mapsHtml = maps.length ? renderBrawlerMaps(maps) : "";
  return `<div class="br-section br-scene-sec"><h3>🧭 Mejores modos y mapas</h3>
    ${styleHtml}${bestHtml}${finalHtml}${unexpHtml}${boxesHtml}${mapsHtml}</div>`;
}
function renderBrawlerMaps(maps) {
  const row = (mp) => {
    const icons = (mp.modes || []).map((mo) => {
      const a = typeof modeAsset === "function" ? modeAsset(mo) : null;
      return a && a.icon ? `<img src="${a.icon}" alt="" title="${esc(modeName(mo))}" onerror="this.style.display='none'">` : "";
    }).join("");
    const cw = mp.community.winrate, yw = mp.your.winrate;
    const you = mp.your.games ? `<span class="scn-map-you" style="color:${pctColor(yw)}">${yw}%</span>` : "";
    return `<div class="scn-map">
      <button class="scn-map-name" onclick="showMap('${esc(mp.map).replace(/'/g, "\\'")}')" title="Ver el mapa">${esc(mapNameEs(mp.map))}</button>
      <span class="scn-map-r"><b style="color:${pctColor(cw)}">${cw == null ? "—" : cw + "%"}</b>${you}<span class="scn-map-icons">${icons}</span></span>
    </div>`;
  };
  const col1 = maps.slice(0, 10).map(row).join("");
  const col2 = maps.slice(10, 20).map(row).join("");
  return `<h4 class="scn-sub">Mejores mapas para este brawler <small>(comunidad · tu win rate)</small></h4>
    <div class="scn-maps"><div class="scn-map-col">${col1}</div><div class="scn-map-col">${col2}</div></div>`;
}

/* ---------- Modal: histórico COMPLETO de cambios de un brawler (wiki) ---------- */
const BH_MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
function bhIcon(k) { return k === "buff" ? "▲" : k === "nerf" ? "▼" : k === "rework" ? "↻" : "•"; }
function bhDate(c) {
  const m = /^(\d{2})\/(\d{2})\/(\d{2})$/.exec(c.date || "");
  if (!m) return c.date || "—";
  return `${+m[1]} ${BH_MONTHS[(+m[2] - 1) % 12] || ""} 20${m[3]}`;
}
function bhTargetIcon(note) {
  const n = (note || "").toLowerCase();
  if (/hiperc|hyperch/.test(n)) return "⚡ ";
  if (/estelar|star power/.test(n)) return "★ ";
  if (/gadget/.test(n)) return "◆ ";
  if (/súper|super/.test(n)) return "✦ ";
  return "";
}
async function openBrawlerHistory(id, name) {
  const m = $("br-history-modal"), body = $("br-history-body");
  if (!m || !body) return;
  body.innerHTML = `<div class="modal-title">Histórico de ${esc(name)}</div><div class="empty">Cargando cambios…</div>`;
  m.classList.add("open");
  let d = null;
  try { d = await getJSON(`/api/brawler/${id}/changes`); } catch (e) { d = null; }
  const hist = (d && d.history) || [], sum = (d && d.summary) || {};
  const chips = [];
  if (sum.buff) chips.push(`<span class="bh-chip buff"><span class="chg-flag buff">▲</span>${sum.buff} buffs</span>`);
  if (sum.nerf) chips.push(`<span class="bh-chip nerf"><span class="chg-flag nerf">▼</span>${sum.nerf} nerfs</span>`);
  if (sum.rework) chips.push(`<span class="bh-chip rework"><span class="chg-flag rework">↻</span>${sum.rework} reworks</span>`);
  let tl = "", curDate = null;
  hist.forEach((c) => {
    if (c.date !== curDate) { curDate = c.date; tl += `<div class="bh-date-h">${esc(bhDate(c))}</div>`; }
    tl += bhRow(c);
  });
  body.innerHTML = `<div class="modal-title">Histórico de cambios</div>
    <div class="modal-sub">Todos los buffs, nerfs y reworks de <b>${esc(name)}</b> a lo largo de la historia del juego. Fuente: <a href="https://brawlstars.fandom.com/wiki/${encodeURIComponent(name)}" target="_blank" rel="noopener" style="color:var(--cyan)">wiki de Brawl Stars</a>.</div>
    ${chips.length ? `<div class="bh-chips">${chips.join("")}</div>` : ""}
    ${hist.length
      ? `<div class="bh-timeline">${tl}</div>`
      : `<div class="empty">Aún no hay histórico para este brawler.<br><small style="opacity:.7">Genera el dataset con <code>python scrape_changes.py</code>.</small></div>`}`;
}
function bhRow(c) {
  return `<div class="bh-row ${c.kind}">
    <span class="chg-flag ${c.kind}">${bhIcon(c.kind)}</span>
    <div class="bh-note">${bhTargetIcon(c.note)}${esc(c.note || "")}</div></div>`;
}
function closeBrawlerHistory() { const m = $("br-history-modal"); if (m) m.classList.remove("open"); }

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

