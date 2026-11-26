# VoiceChess-ID

Main catur dengan perintah suara dalam Bahasa Indonesia. Ucapkan langkah seperti
"Pion E2 ke E4" atau "Kuda G1 ke F3", lalu bidak berpindah di papan.

**Coba langsung:** https://voicechess-id-web.vercel.app

Alurnya: **mikrofon → Faster-Whisper (ASR) → parser Bahasa Indonesia → papan catur**
(legalitas langkah dicek dengan `python-chess`). Kalau ucapannya kurang jelas,
aplikasi menebak langkah terdekat dan meminta konfirmasi ("ya" / "tidak").

Proyek mata kuliah Speech Recognition.

## Fitur

- Perintah suara Bahasa Indonesia, termasuk ejaan petak yang diucapkan ("e dua", "ef tiga").
- Validasi langkah: langkah ilegal ditolak, langkah yang mirip ditawarkan untuk dikonfirmasi.
- Bisa juga mengetik perintah atau mengklik bidak.
- Versi desktop (Pygame) dan versi web (HTML/CSS/JS + server ASR).
- Versi web: tampilan responsif (laptop, tablet, HP), mode gelap/terang, dan
  Mode 2 Pemain dengan jeda antar giliran.

## Arsitektur versi web

```
Browser (mikrofon, deteksi awal/akhir bicara, WAV 16 kHz)
   │  HTTPS
   ▼
Halaman web statis ── Vercel (folder web/)
   │  fetch ke alamat di web/config.js
   ▼
Server ASR ── Azure Container Apps (region East Asia)
   Starlette + Faster-Whisper small (CPU, int8) + parser v2 + python-chess
```

- Image Docker server dibangun otomatis oleh **GitHub Actions**
  (`.github/workflows/server-image.yml`) dari `server/Dockerfile` setiap ada perubahan
  di `app/` atau `server/`, lalu disimpan di `ghcr.io/unvariablehuman/voicechess-asr`.
- Azure Container Apps menjalankan image itu (2 vCPU, 4 GB RAM, 0–1 replika).
- Posisi papan disimpan di memori server per tab browser.

## Struktur folder

| Folder | Isi |
|---|---|
| `app/` | Aplikasi desktop: papan Pygame (`board_ui.py`), logika permainan (`game_logic.py`), ASR (`asr.py`), parser v2 (`voicechess_parser.py`), titik masuk `main.py` |
| `server/` | Server ASR untuk versi web (Starlette + Faster-Whisper), `requirements.txt`, `Dockerfile` |
| `web/` | Halaman web statis: `index.html`, `style.css`, `app.js`, `audio.js` (deteksi suara), `config.js` (alamat server) |
| `dataset/` | Perekam dataset (`record_dataset.py`) dan daftar perintah (`daftar_perintah.txt`) |
| `evaluasi/` | Skrip penyatu dataset, evaluasi Whisper (WER, akurasi parser), notebook Colab |
| `arsip/` | Versi lama (parser v1, notebook parser awal) |
| `.github/workflows/` | Build otomatis image Docker server ke GitHub Container Registry |

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

Lalu buka **http://localhost:8080/?api=http://localhost:7860** dan izinkan akses
mikrofon. Tanpa `?api=`, halaman memakai server Azure. Saat pertama kali dijalankan,
model Whisper (`small`) diunduh otomatis; tunggu sampai status menunjukkan "Server Online".

Variabel lingkungan server yang tersedia: `WHISPER_MODEL`, `WHISPER_DEVICE`,
`WHISPER_COMPUTE`, `ALLOWED_ORIGINS`, `PORT`.

## Demo online

### Default: server di Azure

Buka https://voicechess-id-web.vercel.app, tekan **Mulai Bermain**, lalu **Mulai Bicara**
dan izinkan mikrofon. Tidak perlu menyalakan laptop.

- Server tidur kalau beberapa menit tidak dipakai. Pembukaan pertama setelah itu butuh
  sekitar 30–60 detik (status "Memuat..."), dan posisi papan sebelumnya hilang.
  Buka halaman 1–2 menit sebelum demo.
- Mikrofon browser hanya bisa dipakai lewat HTTPS atau localhost.

### Cadangan: server di laptop lewat Cloudflare Tunnel

Kalau server Azure tidak tersedia, halaman di Vercel bisa memakai server ASR di laptop
yang dibuka ke internet lewat
[Cloudflare Quick Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/)
(pasang sekali: `winget install --id Cloudflare.cloudflared`).

1. Nyalakan server ASR: `python server/server.py`
2. Di terminal lain, buka tunnel: `cloudflared tunnel --url http://localhost:7860`
   lalu salin alamat `https://....trycloudflare.com` dari output-nya.
3. Buka `https://voicechess-id-web.vercel.app/?api=<alamat trycloudflare>`.
   Alamat itu diingat di browser tersebut; `?api=reset` mengembalikan ke server Azure.

Catatan:

- Laptop (server dan tunnel) harus tetap menyala selama demo.
- Alamat tunnel berganti setiap kali `cloudflared` dijalankan, jadi link `?api=` perlu diperbarui.
- Kalau perintah `cloudflared` belum dikenali setelah instalasi, tutup lalu buka lagi terminalnya.
- Demi keamanan, `?api=` hanya menerima `localhost` dan alamat `*.trycloudflare.com`.

## Deploy ulang

- **Web:** setiap push ke `main` otomatis di-deploy Vercel (Root Directory = `web`).
- **Server:** push yang mengubah `app/` atau `server/` memicu GitHub Actions membangun
  image baru. Setelah build hijau, jalankan di Azure Cloud Shell:

  ```bash
  az containerapp update -n voicechess-asr -g voicechess-rg \
    --image ghcr.io/unvariablehuman/voicechess-asr:latest
  ```

## Catatan

- **Rekaman suara dan dataset tidak disertakan** di repositori ini karena alasan privasi.
  Begitu juga hasil evaluasi per rekaman dan log. Skrip di `dataset/` dan `evaluasi/`
  tetap ada untuk merekam dan mengevaluasi dataset sendiri.
- Parser di `app/` adalah versi 2, yaitu versi yang dievaluasi (kamus dibekukan
  18-11-2026; perintah undo dimatikan).
- File model Whisper tidak disertakan di repositori; diunduh otomatis oleh `faster-whisper`
  (di image Docker, model diunduh saat build).
