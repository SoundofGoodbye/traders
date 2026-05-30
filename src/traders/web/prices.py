"""Optional current-price lookup for unrealized P&L on the positions page.

Only `yfinance` exposes a live quote; `stub`/`edgar` have no price data,
so they yield no price function and the UI shows `—`. Kept separate from
the `DataSource` protocol (which serves the Researcher's evidence kinds,
not quotes) so neither concern leaks into the other.
"""

from __future__ import annotations

from collections.abc import Callable

PriceFn = Callable[[str], float | None]

_PRICE_KEYS = ("currentPrice", "regularMarketPrice", "previousClose")


def price_fn_for_source(name: str) -> PriceFn | None:
    """Build a ticker->price function for a data-source name, or None.

    Returns None for sources without quotes (`stub`, `edgar`), which makes
    the positions page render `—` for price and P&L.
    """
    if name == "yfinance":
        return _yfinance_price_fn()
    return None


def _yfinance_price_fn() -> PriceFn:
    try:
        import yfinance
    except ImportError as e:  # pragma: no cover - exercised only without extra
        raise ImportError(
            "yfinance is not installed. Install with: uv sync --extra realdata"
        ) from e

    def price(ticker: str) -> float | None:
        try:
            info = yfinance.Ticker(ticker).info
            if not isinstance(info, dict):
                return None
            for key in _PRICE_KEYS:
                value = info.get(key)
                if isinstance(value, (int, float)) and value > 0:
                    return float(value)
        except Exception:
            return None
        return None

    return price
