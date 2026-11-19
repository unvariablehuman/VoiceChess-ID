"""
VoiceChess-ID - Evaluasi batch: WER, command accuracy, penanganan ragu, latency,
dan analisis salah dengar. Pengganti evaluasi() lama di voicechess_asr.ipynb.

Perbedaan penting dengan evaluasi() lama:
  * Kunci jawaban diambil dari kolom metadata (piece/src/dst/promotion), BUKAN dari
    parse(reference). Versi lama sirkular: kalau parser salah membaca referensi,
    kunci jawabannya ikut salah dan akurasinya tampak bagus.
  * Hasil Whisper disimpan ke results/asr_<model>_<prompt>.csv, jadi kalau Colab
    terputus, jalankan ulang dan ia melanjutkan (tidak mengulang dari nol).
  * Ada "pemanasan" per model (panggilan pertama tidak dihitung latency-nya).
  * Hasil dipisah menjadi 4 kategori keselamatan (lihat outcome()).

Colab (GPU T4 dianjurkan untuk batch; latency final tetap diukur di laptop demo):
    !pip install -q faster-whisper
    !python eval_dataset.py --meta data/metadata_all.csv --root data \
        --models tiny base small --prompt both --device cuda
Uji pipa tanpa Whisper (hipotesis = referensi, jadi WER=0; akurasi = batas atas parser):
    !python eval_dataset.py --meta data/metadata_all.csv --root data --dry-run
"""
import argparse
import difflib
import subprocess
import time
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from voicechess_parser import normalize_for_wer, parse

PROMPT = ("Perintah catur: pion, kuda, gajah, benteng, menteri, raja, rokade. "
          "Contoh: Pion E2 ke E4. Kuda G1 ke F3.")
PIECE_EN = {"pion": "pawn", "kuda": "knight", "gajah": "bishop", "benteng": "rook",
            "menteri": "queen", "ratu": "queen", "raja": "king", "": None}


# ------------------------------------------------------------------ ASR ----
def load_audio(path, sr=16000):
    out = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(path),
                          "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(out, np.float32)


_models = {}


def transcribe(path, model, prompt, device, compute_type):
    from faster_whisper import WhisperModel
    key = (model, device, compute_type)
    if key not in _models:
        _models[key] = WhisperModel(model, device=device, compute_type=compute_type)
    audio = load_audio(path)
    t = time.perf_counter()
    segs, _ = _models[key].transcribe(audio, language="id", beam_size=5, vad_filter=True,
                                      initial_prompt=prompt, condition_on_previous_text=False)
    text = " ".join(s.text.strip() for s in segs).strip()
    return text, time.perf_counter() - t


def run_asr(df, root, model, prompt, device, compute_type, cache):
    """Transkripsi semua file; hasil di-cache per baris supaya bisa dilanjutkan."""
    done = pd.read_csv(cache, keep_default_na=False) if cache.exists() else \
        pd.DataFrame(columns=["file", "hyp", "latency_s"])
    have = set(done.file)
    todo = [f for f in df.file.unique() if f not in have]
    if todo:
        transcribe(Path(root) / todo[0], model, prompt, device, compute_type)  # pemanasan
    new = []
    for i, f in enumerate(todo, 1):
        hyp, dt = transcribe(Path(root) / f, model, prompt, device, compute_type)
        new.append({"file": f, "hyp": hyp, "latency_s": round(dt, 3)})
        if i % 25 == 0 or i == len(todo):
            done = pd.concat([done, pd.DataFrame(new)], ignore_index=True)
            done.to_csv(cache, index=False); new = []
            print(f"    {model}/{'prompt' if prompt else 'tanpa'}: {len(done)}/{df.file.nunique()}")
    return done


# --------------------------------------------------------------- skoring ----
def gt_key(row):
    kind = row.expected_kind
    if kind == "move":
        return ("move", PIECE_EN.get(row.piece.lower()), row.src.lower() or None,
                row.dst.lower() or None, PIECE_EN.get(row.promotion.lower()))
    if kind == "castle":
        return ("castle", row.castle_side or None)
    return (kind,)


