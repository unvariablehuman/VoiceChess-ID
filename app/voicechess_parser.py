"""
VoiceChess-ID - Command parser (teks -> perintah catur).  VERSI 2

Alur:  teks hasil ASR --normalisasi--> token --parse--> ParseResult
       ParseResult + papan python-chess --resolve--> langkah legal / ambigu / ilegal

Pakai:
    from voicechess_parser import parse, resolve, normalize_for_wer
    r = parse("kuda ge satu ke ef tiga")      # piece=knight src=g1 dst=f3
    status, moves, note = resolve(board, r)   # butuh: pip install python-chess

Jalankan tes:  python voicechess_parser.py

Perubahan v2 (KAMUS DIBEKUKAN 18-11-2026). Semua aturan baru diturunkan HANYA dari
salah dengar speaker tuning (S02, S06) pada Whisper small; speaker lain (S01, S03,
S04, S05) tidak dilihat saat menyusun aturan.
  1. Petak "rusak" diperbaiki, selalu dengan confidence=low (sistem bertanya):
       - huruf+angka menyatu: halima->h5, ati->a3, ali->a5 (kata yang mirip nama
         bidak/kata kerja, mis. "benting", "guda", "bindakan", TIDAK dipecah)
       - dua huruf sebelum angka: DM4->d4, Bd8->b8, Gd8->g8, Rd5->d5
       - angka ganda: C28->c8 (digit terakhir), A 7000->a7 (digit pertama)
       - huruf asing: S1->e1
  2. Keamanan: satu petak saja yang muncul SEBELUM "ke/menuju/makan" padahal sesudahnya
     masih ada kata ("Kuda G4 ke Halima") -> tujuan tak terbaca -> tidak dijalankan.
     Petak asal == tujuan ("E5 ke E5") -> confidence=low.
     "Roka depan" TIDAK ditebak sisinya (di data S02 dipakai untuk pendek dan panjang)
     -> rokade tanpa sisi -> bertanya.
     Angka petak tanpa huruf valid ("Benteng K3, makan D5"): satu petak tersisa ->
     tidak dijalankan; dua petak + angka sisa -> confidence=low. "7" di antara dua
     petak dianggap "menuju" (terdengar "7").
  3. Dua nama bidak sebelum petak ("Gajah kuda B2", dari "geser" terdengar "gajah")
     -> pakai yang terdekat ke petak, confidence=low.
  4. Alias: roka/prokade->rokade; dipendek/dupendeg/bendeg/depen->pendek;
     dipanjang->panjang; batau/batang->batal; ulani/ulanin->ulangi;
     makanan->makan. "Ulang E2" -> repeat (bukan gerakan).
  5. normalize_for_wer: "jalan kan" -> "jalankan" (varian ejaan, bukan salah dengar).
"""
import difflib
import re
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------- kamus ----
NUMBER_WORDS = {
    "satu": 1, "dua": 2, "tiga": 3, "empat": 4,
    "lima": 5, "enam": 6, "tujuh": 7, "delapan": 8, "lapan": 8,
}

# Cara orang mengucapkan nama kolom (dan salah dengar yang umum dari ASR)
LETTERS = {
    "a": "a", "ah": "a", "ei": "a",
    "b": "b", "be": "b", "bi": "b",
    "c": "c", "ce": "c", "se": "c", "si": "c",
    "d": "d", "de": "d", "di": "d",
    "e": "e", "ee": "e", "eh": "e",
    "f": "f", "ef": "f", "ep": "f",
    "g": "g", "ge": "g", "gi": "g", "je": "g",
    "h": "h", "ha": "h",
}
# v2: huruf yang bukan nama kolom tapi muncul sebagai salah dengar (selalu confidence=low)
LETTERS_LOW = {"s": "e"}            # "E1" -> "S1" (S02)

PIECES = {
    "pion": "pawn", "pawn": "pawn",
    "kuda": "knight", "knight": "knight",
    "gajah": "bishop", "bishop": "bishop",
    "benteng": "rook", "rook": "rook",
    "menteri": "queen", "ratu": "queen", "queen": "queen",
    "raja": "king", "king": "king",
}

