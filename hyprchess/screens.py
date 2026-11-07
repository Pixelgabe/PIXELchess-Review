"""Menus and dialogs: title screen, setup form, pop-up menus, help, online host/join."""

import asyncio
import getpass
import random
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import chess
import chess.engine
from rich.text import Text
from textual import events, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Container, Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen, Screen
from textual.widgets import Footer, Input, OptionList, Static
from textual.widgets.option_list import Option

from . import analysis, animation, net, sound
from .board import BoardView, board_size, fit, sprite_rows
from .game import CUSTOM_INCREMENTS, CUSTOM_MINUTES, PRESETS, TIME_PRESETS, Game, TimeControl
from .skins import PIECES

HELP = """\
[b]Moving[/b]
  arrows / hjkl   move the cursor
  enter / space   pick up a piece, then drop it       esc  put it back
  mouse click     same as cursor + enter
  Blue squares are the legal moves for the piece you picked up.

[b]Game[/b]
  u  undo your last move      r  redo it        i  hint from the engine
  \\[  easier    ]  harder      x  resign         b  back to the menu
  Undo, redo, hints and difficulty are off in online games.
  m  sound on or off    retro move sounds

[b]View[/b]
  ,  step back    .  step forward    Home  start    End  live
  f  flip the board     c  next skin     t  side panel on or off
  s  save the game as PGN
  You cannot move pieces while reviewing an earlier position.

[b]Review[/b]
  ,  .  Home  End  move through the game      a  analyse or stop
  f  flip the board      c  back to the menu    e  save analysis as JSON
  Click any move in the move list to jump to that position.
  Evals are from White's view and update as each position is analysed.
  Move labels: ★ best   ! excellent   ✓ good   ?! inaccuracy
               ? mistake   ?? blunder
  A move is compared with the engine's eval before and after it, using
  the same search settings. The number is what the move cost.

[b]Clocks[/b]
  Both clocks start once White has moved. The increment, if any, is
  added after each move. A clock at zero loses the game.

[b]Castling[/b]  (written O-O kingside, O-O-O queenside)
  Pick up the king and drop it two squares toward a rook; the rook
  jumps over to the king's other side. Allowed only if:
    - neither the king nor that rook has moved yet
    - every square between them is empty
    - the king is not in check, and does not cross or land on
      a square an enemy piece attacks

[b]En passant[/b]
  When an enemy pawn moves two squares from its start and lands
  right beside your pawn, you may capture it as if it had moved
  only one: your pawn goes diagonally to the empty square behind
  it and the enemy pawn is removed. Only on the very next move.

[b]Promotion[/b]
  A pawn reaching the last rank asks which piece it becomes.

[dim]Arrows scroll, any other key closes[/dim]"""

DIALOG_CSS = """
    {name} {{ align: center middle; }}
    {name} > Vertical, {name} > VerticalScroll {{
        width: {width}; max-width: 100%; height: auto; max-height: 100%;
        padding: 1 2; border: round $accent; background: $surface;
    }}
    {name} OptionList, {name} OptionList:focus {{
        height: auto; max-height: 16; border: none; background: $surface; padding: 0;
    }}
    {name} .title {{ text-style: bold; margin-bottom: 1; }}
    {name} .body {{ margin-bottom: 1; }}
"""


class HelpScreen(ModalScreen):
    DEFAULT_CSS = DIALOG_CSS.format(name="HelpScreen", width=76)

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(HELP)

    def on_key(self, event) -> None:
        if event.key in ("up", "down", "pageup", "pagedown"):
            return  # left to the scroll container
        event.stop()
        self.dismiss()

    def on_click(self) -> None:
        self.dismiss()


def _colour_name(color: chess.Color) -> str:
    return "White" if color == chess.WHITE else "Black"


class ResultScreen(ModalScreen[None]):
    """A read-only result card for a finished game."""

    DEFAULT_CSS = DIALOG_CSS.format(name="ResultScreen", width=52)
    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("enter", "close", "Close", show=False),
        Binding("space", "close", "Close", show=False),
    ]

    def __init__(self, heading: str, body: str) -> None:
        super().__init__()
        self.heading, self.body = heading, body

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.heading, classes="title")
            yield Static(self.body, classes="body")
            yield Static("[dim]Esc to continue[/dim]")

    def action_close(self) -> None:
        self.dismiss(None)

    def on_click(self) -> None:
        self.dismiss(None)


class MenuScreen(ModalScreen[str | None]):
    """A titled pop-up list. Dismisses with the chosen option id, or None on Esc."""

    DEFAULT_CSS = DIALOG_CSS.format(name="MenuScreen", width=46)
    BINDINGS = [Binding("j", "move(1)", show=False), Binding("k", "move(-1)", show=False)]

    def __init__(self, title: str, options: list[tuple[str, str]], body: str = "", escape: bool = True) -> None:
        super().__init__()
        self.heading, self.options, self.body, self.escape = title, options, body, escape

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.heading, classes="title")
            if self.body:
                yield Static(self.body, classes="body")
            yield OptionList(*(Option(label, id=key) for key, label in self.options))

    def action_move(self, delta: int) -> None:
        menu = self.query_one(OptionList)
        menu.action_cursor_down() if delta > 0 else menu.action_cursor_up()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def on_key(self, event) -> None:
        if event.key == "escape" and self.escape:
            event.stop()
            self.dismiss(None)


@dataclass
class Row:
    key: str
    label: str
    options: list[str]
    index: int = 0
    show: Callable[[dict], bool] | None = None  # given the current values, is this row visible?


