# Code Review Style Guide

This project is an unofficial Python client for Google NotebookLM using undocumented RPC APIs.

## Key Review Focus Areas

### Async Patterns
- All API methods should be `async` and use `await` properly
- Use `async with` for context managers (e.g., `NotebookLMClient`)
- Avoid blocking calls in async code

### RPC Layer (`src/notebooklm/rpc/`)
- Method IDs in `types.py` are undocumented and may change
- Parameter positions are critical - verify against existing patterns
- Source ID nesting varies: `[id]`, `[[id]]`, `[[[id]]]`, `[[[[id]]]]`

### Error Handling
- Use specific exception types from `types.py` (e.g., `SourceProcessingError`, `SourceTimeoutError`)
- Don't catch generic `Exception` unless re-raising

### Type Annotations
- All public APIs should have type hints
- Use `dataclasses` for data structures (see `types.py`)

### Testing
- Unit tests: no network calls, mock at RPC layer
- Integration tests: mock HTTP responses
- E2E tests: marked with `@pytest.mark.e2e`, require auth

### CLI (`src/notebooklm/cli/`)
- Use Click decorators consistently
- Follow existing command patterns in `helpers.py`

## Code Style

- Formatter: `ruff format`
- Linter: `ruff check`
- Python: 3.10+
- Follow PEP-8 with 88-character line limit (Black-compatible)

## What NOT to Flag

- Undocumented RPC method IDs (these are intentionally opaque)
- `# type: ignore` comments on RPC responses (necessary for untyped Google APIs)