# v2: alias kata (salah dengar speaker tuning) -> kata baku
ALIASES = {
    "roka": "rokade", "prokade": "rokade", "rokada": "rokade",
    "dipendek": "pendek", "dupendeg": "pendek", "bendeg": "pendek", "depen": "pendek",
    "pendeg": "pendek",
    "dipanjang": "panjang",
    "batau": "batal", "batang": "batal",
    "ulani": "ulangi", "ulanin": "ulangi",
    "makanan": "makan", "takap": "tangkap",
}

STOPWORDS = {
    "pindahkan", "geser", "jalankan", "langkah", "tolong", "dari", "menuju",
    "ke", "makan", "tangkap", "ambil", "jadi", "menjadi", "promosi",
    "promosikan", "bukan", "salah", "tidak", "batal", "ulangi", "rokade",
    "pendek", "panjang", "iya", "betul", "benar",
}
CAPTURE_WORDS = {"makan", "tangkap", "ambil"}
LINK_WORDS = {"ke", "menuju"} | CAPTURE_WORDS     # pemisah petak asal dan tujuan
PROMO_WORDS = {"jadi", "menjadi", "promosi", "promosikan"}
YES = {"ya", "iya", "yap", "yoi", "betul", "benar", "oke", "ok", "setuju", "lanjut"}
NO = {"tidak", "nggak", "enggak", "gak", "ga", "bukan", "salah"}
CANCEL = {"batal", "cancel", "lupakan"}
REPEAT = {"ulangi", "ulang"}
KNOWN = (set(NUMBER_WORDS) | set(PIECES) | STOPWORDS | YES | NO | CANCEL | REPEAT
         | {"jalan", "kan", "majukan", "dan", "lagi", "lanjutkan"})

VERB_STEMS = {"pindahkan", "pindahin", "jalankan", "majukan", "majuin", "geser", "langkah"}
SQUARE_RE = re.compile(r"^[a-h][1-8]$")


# ------------------------------------------------------------ normalisasi --
def _rank(tok: str) -> Optional[int]:
    if tok.isdigit() and 1 <= int(tok) <= 8 and len(tok) == 1:
        return int(tok)
    return NUMBER_WORDS.get(tok)


def _rank_loose(tok: str) -> Optional[int]:
    """v2: angka ganda hasil salah dengar. 'C28' -> 8 (digit terakhir), '7000' -> 7."""
    if tok.isdigit() and len(tok) > 1:
        for d in (tok[-1], tok[0]):
            if "1" <= d <= "8":
                return int(d)
    return None


def _fused_square(tok: str) -> Optional[str]:
    """v2: huruf + kata angka yang menyatu: 'halima'->h5, 'ati'->a3, 'cedlab'->c8."""
    if tok in KNOWN or tok in ALIASES or len(tok) < 3:
        return None
    if difflib.get_close_matches(tok, list((KNOWN - set(NUMBER_WORDS)) | set(ALIASES) | VERB_STEMS), n=1, cutoff=0.7):
        return None                 # "benting", "guda", "bindakan": salah ejaan kata biasa
    for p in sorted(LETTERS, key=len, reverse=True):
        rest = tok[len(p):]
        if tok.startswith(p) and len(rest) >= 2:
            best, score = None, 0.0
            for w, n in NUMBER_WORDS.items():
                s = difflib.SequenceMatcher(a=rest, b=w).ratio()
                if s > score:
                    best, score = n, s
            if score >= 0.6:
                return f"{LETTERS[p]}{best}"
    return None


def tokenize(text: str) -> list:
    """Huruf kecil, buang tanda baca, pisahkan huruf dan angka ('e2' -> 'e','2')."""
    return re.findall(r"[a-z]+|\d+", text.lower())


def merge_squares(tokens: list, repaired: Optional[list] = None) -> list:
    """Gabungkan 'e','dua' / 'ef','3' / 'ge','satu' menjadi satu token petak ('e2').
    v2: juga memperbaiki petak rusak; petak hasil perbaikan dicatat di `repaired`."""
    rep = repaired if repaired is not None else []
    out, i = [], 0
    while i < len(tokens):
        t = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if nxt is not None:
            r = _rank(nxt)
            if t in LETTERS and r:
                out.append(f"{LETTERS[t]}{r}"); i += 2; continue
            loose = r or _rank_loose(nxt)
            letter = None
            if t in LETTERS_LOW:
                letter = LETTERS_LOW[t]
            elif len(t) == 2 and t not in KNOWN and nxt.isdigit():   # DM4, Bd8, Rd5
                letter = t[0] if t[0] in "abcdefgh" else (t[1] if t[1] in "abcdefgh" else None)
            elif t in LETTERS and loose:                              # C28, A 7000
                letter = LETTERS[t]
            if letter and loose:
                sq = f"{letter}{loose}"
                out.append(sq); rep.append(sq); i += 2; continue
        f = _fused_square(t)
        if f:
            out.append(f); rep.append(f); i += 1; continue
        out.append(t)
        i += 1
    return out


