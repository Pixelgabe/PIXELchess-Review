"""Whole-app flows driven through the keyboard, against the real Stockfish when it is installed."""

import asyncio
import shutil

import chess
import pytest

from hyprchess import analysis
from hyprchess.app import ChessApp
from hyprchess.board import TIERS, BoardView
from hyprchess.game import Game, TimeControl
from hyprchess.game_screen import GameScreen
from hyprchess.screens import (
    FormScreen,
    GameListScreen,
    HelpScreen,
    HostScreen,
    JoinScreen,
    MenuScreen,
    PgnPickerScreen,
    ResultScreen,
    ReviewScreen,
    TitleScreen,
)
from textual.widgets import OptionList

needs_engine = pytest.mark.skipif(not shutil.which("stockfish"), reason="stockfish not installed")


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(var, str(tmp_path / var))
    return tmp_path


async def until(pilot, condition, timeout=15.0):
    for _ in range(int(timeout / 0.05)):
        if condition():
            return
        await pilot.pause(0.05)
    raise AssertionError("timed out waiting for the app")


async def choose(pilot, app, option_id):
    """Highlight a title/menu option by id and press enter."""
    await until(pilot, lambda: app.screen.query(OptionList))  # a dialog that was just pushed may not be built yet
    menu = app.screen.query_one(OptionList)
    menu.highlighted = menu.get_option_index(option_id)
    await pilot.press("enter")
    await pilot.pause()


def move(screen, uci):
    m = chess.Move.from_uci(uci[:4])
    screen.cursor = m.from_square
    screen.action_select()
    screen.cursor = m.to_square
    screen.action_select()


def run(coro):
    asyncio.run(coro)


def test_title_menu_skin_cycle_and_help():
    async def main():
        app = ChessApp()
        async with app.run_test(size=(100, 40)) as pilot:
            assert isinstance(app.screen, TitleScreen)
            menu = app.screen.query_one(OptionList)
            assert menu.get_option("resume").disabled
            first = app.skin.name
            menu.highlighted = menu.get_option_index("skin")
            await pilot.press("right")
            assert app.skin.name != first and app.skin.name in str(menu.get_option("skin").prompt)
            assert ChessApp().skin.name == app.skin.name  # remembered across launches
            await pilot.press("question_mark")
            assert isinstance(app.screen, HelpScreen)
            await pilot.press("x")
            assert isinstance(app.screen, TitleScreen)

    run(main())


def test_local_game_with_clock_promotion_picker_resume_and_save(isolated_home):
    async def main():
        app = ChessApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await choose(pilot, app, "local")
            form = app.screen
            assert isinstance(form, FormScreen)
            while form.rows[0].options[form.rows[0].index] != "Custom":
                await pilot.press("right")
            assert len(form.visible()) == 3  # custom rows appear
            await pilot.press("down", "right", "down", "right", "enter")  # 2 min, 1 sec
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, GameScreen) and screen.game.tc == TimeControl(120, 1)
            for uci in ("e2e4", "d7d5", "e4d5", "c7c6", "d5c6", "a7a6", "c6b7", "a6a5"):
                move(screen, uci)
            assert screen.game.clock[chess.WHITE] > 120  # increments added
            move(screen, "b7a8")
            await pilot.pause()
            assert isinstance(app.screen, MenuScreen)  # promotion picker
            await choose(pilot, app, "n")
            assert screen.game.board.piece_at(chess.A8) == chess.Piece(chess.KNIGHT, chess.WHITE)
            _, lost = screen.game.history()
            assert lost[chess.BLACK] == [chess.ROOK, chess.PAWN, chess.PAWN, chess.PAWN]
            await pilot.press("s")
            await until(pilot, lambda: list((isolated_home / "XDG_DATA_HOME" / "hyprchess").glob("*.pgn")))
            await pilot.press("b")
            await pilot.pause()
            assert isinstance(app.screen, TitleScreen)
            assert not app.screen.query_one(OptionList).get_option("resume").disabled
            await choose(pilot, app, "resume")
            resumed = app.screen
            assert isinstance(resumed, GameScreen) and resumed is not screen
            assert resumed.game.board.fen() == screen.game.board.fen() and resumed.game.clock is not None
            # saved game opens in the read-only review screen
            await pilot.press("b")
            await pilot.pause()
            await choose(pilot, app, "pgn")
            await until(pilot, lambda: isinstance(app.screen, MenuScreen))
            await choose(pilot, app, "saved")
            await until(pilot, lambda: isinstance(app.screen, MenuScreen))
            app.screen.query_one(OptionList).highlighted = 0
            await pilot.press("enter")
            await until(pilot, lambda: isinstance(app.screen, ReviewScreen))
            viewer = app.screen
            assert isinstance(viewer, ReviewScreen) and viewer.view == 0
            await pilot.press("full_stop", "full_stop")
            await pilot.pause()
            assert viewer.view == 2 and viewer.shown.fen() == viewer.game.at(2).fen()
            moves_before = len(viewer.game.board.move_stack)
            assert not viewer.can_move
            await pilot.press("enter", "space", "u")
            assert len(viewer.game.board.move_stack) == moves_before

    run(main())


