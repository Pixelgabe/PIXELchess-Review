"""Engine-analysis helpers: score formatting, move classification and the eval bar.

Everything here converts engine output to a single perspective (White by
default), so the review UI, the evaluation bar, and any future coaching code
agree on sign conventions.  These functions do not run an engine themselves and
carry no UI dependencies, which keeps them easy to test and reuse.

Perspective conventions
-----------------------
Engine evaluations (``score``, ``score_cp``, ``mate`` and the eval bar) are
**White-relative**: positive values favour White.  ``loss_cp`` is
**mover-relative**: positive means the move made the mover's position worse.
Mate distances are measured in **moves**, not plies.

Move classification thresholds
------------------------------
A move is judged by comparing two engine evaluations made with the same engine
settings: the position before the move and the position after it.  The eval is
converted to the mover's point of view, so a *loss* is positive when the move
made the mover's position worse.  The bands are inclusive:

* ``Best``       - the engine's top move was played (kept separate from the bands).
* ``Excellent``  - loss 0-25 centipawns.
* ``Good``       - loss 26-50 centipawns.
* ``Inaccuracy`` - loss 51-100 centipawns.
* ``Mistake``    - loss 101-200 centipawns.
* ``Blunder``    - loss 201 centipawns or more.

Forced mate overrides the centipawn bands: walking out of a forced mate for the
mover, or allowing a forced mate against the mover, is a blunder; keeping or
finding a forced mate is best.  Moves where either evaluation is unavailable
are marked ``Unknown`` rather than guessed.

The structured export keeps these conventions explicit in its ``conventions``
and ``classification`` blocks, and adds mate-specific per-move fields.  See
``docs/analysis-schema.md`` for the complete field reference.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

import chess
import chess.engine

from .game import Game

DEFAULT_PERSPECTIVE = chess.WHITE
MATE_CP = 100_000  # internal ordering value for mate scores; never shown as an eval
SCHEMA_VERSION = 2

# Inclusive upper bounds for the centipawn classification bands.  Best is a
# separate rule (the engine's top move was played), not a centipawn band.
EXCELLENT_MAX_CP = 25
GOOD_MAX_CP = 50
INACCURACY_MAX_CP = 100
MISTAKE_MAX_CP = 200

# Mate transitions use python-chess mate distances, which count *moves* (not
# plies) and are always expressed from the mover's point of view.
MATE_TRANSITIONS = (
    "none",
    "found",
    "kept",
    "missed",
    "lost",
    "allowed",
    "escaped",
    "still_mated",
    "delivered_checkmate",
)


def format_score(
    score: chess.engine.PovScore | None,
    perspective: chess.Color = DEFAULT_PERSPECTIVE,
) -> str:
    """Format an engine score from *perspective*.

    Returns ``+0.35``/``-1.20`` for centipawn scores, ``+M5``/``-M3`` for mate
    scores, and ``—`` when the engine gave no score.  Positive values always
    favour *perspective* (White by default).
    """
    if score is None:
        return "—"
    pov = score.pov(perspective)
    mate = pov.mate()
    if mate is not None:
        if mate == 0:  # checkmate: the side to move has no escape
            return "-#" if score.turn == perspective else "+#"
        return f"+M{mate}" if mate > 0 else f"-M{abs(mate)}"
    cp = pov.score()
    if cp is None:
        return "—"
    return f"{cp / 100:+.2f}"


def describe_score(
    score: chess.engine.PovScore | None,
    perspective: chess.Color = DEFAULT_PERSPECTIVE,
) -> str:
    """Say who is better and by how much, e.g. ``White +0.35`` or ``Black mates in 4``."""
    if score is None:
        return "No evaluation"
    pov = score.pov(perspective)
    mate = pov.mate()
    if mate is not None:
        if mate == 0:  # checkmate: the side to move has no escape
            return f"{_color_name(not score.turn)} wins by checkmate"
        if mate > 0:
            return f"{_color_name(perspective)} mates in {mate}"
        return f"{_color_name(not perspective)} mates in {abs(mate)}"
    cp = pov.score()
    if cp is None:
        return "No evaluation"
    if cp == 0:
        return "Equal"
    side = perspective if cp > 0 else not perspective
    return f"{_color_name(side)} {abs(cp) / 100:.2f}"


def format_advantage(
    score: chess.engine.PovScore | None,
    perspective: chess.Color = DEFAULT_PERSPECTIVE,
) -> str:
    """Compact advantage for a move list cell: ``W+0.35``, ``B+1.20``, ``WM5``, ``B#``."""
    if score is None:
        return "…"
    pov = score.pov(perspective)
    mate = pov.mate()
    if mate is not None:
        if mate == 0:  # checkmate: the side to move has no escape
            return "W#" if score.turn != perspective else "B#"
        return f"{'W' if mate > 0 else 'B'}M{abs(mate)}"
    cp = pov.score()
    if cp is None:
        return "…"
    if cp == 0:
        return "="
    return f"{'W' if cp > 0 else 'B'}+{abs(cp) / 100:.2f}"


