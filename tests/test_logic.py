"""Game rules, clocks, skins and the network handshake, with no UI."""

import asyncio
import json

import chess
import pytest

from hyprchess import net
from hyprchess.analysis import (
    BEST,
    BLUNDER,
    GameAnalysis,
    EXCELLENT,
    GOOD,
    INACCURACY,
    MISTAKE,
    MoveReport,
    PositionEval,
    classify_loss,
    classify_move,
    describe_score,
    engine_configuration,
    engine_info,
    format_advantage,
    format_score,
    pv_to_san,
    score_delta,
    score_fraction,
)
from hyprchess.game import Game, TimeControl, format_clock, result_winner
from hyprchess.skins import build_skin, load_skins, parse_sprites

WALNUT = {"board": {"light": "#b89b72", "dark": "#7d5a3c"}, "white": {"body": "#ffffff", "detail": "#999999"},
          "black": {"body": "#000000", "detail": "#666666"}}


def play(game: Game, *ucis: str) -> None:
    for uci in ucis:
        game.push(chess.Move.from_uci(uci))


def test_captures_and_material_include_en_passant_and_promotion():
    game = Game()
    play(game, "e2e4", "a7a6", "e4e5", "d7d5", "e5d6", "a6a5", "d6c7", "a5a4", "c7b8q")  # ep, then promotes with capture
    sans, lost = game.history()
    assert sans[4] == "exd6" and sans[-1] == "cxb8=Q"
    assert lost[chess.BLACK] == [chess.KNIGHT, chess.PAWN, chess.PAWN] and lost[chess.WHITE] == []
    assert Game.material(game.board) == 5 + 8  # two pawns and a knight up, plus pawn became queen


def test_clock_runs_only_after_first_move_adds_increment_and_flags():
    game = Game(TimeControl(60, 2))
    game.tick(5)
    assert game.clock[chess.WHITE] == 60  # not started
    play(game, "e2e4")
    assert game.clock[chess.WHITE] == 62
    game.tick(10)
    assert game.clock[chess.BLACK] == 50 and game.outcome() is None
    game.tick(60)
    assert game.clock[chess.BLACK] == 0 and game.outcome() == ("1-0", "on time")
    game.tick(60)
    assert game.outcome() == ("1-0", "on time")


def test_online_tick_never_flags_the_other_side():
    game = Game(TimeControl(60))
    play(game, "e2e4")
    game.tick(999, only=chess.WHITE)  # it is Black's clock running; White's client must wait for Black's word
    assert game.clock[chess.BLACK] == 0 and game.outcome() is None


def test_undo_redo_and_new_move_clears_redo():
    game = Game()
    play(game, "e2e4", "e7e5")
    game.undo(2)
    assert not game.board.move_stack and game.redo_one() and len(game.board.move_stack) == 2
    game.undo(1)
    play(game, "d7d5")
    assert not game.redo_one()


def test_pgn_round_trip_keeps_moves_and_resignation():
    game = Game(TimeControl(300, 3))
    play(game, "e2e4", "e7e5", "g1f3")
    game.resign(chess.BLACK)
    text = game.pgn("Ann", "Bob")
    assert '[TimeControl "300+3"]' in text and '[Result "1-0"]' in text
    loaded, headers = Game.from_pgn(text)
    assert loaded.board.move_stack == game.board.move_stack and headers["White"] == "Ann"
    assert loaded.outcome() == ("1-0", "resignation")
    with pytest.raises(ValueError):
        Game.from_pgn('[FEN "8/8/8/8/8/8/8/K6k w - - 0 1"]\n[SetUp "1"]\n\n1. Ka2 *')


def test_format_clock():
    assert [format_clock(s) for s in (3725, 600, 61, 9.44, 0)] == ["1:02:05", "10:00", "1:01", "0:09.4", "0:00.0"]


def test_result_winner_reads_pgn_results():
    assert result_winner("1-0") == chess.WHITE
    assert result_winner("0-1") == chess.BLACK
    assert result_winner("1/2-1/2") is None
    assert result_winner("*") is None


