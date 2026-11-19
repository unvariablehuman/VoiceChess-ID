"""
VoiceChess-ID - Command parser (teks -> perintah catur).

Alur:  teks hasil ASR --normalisasi--> token --parse--> ParseResult
       ParseResult + papan python-chess --resolve--> langkah legal / ambigu / ilegal

Pakai:
    from voicechess_parser import parse, resolve, normalize_for_wer
    r = parse("kuda ge satu ke ef tiga")      # piece=kuda src=g1 dst=f3
    status, moves, note = resolve(board, r)   # butuh: pip install python-chess

Jalankan tes:  python voicechess_parser.py
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

PIECES = {
    "pion": "pawn", "pawn": "pawn",
    "kuda": "knight", "knight": "knight",
    "gajah": "bishop", "bishop": "bishop",
    "benteng": "rook", "rook": "rook",
    "menteri": "queen", "ratu": "queen", "queen": "queen",
    "raja": "king", "king": "king",
}

STOPWORDS = {
    "pindahkan", "geser", "jalankan", "langkah", "tolong", "dari", "menuju",
    "ke", "makan", "tangkap", "ambil", "jadi", "menjadi", "promosi",
    "promosikan", "bukan", "salah", "tidak", "batal", "ulangi", "rokade",
    "pendek", "panjang", "iya", "betul", "benar",
}
CAPTURE_WORDS = {"makan", "tangkap", "ambil"}
PROMO_WORDS = {"jadi", "menjadi", "promosi", "promosikan"}
YES = {"ya", "iya", "yap", "yoi", "betul", "benar", "oke", "ok", "setuju", "lanjut"}
NO = {"tidak", "nggak", "enggak", "gak", "ga", "bukan", "salah"}
CANCEL = {"batal", "cancel", "lupakan"}
REPEAT = {"ulangi", "ulang"}

SQUARE_RE = re.compile(r"^[a-h][1-8]$")


# ------------------------------------------------------------ normalisasi --
def _rank(tok: str) -> Optional[int]:
    if tok.isdigit() and 1 <= int(tok) <= 8 and len(tok) == 1:
        return int(tok)
    return NUMBER_WORDS.get(tok)


def tokenize(text: str) -> list:
    """Huruf kecil, buang tanda baca, pisahkan huruf dan angka ('e2' -> 'e','2')."""
    return re.findall(r"[a-z]+|\d+", text.lower())


def merge_squares(tokens: list) -> list:
    """Gabungkan 'e','dua' / 'ef','3' / 'ge','satu' menjadi satu token petak ('e2')."""
    out, i = [], 0
    while i < len(tokens):
        t = tokens[i]
        if t in LETTERS and i + 1 < len(tokens):
            r = _rank(tokens[i + 1])
            if r:
                out.append(f"{LETTERS[t]}{r}")
                i += 2
                continue
        out.append(t)
        i += 1
    return out


def normalize_for_wer(text: str) -> str:
    """Bentuk kanonik untuk menghitung WER: pakai di reference DAN hipotesis."""
    return " ".join(merge_squares(tokenize(text)))


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


def _find_piece(tokens: list, indices: list):
    """Cari bidak di posisi `indices`: exact dulu, lalu fuzzy. -> (piece, fuzzy?)"""
    for i in indices:
        if tokens[i] in PIECES:
            return PIECES[tokens[i]], False, i
    for i in indices:
        t = tokens[i]
        if len(t) >= 3 and t not in STOPWORDS and not SQUARE_RE.match(t):
            m = difflib.get_close_matches(t, PIECES.keys(), n=1, cutoff=0.7)
            if m:
                return PIECES[m[0]], True, i
    return None, False, None


def parse(text: str) -> ParseResult:
    tokens = merge_squares(tokenize(text))
    res = ParseResult(normalized=" ".join(tokens))
    squares = [t for t in tokens if SQUARE_RE.match(t)]
    tset = set(tokens)

    # 1) rokade
    if "rokade" in tset:
        res.kind = "castle"
        if "pendek" in tset:
            res.castle_side = "short"
        elif "panjang" in tset:
            res.castle_side = "long"
        else:
            res.confidence = "low"
            res.notes.append("sisi_rokade_tidak_disebut")
        return res

    # 2) kata kontrol (hanya jika tidak ada petak)
    if not squares:
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
    if len(squares) >= 2:
        res.src, res.dst = squares[0], squares[1]
        if len(squares) > 2:
            res.confidence = "low"
            res.notes.append("petak_lebih_dari_dua")
    else:
        res.dst = squares[0]  # "Kuda ke F3": asal harus ditebak dari papan

    # bagian sebelum / sesudah kata promosi
    promo_idx = next((i for i, t in enumerate(tokens) if t in PROMO_WORDS), None)
    before = list(range(promo_idx)) if promo_idx is not None else list(range(len(tokens)))
    after = list(range(promo_idx + 1, len(tokens))) if promo_idx is not None else []

    piece, fuzzy, _ = _find_piece(tokens, before)
    res.piece = piece
    if fuzzy:
        res.confidence = "low"
        res.notes.append("bidak_hasil_fuzzy")
    if promo_idx is not None:
        promo, pf, _ = _find_piece(tokens, after)
        res.promotion = promo
        if pf:
            res.confidence = "low"
            res.notes.append("promosi_hasil_fuzzy")

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


if __name__ == "__main__":
    _run_tests()