def _canon(tokens: list) -> list:
    return [ALIASES.get(t, t) for t in tokens]


def normalize_for_wer(text: str) -> str:
    """Bentuk kanonik untuk menghitung WER: pakai di reference DAN hipotesis.
    Hanya menyeragamkan penulisan (e dua -> e2, jalan kan -> jalankan); TIDAK memakai
    alias salah dengar, supaya WER tetap mengukur Whisper apa adanya."""
    toks = tokenize(text)
    out, i = [], 0
    while i < len(toks):
        if toks[i] == "jalan" and i + 1 < len(toks) and toks[i + 1] == "kan":
            out.append("jalankan"); i += 2; continue
        out.append(toks[i]); i += 1
    joined, i = [], 0
    while i < len(out):                       # hanya penggabungan petak baku (versi 1)
        t = out[i]
        if t in LETTERS and i + 1 < len(out) and _rank(out[i + 1]):
            joined.append(f"{LETTERS[t]}{_rank(out[i + 1])}"); i += 2; continue
        joined.append(t); i += 1
    return " ".join(joined)


# ----------------------------------------------------------------- parser --
@dataclass
class ParseResult:
    kind: str = "unknown"      # move | castle | yes | no | cancel | repeat | unknown
    piece: Optional[str] = None       # pawn/knight/bishop/rook/queen/king
    src: Optional[str] = None
    dst: Optional[str] = None
    promotion: Optional[str] = None
    castle_side: Optional[str] = None  # short | long | None
    capture: bool = False
    confidence: str = "high"           # high | low  (low -> minta konfirmasi)
    notes: list = field(default_factory=list)
    normalized: str = ""

    @property
    def needs_confirmation(self) -> bool:
        return self.confidence == "low"


def _find_piece(tokens: list, indices: list, nearest_to: Optional[int] = None):
    """Cari bidak di posisi `indices`: exact dulu, lalu fuzzy. -> (piece, fuzzy?, idx, banyak?)
    v2: kalau ada >1 bidak, ambil yang paling dekat ke `nearest_to` (petak pertama)."""
    exact = [i for i in indices if tokens[i] in PIECES]
    if exact:
        i = exact[-1] if nearest_to is not None else exact[0]
        return PIECES[tokens[i]], False, i, len(exact) > 1
    for i in indices:
        t = tokens[i]
        if len(t) >= 3 and t not in STOPWORDS and not SQUARE_RE.match(t):
            m = difflib.get_close_matches(t, PIECES.keys(), n=1, cutoff=0.7)
            if m:
                return PIECES[m[0]], True, i, False
    return None, False, None, False


def _low(res, note):
    res.confidence = "low"
    res.notes.append(note)


