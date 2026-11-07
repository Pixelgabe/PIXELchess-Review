"""The game screen: board, side panel, clocks, and the engine / online opponents."""

from datetime import datetime
from time import monotonic

import asyncio

import chess
import chess.engine
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import Screen
from textual.widgets import Footer, Static

from . import animation, sound
from .animation import MOVE_ANIMATION_DELAY, animation_segments, frame_board, move_path
from .board import TIERS, BoardView, board_size, fit
from .game import PRESETS, Game, format_clock, result_winner
from .screens import HelpScreen, MenuScreen

PANEL_W = 28
VIEW_ONLY = {"review", "flip", "skin", "toggle_panel", "back", "help", "quit_game"}
OFFLINE_ONLY = {"undo", "redo", "hint", "level"}
PROMOTIONS = [("q", "Queen"), ("r", "Rook"), ("b", "Bishop"), ("n", "Knight")]


class GameScreen(Screen):
    DEFAULT_CSS = """
    GameScreen { align: center middle; }
    GameScreen #main { width: auto; height: auto; }
    GameScreen #panel { padding: 0 1; }
    """
    BINDINGS = [
        Binding("enter,space", "select", "Move"),
        Binding("escape", "deselect", show=False),
        Binding("u", "undo", "Undo"),
        Binding("r", "redo", "Redo"),
        Binding("comma", "review(-1)", "Back"),
        Binding("full_stop", "review(1)", "Forward"),
        Binding("home", "review(-9999)", show=False),
        Binding("end", "review(9999)", show=False),
        Binding("i", "hint", "Hint"),
        Binding("left_square_bracket", "level(-1)", "Easier"),
        Binding("right_square_bracket", "level(1)", "Harder"),
        Binding("f", "flip", "Flip"),
        Binding("c", "skin", "Skin"),
        Binding("t", "toggle_panel", "Panel"),
        Binding("s", "save", "Save"),
        Binding("x", "resign", "Resign"),
        Binding("m", "sound", "Sound"),
        Binding("b", "back", "Menu"),
        Binding("question_mark", "help", "Help"),
        Binding("q", "quit_game", "Quit"),
        Binding("left,h", "step(-1,0)", show=False),
        Binding("right,l", "step(1,0)", show=False),
        Binding("up,k", "step(0,1)", show=False),
        Binding("down,j", "step(0,-1)", show=False),
    ]

    def __init__(self, game: Game, mode: str, human=None, preset: int = 2, peer=None, names=None) -> None:
        """mode: "engine", "local", "online" or "viewer". human: the colour played here (engine/online)."""
        super().__init__()
        self.game, self.mode, self.human, self.preset, self.peer = game, mode, human, preset, peer
        self.given_names = names
        self.flipped = human == chess.BLACK
        self.cursor = chess.E7 if self.flipped else chess.E2
        self.selected: int | None = None
        self.targets: set[int] = set()
        self.hint: set[int] = set()
        self.view: int | None = 0 if mode == "viewer" else None  # ply being reviewed, None = live
        self.shown = game.board  # position on screen: live board, or an earlier one in review
        self.anim_board: chess.Board | None = None  # live board while a move animation is running
        self.thinking = False
        self.show_panel = True
        self.stacked = False
        self.finished = mode == "viewer"  # True once the game-over dialog has been shown
        self.connected = peer is not None
        self._last_tick = monotonic()

    # ---- state helpers

    @property
    def names(self) -> dict[chess.Color, str]:
        if self.given_names:
            return self.given_names
        if self.mode == "local":
            return {chess.WHITE: "White", chess.BLACK: "Black"}
        other = f"{self.app.engine_name} {PRESETS[self.preset][0]}" if self.mode == "engine" else "Opponent"
        return {self.human: "You", not self.human: other}

    @property
    def can_move(self) -> bool:
        """Is it this keyboard's turn to move a piece right now?"""
        if self.mode == "viewer" or self.view is not None or self.thinking or self.game.outcome():
            return False
        return self.mode == "local" or self.game.board.turn == self.human

    def check_action(self, action: str, parameters: tuple) -> bool:
        if self.mode == "viewer":
            return action in VIEW_ONLY
        if self.mode == "online" and action in OFFLINE_ONLY:
            return False
        if action == "level":
            return self.mode == "engine"
        if action == "resign":
            return not self.game.outcome()
        return True

    # ---- layout and drawing

    def compose(self) -> ComposeResult:
        with Container(id="main"):
            yield BoardView()
            yield Static(id="panel")
        yield Footer()

    def on_mount(self) -> None:
        self._layout()
        self.set_interval(0.2, self._tick)
        if self.peer:
            self.listen()
        self.engine_move()

    def on_resize(self) -> None:
        self._layout()

    def on_screen_resume(self) -> None:
        self._layout()

    def _layout(self) -> None:
        """Pick the largest board that fits, with the panel beside or below it."""
        w, h = self.size.width, self.size.height - 1  # footer
        if self.show_panel:
            side, below, panel_h = fit(w - PANEL_W, h), fit(w, h - 3), 3
            self.stacked = below > side or w < board_size(0)[0] + PANEL_W
        else:  # panel off: board takes the width, one status line stays below
            side, below, panel_h, self.stacked = 0, fit(w, h - 1), 1, True
        board, panel = self.query_one(BoardView), self.query_one("#panel")
        board.tier = below if self.stacked else side
        board_w, board_h = board_size(board.tier)
        self.query_one("#main").styles.layout = "vertical" if self.stacked else "horizontal"
        panel.styles.width = board_w if self.stacked else PANEL_W
        panel.styles.height = panel_h if self.stacked else board_h
        panel.display = not self.stacked or h >= board_h + panel_h
        self._refresh()

    def _status(self) -> str:
        board = self.game.board
        if self.view is not None:
            return f"Review {self.view}/{len(board.move_stack)}"
        if outcome := self.game.outcome():
            return f"{outcome[0]} {outcome[1]}"
        if self.thinking:
            return f"{self.app.engine_name} thinking…"
        if self.mode == "online" and not self.connected:
            return "Opponent disconnected"
        if self.mode != "local":
            turn = "Your move" if board.turn == self.human else "Opponent's move"
        else:
            turn = f"{'White' if board.turn else 'Black'} to move"
        return turn + (", check" if board.is_check() else "")

    def _player(self, color: chess.Color) -> Text:
        game, live = self.game, not self.game.outcome()
        active = live and game.board.turn == color
        line = Text(f"{self.names[color][:19]:<19}", "bold" if active else "")
        if game.clock:
            low = active and game.clock[color] < 10
            style = "bold white on red" if low else "reverse" if active and game.board.move_stack else ""
            line.append(f"{format_clock(game.clock[color]):>7}", style)
        return line

    def _captures(self, color: chess.Color, lost: dict, material: int) -> Text:
        """What `color` has taken, and how far ahead it is."""
        glyphs = self.app.skin.glyphs
        lead = material if color == chess.WHITE else -material
        return Text("".join(glyphs[pt] for pt in lost[not color]) + (f" +{lead}" if lead > 0 else ""))

    def _refresh(self) -> None:
        game = self.game
        sans, lost = game.history()
        shown = self.anim_board if self.anim_board is not None and self.view is None else game.board
        self.shown = shown if self.view is None else game.at(self.view)
        material = Game.material(game.board)
        cur = (len(sans) if self.view is None else self.view) - 1  # ply that led to the shown position
        rows = []
        for i in range(0, len(sans), 2):
            row = Text(f"{i // 2 + 1:>3}. ")
            for j in range(i, min(i + 2, len(sans))):
                row.append(f"{sans[j]:<8}", "reverse" if j == cur else "")
            rows.append(row)
        top, bottom = (chess.WHITE, chess.BLACK) if self.flipped else (chess.BLACK, chess.WHITE)
        status = Text(self._status(), "bold")

        if not self.show_panel:
            lines = [status]
            if game.clock:
                status.append(f"   {format_clock(game.clock[top])} / {format_clock(game.clock[bottom])}", "not bold")
        elif self.stacked:
            if game.clock:
                second = Text.assemble(self._player(top), "  ", self._player(bottom))
            else:
                second = Text.assemble(self._captures(top, lost, material), "  |  ", self._captures(bottom, lost, material))
            lines = [status, second, Text(" ").join(rows[max(cur // 2 - 2, 0) : cur // 2 + 1])]
        else:
            height = board_size(self.query_one(BoardView).tier)[1]
            roomy, rule = height >= 12, [Text("─" * (PANEL_W - 2), "dim")] if height >= 14 else []
            head = [self._player(top)] + ([self._captures(top, lost, material)] if roomy else [])
            foot = ([self._captures(bottom, lost, material)] if roomy else []) + [self._player(bottom), status]
            room = max(height - len(head) - len(foot) - 2 * len(rule), 0)
            start = min(max(cur // 2 - room // 2, 0), max(len(rows) - room, 0))
            moves = rows[start : start + room]
            lines = [*head, *rule, *moves, *[Text()] * (room - len(moves)), *rule, *foot]
        self.query_one("#panel", Static).update(Text("\n").join(lines))
        self.query_one(BoardView).refresh()

    # ---- clocks, endings, autosave

    def _tick(self) -> None:
        now = monotonic()
        dt, self._last_tick = now - self._last_tick, now
        if not self.game.clock or self.game.outcome():
            return
        self.game.tick(dt, only=self.human if self.mode == "online" else None)
        if self.game.outcome() and self.mode == "online":
            self.send(t="flag")
        self._changed() if self.game.outcome() else self._refresh()

    def _changed(self) -> None:
        """Call after anything alters the game: redraw, autosave, and announce the result once."""
        self._refresh()
        self.refresh_bindings()  # not from the clock tick: the footer rebuilds itself each time
        outcome = self.game.outcome()
        if self.mode in ("engine", "local"):
            if outcome:
                self.app.clear_state()
            else:
                self.app.save_state(self._state())
        if outcome and not self.finished:
            self.finished = True
            self._select(None)
            self.announce(outcome)

    def _state(self) -> dict:
        game = self.game
        return {
            "mode": self.mode,
            "human": self.human,
            "preset": self.preset,
            "engine": self.app.engine_name,
            "base": game.tc.base,
            "inc": game.tc.inc,
            "clock": [game.clock[chess.WHITE], game.clock[chess.BLACK]] if game.clock else None,
            "moves": [m.uci() for m in game.board.move_stack],
        }

    @work
    async def announce(self, outcome: tuple[str, str]) -> None:
        result, reason = outcome
        winner = result_winner(result)
        checkmate = self.game.board.is_checkmate()
        if result == "1/2-1/2":
            title, body = "Draw", f"{result}  {reason}"
        elif result == "*":
            title, body = "Game abandoned", f"{result}  {reason}"
        elif checkmate:
            loser = not winner
            winner_name, loser_name = self.names[winner], self.names[loser]
            if self.mode == "local":
                title = f"Checkmate — {winner_name} wins"
            else:
                title = "Checkmate — You win" if winner == self.human else "Checkmate — You lose"
            if loser_name == "You":
                body = f"You are checkmated. {winner_name} wins.\n{result}  ·  {reason}"
            else:
                body = f"{loser_name} is checkmated. {winner_name} wins.\n{result}  ·  {reason}"
        elif self.mode == "local":
            title = "White wins" if result == "1-0" else "Black wins"
            body = f"{result}  {reason}"
        else:
            title = "You win" if winner == self.human else "You lose"
            body = f"{result}  {reason}"
        options = [("review", "Look at the board"), ("save", "Save PGN"), ("menu", "Main menu")]
        if self.mode != "online":
            options.insert(0, ("rematch", "Play again"))
        choice = await self.app.push_screen_wait(MenuScreen(title, options, body=body))
        if choice == "save":
            self.action_save()
        elif choice == "menu":
            self.app.pop_screen()
        elif choice == "rematch":
            self.app.pop_screen()
            self.app.start_game(Game(self.game.tc), self.mode, human=self.human, preset=self.preset)

    # ---- moving pieces

    def _select(self, sq: int | None) -> None:
        self.selected = sq
        self.targets = {m.to_square for m in self.game.board.legal_moves if m.from_square == sq}

    def action_step(self, dx: int, dy: int) -> None:
        if self.flipped:
            dx, dy = -dx, -dy
        f = min(max(chess.square_file(self.cursor) + dx, 0), 7)
        r = min(max(chess.square_rank(self.cursor) + dy, 0), 7)
        self.cursor = chess.square(f, r)
        self._refresh()

    def action_deselect(self) -> None:
        self._select(None)
        self._refresh()

    def pick(self, sq: int) -> None:
        """Pick up the piece on sq if it belongs to the side to move, otherwise put down whatever is held."""
        piece = self.game.board.piece_at(sq)
        self._select(sq if piece and piece.color == self.game.board.turn else None)
        self._refresh()

    def move_to(self, sq: int) -> bool:
        """Play the picked-up piece to sq. False if nothing is picked up or it cannot go there."""
        if self.selected is None or sq not in self.targets:
            return False
        if self.game.board.piece_type_at(self.selected) == chess.PAWN and chess.square_rank(sq) in (0, 7):
            self.promote(self.selected, sq)
        else:
            self.play(chess.Move(self.selected, sq))
        return True

    def action_select(self) -> None:
        if self.can_move and not self.move_to(self.cursor):
            self.action_deselect() if self.cursor == self.selected else self.pick(self.cursor)

    @work
    async def promote(self, origin: int, target: int) -> None:
        choice = await self.app.push_screen_wait(MenuScreen("Promote to", PROMOTIONS))
        if choice and self.can_move:
            self.play(chess.Move(origin, target, chess.Piece.from_symbol(choice).piece_type))

    def _animation_segments(self, move: chess.Move, before: chess.Board) -> list[tuple]:
        """Moving pieces for an animation: the played move, plus the rook in a castle."""
        return animation.animation_segments(move, before)

    def _after_move(self, move: chess.Move, before: chess.Board) -> None:
        """After any move has been pushed: play the move sound and start a short animation."""
        after = self.game.board
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
        if self.mode == "viewer" or self.view is not None or not self.is_attached or not bool(self.app.config.get("animation", True)):
            return
        segments = self._animation_segments(move, before)
        if not segments:
            return
        self.anim_board = frame_board(self.game.board, segments, 0)
        self._animate_move(move, before)

    @work(exclusive=True, group="move-animation")
    async def _animate_move(self, move: chess.Move, before: chess.Board) -> None:
        """Hop a short animation through intermediate squares, then hand back to the real board."""
        segments = animation.animation_segments(move, before)
        after = self.game.board.copy(stack=False)
        steps = max(len(segment[3]) for segment in segments) if segments else 1
        for index in range(steps):
            if not self.is_attached or self.view is not None:
                self.anim_board = None
                return
            self.anim_board = after if index == steps - 1 else frame_board(after, segments, index)
            if not self.is_attached:
                return
            self.query_one(BoardView).refresh()
            await asyncio.sleep(MOVE_ANIMATION_DELAY)
        if not self.is_attached or self.view is not None:
            self.anim_board = None
            return
        self.anim_board = None
        self._refresh()

    def _frame_board(self, after: chess.Board, segments: list[tuple], index: int) -> chess.Board:
        """A copy of *after* with the moving pieces drawn at the interpolated square."""
        return animation.frame_board(after, segments, index)

    def play(self, move: chess.Move) -> None:
        """Make a move for this keyboard, then hand over to the engine or the network."""
        before = self.game.board.copy(stack=False)
        self.game.push(move)
        self.hint = set()
        self._select(None)
        if self.mode == "online":
            clock = self.game.clock
            self.send(t="move", uci=move.uci(), clock=[clock[chess.WHITE], clock[chess.BLACK]] if clock else None)
        self._after_move(move, before)
        self._changed()
        self.engine_move()

    # ---- engine opponent

    @work
    async def engine_move(self) -> None:
        game = self.game
        if self.mode != "engine" or self.thinking or game.outcome() or game.board.turn == self.human:
            return
        if not (engine := await self.app.get_engine()):
            return
        _, options, limit = PRESETS[self.preset]
        options = {k: v for k, v in options.items() if k in engine.options}  # not every engine has every strength knob
        if "UCI_Elo" in options:
            elo = engine.options["UCI_Elo"]
            options["UCI_Elo"] = min(max(options["UCI_Elo"], elo.min or 0), elo.max or options["UCI_Elo"])
        if game.clock:  # never think away more than a sliver of the remaining time
            limit = {**limit, "time": min(limit.get("time", 0.3), max(game.clock[game.board.turn] / 30, 0.05))}
        self.thinking, plies = True, len(game.board.move_stack)
        self._refresh()
        try:
            result = await engine.play(game.board.copy(), chess.engine.Limit(**limit), options=options)
            if not game.outcome() and len(game.board.move_stack) == plies:
                before = game.board.copy(stack=False)
                game.push(result.move)
                self.hint = set()
                self._after_move(result.move, before)
        except chess.engine.EngineError as e:
            self.app.engine = None
            self.notify(f"{self.app.engine_name} stopped: {e}", severity="error")
        finally:
            self.thinking = False
            self._changed()

    @work
    async def action_hint(self) -> None:
        if not self.can_move:
            return
        if not (engine := await self.app.get_engine()):
            self.notify(self.app.engine_error, title="No hints", severity="warning")
            return
        self.thinking = True
        try:
            result = await engine.play(self.game.board.copy(), chess.engine.Limit(time=0.4))
            self.hint = {result.move.from_square, result.move.to_square}
        except chess.engine.EngineError as e:
            self.app.engine = None
            self.notify(f"{self.app.engine_name} stopped: {e}", severity="error")
        finally:
            self.thinking = False
            self._refresh()

    def action_level(self, delta: int) -> None:
        self.preset = min(max(self.preset + delta, 0), len(PRESETS) - 1)
        self._changed()

    # ---- online opponent

    def send(self, **msg) -> None:
        if self.peer:
            self.run_worker(self.peer.send(**msg))

    @work
    async def listen(self) -> None:
        game, them = self.game, not self.human
        while (msg := await self.peer.recv()) is not None:
            if game.outcome():
                continue
            kind = msg["t"]
            if kind == "resign":
                game.resign(them)
            elif kind == "flag":
                game.end("1-0" if them == chess.BLACK else "0-1", "on time")
            elif kind == "move":
                try:
                    move = chess.Move.from_uci(msg.get("uci"))
                except (ValueError, TypeError):
                    move = None
                if game.board.turn != them or move not in game.board.legal_moves:
                    game.end("*", "opponent sent an illegal move")
                    self.peer.close()
                else:
                    before = game.board.copy(stack=False)
                    game.push(move)
                    self.hint = set()
                    clock = msg.get("clock")
                    if game.clock and isinstance(clock, list) and len(clock) == 2:
                        reported = clock[0 if them == chess.WHITE else 1]
                        if isinstance(reported, (int, float)) and 0 <= reported <= game.clock[them] + 1:
                            game.clock[them] = float(reported)
                    self._after_move(move, before)
            self._changed()
        self.connected = False
        game.end("*", "opponent disconnected")
        if self.is_attached:
            self._changed()

    def on_unmount(self) -> None:
        if self.peer:
            self.peer.close()

    # ---- other actions

    def action_undo(self) -> None:
        if self.thinking:
            return
        self.view, self.hint, self.finished = None, set(), False
        self.game.ended = None
        self.anim_board = None
        self.game.undo(2 if self.mode == "engine" else 1)
        self._select(None)
        self._changed()
        self.engine_move()

    def action_redo(self) -> None:
        if self.thinking or not self.game.redo_one():
            return
        self.view = None
        self.anim_board = None
        self._select(None)
        self._changed()

    def action_review(self, delta: int) -> None:
        """Step the board through earlier positions; stepping past the last move returns to live."""
        n = len(self.game.board.move_stack)
        ply = min(max((n if self.view is None else self.view) + delta, 0), n)
        self.view = ply if self.mode == "viewer" or ply < n else None
        self._select(None)
        self._refresh()

    def action_flip(self) -> None:
        self.flipped = not self.flipped
        self._refresh()

    def action_skin(self) -> None:
        self.app.cycle_skin(1)
        self.notify(f"Skin: {self.app.skin.name}", timeout=2)
        self._refresh()

    def action_sound(self) -> None:
        """Mute or unmute the retro move sounds."""
        enabled = not bool(self.app.config.get("sound", True))
        self.app.config["sound"] = enabled
        self.app.save_config()
        self.notify(f"Sound: {'on' if enabled else 'off'}", timeout=3)

    def action_toggle_panel(self) -> None:
        self.show_panel = not self.show_panel
        self._layout()

    def action_save(self) -> None:
        if not self.game.board.move_stack:
            self.notify("No moves to save")
            return
        names = self.names
        folder = self.app.data_dir
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{datetime.now():%Y%m%d-%H%M%S}.pgn"
        path.write_text(self.game.pgn(names[chess.WHITE], names[chess.BLACK]))
        self.notify(f"Saved {path}")

    @work
    async def action_resign(self) -> None:
        if self.game.outcome():
            return
        who = self.game.board.turn if self.mode == "local" else self.human
        if await self.app.push_screen_wait(MenuScreen("Resign this game?", [("no", "Keep playing"), ("yes", "Resign")])) == "yes":
            self.game.resign(who)
            self.send(t="resign")
            self._changed()

    @work
    async def action_back(self) -> None:
        if self.mode == "online" and not self.game.outcome():
            options = [("no", "Keep playing"), ("yes", "Leave and resign")]
            if await self.app.push_screen_wait(MenuScreen("Leave the online game?", options)) != "yes":
                return
            self.game.resign(self.human)
            await self.peer.send(t="resign")
        self.app.pop_screen()

    def action_help(self) -> None:
        self.app.push_screen(HelpScreen())

    def action_quit_game(self) -> None:
        self.app.exit()
