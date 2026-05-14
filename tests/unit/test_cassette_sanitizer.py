"""Tests for the cassette sanitizer in ``tests/vcr_config.py``.

These tests cover PR-T5.E:

1. Structural display-name scrub (JSON-key-anchored) — positive + negative.
2. Regression: a legitimate two-Capitalized-word source title in cassette-style
   JSON is NOT scrubbed (the broad ``>[A-Z][a-z]+\\s[A-Z][a-z]+<`` regex that
   we deliberately avoided).
3. Broadened email scrub — positive + negative + idempotency on
   ``SCRUBBED_EMAIL@example.com``.
4. The ``tests/check_cassettes_clean.sh`` script exits 0 on clean cassettes and
   exits 1 when a leak is injected.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

# ``tests/vcr_config.py`` lives directly under ``tests/`` (not in a package).
# Other test modules add it to ``sys.path``; we follow the same convention.
REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = REPO_ROOT / "tests"
sys.path.insert(0, str(TESTS_DIR))

from vcr_config import scrub_string  # noqa: E402

SCRIPT_PATH = TESTS_DIR / "check_cassettes_clean.sh"

# The shell-script-driven subprocess tests need a POSIX shell + GNU grep.
# Windows CI runners have git-bash but POSIX-quoting + grep ``-rnE`` semantics
# don't round-trip cleanly there (path separators, line endings). The shell
# script is also skipped in CI on Windows (see ``.github/workflows/test.yml``),
# so we skip the matching subprocess tests here.
_skip_on_windows = pytest.mark.skipif(
    sys.platform == "win32",
    reason="check_cassettes_clean.sh is a POSIX shell script; the Windows CI"
    " step is gated off and we skip its driver tests in lockstep.",
)


# ---------------------------------------------------------------------------
# Structural display-name scrub
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key, value",
    [
        ("displayName", "Alice Example"),
        ("givenName", "Alice"),
        ("familyName", "Example"),
    ],
)
def test_structural_display_name_scrub_positive(key: str, value: str) -> None:
    """Each new key-anchored pattern scrubs the value to SCRUBBED_NAME."""
    text = f'{{"{key}":"{value}"}}'
    scrubbed = scrub_string(text)
    assert value not in scrubbed
    assert f'"{key}":"SCRUBBED_NAME"' in scrubbed


@pytest.mark.parametrize(
    "key, value",
    [
        ("displayName", "Alice Example"),
        ("givenName", "Alice"),
        ("familyName", "Example"),
    ],
)
def test_structural_display_name_scrub_whitespace_variants(key: str, value: str) -> None:
    """JSON ``"key": "value"`` with whitespace around the colon is scrubbed."""
    text = f'{{"{key}" : "{value}"}}'
    scrubbed = scrub_string(text)
    assert value not in scrubbed
    # Replacement does not preserve whitespace; we only assert the value is gone
    # and the key is now mapped to SCRUBBED_NAME.
    assert "SCRUBBED_NAME" in scrubbed


def test_structural_display_name_scrub_negative_sibling_keys() -> None:
    """Sibling keys (``title``, ``name``, ``label``) MUST NOT match."""
    text = '{"title":"My Title","name":"My Name","label":"My Label"}'
    scrubbed = scrub_string(text)
    # None of those keys should have been touched.
    assert scrubbed == text


def test_structural_display_name_no_match_on_substring_keys() -> None:
    """The regex requires the JSON key to be exactly ``displayName`` (the
    opening quote is part of the match). So keys that *contain* the substring
    ``displayName`` but are not equal to it MUST NOT match:

    - ``displayNamespace`` — extra trailing characters before the closing quote
    - ``userDisplayName`` — extra leading characters after the opening quote

    Confirms the anchor is exact-key on both sides, not a substring match.
    (Claude review on PR #477, #6 — earlier docstring claimed
    ``userDisplayName`` would still match, which was wrong.)
    """
    extra_trailing = '{"displayNamespace":"keep-me"}'
    extra_leading = '{"userDisplayName":"Alice Example"}'
    assert scrub_string(extra_trailing) == extra_trailing
    assert scrub_string(extra_leading) == extra_leading


# ---------------------------------------------------------------------------
# Regression: legitimate two-Capitalized-word source title is preserved
# ---------------------------------------------------------------------------


def test_two_capital_word_source_title_not_scrubbed() -> None:
    """A cassette-style JSON snippet with a two-word source title must survive.

    This guards against re-introducing a broad ``>[A-Z][a-z]+\\s[A-Z][a-z]+<``
    pattern that would clobber legitimate fixture content.
    """
    snippet = '{"title": "Source Title"}'
    assert scrub_string(snippet) == snippet


def test_two_capital_word_in_html_text_not_scrubbed() -> None:
    """Same regression in an HTML-ish context ``>Source Title<``."""
    snippet = "<span>Source Title</span>"
    assert scrub_string(snippet) == snippet


# ---------------------------------------------------------------------------
# Broadened email scrub
# ---------------------------------------------------------------------------


_EMAIL_PROVIDERS = [
    "gmail",
    "googlemail",
    "google",
    "anthropic",
    "outlook",
    "hotmail",
    "yahoo",
    "icloud",
    "protonmail",
]


@pytest.mark.parametrize("provider", _EMAIL_PROVIDERS)
def test_broadened_email_scrub_positive(provider: str) -> None:
    """Quoted emails at any of the supported providers get scrubbed."""
    text = f'{{"email":"alice.example+tag@{provider}.com"}}'
    scrubbed = scrub_string(text)
    assert provider not in scrubbed
    assert "alice.example" not in scrubbed
    assert '"SCRUBBED_EMAIL@example.com"' in scrubbed


@pytest.mark.parametrize("provider", _EMAIL_PROVIDERS)
def test_broadened_email_scrub_unquoted_context(provider: str) -> None:
    """Unquoted emails in raw HTML/JS contexts get scrubbed too.

    Addresses gemini-code-assist review feedback on PR #477: the legacy
    unquoted-context fallback was Gmail-only; it now covers the full provider
    list, mirroring the JSON-quoted pattern.
    """
    text = f'<a href="mailto:alice.example+tag@{provider}.com">Mail me</a>'
    scrubbed = scrub_string(text)
    assert provider not in scrubbed
    assert "alice.example" not in scrubbed
    assert "SCRUBBED_EMAIL@example.com" in scrubbed


def test_email_scrub_idempotent_on_example_com() -> None:
    """``SCRUBBED_EMAIL@example.com`` survives a second scrub pass unchanged."""
    once = scrub_string('{"email":"alice@gmail.com"}')
    twice = scrub_string(once)
    assert once == twice
    assert '"SCRUBBED_EMAIL@example.com"' in twice


def test_email_scrub_negative_unrelated_text() -> None:
    """Domains we don't cover (``@corp.internal``) are left alone — by design."""
    text = '{"contact":"bob@corp.internal"}'
    assert scrub_string(text) == text


