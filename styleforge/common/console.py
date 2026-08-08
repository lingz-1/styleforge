"""Console compatibility helpers."""

from __future__ import annotations

import sys


def configure_utf8_console() -> None:
    """Use UTF-8 for redirected Windows output while preserving testability."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
