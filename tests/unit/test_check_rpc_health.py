"""Tests for ``scripts/check_rpc_health.py`` exit-code policy (PR-T1.C).

The nightly canary previously exited ``0`` on every ``ERROR`` status by
labelling them "transient". That meant a silently-broken canary stayed
green in CI while the Tier-1 drift detector was effectively offline.

These tests pin down the new policy:

    * MISMATCH                   -> exit 1   (RPC ID drift)
    * Non-transient ERROR        -> exit 3   (timeouts, parse errors, etc.)
    * Transient rate-limit ERROR -> exit 0   (HTTP 429 / RESOURCE_EXHAUSTED)
    * All OK                     -> exit 0

Priority when statuses collide: MISMATCH (1) > non-transient ERROR (3) > OK (0).
AUTH (2) is signalled earlier via ``sys.exit(2)`` and is exercised in the
auth-failure test by invoking ``main()`` with a missing storage env var.
"""

from __future__ import annotations

import importlib.util
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

# Load scripts/check_rpc_health.py as a module. The ``scripts`` directory
# is not a package, so we go through importlib rather than a normal import.
# Registering the module in ``sys.modules`` before executing it is required
# so that ``@dataclass`` can resolve forward references back to this module
# during class construction.
_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_rpc_health.py"
_spec = importlib.util.spec_from_file_location("check_rpc_health", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
check_rpc_health = importlib.util.module_from_spec(_spec)
sys.modules["check_rpc_health"] = check_rpc_health
_spec.loader.exec_module(check_rpc_health)


CheckStatus = check_rpc_health.CheckStatus
CheckResult = check_rpc_health.CheckResult
compute_exit_code = check_rpc_health.compute_exit_code
is_transient_error = check_rpc_health.is_transient_error
partition_errors = check_rpc_health.partition_errors
print_summary = check_rpc_health.print_summary


def _result(
    name: str,
    status: CheckStatus,
    *,
    error: str | None = None,
) -> CheckResult:
    """Build a CheckResult with a stub RPCMethod-like object.

    ``print_summary`` accesses ``result.method.name`` and
    ``result.expected_id``, so we use a small ducktype rather than
    importing the real ``RPCMethod`` enum (which would add a heavy
    dependency for what is purely a logic test).
    """

    class _Method:
        def __init__(self, n: str) -> None:
            self.name = n

    return CheckResult(  # type: ignore[no-any-return]
        method=_Method(name),  # type: ignore[arg-type]
        status=status,
        expected_id=f"id_{name}",
        found_ids=[],
        error=error,
    )


# ---------------------------------------------------------------------------
# is_transient_error
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "HTTP 429",
        "HTTP 429: Too Many Requests",
        "rpc error: code = RESOURCE_EXHAUSTED desc = quota exceeded",
        "RESOURCE_EXHAUSTED",
    ],
)
def test_transient_markers_match(message: str) -> None:
    assert is_transient_error(message) is True


@pytest.mark.parametrize(
    "message",
    [
        None,
        "",
        "HTTP 500",
        "HTTP 503",
        "Parse error: unexpected token",
        "Connection timeout",
        "RPC ID not found in response",
    ],
)
def test_non_transient_markers_do_not_match(message: str | None) -> None:
    assert is_transient_error(message) is False


# ---------------------------------------------------------------------------
# partition_errors
# ---------------------------------------------------------------------------


def test_partition_errors_separates_transient_from_real() -> None:
    results = [
        _result("ok", CheckStatus.OK),
        _result("rate", CheckStatus.ERROR, error="HTTP 429"),
        _result("timeout", CheckStatus.ERROR, error="Connection timeout"),
        _result("parse", CheckStatus.ERROR, error="Parse error: bad JSON"),
        _result("quota", CheckStatus.ERROR, error="RESOURCE_EXHAUSTED on RPC"),
        _result("mismatch", CheckStatus.MISMATCH),
    ]
    non_transient, transient = partition_errors(results)
    assert [r.method.name for r in non_transient] == ["timeout", "parse"]
    assert [r.method.name for r in transient] == ["rate", "quota"]


# ---------------------------------------------------------------------------
# compute_exit_code priority
# ---------------------------------------------------------------------------


def _counts(**overrides: int) -> Counter[Any]:
    """Build a Counter with all statuses defaulted to 0."""
    base: dict[Any, int] = dict.fromkeys(CheckStatus, 0)
    base.update({getattr(CheckStatus, k.upper()): v for k, v in overrides.items()})
    return Counter(base)


