"""CLI integration tests for settings commands.

Audit item I20 — extend the CLI VCR coverage to the global language /
settings surface. The canonical command in this repo is ``notebooklm
language set <code>`` (registered as the ``language`` group with ``set``
subcommand; see ``src/notebooklm/cli/language.py``). The task brief uses
the conceptual name ``settings set-language``; the test exercises the
real exposed name without touching the CLI implementation.

The CLI ``language set <code>`` flow:

1. Validates ``<code>`` against the local ``SUPPORTED_LANGUAGES`` table.
2. Writes the language to ``config.json`` (no RPC).
3. Unless ``--local`` is passed, calls
   ``client.settings.set_output_language(<code>)`` — a single
   ``SET_USER_SETTINGS`` (rpcids ``hT54vc``) RPC.

The dedicated CLI cassette ``cli_settings_set_language.yaml`` captures
exactly that single-RPC chain (plus the bootstrap homepage GET). It is
NOT the same as the existing ``settings_set_output_language.yaml``
cassette, which carries an additional ``GET_USER_SETTINGS`` preflight and
a second ``SET_USER_SETTINGS`` to restore the original language — neither
of which the CLI emits.
"""

import pytest

from notebooklm.notebooklm_cli import cli

from .conftest import assert_command_success, notebooklm_vcr, parse_json_output, skip_no_cassettes

pytestmark = [pytest.mark.vcr, skip_no_cassettes]


class TestLanguageSetCommand:
    """Test ``notebooklm language set <code>``."""

    def test_language_set(self, runner, mock_auth_for_vcr, tmp_path, monkeypatch):
        """``language set en`` writes locally and syncs the single SET RPC.

        Redirects ``HOME`` to ``tmp_path`` so the test never touches the real
        user's ``~/.notebooklm/config.json``. ``get_config_path`` derives the
        config path from ``get_home_dir`` which respects ``$HOME``, so the
        ``set_language(code)`` write lands inside ``tmp_path/.notebooklm/``.
        """
        monkeypatch.setenv("HOME", str(tmp_path))
        with notebooklm_vcr.use_cassette("cli_settings_set_language.yaml"):
            result = runner.invoke(cli, ["language", "set", "en"])
            assert_command_success(result, allow_no_context=False)

        # The local config should now hold the chosen language.
        config_path = tmp_path / ".notebooklm" / "config.json"
        assert config_path.exists(), "language set must persist config.json locally"
        import json as _json

        config = _json.loads(config_path.read_text(encoding="utf-8"))
        assert config.get("language") == "en"

    def test_language_set_json(self, runner, mock_auth_for_vcr, tmp_path, monkeypatch):
        """``language set en --json`` emits machine-readable success payload."""
        monkeypatch.setenv("HOME", str(tmp_path))
        with notebooklm_vcr.use_cassette("cli_settings_set_language.yaml"):
            result = runner.invoke(cli, ["language", "set", "en", "--json"])
            assert_command_success(result, allow_no_context=False)

            data = parse_json_output(result.output)
            assert isinstance(data, dict), f"Expected JSON object, got: {result.output!r}"
            assert data.get("language") == "en"
            # ``synced_to_server`` is True when the RPC returned a value;
            # the cassette response carries one so this should be True.
            assert data.get("synced_to_server") is True

    def test_language_set_local_skips_rpc(self, runner, mock_auth_for_vcr, tmp_path, monkeypatch):
        """``--local`` skips the server sync — no cassette needed.

        This is the negative-VCR control: with ``--local`` the command MUST
        NOT make any RPC, so we deliberately avoid loading a cassette. If the
        command ever regresses and tries to sync, VCR (in ``record_mode="none"``)
        will raise on the unmatched POST, failing the test loudly.
        """
        monkeypatch.setenv("HOME", str(tmp_path))
        # No ``with notebooklm_vcr.use_cassette(...):`` — any HTTP traffic here
        # is a regression. CliRunner traps the exception and surfaces it via
        # ``result.exception`` / non-zero exit code.
        result = runner.invoke(cli, ["language", "set", "ja", "--local", "--json"])
        assert_command_success(result, allow_no_context=False)

        data = parse_json_output(result.output)
        assert isinstance(data, dict)
        assert data.get("language") == "ja"
        assert data.get("synced_to_server") is False
