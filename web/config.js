/* Alamat server ASR VoiceChess-ID.
 * Default: server ASR di Azure Container Apps (East Asia).
 * Cadangan lewat ?api= (demi keamanan hanya dua bentuk alamat yang diterima):
 *   https://<web-vercel>/?api=https://xxxx.trycloudflare.com  -> diingat di browser ini
 *   https://<web-vercel>/?api=http://localhost:7860           -> hanya untuk pemuatan ini, tidak diingat
 * Alamat localhost yang tersimpan dari versi lama dihapus otomatis.
 * ?api=reset menghapus alamat yang diingat dan kembali ke server Azure. */
(function () {
  var DEFAULT = "https://voicechess-asr.gentleriver-ca156cda.eastasia.azurecontainerapps.io";
  var isTunnel = function (u) { return /^https:\/\/[a-z0-9-]+\.trycloudflare\.com\/?$/i.test(u); };
  var isLocal = function (u) { return /^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?\/?$/i.test(u); };
  var ok = function (u) { return isTunnel(u) || isLocal(u); };
  var q = null, saved = null;
  try { q = new URLSearchParams(location.search).get("api"); } catch (e) { /* abaikan */ }
  try {
    if (q === "reset") localStorage.removeItem("vc_api");
    else if (q && isTunnel(q)) localStorage.setItem("vc_api", q);   // hanya tunnel yang diingat
    saved = localStorage.getItem("vc_api");
    if (saved && !isTunnel(saved)) { localStorage.removeItem("vc_api"); saved = null; }   // sisa versi lama
  } catch (e) { /* mode privat */ }
  var api = (q && ok(q)) ? q : (saved || DEFAULT);
  window.VOICECHESS_API = api;
})();
