# Configuration

**Status:** Active
**Last Updated:** 2026-01-20

This guide covers storage locations, environment settings, and configuration options for `notebooklm-py`.

## File Locations

All data is stored under `~/.notebooklm/` by default, organized by profile:

```
~/.notebooklm/
├── active_profile        # Tracks the current profile name
├── profiles/
│   ├── default/          # Default profile (auto-created)
│   │   ├── storage_state.json    # Authentication cookies and session
│   │   ├── context.json          # CLI context (active notebook, conversation)
│   │   └── browser_profile/      # Persistent Chromium profile
│   ├── work/             # Named profile example
│   │   ├── storage_state.json
│   │   ├── context.json
│   │   └── browser_profile/
│   └── personal/
│       └── ...
```

**Legacy layout:** If upgrading from a pre-profile version, the first run auto-migrates flat files into `profiles/default/`. The legacy flat layout continues to work as a fallback.

You can relocate all files by setting `NOTEBOOKLM_HOME`:

```bash
export NOTEBOOKLM_HOME=/custom/path
# All files now go to /custom/path/profiles/<profile>/
```

### Storage State (`storage_state.json`)

Contains the authentication data extracted from your browser session:

```json
{
  "cookies": [
    {
      "name": "SID",
      "value": "...",
      "domain": ".google.com",
      "path": "/",
      "expires": 1234567890,
      "httpOnly": true,
      "secure": true,
      "sameSite": "Lax"
    },
    ...
  ],
  "origins": []
}
```

**Cookie requirements** (empirically validated via single- and pair-wise ablation, see `auth-keepalive.md` §3.5; enforced by `_validate_required_cookies()` in `auth.py`):

- **Tier 1 — strictly required (raises on absence):** `SID` AND `__Secure-1PSIDTS`. `SID` is the only individually-required cookie (`__Secure-1PSIDTS` is removable on its own because Google can re-mint it via `RotateCookies`), but the pair-wise check uncovered that as soon as `__Secure-1PSIDTS` and any one other auth cookie are both missing, Google rejects with `Authentication expired or invalid`. The library therefore enforces both up-front. Authoritative value: `MINIMUM_REQUIRED_COOKIES` in `auth.py`.
- **Tier 2 — secondary binding (logs a warning if absent):** either `OSID` is present, or both `APISID` and `SAPISID` are present. Without this, even valid Tier 1 cookies can't authenticate the homepage GET. Logged rather than raised so unverified edge-case flows (e.g. Workspace SSO) aren't broken by a too-strict client check.

In practice: extract the full cookie set via `notebooklm login` and don't try to subset it. Partial extractions (a known failure mode of browser-cookies tooling under Chrome 127+ App-Bound Encryption) are the leading suspect for "auth expires immediately" reports — see [#371](https://github.com/teng-lin/notebooklm-py/issues/371).

**Override location:**
```bash
notebooklm --storage /path/to/storage_state.json list
```

### Context File (`context.json`)

Stores the current CLI context (active notebook and conversation):

```json
{
  "notebook_id": "abc123def456",
  "conversation_id": "conv789"
}
```

This file is managed automatically by `notebooklm use` and `notebooklm clear`.

### Browser Profile (`browser_profile/`)

A persistent Chromium user data directory used during `notebooklm login`.

**Why persistent?** Google blocks automated login attempts. A persistent profile makes the browser appear as a regular user installation, avoiding bot detection.