def parse(text: str) -> ParseResult:
    repaired = []
    tokens = _canon(merge_squares(_canon(tokenize(text)), repaired))
    res = ParseResult(normalized=" ".join(tokens))
    sq_idx = [i for i, t in enumerate(tokens) if SQUARE_RE.match(t)]
    # angka petak "yatim": angka yang tersisa tanpa huruf kolom valid ("K3", "K5").
    # Pengecualian: "7" di antara dua petak = "menuju" yang terdengar "7" (data S02/S06).
    orphan = [i for i, t in enumerate(tokens) if t.isdigit() or t in NUMBER_WORDS]
    if len(sq_idx) >= 2:
        orphan = [i for i in orphan if not (tokens[i] == "7" and sq_idx[0] < i < sq_idx[1])]
    squares = [tokens[i] for i in sq_idx]
    tset = set(tokens)
    has_piece = any(t in PIECES for t in tokens)

    # 1) rokade
    if "rokade" in tset:
        res.kind = "castle"
        if "pendek" in tset:
            res.castle_side = "short"
        elif "panjang" in tset:
            res.castle_side = "long"
        else:
            _low(res, "sisi_rokade_tidak_disebut")
        return res

    # 2) kata kontrol (jika tidak ada petak, atau ada kata ulang/batal tanpa bidak)
    if not squares or (not has_piece and tset & (REPEAT | CANCEL)):
        if tset & CANCEL:
            res.kind = "cancel"
        elif tset & REPEAT:
            res.kind = "repeat"
        elif tset & YES and not (tset & NO):
            res.kind = "yes"
        elif tset & NO:
            res.kind = "no"
        else:
            res.notes.append("tidak_ada_petak")
        return res

    # 3) gerakan
    res.kind = "move"
    res.capture = bool(tset & CAPTURE_WORDS)
    if len(squares) == 1 and orphan:
        res.kind = "unknown"
        res.notes.append("petak_tidak_terbaca")
        return res
    if orphan:
        _low(res, "ada_angka_petak_tak_terbaca")
    if len(squares) >= 2:
        res.src, res.dst = squares[0], squares[1]
        if len(squares) > 2:
            _low(res, "petak_lebih_dari_dua")
        if res.src == res.dst:
            _low(res, "asal_sama_dengan_tujuan")
    else:
        # "Kuda ke F3": sah HANYA jika petak itu sesudah kata penghubung (atau tanpa
        # penghubung sama sekali). "Kuda G4 ke Halima" -> petak di depan "ke" adalah
        # asal, tujuan tidak terbaca -> jangan dijalankan.
        i = sq_idx[0]
        link_before = any(t in LINK_WORDS for t in tokens[:i])
        link_after = [j for j in range(i + 1, len(tokens)) if tokens[j] in LINK_WORDS]
        if not link_before and link_after and link_after[0] < len(tokens) - 1:
            res.kind = "unknown"
            res.src = squares[0]
            res.notes.append("tujuan_tidak_terbaca")
            return res
        res.dst = squares[0]
    if repaired and any(s in repaired for s in (res.src, res.dst)):
        _low(res, "petak_hasil_perbaikan")

    # bagian sebelum / sesudah kata promosi
    promo_idx = next((i for i, t in enumerate(tokens) if t in PROMO_WORDS), None)
    first_sq = sq_idx[0]
    before = list(range(promo_idx)) if promo_idx is not None else list(range(len(tokens)))
    before_sq = [i for i in before if i < first_sq] or before
    after = list(range(promo_idx + 1, len(tokens))) if promo_idx is not None else []

    piece, fuzzy, _, many = _find_piece(tokens, before_sq, nearest_to=first_sq)
    if piece is None:
        piece, fuzzy, _, many = _find_piece(tokens, before)
    res.piece = piece
    if fuzzy:
        _low(res, "bidak_hasil_fuzzy")
    if many:
        _low(res, "lebih_dari_satu_bidak")
    if promo_idx is not None:
        promo, pf, _, _ = _find_piece(tokens, after)
        res.promotion = promo
        if pf:
            _low(res, "promosi_hasil_fuzzy")

    if res.src is None and res.piece is None:
        res.kind = "unknown"
        res.notes.append("hanya_tujuan_tanpa_bidak")
    return res


# ---------------------------------------------------------------- resolusi --
def resolve(board, r: ParseResult):
    """
    Cocokkan ParseResult dengan langkah legal di `board` (python-chess).
    Return (status, moves, note):
      ok         -> tepat 1 langkah legal (moves[0])
      ambiguous  -> >1 kandidat (tanya pengguna)
      mismatch   -> bidak di petak asal tidak sama dengan yang disebut
      illegal    -> tidak ada langkah legal yang cocok
      unknown    -> perintah bukan gerakan
    """
    import chess

    names = {"pawn": chess.PAWN, "knight": chess.KNIGHT, "bishop": chess.BISHOP,
             "rook": chess.ROOK, "queen": chess.QUEEN, "king": chess.KING}
    legal = list(board.legal_moves)

    if r.kind == "castle":
        if r.castle_side is None:
            cands = [m for m in legal if board.is_castling(m)]
        elif r.castle_side == "short":
            cands = [m for m in legal if board.is_kingside_castling(m)]
        else:
            cands = [m for m in legal if board.is_queenside_castling(m)]
        return _verdict(cands, "rokade_tidak_legal")

    if r.kind != "move":
        return "unknown", [], "bukan_gerakan"

    dst = chess.parse_square(r.dst)
    cands = [m for m in legal if m.to_square == dst]

    if r.src:
        src = chess.parse_square(r.src)
        on_src = board.piece_type_at(src)
        if r.piece and on_src is not None and on_src != names[r.piece]:
            return "mismatch", [], f"di_{r.src}_ada_{chess.piece_name(on_src)}"
        cands = [m for m in cands if m.from_square == src]
    if r.piece:
        cands = [m for m in cands
                 if board.piece_type_at(m.from_square) == names[r.piece]]
    if r.promotion:
        cands = [m for m in cands if m.promotion == names[r.promotion]]

    return _verdict(cands, "langkah_tidak_legal")


