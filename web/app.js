/* VoiceChess-ID - tampilan web. Semua aturan catur & pemahaman perintah ada di server. */
(function () {
  const API = (window.VOICECHESS_API || "http://localhost:7860").replace(/\/+$/, "");
  const $ = (id) => document.getElementById(id);
  const GLYPH = { k: "♚", q: "♛", r: "♜", b: "♝", n: "♞", p: "♟" };
  const START_COUNT = { p: 8, n: 2, b: 2, r: 2, q: 1 };
  const FILES = "abcdefgh";

  let state = null;
  let selected = null;
  let flipped = false;
  let session = null;
  let prevHistLen = 0;
  try { session = localStorage.getItem("vc_session"); } catch (e) { /* mode privat */ }

  // ------------------------------------------------------------ server ----
  async function post(path, body) {
    const res = await fetch(API + path, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({ session }, body || {})),
    });
    if (!res.ok) throw new Error(`Server ${res.status}`);
    return res.json();
  }

  /** source: "load" (awal/papan baru) atau "action" (perintah/klik/suara) */
  function apply(st, source) {
    const grew = st.history.length > prevHistLen;
    state = st;
    session = st.session;
    try { localStorage.setItem("vc_session", session); } catch (e) { /* abaikan */ }
    selected = null;
    if (source === "load") setParse("-", null);
    else if (st.pending) setParse(arrow(st.pending), "ask");
    else if (grew && st.last_move) setParse(arrow(st.last_move), "ok");
    else setParse("-", "bad");
    prevHistLen = st.history.length;
    // Mode 2 Pemain: jeda suara HANYA setelah langkah benar-benar dijalankan (riwayat bertambah).
    // Konfirmasi Ya/Tidak, langkah tidak sah, atau suara yang diabaikan tidak menambah riwayat.
    if (source === "load") clearPause();
    else if (grew && twoPlayer && mic && !st.game_over) setPause();
    render();
  }

  async function act(path, body, source = "action") {
    try { apply(await post(path, body), source); }
    catch (e) { $("message").textContent = "Tidak bisa menghubungi server: " + e.message; setServer("off"); }
  }

  function setServer(mode, text) {
    const pill = $("serverPill");
    const cls = "pill" + (mode === "on" ? " on" : mode === "off" ? " off" : "");
    const label = text || (mode === "on" ? "Server Online" : mode === "off" ? "Server Offline" : "Memuat model...");
    pill.className = cls; $("serverText").textContent = label;
    $("lpPill").className = cls; $("lpText").textContent = label;   // status yang sama di landing
  }

  async function checkHealth() {
    try {
      const h = await (await fetch(API + "/health")).json();
      if (h.model_error) { setServer("off", "Model gagal dimuat"); return; }
      if (h.model_ready) { setServer("on", `Server Online · Whisper ${h.model}`); }
      else { setServer("wait", `Memuat Whisper ${h.model}...`); setTimeout(checkHealth, 3000); }
    } catch (e) {
      setServer("off");
      setTimeout(checkHealth, 5000);
    }
  }

  // ------------------------------------------------------------ papan ----
  const arrow = (pair) => `${pair[0]} → ${pair[1]}`;
  // ikon Lucide: circle-check, circle-help, circle-x
  const lucide = (inner) => `<svg class="i" viewBox="0 0 24 24" aria-hidden="true">${inner}</svg>`;
  const VERDICT_ICON = {
    ok: lucide('<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>'),
    ask: lucide('<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><path d="M12 17h.01"/>'),
    bad: lucide('<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/>'),
  };

  function setParse(text, kind) {
    $("parseMove").textContent = text;
    const v = $("parseVerdict");
    const label = { ok: "Valid", ask: "Konfirmasi?", bad: "Tidak valid" }[kind];
    v.hidden = !label;
    if (label) { v.innerHTML = VERDICT_ICON[kind] + label; v.className = "verdict " + kind; }
  }

  function fenSquares(fen) {
    const map = {};
    fen.split(" ")[0].split("/").forEach((row, i) => {
      let f = 0;
      for (const ch of row) {
        if (/\d/.test(ch)) { f += +ch; continue; }
        map[FILES[f] + (8 - i)] = ch;
        f++;
      }
    });
    return map;
  }

  function capturedPieces(pieces) {
    const have = {};
    Object.values(pieces).forEach((p) => { have[p] = (have[p] || 0) + 1; });
    const out = [];
    for (const color of ["w", "b"]) {                      // bidak putih yang hilang, lalu hitam
      for (const t of ["q", "r", "b", "n", "p"]) {
        const sym = color === "w" ? t.toUpperCase() : t;
        const lost = Math.max(0, START_COUNT[t] - (have[sym] || 0));
        for (let i = 0; i < lost; i++) out.push({ g: GLYPH[t], color });
      }
    }
    return out;
  }

  function render() {
    if (!state) return;
    const pieces = fenSquares(state.fen);
    const board = $("board");
    board.innerHTML = "";
    const targets = selected ? (state.legal[selected] || []) : [];
    for (let r = 0; r < 8; r++) {
      for (let c = 0; c < 8; c++) {
        const file = flipped ? FILES[7 - c] : FILES[c];
        const rank = flipped ? r + 1 : 8 - r;
        const name = file + rank;
        const sq = document.createElement("div");
        sq.className = "sq " + ((FILES.indexOf(file) + rank) % 2 === 1 ? "l" : "d");
        if (state.last_move && state.last_move.includes(name)) sq.classList.add("last");
        if (state.pending && state.pending.includes(name)) sq.classList.add("pend");
        if (state.check === name) sq.classList.add("check");
        if (selected === name) sq.classList.add("sel");
        const p = pieces[name];
        if (p) {
          const s = document.createElement("span");
          s.className = "pc " + (p === p.toUpperCase() ? "w" : "b");
          s.textContent = GLYPH[p.toLowerCase()] + "︎";   // paksa simbol teks, bukan emoji
          sq.appendChild(s);
        }
        if (targets.includes(name)) {
          const d = document.createElement("span"); d.className = "dot"; sq.appendChild(d);
        }
        sq.addEventListener("click", () => onSquare(name, p));
        board.appendChild(sq);
      }
    }
    const files = flipped ? [...FILES].reverse() : [...FILES];
    const ranks = flipped ? [1, 2, 3, 4, 5, 6, 7, 8] : [8, 7, 6, 5, 4, 3, 2, 1];
    $("files").innerHTML = files.map((f) => `<span>${f}</span>`).join("");
    $("ranks").innerHTML = ranks.map((r) => `<span>${r}</span>`).join("");

    $("turnText").textContent = "Giliran " + state.turn_text;
    $("turnDot").className = "turn-dot" + (state.turn === "b" ? " b" : "");
    if (!liveText) $("transcript").textContent = state.transcript ? `"${state.transcript}"` : "-";
    $("message").textContent = state.message || "-";
    $("confirmRow").hidden = !state.pending;

    const hist = $("history");
    hist.innerHTML = "";
    for (let i = 0; i < state.history.length; i += 2) {
      const li = document.createElement("li");
      li.textContent = state.history[i] + (state.history[i + 1] ? "  ·  " + state.history[i + 1] : "");
      hist.appendChild(li);
    }
    if (!state.history.length) hist.innerHTML = "<li class='muted'>Belum ada langkah</li>";

    const cap = capturedPieces(pieces);
    $("captured").innerHTML = cap.length
      ? cap.map((x) => `<span class="pc-${x.color}">${x.g}︎</span>`).join("") : "-";
    const n = state.history.length;
    $("lastChip").textContent = state.last_move ? `${Math.ceil(n / 2)}. ${arrow(state.last_move)}` : "-";
  }

  function onSquare(name, piece) {
    if (!state || state.game_over) return;
    const mine = piece && ((piece === piece.toUpperCase()) === (state.turn === "w"));
    if (selected && (state.legal[selected] || []).includes(name)) {
      act("/api/move", { from: selected, to: name });
    } else if (mine) {
      selected = selected === name ? null : name;
      render();
    } else {
      selected = null;
      render();
    }
  }

  // --------------------------------------------------------- mikrofon ----
  let mic = null;          // {stream, ctx, proc, src}
  let liveText = false;
  let queue = Promise.resolve();

  const MIC_UI = {
    off:   { badge: "Mati",    cls: "",          status: "Mikrofon mati",
             hint: 'Tekan "Mulai Bicara", lalu ucapkan mis. "Pion E2 ke E4"' },
    ready: { badge: "Ready",   cls: "listening", status: "Siap mendengarkan...",
             hint: "Ucapkan langkah catur dalam Bahasa Indonesia" },
    rec:   { badge: "Merekam", cls: "recording", status: "Merekam...", hint: "Selesaikan kalimat, lalu diam sebentar" },
    busy:  { badge: "Proses",  cls: "listening", status: "Memproses suara...", hint: "Whisper sedang mentranskripsi" },
    paused: { badge: "Jeda",   cls: "paused",    status: "Mikrofon dijeda", hint: "" },
  };

  function micState(mode, hint) {
    if (paused && mic && mode !== "off") { mode = "paused"; hint = null; }   // jeda menang atas status lain
    const u = MIC_UI[mode];
    const b = $("micBadge");
    b.textContent = u.badge;
    b.className = "badge" + ({ ready: " ready", rec: " rec", busy: " busy", paused: " paused" }[mode] || "");
    $("voiceCard").className = "card voice " + u.cls;
    $("micStatus").textContent = u.status;
    $("micHint").textContent = hint || u.hint;
  }

  async function startMic() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      micState("off", "Browser ini tidak mendukung mikrofon (butuh HTTPS atau localhost).");
      return;
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    } catch (e) {
      micState("off", "Izin mikrofon ditolak: " + e.message);
      return;
    }
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const src = ctx.createMediaStreamSource(stream);
    const proc = ctx.createScriptProcessor(4096, 1, 1);
    const rs = new VCAudio.Resampler(ctx.sampleRate);
    // ep & pending disimpan di objek mic supaya bisa diganti baru saat jeda dilepas
    const m = { stream, ctx, proc, src, ep: new VCAudio.Endpointer(), pending: new Float32Array(0) };
    proc.onaudioprocess = (e) => {
      if (paused) { m.pending = new Float32Array(0); return; }   // jeda: stream tetap terbuka, audio dibuang
      m.pending = VCAudio.concat([m.pending, rs.process(new Float32Array(e.inputBuffer.getChannelData(0)))]);
      while (m.pending.length >= VCAudio.BLOCK) {
        const r = m.ep.feed(m.pending.subarray(0, VCAudio.BLOCK));
        m.pending = m.pending.slice(VCAudio.BLOCK);
        if (r === "start") { micState("rec"); showLive("(mendengarkan...)"); }
        else if (r && r.audio) {
          if (r.audio.length / VCAudio.SR < VCAudio.MIN_UTTER_S) { micState("ready"); endLive(); continue; }
          sendVoice(r.audio);
        }
      }
    };
    src.connect(proc);
    proc.connect(ctx.destination);       // perlu agar onaudioprocess berjalan (keluarannya hening)
    mic = m;
    $("micBtnText").textContent = "Berhenti";
    $("micBtn").classList.add("on");
    micState("ready");
  }

  function stopMic() {
    if (!mic) return;
    clearPause();
    mic.proc.disconnect(); mic.src.disconnect();
    mic.stream.getTracks().forEach((t) => t.stop());
    mic.ctx.close();
    mic = null;
    $("micBtnText").textContent = "Mulai Bicara";
    $("micBtn").classList.remove("on");
    micState("off");
    if (liveText) endLive();
  }

  function showLive(t) { liveText = true; $("transcript").textContent = t; $("transcript").classList.add("live"); }
  function endLive() { liveText = false; $("transcript").classList.remove("live"); if (state) render(); }

  function sendVoice(audio) {
    if (paused) return;
    const epoch = pauseEpoch;
    micState("busy");
    showLive("(memproses suara...)");
    const wav = VCAudio.encodeWav(audio);
    queue = queue.then(async () => {
      // Rekaman yang masih antre saat jeda dipasang (mis. ucapan susulan pemain yang baru jalan) dibuang.
      if (paused || epoch !== pauseEpoch) {
        liveText = false; $("transcript").classList.remove("live");
        if (state) render();
        micState(mic ? "ready" : "off");
        return;
      }
      const fd = new FormData();
      fd.append("audio", new Blob([wav], { type: "audio/wav" }), "perintah.wav");
      if (session) fd.append("session", session);
      try {
        const res = await fetch(API + "/api/voice", { method: "POST", body: fd });
        const out = await res.json();
        liveText = false; $("transcript").classList.remove("live");
        if (!res.ok) { micState(mic ? "ready" : "off", out.error || `Server ${res.status}`); return; }
        if (out.ignored || !out.heard) {
          state = out; render();                       // papan tidak berubah
          $("transcript").textContent = out.heard ? `"${out.heard}"` : "-";
          micState(mic ? "ready" : "off", out.ignored ? "Suara barusan bukan perintah, diabaikan."
                                                      : "Tidak terdengar jelas, ulangi.");
          return;
        }
        out.transcript = out.heard;                     // tampilkan persis yang didengar Whisper
        apply(out, "action");
        micState(mic ? "ready" : "off", `Whisper ${out.whisper_s} dtk untuk audio ${out.audio_s} dtk`);
      } catch (e) {
        liveText = false; $("transcript").classList.remove("live");
        micState(mic ? "ready" : "off", "Gagal mengirim suara: " + e.message);
        setServer("off");
      }
    });
  }

  // --------------------------------------------------- mode 2 pemain ----
  let twoPlayer = false;
  let autoFlip = false;
  let paused = false;
  let pauseEpoch = 0;      // naik tiap jeda dipasang; rekaman dari epoch lama tidak dikirim
  try {
    twoPlayer = localStorage.getItem("vc_two_player") === "1";
    autoFlip = localStorage.getItem("vc_auto_flip") === "1";
  } catch (e) { /* mode privat */ }

  function updatePauseUI() {
    $("handoff").hidden = !paused;
    $("micHint").hidden = paused;
    if (paused && state) {
      const who = state.turn === "b" ? "Hitam" : "Putih";
      $("handoffTurn").textContent = "Giliran " + who;
      $("readyBtn").textContent = who + " Siap";
    }
  }

  /** Pasang jeda: suara tidak diproses sampai pemain berikutnya menekan Siap / Spasi. */
  function setPause() {
    paused = true;
    pauseEpoch++;
    liveText = false; $("transcript").classList.remove("live");
    updatePauseUI();
    micState("ready");
  }

  /** Lepas jeda tanpa efek lain (Papan Baru, mikrofon dimatikan, mode dimatikan). */
  function clearPause() {
    if (!paused) return;
    paused = false;
    if (mic) { mic.ep = new VCAudio.Endpointer(); mic.pending = new Float32Array(0); }  // buang potongan audio lama
    updatePauseUI();
    micState(mic ? "ready" : "off");
  }

  /** Pemain berikutnya siap (tombol Siap atau Spasi). */
  function resume() {
    if (!paused) return;
    clearPause();
    if (twoPlayer && autoFlip && state) { flipped = state.turn === "b"; render(); }
  }

  function syncOpts() {
    $("twoPlayer").checked = twoPlayer;
    $("autoFlip").checked = autoFlip;
    $("autoFlip").disabled = !twoPlayer;
    $("autoFlipWrap").classList.toggle("off", !twoPlayer);
  }

  $("twoPlayer").addEventListener("change", (e) => {
    twoPlayer = e.target.checked;
    try { localStorage.setItem("vc_two_player", twoPlayer ? "1" : "0"); } catch (err) { /* abaikan */ }
    if (!twoPlayer) clearPause();
    syncOpts();
  });
  $("autoFlip").addEventListener("change", (e) => {
    autoFlip = e.target.checked;
    try { localStorage.setItem("vc_auto_flip", autoFlip ? "1" : "0"); } catch (err) { /* abaikan */ }
  });
  $("readyBtn").addEventListener("click", resume);
  document.addEventListener("keydown", (e) => {
    if (!paused || e.code !== "Space" || e.repeat) return;
    const t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
    if ($("histDialog").open) return;
    e.preventDefault();      // cegah Spasi ikut "mengklik" tombol yang sedang fokus
    resume();
  });
  syncOpts();

  // ------------------------------------------------------------- tema ----
  // Tema awal sudah dipasang skrip di <head>. Ikut sistem sampai pengguna memilih manual.
  const themeBtns = document.querySelectorAll(".theme-btn");   // di header aplikasi & di landing
  function setTheme(t) {
    document.documentElement.dataset.theme = t;
    themeBtns.forEach((b) => b.setAttribute("aria-label", t === "light" ? "Ganti ke mode gelap" : "Ganti ke mode terang"));
  }
  setTheme(document.documentElement.dataset.theme === "light" ? "light" : "dark");
  themeBtns.forEach((b) => b.addEventListener("click", () => {
    const t = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    setTheme(t);
    try { localStorage.setItem("vc_theme", t); } catch (e) { /* abaikan */ }
  }));

  // ---------------------------------------------------------- landing ----
  // Tampil setiap kali halaman dibuka; aplikasi di belakangnya "inert" sampai pemain menekan Mulai.
  function enterApp() {
    const lp = $("landing");
    if (lp.classList.contains("gone")) return;
    lp.classList.add("gone");
    document.body.classList.remove("landing-open");
    document.querySelectorAll(".app-part").forEach((el) => { el.inert = false; });
    $("micBtn").focus({ preventScroll: true });
    setTimeout(() => { if (lp.classList.contains("gone")) lp.hidden = true; }, 350);   // setelah animasi pudar
  }
  // Klik logo di header: kembali ke landing. Mikrofon dimatikan, posisi papan tetap tersimpan.
  function showLanding() {
    const lp = $("landing");
    if (mic) stopMic();
    if ($("histDialog").open) $("histDialog").close();
    document.querySelectorAll(".app-part").forEach((el) => { el.inert = true; });
    document.body.classList.add("landing-open");
    lp.hidden = false;
    void lp.offsetWidth;                 // mulai dari transparan supaya animasi pudar jalan
    lp.classList.remove("gone");
    $("playBtn").focus({ preventScroll: true });
  }
  $("homeLink").addEventListener("click", (e) => { e.preventDefault(); showLanding(); });
  $("playBtn").addEventListener("click", enterApp);
  $("playBtn").focus({ preventScroll: true });
  const sysLight = window.matchMedia("(prefers-color-scheme: light)");
  sysLight.addEventListener("change", (e) => {
    let saved = null;
    try { saved = localStorage.getItem("vc_theme"); } catch (err) { /* abaikan */ }
    if (!saved) setTheme(e.matches ? "light" : "dark");
  });

  // ------------------------------------------------------------ tombol ----
  const toggleMic = () => (mic ? stopMic() : startMic());
  $("micBtn").addEventListener("click", toggleMic);
  $("orbBtn").addEventListener("click", toggleMic);
  $("yesBtn").addEventListener("click", () => act("/api/confirm", { yes: true }));
  $("noBtn").addEventListener("click", () => act("/api/confirm", { yes: false }));
  $("newBtn").addEventListener("click", () => { prevHistLen = 0; act("/api/new", {}, "load"); });
  $("flipBtn").addEventListener("click", () => { flipped = !flipped; render(); });
  $("histBtn").addEventListener("click", () => $("histDialog").showModal());
  $("histClose").addEventListener("click", () => $("histDialog").close());
  $("typeForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const t = $("typeInput").value.trim();
    if (t) act("/api/command", { text: t });
    $("typeInput").value = "";
  });

  checkHealth();
  post("/api/state").then((st) => { prevHistLen = st.history.length; apply(st, "load"); })
    .catch((e) => { $("message").textContent = "Tidak bisa menghubungi server: " + e.message; setServer("off"); });
})();