**To reset:** Delete the `browser_profile/` directory and run `notebooklm login` again.

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `NOTEBOOKLM_HOME` | Base directory for all files | `~/.notebooklm` |
| `NOTEBOOKLM_PROFILE` | Active profile name | `default` |
| `NOTEBOOKLM_AUTH_JSON` | Inline authentication JSON (for CI/CD) | - |
| `NOTEBOOKLM_NOTEBOOK` | Default notebook ID for commands without `-n/--notebook` | - |
| `NOTEBOOKLM_HL` | Default interface/output language code (e.g. `en`, `ja`, `zh_Hans`) | `en` |
| `NOTEBOOKLM_LOG_LEVEL` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` | `WARNING` |
| `NOTEBOOKLM_DEBUG_RPC` | Legacy: Enable RPC debug logging (use `LOG_LEVEL=DEBUG` instead) | `false` |
| `NOTEBOOKLM_STRICT_DECODE` | Raise `UnknownRPCMethodError` on schema drift instead of warn-and-fallback | `0` |
| `NOTEBOOKLM_RPC_OVERRIDES` | Comma-separated `KEY=ID` pairs that override entries in `rpc/types.py` (community self-patch when Google rotates a method ID) | - |
| `NOTEBOOKLM_QUIET_DEPRECATIONS` | Suppress stderr deprecation notices for deprecated CLI flags | - |

### Env vars and precedence

Every `NOTEBOOKLM_*` variable read by the library and CLI, in one place. CLI
flags always win over env vars; env vars win over persisted profile config /
context; built-in defaults are the last fallback. The "Resolved by" column
points at the canonical resolver so the precedence rule for each variable can
be audited from one location.

| Variable | Purpose | Resolution order (highest → lowest) | Resolved by |
|----------|---------|-------------------------------------|-------------|
| `NOTEBOOKLM_PROFILE` | Active profile name. Selects which `~/.notebooklm/profiles/<name>/` directory backs storage and context. | `-p/--profile` flag → `NOTEBOOKLM_PROFILE` → persisted `active_profile` → `default` | `paths.set_active_profile` / `paths.get_active_profile` |
| `NOTEBOOKLM_AUTH_JSON` | Inline `storage_state.json` payload for CI/CD; bypasses on-disk profile storage entirely. | `--storage` flag → `NOTEBOOKLM_AUTH_JSON` → profile-aware `storage_state.json` → legacy fallback | `auth.load_auth_from_storage` |
| `NOTEBOOKLM_HOME` | Base directory for all per-profile files. | `NOTEBOOKLM_HOME` → `~/.notebooklm` | `paths.get_home_dir` |
| `NOTEBOOKLM_HL` | Default interface/output language for `generate <kind>` and the `hl` query parameter on every batchexecute RPC. | `--language` flag → `NOTEBOOKLM_HL` → profile config `language` → `en` | `language.resolve_hl` |
| `NOTEBOOKLM_LOG_LEVEL` | `DEBUG`/`INFO`/`WARNING`/`ERROR` floor for the `notebooklm` package logger. | `--quiet` flag (forces `ERROR`) → `-v/-vv` flags (force `INFO`/`DEBUG`) → `NOTEBOOKLM_DEBUG_RPC=1` (forces `DEBUG`) → `NOTEBOOKLM_LOG_LEVEL` → `WARNING` | `_logging.configure_logging` + `notebooklm_cli.cli` |
| `NOTEBOOKLM_DEBUG_RPC` | Legacy alias that sets the package logger to `DEBUG`. Prefer `NOTEBOOKLM_LOG_LEVEL=DEBUG` for new code. | (See `NOTEBOOKLM_LOG_LEVEL`.) | `_logging.configure_logging` |
| `NOTEBOOKLM_NOTEBOOK` | Default notebook ID when no `-n/--notebook` flag is passed. Composes with `notebooklm use <id>` so per-shell overrides do not clobber the persisted active-notebook context. | `-n/--notebook` flag → `NOTEBOOKLM_NOTEBOOK` → active context (from `notebooklm use`) → error | `cli.helpers.require_notebook` (Click also reads it natively via `cli/options.py:notebook_option`'s `envvar=`) |
| `NOTEBOOKLM_RPC_OVERRIDES` | Comma-separated `KEY=ID` pairs that override entries in `notebooklm/rpc/types.py`. Community self-patch when Google rotates a method ID. Empty string / unset disables the mechanism. | Process env at import time only — no flag override. | `_env.load_rpc_overrides` |
| `NOTEBOOKLM_QUIET_DEPRECATIONS` | Suppress stderr deprecation notices for deprecated CLI flags (e.g. `source add --mime-type` on file sources). Library-level `DeprecationWarning`s are unaffected. | Set to `1` to suppress; any other value (or unset) leaves the notice enabled. | individual CLI commands; see `NOTEBOOKLM_QUIET_DEPRECATIONS` section below |
| `NOTEBOOKLM_STRICT_DECODE` | Toggle the decoder's drift behavior — warn-and-fallback (`0`, default) vs raise `UnknownRPCMethodError` (`1`/`true`/`True`). | Process env on each decode call. | `rpc.decoder.safe_index` |

**Boolean handling.** `NOTEBOOKLM_DEBUG_RPC` and `NOTEBOOKLM_STRICT_DECODE`
treat `1` / `true` / `yes` (case-insensitive) as truthy; everything else is
falsy. `NOTEBOOKLM_QUIET_DEPRECATIONS` requires the literal string `1`.
`NOTEBOOKLM_NOTEBOOK` is treated as unset when empty or whitespace-only so a
bare `export NOTEBOOKLM_NOTEBOOK=` does not block `notebooklm use` /
`-n/--notebook` from resolving.

**The `--quiet` global flag.** `notebooklm --quiet <subcommand>` raises the
`notebooklm` package logger floor to `ERROR` for the duration of one
invocation, so cron and CI logs stay clean while real failures still surface.
It is mutually exclusive with `-v/-vv` — combining the two raises a
`UsageError` (exit `2`) since the resolved log levels conflict
(`ERROR` vs `INFO`/`DEBUG`). For per-call (rather than per-shell) silencing
of `INFO`/`WARN` the global flag is the preferred surface; `NOTEBOOKLM_LOG_LEVEL`
remains the right tool for shell-wide / always-on suppression.

### NOTEBOOKLM_HOME

Relocates all configuration files to a custom directory:

```bash
export NOTEBOOKLM_HOME=/custom/path

