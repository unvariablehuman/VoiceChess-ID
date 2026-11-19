"""
VoiceChess-ID - Satukan semua rekaman di folder Drive "Rekaman Data" menjadi satu
dataset seragam:

    data/
      metadata_all.csv              <- satu tabel untuk semua speaker
      <speaker>/<kondisi>/<file>.wav   (16 kHz, mono, PCM 16-bit)
      _review/                      <- hal yang perlu dicek manusia

Menangani 4 bentuk folder yang ada di Drive kalian:
  1. Ada metadata.csv + WAV (S01, S02)            -> path Windows dibetulkan
  2. Ada metadata.csv + ZIP (S03)                 -> ZIP diekstrak dulu
  3. Hanya WAV bernama <spk>_<kondisi>_<id>.wav (S06) -> label diambil dari daftar induk
  4. Rekaman panjang "1-30.m4a", "31-40(bising).m4a" (S04)
                                                  -> dipotong otomatis berdasarkan jeda
  5. File bernama kalimatnya sendiri (S05)        -> set "spontan", label dari nama file

Pakai di Colab (folder bersama harus di-"Add shortcut to Drive" dulu):
    from google.colab import drive; drive.mount('/content/drive')
    !python prepare_dataset.py --src "/content/drive/MyDrive/Project KLP 5 - SR/Rekaman Data" \
        --out data --gender S06=P S04=L S05=P

Butuh: numpy, pandas, ffmpeg (sudah ada di Colab).
"""
import argparse
import re
import shutil
import subprocess
import sys
import wave
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

SR = 16000
AUDIO_EXT = {".wav", ".m4a", ".mp3", ".ogg", ".opus", ".webm", ".aac", ".flac"}

# ---------------------------------------------------------------- label ----
# Perintah 118-133 di metadata asli tidak punya piece/src/dst. Diisi manual di sini
# supaya kunci jawaban TIDAK bergantung pada parser yang sedang diuji.
EXTRA_GT = {
    118: ("kuda", "", "F3", ""),   119: ("pion", "", "E4", ""),
    120: ("gajah", "", "C4", ""),  121: ("benteng", "", "D1", ""),
    122: ("menteri", "", "H5", ""), 123: ("raja", "", "G1", ""),
    124: ("pion", "E7", "E8", "menteri"), 125: ("pion", "A7", "A8", "benteng"),
    126: ("pion", "H2", "H1", "kuda"),    127: ("pion", "B7", "B8", "gajah"),
    128: ("pion", "D4", "E5", ""), 129: ("menteri", "D1", "H5", ""),
    130: ("gajah", "C4", "F7", ""), 131: ("pion", "E2", "E4", ""),
    132: ("kuda", "G1", "F3", ""), 133: ("pion", "D2", "D4", ""),
}
PIECE_WORDS = {"pion", "kuda", "gajah", "benteng", "menteri", "ratu", "raja"}
CONTROL = [  # urutan penting: "batalkan langkah" dicek sebelum "batal"
    (r"\bbatalkan langkah\b|\bundo\b|\bmundur\b", "undo"),
    (r"\bbatal\w*\b", "cancel"),
    (r"\bulang\w*\b", "repeat"),
    (r"\btidak\b|\bbukan\b|\bnggak\b|\bgak\b", "no"),
    (r"\bya\b|\biya\b|\bbetul\b|\boke\b|\blanjut\w*\b", "yes"),
]
COLUMNS = ["id", "speaker", "gender", "condition", "set", "file", "reference", "type",
           "template_id", "expected_kind", "piece", "src", "dst", "promotion",
           "castle_side", "source", "needs_review", "note"]


def expected_from_text(text):
    """Kunci jawaban sederhana dari kalimat referensi (dipakai utk set spontan).
    Sengaja TIDAK memakai voicechess_parser agar evaluasinya tidak sirkular."""
    t = text.lower()
    squares = re.findall(r"\b([a-h])\s*-?\s*([1-8])\b", t)
    squares = [f"{c}{n}".upper() for c, n in squares]
    if "rokade" in t:
        side = "short" if "pendek" in t else "long" if "panjang" in t else ""
        return dict(expected_kind="castle", castle_side=side)
    if not squares:
        for pat, kind in CONTROL:
            if re.search(pat, t):
                return dict(expected_kind=kind)
        return dict(expected_kind="unknown")
    words = re.findall(r"[a-z]+", t)
    promo_at = next((i for i, w in enumerate(words) if w in ("jadi", "menjadi", "promosi")), None)
    head = words if promo_at is None else words[:promo_at]
    tail = [] if promo_at is None else words[promo_at + 1:]
    piece = next((w for w in head if w in PIECE_WORDS), "")
    promo = next((w for w in tail if w in PIECE_WORDS), "")
    src, dst = (squares[0], squares[1]) if len(squares) >= 2 else ("", squares[0])
    return dict(expected_kind="move", piece=piece, src=src, dst=dst, promotion=promo)


