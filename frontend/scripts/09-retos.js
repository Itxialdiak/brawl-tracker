/* BrawlSensei · 09-retos.js
   Sección Retos (social): retos del Sensei (misiones que generan los informes) y
   retos de la comunidad, con seguimiento AUTOMÁTICO desde los registros de batalla
   (nunca datos manuales). El backend (app/routers/retos.py) calcula el progreso y la
   dificultad asignada; aquí solo se pinta y se interactúa.

   Reutiliza helpers globales: $ , esc , getJSON , wikiToast , openEvModal/closeEvModal. */

/* ==== Sección Retos ==== */

const RETO_DIFF = { 1: "Muy fácil", 2: "Fácil", 3: "Media", 4: "Difícil", 5: "Muy difícil" };
let RETO_META = null;        // catálogo de métricas (perezoso)
let currentReto = null;      // reto abierto en el modal de detalle

async function jsend(url, method, body) {
  const r = await fetch(url, {
    method, headers: { "Content-Type": "application/json" },
    body: body == null ? undefined : JSON.stringify(body),
  });
  let data = {};
  try { data = await r.json(); } catch (_) { /* sin cuerpo */ }
  return { ok: r.ok, status: r.status, data };
}

function stars(n) {
  n = Math.max(1, Math.min(5, parseInt(n, 10) || 3));
  return `<span class="reto-stars" title="${RETO_DIFF[n]}">${"★".repeat(n)}<span class="off">${"★".repeat(5 - n)}</span></span>`;
}

function retoProgressBar(pct, done) {
  pct = Math.max(0, Math.min(100, pct || 0));
  return `<div class="reto-bar ${done ? "done" : ""}"><span style="width:${pct}%"></span></div>`;
}

function condSummary(conds) {
  if (!conds || !conds.length) return "";
  return conds.map((c) => `<span class="reto-cond-chip">${esc(retoCondText(c))}</span>`).join("");
}

// Texto legible de una condición en cliente (espejo de retos.describe_condition).
function retoCondText(c) {
  const sc = c.scope || {};
  const bits = [];
  if (sc.brawler) bits.push(t("con") + " " + (Array.isArray(sc.brawler) ? sc.brawler.join(", ") : sc.brawler));
  if (sc.mode) bits.push(t("en") + " " + (Array.isArray(sc.mode) ? sc.mode.map((m) => t(modeName(m))).join(", ") : t(modeName(sc.mode))));
  if (sc.map) bits.push(t("en") + " " + (Array.isArray(sc.map) ? sc.map.map(mapNameEs).join(", ") : mapNameEs(sc.map)));
  const s = bits.length ? " " + bits.join(" ") : "";
  const tgt = parseInt(c.target, 10) || 0;   // OJO: no llamar 't' (colisiona con la función i18n t())
  switch (c.metric) {
    case "wins": return `Gana ${tgt} partidas${s}`;
    case "games": return `Juega ${tgt} partidas${s}`;
    case "winrate": return `Mantén ${tgt}% de victorias${s}${c.min_games ? ` (mín. ${c.min_games})` : ""}`;
    case "win_streak": return `Encadena ${tgt} victorias${s}`;
    case "distinct_brawlers": return `Gana con ${tgt} brawlers distintos${s}`;
    case "distinct_played": return `Juega con ${tgt} brawlers distintos${s}`;
    case "trophies": return `Suma ${tgt} copas${s}`;
    case "star_player": return `Sé jugador estelar ${tgt} veces${s}`;
    default: return c.metric;
  }
}
// Traduce nombres de modo crudos (heist, hotZone…) que aparezcan en texto libre (p. ej. el
// nombre de un reto generado por el Sensei) a su nombre en español.
function translateModes(text) {
  if (!text) return text;
  let out = String(text);
  for (const k in MODE_ES) out = out.replace(new RegExp("\\b" + k + "\\b", "gi"), MODE_ES[k]);
  return out;
}

/* ---------- carga de la pestaña ---------- */

async function loadRetos() {
  await Promise.all([loadRetoCounters(), loadRetoMine(), loadRetoBoard()]);
}

