"""CLI tests for `notebooklm login --master-token[-refresh]` (service mocked)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

import notebooklm.cli.services.login.master_token as mt_service
from notebooklm.notebooklm_cli import cli
from notebooklm.paths import get_storage_path


def _seed_profile_account(monkeypatch, tmp_path, email):
    """Write a storage_state.json whose persisted account is ``email``."""
    monkeypatch.setenv("NOTEBOOKLM_HOME", str(tmp_path))
    sp = get_storage_path()
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(
        json.dumps(
            {
                "cookies": [],
                "notebooklm": {"version": 1, "account": {"authuser": 0, "email": email}},
            }
        )
    )


def test_master_token_refresh_calls_service(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTEBOOKLM_HOME", str(tmp_path))
    with patch.object(mt_service, "refresh", new=AsyncMock()) as ref:
        result = CliRunner().invoke(cli, ["login", "--master-token-refresh"])
    assert result.exit_code == 0, result.output
    assert ref.called
    assert "Re-minted" in result.output


def test_master_token_requires_account(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTEBOOKLM_HOME", str(tmp_path))
    result = CliRunner().invoke(cli, ["login", "--master-token"])
    assert result.exit_code == 1
    assert "--account" in result.output


def test_master_token_bootstrap_calls_service(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTEBOOKLM_HOME", str(tmp_path))
    with patch.object(mt_service, "bootstrap", new=AsyncMock(return_value=7)) as boot:
        result = CliRunner().invoke(
            cli,
            ["login", "--master-token", "--account", "e@x.com", "--oauth-token", "TOK"],
        )
    assert result.exit_code == 0, result.output
    assert boot.called
    assert boot.call_args.kwargs["oauth_token"] == "TOK"
    assert "7 notebooks" in result.output


def test_master_token_bootstrap_browser_capture_when_no_oauth(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTEBOOKLM_HOME", str(tmp_path))
    with (
        patch.object(mt_service, "capture_oauth_token", return_value="CAPTOK") as cap,
        patch.object(mt_service, "bootstrap", new=AsyncMock(return_value=3)) as boot,
    ):
        result = CliRunner().invoke(cli, ["login", "--master-token", "--account", "e@x.com"])
    assert result.exit_code == 0, result.output
    assert cap.called
    assert boot.call_args.kwargs["oauth_token"] == "CAPTOK"


def test_master_token_refuses_account_clobber(tmp_path, monkeypatch):
    _seed_profile_account(monkeypatch, tmp_path, "other@x.com")
    # Mismatch must fail fast — before any oauth_token capture.
    with patch.object(mt_service, "capture_oauth_token") as cap:
        result = CliRunner().invoke(cli, ["login", "--master-token", "--account", "e@x.com"])
    assert result.exit_code == 1
    assert "already belongs to other@x.com" in result.output
    assert not cap.called  # guard fires before sign-in


def test_master_token_refuses_clobber_via_token_owner_only(tmp_path, monkeypatch):
    # No storage_state.json, but a master_token.json owned by a different account.
    from notebooklm.auth import write_master_token
    from notebooklm.paths import get_master_token_path

    monkeypatch.setenv("NOTEBOOKLM_HOME", str(tmp_path))
    mtp = get_master_token_path()
    mtp.parent.mkdir(parents=True, exist_ok=True)
    write_master_token(mtp, email="other@x.com", master_token="aas_et/M", android_id="abc")
    with patch.object(mt_service, "capture_oauth_token") as cap:
        result = CliRunner().invoke(cli, ["login", "--master-token", "--account", "e@x.com"])
    assert result.exit_code == 1
    assert "already belongs to other@x.com" in result.output
    assert not cap.called


def test_master_token_force_overwrites_other_account(tmp_path, monkeypatch):
    _seed_profile_account(monkeypatch, tmp_path, "other@x.com")
    with patch.object(mt_service, "bootstrap", new=AsyncMock(return_value=4)) as boot:
        result = CliRunner().invoke(
            cli,
            ["login", "--master-token", "--account", "e@x.com", "--oauth-token", "T", "--force"],
        )
    assert result.exit_code == 0, result.output
    assert boot.call_args.kwargs["force"] is True
