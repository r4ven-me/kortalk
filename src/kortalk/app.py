"""kortalk entry point: CLI, single instance, tray, global hotkeys.

Architecture: `kortalk` with no arguments starts the resident application
(tray + QLocalServer + global hotkeys). Everything is driven from the tray
and via hotkeys assigned in Settings. The CLI flags (--popup etc.) are kept
for scripting: they are delivered to the running instance over a local
socket and return immediately.
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import logging.handlers
import os
import shutil
import signal
import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QTimer
from PySide6.QtGui import QAction, QClipboard, QGuiApplication
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import __version__, i18n, theme
from .config import Config
from .hotkeys import GlobalHotkeys
from .i18n import tr
from .providers import shutdown_workers
from .settings_dialog import SettingsDialog
from .windows import MainWindow, PopupWindow

SOCKET_NAME = f"kortalk-{getpass.getuser()}"
LOG_DIR = Path(os.environ.get("XDG_STATE_HOME",
                              str(Path.home() / ".local" / "state"))) / "kortalk"
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
DESKTOP_FILE = DATA_DIR / "applications" / "kortalk.desktop"

# hotkey action prefix for "open the popup with a specific prompt"
_PROMPT_ACTION = "prompt:"

log = logging.getLogger(__name__)

DESKTOP_ENTRY = """\
[Desktop Entry]
Type=Application
Name=kortalk
Comment=Korvus AI popup for selected text
Exec={exec_path}
Icon={icon_path}
Terminal=false
Categories=Utility;Office;
StartupNotify=false
"""


def ensure_desktop_entry() -> None:
    """Installs an applications-menu launcher entry with the app icon.

    pip/pipx only puts the `kortalk` binary on PATH — there is no install
    hook for a per-user XDG menu entry, so the app writes its own on every
    daemon start (idempotent, and picks up icon/path changes on upgrade).
    """
    try:
        icon_path = theme.install_icon_file()
        exec_path = shutil.which("kortalk") or "kortalk"
        DESKTOP_FILE.parent.mkdir(parents=True, exist_ok=True)
        DESKTOP_FILE.write_text(
            DESKTOP_ENTRY.format(exec_path=exec_path, icon_path=icon_path), encoding="utf-8"
        )
    except OSError:
        pass  # non-critical: the app still runs fine from the tray/CLI


def setup_logging(debug: bool) -> None:
    """~/.local/state/kortalk/kortalk.log always; stderr with --debug."""
    root = logging.getLogger("kortalk")
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            LOG_DIR / "kortalk.log", maxBytes=1_000_000, backupCount=2,
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError:
        pass  # no permissions/space — keep running without the file
    if debug:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(fmt)
        root.addHandler(stream_handler)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kortalk",
        description="AI popup for selected text. With no arguments starts the tray "
                    "daemon; actions via the tray and global hotkeys "
                    "(configured in the app).",
    )
    parser.add_argument("prompt", nargs="?", default=None,
                        help="Prompt for a one-off popup (implies --popup).")
    parser.add_argument("--popup", action="store_true",
                        help="Popup near the cursor with a response to the selected text.")
    parser.add_argument("--window", "--split", action="store_true", dest="window",
                        help="Two-column window: text on the left, response on the right.")
    parser.add_argument("--settings", action="store_true", help="Open settings.")
    parser.add_argument("--provider", default=None,
                        help="Provider ID for this request (see Settings).")
    parser.add_argument("--quit", action="store_true", dest="quit_",
                        help="Quit the running instance.")
    parser.add_argument("--check", action="store_true", help="Environment diagnostics.")
    parser.add_argument("--debug", action="store_true", help="Verbose log and full tracebacks.")
    parser.add_argument("--version", action="version", version=f"kortalk {__version__}")
    return parser


def args_to_command(args) -> dict:
    if args.quit_:
        action = "quit"
    elif args.settings:
        action = "settings"
    elif args.window:
        action = "window"
    elif args.popup or args.prompt:
        action = "popup"
    else:
        action = "daemon"
    return {"action": action, "prompt": args.prompt, "provider": args.provider}


# ---------------------------------------------------------------------------
# --check
# ---------------------------------------------------------------------------

def run_selftest(config: Config) -> int:
    ok = True

    def report(label: str, passed: bool, remedy: str = "") -> None:
        nonlocal ok
        print(f"{'✅' if passed else '❌'} {label}")
        if not passed:
            ok = False
            if remedy:
                print(f"   → {remedy}")

    report(f"PySide6 (Qt {__import__('PySide6').QtCore.qVersion()})", True)

    clipboard = QGuiApplication.clipboard()
    report("PRIMARY selection (QClipboard)",
           clipboard.supportsSelection(),
           "the environment does not support PRIMARY selection")

    report("System tray", QSystemTrayIcon.isSystemTrayAvailable(),
           "tray unavailable — use the CLI flags (--popup, --window)")

    platform = QGuiApplication.platformName()
    print(f"ℹ️  Platform: {platform} → hotkey backend: "
          + ("XGrabKey (X11)" if platform == "xcb" else "XDG portal GlobalShortcuts"))

    for p in config.providers():
        if p.type == "claude-cli":
            report(f"provider “{p.name}”: claude CLI in PATH",
                   shutil.which("claude") is not None,
                   "install Claude Code CLI: https://docs.claude.com")
        elif p.type == "anthropic":
            report(f"provider “{p.name}”: API key set", bool(p.api_key),
                   "add the key: Settings → Providers")
        elif p.type == "openai":
            key_ok = bool(p.api_key) or not p.needs_api_key()
            report(f"provider “{p.name}”: base URL and key",
                   bool(p.base_url) and key_ok,
                   "fill in the fields: Settings → Providers")

    active = config.active_provider()
    print(f"\nActive provider: {active.name} ({active.type}"
          + (f", {active.model}" if active.model else "") + ")")
    active_prompt = config.active_prompt()
    print(f"Hotkeys: {active_prompt.name}={active_prompt.hotkey or '—'}, "
          f"window={config.hotkey('window') or '—'}")
    print(f"Settings file: {config.file_path()}")
    print("All good." if ok else "Issues found, see the recommendations above.")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Single instance
# ---------------------------------------------------------------------------

def try_send_to_running(command: dict) -> bool:
    """True when a running instance was found and the command delivered."""
    socket = QLocalSocket()
    socket.connectToServer(SOCKET_NAME)
    if not socket.waitForConnected(300):
        return False
    socket.write(QByteArray(json.dumps(command).encode("utf-8")))
    socket.flush()
    socket.waitForBytesWritten(1000)
    socket.disconnectFromServer()
    return True


class KortalkApp:
    """Resident application: tray + command server + hotkeys + windows."""

    def __init__(self, app: QApplication, config: Config):
        self.app = app
        self.config = config
        self.popup: PopupWindow | None = None
        self.main_window: MainWindow | None = None
        self.settings_dialog: SettingsDialog | None = None

        theme.apply_theme(app, str(config.get("theme")))
        theme.apply_font(app, str(config.get("font_family")), int(config.get("font_size")))
        app.setQuitOnLastWindowClosed(False)

        self.server = QLocalServer()
        QLocalServer.removeServer(SOCKET_NAME)  # clean up the socket after a crash
        self.server.listen(SOCKET_NAME)
        self.server.newConnection.connect(self._on_connection)

        # IMPORTANT: keep the menu and tray in attributes — setContextMenu
        # does not take ownership, and a local QMenu would be garbage
        # collected (crash on right click).
        self.tray = QSystemTrayIcon(theme.make_tray_icon())
        self.menu = QMenu()
        self.prompt_menu = QMenu(tr("Popup with prompt"))
        self._rebuild_menu()
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._tray_activated)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

        self.hotkeys = GlobalHotkeys()
        self.hotkeys.activated.connect(self._hotkey_activated)
        self._apply_hotkeys()

    # -- tray -----------------------------------------------------------------

    def _rebuild_menu(self) -> None:
        self.menu.clear()
        self.menu.addAction(tr("Popup with selection"), lambda: self.handle({"action": "popup"}))

        self.prompt_menu.clear()
        active_name = self.config.active_prompt().name
        for p in self.config.prompts():
            title = f"● {p.name}" if p.name == active_name else p.name
            if p.hotkey:
                title += f"\t{p.hotkey}"
            action = QAction(title, self.prompt_menu)
            action.triggered.connect(
                lambda _checked=False, name=p.name: self.handle(
                    {"action": "popup", "prompt_name": name})
            )
            self.prompt_menu.addAction(action)
        self.prompt_menu.setTitle(tr("Popup with prompt"))
        self.menu.addMenu(self.prompt_menu)

        self.menu.addAction(tr("Open window"), lambda: self.handle({"action": "window"}))
        self.menu.addSeparator()
        self.menu.addAction(tr("Settings"), self.open_settings)
        self.menu.addSeparator()
        self.menu.addAction(tr("Quit"), self.quit)
        self._update_tooltip()

    def _update_tooltip(self) -> None:
        provider = self.config.active_provider()
        active_prompt = self.config.active_prompt()
        lines = [f"kortalk — {provider.name}"]
        if active_prompt.hotkey:
            lines.append(f"{active_prompt.name}: {active_prompt.hotkey}")
        if self.hotkeys_note():
            lines.append(self.hotkeys_note())
        self.tray.setToolTip("\n".join(lines))

    def hotkeys_note(self) -> str:
        if getattr(self, "hotkeys", None) and self.hotkeys.backend == "none" and self.hotkeys.error:
            return tr("Hotkeys unavailable: {error}").format(error=self.hotkeys.error)
        return ""

    def _apply_hotkeys(self) -> None:
        bindings = {
            "window": self.config.hotkey("window"),
        }
        for p in self.config.prompts():
            if p.hotkey:
                bindings[_PROMPT_ACTION + p.name] = p.hotkey
        self.hotkeys.apply(bindings)
        self._update_tooltip()

    def _hotkey_activated(self, action: str) -> None:
        if action.startswith(_PROMPT_ACTION):
            self.handle({"action": "popup",
                         "prompt_name": action[len(_PROMPT_ACTION):]})
        else:
            self.handle({"action": action})

    def _tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:  # left click
            self.handle({"action": "popup"})

    # -- commands -------------------------------------------------------------

    def handle(self, command: dict) -> None:
        action = command.get("action", "daemon")
        log.debug("command: %s", command)
        if action == "quit":
            self.quit()
        elif action == "settings":
            self.open_settings()
        elif action == "window":
            self.open_window(command)
        elif action == "popup":
            self.open_popup(command)
        # anything else ("daemon", "noop") — just keep running

    def _selection_text(self) -> str:
        clipboard = QGuiApplication.clipboard()
        text = ""
        if clipboard.supportsSelection():
            text = clipboard.text(QClipboard.Mode.Selection)
        return text.strip()

    def _resolve(self, command: dict):
        provider = None
        if command.get("provider"):
            provider = self.config.provider(command["provider"])
        if provider is None:
            provider = self.config.active_provider()

        if command.get("prompt"):
            prompt = command["prompt"]
        elif command.get("prompt_name"):
            named = self.config.prompt_by_name(command["prompt_name"])
            prompt = named.text if named else self.config.active_prompt().text
        else:
            prompt = self.config.active_prompt().text

        selection = self._selection_text()
        full = f"{prompt}\n\n{selection}" if selection else prompt
        return provider, full, selection

    def open_popup(self, command: dict) -> None:
        provider, full_prompt, _selection = self._resolve(command)
        if self.popup is not None:
            self.popup.close()
        label = provider.name + (f" · {provider.model}" if provider.model else "")
        popup = PopupWindow(self.config, label)
        popup.open_in_window.connect(self._popup_to_window)
        # a deferred destroyed of the old popup must not clear the new one
        popup.destroyed.connect(lambda *_a, p=popup: self._popup_destroyed(p))
        self.popup = popup
        popup.show_near_cursor()
        popup.ask(provider, full_prompt)

    def _popup_destroyed(self, popup: PopupWindow) -> None:
        if self.popup is popup:
            self.popup = None

    def open_window(self, command: dict) -> None:
        _provider, full_prompt, selection = self._resolve(command)
        window = self._ensure_main_window()
        if selection or command.get("prompt"):
            window.set_input(full_prompt)
        window.show()
        window.raise_()
        window.activateWindow()

    def _popup_to_window(self, answer: str) -> None:
        window = self._ensure_main_window()
        window.set_output(answer)
        window.show()
        window.raise_()
        window.activateWindow()

    def _ensure_main_window(self) -> MainWindow:
        if self.main_window is None:
            self.main_window = MainWindow(self.config)
            self.main_window.settings_requested.connect(self.open_settings)
        return self.main_window

    def open_settings(self) -> None:
        if self.settings_dialog is not None:
            self.settings_dialog.raise_()
            self.settings_dialog.activateWindow()
            return
        self.settings_dialog = SettingsDialog(self.config)
        self.settings_dialog.saved.connect(self._settings_saved)
        self.settings_dialog.finished.connect(
            lambda _r: setattr(self, "settings_dialog", None)
        )
        self.settings_dialog.show()

    def _settings_saved(self) -> None:
        i18n.set_language(str(self.config.get("language")))
        theme.apply_theme(self.app, str(self.config.get("theme")))
        theme.apply_font(self.app, str(self.config.get("font_family")),
                         int(self.config.get("font_size")))
        self.tray.setIcon(theme.make_tray_icon())
        self._rebuild_menu()
        self._apply_hotkeys()
        if self.main_window is not None:
            self.main_window.reload_providers()
            self.main_window.refresh_theme()

    def quit(self) -> None:
        self.tray.hide()  # first: the cleanup below may take a moment
        self.server.close()
        QLocalServer.removeServer(SOCKET_NAME)
        self.hotkeys.stop()
        shutdown_workers()
        self.app.quit()

    # -- IPC ------------------------------------------------------------------

    def _on_connection(self) -> None:
        socket = self.server.nextPendingConnection()
        if socket is None:
            return
        socket.readyRead.connect(lambda: self._read_command(socket))

    def _read_command(self, socket) -> None:
        data = bytes(socket.readAll()).decode("utf-8", errors="replace")
        socket.disconnectFromServer()
        try:
            command = json.loads(data)
        except json.JSONDecodeError:
            command = {"action": "noop"}
        self.handle(command)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def install_signal_handlers(kortalk: KortalkApp) -> QTimer:
    """Make Ctrl+C (SIGINT) and SIGTERM quit the application cleanly.

    The `app.exec()` loop runs in C++ and does not execute Python bytecode,
    so a Python signal handler would never fire on its own. An empty timer
    wakes the interpreter periodically; the handler runs in the main (GUI)
    thread, so calling quit() from it is safe.
    """
    timer = QTimer()
    timer.timeout.connect(lambda: None)
    timer.start(200)

    def handler(signum, _frame) -> None:
        log.info("received %s, quitting", signal.Signals(signum).name)
        kortalk.quit()

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)
    return timer


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    command = args_to_command(args)
    setup_logging(args.debug)

    if not args.check:
        if try_send_to_running(command):
            if command["action"] == "daemon":
                print("kortalk is already running.", file=sys.stderr)
            return 0
        if args.quit_:
            print("kortalk is not running.", file=sys.stderr)
            return 1

    try:
        app = QApplication(sys.argv[:1])
        app.setApplicationName("kortalk")
        config = Config()
        i18n.set_language(str(config.get("language")))

        if args.check:
            return run_selftest(config)

        kortalk = KortalkApp(app, config)
        ensure_desktop_entry()
        _signal_timer = install_signal_handlers(kortalk)  # noqa: F841 — keep a reference
        log.info("kortalk %s started: platform=%s, hotkeys=%s",
                 __version__, QGuiApplication.platformName(), kortalk.hotkeys.backend)
        if command["action"] != "daemon":
            kortalk.handle(command)
        code = app.exec()
        log.info("kortalk exited (code %d)", code)
        return code
    except Exception as exc:  # noqa: BLE001
        log.exception("unhandled error")
        if args.debug:
            raise
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
