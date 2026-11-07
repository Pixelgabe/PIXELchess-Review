"""Game state with no UI: board, clocks, undo/redo, captured material, PGN."""

import io
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import chess
import chess.pgn

VALUE = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}


def result_winner(result: str) -> chess.Color | None:
    """The winning colour for a PGN result, or None for a draw or unfinished game."""
    if result == "1-0":
        return chess.WHITE
    if result == "0-1":
        return chess.BLACK
    return None


@dataclass(frozen=True)
class TimeControl:
    base: int | None  # seconds per side, None = no clock
    inc: int = 0  # seconds added after each move

    @property
    def label(self) -> str:
        if self.base is None:
            return "No clock"
        minutes = self.base // 60 if self.base % 60 == 0 else round(self.base / 60, 1)
        return f"{minutes}+{self.inc}"


# (name, time control); the last entry is filled in from the custom rows of the setup form
TIME_PRESETS = [
    ("Bullet 1+0", TimeControl(60)),
    ("Blitz 3+2", TimeControl(180, 2)),
    ("Blitz 5+0", TimeControl(300)),
    ("Rapid 10+0", TimeControl(600)),
    ("Rapid 15+10", TimeControl(900, 10)),
    ("Classical 30+20", TimeControl(1800, 20)),
    ("No clock", TimeControl(None)),
]
CUSTOM_MINUTES = [1, 2, 3, 5, 10, 15, 20, 30, 45, 60, 90]
CUSTOM_INCREMENTS = [0, 1, 2, 3, 5, 10, 15, 30]


# (name, Stockfish options, search limit). Options apply only for that move.
PRESETS = [
    ("Beginner", {"Skill Level": 0}, {"depth": 1}),
    ("Casual", {"Skill Level": 3}, {"depth": 3}),
    ("Club 1500", {"UCI_LimitStrength": True, "UCI_Elo": 1500}, {"time": 0.3}),
    ("Strong 1900", {"UCI_LimitStrength": True, "UCI_Elo": 1900}, {"time": 0.5}),
    ("Expert 2300", {"UCI_LimitStrength": True, "UCI_Elo": 2300}, {"time": 0.8}),
    ("Maximum", {}, {"time": 1.5}),
]


