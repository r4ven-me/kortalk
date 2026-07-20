"""Tests for app.py: the desktop launcher entry and tray-click handling."""

import subprocess
from types import SimpleNamespace

from PySide6.QtWidgets import QSystemTrayIcon

from kortalk.app import KortalkApp, augment_path_from_login_shell, ensure_desktop_entry


def test_ensure_desktop_entry_writes_icon_and_launcher(tmp_path):
    import kortalk.app as app_mod
    import kortalk.theme as theme_mod

    ensure_desktop_entry()

    assert theme_mod.ICON_FILE.exists()
    assert theme_mod.ICON_FILE.read_text(encoding="utf-8").startswith("<?xml")

    text = app_mod.DESKTOP_FILE.read_text(encoding="utf-8")
    assert "{exec_path}" not in text
    assert "{icon_path}" not in text
    assert f"Icon={theme_mod.ICON_FILE}" in text
    assert any(line.startswith("Exec=") and len(line) > len("Exec=")
               for line in text.splitlines())


def test_ensure_desktop_entry_is_idempotent(tmp_path):
    ensure_desktop_entry()
    ensure_desktop_entry()  # must not raise or corrupt the file

    import kortalk.app as app_mod
    assert app_mod.DESKTOP_FILE.exists()


def test_ensure_desktop_entry_falls_back_to_argv0_when_not_on_path(tmp_path, monkeypatch):
    # regression: shutil.which("kortalk") can return None if this very
    # process started before ~/.local/bin was added to PATH (e.g. under
    # autostart) — Exec= must still end up as an absolute, working path,
    # not the bare command name.
    import kortalk.app as app_mod

    monkeypatch.setattr(app_mod.shutil, "which", lambda _name: None)
    monkeypatch.setattr(app_mod.sys, "argv", ["/opt/weird/path/kortalk"])

    ensure_desktop_entry()

    text = app_mod.DESKTOP_FILE.read_text(encoding="utf-8")
    exec_line = next(line for line in text.splitlines() if line.startswith("Exec="))
    assert exec_line == "Exec=/opt/weird/path/kortalk"


def test_augment_path_adds_missing_login_shell_dirs(monkeypatch):
    # regression: apps launched from the applications menu inherit a
    # minimal PATH that skips ~/.bashrc-installed dirs (e.g. ~/.local/bin),
    # so `claude` can be unreachable even though it works from a terminal.
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("SHELL", "/bin/bash")
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            a[0], 0, stdout="/usr/bin:/bin:/home/user/.local/bin\n", stderr="")
    )

    augment_path_from_login_shell()

    import os
    assert os.environ["PATH"] == "/usr/bin:/bin:/home/user/.local/bin"


def test_augment_path_does_not_reorder_existing_entries(monkeypatch):
    # a dir already on PATH keeps its current priority — only genuinely
    # missing dirs are appended, nothing gets moved or duplicated.
    monkeypatch.setenv("PATH", "/custom/first:/usr/bin:/bin")
    monkeypatch.setenv("SHELL", "/bin/bash")
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            a[0], 0, stdout="/usr/bin:/bin\n", stderr="")
    )

    augment_path_from_login_shell()

    import os
    assert os.environ["PATH"] == "/custom/first:/usr/bin:/bin"


def test_augment_path_ignores_interactive_shell_banner_noise(monkeypatch):
    # -ilc (interactive) is needed because PATH edits usually live in
    # .zshrc/.bashrc, not .zprofile — but an interactive shell may print a
    # MOTD/prompt fragment before running our command; only the last line
    # is the actual $PATH output.
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            a[0], 0,
            stdout="Welcome back!\nsome-plugin-banner\n/usr/bin:/bin:/home/user/.local/bin\n",
            stderr="")
    )

    augment_path_from_login_shell()

    import os
    assert os.environ["PATH"] == "/usr/bin:/bin:/home/user/.local/bin"


def test_augment_path_survives_a_broken_shell(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("no such shell"))
    )

    augment_path_from_login_shell()  # must not raise

    import os
    assert os.environ["PATH"] == "/usr/bin:/bin"


class _FakeWindow:
    def __init__(self, visible: bool):
        self._visible = visible
        self.hidden = False

    def isVisible(self) -> bool:
        return self._visible

    def hide(self) -> None:
        self.hidden = True


def _fake_app(main_window):
    calls = []
    fake = SimpleNamespace(main_window=main_window, handle=lambda cmd: calls.append(cmd))
    return fake, calls


def test_tray_click_opens_the_window_when_none_exists():
    fake, calls = _fake_app(main_window=None)
    KortalkApp._tray_activated(fake, QSystemTrayIcon.ActivationReason.Trigger)
    assert calls == [{"action": "window"}]


def test_tray_click_opens_a_hidden_window():
    fake, calls = _fake_app(main_window=_FakeWindow(visible=False))
    KortalkApp._tray_activated(fake, QSystemTrayIcon.ActivationReason.Trigger)
    assert calls == [{"action": "window"}]
    assert fake.main_window.hidden is False


def test_tray_click_hides_a_visible_window():
    # clicking the tray again while the window is open collapses it back
    fake, calls = _fake_app(main_window=_FakeWindow(visible=True))
    KortalkApp._tray_activated(fake, QSystemTrayIcon.ActivationReason.Trigger)
    assert fake.main_window.hidden is True
    assert calls == []  # not re-opened, just hidden


def test_tray_context_menu_click_is_ignored():
    fake, calls = _fake_app(main_window=_FakeWindow(visible=True))
    KortalkApp._tray_activated(fake, QSystemTrayIcon.ActivationReason.Context)
    assert calls == []
    assert fake.main_window.hidden is False