class FormScreen(ModalScreen[dict | None]):
    """Setup form: up/down picks a row, left/right changes it. Dismisses with {key: option index}."""

    DEFAULT_CSS = DIALOG_CSS.format(name="FormScreen", width=52)
    BINDINGS = [
        Binding("up,k", "row(-1)", show=False),
        Binding("down,j", "row(1)", show=False),
        Binding("left,h", "change(-1)", show=False),
        Binding("right,l", "change(1)", show=False),
        Binding("enter", "submit", show=False),
        Binding("escape", "cancel", show=False),
    ]

    def __init__(self, title: str, rows: list[Row], submit: str = "start") -> None:
        super().__init__()
        self.heading, self.rows, self.submit, self.cursor = title, rows, submit, 0

    def values(self) -> dict[str, int]:
        return {r.key: r.index for r in self.rows}

    def visible(self) -> list[Row]:
        values = self.values()
        return [r for r in self.rows if r.show is None or r.show(values)]

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.heading, classes="title")
            yield Static(id="rows")

    def on_mount(self) -> None:
        self._draw()

    def _draw(self) -> None:
        out = Text()
        rows = self.visible()
        self.cursor = min(self.cursor, len(rows) - 1)
        for i, row in enumerate(rows):
            active = i == self.cursor
            out.append("▸ " if active else "  ", "bold")
            out.append(f"{row.label:<12}", "bold" if active else "")
            out.append(f"‹ {row.options[row.index]} ›\n", "reverse" if active else "")
        out.append(f"\n←/→ change · Enter {self.submit} · Esc back", "dim")
        self.query_one("#rows", Static).update(out)

    def action_row(self, delta: int) -> None:
        self.cursor = (self.cursor + delta) % len(self.visible())
        self._draw()

    def action_change(self, delta: int) -> None:
        row = self.visible()[self.cursor]
        row.index = (row.index + delta) % len(row.options)
        self._draw()

    def action_submit(self) -> None:
        self.dismiss(self.values())

    def action_cancel(self) -> None:
        self.dismiss(None)


def time_rows(saved: dict) -> list[Row]:
    def custom(values: dict) -> bool:
        return values["time"] == len(TIME_PRESETS)

    def at(key: str, options: list) -> int:
        return saved.get(key, 0) if isinstance(saved.get(key), int) and 0 <= saved[key] < len(options) else 0

    names = [name for name, _ in TIME_PRESETS] + ["Custom"]
    minutes, incs = [f"{m} min" for m in CUSTOM_MINUTES], [f"{s} sec" for s in CUSTOM_INCREMENTS]
    return [
        Row("time", "Time", names, at("time", names)),
        Row("minutes", "Per side", minutes, at("minutes", minutes), custom),
        Row("inc", "Increment", incs, at("inc", incs), custom),
    ]


def time_control(values: dict) -> TimeControl:
    if values["time"] < len(TIME_PRESETS):
        return TIME_PRESETS[values["time"]][1]
    return TimeControl(CUSTOM_MINUTES[values["minutes"]] * 60, CUSTOM_INCREMENTS[values["inc"]])


def pick_side(index: int) -> chess.Color:
    return [chess.WHITE, chess.BLACK, random.choice([chess.WHITE, chess.BLACK])][index]


SIDES = ["White", "Black", "Random"]


class JoinScreen(ModalScreen[str | None]):
    DEFAULT_CSS = DIALOG_CSS.format(name="JoinScreen", width=52)

    def __init__(self, last: str) -> None:
        super().__init__()
        self.last = last

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Join an online game", classes="title")
            yield Static("Address your opponent's Host screen shows:", classes="body")
            yield Input(value=self.last, placeholder=f"192.168.1.20 or host:{net.PORT}")
            yield Static("\n[dim]Enter connect · Esc back[/dim]")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)


class PgnPickerScreen(ModalScreen[str | None]):
    """A folder browser for selecting a PGN file.

    The list is navigable with the arrow keys, Enter opens a folder or selects a
    ``.pgn`` file, and the mouse works too.  A path can also be typed into the
    field at the top.  Esc cancels.  The starting directory defaults to the
    last-used folder stored in *config*["pgn_dir"], then ``~/Downloads``,
    ``~/Documents``, then home.
    """

    DEFAULT_CSS = DIALOG_CSS.format(name="PgnPickerScreen", width=64) + """
    PgnPickerScreen #dirlist { height: auto; max-height: 16; }
    PgnPickerScreen #hint { color: $text-muted; }
    """

    def __init__(self, start_dir: str = "") -> None:
        super().__init__()
        self.start_dir = start_dir
        home = Path.home()
        self.path = start_dir or next(
            (str(p) for p in (home / "Downloads", home / "Documents", home) if p.is_dir()),
            str(home),
        )
        self.selected: str | None = None
        self._entries: dict[str, tuple[str, Path]] = {}

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Input(value=self._display_path(self.path), placeholder="path", id="input")
            yield Static(f"Current: {self.path}", id="caption")
            yield OptionList(id="dirlist")
            yield Static(
                "[dim]↑/↓ move · Enter open · click to choose · Tab switches list/path · Esc cancel[/dim]",
                id="hint",
            )

    def on_mount(self) -> None:
        self._update_dirlist()
        self.query_one(OptionList).focus()

    @staticmethod
    def _display_path(p: str) -> str:
        return p.replace(str(Path.home()), "~", 1) if p.startswith(str(Path.home())) else p

    def _update_dirlist(self) -> None:
        """Rebuild the folder/file list and show the current path."""
        d = Path(self.path)
        self._entries = {}
        options: list[Option] = []

        if d.parent != d:
            self._entries["parent"] = ("parent", d.parent)
            options.append(Option("⬆  ..  (go up)", id="parent"))

        try:
            entries = sorted(d.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError:
            self._entries["error"] = ("error", d)
            options.append(Option("❌ Cannot read this folder", id="error"))
            entries = []

        for entry in entries:
            key = f"{'dir' if entry.is_dir() else 'file'}:{entry}"
            if entry.is_dir():
                self._entries[key] = ("dir", entry)
                options.append(Option(f"📁 {entry.name}", id=key))
            elif entry.is_file() and entry.suffix.lower() == ".pgn":
                self._entries[key] = ("file", entry)
                options.append(Option(f"📄 {entry.name}", id=key))

        if not options:
            options.append(Option("(no folders or .pgn files)", id="empty"))

        listing = self.query_one(OptionList)
        listing.clear_options()
        listing.add_options(options)
        listing.highlighted = 0
        self.query_one("#caption", Static).update(f"Current: {self.path}")
        self.query_one(Input).value = self._display_path(self.path)

    def _navigate(self, path: Path) -> None:
        self.path = str(path)
        self._update_dirlist()
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "dirlist" or event.option.id is None:
            return
        kind, path = self._entries.get(event.option.id, ("", Path()))
        if kind in ("dir", "parent"):
            self._navigate(path)
        elif kind == "file":
            self.selected = str(path)
            self.dismiss(self.selected)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        new_path = event.value.strip()
        if not new_path:
            return
        p = Path(new_path).expanduser()
        if not p.is_absolute():  # a bare name is relative to the folder being shown
            p = Path(self.path) / p
        if p.is_dir():
            self._navigate(p)
        elif p.is_file() and p.suffix.lower() == ".pgn":
            self.selected = str(p)
            self.dismiss(self.selected)
        else:
            self.notify("Not a valid PGN folder or .pgn file", severity="warning", timeout=3)

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)


