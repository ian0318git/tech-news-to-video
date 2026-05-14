"""Shared CLI option decorators.

Provides reusable option decorators to reduce boilerplate in commands.
"""

from collections.abc import Callable

import click
from click.decorators import FC


def notebook_option(f: FC) -> FC:
    """Add --notebook/-n option for notebook ID.

    The option defaults to None, allowing context-based resolution.
    Supports partial ID matching (e.g., 'abc' matches 'abc123...').
    """
    return click.option(
        "-n",
        "--notebook",
        "notebook_id",
        default=None,
        help="Notebook ID (uses current if not set). Supports partial IDs.",
    )(f)


def json_option(f: FC) -> FC:
    """Add --json output flag."""
    return click.option(
        "--json",
        "json_output",
        is_flag=True,
        help="Output as JSON",
    )(f)


def wait_option(f: FC) -> FC:
    """Add --wait/--no-wait flag for generation commands."""
    return click.option(
        "--wait/--no-wait",
        default=False,
        help="Wait for completion (default: no-wait)",
    )(f)


def wait_polling_options(
    default_timeout: int = 300,
    default_interval: int = 2,
) -> Callable[[FC], FC]:
    """Bundle the shared ``--timeout`` / ``--interval`` polling flags.

    Used by every long-running CLI command so the flag surface stays uniform
    across ``generate <kind> --wait``, ``artifact wait``, and ``source wait``
    (audit row I6, P5.T1). Returns a decorator so each call site can supply
    its own historical defaults without diverging on flag name or help text.

    The ``--wait`` flag is intentionally NOT bundled here. It is a *trigger*
    flag on ``generate <kind>`` (paired with ``wait_option`` /
    ``generate_options``) and is implicit on ``artifact wait`` /
    ``source wait`` (those subcommands ARE the wait). Bundling ``--wait``
    here would either force-add it to commands that don't need it, or
    interact awkwardly with ``--wait/--no-wait``'s tri-state default on
    ``generate``. Keeping the trigger separate makes the surface uniform
    and honest about intent.

    Args:
        default_timeout: Default value for ``--timeout`` in seconds. Each
            command keeps its own historical default (e.g. ``generate audio``
            uses 300, ``source wait`` uses 120) so this PR is purely
            additive — no command changes its existing wait ceiling.
        default_interval: Default value for ``--interval`` in seconds. Most
            commands use 2 to match the existing ``artifact wait`` default;
            ``source wait`` uses 1 to match its underlying
            ``wait_until_ready`` default.

    Returns:
        A decorator that adds ``--timeout`` and ``--interval`` Click options
        to the wrapped command. The wrapped function gains two kwargs:
        ``timeout`` (int) and ``interval`` (int).

    Example:
        @click.command()
        @wait_polling_options(default_timeout=600, default_interval=2)
        def my_long_running_cmd(timeout: int, interval: int) -> None:
            ...
    """

    def decorator(f: FC) -> FC:
        f = click.option(
            "--interval",
            default=default_interval,
            type=int,
            help=f"Seconds between status checks (default: {default_interval})",
        )(f)
        f = click.option(
            "--timeout",
            default=default_timeout,
            type=int,
            help=f"Maximum seconds to wait (default: {default_timeout})",
        )(f)
        return f

    return decorator


def source_option(f: FC) -> FC:
    """Add --source/-s option for source ID.

    Supports partial ID matching (e.g., 'abc' matches 'abc123...').
    """
    return click.option(
        "-s",
        "--source",
        "source_id",
        required=True,
        help="Source ID. Supports partial IDs.",
    )(f)


def artifact_option(f: FC) -> FC:
    """Add --artifact/-a option for artifact ID.

    Supports partial ID matching (e.g., 'abc' matches 'abc123...').
    """
    return click.option(
        "-a",
        "--artifact",
        "artifact_id",
        required=True,
        help="Artifact ID. Supports partial IDs.",
    )(f)


def output_option(f: FC) -> FC:
    """Add --output/-o option for output file path."""
    return click.option(
        "-o",
        "--output",
        "output_path",
        type=click.Path(),
        default=None,
        help="Output file path",
    )(f)


def prompt_file_option(f: FC) -> FC:
    """Add --prompt-file option for reading prompt/query text from a file."""
    return click.option(
        "--prompt-file",
        "prompt_file",
        type=click.Path(exists=True, dir_okay=False),
        default=None,
        help="Read prompt/query text from a file instead of the positional argument",
    )(f)


def retry_option(f: FC) -> FC:
    """Add --retry option for rate limit retry with exponential backoff."""
    return click.option(
        "--retry",
        "max_retries",
        type=int,
        default=0,
        help="Retry N times with exponential backoff on rate limit",
    )(f)


# Composite decorators for common patterns


def standard_options(f: FC) -> FC:
    """Apply notebook + json options (most common pattern)."""
    return notebook_option(json_option(f))


def generate_options(f: FC) -> FC:
    """Apply notebook + json + wait + retry options for generation commands."""
    return notebook_option(json_option(wait_option(retry_option(f))))