def test_flag_fall_ends_game_and_offers_rematch():
    async def main():
        app = ChessApp()
        async with app.run_test(size=(100, 40)) as pilot:
            app.start_game(Game(TimeControl(60)), "local")
            await pilot.pause()
            screen = app.screen
            move(screen, "e2e4")
            screen.game.clock[chess.BLACK] = 0.1
            await until(pilot, lambda: isinstance(app.screen, MenuScreen))
            assert screen.game.outcome() == ("1-0", "on time") and app.load_state() is None
            await choose(pilot, app, "rematch")
            assert isinstance(app.screen, GameScreen) and not app.screen.game.board.move_stack
            assert app.screen.game.clock[chess.BLACK] == 60

    run(main())


@needs_engine
def test_engine_game_undo_redo_hint_and_level():
    async def main():
        app = ChessApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await choose(pilot, app, "engine")
            await until(pilot, lambda: isinstance(app.screen, FormScreen))  # Stockfish starts first
            await pilot.press("right")  # play as Black
            await pilot.press("enter")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, GameScreen) and screen.human == chess.BLACK and screen.flipped
            await until(pilot, lambda: len(screen.game.board.move_stack) == 1 and not screen.thinking)
            move(screen, "e7e5")
            await until(pilot, lambda: len(screen.game.board.move_stack) == 3 and not screen.thinking)
            await pilot.press("i")
            await until(pilot, lambda: len(screen.hint) == 2 and not screen.thinking)
            await pilot.press("u")
            await pilot.pause()
            assert len(screen.game.board.move_stack) == 1
            await pilot.press("r")
            assert len(screen.game.board.move_stack) == 3
            await pilot.press("right_square_bracket")
            assert screen.preset == 3 and "Strong" in screen.names[chess.WHITE]
            assert app.load_state()["preset"] == 3

    run(main())


def test_online_game_between_two_apps_including_illegal_move_and_disconnect():
    async def main():
        host_app, guest_app = ChessApp(), ChessApp()
        async with host_app.run_test(size=(100, 40)) as hp, guest_app.run_test(size=(100, 40)) as gp:
            await choose(hp, host_app, "host")
            await hp.press("enter")  # White, default time
            await until(hp, lambda: isinstance(host_app.screen, HostScreen) and host_app.screen.server)
            await choose(gp, guest_app, "join")
            assert isinstance(guest_app.screen, JoinScreen)
            for ch in "127.0.0.1":
                await gp.press("full_stop" if ch == "." else ch)
            await gp.press("enter")
            await until(gp, lambda: isinstance(guest_app.screen, GameScreen))
            await until(hp, lambda: isinstance(host_app.screen, GameScreen))
            host, guest = host_app.screen, guest_app.screen
            assert (host.human, guest.human) == (chess.WHITE, chess.BLACK) and guest.game.tc == host.game.tc
            assert not guest.can_move and not host.check_action("undo", ())
            move(guest, "e7e5")  # not their turn: ignored
            assert not guest.game.board.move_stack
            move(host, "e2e4")
            await until(gp, lambda: len(guest.game.board.move_stack) == 1)
            move(guest, "e7e5")
            await until(hp, lambda: len(host.game.board.move_stack) == 2)
            assert host.game.board.fen() == guest.game.board.fen()
            # a peer that sends an illegal move is cut off rather than obeyed
            await guest.peer.send(t="move", uci="e5e4")
            await until(hp, lambda: host.game.outcome() is not None)
            assert host.game.outcome()[0] == "*" and len(host.game.board.move_stack) == 2
            await until(gp, lambda: guest.game.outcome() == ("*", "opponent disconnected"))

    run(main())


