"""
VoiceChess-ID - Logika permainan (tanpa tampilan).

Menerima teks perintah (dari keyboard sekarang, dari Whisper nanti), menjalankannya di
papan python-chess, dan mengelola dialog konfirmasi ya/tidak/batal + koreksi langkah (undo).
Dipakai oleh board_ui.py (tampilan) dan nanti main.py (mikrofon).

Uji tanpa layar:   python game_logic.py
Butuh: pip install python-chess   + voicechess_parser.py (v2 atau v2.1) di folder yang sama
"""
import chess

from voicechess_parser import parse, resolve

# Koreksi langkah (undo) hanya untuk SATU langkah terakhir. Keputusan tim: TIDAK dipakai.
# Kodenya disimpan untuk jaga-jaga; ganti ke True untuk menyalakan (butuh parser v2.1).
ALLOW_UNDO = False

NAMA = {chess.PAWN: "Pion", chess.KNIGHT: "Kuda", chess.BISHOP: "Gajah",
        chess.ROOK: "Benteng", chess.QUEEN: "Menteri", chess.KING: "Raja"}
NAMA_PROMO = {chess.QUEEN: "menteri", chess.ROOK: "benteng",
              chess.BISHOP: "gajah", chess.KNIGHT: "kuda"}
WARNA = {chess.WHITE: "Putih", chess.BLACK: "Hitam"}
PIECE_EN = {"pawn": chess.PAWN, "knight": chess.KNIGHT, "bishop": chess.BISHOP,
            "rook": chess.ROOK, "queen": chess.QUEEN, "king": chess.KING}


def uraikan(board, move):
    """Langkah -> kalimat Indonesia, mis. 'Kuda G1 ke F3'. Panggil SEBELUM board.push."""
    if board.is_castling(move):
        return "Rokade pendek" if board.is_kingside_castling(move) else "Rokade panjang"
    piece = board.piece_type_at(move.from_square)
    kata = "makan" if board.is_capture(move) else "ke"
    teks = (f"{NAMA[piece]} {chess.square_name(move.from_square).upper()} {kata} "
            f"{chess.square_name(move.to_square).upper()}")
    if move.promotion:
        teks += f" jadi {NAMA_PROMO[move.promotion]}"
    return teks


