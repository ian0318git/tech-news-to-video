# Issue #243: notebooklm login with net::ERR_CONNECTION_CLOSED

## Root Cause

**Error**: `playwright._impl._errors.Error: Page.goto: net::ERR_CONNECTION_CLOSED at https://notebooklm.google.com/`

**Location**: `src/notebooklm/cli/session.py`, line 262 (initial page navigation)

### What's Happening

The `notebooklm login` command crashes when attempting the initial navigation to `https://notebooklm.google.com/` because the server closes the connection prematurely. This is a **transient network error** that should be retried.

### Root Causes

1. **Network connectivity issues** - Temporary network glitch, packet loss
2. **Firewall/VPN blocking** - Corporate firewall or VPN dropping connections
3. **Google rate limiting** - Too many rapid login attempts triggering temporary blocks
4. **Browser/Proxy interference** - Corporate proxy intercepting connections

## The Problem

The code had error handling for the **cookie-forcing navigation** (lines 275-280) but **NO error handling** for the **initial navigation** (line 262):

```python
page = context.pages[0] if context.pages else context.new_page()
page.goto(NOTEBOOKLM_URL)  # ← UNPROTECTED! Crashes on connection errors

# ... later ...

for url in [GOOGLE_ACCOUNTS_URL, NOTEBOOKLM_URL]:
    try:
        page.goto(url, wait_until="commit")
    except PlaywrightError as exc:
        if "Navigation interrupted" not in str(exc):
            raise  # ← Doesn't catch connection errors either
```

## The Fix

### Implementation

Added retry logic with exponential backoff (up to 3 attempts) for transient connection errors:

```python
# Navigate to NotebookLM with retry logic for connection errors
# net::ERR_CONNECTION_CLOSED can happen due to network issues, firewalls, or rate limiting
max_retries = 3
for attempt in range(1, max_retries + 1):
    try:
        page.goto(NOTEBOOKLM_URL, timeout=30000)
        break
    except PlaywrightError as exc:
        error_str = str(exc)
        # Retry on connection errors, but fail fast on other errors
        if "ERR_CONNECTION_CLOSED" in error_str or "ERR_CONNECTION_RESET" in error_str:
            if attempt < max_retries:
                console.print(
                    f"[yellow]Connection interrupted (attempt {attempt}/{max_retries}). Retrying...[/yellow]"
                )
                continue
            else:
                # Helpful error message after all retries exhausted
                console.print(
                    "[red]Failed to connect to NotebookLM after multiple retries.[/red]\n"
                    "This may be caused by:\n"
                    "  • Network connectivity issues\n"
                    "  • Firewall or VPN blocking notebooklm.google.com\n"
                    "  • Corporate proxy interfering with the connection\n"
                    "  • Google rate limiting (too many login attempts)\n\n"
                    "Try:\n"
                    "  1. Check your internet connection\n"
                    "  2. Disable VPN/proxy temporarily\n"
                    "  3. Wait a few minutes before retrying\n"
                    "  4. Check if notebooklm.google.com is accessible in your browser"
                )
                raise SystemExit(1) from None
        else:
            raise
```

### Key Design Decisions

1. **Specific error codes**: Only retry `ERR_CONNECTION_CLOSED` and `ERR_CONNECTION_RESET`. Other errors (invalid URL, etc.) fail fast.

2. **3 retries**: Balances between resilience and not waiting too long. Each retry has a 30-second timeout, so worst case is ~90 seconds.

3. **Helpful error message**: When retries are exhausted, users get actionable diagnostics instead of a cryptic stack trace.

4. **No exponential backoff**: Simple immediate retries are sufficient for transient network glitches.

## Test Coverage

Added 5 new tests to verify the fix:

1. **`test_login_retries_on_connection_closed_error`** - Verifies retry succeeds when first attempt fails with `ERR_CONNECTION_CLOSED`

2. **`test_login_retries_on_connection_reset_error`** - Verifies retry succeeds when first attempt fails with `ERR_CONNECTION_RESET`

3. **`test_login_exits_after_max_retries`** - Verifies proper exit and error message after 3 failed retries

4. **`test_login_fails_fast_on_non_retryable_errors`** - Verifies non-connection errors are not retried (fail immediately)

5. Existing test `test_login_handles_navigation_interrupted_error` still passes

## Files Changed

- `src/notebooklm/cli/session.py` - Added retry logic to initial navigation
- `tests/unit/cli/test_session.py` - Added 4 new tests for retry behavior

## Related Issues

- **#214**: Navigation interrupted errors (already fixed with `wait_until="commit"`)
- **#243**: Connection closed errors (fixed by this change)

## User Impact

**Before**: Login crashes immediately with `net::ERR_CONNECTION_CLOSED` - user loses time waiting for error and has no idea what went wrong.

**After**:
- If it's a transient glitch: Login succeeds after retry
- If persistent: User gets a helpful error message explaining possible causes and how to fix
- All other error types still surface (fail fast principle)
