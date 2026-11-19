"""
VoiceChess-ID - ASR lokal: mikrofon -> deteksi awal/akhir bicara -> Faster-Whisper.

Berjalan di thread terpisah dan mengirim pesan ke papan lewat antrean (queue):
    ("status",  "Mendengarkan...")      keadaan mikrofon/model
    ("partial", "pion e dua ke")        transkripsi sementara selagi bicara
    ("final",   "Pion E2 ke E4.")       perintah selesai -> dijalankan
Setiap perintah final dicatat ke logs/latency.csv (untuk laporan: latency di laptop demo).

Butuh: pip install faster-whisper sounddevice numpy
Pertama kali dijalankan, model diunduh dari internet (small ~ 460 MB), sekali saja.
"""
import csv
import queue
import re
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np

# ------------------------------------------------------------- pengaturan ----
MODEL = "small"            # hasil evaluasi: small + prompt terbaik
DEVICE = "cpu"             # laptop demo: CPU
COMPUTE_TYPE = "int8"
PROMPT = ("Perintah catur: pion, kuda, gajah, benteng, menteri, raja, rokade. "
          "Contoh: Pion E2 ke E4. Kuda G1 ke F3.")
SR = 16000
BLOCK = 480                # 30 ms per blok
START_MS = 150             # suara selama ini -> dianggap mulai bicara
END_SILENCE_MS = 700       # diam selama ini -> dianggap selesai bicara
MAX_UTTER_S = 8.0          # batas panjang satu perintah
PRE_ROLL_MS = 300          # audio sebelum "mulai" ikut disimpan (awal kata tidak terpotong)
PARTIAL_EVERY_S = 1.0      # transkripsi sementara tiap ~1 dtk (hanya kalau sedang luang)
PARTIAL_MODEL = "tiny"     # model cepat KHUSUS tampilan sementara; None = tanpa transkripsi sementara
MIN_UTTER_S = 0.35         # lebih pendek dari ini dianggap bunyi sesaat, diabaikan
LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "latency.csv"
IGNORED_LOG = LOG_PATH.with_name("diabaikan.csv")   # halusinasi yang disaring (untuk laporan)

# Kalimat yang sering "dikarang" Whisper dari napas/derau (gaya penutup video YouTube),
# dan potongan prompt yang ikut tertulis. Hanya dibuang kalau TIDAK ada kata catur.
HALLUCINATION_WORDS = ("terima kasih", "menonton", "kerana", "subscribe", "sampai jumpa",
                       "jangan lupa", "nantikan", "subtitle", "terjemahan", "like dan share",
                       "perintah catur", "contoh")
CHESS_WORDS = ("pion", "kuda", "gajah", "benteng", "menteri", "ratu", "raja", "rokade", "roka",
               "ya", "iya", "tidak", "bukan", "batal", "ulang", "betul", "oke")


def is_hallucination(text):
    """True kalau teks tampak seperti halusinasi Whisper dan bukan perintah catur."""
    t = " " + " ".join(re.findall(r"[a-z0-9]+", text.lower())) + " "
    if not any(w in t for w in HALLUCINATION_WORDS):
        return False
    has_square = re.search(r" [a-h] ?[1-8] ", t) is not None
    has_chess = any(f" {w} " in t for w in CHESS_WORDS)
    return not (has_square or has_chess)


