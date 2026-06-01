"""Tests for the minimal .env loader (slice 31)."""

from __future__ import annotations

import os

from traders.env import load_dotenv


def test_loads_key_values(tmp_path, monkeypatch):
    monkeypatch.delenv("FOO", raising=False)
    monkeypatch.delenv("BAR", raising=False)
    (tmp_path / ".env").write_text("FOO=hello\nBAR = world\n")
    loaded = load_dotenv(tmp_path / ".env")
    assert os.environ["FOO"] == "hello"
    assert os.environ["BAR"] == "world"
    assert set(loaded) == {"FOO", "BAR"}


def test_ignores_comments_blanks_and_export(tmp_path, monkeypatch):
    monkeypatch.delenv("A", raising=False)
    (tmp_path / ".env").write_text("# a comment\n\n   \nexport A=1\n")
    load_dotenv(tmp_path / ".env")
    assert os.environ["A"] == "1"


def test_strips_matching_quotes(tmp_path, monkeypatch):
    monkeypatch.delenv("Q", raising=False)
    monkeypatch.delenv("Q2", raising=False)
    (tmp_path / ".env").write_text("Q=\"spaced value\"\nQ2='single'\n")
    load_dotenv(tmp_path / ".env")
    assert os.environ["Q"] == "spaced value"
    assert os.environ["Q2"] == "single"


def test_does_not_overwrite_existing_env(tmp_path, monkeypatch):
    monkeypatch.setenv("KEEP", "real")
    (tmp_path / ".env").write_text("KEEP=fromfile\n")
    loaded = load_dotenv(tmp_path / ".env")
    assert os.environ["KEEP"] == "real"  # the real environment always wins
    assert "KEEP" not in loaded


def test_empty_value_is_set(tmp_path, monkeypatch):
    monkeypatch.delenv("EMPTY", raising=False)
    (tmp_path / ".env").write_text("EMPTY=\n")
    load_dotenv(tmp_path / ".env")
    assert os.environ["EMPTY"] == ""


def test_missing_file_is_noop(tmp_path):
    assert load_dotenv(tmp_path / "nope.env") == {}
