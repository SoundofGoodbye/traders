"""Unit tests for the stdlib CSRF helpers (no web server needed)."""

from traders.web import csrf

SECRET = "test-secret"


def test_issue_then_validate_roundtrip():
    token, cookie_value = csrf.issue(SECRET)
    assert csrf.token_from_cookie(SECRET, cookie_value) == token
    assert csrf.validate(SECRET, cookie_value, token) is True


def test_validate_rejects_wrong_form_token():
    token, cookie_value = csrf.issue(SECRET)
    assert csrf.validate(SECRET, cookie_value, token + "x") is False


def test_token_from_cookie_handles_malformed_input():
    # None, empty, no-dot, and empty-half cookies all decode to None
    assert csrf.token_from_cookie(SECRET, None) is None
    assert csrf.token_from_cookie(SECRET, "") is None
    assert csrf.token_from_cookie(SECRET, "no-dot-here") is None
    assert csrf.token_from_cookie(SECRET, ".sig-only") is None
    assert csrf.token_from_cookie(SECRET, "token-only.") is None


def test_token_from_cookie_rejects_tampered_signature():
    token, cookie_value = csrf.issue(SECRET)
    tampered = cookie_value[:-1] + ("0" if cookie_value[-1] != "0" else "1")
    assert csrf.token_from_cookie(SECRET, tampered) is None


def test_token_from_cookie_rejects_wrong_secret():
    _token, cookie_value = csrf.issue(SECRET)
    assert csrf.token_from_cookie("other-secret", cookie_value) is None


def test_validate_rejects_missing_form_token():
    _token, cookie_value = csrf.issue(SECRET)
    assert csrf.validate(SECRET, cookie_value, None) is False
    assert csrf.validate(SECRET, cookie_value, "") is False
