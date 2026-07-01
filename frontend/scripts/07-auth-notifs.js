/* BrawlSensei · 07-auth-notifs.js
   autenticación (login/registro/logout) y notificaciones.
   Se carga como <script src> desde index.html, en orden. El JS de cliente
   SIEMPRE es visible en el navegador: aquí no van secretos. */
/* ---------- Autenticación ---------- */
function showLogin() { $("auth-overlay").style.display = "flex"; }
function hideLogin() { $("auth-overlay").style.display = "none"; }
function setUser(user) {
  currentUser = user || null;
  $("user-pill").textContent = user && user.username ? "@" + user.username : "";
  const cs = $("um-country"); if (cs) cs.value = (user && user.country) || "";
  const navAdmin = $("nav-admin");
  // Administración: visible para admins y para traductores/colaboradores (estos solo verán
  // la pestaña "Traducciones"; el resto se ocultan por CSS con body.tr-only).
  if (navAdmin) navAdmin.style.display = (user && (user.is_admin || user.is_translator)) ? "" : "none";
  document.body.classList.toggle("tr-only", !!(user && user.is_translator && !user.is_admin));
  const bell = $("notif-bell"); if (bell) bell.style.display = user ? "inline-flex" : "none";
  if (user) startNotifPolling(); else { clearInterval(_notifTimer); }
}

function switchAuth(which) {
  const isLogin = which === "login";
  $("auth-tab-login").classList.toggle("active", isLogin);
  $("auth-tab-register").classList.toggle("active", !isLogin);
  $("auth-login").style.display = isLogin ? "flex" : "none";
  $("auth-register").style.display = isLogin ? "none" : "flex";
  $("login-error").textContent = ""; $("reg-error").textContent = "";
}

async function loadAuthConfig() {
  try {
    const cfg = await (await fetch("/api/auth/config")).json();
    const open = !!cfg.registration_open;
    $("reg-btn").disabled = !open;                      // botón gris si el registro está cerrado
    $("reg-note").textContent = open ? "" : "El registro está cerrado durante la beta.";
  } catch (e) { /* si falla, el botón se queda como esté */ }
}

async function fetchMe() {
  try { const r = await fetch("/api/auth/me"); if (!r.ok) return null; return await r.json(); }
  catch (e) { return null; }
}

async function doLogin() {
  const username = $("login-user").value.trim(), password = $("login-pass").value;
  $("login-error").textContent = "";
  try {
    const r = await fetch("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password }) });
    const data = await r.json();
    if (!r.ok) { $("login-error").textContent = data.error || data.detail || "No se pudo entrar."; return; }
    onAuthSuccess(data);
  } catch (e) { $("login-error").textContent = "Error de red."; }
}

async function doRegister() {
  if ($("reg-btn").disabled) return;                   // doble seguro: cerrado en beta
  const username = $("reg-user").value.trim(), password = $("reg-pass").value;
  $("reg-error").textContent = "";
  try {
    const r = await fetch("/api/auth/register", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password }) });
    const data = await r.json();
    if (!r.ok) { $("reg-error").textContent = data.error || data.detail || "No se pudo crear la cuenta."; return; }
    onAuthSuccess(data);
  } catch (e) { $("reg-error").textContent = "Error de red."; }
}

function onAuthSuccess(user) {
  $("login-pass").value = ""; $("reg-pass").value = "";
  // Recargar arranca la app desde cero con los permisos del NUEVO usuario y evita heredar la
  // vista/estado del anterior (p. ej. quedarse en el panel de administración). El arranque
  // (10-init) vuelve a leer la sesión y deep-links de la URL.
  window.location.reload();
}

async function doLogout() {
  try { await fetch("/api/auth/logout", { method: "POST" }); } catch (e) {}
  window.location.reload();   // limpia por completo el estado del cliente (vista y datos en memoria)
}
$("logout-btn").addEventListener("click", doLogout);