def test_online_resign_reaches_the_other_side():
    async def main():
        host_app, guest_app = ChessApp(), ChessApp()
        async with host_app.run_test(size=(100, 40)) as hp, guest_app.run_test(size=(100, 40)) as gp:
            await choose(hp, host_app, "host")
            await hp.press("right", "enter")  # host plays Black
            await until(hp, lambda: isinstance(host_app.screen, HostScreen) and host_app.screen.server)
            guest_app.config["join"] = "127.0.0.1"
            await choose(gp, guest_app, "join")
            await gp.press("enter")
            await until(gp, lambda: isinstance(guest_app.screen, GameScreen))
            await until(hp, lambda: isinstance(host_app.screen, GameScreen))
            host, guest = host_app.screen, guest_app.screen
            assert guest.human == chess.WHITE
            move(guest, "d2d4")
            await until(hp, lambda: len(host.game.board.move_stack) == 1)
            await hp.press("x")
            await hp.pause()
            await choose(hp, host_app, "yes")
            await until(gp, lambda: guest.game.outcome() == ("1-0", "resignation"))
            assert host.game.outcome() == ("1-0", "resignation")

    run(main())


def test_mouse_moves_on_press_drags_and_cancels():
    async def main():
        app = ChessApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await choose(pilot, app, "local")
            await pilot.press("enter")
            await pilot.pause()
            screen, view = app.screen, app.screen.query_one(BoardView)
            cw, ch = TIERS[view.tier]

            def at(name):
                sq = chess.parse_square(name)
                return (2 + chess.square_file(sq) * cw + cw // 2, (7 - chess.square_rank(sq)) * ch + ch // 2)

            def stack():
                return [m.uci() for m in screen.game.board.move_stack]

            # press picks up, the next press moves: nothing waits for a release
            await pilot.mouse_down(view, offset=at("e2"))
            assert screen.selected == chess.E2 and chess.E4 in screen.targets
            await pilot.mouse_up(view, offset=at("e2"))
            assert screen.selected == chess.E2
            await pilot.mouse_down(view, offset=at("e4"))
            assert stack() == ["e2e4"]
            await pilot.mouse_up(view, offset=at("e4"))
            # drag: press on the piece, release on the target
            await pilot.mouse_down(view, offset=at("e7"))
            await pilot.mouse_up(view, offset=at("e5"))
            assert stack() == ["e2e4", "e7e5"]
            # a second click on the held piece drops it; so does the right button; bad drops keep it held
            await pilot.mouse_down(view, offset=at("g1"))
            await pilot.mouse_up(view, offset=at("g5"))
            assert screen.selected == chess.G1 and len(stack()) == 2
            await pilot.mouse_down(view, offset=at("g1"))
            await pilot.mouse_up(view, offset=at("g1"))
            assert screen.selected is None
            await pilot.mouse_down(view, offset=at("g1"))
            await pilot.mouse_up(view, offset=at("g1"))
            await pilot.mouse_down(view, offset=at("a5"), button=3)
            assert screen.selected is None and len(stack()) == 2
            # mouse and keyboard share the cursor
            await pilot.mouse_down(view, offset=at("g1"))
            await pilot.mouse_up(view, offset=at("g1"))
            await pilot.press("up", "up", "left", "enter")
            assert stack()[-1] == "g1f3"

    run(main())


@needs_engine
def test_second_engine_from_engines_folder_is_offered_and_remembered(isolated_home):
    folder = isolated_home / "XDG_CONFIG_HOME" / "hyprchess" / "engines"
    folder.mkdir(parents=True)
    shutil.copy(shutil.which("stockfish"), folder / "Mirror")

    async def main():
        app = ChessApp()
        assert list(app.engines) == ["Mirror", "Stockfish"]
        async with app.run_test(size=(120, 44)) as pilot:
            await choose(pilot, app, "engine")
            await until(pilot, lambda: isinstance(app.screen, FormScreen))
            assert app.screen.rows[0].options == ["Mirror", "Stockfish"]
            await pilot.press("right", "enter")  # switch opponent to Stockfish
            await until(pilot, lambda: isinstance(app.screen, GameScreen))
            screen = app.screen
            assert app.engine_name == "Stockfish" and "Stockfish" in screen.names[chess.BLACK]
            move(screen, "e2e4")
            await until(pilot, lambda: len(screen.game.board.move_stack) == 2 and not screen.thinking)
            assert app.load_state()["engine"] == "Stockfish"
        assert ChessApp().config["setup"]["opponent"] == "Stockfish"

    run(main())


def test_pgn_picker_lists_and_selects_a_file(tmp_path):
    from textual.widgets import Input

    async def main():
        pgn = tmp_path / "sample.pgn"
        pgn.write_text('[Event "Picker"]\n[White "A"]\n[Black "B"]\n1. e4 *\n')
        (tmp_path / "sub").mkdir()
        app = ChessApp()
        async with app.run_test(size=(100, 38)) as pilot:
            result = []
            app.push_screen(PgnPickerScreen(str(tmp_path)), result.append)
            await pilot.pause()
            listing = app.screen.query_one("#dirlist", OptionList)
            prompts = [str(option.prompt) for option in listing.options]
            assert any("📁 sub" in prompt for prompt in prompts)
            assert any("📄 sample.pgn" in prompt for prompt in prompts)
            app.screen.query_one(Input).focus()
            app.screen.query_one(Input).value = str(pgn)
            await pilot.press("enter")
            await pilot.pause()
            assert result == [str(pgn)]

    run(main())


def test_pgn_picker_navigates_with_arrows_and_relative_paths(tmp_path):
    from textual.widgets import Input

    async def main():
        pgn = tmp_path / "sample.pgn"
        pgn.write_text('[Event "Picker"]\n[White "A"]\n[Black "B"]\n1. e4 *\n')
        (tmp_path / "sub").mkdir()
        app = ChessApp()
        async with app.run_test(size=(100, 38)) as pilot:
            result = []
            app.push_screen(PgnPickerScreen(str(tmp_path)), result.append)
            await until(
                pilot,
                lambda: isinstance(app.screen, PgnPickerScreen)
                and app.screen.is_mounted
                and bool(app.screen.query(OptionList)),
            )
            picker = app.screen
            listing = picker.query_one("#dirlist", OptionList)
            prompts = [str(option.prompt) for option in listing.options]
            first_dir = next(i for i, prompt in enumerate(prompts) if "📁 sub" in prompt)

            # Move down to "sub" and open it.
            while listing.highlighted != first_dir:
                await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            assert picker.path == str(tmp_path / "sub")

            # The parent entry takes us back.
            await pilot.press("home")
            await pilot.press("enter")
            await pilot.pause()
            assert picker.path == str(tmp_path)

            # A relative path is resolved against the folder being shown.
            field = picker.query_one(Input)
            field.focus()
            field.value = "sample.pgn"
            await pilot.press("enter")
            await pilot.pause()
            assert result == [str(pgn)]

    run(main())


def test_review_screen_is_read_only_and_reports_missing_engine(tmp_path):
    from textual.widgets import Static

    async def main():
        game = Game()
        for uci in ("e2e4", "e7e5", "g1f3"):
            game.push(chess.Move.from_uci(uci))
        app = ChessApp()
        app.engines, app.engine_name, app.engine = {}, None, None
        async with app.run_test(size=(120, 44)) as pilot:
            app.push_screen(ReviewScreen(game, {"Event": "Review"}, len(game.board.move_stack)))
            await until(pilot, lambda: isinstance(app.screen, ReviewScreen) and app.screen.engine_error)
            screen = app.screen
            assert not screen.can_move and screen.view == 0
            assert "Engine unavailable" in str(screen.query_one("#eval-text", Static).render())
            await pilot.press("full_stop", "full_stop")
            assert screen.view == 2 and screen.shown.fen() == game.at(2).fen()
            moves = len(screen.game.board.move_stack)
            await pilot.press("enter", "space", "u")
            assert len(screen.game.board.move_stack) == moves
            import json

            path = screen.export_analysis(tmp_path / "analysis.json")
            data = json.loads(path.read_text())
            assert data["schema_version"] == 2
            assert data["analysis"]["complete"] is False
            assert data["analysis"]["engine_info"] is None
            assert data["analysis"]["configuration"] is None
            assert data["game"]["plies"] == 3

    run(main())


def test_review_board_opens_from_the_side_the_user_played():
    game = Game()
    assert ReviewScreen(game, {"White": "You", "Black": "Stockfish"}, 0).flipped is False
    assert ReviewScreen(game, {"White": "Stockfish", "Black": "You"}, 0).flipped is True
    # Imported or local PGNs that do not identify "You" stay White.
    assert ReviewScreen(Game(), {"Event": "Imported"}, 0).flipped is False
    assert ReviewScreen(Game(), {"White": "You", "Black": "You"}, 0).flipped is False


def test_review_infers_imported_account_from_local_login(monkeypatch):
    monkeypatch.setattr("hyprchess.screens.getpass.getuser", lambda: "pixelgabe")
    game = Game()
    # Chess.com exports use account names, not "You".
    assert ReviewScreen(game, {"White": "kot28914", "Black": "pixelgabe1"}, 0).flipped is True
    assert ReviewScreen(game, {"White": "pixelgabe1", "Black": "kot28914"}, 0).flipped is False
    # An exact login name is recognised too.
    assert ReviewScreen(game, {"White": "pixelgabe", "Black": "someone"}, 0).flipped is False
    # Ambiguous or unrelated names keep the default White orientation.
    assert ReviewScreen(game, {"White": "pixelgabe1", "Black": "pixelgabe2"}, 0).flipped is False
    assert ReviewScreen(game, {"White": "someone", "Black": "someoneelse"}, 0).flipped is False


def test_move_animation_path_hops_through_intermediate_squares():
    from hyprchess.animation import move_path

    assert move_path(chess.E2, chess.E4) == [chess.E2, chess.E3, chess.E4]
    assert move_path(chess.H1, chess.H4) == [chess.H1, chess.H2, chess.H3, chess.H4]
    long_path = move_path(chess.A1, chess.H8)
    assert len(long_path) <= 5 and long_path[-1] == chess.H8


def test_castling_move_animates_king_and_rook():
    from hyprchess import animation
    from hyprchess.game import Game

    game = Game()
    for uci in ("e2e4", "g1f3", "f1c4"):
        game.push(chess.Move.from_uci(uci))
    before = game.board.copy(stack=False)
    castle = chess.Move.from_uci("e1g1")
    game.push(castle)
    segments = animation.animation_segments(castle, before)
    assert len(segments) == 2
    assert segments[0][0].piece_type == chess.KING
    assert segments[0][2] == castle.to_square
    assert segments[1][0].piece_type == chess.ROOK


@needs_engine
def test_review_screen_analyses_every_move_and_labels_them(tmp_path):
    from textual.widgets import Static

    async def main():
        game = Game()
        for uci in ("f2f3", "e7e5", "g2g4", "d8h4"):
            game.push(chess.Move.from_uci(uci))
        app = ChessApp()
        async with app.run_test(size=(130, 46)) as pilot:
            app.push_screen(ReviewScreen(game, {"Event": "Fool's mate"}, 4))
            await until(
                pilot,
                lambda: isinstance(app.screen, ReviewScreen)
                and not app.screen.analysing
                and app.screen.scan_done == app.screen.scan_total,
            )
            screen = app.screen
            assert set(screen.evals) == {0, 1, 2, 3, 4}
            assert set(screen.reports) == {1, 2, 3, 4}
            assert screen.reports[4].classification.label == "Best"
            listing = str(screen.query_one("#move-list", Static).render())
            assert "Qh4#" in listing and "??" in listing
            screen.view = 3
            screen._refresh()
            assert screen.query_one(BoardView)._bg(chess.G4) == analysis.BLUNDER.color
            import json

            path = screen.export_analysis(tmp_path / "analysis.json")
            data = json.loads(path.read_text())
            assert data["analysis"]["complete"] is True
            assert data["analysis"]["engine_info"]["name"]
            config = data["analysis"]["configuration"]
            assert config["search_limit"]["time_seconds"] > 0
            assert config["threads"] is not None and config["hash_mb"] is not None
            assert len(data["moves"]) == 4
            assert data["moves"][-1]["san"] == "Qh4#"
            assert data["moves"][-1]["label"] == "Best"
            assert data["moves"][-1]["mate"]["transition"] == "delivered_checkmate"
            assert data["summary"]["final_score_type"] == "mate"
            assert data["positions"][-1]["fen"] == game.board.fen()

    run(main())


def test_checkmate_names_winner_and_loser_and_marks_king_red():
    async def main():
        game = Game()
        for uci in ("f2f3", "e7e5", "g2g4", "d8h4"):
            game.push(chess.Move.from_uci(uci))
        app = ChessApp()
        async with app.run_test(size=(120, 44)) as pilot:
            app.push_screen(GameScreen(game, "local"))
            await until(
                pilot,
                lambda: isinstance(app.screen, GameScreen)
                and app.screen.is_mounted
                and bool(app.screen.query(BoardView)),
            )
            screen = app.screen
            board = screen.query_one(BoardView)
            assert screen.shown.is_checkmate()
            assert board._bg(chess.E1) == app.skin.checkmate
            screen._changed()
            await until(pilot, lambda: isinstance(app.screen, MenuScreen))
            dialog = app.screen
            assert dialog.heading == "Checkmate — Black wins"
            assert "White is checkmated" in dialog.body and "Black wins" in dialog.body

    run(main())


def test_review_checkmate_shows_result_card_and_marks_king_red():
    from textual.widgets import Static

    async def main():
        game = Game()
        for uci in ("f2f3", "e7e5", "g2g4", "d8h4"):
            game.push(chess.Move.from_uci(uci))
        app = ChessApp()
        app.engines, app.engine_name, app.engine = {}, None, None
        async with app.run_test(size=(120, 44)) as pilot:
            app.push_screen(ReviewScreen(game, {"Event": "Fool's mate", "Result": "0-1"}, 4))
            await until(pilot, lambda: isinstance(app.screen, ReviewScreen))
            review = app.screen
            await pilot.press("end")
            await until(pilot, lambda: isinstance(app.screen, ResultScreen))
            card = app.screen
            assert card.heading == "Checkmate"
            assert "Black wins" in card.body and "White is checkmated" in card.body
            assert review.shown.is_checkmate()
            assert review.query_one(BoardView)._bg(chess.E1) == app.skin.checkmate
            info = str(review.query_one("#game-info", Static).render())
            assert "Checkmate" in info and "Black wins" in info

    run(main())


def test_checkmate_against_the_player_says_you_lose():
    async def main():
        game = Game()
        for uci in ("f2f3", "e7e5", "g2g4", "d8h4"):
            game.push(chess.Move.from_uci(uci))
        app = ChessApp()
        async with app.run_test(size=(120, 44)) as pilot:
            app.push_screen(GameScreen(game, "engine", human=chess.WHITE))
            await until(
                pilot,
                lambda: isinstance(app.screen, GameScreen)
                and app.screen.is_mounted
                and bool(app.screen.query(BoardView)),
            )
            app.screen._changed()
            await until(pilot, lambda: isinstance(app.screen, MenuScreen))
            dialog = app.screen
            assert dialog.heading == "Checkmate — You lose"
            assert "You are checkmated" in dialog.body and "wins" in dialog.body

    run(main())


def test_pgn_picker_mouse_click_selects_a_file(tmp_path):
    async def main():
        pgn = tmp_path / "sample.pgn"
        pgn.write_text('[Event "Picker"]\n[White "A"]\n[Black "B"]\n1. e4 *\n')
        (tmp_path / "sub").mkdir()
        app = ChessApp()
        async with app.run_test(size=(100, 38)) as pilot:
            result = []
            app.push_screen(PgnPickerScreen(str(tmp_path)), result.append)
            await until(
                pilot,
                lambda: isinstance(app.screen, PgnPickerScreen)
                and app.screen.is_mounted
                and bool(app.screen.query(OptionList)),
            )
            picker = app.screen
            listing = picker.query_one("#dirlist", OptionList)
            prompts = [str(option.prompt) for option in listing.options]
            row = next(i for i, prompt in enumerate(prompts) if "📄 sample.pgn" in prompt)
            await pilot.click(listing, offset=(2, row))
            await pilot.pause()
            assert result == [str(pgn)]

    run(main())


def test_review_multi_game_pgn_lists_games_and_opens_selection(tmp_path):
    from textual.widgets import Input

    async def main():
        pgn = tmp_path / "multi.pgn"
        pgn.write_text(
            '[Event "One"]\n[White "A"]\n[Black "B"]\n[Result "1-0"]\n\n'
            "1. e4 e5 2. Qh5 Nc6 3. Bc4 Nf6 4. Qxf7# 1-0\n\n"
            '[Event "Two"]\n[White "C"]\n[Black "D"]\n[Result "0-1"]\n\n'
            "1. f3 e5 2. g4 Qh4# 0-1\n"
        )
        app = ChessApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await choose(pilot, app, "pgn")
            await until(pilot, lambda: isinstance(app.screen, MenuScreen))
            await choose(pilot, app, "browse")
            await until(
                pilot,
                lambda: isinstance(app.screen, PgnPickerScreen)
                and app.screen.is_mounted
                and bool(app.screen.query(Input)),
            )
            field = app.screen.query_one(Input)
            field.focus()
            field.value = str(pgn)
            await pilot.press("enter")
            await until(pilot, lambda: isinstance(app.screen, GameListScreen))
            listing = app.screen.query_one(OptionList)
            assert len(listing.options) == 2
            listing.highlighted = 1
            await pilot.press("enter")
            await until(pilot, lambda: isinstance(app.screen, ReviewScreen))
            review = app.screen
            assert review.headers["Event"] == "Two"
            assert len(review.game.board.move_stack) == 4

    run(main())


def test_review_eval_bar_stays_beside_board_and_full_height_when_stacked():
    from textual.widgets import Static

    async def main():
        game = Game()
        for uci in ("e2e4", "e7e5"):
            game.push(chess.Move.from_uci(uci))
        app = ChessApp()
        app.engines, app.engine_name, app.engine = {}, None, None
        async with app.run_test(size=(90, 40)) as pilot:
            app.push_screen(ReviewScreen(game, {"Event": "Layout"}, 2))
            await until(
                pilot,
                lambda: isinstance(app.screen, ReviewScreen)
                and app.screen.is_mounted
                and bool(app.screen.query(BoardView))
                and bool(app.screen.query("#evalbar")),
            )
            screen = app.screen
            screen._layout()
            await pilot.pause()
            board = screen.query_one(BoardView)
            bar = screen.query_one("#evalbar", Static)
            assert screen.stacked
            assert bar.display
            assert bar.region.x == board.region.x + board.region.width
            assert bar.region.height == board.region.height

    run(main())


def test_review_move_classification_tints_destination_square():
    async def main():
        game = Game()
        game.push(chess.Move.from_uci("e2e4"))
        app = ChessApp()
        app.engines, app.engine_name, app.engine = {}, None, None
        async with app.run_test(size=(120, 44)) as pilot:
            app.push_screen(ReviewScreen(game, {"Event": "Colours"}, 1))
            await until(
                pilot,
                lambda: isinstance(app.screen, ReviewScreen)
                and app.screen.is_mounted
                and bool(app.screen.query(BoardView)),
            )
            review = app.screen
            review.view = 1
            board = review.query_one(BoardView)
            for classification in (
                analysis.BEST,
                analysis.EXCELLENT,
                analysis.GOOD,
                analysis.INACCURACY,
                analysis.MISTAKE,
                analysis.BLUNDER,
            ):
                review.reports[1] = analysis.MoveReport(
                    1, "e4", chess.WHITE, classification, 0, None
                )
                review._refresh()
                assert review.move_highlight == chess.E4
                assert board._bg(chess.E4) == classification.color

            review.reports[1] = analysis.MoveReport(1, "e4", chess.WHITE, analysis.UNKNOWN, None, None)
            review._refresh()
            assert review.move_highlight is None

    run(main())


def test_review_move_rows_are_compact_and_totals_are_listed():
    from textual.widgets import Static

    async def main():
        game = Game()
        for uci in ("e2e4", "e7e5", "g1f3"):
            game.push(chess.Move.from_uci(uci))
        app = ChessApp()
        app.engines, app.engine_name, app.engine = {}, None, None
        async with app.run_test(size=(120, 44)) as pilot:
            app.push_screen(ReviewScreen(game, {"Event": "Totals"}, 3))
            await until(
                pilot,
                lambda: isinstance(app.screen, ReviewScreen)
                and app.screen.is_mounted
                and bool(app.screen.query("#move-list")),
            )
            review = app.screen
            review.reports = {
                1: analysis.MoveReport(1, "e4", chess.WHITE, analysis.BEST, 0, None),
                2: analysis.MoveReport(2, "e5", chess.BLACK, analysis.BLUNDER, 350, None),
            }
            review._refresh()

            lines = str(review.query_one("#move-list", Static).render()).splitlines()
            assert len(lines) == 2
            assert lines[0].startswith("  1.")
            assert "e4" in lines[0] and "★" in lines[0]
            assert "e5" in lines[0] and "??" in lines[0]
            assert lines[1].startswith("  2.") and "Nf3" in lines[1]

            summary = str(review.query_one("#move-summary", Static).render())
            assert "Totals" in summary
            assert "Best 1" in summary and "Blunder 1" in summary
            assert "Good 0" in summary and "Mistake 0" in summary

    run(main())


def test_review_move_badge_is_a_three_row_block_banner():
    from textual.widgets import Static

    async def main():
        game = Game()
        for uci in ("e2e4", "e7e5"):
            game.push(chess.Move.from_uci(uci))
        app = ChessApp()
        app.engines, app.engine_name, app.engine = {}, None, None
        async with app.run_test(size=(120, 44)) as pilot:
            app.push_screen(ReviewScreen(game, {"Event": "Badge"}, 2))
            await until(
                pilot,
                lambda: isinstance(app.screen, ReviewScreen)
                and app.screen.is_mounted
                and bool(app.screen.query("#move-badge")),
            )
            review = app.screen
            review.view = 1
            review.reports[1] = analysis.MoveReport(1, "e4", chess.WHITE, analysis.BLUNDER, 350, None)
            review._refresh()

            badge = review.query_one("#move-badge", Static)
            assert badge.display
            rows = str(badge.render()).splitlines()
            assert len(rows) == 3
            assert all(rows)
            assert "█" in "".join(rows)

            review.view = 0
            review._refresh()
            assert not badge.display

    run(main())


def test_review_move_list_click_jumps_to_that_ply():
    from hyprchess.screens import MOVE_LIST_BLACK_X, MoveList

    async def main():
        game = Game()
        for uci in ("e2e4", "e7e5", "g1f3", "b8c6"):
            game.push(chess.Move.from_uci(uci))
        app = ChessApp()
        app.engines, app.engine_name, app.engine = {}, None, None
        async with app.run_test(size=(120, 44)) as pilot:
            app.push_screen(ReviewScreen(game, {"Event": "Click"}, 4))
            await until(
                pilot,
                lambda: isinstance(app.screen, ReviewScreen)
                and app.screen.is_mounted
                and bool(app.screen.query("#move-list")),
            )
            review = app.screen
            await pilot.pause()
            listing = review.query_one("#move-list", MoveList)

            assert await pilot.click(listing, offset=(0, 0))
            await pilot.pause()
            assert review.view == 1

            assert await pilot.click(listing, offset=(MOVE_LIST_BLACK_X + 1, 0))
            await pilot.pause()
            assert review.view == 2

            assert await pilot.click(listing, offset=(0, 1))
            await pilot.pause()
            assert review.view == 3

            assert await pilot.click(listing, offset=(MOVE_LIST_BLACK_X + 1, 1))
            await pilot.pause()
            assert review.view == 4

    run(main())
