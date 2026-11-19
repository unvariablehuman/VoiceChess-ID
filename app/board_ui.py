"""
VoiceChess-ID - Papan catur Pygame.

Fitur:
  * main dengan mouse (klik bidak, lalu klik petak tujuan; titik = langkah legal)
  * sorotan langkah terakhir, raja yang kena skak, dan langkah yang menunggu konfirmasi
  * panel kanan: giliran, transkripsi, pesan sistem, tombol Ya/Tidak saat konfirmasi,
    riwayat langkah
  * kotak ketik di bawah sebagai PENGGANTI SUARA sementara (Enter untuk kirim)
  * tombol: Papan baru, Balik papan (+ Koreksi/undo kalau ALLOW_UNDO = True di game_logic.py)

Jalankan (di laptop, bukan Colab):
    pip install pygame python-chess
    python board_ui.py
File yang harus satu folder: board_ui.py, game_logic.py, voicechess_parser.py

Untuk main.py nanti: BoardUI(controller, inbox=queue.Queue()). Thread ASR cukup memasukkan
("partial", teks) untuk transkripsi sementara dan ("final", teks) untuk perintah jadi.
"""
import os
import queue
import sys

import chess
import pygame

from game_logic import GameController

# ---------------------------------------------------------------- tampilan ----
MARGIN = 28                  # ruang label a-h / 1-8
PANEL_W = 380