def pred_key(r):
    if r.kind == "move":
        return ("move", r.piece, r.src, r.dst, r.promotion)
    if r.kind == "castle":
        return ("castle", r.castle_side)
    return (r.kind,)


def outcome(correct, asked):
    """Kategori untuk bagian 'Error & Ambiguity Handling' di laporan."""
    if correct and not asked:
        return "1_benar_langsung"
    if correct and asked:
        return "2_benar_via_konfirmasi"
    if asked:
        return "3_salah_tapi_ditanya"      # aman: user bisa bilang 'tidak'
    return "4_salah_dieksekusi"            # berbahaya: langkah salah langsung jalan


def score(df, hyp_col="hyp"):
    rows = []
    for _, row in df.iterrows():
        r = parse(row[hyp_col])
        g, p = gt_key(row), pred_key(r)
        correct = g == p
        asked = r.needs_confirmation or r.kind == "unknown"
        comp = {}
        if row.expected_kind == "move":
            comp = dict(piece_ok=p[1:2] == g[1:2] if r.kind == "move" else False,
                        src_ok=r.kind == "move" and p[2] == g[2],
                        dst_ok=r.kind == "move" and p[3] == g[3])
        rows.append(dict(ref_norm=normalize_for_wer(row.reference),
                         hyp_norm=normalize_for_wer(row[hyp_col]),
                         pred=str(p), gold=str(g), cmd_ok=correct,
                         outcome=outcome(correct, asked), **comp))
    return pd.concat([df.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


def wer(refs, hyps):
    """WER tingkat korpus (sama dengan jiwer.wer): total edit / total kata referensi."""
    err = tot = 0
    for r, h in zip(refs, hyps):
        a, b = r.split(), h.split()
        d = list(range(len(b) + 1))
        for i, x in enumerate(a, 1):
            prev, d[0] = d[0], i
            for j, y in enumerate(b, 1):
                prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (x != y))
        err += d[len(b)]; tot += len(a)
    return err / max(tot, 1)


def summarize(res, by):
    def agg(g):
        mv = g[g.expected_kind == "move"]
        out = {"n": len(g),
               "WER": wer(g.ref_norm, g.hyp_norm),
               "cmd_acc": g.cmd_ok.mean(),
               "piece_acc": mv.piece_ok.mean() if len(mv) else np.nan,
               "src_acc": mv.src_ok.mean() if len(mv) else np.nan,
               "dst_acc": mv.dst_ok.mean() if len(mv) else np.nan}
        if "latency_s" in g:
            out["lat_med_s"] = g.latency_s.median()
            out["lat_p90_s"] = g.latency_s.quantile(0.9)
        for k in ("1_benar_langsung", "2_benar_via_konfirmasi",
                  "3_salah_tapi_ditanya", "4_salah_dieksekusi"):
            out[k] = (g.outcome == k).mean()
        return pd.Series(out)
    return res.groupby(by).apply(agg, include_groups=False).round(3)


def confusions(res, top=25):
    """Pasangan salah dengar dari perataan token (referensi -> hipotesis)."""
    c = Counter()
    for ref, hyp in zip(res.ref_norm, res.hyp_norm):
        a, b = ref.split(), hyp.split()
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
            if op == "replace" and i2 - i1 == j2 - j1:
                for x, y in zip(a[i1:i2], b[j1:j2]):
                    c[(x, y)] += 1
            elif op != "equal":
                c[(" ".join(a[i1:i2]) or "∅", " ".join(b[j1:j2]) or "∅")] += 1
    return pd.DataFrame([dict(referensi=k[0], terdengar=k[1], jumlah=v)
                         for k, v in c.most_common(top)])


# ----------------------------------------------------------------- main ----
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--meta", default="data/metadata_all.csv")
    ap.add_argument("--root", default="data")
    ap.add_argument("--out", default="results")
    ap.add_argument("--models", nargs="+", default=["small"])
    ap.add_argument("--prompt", choices=["none", "yes", "both"], default="both")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--compute-type", default=None, help="default: int8 (cpu) / float16 (cuda)")
    ap.add_argument("--speakers", nargs="*", help="hanya speaker ini (mis. set tuning)")
    ap.add_argument("--set", dest="subset", default=None, help="terbaca / spontan")
    ap.add_argument("--dry-run", action="store_true", help="hipotesis = referensi")
    a = ap.parse_args()
    ct = a.compute_type or ("float16" if a.device == "cuda" else "int8")

    df = pd.read_csv(a.meta, dtype=str, keep_default_na=False)
    if a.speakers:
        df = df[df.speaker.isin(a.speakers)]
    if a.subset:
        df = df[df.set == a.subset]
    out = Path(a.out); out.mkdir(exist_ok=True)

    # Batas atas parser: parser dijalankan pada teks referensi yang sempurna.
    up = score(df.assign(hyp=df.reference))
    print("\n=== Parser pada teks referensi (tanpa ASR) ===")
    print(summarize(up, ["set", "type"])[["n", "cmd_acc", "4_salah_dieksekusi"]])
    bad = up[~up.cmd_ok]
    if len(bad):
        print("\nParser salah membaca referensi (perbaiki kamus/aturan, atau labelnya):")
        print(bad[["speaker", "reference", "gold", "pred"]].drop_duplicates("reference").to_string())
    if a.dry_run:
        return

    configs = [(m, p) for m in a.models
               for p in ([None, PROMPT] if a.prompt == "both" else
                         [PROMPT] if a.prompt == "yes" else [None])]
    allres = []
    for m, p in configs:
        tag = f"{m}_{'prompt' if p else 'tanpa'}"
        print(f"\n--- {tag} ---")
        asr = run_asr(df, a.root, m, p, a.device, ct, out / f"asr_{tag}.csv")
        res = score(df.merge(asr, on="file", how="left").fillna({"hyp": ""}))
        res["model"], res["prompt"] = m, "ya" if p else "tidak"
        res["latency_s"] = pd.to_numeric(res.latency_s, errors="coerce")
        res.to_csv(out / f"detail_{tag}.csv", index=False)
        allres.append(res)

    res = pd.concat(allres, ignore_index=True)
    pd.set_option("display.width", 200); pd.set_option("display.max_columns", 20)
    tabel = summarize(res, ["model", "prompt", "set"])
    print("\n=== Ringkasan per model x prompt ===\n", tabel)
    tabel.to_csv(out / "ringkasan_model.csv")
    per = summarize(res, ["model", "prompt", "speaker", "condition"])
    per.to_csv(out / "ringkasan_speaker_kondisi.csv")
    print("\n(per speaker x kondisi disimpan di", out / "ringkasan_speaker_kondisi.csv", ")")

    # Analisis salah dengar: gabungan semua konfigurasi (paling banyak contoh error),
    # plus daftar per konfigurasi di CSV.
    per_cfg = []
    for (m, p), g in res.groupby(["model", "prompt"]):
        per_cfg.append(confusions(g, 15).assign(model=m, prompt=p))
    pd.concat(per_cfg).to_csv(out / "salah_dengar_per_konfigurasi.csv", index=False)
    conf = confusions(res, 30)
    conf.to_csv(out / "salah_dengar.csv", index=False)
    print("\n=== Salah dengar terbanyak (semua konfigurasi digabung) ===")
    print(conf.to_string() if len(conf) else "  (tidak ada)")
    for col in ("speaker", "condition"):
        print(f"\nSalah dengar per {col}:")
        for k, g in res.groupby(col):
            top = confusions(g, 6)
            print(f"  {k}: " + ("; ".join(f"{r.referensi}->{r.terdengar} ({r.jumlah})"
                                          for r in top.itertuples()) or "-"))
    worst = res[~res.cmd_ok][["model", "prompt", "speaker", "condition", "reference", "hyp",
                              "gold", "pred", "outcome"]]
    worst.to_csv(out / "perintah_gagal.csv", index=False)
    print(f"\n{len(worst)} perintah gagal -> {out / 'perintah_gagal.csv'}")

if __name__ == "__main__":
    main()
