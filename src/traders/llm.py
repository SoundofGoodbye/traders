"""Shared plumbing for the optional LLM-backed generators (slices 27-29).

Model resolution plus an injected-or-lazy ``anthropic`` client, so each generator
(``LLMThesisGenerator``, ``LLMPostMortemGenerator``, …) doesn't re-implement it.
The SDK import stays lazy: a generator constructed with an injected client never
imports ``anthropic`` — which is what keeps the default test suite hermetic and
offline. The real client is built (and cached) only on first use without an
injected one, failing fast with a clear message when the ``llm`` extra is absent.
"""

from __future__ import annotations

import os

DEFAULT_MODEL = "claude-sonnet-4-6"
_MISSING_EXTRA = "the LLM generators require the 'llm' extra. Install with: uv sync --extra llm"


def resolve_model(model: str | None) -> str:
    """Explicit ``model``, else ``$TRADERS_LLM_MODEL``, else the default."""
    return model or os.environ.get("TRADERS_LLM_MODEL") or DEFAULT_MODEL


class LLMGenerator:
    """Base for Claude-backed generators: an injected client (tests) or a lazily
    constructed real one (behind the ``llm`` extra), plus model resolution.
    """

    def __init__(
        self,
        client: object | None = None,
        model: str | None = None,
        max_tokens: int = 1024,
    ) -> None:
        self._injected = client
        self._real: object | None = None
        self._model = resolve_model(model)
        self._max_tokens = max_tokens

    def _resolve_client(self) -> object:
        if self._injected is not None:
            return self._injected
        if self._real is None:
            try:
                import anthropic
            except ImportError as e:
                raise ImportError(_MISSING_EXTRA) from e
            self._real = anthropic.Anthropic()
        return self._real