def _color_name(color: chess.Color) -> str:
    return "White" if color == chess.WHITE else "Black"


def score_fraction(
    score: chess.engine.PovScore | None,
    perspective: chess.Color = DEFAULT_PERSPECTIVE,
) -> float:
    """Return the share of the position (0..1) that favours *perspective*.

    Centipawn scores are mapped through a logistic curve (about 400 cp is a
    90% share); mate scores map to 1.0 or 0.0.  No score means an even 0.5.
    """
    if score is None:
        return 0.5
    pov = score.pov(perspective)
    mate = pov.mate()
    if mate is not None:
        if mate == 0:  # checkmate: the side to move has no escape
            return 1.0 if score.turn != perspective else 0.0
        return 1.0 if mate > 0 else 0.0
    cp = pov.score(mate_score=10000)
    if cp is None:
        return 0.5
    return 1.0 / (1.0 + 10.0 ** (-cp / 400.0))


def score_cp(
    score: chess.engine.PovScore | None,
    perspective: chess.Color = DEFAULT_PERSPECTIVE,
) -> int | None:
    """A comparable centipawn value from *perspective*; mate distance is preserved.

    This is for ordering and classification only.  It deliberately is not a
    user-facing evaluation: a mate is represented by a large value, not by a
    pretend centipawn score.
    """
    if score is None:
        return None
    pov = score.pov(perspective)
    mate = pov.mate()
    if mate is not None:
        if mate == 0:  # checkmate: the side to move has no escape
            return MATE_CP if score.turn != perspective else -MATE_CP
        distance = min(abs(mate), 100)
        value = MATE_CP - distance * 1000
        return value if mate > 0 else -value
    return pov.score()


def score_type(score: chess.engine.PovScore | None) -> str:
    """Whether *score* is ordinary centipawns, a mate score, or absent.

    ``score_cp`` uses a large sentinel for mates, so consumers must not treat it
    as a real pawn value when this returns ``"mate"``.
    """
    if score is None:
        return "none"
    return "mate" if score.is_mate() else "cp"


def pv_to_san(board: chess.Board, pv: list[chess.Move] | tuple[chess.Move, ...], limit: int = 6) -> str:
    """Convert a principal variation to SAN, stopping at the first illegal move."""
    replay = board.copy()
    parts = []
    for move in pv[:limit]:
        if move not in replay.legal_moves:
            break
        parts.append(replay.san(move))
        replay.push(move)
    return " ".join(parts)


def analysis_options(engine: chess.engine.UciProtocol) -> dict:
    """Engine options that put an engine at full strength for analysis, when supported."""
    options: dict = {}
    if "UCI_LimitStrength" in engine.options:
        options["UCI_LimitStrength"] = False
    if "Skill Level" in engine.options:
        option = engine.options["Skill Level"]
        options["Skill Level"] = option.max if option.max is not None else 20
    return options


def engine_info(engine: chess.engine.UciProtocol | None) -> dict | None:
    """The engine's own UCI identification, or None when no engine is available."""
    if engine is None:
        return None
    ident = getattr(engine, "id", None) or {}
    return {"name": ident.get("name"), "author": ident.get("author")}


def engine_configuration(
    engine: chess.engine.UciProtocol | None,
    *,
    time_seconds: float | None = None,
) -> dict | None:
    """The settings Hyprchess actually applies for an analysis pass.

    Threads and Hash are not overridden, so their current engine defaults are
    recorded; values are null when the engine does not advertise them.
    """
    if engine is None:
        return None

    def option_default(name: str):
        option = engine.options.get(name)
        return option.default if option is not None else None

    defaults_used = [name for name in ("Threads", "Hash") if name in engine.options]
    return {
        "search_limit": {"time_seconds": time_seconds, "depth": None, "nodes": None},
        "threads": option_default("Threads"),
        "hash_mb": option_default("Hash"),
        "options": analysis_options(engine),
        "engine_defaults_used": defaults_used,
    }