/* ----- Notificaciones (Fase 6) ----- */
let _notifTimer = null;
function notifRelTime(iso) {
  const t = new Date(iso), now = new Date(), s = Math.floor((now - t) / 1000);
  if (s < 60) return "ahora";
  if (s < 3600) return `hace ${Math.floor(s / 60)} min`;
  if (s < 86400) return `hace ${Math.floor(s / 3600)} h`;
  if (s < 604800) return `hace ${Math.floor(s / 86400)} d`;
  return t.toLocaleDateString();
}
async function refreshUnread() {
  try {
    const r = await getJSON("/api/notifications/unread-count");
    const dot = $("notif-dot"); if (!dot) return;
    if (r && r.unread > 0) { dot.textContent = r.unread > 99 ? "99+" : r.unread; dot.style.display = "inline-flex"; }
    else dot.style.display = "none";
  } catch (e) {}
}
function startNotifPolling() { refreshUnread(); clearInterval(_notifTimer); _notifTimer = setInterval(refreshUnread, 60000); }
async function openNotifications() {
  $("notif-modal").classList.add("open");
  await loadNotifications();
}
function closeNotifications() { $("notif-modal").classList.remove("open"); }
async function loadNotifications() {
  const box = $("notif-list");
  box.innerHTML = `<p class="evd-muted" style="padding:10px">Cargando…</p>`;
  let r;
  try { r = await getJSON("/api/notifications"); } catch (e) { box.innerHTML = `<p class="evd-muted" style="padding:10px">No se pudieron cargar.</p>`; return; }
  const items = (r && r.items) || [];
  if (!items.length) { box.innerHTML = `<p class="evd-muted" style="padding:18px;text-align:center">No tienes notificaciones.</p>`; refreshUnread(); return; }
  box.innerHTML = items.map((n) => {
    const acts = [];
    if (n.type === "player_in_event" && n.event_id) acts.push(`<button class="ghost sm" onclick="notifFollow(${n.event_id}, ${n.id})">Seguir evento</button>`);
    if (n.event_id) acts.push(`<button class="ghost sm" onclick="notifOpenEvent(${n.event_id}, ${n.id})">Ver evento</button>`);
    if (!n.read) acts.push(`<button class="link-btn sm" onclick="markNotifRead(${n.id})">Marcar leída</button>`);
    acts.push(`<button class="link-btn sm danger" onclick="deleteNotif(${n.id})">Eliminar</button>`);
    return `<div class="notif-item${n.read ? "" : " unread"}">
      <div class="notif-head"><span class="notif-title">${esc(n.title || "")}</span><span class="notif-time">${notifRelTime(n.created_at)}</span></div>
      ${n.body ? `<div class="notif-body">${esc(n.body)}</div>` : ""}
      <div class="notif-acts">${acts.join("")}</div></div>`;
  }).join("");
  refreshUnread();
}
async function markNotifRead(nid) { await apiSend(`/api/notifications/${nid}/read`, "POST"); await loadNotifications(); }
async function markAllNotifs() { await apiSend(`/api/notifications/read-all`, "POST"); await loadNotifications(); }
async function deleteNotif(nid) { await apiSend(`/api/notifications/${nid}`, "DELETE"); await loadNotifications(); }
async function deleteAllNotifs() {
  if (!confirm("¿Eliminar todas las notificaciones?")) return;
  await apiSend(`/api/notifications`, "DELETE"); await loadNotifications();
}
async function notifFollow(eid, nid) {
  const { ok } = await apiSend(`/api/events/${eid}/follow`, "POST");
  await apiSend(`/api/notifications/${nid}/read`, "POST");
  if (ok) { wikiToast("Ahora sigues el evento.", "ok"); loadMyEvents && loadMyEvents(); }
  await loadNotifications();
}
async function notifOpenEvent(eid, nid) {
  await apiSend(`/api/notifications/${nid}/read`, "POST");
  closeNotifications(); showSection("leagues"); await openEvent(eid); refreshUnread();
}
function openEventHelp() { $("help-modal").classList.add("open"); }
function closeEventHelp() { $("help-modal").classList.remove("open"); }

