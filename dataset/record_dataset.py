"""
VoiceChess-ID - Pembuat daftar perintah + perekam dataset suara.

Instalasi:
    pip install sounddevice soundfile numpy

Contoh pemakaian (satu sesi per speaker per kondisi):
    python record_dataset.py --speaker S01 --gender L --condition tenang --n 100
    python record_dataset.py --speaker S01 --gender L --condition bising --limit 40

Catatan:
- Seed yang sama -> semua speaker mengucapkan daftar perintah yang sama,
  jadi perbandingan antar speaker adil.
- Kalau terhenti di tengah jalan, jalankan lagi dengan argumen yang sama;
  perintah yang sudah direkam otomatis dilewati.
- Hasil: data/<speaker>/<kondisi>/*.wav dan data/metadata.csv
"""
import argparse
import csv
import random
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

SR = 16000  # 16 kHz mono, sesuai kebutuhan Whisper

PIECES = ["pion", "kuda", "gajah", "benteng", "menteri", "raja"]
FILES = "ABCDEFGH"

# Variasi kalimat alami (bagian "Natural Command" di proposal)
TEMPLATES = [
    "{piece} {src} ke {dst}",
    "Pindahkan {piece} dari {src} ke {dst}",
    "{piece} dari {src} menuju {dst}",
    "Geser {piece} {src} ke {dst}",
    "Jalankan {piece} dari {src} ke {dst}",
    "{piece} {src} makan {dst}",
]

# Perintah tetap: rokade, konfirmasi, dan kontrol
FIXED = [
    ("rokade", "Rokade pendek"),
    ("rokade", "Rokade panjang"),
    ("konfirmasi", "Ya"),
    ("konfirmasi", "Tidak"),
    ("konfirmasi", "Batal"),
    ("konfirmasi", "Ulangi"),
]
FIXED_REPEAT = 3  # tiap perintah tetap diucapkan beberapa kali

# Perintah tambahan: kasus yang lebih sulit, diucapkan sekali oleh tiap speaker.
# Ditaruh di AKHIR daftar supaya nomor perintah utama tidak berubah.
EXTRAS = [
    ("tanpa_asal", "Kuda ke F3"), ("tanpa_asal", "Pion ke E4"),
    ("tanpa_asal", "Gajah ke C4"), ("tanpa_asal", "Benteng ke D1"),
    ("tanpa_asal", "Menteri ke H5"), ("tanpa_asal", "Raja ke G1"),
    ("promosi", "Pion E7 ke E8 jadi menteri"),
    ("promosi", "Pion A7 ke A8 menjadi benteng"),
    ("promosi", "Pion H2 ke H1 promosi kuda"),
    ("promosi", "Pion B7 ke B8 jadi gajah"),
    ("makan", "Pion D4 makan E5"), ("makan", "Menteri D1 ambil H5"),
    ("makan", "Gajah C4 tangkap F7"),
    ("variasi", "Langkah pion E2 ke E4"),
    ("variasi", "Tolong pindahkan kuda G1 ke F3"),
    ("variasi", "Majukan pion D2 ke D4"),
]


def make_squares(piece, rng):
    """Pilih petak asal/tujuan yang masuk akal (tidak harus legal)."""
    if piece == "pion":
        f = rng.choice(FILES)
        r = rng.randint(2, 6)
        return f"{f}{r}", f"{f}{r + rng.choice([1, 2])}"
    while True:
        src = f"{rng.choice(FILES)}{rng.randint(1, 8)}"
        dst = f"{rng.choice(FILES)}{rng.randint(1, 8)}"
        if src != dst:
            return src, dst