def _verdict(cands, illegal_note):
    if not cands:
        return "illegal", [], illegal_note
    if len(cands) == 1:
        return "ok", cands, ""
    if len({(m.from_square, m.to_square) for m in cands}) == 1:
        return "ambiguous", cands, "promosi_belum_disebut"
    return "ambiguous", cands, "beberapa_bidak_bisa"


# -------------------------------------------------------------------- tes --
def _run_tests():
    P = lambda t: parse(t)
    cases = [
        # (teks, kind, piece, src, dst)
        ("Pion E2 ke E4", "move", "pawn", "e2", "e4"),
        ("Pindahkan pion dari E2 ke E4", "move", "pawn", "e2", "e4"),
        ("Pion dari E2 menuju E4", "move", "pawn", "e2", "e4"),
        ("pion e dua ke e empat", "move", "pawn", "e2", "e4"),
        ("kuda ge satu ke ef tiga", "move", "knight", "g1", "f3"),
        ("Pindahkan kuda G1 ke F3.", "move", "knight", "g1", "f3"),
        ("Kuda ke F3", "move", "knight", None, "f3"),
        ("Benteng a 1 ke a 3", "move", "rook", "a1", "a3"),
        ("Jalankan gajah dari C-1 ke H-6", "move", "bishop", "c1", "h6"),
        ("Kuda H7 makan D2", "move", "knight", "h7", "d2"),
        ("E2 ke E4", "move", None, "e2", "e4"),
        ("Pion E7 ke E8 jadi menteri", "move", "pawn", "e7", "e8"),
        ("Rokade pendek", "castle", None, None, None),
        ("Ya", "yes", None, None, None),
        ("Iya.", "yes", None, None, None),
        ("Tidak", "no", None, None, None),
        ("Batal", "cancel", None, None, None),
        ("Ulangi", "repeat", None, None, None),
        ("halo apa kabar", "unknown", None, None, None),
        ("Kuda", "unknown", None, None, None),
    ]
    fails = 0
    for text, kind, piece, src, dst in cases:
        r = P(text)
        got = (r.kind, r.piece, r.src, r.dst)
        want = (kind, piece, src, dst)
        ok = got == want
        fails += not ok
        print(("OK   " if ok else "GAGAL"), f"{text!r:42} -> {got}" + ("" if ok else f"  (harusnya {want})"))

    # perilaku khusus
    r = P("Pion E7 ke E8 jadi menteri")
    assert r.promotion == "queen", r
    r = P("peon e dua ke e empat")
    assert r.piece == "pawn" and r.needs_confirmation, r
    r = P("rokade")
    assert r.kind == "castle" and r.needs_confirmation, r
    assert P("Rokade panjang").castle_side == "long"
    assert normalize_for_wer("Pion E-2 ke ef empat!") == "pion e2 ke f4"
    print("OK    perilaku khusus (promosi, fuzzy, rokade, normalisasi WER)")

    # resolusi dengan python-chess
    try:
        import chess
    except ImportError:
        print("python-chess belum terpasang: tes resolve dilewati")
        return fails
    start = chess.Board()
    res_cases = [
        (start, "Kuda ke F3", "ok"),
        (start, "Pion ke E4", "ok"),
        (start, "Kuda ke C3", "ok"),
        (start, "Pion E2 ke E5", "illegal"),
        (start, "Kuda E2 ke E4", "mismatch"),
        (chess.Board("4k3/8/8/8/8/5N2/8/1N2K3 w - - 0 1"), "Kuda ke D2", "ambiguous"),
        (chess.Board("4k3/8/8/8/8/5N2/8/1N2K3 w - - 0 1"), "Kuda B1 ke D2", "ok"),
        (chess.Board("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"), "Rokade pendek", "ok"),
        (chess.Board("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"), "Rokade", "ambiguous"),
        (chess.Board("4k3/P7/8/8/8/8/8/4K3 w - - 0 1"), "Pion A7 ke A8 jadi menteri", "ok"),
        (chess.Board("4k3/P7/8/8/8/8/8/4K3 w - - 0 1"), "Pion A7 ke A8", "ambiguous"),
    ]
    for board, text, want in res_cases:
        status, moves, note = resolve(board, parse(text))
        ok = status == want
        fails += not ok
        mv = moves[0].uci() if len(moves) == 1 else f"{len(moves)} kandidat"
        print(("OK   " if ok else "GAGAL"), f"resolve {text!r:32} -> {status:9} {mv:12} {note}")
    print("\nSemua tes lulus." if not fails else f"\n{fails} tes gagal.")
    return fails