async function loadRetoCounters() {
  try {
    const c = await getJSON("/api/retos/counters");
    $("retos-counters").innerHTML = `
      <button class="reto-counter sensei" onclick="openRetoCompleted('sensei')">
        <span class="rc-n">${c.sensei || 0}</span><span class="rc-l">🥋 retos del Sensei cumplidos</span></button>
      <button class="reto-counter comm" onclick="openRetoCompleted('user')">
        <span class="rc-n">${c.community || 0}</span><span class="rc-l">🏅 retos de la comunidad cumplidos</span></button>`;
  } catch (_) { /* 401 gestionado */ }
}

async function loadRetoMine() {
  try {
    const g = await getJSON("/api/retos/mine");
    const groups = [
      ["sensei", "🥋 Asignados por el Sensei", "No tienes tareas del Sensei."],
      ["created", "✍️ Creados por ti", "No has creado ningún reto todavía."],
      ["joined", "🤝 Apuntado / siguiendo", "No estás en ningún reto de la comunidad. Mira el tablón de abajo."],
    ];
    // Se OCULTAN las tarjetas de los retos ya cumplidos (siguen accesibles en el contador
    // de arriba); las categorías se muestran SIEMPRE, con su mensaje si no hay activos.
    $("retos-mine").innerHTML = groups.map(([key, title, empty]) => {
      const items = (g[key] || []).filter((r) => r.my_status !== "completed");
      const body = items.length
        ? `<div class="reto-grid">${items.map((r) => retoCardHTML(r, true)).join("")}</div>`
        : `<div class="lg-empty">${empty}</div>`;
      return `<details class="m-acc2 reto-group" open><summary class="reto-group-h">${title} <span class="reto-count">${items.length}</span></summary>${body}</details>`;
    }).join("");
  } catch (_) { /* 401 */ }
}

async function loadRetoBoard() {
  const status = $("rf-status") ? $("rf-status").value : "";
  const diff = $("rf-diff") ? $("rf-diff").value : "";
  try {
    const d = await getJSON(`/api/retos/board?status=${encodeURIComponent(status)}`);
    let retos = d.retos || [];
    if (diff) retos = retos.filter((r) => String(r.assigned_difficulty || r.difficulty_declared) === diff);
    if (!retos.length) { $("retos-board").innerHTML = `<div class="lg-empty">No hay retos comunitarios con esos filtros. ¡Crea el primero!</div>`; return; }
    // agrupar por temática (orden ya viene por dificultad asc del backend)
    const byTheme = {};
    retos.forEach((r) => { const t = (r.theme || "Otros").trim() || "Otros"; (byTheme[t] = byTheme[t] || []).push(r); });
    $("retos-board").innerHTML = Object.keys(byTheme).sort().map((t) =>
      `<div class="reto-theme"><h5 class="reto-theme-h">${esc(t)}</h5><div class="reto-grid">${byTheme[t].map((r) => retoCardHTML(r, false)).join("")}</div></div>`
    ).join("");
  } catch (_) { /* 401 */ }
}

/* ---------- tarjeta de reto ---------- */

function retoCardHTML(r, mine) {
  const diff = r.assigned_difficulty || r.difficulty_declared || 3;
  const prog = r.my_progress;
  const statusTag = r.status === "closed" ? `<span class="reto-tag closed">Cerrado</span>` : "";
  const relTag = r.my_status === "completed" ? `<span class="reto-tag done">✓ Cumplido</span>`
    : r.my_status === "active" ? `<span class="reto-tag active">En curso</span>` : "";
  const progHTML = prog && r.my_status ? retoProgressBar(prog.pct, prog.done) : "";
  const meta = mine
    ? (r.source === "sensei" ? "Reto personalizado del Sensei" : "")
    : `<span class="reto-meta-i">👥 ${r.participants || 0}</span><span class="reto-meta-i">★ ${r.followers || 0}</span>`;
  return `<div class="reto-card" onclick="openReto(${r.id})">
    <div class="reto-card-top">
      ${r.theme ? `<span class="reto-theme-chip">${esc(r.theme)}</span>` : ""}
      ${statusTag}${relTag}
    </div>
    <div class="reto-card-name">${esc(translateModes(r.name))}</div>
    <div class="reto-card-diff">${stars(diff)} <span class="reto-diff-l">${RETO_DIFF[diff]}</span></div>
    <div class="reto-card-conds">${condSummary(r.conditions)}</div>
    ${progHTML}
    <div class="reto-card-foot">${meta}</div>
  </div>`;
}