class GameListScreen(ModalScreen[int | None]):
    """Shows a list of games found in a PGN file, with headers and notes.

    Dismisses with the selected game index, or ``None`` on Esc.
    """

    DEFAULT_CSS = DIALOG_CSS.format(name="GameListScreen", width=60)

    def __init__(self, games: list[tuple[Game, dict[str, str]]], notes: list[str]) -> None:
        super().__init__()
        self.games = games
        self.notes = notes
        self.selected_index: int | None = None

    def compose(self) -> ComposeResult:
        yield Static(f"Games in file ({len(self.games)} loadable)", id="header")
        if self.notes:
            yield Static("\n".join(self.notes), id="notes")
        yield OptionList(
            *(
                Option(
                    f"{i+1}. {h.get('White', '?')} – {h.get('Black', '?')}\n"
                    f"   Event: {h.get('Event', '?')}  |  Date: {h.get('Date', '?')}\n"
                    f"   Moves: {len(g.board.move_stack)}  |  Result: {h.get('Result', '?')}",
                    id=str(i),
                )
                for i, (g, h) in enumerate(self.games)
            )
        )

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(int(event.option.id))

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)


ANALYSIS_TIME = 0.6  # seconds per position, short enough to stay responsive
REVIEW_PANEL_W = 44
REVIEW_EVAL_W = 3

# A compact three-row block font.  Terminals cannot scale a font per line, so a
# classification is rendered three terminal rows tall to stand out without
# changing the size of everything else.
_BLOCK_FONT = {
    "A": ("▄▀▄", "█▀█", "▀ ▀"),
    "B": ("█▀▄", "█▀▄", "▀▀ "),
    "C": ("▄▀▀", "█  ", "▀▀▀"),
    "D": ("█▀▄", "█ █", "▀▀ "),
    "E": ("█▀▀", "█▀ ", "▀▀▀"),
    "F": ("█▀▀", "█▀ ", "▀  "),
    "G": ("▄▀▀", "█ █", "▀▀▀"),
    "H": ("█ █", "█▀█", "▀ ▀"),
    "I": ("▀█▀", " █ ", "▀▀▀"),
    "J": (" ▀█", "  █", "▀▀ "),
    "K": ("█ █", "█▀ ", "▀ ▀"),
    "L": ("█  ", "█  ", "▀▀▀"),
    "M": ("█▄█", "█ █", "▀ ▀"),
    "N": ("█ █", "█▄█", "▀ ▀"),
    "O": ("▄▀▄", "█ █", "▀▀▀"),
    "P": ("█▀▄", "█▀ ", "▀  "),
    "Q": ("▄▀▄", "█ █", "▀▀█"),
    "R": ("█▀▄", "█▀▄", "▀ ▀"),
    "S": ("▄▀▀", "▀▀▄", "▀▀ "),
    "T": ("▀█▀", " █ ", " ▀ "),
    "U": ("█ █", "█ █", "▀▀▀"),
    "V": ("█ █", "█ █", " ▀ "),
    "W": ("█ █", "█▄█", "▀ ▀"),
    "X": ("█ █", " ▄ ", "▀ ▀"),
    "Y": ("█ █", " █ ", " ▀ "),
    "Z": ("▀▀█", " ▄▀", "▀▀ "),
    " ": ("   ", "   ", "   "),
}

# Where the Black move starts on a move-list row: "  1. " + 7 SAN + 2 symbol + 1 gap.
MOVE_LIST_BLACK_X = 5 + 7 + 2 + 1


def block_label(value: str) -> Text:
    """Render *value* in the three-row block font."""
    rows = ["", "", ""]
    for char in value.upper():
        glyph = _BLOCK_FONT.get(char, _BLOCK_FONT[" "])
        for index in range(3):
            if rows[index]:
                rows[index] += " "
            rows[index] += glyph[index]
    return Text("\n".join(rows))


class MoveList(Static):
    """The compact move list; clicking a move jumps to that position."""

    class Selected(Message):
        """A move in the list was clicked."""

        def __init__(self, ply: int) -> None:
            super().__init__()
            self.ply = ply

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.rows: list[tuple[int, int | None]] = []

    def on_click(self, event: events.Click) -> None:
        if not 0 <= event.y < len(self.rows):
            return
        event.stop()
        white, black = self.rows[event.y]
        ply = black if black is not None and event.x >= MOVE_LIST_BLACK_X else white
        self.post_message(self.Selected(ply))


def local_player_names() -> tuple[str, ...]:
    """Names that identify the person at this machine in imported PGNs."""
    names = ["you"]
    try:
        login = getpass.getuser().strip().casefold()
    except (ImportError, OSError):
        login = ""
    if login:
        names.append(login)
    return tuple(names)


def _matches_local_name(value: str, known: tuple[str, ...]) -> bool:
    """True when a PGN player name looks like one of *known* local names."""
    value = (value or "").strip().casefold()
    if not value:
        return False
    for name in known:
        if value == name:
            return True
        # Online handles often append digits or separators to the same base,
        # e.g. a Linux login "pixelgabe" and a Chess.com handle "pixelgabe1".
        if len(name) >= 4 and value.startswith(name):
            suffix = value[len(name):]
            if suffix and all(ch.isdigit() or ch in "_-." for ch in suffix):
                return True
    return False