# ---------------------------------------------------------------------------
# CI grep guard script: clean vs. leak cases
#
# All tests below shell out to ``bash`` + ``grep -rnE``; we skip them on
# Windows in lockstep with the CI workflow (``runner.os != 'Windows'`` on the
# guard step in .github/workflows/test.yml).
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    """Build a minimal repo-shaped tmpdir containing only the guard script and
    a ``tests/cassettes/`` directory the script will scan.

    The script falls back to plain ``grep -rnE`` outside a git work tree, so we
    do NOT need to run ``git init`` here — we just need ``tests/cassettes/`` to
    exist and the script to be executable.
    """
    (tmp_path / "tests" / "cassettes").mkdir(parents=True)
    dest = tmp_path / "tests" / "check_cassettes_clean.sh"
    shutil.copy2(SCRIPT_PATH, dest)
    dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return tmp_path


def _run_guard(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(repo / "tests" / "check_cassettes_clean.sh")],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )


@_skip_on_windows
def test_guard_script_exits_zero_on_clean_cassettes(fake_repo: Path) -> None:
    cassette = fake_repo / "tests" / "cassettes" / "clean.yaml"
    cassette.write_text(
        # Already-scrubbed sample content — no real PII, no real cookies.
        '{"email":"SCRUBBED_EMAIL@example.com","SID":"SCRUBBED"}\n',
    )
    result = _run_guard(fake_repo)
    assert result.returncode == 0, result.stderr
    assert "OK: cassettes are sanitized" in result.stdout


@_skip_on_windows
def test_guard_script_exits_one_on_email_leak(fake_repo: Path) -> None:
    cassette = fake_repo / "tests" / "cassettes" / "leak.yaml"
    cassette.write_text('{"email":"realname@gmail.com"}\n')
    result = _run_guard(fake_repo)
    assert result.returncode == 1
    assert "unsanitized email" in result.stderr