/* ---------- detalle ---------- */

async function openReto(id) {
  try {
    const d = await getJSON("/api/retos/" + id);
    if (d.error) { wikiToast(d.error, "err"); return; }
    currentReto = d;
    renderRetoDetail(d);
    openEvModal("reto-detail-modal");
  } catch (_) { /* 401 */ }
}

function renderRetoDetail(d) {
  const meId = currentUser && currentUser.id;
  const mine = d.my;
  const isParticipant = mine && mine.role === "participant";
  const isOwner = d.creator_id && meId && d.creator_id === meId;
  const assigned = d.assigned_difficulty || d.difficulty_declared;
  // condiciones + tu avance
  const condsHTML = (d.conditions || []).map((c, i) => {
    const p = isParticipant && d.my_progress ? d.my_progress.conditions[i] : null;
    const bar = p ? retoProgressBar(p.pct, p.done) : "";
    const val = p ? `<span class="rcd-val ${p.done ? "done" : ""}">${p.current}${c.metric === "winrate" ? "%" : ""} / ${parseInt(c.target, 10)}${p.note ? ` · ${esc(p.note)}` : ""}</span>` : "";
    return `<div class="rcd-cond ${p && p.done ? "done" : ""}"><div class="rcd-cond-top"><span>${p && p.done ? "✅" : "○"} ${esc(retoCondText(c))}</span>${val}</div>${bar}</div>`;
  }).join("");
  // dificultad declarada vs asignada
  let diffBlock = `<div class="rcd-diff">Dificultad: ${stars(assigned)} <b>${RETO_DIFF[assigned]}</b> <span class="hint-inline">(ajustada a tu nivel)</span></div>`;
  if (d.source !== "sensei" && d.difficulty_declared && d.difficulty_declared !== assigned) {
    diffBlock += `<div class="hint-inline">Declarada por quien lo creó: ${RETO_DIFF[d.difficulty_declared]}</div>`;
  }
  // tu avance (qué falta + consejo)
  let avance = "";
  if (isParticipant && d.my_progress) {
    const mp = d.my_progress;
    if (mp.done) avance = `<div class="rcd-avance ok">🎉 ¡Reto cumplido! Lo verificó el seguimiento automático de tus partidas.</div>`;
    else {
      const falta = mp.conditions.filter((p) => !p.done);
      const peor = falta.slice().sort((a, b) => a.pct - b.pct)[0];
      const tip = peor ? `Céntrate en: <b>${esc(peor.text)}</b> (vas por ${peor.pct}%).` : "";
      avance = `<div class="rcd-avance ${mp.expired ? "exp" : ""}">${mp.expired ? "⏰ El plazo venció sin completarlo." : `Te queda ${falta.length} condición(es). ${tip}`}</div>`;
    }
  }
  // participantes / resultados
  const parts = (d.participants || []).filter((p) => p.role === "participant");
  const partsHTML = parts.length
    ? parts.map((p) => `<div class="rcd-part"><span>${esc(p.player_name || p.player_tag || "—")}</span><span class="rcd-part-st ${p.status}">${p.status === "completed" ? "✓ cumplido" : p.status === "abandoned" ? "abandonó" : "en curso"}</span></div>`).join("")
    : `<div class="hint">Aún no se ha apuntado nadie.</div>`;
  // acciones
  let actions = "";
  const myPlayers = Object.values(playersById || {});
  const tagSel = myPlayers.length
    ? `<select id="reto-join-tag" class="reto-join-sel">${myPlayers.map((p) => `<option value="${esc(p.tag)}" ${p.tag === currentPlayer ? "selected" : ""}>${esc(p.name || p.tag)}</option>`).join("")}</select>` : "";
  if (!mine) {
    actions = `${tagSel}<button class="btn" onclick="joinReto(${d.id})">Apuntarme</button>
      <button class="ghost" onclick="followReto(${d.id})">Seguir</button>`;
  } else if (mine.role === "follower") {
    actions = `${tagSel}<button class="btn" onclick="joinReto(${d.id})">Apuntarme</button>
      <button class="ghost" onclick="leaveReto(${d.id})">Dejar de seguir</button>`;
  } else {
    actions = `<button class="ghost danger" onclick="leaveReto(${d.id})">Abandonar</button>`;
  }
  if (isOwner) actions += `<button class="ghost danger" onclick="deleteReto(${d.id})">Borrar reto</button>`;

  $("reto-detail-body").innerHTML = `
    <div class="rcd-head">
      ${d.theme ? `<span class="reto-theme-chip">${esc(d.theme)}</span>` : ""}
      ${d.source === "sensei" ? `<span class="reto-tag sensei">🥋 del Sensei</span>` : ""}
      ${d.status === "closed" ? `<span class="reto-tag closed">Cerrado</span>` : ""}
    </div>
    <h3 class="rcd-name">${esc(translateModes(d.name))}</h3>
    ${diffBlock}
    ${d.description ? `<p class="rcd-desc">${esc(translateModes(d.description))}</p>` : ""}
    ${d.time_limit_days ? `<div class="hint-inline">⏳ Plazo: ${d.time_limit_days} días desde que te apuntas.</div>` : ""}
    <h4 class="rcd-h">Condiciones <span class="hint-inline">(se verifican solas desde tus partidas)</span></h4>
    <div class="rcd-conds">${condsHTML}</div>
    ${avance}
    <h4 class="rcd-h">Resultados <span class="reto-count">${parts.length}</span></h4>
    <div class="rcd-parts">${partsHTML}</div>
    <div class="modal-actions reto-actions">${actions}</div>`;
}

