# Code Review Guidelines for notebooklm-py

## Project Context

This is an unofficial Python client for Google NotebookLM using undocumented RPC APIs. The codebase uses async/await patterns extensively with httpx for HTTP.

## Security

- Flag any hardcoded credentials, API keys, or tokens
- Check for proper CSRF token handling in RPC calls
- Ensure cookies and auth data are not logged or exposed
- Verify input validation for user-provided URLs and content

## Python Patterns

- Require type hints for public API methods in `client.py` and `_*.py` files
- Prefer `async with` context managers for HTTP client lifecycle
- Use `httpx.AsyncClient` consistently (not requests or aiohttp)
- Check for proper exception handling in async code

## Architecture

- RPC method IDs in `rpc/types.py` are the source of truth
- Verify parameter nesting matches existing patterns (some need `[id]`, others `[[id]]`, etc.)
- CLI commands should use Click decorators consistently
- Client methods should be in the appropriate namespace (`notebooks`, `sources`, `artifacts`, `chat`)

## Testing

- New features should have corresponding tests
- Mock HTTP responses in integration tests, don't call real APIs
- E2E tests are separate and require authentication

## Style

- Line length limit is 100 characters
- Use double quotes for strings
- Imports should be sorted (ruff handles this)
- No trailing whitespace
