"""
VoiceChess-ID - aplikasi lengkap: mikrofon -> Faster-Whisper -> parser -> papan.

Jalankan dari folder Project:
    pip install pygame python-chess faster-whisper sounddevice numpy
    python app/main.py
    python app/main.py --mic 2      # pilih mikrofon (lihat daftar: python app/asr.py --mics)
    python app/main.py --partial none   # tanpa transkripsi sementara (latency paling kecil)

Di papan: bicara langsung (tanpa tombol), F2 = matikan/nyalakan mikrofon.
Kotak ketik dan mouse tetap bisa dipakai.
Latency tiap perintah dicatat di logs/latency.csv.
"""
import argparse
import queue

from asr import VoiceASR
from board_ui import BoardUI
from game_logic import GameController


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mic", type=int, default=None, help="nomor mikrofon")
    ap.add_argument("--model", default="small", help="tiny / base / small")
    ap.add_argument("--partial", default="tiny",
                    help="model transkripsi sementara: tiny / base / none (tanpa)")
    a = ap.parse_args()

    inbox = queue.Queue()
    partial = None if a.partial.lower() == "none" else a.partial
    asr = VoiceASR(inbox, model=a.model, input_device=a.mic, partial_model=partial)
    asr.start()
    ui = BoardUI(GameController(), inbox=inbox, on_toggle_mic=asr.toggle_mute,
                 title="VoiceChess-ID (suara)")
    try:
        ui.run()
    finally:
        asr.stop()


if __name__ == "__main__":
    main()
