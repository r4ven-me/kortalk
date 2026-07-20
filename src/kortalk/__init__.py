"""kortalk — send selected text to an AI (Claude, OpenAI, local models).

PySide6 GUI: popup near the cursor, two-column window, tray icon,
graphical settings. Inspired by Crow Translate.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    # Single source of truth: pyproject.toml's [project].version, read back
    # from the installed package's metadata — a hardcoded string here would
    # drift the moment one of the two copies gets bumped and the other doesn't.
    __version__ = version("kortalk")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"  # running from a source checkout, not installed