def test_skin_defaults_and_rejections(tmp_path):
    skin = build_skin(WALNUT, "file-name")
    assert skin.name == "file-name" and skin.cursor.startswith("#") and 12 in skin.sprites
    assert skin.checkmate == "#ff3b30"
    assert build_skin({**WALNUT, "board": {**WALNUT["board"], "checkmate": "#123456"}}, "x").checkmate == "#123456"
    for broken in (
        {**WALNUT, "board": {"light": "red", "dark": "#000000"}},
        {**WALNUT, "white": {"body": "#ffffff"}},
        {**WALNUT, "glyphs": {"king": "KK"}},
        {**WALNUT, "glyphs": {"wizard": "W"}},
        {**WALNUT, "sprites": {"8": "XX\nXX"}},
        {**WALNUT, "sprites": {"9": "X"}},
        {**WALNUT, "board": "nope"},
    ):
        with pytest.raises(ValueError):
            build_skin(broken, "x")
    with pytest.raises(ValueError):
        parse_sprites("")


def test_user_skin_files_load_override_and_report_errors(tmp_path):
    (tmp_path / "ocean.toml").write_text('name = "Ocean"\n[board]\nlight="#9cc4d9"\ndark="#3d6f8a"\n'
                                         '[white]\nbody="#ffffff"\ndetail="#8fa3ad"\n[black]\nbody="#101820"\ndetail="#5f7f91"\n')
    (tmp_path / "walnut2.toml").write_text('name = "Walnut"\n[board]\nlight="#111111"\ndark="#222222"\n'
                                           '[white]\nbody="#ffffff"\ndetail="#8fa3ad"\n[black]\nbody="#101820"\ndetail="#5f7f91"\n')
    (tmp_path / "bad.toml").write_text("this is not toml = = =")
    (tmp_path / "half.toml").write_text('[board]\nlight="#9cc4d9"\n')
    skins, errors = load_skins(tmp_path)
    names = [s.name for s in skins]
    assert "Ocean" in names and names.count("Walnut") == 1
    assert next(s for s in skins if s.name == "Walnut").square[True] == "#111111"
    assert len(errors) == 2 and any("bad.toml" in e for e in errors) and any("half.toml" in e for e in errors)


def test_hello_validation():
    good = {"t": "hello", "v": net.VERSION, "host_color": "white", "base": 300, "inc": 2}
    assert net.valid_hello(good) and net.valid_hello({**good, "base": None})
    for bad in (None, {}, {**good, "v": 99}, {**good, "host_color": "green"}, {**good, "base": 10**9},
                {**good, "base": "300"}, {**good, "inc": -1}, {**good, "inc": True}, {**good, "base": True}):
        assert not net.valid_hello(bad), bad


def test_host_join_exchange_and_garbage_is_dropped():
    async def main():
        peers = []
        server = await net.host(0, peers.append)
        port = server.sockets[0].getsockname()[1]
        guest = await net.join(f"127.0.0.1:{port}")
        await guest.send(t="move", uci="e2e4")
        await asyncio.sleep(0.05)
        assert await peers[0].recv() == {"t": "move", "uci": "e2e4"}
        guest.writer.write(b"not json\n")
        await guest.writer.drain()
        assert await peers[0].recv() is None
        guest.close()
        server.close()
        with pytest.raises(ValueError):
            await net.join("")
        with pytest.raises(OSError):
            await net.join(f"127.0.0.1:{port}", timeout=2)

    asyncio.run(main())


