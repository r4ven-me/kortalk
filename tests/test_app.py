"""Tests for the applications-menu launcher entry written on daemon start."""

from kortalk.app import ensure_desktop_entry


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