def score_delta(
    before: chess.engine.PovScore | None,
    after: chess.engine.PovScore | None,
    perspective: chess.Color = DEFAULT_PERSPECTIVE,
) -> str | None:
    """Change in centipawn score between two positions, or None when unavailable.

    Mate scores are deliberately not converted to pawns; the caller gets ``None``
    rather than a misleading number.  Positive means the position improved for
    *perspective*.
    """
    if before is None or after is None:
        return None
    a, b = before.pov(perspective), after.pov(perspective)
    if a.is_mate() or b.is_mate():
        return None
    a_cp, b_cp = a.score(), b.score()
    if a_cp is None or b_cp is None:
        return None
    return f"{(b_cp - a_cp) / 100:+.2f}"


@dataclass(frozen=True)
class MoveClassification:
    """A named quality band with a short terminal symbol and colour."""

    key: str
    label: str
    symbol: str
    color: str


BEST = MoveClassification("best", "Best", "★", "bright_green")
EXCELLENT = MoveClassification("excellent", "Excellent", "!", "green")
GOOD = MoveClassification("good", "Good", "✓", "cyan")
INACCURACY = MoveClassification("inaccuracy", "Inaccuracy", "?!", "yellow")
MISTAKE = MoveClassification("mistake", "Mistake", "?", "orange3")
BLUNDER = MoveClassification("blunder", "Blunder", "??", "red")
UNKNOWN = MoveClassification("unknown", "Unknown", "·", "dim")

CLASSIFICATIONS = (BEST, EXCELLENT, GOOD, INACCURACY, MISTAKE, BLUNDER)


@dataclass(frozen=True)
class PositionEval:
    """One engine evaluation, kept in a form that is easy to cache and serialise."""

    ply: int
    score: chess.engine.PovScore | None
    best_move: chess.Move | None
    pv: tuple[chess.Move, ...]
    pv_san: str
    depth: int | None
    seldepth: int | None = None
    nodes: int | None = None
    nps: int | None = None
    time_seconds: float | None = None
    hashfull_permille: int | None = None
    tbhits: int | None = None

    @classmethod
    def from_info(cls, ply: int, board: chess.Board, info: dict) -> "PositionEval":
        pv = tuple(info.get("pv") or ())
        return cls(
            ply=ply,
            score=info.get("score"),
            best_move=pv[0] if pv else None,
            pv=pv,
            pv_san=pv_to_san(board, pv),
            depth=info.get("depth"),
            seldepth=info.get("seldepth"),
            nodes=info.get("nodes"),
            nps=info.get("nps"),
            time_seconds=info.get("time"),
            hashfull_permille=info.get("hashfull"),
            tbhits=info.get("tbhits"),
        )

    @property
    def best_san(self) -> str:
        return self.pv_san.split(" ", 1)[0] if self.pv_san else "—"

    def as_dict(self) -> dict:
        """JSON-friendly form for the future coach and for saved analyses."""
        return {
            "ply": self.ply,
            "depth": self.depth,
            "seldepth": self.seldepth,
            "nodes": self.nodes,
            "nps": self.nps,
            "time_seconds": self.time_seconds,
            "hashfull_permille": self.hashfull_permille,
            "tbhits": self.tbhits,
            "score": format_score(self.score),
            "score_cp": score_cp(self.score),
            "score_type": score_type(self.score),
            "mate": self.score.pov(chess.WHITE).mate() if self.score else None,
            "best_move": self.best_move.uci() if self.best_move else None,
            "pv": [move.uci() for move in self.pv],
            "pv_san": self.pv_san,
        }


