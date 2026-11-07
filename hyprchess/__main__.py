import argparse
import sys

from . import __version__
from .app import ChessApp


def main() -> None:
    parser = argparse.ArgumentParser(prog="PIXELchess", description="Chess in your terminal.")
    parser.add_argument("-e", "--engine", metavar="PATH", help="UCI engine to play against (default: any found on PATH or in the engines folder)")
    parser.add_argument("-s", "--skin", metavar="NAME", help="skin to start with")
    parser.add_argument("--skins-dir", action="store_true", help="print the folder custom skins go in, then exit")
    parser.add_argument("--version", action="version", version=f"PIXELchess {__version__}")
    parser.add_argument("--engines-dir", action="store_true", help="print the folder UCI engines can be dropped in, then exit")
    args = parser.parse_args()
    app = ChessApp(engine_path=args.engine, skin=args.skin)
    if args.skins_dir or args.engines_dir:
        print(app.config_dir / "skins" if args.skins_dir else app.engines_dir)
        return
    sys.stdout.write("\x1b]2;PIXELchess\x07")  # terminal window title
    sys.stdout.flush()
    app.run()


if __name__ == "__main__":
    main()
