# Structured analysis JSON — schema 2

Hyprchess writes one JSON document per reviewed game when you press `e` on the
review screen. The file is intended for tooling and for a future local coach;
it is never shown in the UI. This document is the field reference for schema
version **2**.

## Versioning and compatibility

- `schema_version` is an integer. This version is `2`.
- Version 2 is **additive**: every version 1 field keeps its name, type and
  meaning. Consumers that only understand version 1 can keep reading the file.
- New fields describe perspective, engine metadata, classification rules and
  forced mates explicitly. No gameplay or analysis algorithm changed.
- A document may be incomplete (`analysis.complete` is `false`) when analysis
  was stopped or an engine failed. Missing positions and moves are simply
  absent; unavailable scalar facts are `null`.

## Conventions

| Field | Value | Meaning |
| --- | --- | --- |
| `conventions.eval_perspective` | `"white"` | `score`, `score_cp`, `mate`, `eval_before`, `eval_after` and the eval bar are White-relative. Positive favours White. |
| `conventions.loss_perspective` | `"mover"` | `loss_cp` is relative to the player who made the move. Positive means the move made that player's position worse. |
| `conventions.centipawns_per_pawn` | `100` | Centipawn units per pawn. |
| `conventions.mate_distance_unit` | `"moves"` | Mate distances are full moves, not plies, everywhere in the document. |
| `conventions.white_mate` | text | Positive = White mates, negative = Black mates, `0` = the side to move is checkmated. |
| `conventions.mover_mate` | text | Positive = the mover mates, negative = the mover is mated, `0` = the mover delivered checkmate. |
| `conventions.null` | text | `null` means unavailable or not applicable. |

`score_cp` is a real centipawn value only when the sibling `score_type` is
`"cp"`. For mates it is a large internal ordering value (near 100000); use the
`mate` field instead. When `score_type` is `"none"`, no evaluation is available.

## Top-level fields

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | integer | Schema version, currently `2`. |
| `generated_at` | string | ISO 8601 timestamp with timezone when the report was built. |
| `perspective` | string | Always `"white"`; kept from version 1. See `conventions`. |
| `conventions` | object | Sign and unit conventions (table above). |
| `classification` | object | Thresholds, rules and labels used for this game. |
| `game` | object | Headers and position summary. |
| `analysis` | object | Engine identification, configuration and coverage. |
| `summary` | object | Counts, final evaluation and the biggest errors. |
| `positions` | array | Every evaluated position, in ascending ply order. |
| `moves` | array | Every classified played move, in ascending ply order. |

## `game`

| Field | Type | Meaning |
| --- | --- | --- |
| `headers` | object | PGN headers exactly as parsed or saved. |
| `result` | string | `headers.Result`, or `"*"` when absent. |
| `start_fen` | string | Start position. Hyprchess only loads standard games. |
| `final_fen` | string | Position after the last move. |
| `plies` | integer | Number of half-moves in the game. |
| `full_moves` | integer | Number of full moves, rounded up. |

## `analysis`

| Field | Type | Meaning |
| --- | --- | --- |
| `engine` | string or null | Hyprchess's display name for the engine, such as `"Stockfish"`. Kept from version 1. |
| `seconds_per_position` | number or null | Requested search time per position, in seconds. Kept from version 1. |
| `engine_info` | object or null | The engine's own UCI identification. `null` when no engine was available. |
| `configuration` | object or null | Settings actually applied for this pass. `null` when no engine was available. |
| `positions_analysed` | integer | Number of positions present in `positions`. |
| `positions_expected` | integer | `game.plies + 1`, including the starting position. |
| `complete` | boolean | True when every expected position was analysed. |

### `analysis.engine_info`

| Field | Type | Meaning |
| --- | --- | --- |
| `name` | string or null | UCI `id name`, for example `"Stockfish 19"`. |
| `author` | string or null | UCI `id author`. |

Values come from the live engine handshake. Hyprchess does not invent them.

### `analysis.configuration`