@dataclass(frozen=True)
class MoveReport:
    """The classification of a single played move.

    ``loss_cp`` is mover-relative and only comparable when ``loss_kind`` is
    ``"centipawn"``.  Mate information is mover-relative; distances are in
    moves.  The defaults keep older construction sites working.
    """

    ply: int
    san: str
    mover: chess.Color
    classification: MoveClassification
    loss_cp: int | None
    score_after: chess.engine.PovScore | None
    loss_kind: str = "unknown"
    classification_basis: str = "unknown"
    mate_before_for_mover: int | None = None
    mate_after_for_mover: int | None = None
    mate_transition: str = "none"
    allows_forced_mate: bool = False
    misses_forced_mate: bool = False

    def mate_dict(self) -> dict | None:
        """Mate details for the export, or None when the move involved no mate."""
        if self.loss_kind != "mate" and self.mate_before_for_mover is None and self.mate_after_for_mover is None:
            return None
        return {
            "before_for_mover": self.mate_before_for_mover,
            "after_for_mover": self.mate_after_for_mover,
            "transition": self.mate_transition,
            "allows_forced_mate": self.allows_forced_mate,
            "misses_forced_mate": self.misses_forced_mate,
        }

    def as_dict(self) -> dict:
        return {
            "ply": self.ply,
            "san": self.san,
            "mover": "white" if self.mover == chess.WHITE else "black",
            "classification": self.classification.key,
            "label": self.classification.label,
            "loss_cp": self.loss_cp,
            "loss_kind": self.loss_kind,
            "classification_basis": self.classification_basis,
            "mate": self.mate_dict(),
            "eval_after": format_score(self.score_after),
        }


def classify_loss(loss_cp: int, *, best_move_played: bool = False) -> MoveClassification:
    """Map a centipawn loss to a classification band (see the module docstring)."""
    if best_move_played:
        return BEST
    if loss_cp <= EXCELLENT_MAX_CP:
        return EXCELLENT
    if loss_cp <= GOOD_MAX_CP:
        return GOOD
    if loss_cp <= INACCURACY_MAX_CP:
        return INACCURACY
    if loss_cp <= MISTAKE_MAX_CP:
        return MISTAKE
    return BLUNDER


def mate_transition(before_mate: int | None, after_mate: int | None) -> str:
    """Describe what happened to the forced mate between two mover-relative evals.

    Distances are in moves, exactly as python-chess reports them.  Positive is
    a forced mate for the mover, negative a forced mate against the mover, and
    zero means the side to move is checkmated.
    """
    if after_mate == 0:
        return "delivered_checkmate"
    if before_mate is not None and before_mate > 0:
        if after_mate is not None and after_mate > 0:
            return "kept"
        if after_mate is not None and after_mate < 0:
            return "lost"
        return "missed"
    if before_mate is not None and before_mate < 0:
        if after_mate is not None and after_mate < 0:
            return "still_mated"
        return "escaped"
    if after_mate is not None and after_mate > 0:
        return "found"
    if after_mate is not None and after_mate < 0:
        return "allowed"
    return "none"


def allows_forced_mate(before_mate: int | None, after_mate: int | None) -> bool:
    """True when the move newly leaves the mover facing a forced mate."""
    already_mated = before_mate is not None and before_mate < 0
    return after_mate is not None and after_mate < 0 and not already_mated


def misses_forced_mate(before_mate: int | None, after_mate: int | None) -> bool:
    """True when the mover had a forced mate and failed to keep or deliver it."""
    had_mate = before_mate is not None and before_mate > 0
    return had_mate and (after_mate is None or after_mate < 0)


def classify_move(
    *,
    ply: int,
    san: str,
    played: chess.Move | None,
    before: PositionEval,
    after: PositionEval,
) -> MoveReport:
    """Classify the move at *ply* by comparing the eval before and after it."""
    mover = chess.WHITE if ply % 2 == 1 else chess.BLACK
    best_played = played is not None and before.best_move is not None and played == before.best_move
    if before.score is None or after.score is None:
        return MoveReport(
            ply, san, mover, UNKNOWN, None, after.score,
            loss_kind="unknown", classification_basis="unknown",
        )

    before_mate = before.score.pov(mover).mate()
    after_mate = after.score.pov(mover).mate()

    if before_mate is not None or after_mate is not None:
        classification = _classify_mate(before_mate, after_mate, best_played)
        loss = 0 if classification is BEST else None
        return MoveReport(
            ply, san, mover, classification, loss, after.score,
            loss_kind="mate",
            classification_basis="mate",
            mate_before_for_mover=before_mate,
            mate_after_for_mover=after_mate,
            mate_transition=mate_transition(before_mate, after_mate),
            allows_forced_mate=allows_forced_mate(before_mate, after_mate),
            misses_forced_mate=misses_forced_mate(before_mate, after_mate),
        )

    before_cp = score_cp(before.score, mover)
    after_cp = score_cp(after.score, mover)
    if before_cp is None or after_cp is None:
        return MoveReport(
            ply, san, mover, UNKNOWN, None, after.score,
            loss_kind="unknown", classification_basis="unknown",
        )

    loss = max(0, before_cp - after_cp)
    classification = classify_loss(loss, best_move_played=best_played)
    return MoveReport(
        ply, san, mover, classification, loss, after.score,
        loss_kind="centipawn",
        classification_basis="best_move" if best_played else "centipawn",
    )


