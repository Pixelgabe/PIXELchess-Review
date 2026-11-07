"""Skins: board colours, piece colours, glyphs and pixel sprites.

Built-in skins are the TOML files in ./skins. Users add their own by dropping
a TOML file into the user skins directory; see README for the format.
"""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

import chess

from .sprites import SHEETS

PIECES = (chess.KING, chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT, chess.PAWN)
PIECE_KEYS = ("king", "queen", "rook", "bishop", "knight", "pawn")
OMARCHY_THEME = Path("~/.local/state/omarchy/current/theme/colors.toml").expanduser()
_HEX = re.compile(r"#[0-9a-fA-F]{6}")


def parse_sprites(block: str) -> dict[int, list[str]]:
    """Parse six stacked n x n sprites (king, queen, rook, bishop, knight, pawn)."""
    rows = block.split()
    n = len(rows[0]) if rows else 0
    if not n or len(rows) != 6 * n or any(len(r) != n or set(r) - set("Xo.") for r in rows):
        raise ValueError(f"sprite sheet must be 6 stacked {n}x{n} blocks of 'X', 'o' and '.'")
    return {pt: rows[i * n : (i + 1) * n] for i, pt in enumerate(PIECES)}


DEFAULT_SPRITES = {n: parse_sprites(block) for n, block in SHEETS.items()}
DEFAULT_GLYPHS = dict(zip(PIECES, "♚♛♜♝♞♟"))


def blend(a: str, b: str, t: float) -> str:
    """Mix two #rrggbb colours; t=0 gives a, t=1 gives b."""
    pa, pb = (int(a[i : i + 2], 16) for i in (1, 3, 5)), (int(b[i : i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(pa, pb))


@dataclass(frozen=True)
class Skin:
    name: str
    square: dict[bool, str]  # keyed by is_light
    last: dict[bool, str]
    target: dict[bool, str]
    cursor: str
    selected: str
    check: str
    checkmate: str
    hint: str
    label: str
    pieces: dict[bool, dict[str, str]]  # colour -> {"X": body, "o": detail}
    glyphs: dict[int, str]
    sprites: dict[int, dict[int, list[str]]]  # cell width -> piece type -> rows


def _colour(table: dict, key: str, default: str | None = None) -> str:
    value = table.get(key, default)
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise ValueError(f"'{key}' must be a colour like \"#a1b2c3\"")
    return value


def build_skin(data: dict, fallback_name: str) -> Skin:
    """Turn parsed TOML into a Skin. Raises ValueError with a readable reason."""
    board, white, black = (data.get(k) or {} for k in ("board", "white", "black"))
    if not all(isinstance(t, dict) for t in (board, white, black)):
        raise ValueError("[board], [white] and [black] must be tables")
    light, dark = _colour(board, "light"), _colour(board, "dark")

    def pair(name: str, tint: str, amount: float) -> dict[bool, str]:
        return {
            True: _colour(board, f"{name}_light", blend(light, tint, amount)),
            False: _colour(board, f"{name}_dark", blend(dark, tint, amount)),
        }

    glyphs = dict(DEFAULT_GLYPHS)
    for key, value in (data.get("glyphs") or {}).items():
        if key not in PIECE_KEYS or not isinstance(value, str) or len(value) != 1:
            raise ValueError(f"[glyphs] {key}: expected one character for one of {', '.join(PIECE_KEYS)}")
        glyphs[PIECES[PIECE_KEYS.index(key)]] = value

    sprites = dict(DEFAULT_SPRITES)
    for key, block in (data.get("sprites") or {}).items():
        if key not in ("8", "10", "12") or not isinstance(block, str):
            raise ValueError("[sprites] keys must be 8, 10 or 12 with a text block")
        sheet = parse_sprites(block)
        if len(sheet[chess.KING]) != int(key):
            raise ValueError(f"[sprites] {key}: sheet is {len(sheet[chess.KING])}px wide")
        sprites[int(key)] = sheet

    name = data.get("name", fallback_name)
    if not isinstance(name, str) or not name.strip():
        raise ValueError("'name' must be text")
    return Skin(
        name=name.strip()[:24],
        square={True: light, False: dark},
        last=pair("last", "#e8d44a", 0.45),
        target=pair("target", "#4aa3e8", 0.5),
        cursor=_colour(board, "cursor", "#e8c84a"),
        selected=_colour(board, "selected", "#6fa86f"),
        check=_colour(board, "check", "#c0504d"),
        checkmate=_colour(board, "checkmate", "#ff3b30"),
        hint=_colour(board, "hint", "#b07ad9"),
        label=_colour(board, "label", "#8a8a8a"),
        pieces={
            chess.WHITE: {"X": _colour(white, "body"), "o": _colour(white, "detail")},
            chess.BLACK: {"X": _colour(black, "body"), "o": _colour(black, "detail")},
        },
        glyphs=glyphs,
        sprites=sprites,
    )


def omarchy_colors(path: Path = OMARCHY_THEME) -> dict[str, str] | None:
    """background, foreground, accent and cursor of the active Omarchy theme, or None when there isn't one."""
    try:
        c = tomllib.loads(path.read_text())
        colors = {k: _colour(c, k) for k in ("background", "foreground", "accent")}
        return {**colors, "cursor": _colour(c, "cursor", colors["accent"])}
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        return None


def omarchy_skin() -> Skin | None:
    """A skin derived from the active Omarchy theme, or None when there isn't one."""
    if not (c := omarchy_colors()):
        return None
    bg, fg, accent = c["background"], c["foreground"], c["accent"]
    return build_skin(
        {
            "name": "Omarchy",
            "board": {
                "light": blend(bg, fg, 0.62),
                "dark": blend(bg, fg, 0.3),
                "cursor": c["cursor"],
                "selected": blend(accent, "#ffffff", 0.35),
                "label": blend(bg, fg, 0.7),
            },
            "white": {"body": blend(fg, "#ffffff", 0.88), "detail": blend(bg, fg, 0.45)},
            "black": {"body": blend(bg, "#000000", 0.35), "detail": accent},
        },
        "Omarchy",
    )


def load_skins(user_dir: Path) -> tuple[list[Skin], list[str]]:
    """Built-in skins, the Omarchy theme skin, then user skins. Bad files are skipped and reported."""
    skins, errors = [], []
    if theme := omarchy_skin():
        skins.append(theme)
    builtin = sorted((Path(__file__).parent / "skins").glob("*.toml"))
    user = sorted(user_dir.glob("*.toml")) if user_dir.is_dir() else []
    for path in [*builtin, *user]:
        try:
            skin = build_skin(tomllib.loads(path.read_text()), path.stem)
        except (OSError, ValueError, tomllib.TOMLDecodeError) as e:
            errors.append(f"{path.name}: {e}")
            continue
        skins = [s for s in skins if s.name != skin.name] + [skin]  # a user skin replaces a same-named one
    return skins, errors