def played_colour(headers: dict[str, str]) -> chess.Color:
    """Infer the side the local player had, defaulting to White when unknown.

    Saved hyprchess PGNs put ``You`` in the White or Black header.  Imported
    PGNs usually use the player's account name, so the local login name and
    close handle variants are recognised too.  When both names match, or
    neither does, the board stays White.
    """
    known = local_player_names()
    white_local = _matches_local_name(headers.get("White") or "", known)
    black_local = _matches_local_name(headers.get("Black") or "", known)
    if black_local and not white_local:
        return chess.BLACK
    return chess.WHITE


class ReviewScreen(Screen):
    """Dedicated read-only review interface for PGN games.

    Shows the chessboard, move list with SAN notation, game information, an
    engine-evaluation panel and a small evaluation bar.  All actions are
    read-only; pieces cannot be moved.  Use the keyboard to navigate moves,
    flip the board, or return to the title screen.

    Evaluations come from the app's configured UCI engine.  They are always
    shown from White's perspective and are never invented: when no engine is
    available the panel says so instead of displaying a number.  The board
    starts from the side the local player had when the PGN records it.
    """

    BINDINGS = [
        Binding("comma", "review(-1)", "Previous"),
        Binding("full_stop", "review(1)", "Next"),
        Binding("home", "review(-9999)", "Start"),
        Binding("end", "review(9999)", "End"),
        Binding("a", "analyse", "Analyse/stop"),
        Binding("e", "export", "Export JSON"),
        Binding("f", "flip", "Flip"),
        Binding("c", "back", "Menu"),
        Binding("question_mark", "help", "Help"),
        Binding("q", "quit", "Quit"),
    ]

    DEFAULT_CSS = """
    ReviewScreen { align: center middle; }
    ReviewScreen #main { width: auto; height: auto; }
    ReviewScreen #boardrow { width: auto; height: auto; layout: horizontal; }
    ReviewScreen #panel { padding: 0 1; }
    ReviewScreen #evalbar { width: 3; height: auto; }
    ReviewScreen #move-badge { height: auto; }
    ReviewScreen #moves { height: 1fr; }
    """

    def __init__(self, game: Game, headers: dict[str, str], move_count: int) -> None:
        super().__init__()
        self.game = game
        self.headers = headers
        self.move_count = move_count
        self.view: int | None = 0  # ply being reviewed (0 = starting position)
        self.flipped = played_colour(headers) == chess.BLACK
        self.anim_board: chess.Board | None = None  # reviewed board while a move animation is running
        # Attributes BoardView reads from its screen.
        self.cursor = chess.E7 if self.flipped else chess.E2
        self.selected: int | None = None
        self.targets: set[int] = set()
        self.hint: set[int] = set()
        self.shown = game.at(0)
        # Engine/eval state.
        self.analysing = False
        self.engine_error = ""
        self.evals: dict[int, analysis.PositionEval] = {}
        self.reports: dict[int, analysis.MoveReport] = {}
        self.engine_info: dict | None = None
        self.engine_configuration: dict | None = None
        self.scan_done = 0
        self.scan_total = len(game.board.move_stack) + 1
        self._analysis_id = 0
        self._analysis_worker = None
        self._result_shown = False
        self.move_highlight: int | None = None
        self.move_highlight_color = ""
        self.stacked = False

    @property
    def can_move(self) -> bool:
        """Review is always read-only."""
        return False

    # ---- layout

    def compose(self) -> ComposeResult:
        with Container(id="main"):
            with Container(id="boardrow"):
                yield BoardView()
                yield Static(id="evalbar")
            with Vertical(id="panel"):
                yield Static(id="game-info", classes="title")
                yield Static(id="eval-text")
                yield Static(id="move-badge")
                with VerticalScroll(id="moves"):
                    yield MoveList(id="move-list")
                yield Static(id="move-summary")
        yield Footer()

    def on_mount(self) -> None:
        self._layout()
        self._refresh()
        self._start_analysis()

    def on_resize(self) -> None:
        self._layout()

    def on_move_list_selected(self, message: MoveList.Selected) -> None:
        """Jump to the move the user clicked."""
        previous = self._current_ply()
        self.view = message.ply
        self._refresh()
        n = len(self.game.board.move_stack)
        if self.view is not None and self.view > previous and self.view > 0 and self.view <= n:
            self._play_review_move(self.view)

    def on_screen_resume(self) -> None:
        self._layout()

    def on_unmount(self) -> None:
        self.anim_board = None
        self._stop_analysis()

    def _layout(self) -> None:
        """Put the panel beside the board, or below it on narrow terminals."""
        w, h = self.size.width, self.size.height - 1  # footer
        side = fit(w - REVIEW_PANEL_W - REVIEW_EVAL_W, h)
        below = fit(w - REVIEW_EVAL_W, max(h - 9, 1))  # the bar stays beside the board
        self.stacked = below > side or w < board_size(0)[0] + REVIEW_EVAL_W + REVIEW_PANEL_W
        board = self.query_one(BoardView)
        board.tier = below if self.stacked else side
        board_w, board_h = board_size(board.tier)
        self.query_one("#main").styles.layout = "vertical" if self.stacked else "horizontal"
        panel = self.query_one("#panel")
        panel.styles.width = board_w if self.stacked else REVIEW_PANEL_W
        panel.styles.height = max(h - board_h, 3) if self.stacked else board_h
        bar = self.query_one("#evalbar")
        bar.display = True
        bar.styles.height = board_h
        self._refresh()

    # ---- drawing

    def _current_ply(self) -> int:
        n = len(self.game.board.move_stack)
        return min(max(self.view if self.view is not None else n, 0), n)

    def _refresh(self) -> None:
        ply = self._current_ply()
        self.shown = self.anim_board if self.anim_board is not None and ply == self.view else self.game.at(ply)
        self._refresh_info()
        self._refresh_moves(ply)
        self._refresh_eval(ply)
        self._refresh_move_highlight(ply)
        self.query_one(BoardView).refresh()
        self._maybe_show_checkmate(ply)

    def _refresh_move_highlight(self, ply: int) -> None:
        """Tint the square a move landed on with its classification colour."""
        self.move_highlight = None
        self.move_highlight_color = ""
        if ply <= 0:
            return
        report = self.reports.get(ply)
        if report is None or report.classification is analysis.UNKNOWN:
            return
        self.move_highlight = self.game.board.move_stack[ply - 1].to_square
        self.move_highlight_color = report.classification.color

    def _refresh_info(self) -> None:
        h = self.headers
        white = h.get("White") or "White"
        black = h.get("Black") or "Black"
        event = h.get("Event") or "?"
        date = h.get("Date") or "?"
        result = h.get("Result") or "*"
        plies = len(self.game.board.move_stack)
        info = f"[b]{white}[/b] vs [b]{black}[/b]\n{event}  ·  {date}\nResult: {result}  ·  {plies} plies"
        if self.shown.is_checkmate():
            winner = not self.shown.turn
            loser = self.shown.turn
            info += (
                f"\n[bold red]Checkmate[/bold red] — {_colour_name(winner)} wins, "
                f"{_colour_name(loser)} loses"
            )
        self.query_one("#game-info", Static).update(info)

    def _maybe_show_checkmate(self, ply: int) -> None:
        """Show the winner/loser card once, when the review reaches a checkmated final position."""
        if self._result_shown or not self.game.board.is_checkmate():
            return
        if ply != len(self.game.board.move_stack):
            return
        self._result_shown = True
        self.call_after_refresh(self._show_checkmate)

    def _show_checkmate(self) -> None:
        if not self.is_attached:
            return
        board = self.game.board
        winner, loser = not board.turn, board.turn
        result = self.headers.get("Result")
        if result not in ("1-0", "0-1"):
            result = "1-0" if winner == chess.WHITE else "0-1"
        self.app.push_screen(
            ResultScreen(
                "Checkmate",
                f"[bold]{_colour_name(winner)} wins[/bold]\n"
                f"{_colour_name(loser)} is checkmated.\n\n{result}  ·  checkmate",
            )
        )

    def _refresh_moves(self, ply: int) -> None:
        sans, _ = self.game.history()
        text = Text()
        rows: list[tuple[int, int | None]] = []
        current = ply - 1 if ply > 0 else None
        for i in range(0, len(sans), 2):
            text.append(f"{i // 2 + 1:>3}. ", "dim")
            for j in range(i, min(i + 2, len(sans))):
                report = self.reports.get(j + 1)
                symbol = report.classification.symbol if report else " "
                style = report.classification.color if report else ""
                if j == current:
                    style = f"reverse bold {style}".strip()
                text.append(f"{sans[j]:<7}", style)
                text.append(f"{symbol:<2} ", style)
            text.append("\n")
            rows.append((i + 1, i + 2 if i + 1 < len(sans) else None))
        if not sans:
            text.append("(no moves)", "dim")
        listing = self.query_one("#move-list", MoveList)
        listing.rows = rows
        listing.update(text)
        self._refresh_summary()
        pair = max(0, (current or 0) // 2)
        moves = self.query_one("#moves", VerticalScroll)
        self.call_after_refresh(lambda: moves.scroll_to(y=max(pair - 2, 0), animate=False))

    def _refresh_badge(self, ply: int) -> None:
        """Show the current move's label as a large, three-row block banner."""
        badge = self.query_one("#move-badge", Static)
        report = self.reports.get(ply) if ply > 0 else None
        if report is None or report.classification is analysis.UNKNOWN:
            badge.display = False
            badge.update("")
            return
        badge.display = True
        text = block_label(report.classification.label)
        text.stylize(f"bold {report.classification.color}")
        badge.update(text)

    def _refresh_summary(self) -> None:
        """Show totals for every move classification under the move list."""
        counts = {classification: 0 for classification in analysis.CLASSIFICATIONS}
        unknown = 0
        for report in self.reports.values():
            if report.classification is analysis.UNKNOWN:
                unknown += 1
            else:
                counts[report.classification] += 1

        text = Text()
        text.append("Totals", "bold")
        if not self.reports:
            text.append("\n")
            text.append("No moves classified yet", "dim")
        else:
            rows = (
                (analysis.BEST, analysis.EXCELLENT),
                (analysis.GOOD, analysis.INACCURACY),
                (analysis.MISTAKE, analysis.BLUNDER),
            )
            for index, (left, right) in enumerate(rows):
                text.append("\n" if index else "  ")
                for column, classification in enumerate((left, right)):
                    if column:
                        text.append("   ")
                    text.append(f"{classification.symbol} {classification.label} ", classification.color)
                    text.append(str(counts[classification]), "bold")
            if unknown:
                text.append("\n")
                text.append(f"{analysis.UNKNOWN.symbol} Unknown {unknown}", "dim")
        self.query_one("#move-summary", Static).update(text)

    def _eval_cell(self, ply: int) -> str:
        pe = self.evals.get(ply)
        return analysis.format_advantage(pe.score, chess.WHITE) if pe else "…"

    def _refresh_eval(self, ply: int) -> None:
        self._refresh_badge(ply)
        text = self.query_one("#eval-text", Static)
        if self.engine_error:
            text.update(f"[red]Engine unavailable[/red]\n{self.engine_error}")
            self._refresh_bar(None)
            return

        pe = self.evals.get(ply)
        if pe is None:
            if self.analysing:
                text.update(f"[yellow]Analysing {self.scan_done}/{self.scan_total}…[/yellow]")
            else:
                text.update("[dim]Not analysed yet — press a to analyse[/dim]")
            self._refresh_bar(None)
            return

        score = pe.score
        parts = [f"[b]{analysis.describe_score(score, chess.WHITE)}[/b]"]
        detail = f"{analysis.format_score(score, chess.WHITE)} from White's view"
        if pe.depth is not None:
            detail += f"  ·  depth {pe.depth}"
        parts.append(f"[dim]{detail}[/dim]")
        if ply > 0 and (report := self.reports.get(ply)) is not None:
            c = report.classification
            line = f"[dim]Move {report.san}[/dim]  [{c.color}][b]{c.symbol}[/b][/]"
            if report.loss_cp:
                line += f"  [dim]·  {report.loss_cp / 100:.2f} lost[/dim]"
            parts.append(line)
        if pe.pv_san:
            parts.append(f"Best: [b]{pe.best_san}[/b]")
            rest = pe.pv_san[len(pe.best_san):].strip()
            if rest:
                parts.append(f"PV: {pe.pv_san}")
        previous = self.evals.get(ply - 1)
        delta = analysis.score_delta(previous.score if previous else None, score, chess.WHITE)
        if delta is not None:
            parts.append(f"[dim]Δ since previous ply: {delta}[/dim]")
        if self.analysing:
            parts.append(f"[yellow]Analysing {self.scan_done}/{self.scan_total}…[/yellow]")
        text.update("\n".join(parts))
        self._refresh_bar(score)

    def _refresh_bar(self, score: chess.engine.PovScore | None) -> None:
        bar = self.query_one("#evalbar", Static)
        from rich.style import Style

        height = board_size(self.query_one(BoardView).tier)[1]
        fraction = analysis.score_fraction(score, chess.WHITE)
        white_rows = round(fraction * height)
        text = Text()
        white = Style(bgcolor=self.app.skin.pieces[chess.WHITE]["X"])
        black = Style(bgcolor=self.app.skin.pieces[chess.BLACK]["X"])
        for row in range(height):
            text.append("   ", white if row >= height - white_rows else black)
            if row < height - 1:
                text.append("\n")
        bar.update(text)

    # ---- engine evaluation

    def _start_analysis(self) -> None:
        self.evals.clear()
        self.reports.clear()
        self.engine_info = None
        self.engine_configuration = None
        self._refresh_move_highlight(self._current_ply())
        self._refresh_badge(self._current_ply())
        self.query_one(BoardView).refresh()
        self._analysis_id += 1
        self._analysis_worker = self.analyse_game(self._analysis_id)

    def _stop_analysis(self) -> None:
        self._analysis_id += 1
        if self._analysis_worker is not None:
            self._analysis_worker.cancel()
            self._analysis_worker = None
        self.analysing = False

    @work(exclusive=True, group="move-review-animation")
    async def _animate_review(self, destination: int, after: chess.Board, segments: list[tuple]) -> None:
        """Hop through intermediate squares towards a reviewed move, then hand back."""
        steps = max(len(segment[3]) for segment in segments) if segments else 1
        for index in range(steps):
            if not self.is_attached or self.view != destination:
                self.anim_board = None
                return
            self.anim_board = after if index == steps - 1 else animation.frame_board(after, segments, index)
            if not self.is_attached:
                return
            self.query_one(BoardView).refresh()
            await asyncio.sleep(animation.MOVE_ANIMATION_DELAY)
        if not self.is_attached or self.view != destination:
            self.anim_board = None
            return
        self.anim_board = None
        self._refresh()

    def _play_review_move(self, ply: int) -> None:
        """Animate and sound the move that leads into the position at *ply*."""
        move = self.game.board.move_stack[ply - 1]
        before = self.game.at(ply - 1)
        after = self.game.at(ply)
        if after.is_checkmate():
            kind = "mate"
        elif before.is_capture(move):
            kind = "capture"
        elif after.is_check():
            kind = "check"
        else:
            kind = "move"
        if bool(self.app.config.get("sound", True)):
            sound.play_sound(kind)
        segments = animation.animation_segments(move, before)
        if not segments:
            return
        self.anim_board = animation.frame_board(after, segments, 0)
        self.query_one(BoardView).refresh()
        self._animate_review(ply, after, segments)

    @work(exclusive=True, group="review-eval", exit_on_error=False)
    async def analyse_game(self, request_id: int) -> None:
        """Analyse every position in the background, one consistent pass over the game."""
        self.analysing = True
        self.scan_done = 0
        self.engine_error = ""
        self._refresh_eval(self._current_ply())
        self._refresh_moves(self._current_ply())
        try:
            engine = await self.app.get_engine()
            if engine is None:
                self.engine_error = self.app.engine_error or "No engine available"
                return
            options = analysis.analysis_options(engine)
            self.engine_info = analysis.engine_info(engine)
            self.engine_configuration = analysis.engine_configuration(
                engine, time_seconds=ANALYSIS_TIME
            )
            sans, _ = self.game.history()
            for ply in range(self.scan_total):
                if request_id != self._analysis_id or not self.is_attached:
                    return
                board = self.game.at(ply)
                info = await engine.analyse(
                    board,
                    chess.engine.Limit(time=ANALYSIS_TIME),
                    options=options,
                )
                if request_id != self._analysis_id or not self.is_attached:
                    return
                position = analysis.PositionEval.from_info(ply, board, info)
                self.evals[ply] = position
                if ply > 0 and (before := self.evals.get(ply - 1)) is not None:
                    played = self.game.board.move_stack[ply - 1]
                    self.reports[ply] = analysis.classify_move(
                        ply=ply,
                        san=sans[ply - 1],
                        played=played,
                        before=before,
                        after=position,
                    )
                self.scan_done = ply + 1
                current = self._current_ply()
                self._refresh_moves(current)
                if ply == current:
                    self._refresh_eval(current)
                    self._refresh_move_highlight(current)
                    self.query_one(BoardView).refresh()
            self.scan_done = self.scan_total
        except (chess.engine.EngineError, OSError, RuntimeError) as e:
            self.app.engine = None
            self.engine_error = f"{self.app.engine_name} stopped: {e}"
        finally:
            if request_id == self._analysis_id:
                self.analysing = False
                if self.is_attached:
                    current = self._current_ply()
                    self._refresh_moves(current)
                    self._refresh_eval(current)
                    self._refresh_move_highlight(current)
                    self.query_one(BoardView).refresh()

    # ---- actions

    # The board widget calls these while handling the mouse; review is read-only.
    def action_deselect(self) -> None:
        pass

    def pick(self, square: int) -> None:
        pass

    def move_to(self, square: int) -> bool:
        return False

    def action_review(self, delta: int) -> None:
        n = len(self.game.board.move_stack)
        previous = self._current_ply()
        self.view = min(max(self._current_ply() + delta, 0), n)
        self._refresh()
        if self.view is not None and self.view > previous and self.view > 0 and self.view <= n:
            self._play_review_move(self.view)

    def action_analyse(self) -> None:
        """Start a full-game analysis pass, or stop the one that is running."""
        if self.analysing:
            self._stop_analysis()
            self._refresh()
        else:
            self._start_analysis()

    def analysis_report(self) -> analysis.GameAnalysis:
        """A structured report for the review UI and any future coach."""
        return analysis.GameAnalysis.from_game(
            self.game,
            self.headers,
            self.evals,
            self.reports,
            engine_name=self.app.engine_name,
            seconds_per_position=ANALYSIS_TIME,
            engine_info=self.engine_info,
            configuration=self.engine_configuration,
        )

    def export_analysis(self, path: Path | None = None) -> Path:
        """Write the structured analysis as JSON; returns the path written."""
        report = self.analysis_report()
        if path is None:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            white = self.headers.get("White") or "white"
            black = self.headers.get("Black") or "black"
            slug = "".join(ch if ch.isalnum() else "-" for ch in f"{white}-{black}")
            slug = "-".join(part for part in slug.split("-") if part)[:40] or "game"
            path = Path(self.app.data_dir) / f"analysis-{slug}-{stamp}.json"
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report.to_json() + "\n", encoding="utf-8")
        return path

    def action_export(self) -> None:
        try:
            path = self.export_analysis()
        except OSError as e:
            self.notify(f"Could not save analysis: {e}", severity="error")
            return
        self.notify(f"Analysis saved to {path}", timeout=8)

    def action_flip(self) -> None:
        self.flipped = not self.flipped
        self._refresh()

    def action_back(self) -> None:
        self._stop_analysis()
        self.app.pop_screen()

    def action_help(self) -> None:
        self.app.push_screen(HelpScreen())

    def action_quit(self) -> None:
        self.app.exit()


