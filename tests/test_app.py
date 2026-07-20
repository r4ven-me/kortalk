"""Tests for app.py: the desktop launcher entry and tray-click handling."""

from types import SimpleNamespace

from PySide6.QtWidgets import QSystemTrayIcon

from kortalk.app import KortalkApp, ensure_desktop_entry


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