def test_analysis_scores_are_formatted_from_white_perspective():
    from chess.engine import Cp, Mate, PovScore

    assert format_score(None) == "—"
    assert format_score(PovScore(Cp(35), chess.WHITE)) == "+0.35"
    assert format_score(PovScore(Cp(-120), chess.WHITE)) == "-1.20"
    assert format_score(PovScore(Cp(35), chess.BLACK)) == "-0.35"
    assert format_score(PovScore(Mate(3), chess.WHITE)) == "+M3"
    assert format_score(PovScore(Mate(-2), chess.BLACK)) == "+M2"
    assert format_score(PovScore(Mate(0), chess.WHITE)) == "-#"
    assert format_score(PovScore(Mate(0), chess.BLACK)) == "+#"
    assert score_fraction(None) == 0.5
    assert score_fraction(PovScore(Mate(1), chess.WHITE)) == 1.0
    assert score_fraction(PovScore(Mate(-1), chess.WHITE)) == 0.0
    assert score_fraction(PovScore(Mate(0), chess.WHITE)) == 0.0
    assert score_fraction(PovScore(Mate(0), chess.BLACK)) == 1.0
    assert 0.5 < score_fraction(PovScore(Cp(400), chess.WHITE)) < 1.0
    assert round(score_fraction(PovScore(Cp(0), chess.WHITE)), 3) == 0.5


def test_analysis_score_delta_and_pv():
    from chess.engine import Cp, Mate, PovScore

    assert score_delta(PovScore(Cp(20), chess.WHITE), PovScore(Cp(70), chess.WHITE)) == "+0.50"
    assert score_delta(PovScore(Cp(20), chess.BLACK), PovScore(Cp(70), chess.BLACK)) == "-0.50"
    assert score_delta(None, PovScore(Cp(0), chess.WHITE)) is None
    assert score_delta(PovScore(Mate(2), chess.WHITE), PovScore(Cp(500), chess.WHITE)) is None
    board = chess.Board()
    pv = [chess.Move.from_uci(u) for u in ("e2e4", "e7e5", "g1f3", "b8c6")]
    assert pv_to_san(board, pv) == "e4 e5 Nf3 Nc6"
    assert pv_to_san(board, pv[:2], limit=1) == "e4"
    assert pv_to_san(board, [chess.Move.from_uci("e2e5")]) == ""


def test_analysis_describes_the_advantaged_side():
    from chess.engine import Cp, Mate, PovScore

    assert describe_score(PovScore(Cp(35), chess.WHITE)) == "White 0.35"
    assert describe_score(PovScore(Cp(-120), chess.WHITE)) == "Black 1.20"
    assert describe_score(PovScore(Cp(0), chess.WHITE)) == "Equal"
    assert describe_score(PovScore(Mate(4), chess.WHITE)) == "White mates in 4"
    assert describe_score(PovScore(Mate(-3), chess.WHITE)) == "Black mates in 3"
    assert describe_score(PovScore(Mate(0), chess.WHITE)) == "Black wins by checkmate"
    assert describe_score(PovScore(Mate(0), chess.BLACK)) == "White wins by checkmate"
    assert describe_score(None) == "No evaluation"
    assert format_advantage(PovScore(Cp(35), chess.WHITE)) == "W+0.35"
    assert format_advantage(PovScore(Cp(120), chess.BLACK)) == "B+1.20"
    assert format_advantage(PovScore(Cp(0), chess.BLACK)) == "="
    assert format_advantage(PovScore(Mate(3), chess.WHITE)) == "WM3"
    assert format_advantage(PovScore(Mate(-3), chess.WHITE)) == "BM3"
    assert format_advantage(PovScore(Mate(0), chess.WHITE)) == "B#"
    assert format_advantage(PovScore(Mate(0), chess.BLACK)) == "W#"


