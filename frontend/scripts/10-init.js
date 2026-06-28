/* BrawlSensei · 09-init.js
   arranque de la app (auth + bootApp + sondeo de estado).
   Se carga como <script src> desde index.html, en orden. El JS de cliente
   SIEMPRE es visible en el navegador: aquí no van secretos. */
/* ---------- Arranque ---------- */
async function bootApp() { await loadAssets(); await refreshAll(); }

(async () => {
  await loadAuthConfig();
  const me = await fetchMe();
  if (me && me.username) { hideLogin(); setUser(me); bootApp(); checkImportParam(); checkEventParam(); }
  else { showLogin(); }
})();

setInterval(() => loadStatus().catch(() => {}), 30000);
