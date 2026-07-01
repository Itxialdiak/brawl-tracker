/* BrawlSensei · 05-admin.js
   Administración (cambios pendientes, usuarios, jugadores, métricas, historial).
   Se carga como <script src> desde index.html, en orden. El JS de cliente
   SIEMPRE es visible en el navegador: aquí no van secretos. */
/* ============================ ADMINISTRACIÓN ============================ */
function updatePendingBadge(n) {
  const b1 = $("admin-pending-badge"), b2 = $("admin-tab-badge");
  [b1, b2].forEach((b) => { if (b) { b.textContent = n; b.style.display = n > 0 ? "" : "none"; } });
  const all = $("approve-all-btn"); if (all) all.style.display = n > 0 ? "" : "none";
}
function showAdminTab(name) {
  document.querySelectorAll(".atab").forEach((t) => t.classList.toggle("active", t.dataset.atab === name));
  document.querySelectorAll(".admin-panel").forEach((p) => p.classList.toggle("active", p.id === "admin-" + name));
  if (name === "pending") loadAdminPending();
  if (name === "users") loadAdminUsers();
  if (name === "players") loadAdminPlayers();
  if (name === "metrics") loadAdminMetrics();
  if (name === "history") loadAdminHistory();
}

const KIND_LABEL = { edit: ["Edición", "pk-edit"], create_section: ["Nueva sección", "pk-create"],
  create_subsection: ["Nueva subsección", "pk-create"], create_separator: ["Nuevo separador", "pk-create"],
  delete: ["Eliminación", "pk-delete"], reorder: ["Reordenación", "pk-reorder"],
  translate: ["Traducción", "pk-translate"] };

