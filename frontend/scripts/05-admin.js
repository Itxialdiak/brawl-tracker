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
// Visibilidad de pestañas del panel según los permisos del usuario (RBAC). Colaboradores
// ven Métricas (solo página) + Historial + Cambios + Traducciones; traductores solo Traducciones;
// admin/root todas. No carga datos: solo ajusta qué pestañas se ven y cuál queda activa.
function applyAdminTabPerms(perms) {
  perms = perms || [];
  const has = (p) => !p || perms.includes(p);
  let firstVisible = null, activeVisible = false;
  document.querySelectorAll(".atab").forEach((t) => {
    const ok = has(t.dataset.perm);
    t.style.display = ok ? "" : "none";
    if (ok && !firstVisible) firstVisible = t;
    if (ok && t.classList.contains("active")) activeVisible = true;
  });
  if (firstVisible && !activeVisible) {
    document.querySelectorAll(".atab").forEach((t) => t.classList.remove("active"));
    firstVisible.classList.add("active");
    const name = firstVisible.dataset.atab;
    document.querySelectorAll(".admin-panel").forEach((p) => p.classList.toggle("active", p.id === "admin-" + name));
  }
}
// Abre la pestaña activa (visible) al entrar en Administración; carga sus datos.
function openDefaultAdminTab() {
  const tabs = [...document.querySelectorAll(".atab")].filter((t) => t.style.display !== "none");
  const active = tabs.find((t) => t.classList.contains("active")) || tabs[0];
  if (active) showAdminTab(active.dataset.atab);
}

function showAdminTab(name) {
  document.querySelectorAll(".atab").forEach((t) => t.classList.toggle("active", t.dataset.atab === name));
  document.querySelectorAll(".admin-panel").forEach((p) => p.classList.toggle("active", p.id === "admin-" + name));
  if (name === "pending") loadAdminPending();
  if (name === "users") loadAdminUsers();
  if (name === "roles") loadAdminRoles();
  if (name === "players") loadAdminPlayers();
  if (name === "metrics") loadAdminMetrics();
  if (name === "history") loadAdminHistory();
  if (name === "i18n") initI18nEditor();
}

/* ---------- Traducciones de la interfaz (Rosetta) ---------- */
let I18N_CAT = null;               // catálogo {exact:[], patterns:[]}
let i18nTargetMap = {}, i18nRefMap = {};

async function initI18nEditor() {
  const tsel = $("i18n-target"), rsel = $("i18n-ref");
  if (tsel.options.length) return;   // ya inicializado
  const langs = window.I18N_LANGS || [];
  tsel.innerHTML = langs.filter((l) => l.code !== "es")
    .map((l) => `<option value="${l.code}">${esc(l.label)}${l.soon ? " ·" : ""}</option>`).join("");
  rsel.innerHTML = `<option value="">— sin referencia —</option>` +
    langs.map((l) => `<option value="${l.code}"${l.code === "en" ? " selected" : ""}>${esc(l.label)}</option>`).join("");
  try { I18N_CAT = await getJSON("/static/i18n/_sources.json"); } catch (_) { I18N_CAT = { exact: [], patterns: [] }; }
  loadI18nEditor();
}

async function loadI18nEditor() {
  const lang = $("i18n-target").value;
  if (!lang) return;
  try { i18nTargetMap = (await getJSON("/api/admin/i18n?lang=" + encodeURIComponent(lang))).map || {}; }
  catch (_) { i18nTargetMap = {}; }
  await loadI18nRef();
}
async function loadI18nRef() {
  const ref = $("i18n-ref").value;
  if (ref && ref !== "es") {
    try { i18nRefMap = (await getJSON("/api/admin/i18n?lang=" + encodeURIComponent(ref))).map || {}; }
    catch (_) { i18nRefMap = {}; }
  } else { i18nRefMap = {}; }
  renderI18nRows();
}

function i18nRowHTML(src, kind) {
  const cur = (i18nTargetMap[src] || {}).target || "";
  const refV = $("i18n-ref").value;
  const ref = refV === "es" ? src : ((i18nRefMap[src] || {}).target || "");
  const refHTML = refV ? `<div class="i18n-ref-cell" title="Referencia">${esc(ref)}</div>` : "";
  return `<div class="i18n-row${cur ? " done" : ""}" data-src="${esc(src)}" data-kind="${kind}">
      <div class="i18n-src">${esc(src)}${kind === "pattern" ? '<span class="i18n-badge">patrón</span>' : ""}</div>
      ${refHTML}
      <input type="text" class="i18n-inp" value="${esc(cur)}" placeholder="traducción…"
        onchange="saveI18n(this)" onkeydown="if(event.key==='Enter')this.blur()">
    </div>`;
}

