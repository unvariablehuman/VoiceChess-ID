# VoiceChess-ID

Main catur dengan perintah suara dalam Bahasa Indonesia. Ucapkan langkah seperti
"Pion E2 ke E4" atau "Kuda G1 ke F3", lalu bidak berpindah di papan.

Alurnya: **mikrofon → Faster-Whisper (ASR) → parser Bahasa Indonesia → papan catur**
(legalitas langkah dicek dengan `python-chess`). Kalau ucapannya kurang jelas,
aplikasi menebak langkah terdekat dan meminta konfirmasi ("ya" / "tidak").

Proyek mata kuliah Speech Recognition.

## Fitur

- Perintah suara Bahasa Indonesia, termasuk ejaan petak yang diucapkan ("e dua", "ef tiga").
- Validasi langkah: langkah ilegal ditolak, langkah yang mirip ditawarkan untuk dikonfirmasi.
- Bisa juga mengetik perintah atau mengklik bidak.
- Versi desktop (Pygame) dan versi web (HTML/CSS/JS + server ASR lokal).
- Versi web: tampilan responsif (laptop, tablet, HP), mode gelap/terang, dan
  Mode 2 Pemain dengan jeda antar giliran.

## Struktur folder

| Folder | Isi |
|---|---|
| `app/` | Aplikasi desktop: papan Pygame (`board_ui.py`), logika permainan (`game_logic.py`), ASR (`asr.py`), parser v2 (`voicechess_parser.py`), titik masuk `main.py` |
| `server/` | Server ASR untuk versi web (Starlette + Faster-Whisper), `requirements.txt`, `Dockerfile` |
| `web/` | Halaman web statis: `index.html`, `style.css`, `app.js`, `audio.js` (deteksi suara), `config.js` (alamat server) |
| `dataset/` | Perekam dataset (`record_dataset.py`) dan daftar perintah (`daftar_perintah.txt`) |
| `evaluasi/` | Skrip penyatu dataset, evaluasi Whisper (WER, akurasi parser), notebook Colab |
| `arsip/` | Versi lama (parser v1, notebook parser awal) |

## Menjalankan

Semua perintah dijalankan dari folder root proyek. Disarankan Python 3.10.

### Aplikasi desktop

```bash
pip install pygame python-chess faster-whisper sounddevice numpy
python app/main.py
```

Opsi:

```bash
python app/asr.py --mics            # lihat daftar mikrofon
python app/main.py --mic 2          # pilih mikrofon
python app/main.py --partial none   # tanpa transkripsi sementara (latency lebih kecil)
```

Di papan: langsung bicara (tanpa tombol), **F2** mematikan/menyalakan mikrofon.
Kotak ketik dan mouse tetap bisa dipakai.

### Versi web (lokal)

Jalankan dua server di dua terminal:

```bash
# Terminal 1: server ASR di http://localhost:7860
pip install -r server/requirements.txt
python server/server.py

# Terminal 2: halaman web di http://localhost:8080
python -m http.server 8080 --directory web
```

Lalu buka **http://localhost:8080** dan izinkan akses mikrofon. Saat pertama kali
dijalankan, model Whisper (`small`) diunduh otomatis; tunggu sampai status menunjukkan
"Server Online".

Alamat server ASR bisa diganti di `web/config.js`. Variabel lingkungan server yang
tersedia: `WHISPER_MODEL`, `WHISPER_DEVICE`, `WHISPER_COMPUTE`, `ALLOWED_ORIGINS`, `PORT`.

### Demo online (Vercel + server di laptop)

Halaman web di Vercel bisa memakai server ASR yang berjalan di laptop, dibuka ke
internet lewat [Cloudflare Quick Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/)
(pasang sekali: `winget install --id Cloudflare.cloudflared`).

1. Nyalakan server ASR: `python server/server.py`
2. Di terminal lain, buka tunnel: `cloudflared tunnel --url http://localhost:7860`
   lalu salin alamat `https://....trycloudflare.com` dari output-nya.
3. Buka `https://<web-vercel>/?api=<alamat trycloudflare>`.
   Alamat itu diingat di browser tersebut; `?api=reset` mengembalikan ke default (localhost).

Catatan:

- Laptop (server dan tunnel) harus tetap menyala selama demo.
- Alamat tunnel berganti setiap kali `cloudflared` dijalankan, jadi link `?api=` perlu diperbarui.
- Kalau perintah `cloudflared` belum dikenali setelah instalasi, tutup lalu buka lagi terminalnya.
- Demi keamanan, `?api=` hanya menerima `localhost` dan alamat `*.trycloudflare.com`.

## Catatan

- **Rekaman suara dan dataset tidak disertakan** di repositori ini karena alasan privasi.
  Begitu juga hasil evaluasi per rekaman dan log. Skrip di `dataset/` dan `evaluasi/`
  tetap ada untuk merekam dan mengevaluasi dataset sendiri.
- Parser di `app/` adalah versi 2, yaitu versi yang dievaluasi (kamus dibekukan
  18-11-2026; perintah undo dimatikan).
- File model Whisper tidak disertakan; diunduh otomatis oleh `faster-whisper`.
