"""Tests for hotkeys.py.

parse_sequence/to_portal_trigger are pure string logic and tested directly.
The two actual backends (_X11HotkeyThread needs a live X11 display,
_PortalHotkeys needs a session bus with the GlobalShortcuts portal) are out
of reach in a headless test run — GlobalHotkeys' own dispatch/fallback
logic is instead verified with both backends faked out.
"""

from __future__ import annotations

import ctypes
from unittest.mock import MagicMock

import pytest
from PySide6.QtGui import QGuiApplication

import kortalk.hotkeys as hotkeys_mod
from kortalk.hotkeys import X11_MODMASK, GlobalHotkeys, parse_sequence, to_portal_trigger

# -- parse_sequence -------------------------------------------------------------

def test_parse_sequence_ctrl_alt_letter():
    mods, keysym = parse_sequence("Ctrl+Alt+C")
    assert keysym == "c"
    assert mods == X11_MODMASK["ctrl"] | X11_MODMASK["alt"]


def test_parse_sequence_named_key():
    mods, keysym = parse_sequence("Ctrl+Escape")
    assert keysym == "Escape"
    assert mods == X11_MODMASK["ctrl"]


def test_parse_sequence_function_key():
    mods, keysym = parse_sequence("Shift+F5")
    assert keysym == "F5"
    assert mods == X11_MODMASK["shift"]


def test_parse_sequence_single_key_no_modifier():
    mods, keysym = parse_sequence("F5")
    assert mods == 0
    assert keysym == "F5"


def test_parse_sequence_unknown_modifier_returns_none():
    assert parse_sequence("Foo+C") is None


@pytest.mark.parametrize("sequence", ["", "   "])
def test_parse_sequence_blank_returns_none(sequence):
    assert parse_sequence(sequence) is None


@pytest.mark.parametrize("key,keysym", [
    ("`", "grave"), ("-", "minus"), ("=", "equal"),
    ("[", "bracketleft"), ("]", "bracketright"),
    (";", "semicolon"), ("'", "apostrophe"),
    (",", "comma"), (".", "period"), ("/", "slash"), ("\\", "backslash"),
])
def test_parse_sequence_maps_punctuation_to_its_x11_keysym_name(key, keysym):
    # Regression: a punctuation key's raw character is NoSymbol to
    # XStringToKeysym (unlike a letter/digit, whose keysym value IS its
    # ASCII code) — a hotkey ending in one of these (e.g. the default
    # window hotkey rebound to "Alt+`") silently failed to grab, with no
    # visible error beyond a log line, because the fallback in
    # parse_sequence only produces a resolvable name for single letters.
    mods, resolved = parse_sequence(f"Alt+{key}")
    assert resolved == keysym
    assert mods == X11_MODMASK["alt"]


def test_all_named_keysyms_are_real_x11_keysym_names():
    # Guards the whole _KEYSYM_NAMES table against a typo'd/wrong keysym
    # name — exactly the failure mode fixed above, generalized to every
    # entry instead of just the punctuation added for it.
    try:
        xlib = ctypes.CDLL("libX11.so.6")
    except OSError:
        pytest.skip("libX11 not available")
    xlib.XStringToKeysym.argtypes = [ctypes.c_char_p]
    xlib.XStringToKeysym.restype = ctypes.c_ulong
    for qt_name, keysym_name in hotkeys_mod._KEYSYM_NAMES.items():
        assert xlib.XStringToKeysym(keysym_name.encode()) != 0, \
            f"{qt_name!r} -> {keysym_name!r} is not a real X11 keysym"


# -- to_portal_trigger ------------------------------------------------------------

def test_to_portal_trigger_uppercases_modifiers_lowercases_letter():
    assert to_portal_trigger("Ctrl+Alt+C") == "CTRL+ALT+c"


def test_to_portal_trigger_keeps_named_key_as_is():
    assert to_portal_trigger("Ctrl+Escape") == "CTRL+Escape"


def test_to_portal_trigger_empty_string():
    assert to_portal_trigger("") == ""


# -- GlobalHotkeys facade ---------------------------------------------------------

