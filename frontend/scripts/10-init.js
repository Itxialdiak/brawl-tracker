/* BrawlSensei · 09-init.js
   arranque de la app (auth + bootApp + sondeo de estado).
   Se carga como <script src> desde index.html, en orden. El JS de cliente
   SIEMPRE es visible en el navegador: aquí no van secretos. */
/* ---------- Arranque ---------- */
async function bootApp() { await loadAssets(); await refreshAll(); }

// Al refrescar, arrancar SIEMPRE arriba del todo (cabecera visible), no donde
// estuviera el scroll. Desactivamos la restauración automática del navegador y
// forzamos el tope (también tras el primer render, por si el layout crece).
if ("scrollRestoration" in history) history.scrollRestoration = "manual";
function scrollToTop() { window.scrollTo(0, 0); }
scrollToTop();
window.addEventListener("load", scrollToTop);

(async () => {
  await loadAuthConfig();
  const me = await fetchMe();
  if (me && me.username) { hideLogin(); exitGuestMode(); setUser(me); bootApp(); checkImportParam(); checkEventParam(); checkUserParam(); }
  else { enterGuestMode(); checkUserParam(); }   // sin cuenta: modo invitado (comunidad pública), sin bloquear la web
  scrollToTop();
})();

setInterval(() => loadStatus().catch(() => {}), 30000);
