"""Public I/O helpers (re-exports from internal _atomic_io).

Exists so :mod:`notebooklm.cli` can import :func:`atomic_write_json` without
violating the ``cli/`` boundary rule (no ``notebooklm._*`` imports). See
``tests/unit/test_cli_boundary.py``.
"""

from ._atomic_io import atomic_write_json

__all__ = ["atomic_write_json"]