def test_apply_with_no_bindings_stays_none():
    hotkeys = GlobalHotkeys()
    hotkeys.apply({"window": "", "prompt:Explain": ""})
    assert hotkeys.backend == "none"


def test_apply_falls_back_to_none_when_portal_unavailable(monkeypatch):
    monkeypatch.setattr(QGuiApplication, "platformName", lambda: "wayland")

    class _FailingPortal:
        def __init__(self, *a, **kw):
            raise RuntimeError("GlobalShortcuts portal is unavailable")

    monkeypatch.setattr(hotkeys_mod, "_PortalHotkeys", _FailingPortal)

    hotkeys = GlobalHotkeys()
    hotkeys.apply({"window": "Ctrl+Alt+W"})
    assert hotkeys.backend == "none"
    assert "unavailable" in hotkeys.error


def test_apply_uses_portal_backend_when_available(monkeypatch):
    monkeypatch.setattr(QGuiApplication, "platformName", lambda: "wayland")

    fake_portal = MagicMock()
    fake_portal_cls = MagicMock(return_value=fake_portal)
    monkeypatch.setattr(hotkeys_mod, "_PortalHotkeys", fake_portal_cls)

    hotkeys = GlobalHotkeys()
    hotkeys.apply({"window": "Ctrl+Alt+W"})

    assert hotkeys.backend == "portal"
    fake_portal_cls.assert_called_once_with({"window": "Ctrl+Alt+W"}, hotkeys)
    fake_portal.activated.connect.assert_called_once()


def test_apply_starts_x11_backend_and_parses_bindings(monkeypatch):
    monkeypatch.setattr(QGuiApplication, "platformName", lambda: "xcb")

    fake_thread = MagicMock()
    fake_thread_cls = MagicMock(return_value=fake_thread)
    monkeypatch.setattr(hotkeys_mod, "_X11HotkeyThread", fake_thread_cls)

    hotkeys = GlobalHotkeys()
    hotkeys.apply({"window": "Ctrl+Alt+W"})

    assert hotkeys.backend == "x11"
    fake_thread.start.assert_called_once()
    parsed = fake_thread_cls.call_args[0][0]
    assert parsed == [(X11_MODMASK["ctrl"] | X11_MODMASK["alt"], "w", "window")]


def test_apply_x11_backend_skips_unparseable_sequences(monkeypatch):
    monkeypatch.setattr(QGuiApplication, "platformName", lambda: "xcb")

    fake_thread = MagicMock()
    fake_thread_cls = MagicMock(return_value=fake_thread)
    monkeypatch.setattr(hotkeys_mod, "_X11HotkeyThread", fake_thread_cls)

    hotkeys = GlobalHotkeys()
    hotkeys.apply({"window": "Foo+W"})  # "Foo" isn't a known modifier

    assert hotkeys.error == "failed to parse the key sequences"
    fake_thread_cls.assert_not_called()


def test_stop_stops_x11_thread_and_resets_backend(monkeypatch):
    monkeypatch.setattr(QGuiApplication, "platformName", lambda: "xcb")
    fake_thread = MagicMock()
    monkeypatch.setattr(hotkeys_mod, "_X11HotkeyThread", MagicMock(return_value=fake_thread))

    hotkeys = GlobalHotkeys()
    hotkeys.apply({"window": "Ctrl+Alt+W"})
    hotkeys.stop()

    fake_thread.stop.assert_called_once()
    assert hotkeys.backend == "none"
    assert hotkeys.error == ""


def test_apply_replaces_previous_backend(monkeypatch):
    monkeypatch.setattr(QGuiApplication, "platformName", lambda: "xcb")
    first_thread = MagicMock()
    second_thread = MagicMock()
    fake_thread_cls = MagicMock(side_effect=[first_thread, second_thread])
    monkeypatch.setattr(hotkeys_mod, "_X11HotkeyThread", fake_thread_cls)

    hotkeys = GlobalHotkeys()
    hotkeys.apply({"window": "Ctrl+Alt+W"})
    hotkeys.apply({"window": "Ctrl+Alt+Q"})

    first_thread.stop.assert_called_once()  # the old backend is torn down first
    second_thread.start.assert_called_once()
    assert hotkeys.backend == "x11"