class HostScreen(ModalScreen["net.Peer | None"]):
    """Listens for one opponent. Dismisses with the connected Peer, or None on Esc."""

    DEFAULT_CSS = DIALOG_CSS.format(name="HostScreen", width=56)

    def __init__(self, port: int = net.PORT) -> None:
        super().__init__()
        self.port, self.server = port, None

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Hosting an online game", classes="title")
            yield Static("Starting…", id="status")
            yield Static("\n[dim]Esc cancel[/dim]")

    async def on_mount(self) -> None:
        status = self.query_one("#status", Static)
        try:
            self.server = await net.host(self.port, self._connected)
        except OSError as e:
            status.update(f"Could not listen on port {self.port}: {e.strerror or e}")
            return
        addresses = "\n".join(f"  {a}:{self.port}" for a in net.local_addresses())
        status.update(
            f"Waiting for an opponent. Tell them to choose Join and enter:\n\n{addresses}\n\n"
            "Works on the same network or over a VPN such as Tailscale.\n"
            f"Across the internet, forward TCP port {self.port} to this machine."
        )

    def _connected(self, peer: net.Peer) -> None:
        if self.server is None:  # second connection, or we already left
            peer.close()
            return
        self._stop()
        self.dismiss(peer)

    def _stop(self) -> None:
        if self.server:
            self.server.close()
            self.server = None

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self._stop()
            self.dismiss(None)