# All files now go here:
# /custom/path/profiles/<profile>/storage_state.json
# /custom/path/profiles/<profile>/context.json
# /custom/path/profiles/<profile>/browser_profile/
```

**Use cases:**
- Per-project isolation
- Custom storage locations

### NOTEBOOKLM_PROFILE

Selects the active profile without changing the persisted default:

```bash
export NOTEBOOKLM_PROFILE=work
notebooklm list   # Uses ~/.notebooklm/profiles/work/
```

Equivalent to passing `-p work` on every command. The CLI flag takes precedence over the env var.

### NOTEBOOKLM_AUTH_JSON

Provides authentication inline without writing files. Ideal for CI/CD:

```bash
export NOTEBOOKLM_AUTH_JSON='{"cookies":[...]}'
notebooklm list  # Works without any file on disk
```

**Precedence:**
1. `--storage` CLI flag (highest)
2. `NOTEBOOKLM_AUTH_JSON` environment variable
3. Profile-aware path: `$NOTEBOOKLM_HOME/profiles/<profile>/storage_state.json`
4. `~/.notebooklm/profiles/default/storage_state.json` (default)
5. `~/.notebooklm/storage_state.json` (legacy fallback)

**Note:** Cannot run `notebooklm login` when `NOTEBOOKLM_AUTH_JSON` is set.

### NOTEBOOKLM_HL

Sets the default interface/output language used by the client. The value is
passed as the `hl` query parameter on every batchexecute RPC call and is the
fallback language for the `generate audio|video|slide-deck|infographic|
data-table|mind-map|report` commands and their `ArtifactsAPI` equivalents:

```bash
export NOTEBOOKLM_HL=ja
notebooklm generate audio "deep dive"   # Japanese audio overview
```

Surrounding whitespace is stripped; an empty or whitespace-only value falls
back to `en`. For the generate commands, the resolution order is:

1. `--language` CLI flag
2. `NOTEBOOKLM_HL` environment variable
3. `language` value from the active profile's config
4. `en` (built-in default)

### NOTEBOOKLM_QUIET_DEPRECATIONS

Suppresses stderr deprecation notices emitted by CLI commands when a
deprecated flag or option is used. Useful in CI logs where the deprecation
signal would otherwise be repeated across every invocation in a pipeline.

```bash
export NOTEBOOKLM_QUIET_DEPRECATIONS=1
notebooklm source add ./report.pdf --type file --mime-type application/pdf
# (no "--mime-type is unused for file sources" notice on stderr)
```

Set the value to ``1`` to suppress the notice; any other value (including
``0`` or ``false``) leaves the deprecation notice enabled. The underlying
behavior — that the deprecated flag remains a no-op — is unchanged; only
the user-facing
warning text is silenced. Library-level `DeprecationWarning`s emitted from
the Python API (e.g. `client.sources.add_file(..., mime_type=...)`) are
**not** affected by this variable; use standard `warnings.filterwarnings`
to manage those programmatically.

### Decoder strictness

NotebookLM's batchexecute responses are obfuscated, undocumented, and reshaped
by Google without notice. The decoder uses a shared `safe_index` helper to walk
nested response payloads. When it can't descend (an index is out of range, or
the value at a step isn't indexable), behavior depends on
`NOTEBOOKLM_STRICT_DECODE`:

| Value | Behavior |
|-------|----------|
| `0` (default) | Log a warning with the failing path, `method_id`, `source` label, and a truncated repr of the data. Return `None` so legacy callers keep working. |
| `1` / `true` / `True` | Raise `UnknownRPCMethodError` (a subclass of `DecodingError` / `RPCError`) with structured `method_id`, `path`, `source`, and `data_at_failure` attributes. |

The default of `0` is a soft-rollout safeguard for this release while
call sites migrate to defensive indexing. A future release will flip the
default to `1` — set `NOTEBOOKLM_STRICT_DECODE=1` in your CI/staging
environments now to catch drift early.

The same `UnknownRPCMethodError` is also raised by `decode_response()` when the
batchexecute response contains RPC IDs but not the one the call requested
(typically a sign that Google rotated the method ID).

## CLI Options

### Global Options

| Option | Description | Default |
|--------|-------------|---------|
| `--storage PATH` | Path to storage_state.json | `$NOTEBOOKLM_HOME/profiles/<profile>/storage_state.json` |
| `-p, --profile NAME` | Use a named profile | Active profile or `default` |
| `-v, --verbose` | Enable verbose output (`-v` for INFO, `-vv` for DEBUG) | - |
| `--quiet` | Suppress INFO/WARN logs on stderr (only ERROR survives). Mutually exclusive with `-v`. | - |
| `--version` | Show version | - |
| `--help` | Show help | - |

### Viewing Configuration

See where your configuration files are located:

```bash
notebooklm status --paths
```

Output:
```
                Configuration Paths