@_skip_on_windows
def test_guard_script_exits_one_on_cookie_json_key_leak(fake_repo: Path) -> None:
    """Shape B — JSON dict with the cookie name as a top-level key."""
    cassette = fake_repo / "tests" / "cassettes" / "leak.yaml"
    # Cookie value starts with something other than 'S' so it is NOT the
    # canonical "SCRUBBED" sentinel; the guard regex requires ``[^S][^"]+``.
    cassette.write_text('{"SAPISID": "abcdef1234567890"}\n')
    result = _run_guard(fake_repo)
    assert result.returncode == 1
    assert "JSON-key" in result.stderr or "cookie" in result.stderr


@_skip_on_windows
def test_guard_script_exits_one_on_cookie_header_leak(fake_repo: Path) -> None:
    """Shape A — HTTP ``Cookie:`` / ``Set-Cookie:`` header form."""
    cassette = fake_repo / "tests" / "cassettes" / "leak.yaml"
    cassette.write_text("Set-Cookie: SID=abc1234567xyz; Path=/\n")
    result = _run_guard(fake_repo)
    assert result.returncode == 1
    assert "cookie header" in result.stderr


@_skip_on_windows
def test_guard_script_exits_one_on_storage_state_leak_name_first(fake_repo: Path) -> None:
    """Shape C — Playwright ``storage_state.json``, ``name`` before ``value``."""
    cassette = fake_repo / "tests" / "cassettes" / "leak.yaml"
    cassette.write_text(
        '{"name":"SID","value":"abc1234567","domain":".google.com"}\n',
    )
    result = _run_guard(fake_repo)
    assert result.returncode == 1
    assert "storage_state" in result.stderr


@_skip_on_windows
def test_guard_script_exits_one_on_storage_state_leak_value_first(fake_repo: Path) -> None:
    """Shape C — Playwright ``storage_state.json``, ``value`` before ``name``."""
    cassette = fake_repo / "tests" / "cassettes" / "leak.yaml"
    cassette.write_text(
        '{"value":"abc1234567","name":"__Secure-1PSID","domain":".google.com"}\n',
    )
    result = _run_guard(fake_repo)
    assert result.returncode == 1
    assert "storage_state" in result.stderr


@_skip_on_windows
def test_guard_script_catches_single_char_cookie_leak(fake_repo: Path) -> None:
    """A 1-character cookie value (``"SID": "x"``) still trips the guard.

    Earlier the cookie-value regex was ``[^S][^"]+`` which required two-or-more
    characters; a single-character non-scrubbed leak slipped through. The
    regex was tightened to ``[^S"][^"]*`` (Claude review on PR #477, #7).
    """
    cassette = fake_repo / "tests" / "cassettes" / "leak.yaml"
    cassette.write_text('{"SID": "x"}\n')
    result = _run_guard(fake_repo)
    assert result.returncode == 1


@_skip_on_windows
def test_guard_script_exits_zero_when_cassettes_directory_missing(tmp_path: Path) -> None:
    """Fresh checkouts with no recorded cassettes must not fail the guard.

    Addresses gemini-code-assist review feedback on PR #477.
    """
    dest = tmp_path / "tests" / "check_cassettes_clean.sh"
    dest.parent.mkdir()
    shutil.copy2(SCRIPT_PATH, dest)
    dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    # NOTE: deliberately do NOT create tests/cassettes/.
    result = subprocess.run(
        ["bash", str(dest)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "no cassettes" in result.stdout


@_skip_on_windows
def test_guard_script_allows_scrubbed_cookie_sentinel(fake_repo: Path) -> None:
    """All three cookie shapes with the ``SCRUBBED`` sentinel must NOT trip the guard."""
    cassette = fake_repo / "tests" / "cassettes" / "ok.yaml"
    cassette.write_text(
        # Shape B — JSON dict, cookie name as key
        '{"SID": "SCRUBBED", "__Secure-1PSID": "SCRUBBED"}\n'
        # Shape A — HTTP header
        "Set-Cookie: SID=SCRUBBED; Path=/\n"
        # Shape C — Playwright storage_state
        '{"name":"SAPISID","value":"SCRUBBED","domain":".google.com"}\n',
    )
    result = _run_guard(fake_repo)
    assert result.returncode == 0, result.stderr
