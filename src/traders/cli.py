"""CLI entry point for the traders package."""

from __future__ import annotations

import argparse

from traders import __version__
from traders.cli_commands import all_commands
from traders.env import load_dotenv


def main(argv: list[str] | None = None) -> None:
    load_dotenv()  # pick up ./.env (TIINGO_API_KEY, TRADERS_EDGAR_UA, …) if present
    parser = argparse.ArgumentParser(prog="traders")
    parser.add_argument("--version", action="version", version=f"traders v{__version__}")
    sub = parser.add_subparsers(dest="cmd")

    commands = all_commands()
    for c in commands:
        c.add_parser(sub)

    args = parser.parse_args(argv)
    run = {c.name: c.run for c in commands}.get(args.cmd)
    if run is None:
        print(f"traders v{__version__}")
        return
    run(args)


if __name__ == "__main__":
    main()
