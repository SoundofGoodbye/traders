"""Minimal CLI entry point for the traders package."""

from traders import __version__


def main() -> None:
    print(f"traders v{__version__}")


if __name__ == "__main__":
    main()