def test_move_classification_uses_documented_centipawn_bands():
    from chess.engine import Cp, Mate, PovScore

    def position(ply, score, best=None):
        return PositionEval(ply, score, best, (), "", 20)

    board = chess.Board()
    e4 = chess.Move.from_uci("e2e4")
    before = position(1, PovScore(Cp(20), chess.WHITE), best=e4)
    # After White's move it is Black to move; -20 from Black's view is +20 for White.
    after = position(2, PovScore(Cp(-20), chess.BLACK))
    report = classify_move(ply=1, san="e4", played=e4, before=before, after=after)
    assert report.classification is BEST and report.loss_cp == 0

    after = position(2, PovScore(Cp(30), chess.BLACK))  # White now -30: 50 cp lost
    report = classify_move(ply=1, san="e4", played=None, before=before, after=after)
    assert report.classification is GOOD and report.loss_cp == 50

    after = position(2, PovScore(Cp(130), chess.BLACK))  # 150 cp lost
    report = classify_move(ply=1, san="e4", played=None, before=before, after=after)
    assert report.classification is MISTAKE and report.loss_cp == 150

    after = position(2, PovScore(Cp(250), chess.BLACK))  # 270 cp lost
    report = classify_move(ply=1, san="e4", played=None, before=before, after=after)
    assert report.classification is BLUNDER and report.loss_cp == 270


def test_move_classification_handles_forced_mates():
    from chess.engine import Cp, Mate, PovScore

    def position(ply, score):
        return PositionEval(ply, score, None, (), "", 20)

    # White has mate in 2 and throws it away.
    report = classify_move(
        ply=1, san="e4", played=None,
        before=position(1, PovScore(Mate(2), chess.WHITE)),
        after=position(2, PovScore(Cp(0), chess.BLACK)),
    )
    assert report.classification is BLUNDER

    # White was being mated and escapes.
    report = classify_move(
        ply=1, san="e4", played=None,
        before=position(1, PovScore(Mate(-3), chess.WHITE)),
        after=position(2, PovScore(Cp(0), chess.BLACK)),
    )
    assert report.classification is EXCELLENT


def test_move_classification_treats_delivered_checkmate_as_best():
    from chess.engine import Mate, PovScore

    def position(ply, score):
        return PositionEval(ply, score, None, (), "", 20)

    # White mates in one: the mating move leaves Black (to move) checkmated.
    report = classify_move(
        ply=1, san="Qh5#", played=None,
        before=position(1, PovScore(Mate(1), chess.WHITE)),
        after=position(2, PovScore(Mate(0), chess.BLACK)),
    )
    assert report.classification is BEST

    # Black mates on move 2; from Black's view the after-position is mate 0.
    report = classify_move(
        ply=2, san="Qh4#", played=None,
        before=position(2, PovScore(Mate(1), chess.BLACK)),
        after=position(3, PovScore(Mate(0), chess.WHITE)),
    )
    assert report.classification is BEST