async function loadAdminPending() {
  const wrap = $("pending-list");
  try {
    const d = await getJSON("/api/admin/proposals?status=pending");
    const list = d.proposals || [];
    updatePendingBadge(list.length);
    if (!list.length) { wrap.innerHTML = '<div class="admin-empty">No hay cambios pendientes. 🎉</div>'; return; }
    wrap.innerHTML = list.map((p) => {
      const [lab, cls] = KIND_LABEL[p.kind] || ["Cambio", ""];
      const when = new Date(p.created_at).toLocaleString("es-ES", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
      return `<div class="prop-card">
        <div class="prop-main">
          <span class="prop-kind ${cls}">${lab}</span>
          <div class="prop-summary">${esc(p.summary || "(sin resumen)")}</div>
          <div class="prop-meta">por <b>${esc(p.username || "—")}</b> · ${when}</div>
        </div>
        <div class="prop-actions">
          <button class="user-actions" onclick="openReview(${p.id})" style="border:1px solid var(--border);background:transparent;color:var(--cyan);padding:7px 13px;border-radius:9px;cursor:pointer">Revisar</button>
        </div></div>`;
    }).join("");
  } catch (e) {
    if (String(e.message) === "401") return;
    wrap.innerHTML = '<div class="admin-empty">No se pudo cargar (¿tienes permisos de administrador?).</div>';
  }
}

let reviewId = null;
async function openReview(pid) {
  reviewId = pid;
  try {
    const d = await getJSON("/api/admin/proposals/" + pid);
    const p = d.proposal, cur = d.current, parent = d.parent;
    const [lab] = KIND_LABEL[p.kind] || ["Cambio"];
    $("rv-title").textContent = lab;
    $("rv-meta").innerHTML = `Propuesto por <b>${esc(p.username || "—")}</b> · ${new Date(p.created_at).toLocaleString("es-ES")}`;
    $("rv-just").innerHTML = `<b>Resumen:</b> ${esc(p.summary || "—")}<br><b>Justificación:</b> ${esc(p.justification || "—")}`;
    $("rv-diff").innerHTML = renderDiff(p, cur, parent, d.current_translation);
    $("rv-err").textContent = "";
    $("review-modal").classList.add("open");
  } catch (e) { wikiToast("No se pudo abrir la propuesta", "err"); }
}
function closeReview() { $("review-modal").classList.remove("open"); reviewId = null; }

function diffCol(side, header, title, bodyHtml) {
  return `<div class="rv-col ${side}"><div class="rv-col-h">${header}</div>${title ? "<h4>" + esc(title) + "</h4>" : ""}<div class="wiki-body">${wrapTables(bodyHtml) || ""}</div></div>`;
}
function renderDiff(p, cur, parent, curTr) {
  const pl = p.payload || {};
  if (p.kind === "translate") {
    const lg = (pl.lang || "").toUpperCase();
    return diffCol("before", "Traducción actual (" + lg + ")", curTr ? curTr.title : "",
        curTr ? curTr.body : "<p style='color:var(--muted)'>Aún no hay traducción en este idioma.</p>")
      + diffCol("after", "Traducción propuesta (" + lg + ")", pl.title, pl.body);
  }
  if (p.kind === "edit") {
    return diffCol("before", "Versión actual (pública)", cur ? cur.title : "", cur ? cur.body : "")
         + diffCol("after", "Propuesta", pl.title, pl.body);
  }
  if (p.kind === "create_section" || p.kind === "create_subsection") {
    const ctx = p.kind === "create_subsection" && parent ? "<p style='color:var(--muted)'>En la sección «" + esc(parent.title) + "».</p>" : "";
    return diffCol("before", "Ahora", "", "<p style='color:var(--muted)'>Este apartado todavía no existe.</p>")
         + diffCol("after", "Se creará", pl.title, ctx + (pl.body || ""));
  }
  if (p.kind === "create_separator") {
    return diffCol("before", "Ahora", "", "<p style='color:var(--muted)'>No existe.</p>")
         + diffCol("after", "Se creará (separador)", pl.title, "");
  }
  if (p.kind === "delete") {
    return diffCol("before", "Versión actual (se eliminará)", cur ? cur.title : "", cur ? cur.body : "")
         + diffCol("after", "Después", "", "<p style='color:var(--loss)'>El apartado se eliminará.</p>");
  }
  if (p.kind === "reorder") {
    const byId = {}; wikiTree.forEach((n) => { byId[n.id] = n.title; (n.subs || []).forEach((s) => byId[s.id] = s.title); });
    const newOrder = (pl.top || []).map((id) => "<li>" + esc(byId[id] || ("#" + id)) + "</li>").join("");
    return diffCol("before", "Orden actual", "", "<p style='color:var(--muted)'>El orden actual del índice.</p>")
         + diffCol("after", "Nuevo orden propuesto", "", "<ol>" + newOrder + "</ol>");
  }
  return diffCol("after", "Cambio", "", "");
}

async function reviewApprove() {
  if (!reviewId) return;
  const { ok, d } = await apiSend("/api/admin/proposals/" + reviewId + "/approve", "POST");
  if (!ok) { $("rv-err").textContent = d.error || "No se pudo aprobar."; return; }
  closeReview(); wikiToast("Cambio aprobado y publicado ✓", "ok");
  loadAdminPending(); loadWikiTree(true);
}
async function reviewReject() {
  if (!reviewId) return;
  const { ok, d } = await apiSend("/api/admin/proposals/" + reviewId + "/reject", "POST");
  if (!ok) { $("rv-err").textContent = d.error || "No se pudo rechazar."; return; }
  closeReview(); wikiToast("Cambio rechazado", "");
  loadAdminPending();
}
async function approveAll() {
  if (!confirm("¿Aprobar TODOS los cambios pendientes? Se publicarán en orden.")) return;
  const { ok, d } = await apiSend("/api/admin/proposals/approve-all", "POST");
  if (ok) { wikiToast("Aprobados " + (d.approved || 0) + " cambios ✓", "ok"); loadAdminPending(); loadWikiTree(true); }
}

/* ---------- Usuarios ---------- */
async function loadAdminUsers() {
  const wrap = $("users-list");
  try {
    const d = await getJSON("/api/admin/users");
    wrap.innerHTML = (d.users || []).map((u) => {
      const me = currentUser && u.id === currentUser.id;
      return `<div class="user-row">
        <div class="user-name">${esc(u.username)}${u.is_admin ? '<span class="badge-admin">admin</span>' : ""}${me ? '<span class="you">(tú)</span>' : ""}</div>
        <div class="user-actions">
          <button onclick="toggleUserAdmin(${u.id}, ${u.is_admin ? 0 : 1})" ${me && u.is_admin ? "disabled style='opacity:.4;cursor:not-allowed'" : ""}>${u.is_admin ? "Quitar admin" : "Hacer admin"}</button>
          <button onclick="openUserPw(${u.id}, '${esc(u.username)}')">Resetear contraseña</button>
          <button class="danger" onclick="deleteUser(${u.id}, '${esc(u.username)}')" ${me ? "disabled style='opacity:.4;cursor:not-allowed'" : ""}>Borrar</button>
        </div></div>`;
    }).join("");
  } catch (e) { if (String(e.message) !== "401") wrap.innerHTML = '<div class="admin-empty">No se pudo cargar.</div>'; }
}
async function toggleUserAdmin(uid, val) {
  const { ok, d } = await apiSend("/api/admin/users/" + uid + "/admin", "POST", { is_admin: !!val });
  if (!ok) { wikiToast(d.error || "No se pudo cambiar", "err"); return; }
  loadAdminUsers();
}
async function deleteUser(uid, name) {
  if (!confirm("¿Borrar al usuario «" + name + "»? Esta acción es permanente.")) return;
  const { ok, d } = await apiSend("/api/admin/users/" + uid, "DELETE");
  if (!ok) { wikiToast(d.error || "No se pudo borrar", "err"); return; }
  wikiToast("Usuario borrado", ""); loadAdminUsers();
}
let pwUserId = null;
function openUserPw(uid, name) {
  pwUserId = uid; $("upw-sub").textContent = "Nueva contraseña para «" + name + "».";
  $("upw-pass").value = ""; $("upw-err").textContent = "";
  $("user-pw-modal").classList.add("open"); $("upw-pass").focus();
}
function closeUserPw() { $("user-pw-modal").classList.remove("open"); }
async function submitUserPw() {
  const pw = $("upw-pass").value;
  if (pw.length < 6) { $("upw-err").textContent = "Mínimo 6 caracteres."; return; }
  const { ok, d } = await apiSend("/api/admin/users/" + pwUserId + "/password", "POST", { password: pw });
  if (!ok) { $("upw-err").textContent = d.error || "No se pudo."; return; }
  closeUserPw(); wikiToast("Contraseña actualizada ✓", "ok");
}
function openCreateUser() {
  $("uc-name").value = ""; $("uc-pass").value = ""; $("uc-admin").checked = false; $("uc-err").textContent = "";
  $("user-create-modal").classList.add("open"); $("uc-name").focus();
}
function closeCreateUser() { $("user-create-modal").classList.remove("open"); }
async function submitCreateUser() {
  const username = $("uc-name").value.trim(), password = $("uc-pass").value, is_admin = $("uc-admin").checked;
  const err = $("uc-err"); err.textContent = "";
  if (username.length < 3) { err.textContent = "Usuario inválido (mínimo 3 caracteres)."; return; }
  if (password.length < 6) { err.textContent = "Contraseña mínima de 6 caracteres."; return; }
  const { ok, d } = await apiSend("/api/admin/users", "POST", { username, password, is_admin });
  if (!ok) { err.textContent = d.error || d.detail || "No se pudo crear."; return; }
  closeCreateUser(); wikiToast("Usuario creado ✓", "ok"); loadAdminUsers();
}

/* ---------- Jugadores (admin) ---------- */
async function loadAdminPlayers() {
  const wrap = $("players-list");
  try {
    const d = await getJSON("/api/admin/players");
    const players = d.players || [];
    if (!players.length) { wrap.innerHTML = '<div class="admin-empty">No hay jugadores trackeados.</div>'; return; }
    wrap.innerHTML = players.map((p) => {
      const review = !!p.last_error, orphan = (p.followers || 0) === 0, name = p.name || p.tag;
      const cls = review ? "needs-review" : (orphan ? "orphan" : "");
      const badges = (review ? '<span class="review-badge" title="Revísalo o bórralo">necesita revisión</span>' : "")
        + (orphan ? '<span class="orphan-badge">huérfano</span>' : "")
        + (p.active ? "" : '<span class="off-badge">inactivo</span>');
      const meta = `${(p.battles || 0).toLocaleString("es-ES")} partidas · ${p.followers} seguidor${p.followers === 1 ? "" : "es"}`
        + (review ? ` · <span class="pl-err">⚠ ${esc(p.last_error)}</span>` : "");
      return `<div class="user-row ${cls}">
        <div class="user-name">${esc(name)}<span class="pl-tag">${esc(p.tag)}</span>${badges}</div>
        <div class="pl-meta">${meta}</div>
        <div class="user-actions"><button class="danger" onclick="delAdminPlayer('${esc(p.tag)}', '${esc(name)}')">Borrar</button></div>
      </div>`;
    }).join("");
  } catch (e) { if (String(e.message) !== "401") wrap.innerHTML = '<div class="admin-empty">No se pudo cargar.</div>'; }
}
async function openAddPlayers() {
  const tags = prompt("Añadir jugador(es) al trackeo (aunque no los siga ningún usuario).\n\nPega uno o varios player IDs separados por comas:");
  if (!tags || !tags.trim()) return;
  const { ok, d } = await apiSend("/api/admin/players", "POST", { tags });
  if (!ok) { wikiToast(d.error || "No se pudo añadir", "err"); return; }
  const n = (d.added || []).length, s = (d.skipped || []).length;
  wikiToast(`Añadidos ${n}${s ? ` · ${s} ya estaban` : ""} ✓`, "ok");
  loadAdminPlayers();
}
async function delAdminPlayer(tag, name) {
  if (!confirm(`¿Dejar de trackear a ${name} (${tag})? No se recopilarán más sus partidas.`)) return;
  const delBattles = confirm("¿Borrar también su historial de partidas?\n\nAceptar = borrar todo · Cancelar = conservar el historial");
  const { ok, d } = await apiSend(`/api/admin/players/${encodeURIComponent(tag)}?delete_battles=${delBattles}`, "DELETE");
  if (!ok) { wikiToast(d.error || "No se pudo borrar", "err"); return; }
  wikiToast(delBattles ? "Jugador y partidas borrados" : "Jugador eliminado (historial conservado)", "ok");
  loadAdminPlayers();
}

/* ---------- Métricas (admin) ---------- */
async function loadAdminMetrics() {
  const wrap = $("metrics-body");
  try {
    const m = await getJSON("/api/admin/metrics");
    const fmt = (n) => (n || 0).toLocaleString("es-ES");
    const card = (label, value, sub) => `<div class="metric-card"><div class="metric-v">${value}</div><div class="metric-l">${label}</div>${sub ? `<div class="metric-s">${sub}</div>` : ""}</div>`;
    const eur = (o) => { const c = (o || {}).cost_eur || 0; return (c ? (c < 1 ? c.toFixed(4) : c.toFixed(2)) : "0") + " €"; };
    const tokSub = (o) => `${fmt((o || {}).tokens)} tok · ${fmt((o || {}).requests)} pet.`;
    const general = card("Usuarios", fmt(m.users)) +
      card("Jugadores trackeados", fmt(m.active_players), `${fmt(m.orphans)} huérfanos · ${fmt(m.players)} en total`) +
      card("Partidas en BD", fmt(m.battles)) +
      card("Informes IA generados", fmt(m.reports));
    const ai = m.ai || {};
    const aiCards = card("Total", eur(ai.total), tokSub(ai.total)) +
      card("Este mes", eur(ai.month), tokSub(ai.month)) +
      card("Esta semana", eur(ai.week), tokSub(ai.week)) +
      card("Hoy", eur(ai.day), tokSub(ai.day));
    const modelName = (ai.model || "claude").replace("claude-", "").replace(/-/g, " ");
    wrap.innerHTML = `<h4 class="metrics-sub">Métricas de la aplicación</h4>
      <div class="metrics-grid">${general}</div>
      <h4 class="metrics-sub">Métricas de Consumo · coste estimado de la IA</h4>
      <div class="metrics-grid">${aiCards}</div>
      <p class="hint" style="margin-top:10px;font-size:.78rem">Estimado con tarifas de <b>${esc(modelName)}</b> (${(ai.price_in_eur || 0).toFixed(2)} € entrada / ${(ai.price_out_eur || 0).toFixed(2)} € salida por millón de tokens; la salida cuesta 5× la entrada) y conversión aproximada USD→EUR.</p>`;
  } catch (e) { if (String(e.message) !== "401") wrap.innerHTML = '<div class="admin-empty">No se pudo cargar.</div>'; }
}

/* ---------- Historial ---------- */
async function loadAdminHistory() {
  const wrap = $("history-list");
  try {
    const d = await getJSON("/api/admin/history");
    const list = d.history || [];
    if (!list.length) { wrap.innerHTML = '<div class="admin-empty">Aún no hay historial de cambios.</div>'; return; }
    wrap.innerHTML = list.map((h) => {
      const when = new Date(h.changed_at).toLocaleString("es-ES");
      const ck = { edit: "editado", delete: "eliminado", revert: "restaurado" }[h.change_kind] || h.change_kind;
      return `<div class="hist-row">
        <div class="hist-main"><b>${esc(h.title)}</b> · versión guardada antes de ser ${esc(ck)}
          <div class="hist-when">por ${esc(h.username || "—")} · ${when}</div></div>
        <div class="user-actions"><button onclick="revertHistory(${h.id}, '${esc(h.title)}')">↩ Restaurar esta versión</button></div>
      </div>`;
    }).join("");
  } catch (e) { if (String(e.message) !== "401") wrap.innerHTML = '<div class="admin-empty">No se pudo cargar.</div>'; }
}
async function revertHistory(hid, title) {
  if (!confirm("¿Restaurar la versión guardada de «" + title + "»? Sustituirá el contenido actual (se guardará el actual en el historial).")) return;
  const { ok } = await apiSend("/api/admin/history/" + hid + "/revert", "POST");
  if (ok) { wikiToast("Versión restaurada ✓", "ok"); loadAdminHistory(); loadWikiTree(true); }
}