class GameController:
    def __init__(self, allow_undo=ALLOW_UNDO):
        self.allow_undo = allow_undo
        self.new_game()

    # ------------------------------------------------------------ status ----
    def new_game(self):
        self.board = chess.Board()
        self.pending = None        # langkah yang menunggu jawaban ya/tidak
        self.last_move = None
        self.can_undo = False      # hanya boleh setelah ada langkah baru
        self.transcript = ""
        self.history = []          # kalimat Indonesia tiap langkah
        self.message = "Giliran Putih. Ucapkan atau ketik perintah, mis. 'Pion E2 ke E4'."

    @property
    def turn_text(self):
        return WARNA[self.board.turn]

    def _push(self, move, via=""):
        desc = uraikan(self.board, move)
        self.board.push(move)
        self.last_move = move
        self.pending = None
        self.can_undo = True
        self.history.append(desc)
        self.message = f"{desc}{via}. " + self._status_after()

    def _status_after(self):
        b = self.board
        if b.is_checkmate():
            return f"Skakmat! {WARNA[not b.turn]} menang."
        if b.is_stalemate():
            return "Remis (pat)."
        if b.is_insufficient_material():
            return "Remis (bidak tidak cukup)."
        if b.can_claim_draw():
            return "Remis bisa diklaim (pengulangan / 50 langkah)."
        return f"Giliran {WARNA[b.turn]}." + (" Skak!" if b.is_check() else "")

    def _ask(self, move, why=""):
        self.pending = move
        self.message = f"Apakah maksud Anda {uraikan(self.board, move)}? (ya / tidak){why}"

    # ------------------------------------------------------------ masukan ----
    def handle_text(self, text):
        """Masukan utama: teks dari keyboard atau hasil Whisper. Return pesan sistem."""
        self.transcript = text.strip()
        if not self.transcript:
            return self.message
        if self.board.is_game_over():
            self.message = "Permainan sudah selesai. Tekan 'Papan baru' untuk mulai lagi."
            return self.message
        r = parse(self.transcript)

        # 1) sedang menunggu konfirmasi
        if self.pending is not None:
            if r.kind == "yes":
                self._push(self.pending, " (dikonfirmasi)")
            elif r.kind in ("no", "cancel"):
                self.pending = None
                self.message = "Dibatalkan. Silakan ucapkan perintah lagi."
            elif r.kind == "repeat":
                self._ask(self.pending)
            elif r.kind in ("move", "castle"):
                self.pending = None
                self._handle_move(r)          # perintah baru menggantikan yang lama
            else:
                self.message = (f"Jawab 'ya' atau 'tidak' untuk "
                                f"{uraikan(self.board, self.pending)}.")
            return self.message

        # 2) perintah biasa
        if r.kind == "undo":
            self.undo()
        elif r.kind in ("move", "castle"):
            self._handle_move(r)
        elif r.kind == "repeat":
            pass                               # pesan terakhir tetap ditampilkan
        elif r.kind in ("yes", "no", "cancel"):
            self.message = "Tidak ada langkah yang menunggu konfirmasi."
        else:
            self.message = self._explain_unknown(r)
        return self.message

    def _handle_move(self, r):
        status, moves, note = resolve(self.board, r)
        if status == "ok":
            if r.needs_confirmation:
                self._ask(moves[0])
            else:
                self._push(moves[0])
        elif status == "ambiguous":
            if note == "promosi_belum_disebut":
                self.message = ("Promosi jadi apa? Ucapkan lengkap, mis. "
                                f"'{uraikan(self.board, moves[0]).split(' jadi')[0]} jadi menteri'.")
            else:
                pilihan = sorted({uraikan(self.board, m) for m in moves})
                self.message = ("Ada beberapa kemungkinan: " + "; ".join(pilihan[:4]) +
                                ". Sebutkan petak asalnya.")
        elif status == "mismatch":
            self._handle_mismatch(r, note)
        elif status == "illegal":
            self.message = self._explain_illegal(r)
        else:
            self.message = "Perintah tidak dikenali sebagai langkah."

    def _handle_mismatch(self, r, note):
        """Bidak yang disebut beda dengan isi petak asal (sering salah dengar bidak).
        Kalau dengan bidak yang sebenarnya langkahnya legal, tawarkan konfirmasi."""
        src, dst = chess.parse_square(r.src), chess.parse_square(r.dst)
        cands = [m for m in self.board.legal_moves if m.from_square == src and m.to_square == dst]
        if r.promotion:
            cands = [m for m in cands if m.promotion == PIECE_EN[r.promotion]]
        if len(cands) == 1:
            real = NAMA[self.board.piece_type_at(src)]
            self._ask(cands[0], f" (di {r.src.upper()} ada {real.lower()})")
        else:
            real = self.board.piece_at(src)
            isi = NAMA[real.piece_type].lower() if real else "kosong"
            self.message = f"Di {r.src.upper()} ada {isi}, bukan {self._nama_en(r.piece)}."

    def _explain_illegal(self, r):
        if r.kind == "castle":
            return "Rokade tidak bisa dilakukan sekarang."
        if r.src:
            p = self.board.piece_at(chess.parse_square(r.src))
            if p is None:
                return f"Petak {r.src.upper()} kosong."
            if p.color != self.board.turn:
                return f"Bidak di {r.src.upper()} milik {WARNA[p.color]}; sekarang giliran {self.turn_text}."
        tujuan = r.dst.upper() if r.dst else "?"
        return f"Langkah ke {tujuan} tidak legal. Coba lagi."

    @staticmethod
    def _explain_unknown(r):
        if "tujuan_tidak_terbaca" in r.notes or "petak_tidak_terbaca" in r.notes:
            return "Petak tidak terdengar jelas. Silakan ulangi perintahnya."
        if "hanya_tujuan_tanpa_bidak" in r.notes:
            return "Bidak apa yang ingin dijalankan? Mis. 'Kuda ke F3'."
        return "Perintah tidak dikenali. Contoh: 'Pion E2 ke E4', 'Rokade pendek'."

    @staticmethod
    def _nama_en(piece_en):
        inv = {"pawn": "pion", "knight": "kuda", "bishop": "gajah", "rook": "benteng",
               "queen": "menteri", "king": "raja"}
        return inv.get(piece_en, "bidak itu")

    # -------------------------------------------------------- undo & mouse ----
    def undo(self):
        if not self.allow_undo:
            self.message = "Koreksi langkah (undo) dinonaktifkan."
        elif not self.board.move_stack or not self.can_undo:
            self.message = "Tidak ada langkah yang bisa dikoreksi (hanya satu langkah terakhir)."
        else:
            move = self.board.pop()
            desc = self.history.pop() if self.history else uraikan(self.board, move)
            self.pending = None
            self.can_undo = False
            self.last_move = self.board.peek() if self.board.move_stack else None
            self.message = f"Langkah '{desc}' dibatalkan. Giliran {self.turn_text}."
        return self.message

    def confirm(self, yes):
        """Untuk tombol Ya/Tidak di layar."""
        return self.handle_text("ya" if yes else "tidak")

    def legal_targets(self, from_sq):
        return {m.to_square for m in self.board.legal_moves if m.from_square == from_sq}

    def mouse_move(self, from_sq, to_sq):
        """Langkah lewat mouse. Promosi otomatis jadi menteri. Return True kalau berhasil."""
        if self.board.is_game_over():
            return False
        move = chess.Move(from_sq, to_sq)
        piece = self.board.piece_at(from_sq)
        if piece and piece.piece_type == chess.PAWN and chess.square_rank(to_sq) in (0, 7):
            move = chess.Move(from_sq, to_sq, promotion=chess.QUEEN)
        if move in self.board.legal_moves:
            self.transcript = "(mouse)"
            self._push(move)
            return True
        return False