def plausibility_note(piece, src):
    """Tandai label yang mencurigakan (mis. 'pion dari B1': pion tak pernah di baris 1/8)."""
    if piece == "pion" and src and src[1] in "18":
        return "pion_di_baris_1/8?_cek_label"
    return ""


def load_master(src_root):
    """Ambil daftar 134 perintah dari metadata.csv mana pun yang lengkap (kondisi tenang)."""
    for csv in sorted(src_root.rglob("metadata.csv")):
        df = read_meta(csv)
        t = df[df.condition.str.lower() == "tenang"]
        if t.id.nunique() >= 134:
            m = t.drop_duplicates("id").set_index("id")
            print(f"[master] daftar perintah diambil dari {csv.relative_to(src_root)}")
            return m[["reference", "type", "template_id", "piece", "src", "dst"]]
    sys.exit("Tidak ada metadata.csv dengan 134 perintah 'tenang' untuk dijadikan daftar induk.")


def read_meta(path):
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df.columns = [c.strip().replace("\\_", "_") for c in df.columns]
    df["id"] = df["id"].astype(int)
    return df


def label_row(master, cid):
    r = master.loc[cid]
    piece, src, dst, promo = r.piece, r.src, r.dst, ""
    if cid in EXTRA_GT:
        piece, src, dst, promo = EXTRA_GT[cid]
    exp = expected_from_text(r.reference)
    return dict(reference=r.reference, type=r.type, template_id=r.template_id,
                expected_kind=exp["expected_kind"], castle_side=exp.get("castle_side", ""),
                piece=piece, src=src, dst=dst, promotion=promo,
                note=plausibility_note(piece, src))


# ---------------------------------------------------------------- audio ----
def decode(path):
    out = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(path),
                          "-f", "s16le", "-ac", "1", "-ar", str(SR), "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(out, np.int16)


def write_wav(path, pcm16):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(pcm16.astype(np.int16).tobytes())


def to_standard_wav(src, dst):
    """Semua file diseragamkan ke 16 kHz mono 16-bit (WAV HP kadang 44.1/48 kHz stereo)."""
    pcm = decode(src)
    write_wav(dst, pcm)
    return len(pcm) / SR


