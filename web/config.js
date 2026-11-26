/* Alamat server ASR VoiceChess-ID.
 * Default: server ASR di Azure Container Apps (East Asia).
 * Cadangan (server lokal / Cloudflare Tunnel), buka web dengan link:
 *   https://<web-vercel>/?api=https://xxxx.trycloudflare.com
 * Alamat diingat di browser ini; ?api=reset untuk kembali ke server Azure.
 * Demi keamanan hanya localhost dan *.trycloudflare.com yang diterima. */
(function () {
  var DEFAULT = "https://voicechess-asr.gentleriver-ca156cda.eastasia.azurecontainerapps.io";
  var ok = function (u) {
    return /^https:\/\/[a-z0-9-]+\.trycloudflare\.com\/?$/i.test(u) ||
           /^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?\/?$/i.test(u);
  };
  var q = null, saved = null;
  try { q = new URLSearchParams(location.search).get("api"); } catch (e) { /* abaikan */ }
  try {
    if (q === "reset") localStorage.removeItem("vc_api");
    else if (q && ok(q)) localStorage.setItem("vc_api", q);
    saved = localStorage.getItem("vc_api");
  } catch (e) { /* mode privat */ }
  var api = (q && ok(q)) ? q : (saved && ok(saved) ? saved : DEFAULT);
  window.VOICECHESS_API = api;
})();