def build_commands(n, seed):
    rng = random.Random(seed)
    cmds = []
    for i in range(n):
        tpl_id = i % len(TEMPLATES)               # template merata
        piece = PIECES[(i // len(TEMPLATES)) % len(PIECES)]  # bidak merata
        src, dst = make_squares(piece, rng)
        text = TEMPLATES[tpl_id].format(
            piece=piece.capitalize() if tpl_id in (0, 2) else piece,
            src=src, dst=dst,
        )
        text = text[0].upper() + text[1:]
        cmds.append({"type": "gerakan", "template_id": tpl_id,
                     "piece": piece, "src": src, "dst": dst, "text": text})
    for kind, text in FIXED:
        for _ in range(FIXED_REPEAT):
            cmds.append({"type": kind, "template_id": "", "piece": "",
                         "src": "", "dst": "", "text": text})
    rng.shuffle(cmds)
    for kind, text in EXTRAS:   # setelah shuffle: urutan utama tidak terganggu
        cmds.append({"type": kind, "template_id": "", "piece": "",
                     "src": "", "dst": "", "text": text})
    for i, c in enumerate(cmds):
        c["id"] = f"{i:04d}"
    return cmds


def record_until_enter():
    frames = []

    def callback(indata, n_frames, time_info, status):
        frames.append(indata.copy())

    with sd.InputStream(samplerate=SR, channels=1, dtype="float32",
                        callback=callback):
        input()  # tekan Enter untuk berhenti
    if not frames:
        return np.zeros((0, 1), dtype="float32")
    return np.concatenate(frames)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--speaker", required=True, help="ID speaker, mis. S01")
    ap.add_argument("--gender", required=True, choices=["L", "P"])
    ap.add_argument("--condition", default="tenang",
                    help="mis. tenang / bising / cepat / jauh")
    ap.add_argument("--n", type=int, default=100,
                    help="jumlah perintah gerakan")
    ap.add_argument("--limit", type=int, default=None,
                    help="rekam hanya N perintah pertama dari daftar (mis. 40 untuk kondisi bising)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="data")
    args = ap.parse_args()

    out = Path(args.out)
    wav_dir = out / args.speaker / args.condition
    wav_dir.mkdir(parents=True, exist_ok=True)
    meta_path = out / "metadata.csv"
    fields = ["id", "speaker", "gender", "condition", "file", "reference",
              "type", "template_id", "piece", "src", "dst"]

    done = set()
    if meta_path.exists():
        with open(meta_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["speaker"] == args.speaker and \
                        row["condition"] == args.condition:
                    done.add(row["id"])
    new_file = not meta_path.exists()

    cmds = build_commands(args.n, args.seed)
    if args.limit:
        cmds = cmds[:args.limit]
    cmds = [c for c in cmds if c["id"] not in done]
    total = len(cmds)
    print(f"{total} perintah tersisa untuk {args.speaker} ({args.condition}).")

    with open(meta_path, "a", newline="", encoding="utf-8") as mf:
        writer = csv.DictWriter(mf, fieldnames=fields)
        if new_file:
            writer.writeheader()

        i = 0
        while i < total:
            c = cmds[i]
            print(f"\n[{i + 1}/{total}]  Ucapkan:  >>> {c['text']} <<<")
            input("Tekan Enter untuk MULAI rekam...")
            print("Merekam... tekan Enter untuk BERHENTI.")
            audio = record_until_enter()
            dur = len(audio) / SR
            print(f"Durasi: {dur:.1f} detik")
            ans = input("Enter=simpan | r=ulang | s=lewati | q=keluar: ")
            ans = ans.strip().lower()
            if ans == "q":
                break
            if ans == "r":
                continue
            if ans == "s":
                i += 1
                continue
            fname = f"{args.speaker}_{args.condition}_{c['id']}.wav"
            sf.write(wav_dir / fname, audio, SR)
            writer.writerow({
                "id": c["id"], "speaker": args.speaker,
                "gender": args.gender, "condition": args.condition,
                "file": str(Path(args.speaker) / args.condition / fname),
                "reference": c["text"], "type": c["type"],
                "template_id": c["template_id"], "piece": c["piece"],
                "src": c["src"], "dst": c["dst"],
            })
            mf.flush()
            i += 1

    print("Selesai / dihentikan. Data tersimpan di", out)


if __name__ == "__main__":
    main()