# --------------------------------------------------- deteksi awal/akhir bicara ----
class Endpointer:
    """Deteksi bicara berbasis energi (tanpa pustaka tambahan).
    Lantai derau = tingkat energi paling pelan (persentil 10) dalam ~5 dtk terakhir,
    sehingga mengikuti keadaan ruangan; blok hening total (awal mikrofon) diabaikan.
    feed(blok) -> None, "start", atau ("end", audio_float32)."""

    HISTORY_BLOCKS = 5000 // 30            # ~5 dtk
    DIGITAL_SILENCE_DB = -85.0

    def __init__(self, margin_db=10.0):
        self.margin_db = margin_db
        self.hist = deque(maxlen=self.HISTORY_BLOCKS)
        self.speaking = False
        self.voiced_run = 0
        self.silence_run = 0
        self.buf = []
        self.pre = deque(maxlen=max(1, PRE_ROLL_MS * SR // 1000 // BLOCK))

    @staticmethod
    def _db(block):
        return 10 * np.log10(np.mean(block.astype(np.float64) ** 2) + 1e-10)

    @property
    def noise_db(self):
        return float(np.percentile(self.hist, 10)) if self.hist else None

    def feed(self, block):
        db = self._db(block)
        if db > self.DIGITAL_SILENCE_DB:
            self.hist.append(db)
        if len(self.hist) < 10:                # ~0,3 dtk pertama: kalibrasi dulu
            self.pre.append(block)
            return None
        voiced = db > self.noise_db + self.margin_db
        if not self.speaking:
            self.pre.append(block)
            self.voiced_run = self.voiced_run + 1 if voiced else 0
            if self.voiced_run * BLOCK * 1000 // SR >= START_MS:
                self.speaking, self.silence_run = True, 0
                self.buf = list(self.pre)
                return "start"
            return None
        self.buf.append(block)
        self.silence_run = 0 if voiced else self.silence_run + 1
        dur = len(self.buf) * BLOCK / SR
        if self.silence_run * BLOCK * 1000 // SR >= END_SILENCE_MS or dur >= MAX_UTTER_S:
            audio = np.concatenate(self.buf)
            self.speaking, self.voiced_run, self.buf = False, 0, []
            self.pre.clear()
            return ("end", audio)
        return None

    def current_audio(self):
        return np.concatenate(self.buf) if self.buf else np.zeros(0, np.float32)


# ------------------------------------------------------------------ ASR ----
class VoiceASR:
    def __init__(self, outbox, model=MODEL, device=DEVICE, compute_type=COMPUTE_TYPE,
                 prompt=PROMPT, input_device=None, partial_model=PARTIAL_MODEL):
        self.outbox = outbox
        self.model_name, self.device, self.compute_type = model, device, compute_type
        self.prompt = prompt
        self.input_device = input_device
        self.model = None
        self.partial_model_name = partial_model
        self.partial_model = None
        self.muted = False
        self._stop = threading.Event()
        self._audio_q = queue.Queue()
        self._busy = threading.Lock()          # model final: satu audio sekaligus
        self._pbusy = threading.Lock()         # model sementara: terpisah, tidak menghalangi final
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def toggle_mute(self):
        self.muted = not self.muted
        self._say("Mikrofon dimatikan (F2 untuk menyalakan)" if self.muted else "Mendengarkan...")

    def _say(self, text):
        self.outbox.put(("status", text))

    # ---------------------------------------------------------------- inti ----
    def _load(self):
        from faster_whisper import WhisperModel
        self._say(f"Memuat model Whisper '{self.model_name}'... (pertama kali: mengunduh)")
        t = time.perf_counter()
        self.model = WhisperModel(self.model_name, device=self.device,
                                  compute_type=self.compute_type)
        self.model.transcribe(np.zeros(SR, np.float32), language="id")   # pemanasan
        if self.partial_model_name:
            self.partial_model = WhisperModel(self.partial_model_name, device=self.device,
                                              compute_type=self.compute_type)
        return time.perf_counter() - t

    def _transcribe(self, audio, beam, model=None):
        segs, _ = (model or self.model).transcribe(
            audio, language="id", beam_size=beam, vad_filter=True,
            initial_prompt=self.prompt, condition_on_previous_text=False)
        keep = [s.text.strip() for s in segs
                if not (s.no_speech_prob > 0.6 and s.avg_logprob < -1.0)]  # buang halusinasi
        return " ".join(keep).strip()

    def _partial(self, audio):
        if self.partial_model is None or not self._pbusy.acquire(blocking=False):
            return                                  # sedang sibuk: lewati sementara ini
        try:
            text = self._transcribe(audio, beam=1, model=self.partial_model)
            if text and not is_hallucination(text):
                self.outbox.put(("partial", text))
        finally:
            self._pbusy.release()

    def _final(self, audio, t_end):
        with self._busy:
            t0 = time.perf_counter()
            text = self._transcribe(audio, beam=5)
            t1 = time.perf_counter()
        if text and is_hallucination(text):
            self._log_ignored(text, len(audio) / SR)
            self.outbox.put(("partial", ""))
            self._say("Mendengarkan... (suara barusan bukan perintah, diabaikan)")
            return
        self.outbox.put(("final", text) if text else ("status", "Tidak terdengar jelas, ulangi."))
        if text:
            self._log(text, len(audio) / SR, t1 - t0, t1 - t_end)
        self._say("Mendengarkan...")

    def _log_ignored(self, text, audio_s):
        new = not IGNORED_LOG.exists()
        IGNORED_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(IGNORED_LOG, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["waktu", "model", "teks_diabaikan", "durasi_audio_s"])
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), self.model_name, text,
                        f"{audio_s:.2f}"])

    def _log(self, text, audio_s, asr_s, total_s):
        new = not LOG_PATH.exists()
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["waktu", "model", "device", "teks", "durasi_audio_s",
                            "whisper_s", "akhir_bicara_ke_teks_s"])
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), self.model_name, self.device,
                        text, f"{audio_s:.2f}", f"{asr_s:.3f}", f"{total_s:.3f}"])

    def _run(self):
        try:
            import sounddevice as sd
            load_s = self._load()
        except Exception as e:                      # model/mikrofon gagal: tampilkan di papan
            self._say(f"ASR gagal dimulai: {e}")
            return
        self._say(f"Mendengarkan... (model siap dalam {load_s:.0f} dtk)")
        ep = Endpointer()
        last_partial = 0.0

        def callback(indata, frames, t, status):
            self._audio_q.put(indata[:, 0].copy())

        try:
            with sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=BLOCK,
                                device=self.input_device, callback=callback):
                while not self._stop.is_set():
                    try:
                        block = self._audio_q.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    if self.muted:
                        continue
                    ev = ep.feed(block)
                    if ev == "start":
                        self._say("Merekam...")
                        last_partial = time.perf_counter()
                    elif isinstance(ev, tuple):
                        audio = ev[1]
                        if len(audio) / SR < MIN_UTTER_S:
                            self._say("Mendengarkan...")
                            continue
                        self._say("Memproses...")
                        threading.Thread(target=self._final, args=(audio, time.perf_counter()),
                                         daemon=True).start()
                    elif ep.speaking and time.perf_counter() - last_partial >= PARTIAL_EVERY_S:
                        last_partial = time.perf_counter()
                        threading.Thread(target=self._partial, args=(ep.current_audio(),),
                                         daemon=True).start()
        except Exception as e:
            self._say(f"Mikrofon error: {e}")


def list_microphones():
    import sounddevice as sd
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            print(f"{i:3}  {d['name']}")


if __name__ == "__main__":
    # Tes tanpa papan: python app/asr.py   (Ctrl+C untuk berhenti)
    import sys
    if "--mics" in sys.argv:
        list_microphones()
        sys.exit()
    q = queue.Queue()
    asr = VoiceASR(q)
    asr.start()
    print("Bicaralah ke mikrofon. Ctrl+C untuk berhenti.")
    try:
        while True:
            kind, text = q.get()
            print(f"[{kind:7}] {text}")
    except KeyboardInterrupt:
        asr.stop()