function renderI18nRows() {
  if (!I18N_CAT) return;
  const q = ($("i18n-search").value || "").trim().toLowerCase();
  const refOn = !!$("i18n-ref").value;
  const match = (s) => !q || s.toLowerCase().includes(q);
  const exact = I18N_CAT.exact.filter(match);
  const pats = I18N_CAT.patterns.filter(match);
  const wrap = $("i18n-rows");
  wrap.classList.toggle("with-ref", refOn);
  let done = 0;
  (I18N_CAT.exact.concat(I18N_CAT.patterns)).forEach((s) => { if ((i18nTargetMap[s] || {}).target) done++; });
  $("i18n-stat").textContent = `${done}/${I18N_CAT.exact.length + I18N_CAT.patterns.length} traducidas`;
  const sec = (title, arr, kind) => arr.length
    ? `<div class="i18n-sec-h">${title} <span class="reto-count">${arr.length}</span></div>${arr.map((s) => i18nRowHTML(s, kind)).join("")}` : "";
  wrap.innerHTML = (sec("Textos", exact, "exact") + sec("Frases con variables (patrones)", pats, "pattern"))
    || `<div class="lg-empty">Sin resultados para «${esc(q)}».</div>`;
}

async function saveI18n(inp) {
  const row = inp.closest(".i18n-row");
  const source = row.dataset.src, kind = row.dataset.kind, lang = $("i18n-target").value;
  const target = inp.value.trim();
  const { ok, d } = await apiSend("/api/admin/i18n", "POST", { lang, source, kind, target });
  if (!ok) { wikiToast(d.error || "No se pudo guardar.", "err"); return; }
  i18nTargetMap[source] = { kind, target };
  row.classList.toggle("done", !!target);
  wikiToast("Guardado ✓", "ok");
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
// Etiquetas de rol (coinciden con app/rbac.py). El color se define en CSS por data-role.
const ROLE_LABELS = { root: "Root", admin: "Administrador", collaborator: "Colaborador", translator: "Traductor", user: "Usuario" };
function roleBadge(role) {
  const r = ROLE_LABELS[role] ? role : "user";
  return `<span class="role-badge" data-role="${r}">${esc(ROLE_LABELS[r])}</span>`;
}

async function loadAdminUsers() {
  const wrap = $("users-list");
  try {
    const d = await getJSON("/api/admin/users");
    const users = d.users || [];
    const pending = users.filter((u) => (u.status || "active") === "pending");
    const active = users.filter((u) => (u.status || "active") !== "pending");
    updateUsersBadge(pending.length);
    const userRow = (u) => {
      const me = currentUser && u.id === currentUser.id;
      const sys = !!u.hidden;                                 // cuenta de sistema (tester)
      const disabledAcc = (u.status === "disabled");
      const badges = roleBadge(u.role)
        + (u.is_croker ? '<span class="role-badge" data-role="croker">Croker</span>' : "")
        + (sys ? '<span class="badge-admin" style="background:rgba(148,141,196,.2);color:#b8b3ff">sistema</span>' : "")
        + (disabledAcc ? '<span class="off-badge">deshabilitada</span>' : "")
        + (me ? '<span class="you">(tú)</span>' : "");
      return `<div class="user-row">
        <div class="user-name">${esc(u.username)}${badges}${u.email ? `<span class="pl-tag">${esc(u.email)}</span>` : ""}</div>
        <div class="user-actions">
          <button onclick="openUserPw(${u.id}, ${esc(JSON.stringify(u.username))})">Resetear contraseña</button>
          <button class="danger" onclick="deleteUser(${u.id}, ${esc(JSON.stringify(u.username))})" ${me ? "disabled style='opacity:.4;cursor:not-allowed'" : ""}>Borrar</button>
        </div></div>`;
    };
    const pendingRow = (u) => `<div class="user-row is-pending">
        <div class="user-name">${esc(u.username)}<span class="pending-badge">pendiente</span>${u.email ? `<span class="pl-tag">${esc(u.email)}</span>` : ""}</div>
        <div class="user-actions">
          <button class="ok" onclick="approveUser(${u.id}, ${esc(JSON.stringify(u.username))})">✓ Aprobar</button>
          <button class="danger" onclick="rejectUser(${u.id}, ${esc(JSON.stringify(u.username))})">Rechazar</button>
        </div></div>`;
    let html = "";
    if (pending.length) {
      html += `<div class="admin-subhead">Pendientes de aprobación <span class="admin-badge">${pending.length}</span></div>`
        + pending.map(pendingRow).join("");
    }
    html += `<div class="admin-subhead">Cuentas${pending.length ? " activas" : ""}</div>`
      + active.map(userRow).join("");
    html += `<p class="admin-hint" style="margin-top:14px">Para cambiar el <b>rol</b> de un usuario usa la pestaña <b>Roles</b> (arrastrar y soltar).</p>`;
    wrap.innerHTML = html;
  } catch (e) { if (String(e.message) !== "401") wrap.innerHTML = '<div class="admin-empty">No se pudo cargar.</div>'; }
}
function updateUsersBadge(n) {
  const b = $("admin-users-badge");
  if (b) { b.textContent = n; b.style.display = n > 0 ? "" : "none"; }
}
async function approveUser(uid, name) {
  const { ok, d } = await apiSend("/api/admin/users/" + uid + "/approve", "POST", {});
  if (!ok) { wikiToast(d.error || "No se pudo aprobar", "err"); return; }
  wikiToast("Cuenta «" + name + "» aprobada ✓", "ok");
  loadAdminUsers();
}
async function rejectUser(uid, name) {
  if (!confirm("¿Rechazar la solicitud de «" + name + "»? Se eliminará la cuenta pendiente.")) return;
  const { ok, d } = await apiSend("/api/admin/users/" + uid, "DELETE");
  if (!ok) { wikiToast(d.error || "No se pudo rechazar", "err"); return; }
  wikiToast("Solicitud rechazada", "ok");
  loadAdminUsers();
}

/* ---------- Roles y permisos (arrastrar y soltar) ---------- */
let _rolesData = null, _dragUid = null;
async function loadAdminRoles() {
  const board = $("roles-board");
  try {
    const [rbac, ud] = await Promise.all([getJSON("/api/admin/rbac"), getJSON("/api/admin/users")]);
    _rolesData = rbac;
    const assignable = new Set(rbac.assignable || []);
    // Cuentas de sistema (tester) fuera del tablero; el resto se agrupan por rol.
    const users = (ud.users || []).filter((u) => !u.hidden);
    const byRole = {};
    (rbac.roles || []).forEach((r) => (byRole[r.id] = []));
    users.forEach((u) => { const r = ROLE_LABELS[u.role] ? u.role : "user"; (byRole[r] = byRole[r] || []).push(u); });
    // Mostramos de mayor a menor autoridad.
    const order = [...(rbac.roles || [])].sort((a, b) => b.level - a.level);
    board.innerHTML = order.map((r) => {
      const canDrop = assignable.has(r.id);
      const chips = (byRole[r.id] || []).map((u) => {
        const me = currentUser && u.id === currentUser.id;
        const draggable = !me;                    // no puedes moverte a ti mismo
        return `<div class="role-chip${me ? " is-me" : ""}" ${draggable ? `draggable="true" ondragstart="roleDragStart(event, ${u.id})" ondragend="roleDragEnd(event)"` : ""}>
          ${esc(u.username)}${u.is_croker ? ' <span class="mini-croker" title="Croker">◆</span>' : ""}${me ? ' <span class="you">(tú)</span>' : ""}</div>`;
      }).join("") || '<div class="role-empty">— vacío —</div>';
      return `<div class="role-col${canDrop ? " droppable" : " locked"}" data-role="${r.id}"
          ondragover="roleDragOver(event)" ondragleave="roleDragLeave(event)" ondrop="roleDrop(event, '${r.id}')">
        <div class="role-col-head" data-role="${r.id}">${esc(r.label_plural || r.label)}
          ${canDrop ? "" : '<span class="role-lock" title="No puedes asignar este rol">🔒</span>'}</div>
        <div class="role-col-body">${chips}</div>
      </div>`;
    }).join("");
  } catch (e) { if (String(e.message) !== "401") board.innerHTML = '<div class="admin-empty">No se pudo cargar.</div>'; }
}
function roleDragStart(ev, uid) { _dragUid = uid; ev.dataTransfer.effectAllowed = "move"; try { ev.dataTransfer.setData("text/plain", String(uid)); } catch (_) {} }
function roleDragEnd(ev) { _dragUid = null; document.querySelectorAll(".role-col.drag-over").forEach((c) => c.classList.remove("drag-over")); }
function roleDragOver(ev) {
  const col = ev.currentTarget;
  if (!col.classList.contains("droppable")) return;   // no permitir soltar en columnas bloqueadas
  ev.preventDefault(); ev.dataTransfer.dropEffect = "move"; col.classList.add("drag-over");
}
function roleDragLeave(ev) { ev.currentTarget.classList.remove("drag-over"); }
async function roleDrop(ev, role) {
  const col = ev.currentTarget; col.classList.remove("drag-over");
  if (!col.classList.contains("droppable")) return;
  ev.preventDefault();
  let uid = _dragUid;
  if (uid == null) { const t = ev.dataTransfer.getData("text/plain"); uid = t ? parseInt(t, 10) : null; }
  _dragUid = null;
  if (uid == null) return;
  const { ok, d } = await apiSend("/api/admin/users/" + uid + "/role", "POST", { role });
  if (!ok) { wikiToast(d.error || "No se pudo cambiar el rol", "err"); return; }
  wikiToast("Rol actualizado: " + (d.role_label || role) + " ✓", "ok");
  loadAdminRoles(); loadAdminUsers();
}
async function deleteUser(uid, name) {
  if (!confirm("¿Borrar al usuario «" + name + "»? Esta acción es permanente.")) return;
  // 2.º modal: por defecto se CONSERVAN los jugadores en el tracking; solo se borran si se confirma.
  const alsoPlayers = confirm(
    "Eliminar una cuenta no elimina los jugadores asociados a ella del tracking de jugadores.\n\n" +
    "¿Quieres eliminarlos también?\n\n" +
    "• Aceptar = borrar también los jugadores que no siga ningún otro usuario.\n" +
    "• Cancelar = conservarlos (se siguen recogiendo sus partidas).");
  const { ok, d } = await apiSend("/api/admin/users/" + uid + (alsoPlayers ? "?delete_players=true" : ""), "DELETE");
  if (!ok) { wikiToast(d.error || "No se pudo borrar", "err"); return; }
  wikiToast(alsoPlayers ? "Usuario y sus jugadores borrados" : "Usuario borrado (jugadores conservados)", "ok");
  loadAdminUsers();
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
    // El color rojo (needs-review) SOLO para los que tienen error; huérfano ya no es color, es sección.
    const playerRow = (p) => {
      const review = !!p.last_error, name = p.name || p.tag;
      const badges = (review ? '<span class="review-badge" title="Revísalo o bórralo">necesita revisión</span>' : "")
        + (p.active ? "" : '<span class="off-badge">inactivo</span>');
      const meta = `${(p.battles || 0).toLocaleString("es-ES")} partidas · ${p.followers} seguidor${p.followers === 1 ? "" : "es"}`
        + (review ? ` · <span class="pl-err">⚠ ${esc(p.last_error)}</span>` : "");
      return `<div class="user-row ${review ? "needs-review" : ""}">
        <div class="user-name">${esc(name)}<span class="pl-tag">${esc(p.tag)}</span>${badges}</div>
        <div class="pl-meta">${meta}</div>
        <div class="user-actions"><button class="danger" onclick="delAdminPlayer(${esc(JSON.stringify(p.tag))}, ${esc(JSON.stringify(name))})">Borrar</button></div>
      </div>`;
    };
    // Orden dentro de cada grupo: primero los que necesitan revisión.
    const bySeverity = (a, b) => (b.last_error ? 1 : 0) - (a.last_error ? 1 : 0);
    const followed = players.filter((p) => (p.followers || 0) > 0).sort(bySeverity);
    const orphans = players.filter((p) => (p.followers || 0) === 0).sort(bySeverity);
    let html = `<div class="admin-subhead">Con seguidores <span class="admin-badge">${followed.length}</span></div>`
      + (followed.map(playerRow).join("") || '<div class="admin-empty">Ninguno.</div>');
    html += `<div class="admin-subhead">Huérfanos <span class="admin-badge">${orphans.length}</span>`
      + `<span class="admin-hint" style="margin:0 0 0 8px;display:inline">no los sigue ningún usuario</span></div>`
      + (orphans.map(playerRow).join("") || '<div class="admin-empty">Ninguno.</div>');
    wrap.innerHTML = html;
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
    let consumo = "";
    // Los colaboradores NO ven consumo: el backend redacta `ai` (consumption_hidden).
    if (m.ai) {
      const ai = m.ai;
      const aiCards = card("Total", eur(ai.total), tokSub(ai.total)) +
        card("Este mes", eur(ai.month), tokSub(ai.month)) +
        card("Esta semana", eur(ai.week), tokSub(ai.week)) +
        card("Hoy", eur(ai.day), tokSub(ai.day));
      const modelName = (ai.model || "claude").replace("claude-", "").replace(/-/g, " ");
      consumo = `<h4 class="metrics-sub">Métricas de Consumo · coste estimado de la IA</h4>
        <div class="metrics-grid">${aiCards}</div>
        <p class="hint" style="margin-top:10px;font-size:.78rem">Estimado con tarifas de <b>${esc(modelName)}</b> (${(ai.price_in_eur || 0).toFixed(2)} € entrada / ${(ai.price_out_eur || 0).toFixed(2)} € salida por millón de tokens; la salida cuesta 5× la entrada) y conversión aproximada USD→EUR.</p>`;
    }
    wrap.innerHTML = `<h4 class="metrics-sub">Métricas de la aplicación</h4>
      <div class="metrics-grid">${general}</div>${consumo}`;
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