┏━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┓
┃ File            ┃ Path                                     ┃ Source    ┃
┡━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━┩
│ Profile         │ default                                  │ active    │
│ Home Directory  │ /home/user/.notebooklm                   │ default   │
│ Storage State   │ .../profiles/default/storage_state.json  │           │
│ Context         │ .../profiles/default/context.json        │           │
│ Browser Profile │ .../profiles/default/browser_profile     │           │
└─────────────────┴──────────────────────────────────────────┴───────────┘
```

## Session Management

### Session Lifetime

Authentication sessions are tied to Google's cookie expiration:
- Sessions typically last several days to weeks
- Google may invalidate sessions for security reasons
- Rate limiting or suspicious activity can trigger earlier expiration

### Refreshing Sessions

**Automatic Refresh:** CSRF tokens and session IDs are automatically refreshed when authentication errors are detected. This handles most "session expired" errors transparently.

**Manual Re-authentication:** If your session cookies have fully expired (automatic refresh won't help), re-authenticate:

```bash
notebooklm login
```

### Multiple Accounts

**Profiles (recommended):** Use named profiles to manage multiple Google accounts under a single home directory:

```bash
# Create and authenticate profiles
notebooklm profile create work
notebooklm -p work login
notebooklm -p work list

notebooklm profile create personal
notebooklm -p personal login
notebooklm -p personal list

# Switch the active profile
notebooklm profile switch work
notebooklm list   # Uses work profile

# List all profiles
notebooklm profile list

# Use env var for session-wide override
export NOTEBOOKLM_PROFILE=personal
notebooklm list   # Uses personal profile
```

Each profile stores its own `storage_state.json`, `context.json`, and `browser_profile/` under `~/.notebooklm/profiles/<name>/`.

**Alternative: `NOTEBOOKLM_HOME`** still works for full directory-level isolation:

```bash
export NOTEBOOKLM_HOME=~/.notebooklm-work
notebooklm login
```

**One-off override with `--storage`:**

```bash
notebooklm --storage /path/to/storage_state.json list
```

When `--storage <path>` is set, notebook/conversation context is isolated to a
sibling file `<path>.context.json` next to the storage file. This means two
`--storage` invocations against different files cannot see each other's
selected notebook, and neither pollutes the default profile context. Run
`notebooklm --storage <path> status --paths` to see exactly which sibling
context file is being used.

## CI/CD Configuration

### GitHub Actions (Recommended)

Use `NOTEBOOKLM_AUTH_JSON` for secure, file-free authentication:

```yaml
jobs:
  notebook-task:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install notebooklm-py
        run: pip install notebooklm-py

      # Pre-flight: fail fast and loud on missing/expired auth.
      # `auth check --json` returns exit 0 even when status is "error"; --test makes the network
      # call needed to detect expired cookies, and the `jq -e` flag converts a non-"ok" status
      # into a non-zero exit code so the runner step actually fails.
      - name: Verify auth (fail-fast on expired cookies)
        env:
          NOTEBOOKLM_AUTH_JSON: ${{ secrets.NOTEBOOKLM_AUTH_JSON }}
        run: notebooklm auth check --test --json | jq -e '.status == "ok"'

      - name: List notebooks
        env:
          NOTEBOOKLM_AUTH_JSON: ${{ secrets.NOTEBOOKLM_AUTH_JSON }}
        run: notebooklm list