async function joinReto(id) {
  const sel = $("reto-join-tag");
  const player = sel ? sel.value : null;
  if (!player) { wikiToast("Sigue primero a un jugador en Brawl Sensei para apuntarte.", "err"); return; }
  const r = await jsend(`/api/retos/${id}/join`, "POST", { player });
  if (!r.ok) { wikiToast(r.data.error || r.data.detail || "No se pudo apuntar.", "err"); return; }
  wikiToast("¡Apuntado! Tus partidas a partir de ahora cuentan.", "ok");
  closeEvModal("reto-detail-modal"); loadRetos();
}

async function followReto(id) {
  const r = await jsend(`/api/retos/${id}/follow`, "POST", {});
  if (!r.ok) { wikiToast(r.data.error || "No se pudo seguir.", "err"); return; }
  wikiToast("Siguiendo el reto. Te avisaremos de los resultados.", "ok");
  closeEvModal("reto-detail-modal"); loadRetos();
}

async function leaveReto(id) {
  const r = await jsend(`/api/retos/${id}/join`, "DELETE", null);
  if (!r.ok) { wikiToast("No se pudo.", "err"); return; }
  closeEvModal("reto-detail-modal"); loadRetos();
}

async function deleteReto(id) {
  if (!confirm("¿Borrar este reto? Si participa más gente, solo te desapuntarás tú y el reto seguirá.")) return;
  const r = await jsend(`/api/retos/${id}`, "DELETE", null);
  if (!r.ok) { wikiToast("No se pudo borrar.", "err"); return; }
  wikiToast(r.data.deleted ? "Reto borrado." : "Te has quitado del reto.", "ok");
  closeEvModal("reto-detail-modal"); loadRetos();
}

/* ---------- completados (modal de los contadores) ---------- */

async function openRetoCompleted(source) {
  try {
    const d = await getJSON("/api/retos/completed?source=" + source);
    $("reto-completed-title").textContent = source === "sensei" ? "Retos del Sensei cumplidos" : "Retos de la comunidad cumplidos";
    const items = d.retos || [];
    $("reto-completed-body").innerHTML = items.length
      ? items.map((r) => `<details class="reto-done-item"><summary><span class="reto-done-date">${(r.my_completed || "").slice(0, 10)}</span> <a class="reto-done-link" title="Ver ficha completa" onclick="event.preventDefault();event.stopPropagation();openReto(${r.id})">${esc(r.name)}</a></summary>
          ${r.description ? `<p class="rcd-desc">${esc(r.description)}</p>` : ""}
          <div class="rcd-conds">${(r.conditions || []).map((c) => `<div class="rcd-cond done"><div class="rcd-cond-top"><span>✅ ${esc(retoCondText(c))}</span></div></div>`).join("")}</div></details>`).join("")
      : `<div class="hint">Todavía no has cumplido ninguno. ¡A por ellos!</div>`;
    openEvModal("reto-completed-modal");
  } catch (_) { /* 401 */ }
}