def test_game_analysis_report_has_versioned_json_schema():
    from chess.engine import Cp, PovScore

    game = Game()
    play(game, "e2e4", "e7e5")
    e4, e5 = chess.Move.from_uci("e2e4"), chess.Move.from_uci("e7e5")
    evals = {
        0: PositionEval(0, PovScore(Cp(20), chess.WHITE), e4, (e4,), "e4", 20),
        1: PositionEval(1, PovScore(Cp(-20), chess.BLACK), e5, (e5,), "e5", 20),
        2: PositionEval(2, PovScore(Cp(10), chess.WHITE), None, (), "", 20),
    }
    reports = {
        1: classify_move(ply=1, san="e4", played=e4, before=evals[0], after=evals[1]),
        2: classify_move(ply=2, san="e5", played=e5, before=evals[1], after=evals[2]),
    }
    report = GameAnalysis.from_game(
        game,
        {"Event": "Test", "White": "A", "Black": "B", "Result": "*"},
        evals,
        reports,
        engine_name="Stockfish",
        seconds_per_position=0.6,
        generated_at="2026-01-02T03:04:05+00:00",
    )
    data = report.as_dict()
    assert data["schema_version"] == 2
    assert data["generated_at"] == "2026-01-02T03:04:05+00:00"
    assert data["perspective"] == "white"
    assert data["conventions"]["eval_perspective"] == "white"
    assert data["conventions"]["loss_perspective"] == "mover"
    assert data["conventions"]["mate_distance_unit"] == "moves"
    assert data["classification"]["thresholds_cp"] == {
        "excellent": {"min_loss": 0, "max_loss": 25},
        "good": {"min_loss": 26, "max_loss": 50},
        "inaccuracy": {"min_loss": 51, "max_loss": 100},
        "mistake": {"min_loss": 101, "max_loss": 200},
        "blunder": {"min_loss": 201, "max_loss": None},
    }
    assert data["game"]["plies"] == 2 and data["game"]["result"] == "*"
    assert data["analysis"] == {
        "engine": "Stockfish",
        "seconds_per_position": 0.6,
        "engine_info": None,
        "configuration": None,
        "positions_analysed": 3,
        "positions_expected": 3,
        "complete": True,
    }
    assert data["summary"]["white"]["best"] == 1
    assert data["summary"]["black"]["best"] == 1
    assert data["summary"]["final_eval"] == "+0.10"
    assert data["summary"]["final_score_type"] == "cp"
    assert data["summary"]["biggest_mate_error"] is None
    assert data["positions"][0]["score_type"] == "cp"
    assert data["positions"][0]["seldepth"] is None
    assert len(data["positions"]) == 3 and data["positions"][1]["fen"] == game.at(1).fen()
    first = data["moves"][0]
    assert first["san"] == "e4" and first["uci"] == "e2e4" and first["color"] == "white"
    assert first["fen_before"] == chess.STARTING_FEN and first["fen_after"] == game.at(1).fen()
    assert first["eval_before"]["score"] == "+0.20" and first["eval_after"]["score"] == "+0.20"
    assert first["eval_before"]["score_type"] == "cp"
    assert first["classification"] == "best" and first["best_san"] == "e4" and first["pv_before"] == "e4"
    assert first["loss_kind"] == "centipawn" and first["classification_basis"] == "best_move"
    assert first["mate"] is None
    assert json.loads(report.to_json()) == data


def test_incomplete_game_analysis_is_marked_as_such():
    from chess.engine import Cp, PovScore

    game = Game()
    play(game, "e2e4")
    evals = {0: PositionEval(0, PovScore(Cp(0), chess.WHITE), None, (), "", 12)}
    report = GameAnalysis.from_game(game, {}, evals, {}, engine_name=None)
    data = report.as_dict()
    assert data["analysis"]["complete"] is False
    assert data["analysis"]["positions_analysed"] == 1
    assert data["analysis"]["positions_expected"] == 2
    assert data["summary"]["moves"] == 0 and data["moves"] == []


def test_classification_thresholds_are_inclusive_and_best_is_separate():
    assert classify_loss(0) is EXCELLENT
    assert classify_loss(25) is EXCELLENT
    assert classify_loss(26) is GOOD
    assert classify_loss(50) is GOOD
    assert classify_loss(51) is INACCURACY
    assert classify_loss(100) is INACCURACY
    assert classify_loss(101) is MISTAKE
    assert classify_loss(200) is MISTAKE
    assert classify_loss(201) is BLUNDER
    assert classify_loss(0, best_move_played=True) is BEST
    assert classify_loss(900, best_move_played=True) is BEST