```

**Benefits:**
- No file writes needed
- Secret stays in memory only
- Clean, simple workflow

### Obtaining the Secret Value

1. Run `notebooklm login` locally
2. Copy the contents of `~/.notebooklm/profiles/default/storage_state.json` (the canonical write location; the legacy `~/.notebooklm/storage_state.json` is only read as a fallback)
3. Add as a GitHub repository secret named `NOTEBOOKLM_AUTH_JSON` (see [installation.md#d-headless-server-or-ci](installation.md#d-headless-server-or-ci) for trailing-newline + ephemeral-runner refresh notes)

### Alternative: File-Based Auth

If you prefer file-based authentication:

```yaml
- name: Setup NotebookLM auth
  run: |
    mkdir -p ~/.notebooklm/profiles/default
    echo "${{ secrets.NOTEBOOKLM_AUTH_JSON }}" > ~/.notebooklm/profiles/default/storage_state.json
    chmod 600 ~/.notebooklm/profiles/default/storage_state.json

- name: List notebooks
  run: notebooklm list
```

For profile-specific CI auth:

```yaml
- name: Setup work profile auth
  run: |
    mkdir -p ~/.notebooklm/profiles/work
    echo "${{ secrets.WORK_AUTH_JSON }}" > ~/.notebooklm/profiles/work/storage_state.json
    chmod 600 ~/.notebooklm/profiles/work/storage_state.json

- name: List notebooks (work)
  run: notebooklm -p work list
```

### Session Expiration

CSRF tokens are automatically refreshed during API calls. However, the underlying session cookies still expire. For long-running CI pipelines:
- Update the `NOTEBOOKLM_AUTH_JSON` secret every 1-2 weeks
- Monitor for persistent auth failures (these indicate cookie expiration)

## Debugging

### Enable Verbose Output

Some commands support verbose output via Rich console:

```bash
# Most errors are printed to stderr with details
notebooklm list 2>&1 | cat
```

### Enable RPC Debug Logging

```bash
NOTEBOOKLM_DEBUG_RPC=1 notebooklm list
```

### Check Authentication

Verify your session is working:

```bash
# Should list notebooks or show empty list
notebooklm list

# If you see "Unauthorized" or redirect errors, re-login
notebooklm login
```

### Check Configuration Paths

```bash
# See where files are being read from
notebooklm status --paths
```

### Network Issues

The CLI uses `httpx` for HTTP requests. Common issues:

- **Timeout**: Google's API can be slow; large operations may time out
- **SSL errors**: Ensure your system certificates are up to date
- **Proxy**: Set standard environment variables (`HTTP_PROXY`, `HTTPS_PROXY`) if needed

## Platform Notes

### macOS

Works out of the box. Chromium is downloaded automatically by Playwright.

### Linux

For Playwright system dependencies and the Chromium install on Debian/Ubuntu, see [docs/installation.md#platform-notes](installation.md#platform-notes) (and [troubleshooting.md#linux](troubleshooting.md#linux) if you hit `TypeError: onExit is not a function`).

### Windows

Works with PowerShell or CMD. Use backslashes for paths:

```powershell
notebooklm --storage C:\Users\Name\.notebooklm\storage_state.json list
```

Or set environment variable:

```powershell
$env:NOTEBOOKLM_HOME = "C:\Users\Name\custom-notebooklm"
notebooklm list
```

### WSL

Browser login opens in the Windows host browser. The storage file is saved in the WSL filesystem.

### Headless Servers & Containers

**Playwright is only required for the `notebooklm login` command.** All other operations use standard HTTP requests via `httpx`.

For the install + auth-bootstrap recipe (run `notebooklm login` on a workstation, copy `storage_state.json` to the server, set `NOTEBOOKLM_AUTH_JSON`), see the canonical Persona D guide: [docs/installation.md#d-headless-server-or-ci](installation.md#d-headless-server-or-ci).