def find_segments(pcm, delta_db, min_gap, min_dur=0.25, pad=0.15):
    """Potong berdasarkan energi: bagian di atas (lantai derau + delta_db) dianggap suara,
    jeda < min_gap detik digabung (jeda antar-kata), potongan < min_dur dibuang."""
    x = pcm.astype(np.float32) / 32768
    hop, win = int(0.01 * SR), int(0.03 * SR)
    n = max(1, 1 + (len(x) - win) // hop)
    frames = np.lib.stride_tricks.sliding_window_view(x, win)[::hop][:n]
    db = 10 * np.log10(np.mean(frames ** 2, axis=1) + 1e-10)
    floor = np.percentile(db, 10)
    voiced = db > floor + delta_db
    segs, start = [], None
    for i, v in enumerate(voiced):
        if v and start is None:
            start = i
        elif not v and start is not None:
            segs.append([start, i]); start = None
    if start is not None:
        segs.append([start, len(voiced)])
    merged = []
    for s in segs:
        if merged and (s[0] - merged[-1][1]) * 0.01 < min_gap:
            merged[-1][1] = s[1]
        else:
            merged.append(s)
    out = []
    for s, e in merged:
        if (e - s) * 0.01 >= min_dur:
            a = max(0, int((s * 0.01 - pad) * SR)); b = min(len(pcm), int((e * 0.01 + pad) * SR))
            out.append((a, b))
    return out


def split_to_n(pcm, n_expected):
    """Cari pengaturan yang menghasilkan tepat n potongan. Dari semua pengaturan yang
    cocok, pilih yang paling 'tengah' (paling stabil). Return (segmen, info, cocok?)."""
    hits, best = [], None
    for delta in (6, 8, 10, 12, 14, 16, 18, 20):
        for gap in (0.4, 0.5, 0.6, 0.7, 0.8, 1.0, 1.2, 1.5):
            segs = find_segments(pcm, delta, gap)
            diff = abs(len(segs) - n_expected)
            if diff == 0:
                hits.append((delta, gap, segs))
            if best is None or diff < best[0]:
                best = (diff, delta, gap, segs)
    if hits:
        delta, gap, segs = hits[len(hits) // 2]
        return segs, f"delta={delta}dB gap={gap}s ({len(hits)} pengaturan cocok)", True
    _, delta, gap, segs = best
    return segs, f"terdekat: {len(segs)} potongan (delta={delta}dB gap={gap}s)", False


# ------------------------------------------------------------- handlers ----
RANGE_RE = re.compile(r"^(\d+)\s*-\s*(\d+)\s*(?:\((\w+)\))?$", re.I)
NUMBERED_RE = re.compile(r"^(.+?)_([a-z]+)_(\d{3,4})$", re.I)


def handle_with_metadata(spk_dir, metas, out, master, unzip_root):
    rows, problems = [], []
    for z in spk_dir.rglob("*.zip"):  # S03: tenang.zip, jauh.zip
        dest = unzip_root / spk_dir.name / z.stem
        if not dest.exists():
            with zipfile.ZipFile(z) as zf:
                zf.extractall(dest)
    index = {}
    for root in (spk_dir, unzip_root / spk_dir.name):
        if root.exists():
            for p in root.rglob("*"):
                if p.suffix.lower() in AUDIO_EXT:
                    index.setdefault(p.name.lower(), p)
    used = set()
    for csv in metas:
        for _, m in read_meta(csv).iterrows():
            base = m.file.replace("\\\\", "/").replace("\\", "/").split("/")[-1]
            src = index.get(base.lower())
            if src is None:
                problems.append(f"{spk_dir.name}: file di metadata tidak ditemukan: {base}")
                continue
            used.add(src.name.lower())
            dst = out / m.speaker / m.condition / Path(base).with_suffix(".wav").name
            dur = to_standard_wav(src, dst)
            lab = label_row(master, int(m.id))
            if lab["reference"].strip().lower() != m.reference.strip().lower():
                problems.append(f"{base}: reference beda dgn daftar induk "
                                f"('{m.reference}' vs '{lab['reference']}')")
            rows.append(dict(lab, id=int(m.id), speaker=m.speaker, gender=m.gender,
                             condition=m.condition, set="terbaca",
                             file=dst.relative_to(out).as_posix(), source=str(src.name),
                             needs_review=0, duration_s=round(dur, 2)))
    extra = sorted(set(index) - used)
    if extra:
        problems.append(f"{spk_dir.name}: {len(extra)} audio tidak tercatat di metadata, "
                        f"mis. {extra[:3]}")
    return rows, problems


def handle_numbered(spk_dir, files, out, master, genders):
    rows, problems = [], []
    for f in files:
        spk, cond, cid = NUMBERED_RE.match(f.stem).groups()
        cid = int(cid)
        if cid not in master.index:
            problems.append(f"{f.name}: id {cid} tidak ada di daftar induk"); continue
        dst = out / spk / cond / f.with_suffix(".wav").name
        dur = to_standard_wav(f, dst)
        rows.append(dict(label_row(master, cid), id=cid, speaker=spk,
                         gender=genders.get(spk, genders.get(spk_dir.name, "?")),
                         condition=cond, set="terbaca", file=dst.relative_to(out).as_posix(),
                         source=f.name, needs_review=0, duration_s=round(dur, 2)))
    ids = sorted({r["id"] for r in rows if r["condition"] == "tenang"})
    missing = sorted(set(range(134)) - set(ids))
    if ids and missing:
        problems.append(f"{spk_dir.name}: kondisi tenang kurang {len(missing)} id, mis. {missing[:10]}")
    return rows, problems


def handle_long(spk_dir, files, out, master, genders, review):
    rows, problems = [], []
    spk = spk_dir.name
    for f in sorted(files, key=lambda p: int(RANGE_RE.match(p.stem).group(1))):
        a, b, cond = RANGE_RE.match(f.stem).groups()
        cond = (cond or "tenang").lower()
        ids = list(range(int(a) - 1, int(b)))       # nama file 1-based -> id 0-based
        pcm = decode(f)
        segs, info, ok = split_to_n(pcm, len(ids))
        tag = f"{spk}/{f.name}: {len(ids)} perintah, {info}"
        if not ok:
            problems.append("PERINGATAN " + tag + " -> TIDAK dimasukkan; cek folder _review")
            for k, (s, e) in enumerate(segs):
                write_wav(review / spk / f.stem / f"potongan_{k:02d}.wav", pcm[s:e])
            pd.DataFrame([dict(potongan=k, mulai_s=round(s / SR, 2), selesai_s=round(e / SR, 2))
                          for k, (s, e) in enumerate(segs)]).to_csv(
                review / spk / f.stem / "potongan.csv", index=False)
            continue
        print("  [potong] " + tag)
        for cid, (s, e) in zip(ids, segs):
            name = f"{spk}_{cond}_{cid:04d}.wav"
            dst = out / spk / cond / name
            write_wav(dst, pcm[s:e])
            rows.append(dict(label_row(master, cid), id=cid, speaker=spk,
                             gender=genders.get(spk, "?"), condition=cond, set="terbaca",
                             file=dst.relative_to(out).as_posix(),
                             source=f"{f.name}@{s / SR:.2f}-{e / SR:.2f}s",
                             needs_review=1,  # tetap dengarkan acak beberapa potongan
                             duration_s=round((e - s) / SR, 2)))
    return rows, problems


def handle_spontan(spk_dir, files, out, genders):
    rows, problems = [], []
    spk = spk_dir.name
    for k, f in enumerate(sorted(files)):
        ref = f.stem.strip()
        exp = expected_from_text(ref)
        dst = out / spk / "spontan" / f"{spk}_spontan_{k:04d}.wav"
        dur = to_standard_wav(f, dst)
        note = plausibility_note(exp.get("piece", ""), exp.get("src", ""))
        if exp["expected_kind"] == "undo":
            note = "undo_belum_didukung_parser"
        rows.append(dict(exp, id=k, speaker=spk, gender=genders.get(spk, "?"),
                         condition="tenang", set="spontan", file=dst.relative_to(out).as_posix(),
                         reference=ref, type="spontan", template_id="", source=f.name,
                         needs_review=1, note=note, duration_s=round(dur, 2)))
    return rows, problems


# ----------------------------------------------------------------- main ----
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help='folder "Rekaman Data"')
    ap.add_argument("--out", default="data")
    ap.add_argument("--gender", nargs="*", default=[], help="mis. S06=P S04=L")
    ap.add_argument("--skip", nargs="*", default=["Commands"], help="subfolder yang dilewati")
    args = ap.parse_args()

    src, out = Path(args.src), Path(args.out)
    review, unzip_root = out / "_review", out / "_unzip"
    genders = dict(g.split("=", 1) for g in args.gender)
    master = load_master(src)

    rows, problems = [], []
    for spk_dir in sorted(p for p in src.iterdir() if p.is_dir() and p.name not in args.skip):
        audio = [p for p in spk_dir.rglob("*") if p.suffix.lower() in AUDIO_EXT]
        metas = list(spk_dir.rglob("metadata.csv"))
        has_zip = any(spk_dir.rglob("*.zip"))
        print(f"[{spk_dir.name}] {len(audio)} audio, {len(metas)} metadata.csv, zip={has_zip}")
        if metas:
            r, p = handle_with_metadata(spk_dir, metas, out, master, unzip_root)
        elif audio and all(NUMBERED_RE.match(a.stem) for a in audio):
            r, p = handle_numbered(spk_dir, audio, out, master, genders)
        elif audio and all(RANGE_RE.match(a.stem) for a in audio):
            r, p = handle_long(spk_dir, audio, out, master, genders, review)
        elif audio:
            r, p = handle_spontan(spk_dir, audio, out, genders)
        else:
            r, p = [], [f"{spk_dir.name}: tidak ada audio"]
        rows += r; problems += p

    df = pd.DataFrame(rows)
    for c in COLUMNS:
        if c not in df:
            df[c] = ""
    df = df[COLUMNS + ["duration_s"]]
    df[COLUMNS] = df[COLUMNS].fillna("")
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "metadata_all.csv", index=False)
    review.mkdir(parents=True, exist_ok=True)
    df[(df.needs_review == 1) | (df.note != "")].to_csv(review / "perlu_dicek.csv", index=False)

    print("\n=== Ringkasan (jumlah file per speaker x kondisi) ===")
    print(pd.crosstab([df.speaker, df.gender], [df.set, df.condition], margins=True))
    print("\nDurasi (detik):", df.duration_s.describe()[["min", "50%", "max"]].round(2).to_dict())
    short = df[df.duration_s < 0.4]
    if len(short):
        print(f"PERINGATAN: {len(short)} file < 0.4 dtk (mungkin kosong/terpotong):",
              list(short.file[:5]))
    print(f"\n=== {len(problems)} catatan ===")
    for p in problems:
        print(" -", p)
    print(f"\nTersimpan: {out / 'metadata_all.csv'} ({len(df)} baris)")


if __name__ == "__main__":
    main()