def format_clock(seconds: float) -> str:
    if seconds < 10:
        return f"0:{seconds:04.1f}"
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02}:{rest % 60:02}" if hours else f"{rest // 60}:{rest % 60:02}"


def _parse_pgn_game(parsed: chess.pgn.Game) -> Optional[tuple]:
    """Convert a parsed PGN Game node into (Game, headers), or None if it cannot be loaded.

    Returns None when the game does not start from the standard position,
    has parser errors, or has no legal mainline moves.
    """
    if parsed is None or parsed.errors:
        return None
    if parsed.board().fen() != chess.STARTING_FEN:
        return None  # non-standard starting position
    game = Game()  # the hyprchess Game class
    for move in parsed.mainline_moves():
        game.board.push(move)
    # If the game has no result header but the position is terminal, infer from outcome
    headers = dict(parsed.headers)
    if not game.board.outcome(claim_draw=True) and headers.get("Result") in ("1-0", "0-1", "1/2-1/2"):
        game.ended = (headers["Result"], headers.get("Termination", "game over"))
    return game, headers


class Game:
    def __init__(self, tc: TimeControl = TimeControl(None)) -> None:
        self.board = chess.Board()
        self.tc = tc
        self.clock = None if tc.base is None else {chess.WHITE: float(tc.base), chess.BLACK: float(tc.base)}
        self.redo: list[list[chess.Move]] = []  # undone move groups; any new move clears it
        self.ended: tuple[str, str] | None = None  # (result, reason) for endings the board can't see

    def push(self, move: chess.Move) -> None:
        if self.clock:
            self.clock[self.board.turn] += self.tc.inc
        self.board.push(move)
        self.redo.clear()

    def undo(self, plies: int) -> None:
        undone = [self.board.pop() for _ in range(min(plies, len(self.board.move_stack)))]
        if undone:
            self.redo.append(undone)

    def redo_one(self) -> bool:
        if not self.redo:
            return False
        for move in reversed(self.redo.pop()):
            self.board.push(move)
        return True

    def tick(self, dt: float, only: chess.Color | None = None) -> None:
        """Run the clock of the side to move. Clocks start once White has moved.

        With `only` set (online play), just that side's flag can fall here; the
        other side reports its own flag over the network.
        """
        if not self.clock or self.outcome() or not self.board.move_stack:
            return
        side = self.board.turn
        self.clock[side] = max(self.clock[side] - dt, 0.0)
        if self.clock[side] == 0 and only in (None, side):
            self.end("0-1" if side == chess.WHITE else "1-0", "on time")

    def end(self, result: str, reason: str) -> None:
        if not self.outcome():
            self.ended = (result, reason)

    def resign(self, side: chess.Color) -> None:
        self.end("0-1" if side == chess.WHITE else "1-0", "resignation")

    def outcome(self) -> tuple[str, str] | None:
        """(result, reason) once the game is over, else None."""
        if self.ended:
            return self.ended
        if o := self.board.outcome(claim_draw=True):
            return o.result(), o.termination.name.replace("_", " ").lower()
        return None

    def history(self) -> tuple[list[str], dict[chess.Color, list[int]]]:
        """SAN for every move, and the piece types each colour has lost to captures."""
        key = tuple(self.board.move_stack)  # replaying is slow and this runs on every redraw, so keep the last answer
        if getattr(self, "_history", (None,))[0] == key:
            return self._history[1]
        replay, sans = chess.Board(), []
        lost: dict[chess.Color, list[int]] = {chess.WHITE: [], chess.BLACK: []}
        for move in self.board.move_stack:
            if replay.is_capture(move):
                victim = chess.PAWN if replay.is_en_passant(move) else replay.piece_type_at(move.to_square)
                lost[not replay.turn].append(victim)
            sans.append(replay.san(move))
            replay.push(move)
        for pieces in lost.values():
            pieces.sort(key=VALUE.get, reverse=True)
        self._history = (key, (sans, lost))
        return sans, lost

    def at(self, ply: int) -> chess.Board:
        """The position after `ply` moves."""
        board = chess.Board()
        for move in self.board.move_stack[:ply]:
            board.push(move)
        return board

    @staticmethod
    def material(board: chess.Board) -> int:
        """White's material minus Black's, in pawns."""
        return sum(VALUE[p.piece_type] * (1 if p.color else -1) for p in board.piece_map().values())

    def pgn(self, white: str, black: str) -> str:
        game = chess.pgn.Game.from_board(self.board)
        outcome = self.outcome()
        game.headers.update(
            Event="hyprchess",
            Date=f"{datetime.now():%Y.%m.%d}",
            White=white,
            Black=black,
            Result=outcome[0] if outcome else "*",
            TimeControl="-" if self.tc.base is None else f"{self.tc.base}+{self.tc.inc}",
        )
        if outcome:
            game.headers["Termination"] = outcome[1]
        return f"{game}\n"

    @classmethod
    def from_pgn(cls, text: str) -> tuple["Game", dict[str, str]]:
        """First game of a PGN text and its headers. Raises ValueError if it has no legal mainline.

        This method keeps its original strict behaviour: it only loads a game that starts
        from the standard position, has no parser errors, and contains a legal mainline.
        """
        parsed = chess.pgn.read_game(io.StringIO(text))
        result = _parse_pgn_game(parsed)
        if result is None:
            raise ValueError("not a standard game from the starting position")
        return result

    @classmethod
    def games_from_pgn(cls, text: str) -> tuple[list[tuple["Game", dict[str, str]]], list[str]]:
        """Parse some or all PGN games from *text* and return (games, notes).

        Returns
        -------
        games : list[tuple[Game, dict]]
            Each tuple is (game, headers) for a successfully loaded game.
        notes : list[str]
            Human-readable notes for games that could not be loaded.
            Each entry has the form ``"Game N: <reason>"``.
            If every game loaded successfully, *notes* is an empty list.

        behaviour
        ---------
        * Loops ``chess.pgn.read_game()`` until ``None`` is returned, so multi-game PGN
          files are fully supported.
        * A game that does not start from the standard position, has parser errors, or
          contains no legal mainline moves is **not** loaded; instead a note is recorded.
        * Games with recoverable positions after the first move are still loaded; only
          the initial-position check rejects them.
        * The PGN text is read with UTF-8-BOM tolerance (via ``utf-8-sig``) and falls
          back to ``latin-1`` if decoding fails.
        """
        games: list[tuple["Game", dict[str, str]]] = []
        notes: list[str] = []

        # Read the PGN text with encoding tolerance
        try:
            pgn_text = text.encode("utf-8-sig").decode("utf-8-sig")
        except Exception:
            pgn_text = text.encode("latin-1").decode("latin-1")

        handle = io.StringIO(pgn_text)

        index = 1
        parsed = chess.pgn.read_game(handle)
        while parsed is not None:
            result = _parse_pgn_game(parsed)
            if result is not None:
                game, headers = result
                games.append((game, headers))
            else:
                notes.append(f"Game {index}: could not load (non-standard start, parser errors, or no mainline)")
            index += 1
            parsed = chess.pgn.read_game(handle)

        return games, notes
