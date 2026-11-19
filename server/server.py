"""
VoiceChess-ID - Server ASR untuk versi web.

Membungkus kode yang SAMA dengan aplikasi desktop dan evaluasi:
  app/voicechess_parser.py (v2)   app/game_logic.py   app/asr.py (prompt + filter halusinasi)
Halaman web (di Vercel) mengirim rekaman tiap perintah ke sini, server mentranskripsi
dengan Faster-Whisper, menjalankan perintah di papan, lalu mengembalikan keadaan papan.

Jalankan lokal (dari folder Project):
    pip install -r server/requirements.txt
    python server/server.py                 # http://localhost:7860

Variabel lingkungan (opsional):
    WHISPER_MODEL=small   WHISPER_DEVICE=cpu   WHISPER_COMPUTE=int8
    ALLOWED_ORIGINS=https://nama-proyek.vercel.app   (default: semua)
    PORT=7860
"""
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

from starlette.applications import Starlette                    # noqa: E402
from starlette.middleware import Middleware                     # noqa: E402
from starlette.middleware.cors import CORSMiddleware            # noqa: E402
from starlette.responses import JSONResponse, PlainTextResponse  # noqa: E402
from starlette.routing import Route                             # noqa: E402

from asr import PROMPT, is_hallucination                        # noqa: E402
from game_logic import GameController                           # noqa: E402

MODEL_NAME = os.environ.get("WHISPER_MODEL", "small")
DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")
COMPUTE = os.environ.get("WHISPER_COMPUTE", "int8")
MAX_AUDIO_BYTES = 3 * 1024 * 1024        # ~1,5 menit WAV 16 kHz; satu perintah jauh lebih kecil
MAX_SESSIONS = 200
SR = 16000
LOG_PATH = ROOT / "logs" / "latency_web.csv"

# ------------------------------------------------------------ model ----
_model = None
_model_error = None
_model_lock = threading.Lock()           # CPU: satu transkripsi sekaligus


def _load_model():
    global _model, _model_error
    try:
        from faster_whisper import WhisperModel
        import numpy as np
        m = WhisperModel(MODEL_NAME, device=DEVICE, compute_type=COMPUTE)
        m.transcribe(np.zeros(SR, np.float32), language="id")            # pemanasan
        _model = m
        print(f"[server] model {MODEL_NAME} siap", flush=True)
    except Exception as e:                                               # tampilkan di /health
        _model_error = repr(e)
        print(f"[server] model gagal dimuat: {e}", flush=True)


def decode_audio(data: bytes):
    """Audio dari browser -> float32 mono 16 kHz.
    WAV PCM 16-bit (yang dikirim halaman web) dibaca langsung tanpa ffmpeg;
    format lain baru memakai ffmpeg (kalau terpasang)."""
    import io
    import wave
    import numpy as np
    try:
        with wave.open(io.BytesIO(data)) as w:
            ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            raw = w.readframes(n)
        if width == 2:
            x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
            if ch > 1:
                x = x.reshape(-1, ch).mean(axis=1)
            if rate != SR:
                t_new = np.arange(int(len(x) * SR / rate)) / SR
                x = np.interp(t_new, np.arange(len(x)) / rate, x).astype(np.float32)
            return x
    except (wave.Error, EOFError):
        pass
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        f.write(data)
        path = f.name
    try:
        out = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", path,
                              "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"],
                             capture_output=True, check=True).stdout
    except FileNotFoundError:
        raise ValueError("format audio bukan WAV dan ffmpeg tidak terpasang")
    finally:
        os.unlink(path)
    return np.frombuffer(out, np.float32)


def transcribe(audio):
    with _model_lock:
        t = time.perf_counter()
        segs, _ = _model.transcribe(audio, language="id", beam_size=5, vad_filter=True,
                                    initial_prompt=PROMPT, condition_on_previous_text=False)
        keep = [s.text.strip() for s in segs
                if not (s.no_speech_prob > 0.6 and s.avg_logprob < -1.0)]
        return " ".join(keep).strip(), time.perf_counter() - t


def log_latency(text, audio_s, whisper_s, ignored):
    import csv
    new = not LOG_PATH.exists()
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["waktu", "model", "device", "teks", "durasi_audio_s", "whisper_s",
                        "diabaikan"])
        w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), MODEL_NAME, DEVICE, text,
                    f"{audio_s:.2f}", f"{whisper_s:.3f}", int(ignored)])


# ----------------------------------------------------------- sesi ----
_sessions: "OrderedDict[str, GameController]" = OrderedDict()
_sessions_lock = threading.Lock()


def get_game(sid):
    """Satu papan per tab browser. Sesi lama dibuang kalau lebih dari MAX_SESSIONS."""
    with _sessions_lock:
        if not sid or sid not in _sessions:
            sid = sid or uuid.uuid4().hex
            _sessions[sid] = GameController()
            while len(_sessions) > MAX_SESSIONS:
                _sessions.popitem(last=False)
        _sessions.move_to_end(sid)
        return sid, _sessions[sid]