# ------------------------------------------------------------------- tes ----
def _run_tests():
    fails = 0

    def check(label, cond):
        nonlocal fails
        fails += not cond
        print(("OK   " if cond else "GAGAL"), label)

    g = GameController(allow_undo=True)
    g.handle_text("Pion E2 ke E4")
    check("langkah biasa dijalankan", g.board.peek().uci() == "e2e4")
    g.handle_text("Pion E7 ke E5")
    g.handle_text("Kuda ke F3")
    check("tanpa petak asal", g.board.peek().uci() == "g1f3")
    g.handle_text("Kuda B8 ke C6")
    n = len(g.board.move_stack)
    g.handle_text("Gajah F1 ke Belima")           # v2: "belima" -> b5 hasil perbaikan -> ditanya
    check("petak hasil perbaikan -> bertanya", g.pending is not None and len(g.board.move_stack) == n)
    g.handle_text("tidak")
    check("'tidak' membatalkan", g.pending is None and len(g.board.move_stack) == n)
    g.handle_text("Gajah F1 ke C4")
    check("lanjut normal", g.board.peek().uci() == "f1c4")
    g.handle_text("Batal")
    check("'batal' tanpa pertanyaan tidak mengubah papan", g.board.peek().uci() == "f1c4")
    g.handle_text("Kuda E2 ke E4")                 # di e2 tidak ada apa-apa
    check("petak kosong ditolak", g.board.peek().uci() == "f1c4")
    g.handle_text("Kuda G8 ke F6")
    g.handle_text("Kuda F3 ke G5")
    g.handle_text("Pion D7 ke D5")
    g.handle_text("Pion ke D5")                    # e4xd5? ambigu? -> e4 pawn takes d5 (satu)
    check("pion makan tanpa asal", g.board.peek().uci() == "e4d5")
    n = len(g.board.move_stack)
    if parse("Batalkan langkah terakhir").kind == "undo":
        g.handle_text("Batalkan langkah terakhir")
        check("undo satu langkah", len(g.board.move_stack) == n - 1)
        g.handle_text("Batalkan langkah terakhir")
        check("undo kedua ditolak", len(g.board.move_stack) == n - 1)
    else:
        print("-    tes undo dilewati (parser v2 belum mengenal undo; pakai v2.1)")

    g2 = GameController()
    g2.handle_text("Pion E2 ke E4")
    g2.handle_text("Benteng E7 ke E5")             # di e7 ada pion -> tawarkan konfirmasi
    check("bidak salah sebut -> ditawari konfirmasi", g2.pending is not None
          and g2.pending.uci() == "e7e5")
    g2.handle_text("ya")
    check("'ya' menjalankan", g2.board.peek().uci() == "e7e5")

    g3 = GameController()
    for t in ["Pion F2 ke F3", "Pion E7 ke E5", "Pion G2 ke G4", "Menteri D8 ke H4"]:
        g3.handle_text(t)
    check("skakmat terdeteksi", g3.board.is_checkmate() and "Skakmat" in g3.message)

    g4 = GameController()
    check("mouse: langkah legal", g4.mouse_move(chess.E2, chess.E4))
    check("mouse: langkah ilegal ditolak", not g4.mouse_move(chess.E7, chess.E4))

    print("\nSemua tes logika lulus." if not fails else f"\n{fails} tes gagal.")
    return fails


if __name__ == "__main__":
    _run_tests()
