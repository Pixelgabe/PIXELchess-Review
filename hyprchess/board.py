"""The board widget: draws whatever position its GameScreen is showing, at the largest size that fits."""

from functools import lru_cache

import chess
from rich.style import Style
from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget

from .skins import Skin

# Cell (width, height) in characters, smallest first. Widths 8, 10 and 12 use pixel sprites.
TIERS = [(3, 1), (7, 3), (8, 4), (10, 5), (12, 6)]


def board_size(tier: int) -> tuple[int, int]:
    cw, ch = TIERS[tier]
    return 2 + 8 * cw, 8 * ch + 1


def fit(width: int, height: int) -> int:
    """Largest tier whose board fits in width x height (smallest if none do)."""
    return max((i for i in range(len(TIERS)) if board_size(i)[0] <= width and board_size(i)[1] <= height), default=0)


@lru_cache(maxsize=None)
def _style(fg: str | None, bg: str | None) -> Style:
    return Style(color=fg, bgcolor=bg, bold=True)


def sprite_rows(skin: Skin, piece_type: int, color: chess.Color, size: int) -> list[Text]:
    """One piece as half-block text on a transparent background (for menus)."""
    pal, sprite, rows = skin.pieces[color], skin.sprites[size][piece_type], []
    for y in range(0, size, 2):
        row = Text()
        for t, b in zip(sprite[y], sprite[y + 1]):
            t, b = pal.get(t), pal.get(b)
            if t and b:
                row.append("▀" if t != b else "█", _style(t, b if t != b else None))
            else:
                row.append("▀" if t else "▄" if b else " ", _style(t or b, None))
        rows.append(row)
    return rows


class BoardView(Widget):
    tier = reactive(0)

    def watch_tier(self, tier: int) -> None:
        self.styles.width, self.styles.height = board_size(tier)

    def _bg(self, sq: int) -> str:
        g, skin = self.screen, self.app.skin
        light = (chess.square_file(sq) + chess.square_rank(sq)) % 2 == 1
        if sq == g.cursor and g.view is None and g.can_move:
            return skin.cursor
        if sq == g.selected:
            return skin.selected
        if sq in g.targets:
            return skin.target[light]
        if sq in g.hint:
            return skin.hint
        if g.shown.is_check() and sq == g.shown.king(g.shown.turn):
            return skin.checkmate if g.shown.is_checkmate() else skin.check
        if sq == getattr(g, "move_highlight", None) and getattr(g, "move_highlight_color", ""):
            return g.move_highlight_color
        if g.shown.move_stack and sq in (g.shown.peek().from_square, g.shown.peek().to_square):
            return skin.last[light]
        return skin.square[light]

    def render(self) -> Text:
        g, skin = self.screen, self.app.skin
        cw, ch = TIERS[self.tier]
        sprites, label = skin.sprites.get(cw), _style(skin.label, None)
        out = Text(no_wrap=True)
        for row in range(8):
            rank = row if g.flipped else 7 - row
            squares = [chess.square(7 - col if g.flipped else col, rank) for col in range(8)]
            cells = [(self._bg(sq), g.shown.piece_at(sq)) for sq in squares]
            for y in range(ch):
                out.append(f"{rank + 1} " if y == ch // 2 else "  ", label)
                for bg, piece in cells:
                    if not piece:
                        out.append(" " * cw, _style(bg, bg))
                    elif sprites:
                        pal = skin.pieces[piece.color]
                        top, bot = sprites[piece.piece_type][2 * y : 2 * y + 2]
                        for t, b in zip(top, bot):
                            t, b = pal.get(t, bg), pal.get(b, bg)
                            out.append(" " if t == b else "▀", _style(t, b))
                    else:
                        glyph = skin.glyphs[piece.piece_type] if y == ch // 2 else ""
                        out.append(f"{glyph:^{cw}}", _style(skin.pieces[piece.color]["X"], bg))
                out.append("\n")
        files = "hgfedcba" if g.flipped else "abcdefgh"
        out.append("  " + "".join(f"{f:^{cw}}" for f in files), label)
        return out

    # ---- mouse: everything happens on the press, so a move never waits for the button to come back up

    _was_selected = False  # the pressed piece was already picked up: releasing on it puts it down again

    def _square(self, event) -> int | None:
        g = self.screen
        cw, ch = TIERS[self.tier]
        col, row = (event.x - 2) // cw, event.y // ch
        if event.x >= 2 and 0 <= col < 8 and 0 <= row < 8:
            return chess.square(7 - col if g.flipped else col, row if g.flipped else 7 - row)
        return None

    def on_mouse_down(self, event) -> None:
        """Left press: play the picked-up piece here, or pick up the piece under the pointer. Other buttons cancel."""
        g = self.screen
        if event.button != 1:
            g.action_deselect()
        elif (sq := self._square(event)) is not None and g.can_move:
            g.cursor, self._was_selected = sq, sq == g.selected
            if not g.move_to(sq):
                g.pick(sq)
                self.capture_mouse()  # so a drag that ends off the board still reaches on_mouse_up

    def on_mouse_up(self, event) -> None:
        """Release on another square finishes a drag; release on a piece that was already picked up drops it."""
        g = self.screen
        self.release_mouse()
        if event.button != 1 or g.selected is None or not g.can_move:
            return
        sq = self._square(event)
        if sq == g.selected:
            if self._was_selected:
                g.action_deselect()
        elif sq in g.targets:
            g.cursor = sq
            g.move_to(sq)