| Field | Type | Meaning |
| --- | --- | --- |
| `search_limit.time_seconds` | number or null | Time limit passed to the engine per position, in seconds. |
| `search_limit.depth` | integer or null | Depth limit, or `null` when no depth limit was used. |
| `search_limit.nodes` | integer or null | Node limit, or `null` when no node limit was used. |
| `threads` | integer or null | Threads setting in effect (the engine default, because Hyprchess does not override it). |
| `hash_mb` | integer or null | Hash size in mebibytes in effect (the engine default). |
| `options` | object | Strength options Hyprchess explicitly applied, for example `UCI_LimitStrength: false` and `Skill Level: 20`. |
| `engine_defaults_used` | array of strings | Options left at engine defaults, such as `["Threads", "Hash"]`. |

Actual search depth, node count and time per position are recorded in each
`positions[]` entry.

## `classification`

| Field | Type | Meaning |
| --- | --- | --- |
| `loss_perspective` | string | Always `"mover"`. |
| `thresholds_cp` | object | Inclusive mover-relative centipawn bands. |
| `rules` | object | Human-readable rules, including that Best is separate and mates override the bands. |
| `labels` | object | Display label and terminal symbol for every classification key. |

### Centipawn bands

| Classification | Loss in centipawns |
| --- | --- |
| `best` | Separate rule: the engine's top move was played. No loss band applies. |
| `excellent` | 0–25 |
| `good` | 26–50 |
| `inaccuracy` | 51–100 |
| `mistake` | 101–200 |
| `blunder` | 201 or more |

Band boundaries are inclusive. The same constants drive `classify_loss` and the
exported `thresholds_cp`, so the document cannot drift from the code.

Forced mate overrides these bands; see `moves[].mate`.

## `summary`

| Field | Type | Meaning |
| --- | --- | --- |
| `moves` | integer | Number of classified moves. |
| `white`, `black` | object | Counts per classification for each side. |
| `final_eval` | string or null | Formatted White-relative final score. |
| `final_eval_cp` | integer or null | White-relative centipawn value; a sentinel when `final_score_type` is `"mate"`. |
| `final_score_type` | string or null | `"cp"`, `"mate"`, `"none"`, or `null` when the final position was not analysed. |
| `final_mate` | integer or null | White-relative mate distance in moves; `0` means checkmate. |
| `biggest_loss` | object or null | Largest **centipawn** loss: `{ply, san, loss_cp}`. Mate errors are excluded, preserving version 1 behaviour. |
| `biggest_mate_error` | object or null | Worst mate-classified mistake or blunder, or `null` when there is none. |

Each side count has the keys `best`, `excellent`, `good`, `inaccuracy`,
`mistake`, `blunder` and `unknown`. The same move can make a side's count in
only one category.

### `summary.biggest_mate_error`

| Field | Type | Meaning |
| --- | --- | --- |
| `ply` | integer | Ply of the selected move. |
| `san` | string | Move in SAN. |
| `classification` | string | `"mistake"` or `"blunder"`. |
| `classification_basis` | string | `"mate"`. |
| `mate_transition` | string | See the transition table below. |
| `before_for_mover` | integer or null | Mover-relative mate distance before the move, in moves. |
| `after_for_mover` | integer or null | Mover-relative mate distance after the move, in moves. |

**Selection rule.** Only moves whose `loss_kind` is `"mate"` and whose
classification is `mistake` or `blunder` are candidates. The worst
classification wins (`blunder` beats `mistake`); ties are broken by the
**earliest ply** (smallest number). The rule is deterministic, so the same game
always selects the same move.

## `positions[]`

| Field | Type | Meaning |
| --- | --- | --- |
| `ply` | integer | Half-move index; `0` is the starting position. |
| `fen` | string | Position in FEN. |
| `depth` | integer or null | Search depth reached, in plies. |
| `seldepth` | integer or null | Selective search depth reached, in plies. |
| `nodes` | integer or null | Nodes searched. |
| `nps` | integer or null | Nodes per second reported by the engine. |
| `time_seconds` | number or null | Search time for this position, in seconds. |
| `hashfull_permille` | integer or null | Transposition-table occupancy in permille (0–1000). |
| `tbhits` | integer or null | Tablebase hits. |
| `score` | string | Formatted White-relative score, such as `"+0.35"`, `"+M5"` or `"-#"`. |
| `score_cp` | integer or null | White-relative centipawn value; sentinel when `score_type` is `"mate"`. |
| `score_type` | string | `"cp"`, `"mate"` or `"none"`. |
| `mate` | integer or null | White-relative mate distance in moves; `0` means checkmate. |
| `best_move` | string or null | Engine's best move in UCI. |
| `pv` | array of strings | Principal variation in UCI, up to the engine's first batch. |
| `pv_san` | string | The same variation in SAN, limited to the first few legal moves. |