def test_exit_code_all_ok() -> None:
    assert compute_exit_code(_counts(ok=10), []) == 0


def test_exit_code_only_transient_errors() -> None:
    # Counts include the ERROR, but the non_transient list is empty.
    counts = _counts(ok=9, error=1)
    assert compute_exit_code(counts, []) == 0


def test_exit_code_non_transient_error() -> None:
    counts = _counts(ok=9, error=1)
    non_transient = [_result("timeout", CheckStatus.ERROR, error="Connection timeout")]
    assert compute_exit_code(counts, non_transient) == 3


def test_exit_code_mismatch_alone() -> None:
    counts = _counts(ok=9, mismatch=1)
    assert compute_exit_code(counts, []) == 1


def test_exit_code_mismatch_beats_non_transient_error() -> None:
    """Priority: MISMATCH (1) wins over non-transient ERROR (3)."""
    counts = _counts(ok=8, mismatch=1, error=1)
    non_transient = [_result("timeout", CheckStatus.ERROR, error="Connection timeout")]
    assert compute_exit_code(counts, non_transient) == 1


# ---------------------------------------------------------------------------
# print_summary (integration over the helpers)
# ---------------------------------------------------------------------------


def test_print_summary_all_match_returns_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    results = [_result(f"m{i}", CheckStatus.OK) for i in range(3)]
    assert print_summary(results) == 0
    out = capsys.readouterr().out
    assert "RESULT: PASS" in out


def test_print_summary_only_rate_limit_returns_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    results = [
        _result("ok", CheckStatus.OK),
        _result("rate", CheckStatus.ERROR, error="HTTP 429"),
    ]
    assert print_summary(results) == 0
    out = capsys.readouterr().out
    assert "transient" in out.lower()
    assert "RESULT: PASS" in out


def test_print_summary_non_transient_error_returns_three(
    capsys: pytest.CaptureFixture[str],
) -> None:
    results = [
        _result("ok", CheckStatus.OK),
        _result("timeout", CheckStatus.ERROR, error="Connection timeout"),
    ]
    assert print_summary(results) == 3
    out = capsys.readouterr().out
    assert "non-transient ERROR detected in methods: timeout" in out


def test_print_summary_mismatch_plus_error_returns_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    results = [
        _result("drift", CheckStatus.MISMATCH),
        _result("timeout", CheckStatus.ERROR, error="Connection timeout"),
    ]
    assert print_summary(results) == 1
    out = capsys.readouterr().out
    assert "RESULT: FAIL - RPC ID mismatches detected" in out


def test_print_summary_lists_affected_methods_on_exit_three(
    capsys: pytest.CaptureFixture[str],
) -> None:
    results = [
        _result("timeout", CheckStatus.ERROR, error="Connection timeout"),
        _result("parse", CheckStatus.ERROR, error="Parse error: bad JSON"),
        _result("rate", CheckStatus.ERROR, error="HTTP 429"),
    ]
    assert print_summary(results) == 3
    out = capsys.readouterr().out
    # Extract the "affected methods" header line so we only inspect the list
    # of method names (the surrounding explanatory text legitimately mentions
    # "rate-limit transients").
    header_line = next(
        line for line in out.splitlines() if "non-transient ERROR detected in methods:" in line
    )
    affected = header_line.split("methods:", 1)[1]
    # Both non-transient methods appear in the affected list…
    assert "timeout" in affected and "parse" in affected
    # …and the transient one is NOT listed as an affected method.
    assert "rate" not in affected


# ---------------------------------------------------------------------------
# main() auth-failure path -> exit 2
# ---------------------------------------------------------------------------


def test_main_exits_two_when_auth_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing ``NOTEBOOKLM_AUTH_JSON`` must surface as exit code 2.

    Even if the developer running the test happens to have a local
    ``~/.notebooklm/storage_state.json``, we patch the loader to simulate
    a fresh CI environment with no credentials available.
    """
    monkeypatch.delenv("NOTEBOOKLM_AUTH_JSON", raising=False)
    monkeypatch.setattr("sys.argv", ["check_rpc_health.py"])

    def _missing() -> dict[str, str]:
        raise FileNotFoundError("simulated missing storage_state.json")

    monkeypatch.setattr(check_rpc_health, "load_auth_from_storage", _missing)

    with pytest.raises(SystemExit) as excinfo:
        check_rpc_health.main()
    assert excinfo.value.code == 2
