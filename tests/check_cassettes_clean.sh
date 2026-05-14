#!/usr/bin/env bash
# CI grep guard for cassette PII leaks.
#
# Fails the build (exit 1) if any cassette under tests/cassettes/ contains:
#   - an unsanitized email at a real provider (gmail, googlemail, google,
#     anthropic, outlook, hotmail, yahoo, icloud, protonmail)
#   - an unsanitized Google session cookie value in any of THREE cookie shapes:
#       Shape A: HTTP "Cookie: SID=value;" header form.
#       Shape B: JSON object with the cookie name as the key,
#                e.g. {"SID":"value"}.
#       Shape C: Playwright storage_state.json form,
#                e.g. {"name":"SID","value":"value","domain":"..."}.
#     A cookie value is considered scrubbed when it starts with 'S' (the
#     canonical "SCRUBBED" sentinel and its "SCRUBBED_*" variants). See the
#     "Cookie value heuristic" comment below for the trade-offs.
#
# The guard scans all files under ``tests/cassettes/`` (including untracked
# ones), so a freshly recorded cassette that hasn't been staged yet still gets
# inspected.
#
# Usage:
#   ./tests/check_cassettes_clean.sh
#
# Exit codes:
#   0 — cassettes are clean
#   1 — one or more leaks found
set -e

# No cassettes to check (e.g., fresh checkout with no recorded fixtures) is a
# valid clean state — exit 0 without trying to grep a missing directory, which
# would otherwise trip ``set -e`` even when no leak exists.
if [ ! -d "tests/cassettes/" ]; then
    echo "OK: no cassettes directory to check"
    exit 0
fi

# Use plain ``grep -rnE`` (NOT ``git grep``) so the guard also scans cassettes
# that have just been recorded with ``NOTEBOOKLM_VCR_RECORD=1`` but not yet
# staged. ``git grep`` would silently skip those untracked files, creating a
# false-negative path in local pre-commit runs (Claude review on PR #477, #5).
GREP=(grep -rnE)

# Email regex — matches the address itself (un-anchored on quotes) so an
# unquoted leak in raw HTML/JS content (e.g., a ``mailto:`` href or an
# inline-rendered template) is also caught. Aligned with the legacy
# ``[a-zA-Z0-9._%+-]+@gmail.com`` pattern in ``tests/vcr_config.py`` but
# broadened to the same provider list as the JSON-aware sanitizer.
email_re='[A-Za-z0-9._%+-]+@(gmail|googlemail|google|anthropic|outlook|hotmail|yahoo|icloud|protonmail)\.com'

# ---------------------------------------------------------------------------
# Cookie value heuristic
#
# Our sanitizer always replaces real cookie values with strings starting in
# 'S' — "SCRUBBED" and "SCRUBBED_*" sentinels. We use ``[^S"]`` as the first
# character of the captured value to reject a scrubbed sentinel while still
# matching any leaked value whose first character is not 'S'. This is a
# heuristic: a real leaked token whose first character happens to be 'S'
# (~1/62 chance for base64) will slip through. We accept that gap because the
# scrubber upstream is canonical — the guard is a defense-in-depth check, not
# a content-classifier. ``[^"]*`` (zero or more) covers single-char values as
# well, so even a one-character leak trips the guard. (Claude review on
# PR #477, #7.)
# ---------------------------------------------------------------------------

# Shape A — Cookie header / Set-Cookie style: "SID=value;" where the value
# does not start with the scrubbed sentinel.
cookie_header_re='\b(SID|SAPISID|HSID|SSID|APISID|__Secure-[13]PSID)=[^;S][^;]*'

# Shape B — JSON cookie-name-as-key: "SID":"value". Used by request-body JSON
# the client sends to NotebookLM and any place that dumps cookies into a flat
# JSON dict.
cookie_json_key_re='"(SID|SAPISID|HSID|SSID|APISID|__Secure-[13]PSID)"[[:space:]]*:[[:space:]]*"[^S"][^"]*"'

# Shape C — Playwright storage_state.json form:
#   {"name":"SID", ... ,"value":"realvalue", ...}
# The value field may appear before or after "name"; we check both orderings.
cookie_storage_state_re_a='"name"[[:space:]]*:[[:space:]]*"(SID|SAPISID|HSID|SSID|APISID|__Secure-[13]PSID)"[^}]*"value"[[:space:]]*:[[:space:]]*"[^S"][^"]*"'
cookie_storage_state_re_b='"value"[[:space:]]*:[[:space:]]*"[^S"][^"]*"[^}]*"name"[[:space:]]*:[[:space:]]*"(SID|SAPISID|HSID|SSID|APISID|__Secure-[13]PSID)"'

if "${GREP[@]}" "$email_re" tests/cassettes/ ; then
    echo "ERROR: unsanitized email found in cassette" >&2
    exit 1
fi

if "${GREP[@]}" "$cookie_header_re" tests/cassettes/ ; then
    echo "ERROR: unsanitized cookie header value found in cassette" >&2
    exit 1
fi

if "${GREP[@]}" "$cookie_json_key_re" tests/cassettes/ ; then
    echo "ERROR: unsanitized cookie JSON-key value found in cassette" >&2
    exit 1
fi

if "${GREP[@]}" "$cookie_storage_state_re_a" tests/cassettes/ ; then
    echo "ERROR: unsanitized Playwright storage_state cookie found in cassette (name-before-value)" >&2
    exit 1
fi

if "${GREP[@]}" "$cookie_storage_state_re_b" tests/cassettes/ ; then
    echo "ERROR: unsanitized Playwright storage_state cookie found in cassette (value-before-name)" >&2
    exit 1
fi

echo "OK: cassettes are sanitized"