## `moves[]`

| Field | Type | Meaning |
| --- | --- | --- |
| `ply` | integer | Half-move index; White's first move is ply `1`. |
| `move_number` | integer | Full-move number. |
| `color` | string | `"white"` or `"black"`; the side that made the move. |
| `san` | string | Move in SAN. |
| `uci` | string | Move in UCI. |
| `fen_before` | string | Position before the move. |
| `fen_after` | string | Position after the move. |
| `classification` | string | Classification key. |
| `label` | string | Display label for the classification. |
| `loss_cp` | integer or null | Mover-relative centipawn loss. `0` for a Best move; `null` when unknown or when the move was judged by mate rules. |
| `loss_kind` | string | `"centipawn"`, `"mate"` or `"unknown"`. |
| `classification_basis` | string | `"best_move"`, `"centipawn"`, `"mate"` or `"unknown"`. |
| `mate` | object or null | Forced-mate details, or `null` when the move involved no mate. |
| `eval_before` | object or null | Evaluation before the move (see below). |
| `eval_after` | object or null | Evaluation after the move (see below). |
| `best_move` | string or null | Engine's preferred move before the move was played, in UCI. |
| `best_san` | string or null | The same move in SAN. |
| `pv_before` | string | Principal variation before the move, in SAN. |
| `reply_pv` | string | Principal variation after the move, in SAN. |

`loss_cp` is only comparable when `loss_kind` is `"centipawn"`. For mate
transitions, compare the `mate` object instead.

### `moves[].eval_before` and `moves[].eval_after`

These remain objects, as in version 1.

| Field | Type | Meaning |
| --- | --- | --- |
| `score` | string | Formatted White-relative score. |
| `score_cp` | integer or null | White-relative centipawn value; sentinel when `score_type` is `"mate"`. |
| `score_type` | string | `"cp"`, `"mate"` or `"none"`. |
| `mate` | integer or null | White-relative mate distance in moves. |

### `moves[].mate`

`null` for ordinary centipawn moves. Otherwise:

| Field | Type | Meaning |
| --- | --- | --- |
| `before_for_mover` | integer or null | Mate distance **from the mover's point of view** before the move, in moves. Positive means the mover had a forced mate. |
| `after_for_mover` | integer or null | Mate distance from the mover's point of view after the move, in moves. |
| `transition` | string | What happened to the forced mate (table below). |
| `allows_forced_mate` | boolean | True when the move newly leaves the mover facing a forced mate. |
| `misses_forced_mate` | boolean | True when the mover had a forced mate and did not keep or deliver it. |

Both `before_for_mover` and `after_for_mover` are evaluated for the player who
made **that** move, even though the rest of the document uses White-relative
evaluations. A positive value always means the mover can force mate; a negative
value always means the mover is being mated.

### Mate transitions

| Value | Meaning |
| --- | --- |
| `none` | No forced mate before or after the move. |
| `found` | The mover now has a forced mate. |
| `kept` | The mover had a forced mate and still has one. |
| `missed` | The mover had a forced mate and no longer has one. |
| `lost` | The mover had a forced mate and now the opponent has one. |
| `allowed` | The mover now faces a forced mate that was not there before. |
| `escaped` | The mover was facing a forced mate and no longer is. |
| `still_mated` | The mover was facing a forced mate and still is. |
| `delivered_checkmate` | The move checkmates the opponent. |

## Example excerpt

```json
{
  "schema_version": 2,
  "perspective": "white",
  "conventions": {
    "eval_perspective": "white",
    "loss_perspective": "mover",
    "centipawns_per_pawn": 100,
    "mate_distance_unit": "moves"
  },
  "moves": [
    {
      "ply": 3,
      "san": "g4",
      "classification": "blunder",
      "loss_cp": null,
      "loss_kind": "mate",
      "classification_basis": "mate",
      "mate": {
        "before_for_mover": null,
        "after_for_mover": -1,
        "transition": "allowed",
        "allows_forced_mate": true,
        "misses_forced_mate": false
      }
    }
  ]
}
```

The checked-in example in [sample-analysis.json](sample-analysis.json) was
produced by the real `ReviewScreen.export_analysis()` path with Stockfish on
Fool's Mate.
