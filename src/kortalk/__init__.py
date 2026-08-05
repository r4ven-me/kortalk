"""kortalk — send selected text to an AI (Claude, OpenAI, local models).

PySide6 GUI: popup near the cursor, two-column window, tray icon,
graphical settings. Inspired by Crow Translate.
"""

from importlib.metadata import PackageNotFoundError, metadata, version

try:
    # Single source of truth: pyproject.toml's [project] table, read back
    # from the installed package's metadata (populated from it at build
    # time) — hardcoding copies of these here would drift the moment
    # pyproject.toml changes and this file doesn't.
    __version__ = version("kortalk")
    _metadata = metadata("kortalk")
    __description__ = _metadata.get("Summary", "")
    __author__ = _metadata.get("Author", "")
    __license__ = _metadata.get("License", "")
    __homepage__ = next(
        (
            url.split(",", 1)[1].strip()
            for url in _metadata.get_all("Project-URL", [])
            if url.split(",", 1)[0].strip() == "Homepage"
        ),
        "",
    )
except PackageNotFoundError:
    # Running from a source checkout, not installed.
    __version__ = "0.0.0+unknown"
    __description__ = ""
    __author__ = ""
    __license__ = ""
    __homepage__ = ""