def _fit_square():
    """Ukuran petak menyesuaikan layar. Di Windows dengan pembesaran tampilan (125-150%),
    jendela dibuat 'DPI aware' supaya tidak dibesarkan Windows (dan tidak buram)."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    os.environ.setdefault("SDL_VIDEO_CENTERED", "1")
    pygame.display.init()
    info = pygame.display.Info()
    fit_h = (info.current_h - 200) // 8          # sisakan ruang taskbar, judul, kotak ketik
    fit_w = (info.current_w - PANEL_W - MARGIN - 40) // 8
    return max(48, min(80, fit_h, fit_w))


SQ = _fit_square()           # ukuran satu petak (piksel), maks. 80
BOARD_PX = SQ * 8
W, H = MARGIN + BOARD_PX + PANEL_W, MARGIN + BOARD_PX + 80
FPS = 30

C_BG = (32, 34, 38)
C_LIGHT, C_DARK = (238, 216, 181), (181, 136, 99)
C_LAST = (246, 230, 110, 140)
C_SELECT = (110, 190, 110, 150)
C_PENDING = (90, 150, 240, 150)
C_CHECK = (225, 60, 60, 170)
C_DOT = (40, 40, 40, 90)
C_TEXT, C_MUTED = (235, 235, 235), (160, 165, 175)
C_BOX, C_BOX_EDGE = (48, 51, 58), (80, 84, 94)
C_YES, C_NO, C_BTN = (60, 150, 90), (180, 70, 70), (70, 76, 90)

GLYPH = {"K": "♔", "Q": "♕", "R": "♖", "B": "♗", "N": "♘", "P": "♙",
         "k": "♚", "q": "♛", "r": "♜", "b": "♝", "n": "♞", "p": "♟"}


def wrap(font, text, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if font.size(test)[0] <= width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


class Button:
    def __init__(self, rect, label, color, action):
        self.rect, self.label, self.color, self.action = pygame.Rect(rect), label, color, action

    def draw(self, surf, font, enabled=True):
        col = self.color if enabled else (60, 62, 68)
        pygame.draw.rect(surf, col, self.rect, border_radius=8)
        t = font.render(self.label, True, C_TEXT if enabled else C_MUTED)
        surf.blit(t, t.get_rect(center=self.rect.center))


class BoardUI:
    def __init__(self, controller, inbox=None, title="VoiceChess-ID", on_toggle_mic=None):
        pygame.init()
        pygame.display.set_caption(title)
        self.screen = pygame.display.set_mode((W, H))
        self.clock = pygame.time.Clock()
        self.gc = controller
        self.inbox = inbox            # queue.Queue berisi ("partial"/"final", teks)
        self.partial = ""             # transkripsi sementara dari ASR
        self.status = ""              # keadaan mikrofon/model dari ASR
        self.on_toggle_mic = on_toggle_mic
        self.flipped = False
        self.selected = None
        self.typed = ""

        self.f_small = pygame.font.SysFont("segoeui,arial,dejavusans", 16)
        self.f_norm = pygame.font.SysFont("segoeui,arial,dejavusans", 19)
        self.f_bold = pygame.font.SysFont("segoeui,arial,dejavusans", 21, bold=True)
        self.f_piece, self.use_glyph = self._piece_font()

        px = MARGIN + BOARD_PX + 20
        self.btn_yes = Button((px, 0, 160, 40), "Ya", C_YES, lambda: self.gc.confirm(True))
        self.btn_no = Button((px + 175, 0, 160, 40), "Tidak", C_NO, lambda: self.gc.confirm(False))
        by = MARGIN + BOARD_PX + 20
        self.btn_undo = Button((px, by, 105, 40), "Koreksi", C_BTN, self.gc.undo)
        self.btn_new = Button((px + 115, by, 105, 40), "Papan baru", C_BTN, self._new_game)
        self.btn_flip = Button((px + 230, by, 105, 40), "Balik", C_BTN, self._flip)

    # ------------------------------------------------------------ helper ----
    def _piece_font(self):
        """Pakai simbol catur Unicode kalau fontnya ada (Windows: Segoe UI Symbol)."""
        for name in ("segoeuisymbol", "dejavusans", "notosanssymbols2", "arialunicodems"):
            path = pygame.font.match_font(name)
            if path:
                f = pygame.font.Font(path, int(SQ * 0.8))
                if f.size("♔")[0] > SQ * 0.3:
                    return f, True
        return pygame.font.SysFont("arial", int(SQ * 0.45), bold=True), False

    def _new_game(self):
        self.gc.new_game()
        self.selected = None

    def _flip(self):
        self.flipped = not self.flipped

    def sq_to_xy(self, sq):
        f, r = chess.square_file(sq), chess.square_rank(sq)
        if self.flipped:
            f, r = 7 - f, 7 - r
        return MARGIN + f * SQ, (7 - r) * SQ

    def xy_to_sq(self, x, y):
        x -= MARGIN
        if not (0 <= x < BOARD_PX and 0 <= y < BOARD_PX):
            return None
        f, r = x // SQ, 7 - y // SQ
        if self.flipped:
            f, r = 7 - f, 7 - r
        return chess.square(f, r)

    def overlay(self, sq, rgba):
        s = pygame.Surface((SQ, SQ), pygame.SRCALPHA)
        s.fill(rgba)
        self.screen.blit(s, self.sq_to_xy(sq))

    # -------------------------------------------------------------- gambar ----
    def draw_board(self):
        b = self.gc.board
        for sq in chess.SQUARES:
            x, y = self.sq_to_xy(sq)
            light = (chess.square_file(sq) + chess.square_rank(sq)) % 2 == 1
            pygame.draw.rect(self.screen, C_LIGHT if light else C_DARK, (x, y, SQ, SQ))
        if self.gc.last_move:
            for sq in (self.gc.last_move.from_square, self.gc.last_move.to_square):
                self.overlay(sq, C_LAST)
        if self.gc.pending:
            for sq in (self.gc.pending.from_square, self.gc.pending.to_square):
                self.overlay(sq, C_PENDING)
        if b.is_check():
            self.overlay(b.king(b.turn), C_CHECK)
        if self.selected is not None:
            self.overlay(self.selected, C_SELECT)

        for sq, piece in b.piece_map().items():
            x, y = self.sq_to_xy(sq)
            cx, cy = x + SQ // 2, y + SQ // 2
            if self.use_glyph:
                g = GLYPH[piece.symbol()]
                fill = (255, 255, 255) if piece.color else (20, 20, 20)
                edge = (20, 20, 20)
                solid = GLYPH[piece.symbol().lower()]   # bentuk penuh dipakai sebagai isi
                body = self.f_piece.render(solid, True, fill)
                self.screen.blit(body, body.get_rect(center=(cx, cy + 2)))
                if piece.color:                         # bidak putih: tambah garis tepi
                    outline = self.f_piece.render(g, True, edge)
                    self.screen.blit(outline, outline.get_rect(center=(cx, cy + 2)))
            else:
                fill = (250, 250, 250) if piece.color else (25, 25, 25)
                pygame.draw.circle(self.screen, fill, (cx, cy), SQ // 2 - 8)
                pygame.draw.circle(self.screen, (90, 90, 90), (cx, cy), SQ // 2 - 8, 2)
                t = self.f_piece.render(piece.symbol().upper(), True,
                                        (25, 25, 25) if piece.color else (250, 250, 250))
                self.screen.blit(t, t.get_rect(center=(cx, cy)))

        if self.selected is not None:
            for sq in self.gc.legal_targets(self.selected):
                x, y = self.sq_to_xy(sq)
                s = pygame.Surface((SQ, SQ), pygame.SRCALPHA)
                pygame.draw.circle(s, C_DOT, (SQ // 2, SQ // 2), SQ // 7)
                self.screen.blit(s, (x, y))

        files = "abcdefgh"[::-1] if self.flipped else "abcdefgh"
        ranks = "12345678" if self.flipped else "87654321"
        for i in range(8):
            t = self.f_small.render(files[i], True, C_MUTED)
            self.screen.blit(t, t.get_rect(center=(MARGIN + i * SQ + SQ // 2, BOARD_PX + 14)))
            t = self.f_small.render(ranks[i], True, C_MUTED)
            self.screen.blit(t, t.get_rect(center=(MARGIN // 2, i * SQ + SQ // 2)))

    def text_box(self, x, y, w, title, body, font, min_lines=2, color=C_TEXT):
        self.screen.blit(self.f_small.render(title, True, C_MUTED), (x, y))
        lines = wrap(font, body, w - 20)
        h = max(min_lines, len(lines)) * (font.get_linesize()) + 16
        pygame.draw.rect(self.screen, C_BOX, (x, y + 22, w, h), border_radius=8)
        pygame.draw.rect(self.screen, C_BOX_EDGE, (x, y + 22, w, h), 1, border_radius=8)
        for i, line in enumerate(lines):
            self.screen.blit(font.render(line, True, color), (x + 10, y + 30 + i * font.get_linesize()))
        return y + 22 + h + 14

    def draw_panel(self):
        x, w = MARGIN + BOARD_PX + 20, PANEL_W - 40
        y = 14
        turn = self.gc.turn_text
        dot = (250, 250, 250) if self.gc.board.turn else (20, 20, 20)
        pygame.draw.circle(self.screen, dot, (x + 10, y + 13), 9)
        pygame.draw.circle(self.screen, C_MUTED, (x + 10, y + 13), 9, 1)
        self.screen.blit(self.f_bold.render(f"Giliran {turn}", True, C_TEXT), (x + 28, y))
        y += 44
        if self.status:
            mic = "Mikrofon: " + self.status
            self.screen.blit(self.f_small.render(mic[:48], True, C_MUTED), (x, y - 12))
            y += 14

        heard = self.partial if self.partial else self.gc.transcript
        title = "Transkripsi (sementara...)" if self.partial else "Transkripsi"
        y = self.text_box(x, y, w, title, heard or "-", self.f_norm,
                          color=C_MUTED if self.partial else C_TEXT)
        y = self.text_box(x, y, w, "Sistem", self.gc.message, self.f_norm, min_lines=3)

        if self.gc.pending is not None:
            self.btn_yes.rect.y = self.btn_no.rect.y = y
            self.btn_yes.draw(self.screen, self.f_bold)
            self.btn_no.draw(self.screen, self.f_bold)
            y += 54

        self.screen.blit(self.f_small.render("Riwayat", True, C_MUTED), (x, y))
        y += 22
        hist = self.gc.history
        room = max(1, (MARGIN + BOARD_PX - y) // 22)
        start = max(0, len(hist) - room)
        for i in range(start, len(hist)):
            no = i // 2 + 1
            who = "" if i % 2 else f"{no}."
            t = self.f_small.render(f"{who:>4} {hist[i]}", True, C_TEXT if i % 2 == 0 else C_MUTED)
            self.screen.blit(t, (x, y))
            y += 22

    def draw_bottom(self):
        y = MARGIN + BOARD_PX + 20
        rect = pygame.Rect(MARGIN, y, BOARD_PX, 40)
        pygame.draw.rect(self.screen, C_BOX, rect, border_radius=8)
        pygame.draw.rect(self.screen, C_BOX_EDGE, rect, 1, border_radius=8)
        if self.typed:
            show, col = self.typed, C_TEXT
        else:
            show, col = "Ketik perintah lalu Enter (pengganti suara), mis. Kuda G1 ke F3", C_MUTED
        cursor = "|" if self.typed and pygame.time.get_ticks() // 500 % 2 else ""
        self.screen.blit(self.f_norm.render(show + cursor, True, col), (rect.x + 12, rect.y + 9))
        if self.gc.allow_undo:                  # tombol Koreksi hanya tampil kalau undo aktif
            self.btn_undo.draw(self.screen, self.f_norm, enabled=self.gc.can_undo)
        self.btn_new.draw(self.screen, self.f_norm)
        self.btn_flip.draw(self.screen, self.f_norm)

    # -------------------------------------------------------------- input ----
    def on_click(self, pos):
        buttons = [self.btn_new, self.btn_flip] + ([self.btn_undo] if self.gc.allow_undo else [])
        for btn in buttons:
            if btn.rect.collidepoint(pos):
                btn.action(); self.selected = None
                return
        if self.gc.pending is not None:
            for btn in (self.btn_yes, self.btn_no):
                if btn.rect.collidepoint(pos):
                    btn.action()
                    return
        sq = self.xy_to_sq(*pos)
        if sq is None:
            return
        piece = self.gc.board.piece_at(sq)
        if self.selected is None:
            if piece and piece.color == self.gc.board.turn:
                self.selected = sq
        elif sq == self.selected:
            self.selected = None
        elif piece and piece.color == self.gc.board.turn:
            self.selected = sq                       # ganti pilihan
        else:
            if not self.gc.mouse_move(self.selected, sq) and not self.gc.board.is_game_over():
                self.gc.message = "Langkah itu tidak legal."
            self.selected = None

    def on_key(self, ev):
        if ev.key == pygame.K_RETURN:
            if self.typed.strip():
                self.gc.handle_text(self.typed)
            self.typed = ""
        elif ev.key == pygame.K_BACKSPACE:
            self.typed = self.typed[:-1]
        elif ev.key == pygame.K_F2 and self.on_toggle_mic:
            self.on_toggle_mic()
        elif ev.key == pygame.K_ESCAPE:
            self.typed, self.selected = "", None
        elif ev.unicode and ev.unicode.isprintable() and len(self.typed) < 60:
            self.typed += ev.unicode       # huruf dibaca langsung dari tombol yang ditekan

    def poll_inbox(self):
        if self.inbox is None:
            return
        try:
            while True:
                kind, text = self.inbox.get_nowait()
                if kind == "status":
                    self.status = text
                elif kind == "partial":
                    self.partial = text
                else:
                    self.partial = ""
                    self.gc.handle_text(text)
        except queue.Empty:
            pass

    # --------------------------------------------------------------- loop ----
    def run(self):
        pygame.key.set_repeat(400, 40)     # tahan Backspace untuk hapus beruntun
        while True:
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    pygame.quit()
                    return
                if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                    self.on_click(ev.pos)
                elif ev.type == pygame.KEYDOWN:
                    self.on_key(ev)
            self.poll_inbox()
            self.screen.fill(C_BG)
            self.draw_board()
            self.draw_panel()
            self.draw_bottom()
            pygame.display.flip()
            self.clock.tick(FPS)


if __name__ == "__main__":
    BoardUI(GameController()).run()
    sys.exit()