def test_mate_transitions_are_mover_relative_and_in_moves():
    from chess.engine import Cp, Mate, PovScore

    def position(ply, score):
        return PositionEval(ply, score, None, (), "", 20)

    def mate(ply, distance):
        return position(ply, PovScore(Mate(distance), chess.WHITE))

    def cp(ply=2, value=0):
        return position(ply, PovScore(Cp(value), chess.WHITE))

    cases = [
        # (before, after, classification, transition, allows, misses)
        (cp(1), mate(2, 2), BEST, "found", False, False),
        (mate(1, 2), mate(2, 3), GOOD, "kept", False, False),
        (mate(1, 2), cp(), BLUNDER, "missed", False, True),
        (mate(1, 2), mate(2, -1), BLUNDER, "lost", True, True),
        (cp(1), mate(2, -2), BLUNDER, "allowed", True, False),
        (mate(1, -2), cp(), EXCELLENT, "escaped", False, False),
        (mate(1, -2), mate(2, -4), GOOD, "still_mated", False, False),
        (mate(1, 1), mate(2, 0), BEST, "delivered_checkmate", False, False),
        (cp(1), cp(), EXCELLENT, "none", False, False),
    ]
    for before, after, classification, transition, allows, misses in cases:
        report = classify_move(ply=1, san="e4", played=None, before=before, after=after)
        assert report.classification is classification, (transition, report.classification)
        expected_kind = "centipawn" if transition == "none" else "mate"
        assert report.loss_kind == expected_kind
        assert report.classification_basis == expected_kind
        assert report.mate_transition == transition
        assert report.allows_forced_mate is allows
        assert report.misses_forced_mate is misses
        if transition == "none":
            assert report.mate_before_for_mover is None
            assert report.mate_after_for_mover is None
        else:
            mate_dict = report.mate_dict()
            assert mate_dict is not None
            assert mate_dict["before_for_mover"] == report.mate_before_for_mover
            assert mate_dict["after_for_mover"] == report.mate_after_for_mover


def test_biggest_mate_error_uses_worst_then_earliest_ply():
    game = Game()
    play(game, "e2e4", "e7e5", "g1f3", "b8c6", "f1c4")

    def report(ply, classification, transition="allowed", allows=True):
        return MoveReport(
            ply=ply,
            san=f"m{ply}",
            mover=chess.WHITE if ply % 2 else chess.BLACK,
            classification=classification,
            loss_cp=None,
            score_after=None,
            loss_kind="mate",
            classification_basis="mate",
            mate_before_for_mover=None,
            mate_after_for_mover=-1,
            mate_transition=transition,
            allows_forced_mate=allows,
            misses_forced_mate=transition in ("missed", "lost"),
        )

    reports = {
        1: report(1, BLUNDER),
        2: MoveReport(
            ply=2, san="e5", mover=chess.BLACK, classification=INACCURACY,
            loss_cp=120, score_after=None, loss_kind="centipawn",
            classification_basis="centipawn",
        ),
        3: report(3, BLUNDER),
        5: report(5, MISTAKE, transition="still_mated", allows=False),
    }
    data = GameAnalysis.from_game(game, {}, {}, reports).as_dict()
    biggest = data["summary"]["biggest_mate_error"]
    assert biggest["ply"] == 1  # earliest of the equally severe blunders
    assert biggest["classification"] == "blunder"
    assert biggest["mate_transition"] == "allowed"
    assert biggest["before_for_mover"] is None
    assert biggest["after_for_mover"] == -1
    # biggest_loss keeps its v1 centipawn-only meaning and ignores mate errors.
    assert data["summary"]["biggest_loss"] == {"ply": 2, "san": "e5", "loss_cp": 120}


def test_engine_metadata_comes_from_actual_uci_identification():
    from types import SimpleNamespace

    class FakeEngine:
        id = {"name": "Stockfish 19", "author": "the Stockfish developers"}
        options = {
            "Threads": SimpleNamespace(default=1, max=1024),
            "Hash": SimpleNamespace(default=16, max=33554432),
            "UCI_LimitStrength": SimpleNamespace(default=False, max=None),
            "Skill Level": SimpleNamespace(default=20, max=20),
        }

    assert engine_info(None) is None
    assert engine_configuration(None, time_seconds=0.6) is None
    assert engine_info(FakeEngine()) == {
        "name": "Stockfish 19",
        "author": "the Stockfish developers",
    }
    config = engine_configuration(FakeEngine(), time_seconds=0.6)
    assert config == {
        "search_limit": {"time_seconds": 0.6, "depth": None, "nodes": None},
        "threads": 1,
        "hash_mb": 16,
        "options": {"UCI_LimitStrength": False, "Skill Level": 20},
        "engine_defaults_used": ["Threads", "Hash"],
    }