def _run_tests_v2():
    """Kasus dari salah dengar speaker tuning (S02, S06) + jaminan perilaku lama."""
    cases = [  # (teks Whisper, kind, piece, src, dst, harus_bertanya)
        ("Kuda G4 ke Halima.", "move", "knight", "g4", "h5", True),
        ("Gajah dari C7 menuju ati.", "move", "bishop", "c7", "a3", True),
        ("Benteng F5 ke DM4.", "move", "rook", "f5", "d4", True),
        ("Gajah C4. Makan C28.", "move", "bishop", "c4", "c8", True),
        ("Raja Bd8 makan B3.", "move", "king", "b8", "b3", True),
        ("Pindahkan menteri dari S1 ke G1.", "move", "queen", "e1", "g1", True),
        ("Gajah kuda B2 ke G2.", "move", "knight", "b2", "g2", True),
        ("Pion E5 ke E5.", "move", "pawn", "e5", "e5", True),
        ("Roka de panjang.", "castle", None, None, None, False),
        ("Roka dipendek.", "castle", None, None, None, False),
        ("Roka depan.", "castle", None, None, None, True),
        ("geser benting C7 ke A7", "move", "rook", "c7", "a7", True),
        ("Bindakan Raja dari E8 ke F2.", "move", "king", "e8", "f2", False),
        ("Tolong pindahkan guda G1 ke F3", "move", "knight", "g1", "f3", True),
        ("Batau.", "cancel", None, None, None, False),
        ("Ulani.", "repeat", None, None, None, False),
        ("Ulang E2.", "repeat", None, None, None, False),
        ("Raja D5 ke K3.", "unknown", "king", None, None, False),
        ("Benteng K3, makan D5.", "unknown", "rook", None, None, False),
        ("Benteng dari K5 ke E4.", "unknown", "rook", None, None, False),
        ("Raja dari E8-7B2.", "move", "king", "e8", "b2", False),
        ("Kuda ke F3", "move", "knight", None, "f3", False),
        ("Pion E2 ke E4", "move", "pawn", "e2", "e4", False),
        ("Menteri D1 ambil H5", "move", "queen", "d1", "h5", False),
    ]
    fails = 0
    for text, kind, piece, src, dst, ask in cases:
        r = parse(text)
        got = (r.kind, r.piece, r.src, r.dst, r.needs_confirmation)
        want = (kind, piece, src, dst, ask)
        if kind == "unknown":
            got, want = got[:1] + got[2:3], want[:1] + want[2:3]
        ok = got == want
        fails += not ok
        print(("OK   " if ok else "GAGAL"), f"v2 {text!r:38} -> {got}" + ("" if ok else f"  (harusnya {want})"))
    assert normalize_for_wer("Jalan kan pion E2 ke E4") == "jalankan pion e2 ke e4"
    assert normalize_for_wer("Pion E-2 ke ef empat!") == "pion e2 ke f4"
    return fails


if __name__ == "__main__":
    f = _run_tests()
    f += _run_tests_v2()
    print("\nSEMUA TES (v1 + v2) LULUS." if not f else f"\n{f} tes gagal.")