/* ---------- crear reto ---------- */

let RETO_OPTS = null;   // opciones de ámbito {mode:[{v,label,img}], map:[...]}; brawler = catálogo completo

/* Ámbito opcional (brawler/modo/mapa) como multi-selección: checkbox para activar (por
   defecto desactivado y deshabilitado) + desplegable con imágenes y "Todos/Ninguno". */
function scopeMsHTML(kind, label) {
  return `<div class="rc-scope" data-kind="${kind}">
      <label class="rc-scope-en"><input type="checkbox" onchange="retoScopeToggle(this)"> ${esc(label)}</label>
      <div class="rc-ms">
        <button type="button" class="rc-ms-trigger" onclick="retoMsOpen(event,this)">Cualquiera</button>
        <div class="rc-ms-panel">
          <div class="rc-ms-actions"><button type="button" onclick="retoMsAll(event,this,true)">✓ Todos</button><button type="button" onclick="retoMsAll(event,this,false)">Ninguno</button></div>
          <div class="rc-ms-opts"></div>
        </div>
      </div>
    </div>`;
}
function retoScopeOpts(kind) {
  if (kind === "brawler") {
    // Solo brawlers ACTIVOS actuales (del catálogo del servidor), sin colabs temporales
    // como Buzz Lightyear. Si aún no llegó la meta, caemos a las imágenes disponibles.
    const names = (RETO_META && RETO_META.brawlers && RETO_META.brawlers.length)
      ? RETO_META.brawlers
      : Object.keys(ASSETS.brawlers || {});
    return names.slice().sort()
      .map((n) => ({ v: n, label: n, img: (ASSETS.brawlers || {})[n] }));
  }
  return (RETO_OPTS && RETO_OPTS[kind]) || [];
}
function retoScopeToggle(chk) {
  const sc = chk.closest(".rc-scope");
  sc.querySelector(".rc-ms").classList.toggle("on", chk.checked);   // el activable es .rc-ms, no .rc-scope
  if (chk.checked && !sc.querySelector(".rc-ms-opts").childElementCount) {
    const opts = retoScopeOpts(sc.dataset.kind);
    sc.querySelector(".rc-ms-opts").innerHTML = opts.length
      ? opts.map((o) => `<label class="rc-ms-opt"><input type="checkbox" value="${esc(o.v)}" onchange="retoMsUpd(this)">${o.img ? `<img src="${esc(o.img)}" alt="" onerror="this.style.display='none'">` : ""}<span>${esc(o.label)}</span></label>`).join("")
      : `<div class="ms-empty">Sin opciones (juega alguna partida primero)</div>`;
  }
}
function retoMsOpen(e, btn) { e.preventDefault(); e.stopPropagation(); btn.parentElement.classList.toggle("open"); }
// Cerrar el desplegable de ámbito al hacer clic fuera de él (no se queda abierto).
document.addEventListener("click", (e) => {
  document.querySelectorAll(".rc-ms.open").forEach((ms) => {
    if (!ms.contains(e.target)) ms.classList.remove("open");
  });
});
function retoMsAll(e, btn, all) {
  e.preventDefault();
  const ms = btn.closest(".rc-ms");
  ms.querySelectorAll(".rc-ms-opt input").forEach((c) => { c.checked = all; });
  retoMsTrigger(ms);
}
function retoMsUpd(inp) { retoMsTrigger(inp.closest(".rc-ms")); }
function retoMsTrigger(ms) {
  const sel = [...ms.querySelectorAll(".rc-ms-opt input:checked")];
  const t = ms.querySelector(".rc-ms-trigger");
  t.textContent = !sel.length ? "Cualquiera"
    : sel.length === 1 ? sel[0].closest(".rc-ms-opt").querySelector("span").textContent
      : sel.length + " seleccionados";
}

