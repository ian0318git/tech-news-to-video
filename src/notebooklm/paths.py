"""Path resolution for NotebookLM configuration files.

This module provides centralized path resolution that respects environment variables:

- NOTEBOOKLM_HOME: Base directory for all NotebookLM files (default: ~/.notebooklm)

All paths are derived from the home directory:
- storage_state.json: Authentication cookies from Playwright
- context.json: CLI context (current notebook/conversation)
- browser_profile/: Playwright browser profile directory

Usage:
    from notebooklm.paths import get_home_dir, get_storage_path

    # Get paths (respects NOTEBOOKLM_HOME if set)
    home = get_home_dir()
    storage = get_storage_path()

    # Create directories if needed
    home = get_home_dir(create=True)
"""

import os
import sys
from pathlib import Path


def get_home_dir(create: bool = False) -> Path:
    """Get NotebookLM home directory.

    Precedence: NOTEBOOKLM_HOME env var > ~/.notebooklm

    Args:
        create: If True, create directory. On Unix, sets 0o700 permissions via
            mkdir + chmod. On Windows, skips mode= and chmod entirely because:
            - Python < 3.13: mode= is silently ignored by mkdir().
            - Python >= 3.13: mode= applies Windows ACLs that can be overly
              restrictive, blocking other processes (even the same user) from
              reading the directory.
            In both cases, Windows inherits ACLs from the parent directory.

    Returns:
        Path to the NotebookLM home directory.

    Example:
        >>> import os
        >>> os.environ["NOTEBOOKLM_HOME"] = "/custom/path"
        >>> get_home_dir()
        PosixPath('/custom/path')
    """
    if home := os.environ.get("NOTEBOOKLM_HOME"):
        path = Path(home).expanduser().resolve()
    else:
        path = Path.home() / ".notebooklm"

    if create:
        if sys.platform == "win32":
            # On Windows < Python 3.13, mode= is ignored by mkdir(). On
            # Python 3.13+, mode= applies Windows ACLs that can be overly
            # restrictive (0o700 blocks other same-user processes). Skip mode
            # entirely and let Windows inherit ACLs from the parent directory.
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Ensure correct permissions even if directory already existed
            # (protects against TOCTOU race where attacker creates dir with wrong perms)
            path.chmod(0o700)

    return path


def get_storage_path() -> Path:
    """Get storage_state.json path.

    Returns:
        Path to storage_state.json within NOTEBOOKLM_HOME.
    """
    return get_home_dir() / "storage_state.json"


def get_context_path() -> Path:
    """Get context.json path.

    Returns:
        Path to context.json within NOTEBOOKLM_HOME.
    """
    return get_home_dir() / "context.json"


def get_browser_profile_dir() -> Path:
    """Get browser profile directory.

    Returns:
        Path to browser_profile/ within NOTEBOOKLM_HOME.
    """
    return get_home_dir() / "browser_profile"


def get_config_path() -> Path:
    """Get config.json path.

    Returns:
        Path to config.json within NOTEBOOKLM_HOME.
    """
    return get_home_dir() / "config.json"


def get_path_info() -> dict[str, str]:
    """Get diagnostic info about resolved paths.

    Useful for debugging and the `status` command.

    Returns:
        Dict with path information and sources.

    Example:
        >>> info = get_path_info()
        >>> print(info["home_source"])
        'NOTEBOOKLM_HOME' or 'default (~/.notebooklm)'
    """
    home_from_env = os.environ.get("NOTEBOOKLM_HOME")
    return {
        "home_dir": str(get_home_dir()),
        "home_source": "NOTEBOOKLM_HOME" if home_from_env else "default (~/.notebooklm)",
        "storage_path": str(get_storage_path()),
        "context_path": str(get_context_path()),
        "config_path": str(get_config_path()),
        "browser_profile_dir": str(get_browser_profile_dir()),
    }