class TitleScreen(Screen):
    DEFAULT_CSS = """
    TitleScreen { align: center middle; }
    TitleScreen Center { height: auto; }
    TitleScreen #art { width: auto; height: auto; }
    TitleScreen #name { width: auto; text-style: bold; margin-top: 1; }
    TitleScreen #tag { width: auto; color: $text-muted; margin-bottom: 1; }
    TitleScreen OptionList, TitleScreen OptionList:focus {
        width: 44; max-width: 100%; height: auto; border: round $accent; padding: 0 1; background: $surface;
    }
    """
    BINDINGS = [
        Binding("j", "move(1)", show=False),
        Binding("k", "move(-1)", show=False),
        Binding("left,h", "skin(-1)", show=False),
        Binding("right,l", "skin(1)", show=False),
        Binding("question_mark", "help", "Help"),
        Binding("q", "app.quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Center(Static(id="art"))
        yield Center(Static("H Y P R C H E S S", id="name"))
        yield Center(Static("Chess in your terminal", id="tag"))
        yield Center(self._menu())
        yield Footer()

    def _menu(self) -> OptionList:
        return OptionList(
            Option("Play the computer", id="engine"),
            Option("Two players, this keyboard", id="local"),
            Option("Online: host a game", id="host"),
            Option("Online: join a game", id="join"),
            Option("Resume last game", id="resume"),
            Option("Review a game", id="pgn"),
            Option("", id="skin"),
            Option("Help", id="help"),
            Option("Quit", id="quit"),
        )

    def on_mount(self) -> None:
        self._sync()

    def on_screen_resume(self) -> None:
        self._sync()

    def on_resize(self) -> None:
        self._sync()

    def _sync(self) -> None:
        """Redraw everything that depends on the skin, the window size or saved state."""
        app, menu = self.app, self.query_one(OptionList)
        w, h = self.size
        size = 12 if (w >= 84 and h >= 28) else 8 if (w >= 58 and h >= 22) else 0
        art = self.query_one("#art", Static)
        art.display = bool(size)
        if size:
            pieces = [sprite_rows(app.skin, pt, chess.WHITE, size) for pt in PIECES]
            art.update(Text("\n").join(Text(" ").join(rows) for rows in zip(*pieces)))
        self.query_one("#tag").display = h >= 16
        menu.replace_option_prompt("skin", f"Skin: ‹ {app.skin.name} ›")
        menu.enable_option("resume") if app.load_state() else menu.disable_option("resume")

    def action_move(self, delta: int) -> None:
        menu = self.query_one(OptionList)
        menu.action_cursor_down() if delta > 0 else menu.action_cursor_up()

    def action_skin(self, delta: int) -> None:
        menu = self.query_one(OptionList)
        if menu.highlighted is not None and menu.get_option_at_index(menu.highlighted).id == "skin":
            self.app.cycle_skin(delta)
            self._sync()

    def action_help(self) -> None:
        self.app.push_screen(HelpScreen())

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        choice = event.option.id
        if choice == "skin":
            self.app.cycle_skin(1)
            self._sync()
        elif choice == "help":
            self.action_help()
        elif choice == "quit":
            self.app.exit()
        else:
            getattr(self, f"flow_{choice}")()

    # Each flow runs as a worker so it can wait on dialogs one after another.

    @work
    async def flow_engine(self) -> None:
        app = self.app
        saved, engines = app.config["setup"], list(app.engines)
        if engines and saved.get("opponent") in engines:
            app.engine_name = saved["opponent"]
        if not await app.get_engine():  # fail before the form, not after it
            app.notify(app.engine_error, title="No engine", severity="error", timeout=12)
            return
        levels = [name for name, _, _ in PRESETS]
        rows = [
            Row("engine", "Opponent", engines, engines.index(app.engine_name), show=lambda _: len(engines) > 1),
            Row("side", "Play as", SIDES, saved.get("side", 0) % 3),
            Row("level", "Difficulty", levels, saved.get("level", 2) % len(levels)),
            *time_rows(saved),
        ]
        if (values := await app.push_screen_wait(FormScreen("Play the computer", rows))) is None:
            return
        app.engine_name = engines[values.pop("engine")]
        app.remember({**values, "opponent": app.engine_name})
        if not await app.get_engine():
            app.notify(app.engine_error, title="No engine", severity="error", timeout=12)
            return
        app.start_game(Game(time_control(values)), "engine", human=pick_side(values["side"]), preset=values["level"])

    @work
    async def flow_local(self) -> None:
        app = self.app
        if (values := await app.push_screen_wait(FormScreen("Two players", time_rows(app.config["setup"])))) is None:
            return
        app.remember(values)
        app.start_game(Game(time_control(values)), "local")

    @work
    async def flow_host(self) -> None:
        app = self.app
        rows = [Row("side", "Play as", SIDES, app.config["setup"].get("side", 0) % 3), *time_rows(app.config["setup"])]
        if (values := await app.push_screen_wait(FormScreen("Host an online game", rows, submit="host"))) is None:
            return
        app.remember(values)
        if (peer := await app.push_screen_wait(HostScreen())) is None:
            return
        me, tc = pick_side(values["side"]), time_control(values)
        await peer.send(t="hello", v=net.VERSION, host_color="white" if me else "black", base=tc.base, inc=tc.inc)
        app.start_game(Game(tc), "online", human=me, peer=peer)

    @work
    async def flow_join(self) -> None:
        app = self.app
        if (address := await app.push_screen_wait(JoinScreen(app.config.get("join", "")))) is None:
            return
        app.config["join"] = address
        app.save_config()
        try:
            peer = await net.join(address)
            hello = await asyncio.wait_for(peer.recv(), 8)
        except (OSError, ValueError, TimeoutError) as e:
            app.notify(f"Could not connect to {address}: {e or 'timed out'}", severity="error", timeout=8)
            return
        if not net.valid_hello(hello):
            peer.close()
            app.notify("That host is not running a compatible hyprchess.", severity="error", timeout=8)
            return
        me = chess.BLACK if hello["host_color"] == "white" else chess.WHITE
        app.start_game(Game(TimeControl(hello["base"], hello["inc"])), "online", human=me, peer=peer)

    @work
    async def flow_resume(self) -> None:
        self.app.resume_game()

    @work
    async def flow_pgn(self) -> None:
        app = self.app

        # Ask the user: use saved games or browse for a new PGN file
        choice = await app.push_screen_wait(MenuScreen(
            "PGN import",
            [("saved", "Saved games (hyprchess folder)"),
             ("browse", "Browse for a PGN file…")],
            body="Choose how to import a game"
        ))
        if choice is None:
            return

        if choice == "saved":
            # Existing behaviour: list saved games from the hyprchess data directory
            files = sorted(app.data_dir.glob("*.pgn"), reverse=True)[:30]
            if not files:
                app.notify(f"No saved games yet. Press s during a game to save one to {app.data_dir}")
                return
            options = [(f.name, f.stem) for f in files]
            if (name := await app.push_screen_wait(MenuScreen("Review a saved game", options))) is None:
                return
            try:
                game, headers = Game.from_pgn((app.data_dir / name).read_text())
            except (OSError, ValueError) as e:
                app.notify(f"Could not open {name}: {e}", severity="error")
                return
            app.push_screen(ReviewScreen(game, headers, len(game.board.move_stack)))

        else:  # choice == "browse"
            # Remember the last folder; default to last-used or home
            last_dir = app.config["setup"].get("pgn_dir", "")
            picker = PgnPickerScreen(start_dir=last_dir)
            selected_path: str | None = await app.push_screen_wait(picker)
            if selected_path is None:
                return  # user cancelled

            # Read the PGN file with encoding tolerance
            try:
                pgn_text = Path(selected_path).read_text(encoding="utf-8-sig")
            except Exception:
                pgn_text = Path(selected_path).read_text(encoding="latin-1")

            games, notes = Game.games_from_pgn(pgn_text)

            if not games:
                app.notify("\n".join(notes) or "No loadable games found in that PGN file.", severity="error")
                return

            if len(games) == 1:
                # Single game: open it straight in the review screen
                game, headers = games[0]
                # Remember the folder for next time
                try:
                    p = Path(selected_path)
                    app.config["setup"]["pgn_dir"] = str(p.parent)
                except Exception:
                    pass
                app.push_screen(ReviewScreen(game, headers, len(game.board.move_stack)))
            else:
                # Multiple games: show the game-selection list
                list_screen = GameListScreen(games=games, notes=notes)
                selected_idx: int | None = await app.push_screen_wait(list_screen)
                if selected_idx is None:
                    return  # user cancelled
                game, headers = games[selected_idx]
                # Remember the folder for next time
                try:
                    p = Path(selected_path)
                    app.config["setup"]["pgn_dir"] = str(p.parent)
                except Exception:
                    pass
                app.push_screen(ReviewScreen(game, headers, len(game.board.move_stack)))