async function openCreateReto() {
  if (!RETO_META) {
    try { RETO_META = await getJSON("/api/retos/meta"); } catch (_) { return; }
  }
  if (!RETO_OPTS) {   // modos/mapas con valores que casan con las partidas
    try {
      const f = await getJSON("/api/filters?player=" + encodeURIComponent(currentPlayer || ""));
      RETO_OPTS = {
        mode: (f.modes || []).map((m) => ({ v: m, label: modeName(m), img: (modeAsset(m) || {}).icon })),
        map: (f.maps || []).map((m) => ({ v: m, label: mapNameEs(m), img: (mapAsset(m) || {}).image })),
      };
    } catch (_) { RETO_OPTS = { mode: [], map: [] }; }
  }
  $("rc-name").value = ""; $("rc-theme").value = ""; $("rc-diff").value = "3";
  $("rc-vis").value = "public"; $("rc-limit").value = ""; $("rc-desc").value = "";
  $("rc-err").textContent = "";
  $("reto-help").style.display = "none";
  $("reto-conditions").innerHTML = Object.entries(RETO_META.metrics).map(([key, m]) => `
    <div class="rc-cond">
      <label class="rc-cond-on"><input type="checkbox" data-metric="${key}" onchange="this.closest('.rc-cond').classList.toggle('on', this.checked)"> <b>${esc(m.label)}</b></label>
      <div class="rc-cond-fields">
        <span class="rc-f">objetivo <input type="number" min="1" class="rc-target" placeholder="${key === "winrate" ? "%" : "nº"}"></span>
        ${m.min_games ? `<span class="rc-f">mín. partidas <input type="number" min="1" class="rc-mingames" placeholder="p.ej. 20"></span>` : ""}
        <div class="rc-scopes">${scopeMsHTML("brawler", "Brawler")}${scopeMsHTML("mode", "Modo")}${scopeMsHTML("map", "Mapa")}</div>
      </div>
      <div class="rc-cond-help">${esc(m.help)}</div>
    </div>`).join("");
  openEvModal("reto-create-modal");
}

function toggleRetoHelp() {
  const h = $("reto-help");
  h.style.display = h.style.display === "none" ? "block" : "none";
}

function collectRetoConditions() {
  const conds = [];
  document.querySelectorAll("#reto-conditions .rc-cond").forEach((row) => {
    const chk = row.querySelector("input[data-metric]");
    if (!chk.checked) return;
    const target = parseFloat(row.querySelector(".rc-target").value);
    if (!target || target <= 0) return;
    const c = { metric: chk.dataset.metric, target };
    const mg = row.querySelector(".rc-mingames");
    if (mg && mg.value) c.min_games = parseInt(mg.value, 10);
    const scope = {};
    row.querySelectorAll(".rc-scope").forEach((sc) => {
      const en = sc.querySelector(".rc-scope-en input");
      if (!en || !en.checked) return;   // ámbito no activado = opcional, se ignora
      const vals = [...sc.querySelectorAll(".rc-ms-opt input:checked")].map((c) => c.value);
      if (vals.length) scope[sc.dataset.kind] = vals.length === 1 ? vals[0] : vals;
    });
    if (Object.keys(scope).length) c.scope = scope;
    conds.push(c);
  });
  return conds;
}

async function submitCreateReto() {
  const name = $("rc-name").value.trim();
  if (!name) { $("rc-err").textContent = "Ponle un nombre al reto."; return; }
  const conditions = collectRetoConditions();
  if (!conditions.length) { $("rc-err").textContent = "Marca al menos una condición con su objetivo."; return; }
  const payload = {
    name, theme: $("rc-theme").value.trim(), description: $("rc-desc").value.trim(),
    difficulty: parseInt($("rc-diff").value, 10), visibility: $("rc-vis").value,
    time_limit_days: $("rc-limit").value ? parseInt($("rc-limit").value, 10) : null,
    conditions,
  };
  const r = await jsend("/api/retos", "POST", payload);
  if (!r.ok) { $("rc-err").textContent = r.data.error || "No se pudo crear."; return; }
  wikiToast("¡Reto creado! Ya está en el tablón.", "ok");
  closeEvModal("reto-create-modal"); loadRetos();
}
