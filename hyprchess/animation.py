"""Shared piece-move animation helpers: square paths, move segments and frame boards."""

from __future__ import annotations

import chess

MOVE_ANIMATION_FRAMES = 5
MOVE_ANIMATION_DELAY = 0.04


def move_path(start: int, end: int) -> list[int]:
    """Squares a piece travels over, from start to end, capped at a few stops."""
    if start == end:
        return [start]
    file_step = chess.square_file(end) - chess.square_file(start)
    rank_step = chess.square_rank(end) - chess.square_rank(start)
    steps = max(abs(file_step), abs(rank_step), 1)
    points: list[int] = []
    for index in range(steps + 1):
        square = chess.square(chess.square_file(start) + round(file_step * index / steps), chess.square_rank(start) + round(rank_step * index / steps))
        if not points or points[-1] != square:
            points.append(square)
    if len(points) > MOVE_ANIMATION_FRAMES:
        span = len(points) - 1
        points = [points[round(index * span / (MOVE_ANIMATION_FRAMES - 1))] for index in range(MOVE_ANIMATION_FRAMES)]
        points[-1] = end
    return points


def animation_segments(move: chess.Move, before: chess.Board) -> list[tuple]:
    """Moving pieces for an animation: the played move, plus the rook in a castle."""
    segments: list[tuple] = []
    piece = before.piece_at(move.from_square)
    if piece is not None:
        segments.append((piece, move.from_square, move.to_square, move_path(move.from_square, move.to_square)))
    if before.is_castling(move) and len(segments) == 1:
        rank = chess.square_rank(move.from_square)
        rook_from, rook_to = (chess.square(7, rank), chess.square(5, rank)) if chess.square_file(move.to_square) > chess.square_file(move.from_square) else (chess.square(0, rank), chess.square(3, rank))
        rook = before.piece_at(rook_from)
        if rook is not None and rook.color == piece.color:
            segments.append((rook, rook_from, rook_to, move_path(rook_from, rook_to)))
    return segments


def frame_board(after: chess.Board, segments: list[tuple], index: int) -> chess.Board:
    """A copy of *after* with the moving pieces drawn at the interpolated square."""
    board = after.copy(stack=False)
    for _piece, _start, end, _points in segments:
        board.remove_piece_at(end)
    for piece, _start, end, points in segments:
        board.set_piece_at(points[min(index, len(points) - 1)], piece)
    return board