def _classify_mate(
    before_mate: int | None,
    after_mate: int | None,
    best_played: bool,
) -> MoveClassification:
    """Classification when a forced mate is being kept, found, lost or escaped."""
    if before_mate is not None and before_mate > 0:
        if after_mate is not None and after_mate >= 0:
            # Still mating; a shorter mate is better, but any forced mate wins.
            return BEST if best_played or after_mate <= before_mate else GOOD
        return BLUNDER  # threw away a forced mate
    if before_mate is not None and before_mate < 0:
        if after_mate is not None and after_mate < 0:
            # Already being mated: delaying it is better than allowing it sooner.
            return GOOD if abs(after_mate) >= abs(before_mate) else MISTAKE
        if after_mate == 0:
            return BLUNDER  # checkmated immediately
        return EXCELLENT  # escaped a forced mate
    if after_mate is not None:
        return BEST if after_mate >= 0 else BLUNDER
    return UNKNOWN


def _empty_counts() -> dict[str, int]:
    return {item.key: 0 for item in (*CLASSIFICATIONS, UNKNOWN)}


@dataclass(frozen=True)
class GameAnalysis:
    """A complete, JSON-friendly analysis of one game.

    This is the boundary between the review UI and any future coach: it contains
    only structured engine facts (evals, best moves, classifications and the
    positions they belong to), never prose or invented evaluations.
    """

    game: Game
    headers: dict[str, str]
    evals: dict[int, PositionEval]
    reports: dict[int, MoveReport]
    engine_name: str | None
    seconds_per_position: float | None
    generated_at: str
    engine_info: dict | None = None
    configuration: dict | None = None

    @classmethod
    def from_game(
        cls,
        game: Game,
        headers: dict[str, str],
        evals: dict[int, PositionEval],
        reports: dict[int, MoveReport],
        *,
        engine_name: str | None = None,
        seconds_per_position: float | None = None,
        generated_at: str | None = None,
        engine_info: dict | None = None,
        configuration: dict | None = None,
    ) -> "GameAnalysis":
        return cls(
            game=game,
            headers=dict(headers),
            evals=dict(evals),
            reports=dict(reports),
            engine_name=engine_name,
            seconds_per_position=seconds_per_position,
            generated_at=generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            engine_info=dict(engine_info) if engine_info is not None else None,
            configuration=dict(configuration) if configuration is not None else None,
        )

    @property
    def complete(self) -> bool:
        """True when every position, including the final one, has an evaluation."""
        return len(self.evals) == len(self.game.board.move_stack) + 1

    def _position_dict(self, ply: int) -> dict | None:
        position = self.evals.get(ply)
        if position is None:
            return None
        data = position.as_dict()
        data["fen"] = self.game.at(ply).fen()
        return data

    @staticmethod
    def _eval_line(position: PositionEval | None) -> dict | None:
        if position is None:
            return None
        return {
            "score": format_score(position.score),
            "score_cp": score_cp(position.score),
            "score_type": score_type(position.score),
            "mate": position.score.pov(chess.WHITE).mate() if position.score else None,
        }

    @staticmethod
    def _conventions() -> dict:
        """Sign and unit conventions shared by every field in the export."""
        return {
            "eval_perspective": "white",
            "loss_perspective": "mover",
            "centipawns_per_pawn": 100,
            "mate_distance_unit": "moves",
            "white_mate": "positive = White mates, negative = Black mates, 0 = checkmate",
            "mover_mate": "positive = mover mates, negative = mover is mated, 0 = mover delivered checkmate",
            "null": "unavailable or not applicable",
        }

    @staticmethod
    def _classification() -> dict:
        """The classification bands and rules, generated from the live constants."""
        return {
            "loss_perspective": "mover",
            "thresholds_cp": {
                "excellent": {"min_loss": 0, "max_loss": EXCELLENT_MAX_CP},
                "good": {"min_loss": EXCELLENT_MAX_CP + 1, "max_loss": GOOD_MAX_CP},
                "inaccuracy": {"min_loss": GOOD_MAX_CP + 1, "max_loss": INACCURACY_MAX_CP},
                "mistake": {"min_loss": INACCURACY_MAX_CP + 1, "max_loss": MISTAKE_MAX_CP},
                "blunder": {"min_loss": MISTAKE_MAX_CP + 1, "max_loss": None},
            },
            "rules": {
                "best": "played the engine's top move (kept separate from the centipawn bands)",
                "unknown": "either evaluation unavailable",
                "mate": "mate scores override the centipawn bands",
                "thresholds": "inclusive centipawn bands, mover-relative",
            },
            "labels": {
                item.key: {"label": item.label, "symbol": item.symbol}
                for item in (*CLASSIFICATIONS, UNKNOWN)
            },
        }

    def _summary(self) -> dict:
        counts = {"white": _empty_counts(), "black": _empty_counts()}
        losses = []
        for report in self.reports.values():
            side = "white" if report.mover == chess.WHITE else "black"
            counts[side][report.classification.key] += 1
            if report.loss_cp is not None:
                losses.append(report)
        final_ply = len(self.game.board.move_stack)
        final = self.evals.get(final_ply)
        biggest = max(losses, key=lambda item: item.loss_cp or 0, default=None)
        # Deterministic rule: worst mate error first (Blunder beats Mistake),
        # then the earliest ply.  Only mate-classified mistakes and blunders count.
        mate_errors = [
            report
            for report in self.reports.values()
            if report.loss_kind == "mate"
            and report.classification in (MISTAKE, BLUNDER)
        ]
        biggest_mate = max(
            mate_errors,
            key=lambda item: (item.classification is BLUNDER, -item.ply),
            default=None,
        )
        return {
            "moves": len(self.reports),
            "white": counts["white"],
            "black": counts["black"],
            "final_eval": format_score(final.score) if final else None,
            "final_eval_cp": score_cp(final.score) if final else None,
            "final_score_type": score_type(final.score) if final else None,
            "final_mate": final.score.pov(chess.WHITE).mate() if final and final.score else None,
            "biggest_loss": (
                {"ply": biggest.ply, "san": biggest.san, "loss_cp": biggest.loss_cp}
                if biggest is not None
                else None
            ),
            "biggest_mate_error": (
                {
                    "ply": biggest_mate.ply,
                    "san": biggest_mate.san,
                    "classification": biggest_mate.classification.key,
                    "classification_basis": biggest_mate.classification_basis,
                    "mate_transition": biggest_mate.mate_transition,
                    "before_for_mover": biggest_mate.mate_before_for_mover,
                    "after_for_mover": biggest_mate.mate_after_for_mover,
                }
                if biggest_mate is not None
                else None
            ),
        }

    def as_dict(self) -> dict:
        moves = []
        for ply in sorted(self.reports):
            report = self.reports[ply]
            before = self.evals.get(ply - 1)
            after = self.evals.get(ply)
            played = self.game.board.move_stack[ply - 1]
            moves.append(
                {
                    "ply": ply,
                    "move_number": (ply + 1) // 2,
                    "color": "white" if report.mover == chess.WHITE else "black",
                    "san": report.san,
                    "uci": played.uci(),
                    "fen_before": self.game.at(ply - 1).fen(),
                    "fen_after": self.game.at(ply).fen(),
                    "classification": report.classification.key,
                    "label": report.classification.label,
                    "loss_cp": report.loss_cp,
                    "loss_kind": report.loss_kind,
                    "classification_basis": report.classification_basis,
                    "mate": report.mate_dict(),
                    "eval_before": self._eval_line(before),
                    "eval_after": self._eval_line(after),
                    "best_move": before.best_move.uci() if before and before.best_move else None,
                    "best_san": before.best_san if before else None,
                    "pv_before": before.pv_san if before else "",
                    "reply_pv": after.pv_san if after else "",
                }
            )

        plies = len(self.game.board.move_stack)
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": self.generated_at,
            "perspective": "white",
            "conventions": self._conventions(),
            "classification": self._classification(),
            "game": {
                "headers": dict(self.headers),
                "result": self.headers.get("Result", "*"),
                "start_fen": chess.STARTING_FEN,
                "final_fen": self.game.board.fen(),
                "plies": plies,
                "full_moves": (plies + 1) // 2,
            },
            "analysis": {
                "engine": self.engine_name,
                "seconds_per_position": self.seconds_per_position,
                "engine_info": self.engine_info,
                "configuration": self.configuration,
                "positions_analysed": len(self.evals),
                "positions_expected": plies + 1,
                "complete": self.complete,
            },
            "summary": self._summary(),
            "positions": [self._position_dict(ply) for ply in sorted(self.evals)],
            "moves": moves,
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.as_dict(), indent=indent, ensure_ascii=False)
