"""Tests for app.py: the desktop launcher entry and tray-click handling."""

import os
import subprocess
from types import SimpleNamespace

from PySide6.QtNetwork import QLocalServer
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


def test_local_ipc_socket_is_restricted_to_the_current_user(qapp, config, monkeypatch):
    # Qt's default socket permissions let any local user connect and send
    # commands (open a popup with an arbitrary prompt through the victim's
    # configured provider, --quit, ...) — regression guard for the
    # UserAccessOption fix in KortalkApp.__init__.
    import kortalk.app as app_mod

    test_socket_name = "kortalk-test-ipc-permissions"
    monkeypatch.setattr(app_mod, "SOCKET_NAME", test_socket_name)
    QLocalServer.removeServer(test_socket_name)

    kortalk = KortalkApp(qapp, config)
    try:
        path = kortalk.server.fullServerName()
        mode = os.stat(path).st_mode
        assert mode & 0o077 == 0, "group/other must have no access to the IPC socket"
    finally:
        kortalk.quit()


def _make_kortalk(qapp, config, monkeypatch, name):
    import kortalk.app as app_mod

    monkeypatch.setattr(app_mod, "SOCKET_NAME", name)
    QLocalServer.removeServer(name)
    return KortalkApp(qapp, config)


def test_resolve_uses_the_prompts_own_provider_when_set(qapp, config, monkeypatch):
    # a hotkey-bound prompt can pin its popup to a specific provider,
    # overriding whatever provider is currently active
    prompts = config.prompts()
    prompts[1].provider_id = "ollama"  # "Translate"
    config.set_prompts(prompts)

    kortalk = _make_kortalk(qapp, config, monkeypatch, "kortalk-test-resolve-1")
    try:
        provider, prompt, _selection = kortalk._resolve(
            {"action": "popup", "prompt_name": "Translate"})
        assert provider.id == "ollama"
        assert prompt.startswith("Translate")
    finally:
        kortalk.quit()


def test_resolve_falls_back_to_the_active_provider_when_prompt_has_none(qapp, config, monkeypatch):
    kortalk = _make_kortalk(qapp, config, monkeypatch, "kortalk-test-resolve-2")
    try:
        provider, _prompt, _selection = kortalk._resolve(
            {"action": "popup", "prompt_name": "Fix"})
        assert provider.id == config.active_provider().id
    finally:
        kortalk.quit()


def test_resolve_explicit_provider_override_wins_over_the_prompts_own(qapp, config, monkeypatch):
    prompts = config.prompts()
    prompts[1].provider_id = "ollama"
    config.set_prompts(prompts)

    kortalk = _make_kortalk(qapp, config, monkeypatch, "kortalk-test-resolve-3")
    try:
        provider, _prompt, _selection = kortalk._resolve(
            {"action": "popup", "prompt_name": "Translate", "provider": "anthropic"})
        assert provider.id == "anthropic"
    finally:
        kortalk.quit()


def test_resolve_does_not_change_the_dialog_windows_active_provider(qapp, config, monkeypatch):
    # regression: a popup pinned to a specific provider must not leak into
    # config's "active_provider" — that's what the dialog window's provider
    # dropdown reads, and it must stay exactly as the user last set it there
    prompts = config.prompts()
    prompts[1].provider_id = "ollama"
    config.set_prompts(prompts)
    before = config.get("active_provider")

    kortalk = _make_kortalk(qapp, config, monkeypatch, "kortalk-test-resolve-4")
    try:
        kortalk._resolve({"action": "popup", "prompt_name": "Translate"})
        assert config.get("active_provider") == before
    finally:
        kortalk.quit()


def test_resolve_raw_prompt_text_ignores_the_active_prompts_provider(qapp, config, monkeypatch):
    # a one-off `kortalk "some text"` prompt isn't tied to any named prompt,
    # so it must not silently inherit the active prompt's provider pin
    prompts = config.prompts()
    active_name = config.active_prompt().name
    for p in prompts:
        if p.name == active_name:
            p.provider_id = "ollama"
    config.set_prompts(prompts)

    kortalk = _make_kortalk(qapp, config, monkeypatch, "kortalk-test-resolve-5")
    try:
        provider, prompt, _selection = kortalk._resolve(
            {"action": "popup", "prompt": "custom text"})
        assert provider.id == config.active_provider().id
        assert prompt == "custom text"
    finally:
        kortalk.quit()


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
    # lets _hotkey_activated's "window" branch (which delegates to
    # _tray_activated for the same show/hide toggle a tray click gets)
    # run against this fake too, not just _tray_activated's own tests
    fake._tray_activated = lambda reason: KortalkApp._tray_activated(fake, reason)
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


def test_window_hotkey_opens_the_window_when_none_exists():
    fake, calls = _fake_app(main_window=None)
    KortalkApp._hotkey_activated(fake, "window")
    assert calls == [{"action": "window"}]


def test_window_hotkey_toggles_a_visible_window_shut():
    # regression: the "window" hotkey used to only ever (re)show/focus the
    # window, so pressing it again while already focused did nothing
    # visible — it must toggle exactly like a tray click does
    fake, calls = _fake_app(main_window=_FakeWindow(visible=True))
    KortalkApp._hotkey_activated(fake, "window")
    assert fake.main_window.hidden is True
    assert calls == []  # not re-opened, just hidden