def board_state(sid, gc):
    import chess
    b = gc.board
    legal = {}
    for m in b.legal_moves:
        legal.setdefault(chess.square_name(m.from_square), []).append(chess.square_name(m.to_square))
    sq = chess.square_name
    return {
        "session": sid,
        "fen": b.fen(),
        "turn": "w" if b.turn else "b",
        "turn_text": gc.turn_text,
        "message": gc.message,
        "transcript": gc.transcript,
        "history": gc.history,
        "last_move": [sq(gc.last_move.from_square), sq(gc.last_move.to_square)] if gc.last_move else None,
        "pending": [sq(gc.pending.from_square), sq(gc.pending.to_square)] if gc.pending else None,
        "check": sq(b.king(b.turn)) if b.is_check() else None,
        "game_over": b.is_game_over(),
        "legal": legal,
    }


# --------------------------------------------------------- endpoint ----
async def _json(request):
    try:
        return await request.json()
    except Exception:
        return {}


async def home(request):
    return PlainTextResponse("VoiceChess-ID server OK. Lihat /health.")


async def health(request):
    return JSONResponse({"ok": True, "model": MODEL_NAME, "device": DEVICE,
                         "model_ready": _model is not None, "model_error": _model_error})


async def new_game(request):
    body = await _json(request)
    sid, gc = get_game(body.get("session"))
    gc.new_game()
    return JSONResponse(board_state(sid, gc))


async def state(request):
    body = await _json(request)
    sid, gc = get_game(body.get("session"))
    return JSONResponse(board_state(sid, gc))


async def command(request):
    body = await _json(request)
    sid, gc = get_game(body.get("session"))
    gc.handle_text(str(body.get("text", ""))[:200])
    return JSONResponse(board_state(sid, gc))


async def confirm(request):
    body = await _json(request)
    sid, gc = get_game(body.get("session"))
    gc.confirm(bool(body.get("yes")))
    return JSONResponse(board_state(sid, gc))


async def move(request):
    import chess
    body = await _json(request)
    sid, gc = get_game(body.get("session"))
    try:
        ok = gc.mouse_move(chess.parse_square(body["from"]), chess.parse_square(body["to"]))
    except (KeyError, ValueError):
        ok = False
    if not ok and not gc.board.is_game_over():
        gc.message = "Langkah itu tidak legal."
    return JSONResponse(board_state(sid, gc))


async def voice(request):
    """Form multipart: audio (WAV) + session. Transkripsi -> jalankan perintah -> keadaan papan."""
    if _model is None:
        msg = ("Model Whisper sedang dimuat, coba lagi sebentar." if _model_error is None
               else f"Model gagal dimuat: {_model_error}")
        return JSONResponse({"error": msg}, status_code=503)
    form = await request.form()
    sid, gc = get_game(form.get("session"))
    upload = form.get("audio")
    if upload is None:
        return JSONResponse({"error": "audio tidak ada"}, status_code=400)
    data = await upload.read()
    if not data or len(data) > MAX_AUDIO_BYTES:
        return JSONResponse({"error": "ukuran audio tidak valid"}, status_code=400)
    try:
        audio = decode_audio(data)
    except (subprocess.CalledProcessError, ValueError) as e:
        return JSONResponse({"error": f"audio tidak bisa dibaca ({e})"}, status_code=400)

    import anyio
    try:
        text, whisper_s = await anyio.to_thread.run_sync(transcribe, audio)
    except Exception as e:                    # balas dengan pesan, bukan koneksi putus
        print(f"[server] transkripsi gagal: {e!r}", flush=True)
        return JSONResponse({"error": f"transkripsi gagal: {e}"}, status_code=500)
    ignored = bool(text) and is_hallucination(text)
    log_latency(text, len(audio) / SR, whisper_s, ignored)
    if text and not ignored:
        gc.handle_text(text)
    out = board_state(sid, gc)
    out.update({"heard": text, "ignored": ignored, "whisper_s": round(whisper_s, 3),
                "audio_s": round(len(audio) / SR, 2)})
    return JSONResponse(out)


@asynccontextmanager
async def lifespan(app):
    threading.Thread(target=_load_model, daemon=True).start()   # model dimuat di latar
    yield


origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
app = Starlette(
    routes=[
        Route("/", home), Route("/health", health),
        Route("/api/new", new_game, methods=["POST"]),
        Route("/api/state", state, methods=["POST"]),
        Route("/api/command", command, methods=["POST"]),
        Route("/api/confirm", confirm, methods=["POST"]),
        Route("/api/move", move, methods=["POST"]),
        Route("/api/voice", voice, methods=["POST"]),
    ],
    middleware=[Middleware(CORSMiddleware, allow_origins=origins,
                           allow_methods=["GET", "POST"], allow_headers=["*"])],
    lifespan=lifespan,
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 7860)))
