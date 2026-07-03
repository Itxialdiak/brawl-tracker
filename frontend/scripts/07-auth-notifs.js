/* BrawlSensei · 07-auth-notifs.js
   autenticación (login/registro/logout) y notificaciones.
   Se carga como <script src> desde index.html, en orden. El JS de cliente
   SIEMPRE es visible en el navegador: aquí no van secretos. */
/* ---------- Autenticación ---------- */
function showLogin() { $("auth-overlay").style.display = "flex"; }
function hideLogin() { $("auth-overlay").style.display = "none"; }
function setUser(user) {
  currentUser = user || null;
  renderUserSwitch();
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

/* ---------- Menú de usuario (desplegable estilo idioma: Cuenta/Amigos/Mensajes/Salir) ---------- */
function renderUserSwitch() {
  const box = $("user-switch");
  if (!box) return;
  if (!currentUser) { box.innerHTML = ""; return; }
  box.innerHTML = `
    <button class="user-toggle" id="user-toggle" onclick="toggleUserMenu(event)" title="Tu cuenta">
      <span class="user-ava">@${esc(currentUser.username || "")}</span>
      <span class="user-fr-dot" id="user-fr-dot" style="display:none"></span>
      <span class="lang-caret">▾</span>
    </button>
    <div class="user-menu" id="user-menu">
      <button class="user-menu-opt" onclick="userMenu('cuenta')">⚙️ Cuenta</button>
      <button class="user-menu-opt" onclick="userMenu('amigos')">👥 Amigos <span class="user-menu-badge" id="user-fr-badge" style="display:none"></span></button>
      <button class="user-menu-opt" onclick="userMenu('mensajes')">✉️ Mensajes</button>
      <button class="user-menu-opt danger" onclick="userMenu('salir')">🚪 Cerrar sesión</button>
    </div>`;
  refreshFriendsBadge();
}
function toggleUserMenu(e) {
  e.stopPropagation();
  const m = $("user-menu"), t = $("user-toggle");
  const open = m && m.classList.toggle("open");
  if (t) t.classList.toggle("open", !!open);
}
function closeUserMenu() {
  const m = $("user-menu"), t = $("user-toggle");
  if (m) m.classList.remove("open"); if (t) t.classList.remove("open");
}
document.addEventListener("click", (e) => {
  const sw = document.getElementById("user-switch");
  if (sw && !sw.contains(e.target)) closeUserMenu();
});
function userMenu(which) {
  closeUserMenu();
  if (which === "cuenta") openAccount();
  else if (which === "amigos") openFriends();
  else if (which === "mensajes") openMessages();
  else if (which === "salir") doLogout();
}
async function refreshFriendsBadge() {
  if (!currentUser) return;
  try {
    const r = await getJSON("/api/friends/count");
    const n = (r && r.incoming) || 0;
    const b = $("user-fr-badge"), dot = $("user-fr-dot");
    if (b) { if (n > 0) { b.textContent = n; b.style.display = "inline-flex"; } else b.style.display = "none"; }
    if (dot) dot.style.display = n > 0 ? "inline-block" : "none";
  } catch (e) { /* 401 */ }
}

/* ---------- Amigos ---------- */
function openFriends() {
  $("friends-modal").classList.add("open");
  $("fr-search").value = ""; $("fr-results").innerHTML = "";
  loadFriends();
}
function closeFriends() { $("friends-modal").classList.remove("open"); }
async function loadFriends() {
  const box = $("fr-body");
  box.innerHTML = `<p class="evd-muted" style="padding:10px">Cargando…</p>`;
  let r;
  try { r = await getJSON("/api/friends"); } catch (e) { box.innerHTML = `<p class="evd-muted" style="padding:10px">No se pudo cargar.</p>`; return; }
  const inc = r.incoming || [], out = r.outgoing || [], fr = r.friends || [];
  let html = "";
  if (inc.length) {
    html += `<div class="fr-sec-h">Solicitudes recibidas <span class="reto-count">${inc.length}</span></div>`;
    html += inc.map((u) => `<div class="fr-row"><button class="fr-name fr-link" onclick="openPublicProfile(${u.id})" title="Ver perfil público">@${esc(u.username)}</button><span class="fr-acts"><button class="mini-ok" onclick="acceptFriend(${u.req_id})">Aceptar</button><button class="mini-no" onclick="rejectFriend(${u.req_id})">Rechazar</button></span></div>`).join("");
  }
  if (out.length) {
    html += `<div class="fr-sec-h">Enviadas</div>`;
    html += out.map((u) => `<div class="fr-row"><button class="fr-name fr-link" onclick="openPublicProfile(${u.id})" title="Ver perfil público">@${esc(u.username)}</button><span class="fr-acts"><span class="evd-muted">pendiente</span><button class="link-btn sm danger" onclick="rejectFriend(${u.req_id})">Cancelar</button></span></div>`).join("");
  }
  _friendsCache = fr;
  html += `<div class="fr-sec-h">Tus amigos <span class="reto-count">${fr.length}</span></div>`;
  html += fr.length
    ? fr.map((u) => `<div class="fr-row"><button class="fr-name fr-link" onclick="openPublicProfile(${u.id})" title="Ver perfil público">@${esc(u.username)}</button><span class="fr-acts"><button class="link-btn sm danger" onclick="removeFriend(${u.id})">Quitar</button></span></div>`).join("")
    : `<p class="evd-muted" style="padding:8px 2px">Aún no tienes amigos. Búscalos arriba por su nombre.</p>`;
  box.innerHTML = html;
  refreshFriendsBadge();
}
let _friendsCache = [];
let _frSearchT = null;
function friendsSearch() { clearTimeout(_frSearchT); _frSearchT = setTimeout(doFriendsSearch, 250); }
async function doFriendsSearch() {
  const q = $("fr-search").value.trim();
  const box = $("fr-results");
  if (!q) { box.innerHTML = ""; return; }
  let r;
  try { r = await getJSON("/api/friends/search?q=" + encodeURIComponent(q)); } catch (e) { return; }
  const us = r.users || [];
  if (!us.length) { box.innerHTML = `<div class="fr-res-empty">Sin resultados</div>`; return; }
  box.innerHTML = us.map((u) => {
    let act;
    if (u.relation === "friend") act = `<span class="evd-muted">ya sois amigos</span>`;
    else if (u.relation === "outgoing") act = `<span class="evd-muted">solicitud enviada</span>`;
    // "incoming": ya te envió solicitud; enviar la tuya la acepta mutuamente (auto-accept en backend).
    else if (u.relation === "incoming") act = `<button class="mini-ok" onclick="reqFriendId(${u.id})">Aceptar</button>`;
    else act = `<button class="mini-ok" onclick="reqFriendId(${u.id})">+ Añadir</button>`;
    return `<div class="fr-res-row"><button class="fr-name fr-link" onclick="openPublicProfile(${u.id})" title="Ver perfil público">@${esc(u.username)}</button>${act}</div>`;
  }).join("");
}
async function reqFriendId(uid) {
  const { ok, d } = await apiSend("/api/friends/request", "POST", { user_id: uid });
  if (!ok) { wikiToast(d.error || d.detail || "No se pudo enviar la solicitud", "err"); return; }
  wikiToast(d.status === "friends" ? "¡Ya sois amigos!" : d.status === "exists" ? "Ya habías enviado la solicitud" : "Solicitud enviada", "ok");
  doFriendsSearch(); loadFriends();
}
async function acceptFriend(rid) {
  const { ok, d } = await apiSend(`/api/friends/requests/${rid}/accept`, "POST");
  if (!ok) { wikiToast(d.error || "No se pudo aceptar", "err"); return; }
  wikiToast("Solicitud aceptada", "ok"); loadFriends();
}
async function rejectFriend(rid) { const { ok } = await apiSend(`/api/friends/requests/${rid}/reject`, "POST"); if (ok) loadFriends(); }
async function removeFriend(uid) {
  const u = _friendsCache.find((x) => x.id === uid);
  if (!confirm(`¿Quitar a @${u ? u.username : "este usuario"} de tus amigos?`)) return;
  await apiSend(`/api/friends/${uid}`, "DELETE"); loadFriends();
}

/* ---------- Mensajes (placeholder; fase posterior) ---------- */
function openMessages() { $("messages-modal").classList.add("open"); }
function closeMessages() { $("messages-modal").classList.remove("open"); }

/* ---------- Perfil público (fase C): vista de solo lectura de un usuario ---------- */
let _pubProfile = null, _pubTag = null;
async function openPublicProfile(uid) {
  $("pubprofile-modal").classList.add("open");
  $("pub-body").innerHTML = `<p class="evd-muted" style="padding:20px">Cargando…</p>`;
  let d;
  try { d = await getJSON(`/api/users/${uid}/profile`); } catch (e) { $("pub-body").innerHTML = `<p class="evd-muted" style="padding:20px">No se pudo cargar el perfil.</p>`; return; }
  if (d.error) { $("pub-body").innerHTML = `<p class="evd-muted" style="padding:20px">${esc(d.error)}</p>`; return; }
  _pubProfile = d;
  _pubTag = (d.players && d.players[0]) ? d.players[0].tag : null;
  renderPublicProfile();
  if (_pubTag) loadPubSummary(_pubTag);
}
function closePublicProfile() { $("pubprofile-modal").classList.remove("open"); }
function renderPublicProfile() {
  const d = _pubProfile;
  const rel = d.relation;
  let acts = "";
  if (rel === "none") acts = `<button class="btn mini-btn" onclick="pubAddFriend(${d.id})">＋ Enviar solicitud</button>`;
  else if (rel === "outgoing") acts = `<span class="evd-muted">Solicitud enviada</span>`;
  else if (rel === "incoming") acts = `<button class="btn mini-btn" onclick="pubAddFriend(${d.id})">Aceptar solicitud</button>`;
  else if (rel === "friend") acts = `<span class="reto-tag done">✓ Amigos</span>`;
  if (rel !== "self") acts += `<button class="ghost mini-btn" onclick="pubMessage(${d.id})">✉️ Enviar mensaje</button>`;
  const players = d.players || [];
  const picker = players.length > 1
    ? `<div class="pub-players">${players.map((p) => `<button class="pub-player-chip ${p.tag === _pubTag ? "active" : ""}" onclick="pubSelectPlayer('${esc(p.tag)}')">${esc(p.name || p.tag)}</button>`).join("")}</div>`
    : "";
  $("pub-body").innerHTML = `
    <div class="pub-head"><div class="pub-name">@${esc(d.username)}</div><div class="pub-acts">${acts}</div></div>
    ${players.length ? picker + `<div id="pub-summary"></div>` : `<p class="evd-muted" style="padding:14px 2px">Este usuario no tiene jugadores registrados.</p>`}`;
}
function pubSelectPlayer(tag) { _pubTag = tag; renderPublicProfile(); loadPubSummary(tag); }
async function pubAddFriend(uid) {
  const { ok, d } = await apiSend("/api/friends/request", "POST", { user_id: uid });
  if (!ok) { wikiToast(d.error || "No se pudo", "err"); return; }
  wikiToast(d.status === "friends" ? "¡Ya sois amigos!" : "Solicitud enviada", "ok");
  openPublicProfile(uid);   // recarga la relación
}
function pubMessage(uid) { openMessages(); }   // fase E: aquí abrirá el compositor al usuario
async function loadPubSummary(tag) {
  const box = $("pub-summary");
  if (!box) return;
  box.innerHTML = `<p class="evd-muted" style="padding:14px">Cargando analíticas…</p>`;
  let s;
  try { s = await getJSON(`/api/users/${_pubProfile.id}/players/${encodeURIComponent(tag)}/summary`); }
  catch (e) { box.innerHTML = `<p class="evd-muted" style="padding:14px">Sin datos de este jugador.</p>`; return; }
  if (s.error) { box.innerHTML = `<p class="evd-muted" style="padding:14px">${esc(s.error)}</p>`; return; }
  const r = s.report || {}, ov = r.overview || {}, hl = r.highlights || {}, rt = s.rating || {};
  const stat = (k, v, sub) => `<div class="pub-stat"><div class="k">${esc(k)}</div><div class="v">${v}</div>${sub ? `<div class="sub">${esc(sub)}</div>` : ""}</div>`;
  const line1 = `<div class="pub-line pub-overview">
    ${stat("Win rate", (ov.winrate != null ? ov.winrate + "%" : "—"), `${ov.wins || 0}V · ${ov.losses || 0}D`)}
    ${stat("Partidas", ov.total || 0, "registradas")}
    ${stat("Jugador estelar", (ov.star_rate != null ? ov.star_rate + "%" : "—"), `${ov.star_players || 0} MVP`)}
    ${stat("Balance trofeos", (ov.trophy_delta >= 0 ? "+" : "") + (ov.trophy_delta || 0), "acumulado")}</div>`;
  const hlName = (it, kind) => {
    const v = it.label || it.name || it.brawler || "—";
    if (kind === "mode" && typeof modeName === "function") return modeName(v);
    if (kind === "map" && typeof mapNameEs === "function") return mapNameEs(v);
    return v;
  };
  const hlItem = (lab, it, kind, val) => it ? `<div class="pub-hl"><div class="lab">${esc(lab)}</div><div class="nm">${esc(hlName(it, kind))}</div><div class="val">${esc(val)}</div></div>` : "";
  const line2 = `<div class="pub-line pub-hls">
    ${hlItem("Más jugado", hl.most_played, "brawler", hl.most_played ? hl.most_played.total + "p" : "")}
    ${hlItem("Mejor brawler", hl.best_brawler, "brawler", hl.best_brawler ? hl.best_brawler.winrate + "%" : "")}
    ${hlItem("Mejor modo", hl.best_mode, "mode", hl.best_mode ? hl.best_mode.winrate + "%" : "")}
    ${hlItem("Mejor mapa", hl.best_map, "map", hl.best_map ? hl.best_map.winrate + "%" : "")}</div>`;
  const subBar = (lab, v) => `<div class="pub-sub"><div class="l">${esc(lab)} <b>${Math.round(v || 0)}</b></div><div class="pub-subbar"><span style="width:${Math.max(0, Math.min(100, v || 0))}%"></span></div></div>`;
  const line3 = rt.overall != null ? `<div class="pub-line pub-rating">
    <div class="pub-score"><span class="num">${Math.round(rt.overall)}</span><span class="max">/100</span><span class="tier">${esc(rt.tier || "")}</span></div>
    <div class="pub-subs">${subBar("Colección", rt.collection)}${subBar("Maestría", rt.mastery)}${subBar("Eficiencia", rt.efficiency)}${subBar("Pushing", rt.pushing)}</div></div>` : "";
  const chart = `<div class="pub-chart"><h4>Evolución de trofeos</h4>${trophyChart(r.trophy_series || [])}</div>`;
  box.innerHTML = line1 + line2 + line3 + chart;
}

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
function startNotifPolling() {
  refreshUnread(); refreshFriendsBadge();
  clearInterval(_notifTimer);
  _notifTimer = setInterval(() => { refreshUnread(); refreshFriendsBadge(); }, 60000);
}
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

