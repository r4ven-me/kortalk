"""Global hotkeys inside the application, no external tools.

X11: XGrabKey via ctypes (libX11) + a dedicated X event reading thread.
Wayland: XDG Desktop Portal (org.freedesktop.portal.GlobalShortcuts) via
QtDBus — the compositor itself shows the binding confirmation dialog.

If no backend is available the application keeps working — the popup is
opened from the tray or with `kortalk --popup`.
"""

from __future__ import annotations

import ctypes
import logging
import os

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtGui import QGuiApplication

log = logging.getLogger(__name__)

# -- parsing strings like "Ctrl+Alt+C" ----------------------------------------

X11_MODMASK = {"ctrl": 1 << 2, "shift": 1 << 0, "alt": 1 << 3, "meta": 1 << 6}
_NUMLOCK, _CAPSLOCK = 1 << 4, 1 << 1  # Mod2Mask, LockMask
_RELEVANT_MODS = sum(X11_MODMASK.values())

# Qt key names -> X11 keysym names (XStringToKeysym)
_KEYSYM_NAMES = {
    "space": "space", "return": "Return", "enter": "Return", "tab": "Tab",
    "esc": "Escape", "escape": "Escape", "backspace": "BackSpace",
    "ins": "Insert", "del": "Delete", "home": "Home", "end": "End",
    "pgup": "Prior", "pgdown": "Next", "print": "Print", "pause": "Pause",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    **{f"f{i}": f"F{i}" for i in range(1, 25)},
}


def parse_sequence(sequence: str) -> tuple[int, str] | None:
    """'Ctrl+Alt+C' -> (X11 modifier mask, keysym name). None — unparseable."""
    parts = [p.strip() for p in sequence.split("+") if p.strip()]
    if not parts:
        return None
    mods = 0
    for part in parts[:-1]:
        mask = X11_MODMASK.get(part.lower())
        if mask is None:
            return None
        mods |= mask
    key = parts[-1]
    keysym_name = _KEYSYM_NAMES.get(key.lower(), key.lower() if len(key) == 1 else key)
    return mods, keysym_name


def to_portal_trigger(sequence: str) -> str:
    """'Ctrl+Alt+C' -> 'CTRL+ALT+c' (the portal's preferred_trigger format)."""
    parts = [p.strip() for p in sequence.split("+") if p.strip()]
    if not parts:
        return ""
    mods = [p.upper() for p in parts[:-1]]
    key = parts[-1]
    return "+".join(mods + [key.lower() if len(key) == 1 else key])


# -- X11 backend --------------------------------------------------------------

class _X11HotkeyThread(QThread):
    """A separate Xlib connection: grab keys on the root window and read
    events. Qt talks over its own xcb connection, so our Xlib error handler
    and event loop do not interfere with it."""

    activated = Signal(str)

    def __init__(self, bindings: list[tuple[int, str, str]]):
        # bindings: (modifier mask, keysym name, action)
        super().__init__()
        self._bindings = bindings
        self._stop = False
        self.grabbed: list[str] = []   # actions that were bound successfully
        self.failed: list[str] = []

    def stop(self) -> None:
        self._stop = True
        self.wait(2000)

    def run(self) -> None:  # noqa: C901
        try:
            xlib = ctypes.CDLL("libX11.so.6")
        except OSError:
            self.failed = [action for _m, _k, action in self._bindings]
            return

        xlib.XOpenDisplay.restype = ctypes.c_void_p
        xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        xlib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        xlib.XDefaultRootWindow.restype = ctypes.c_ulong
        xlib.XStringToKeysym.argtypes = [ctypes.c_char_p]
        xlib.XStringToKeysym.restype = ctypes.c_ulong
        xlib.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        xlib.XKeysymToKeycode.restype = ctypes.c_ubyte
        xlib.XGrabKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint,
                                  ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        xlib.XUngrabKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_ulong]
        xlib.XPending.argtypes = [ctypes.c_void_p]
        xlib.XPending.restype = ctypes.c_int
        xlib.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]

        # A combination taken by another client yields BadAccess; the default
        # Xlib handler kills the process — install a silent one (Xlib only,
        # Qt lives on xcb and is not affected).
        handler_t = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
        silent = handler_t(lambda *_a: 0)
        xlib.XSetErrorHandler.argtypes = [ctypes.c_void_p]
        xlib.XSetErrorHandler(ctypes.cast(silent, ctypes.c_void_p))

        display = xlib.XOpenDisplay(None)
        if not display:
            self.failed = [action for _m, _k, action in self._bindings]
            return
        root = xlib.XDefaultRootWindow(display)

        class XKeyEvent(ctypes.Structure):
            _fields_ = [
                ("type", ctypes.c_int), ("serial", ctypes.c_ulong),
                ("send_event", ctypes.c_int), ("display", ctypes.c_void_p),
                ("window", ctypes.c_ulong), ("root", ctypes.c_ulong),
                ("subwindow", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("x", ctypes.c_int), ("y", ctypes.c_int),
                ("x_root", ctypes.c_int), ("y_root", ctypes.c_int),
                ("state", ctypes.c_uint), ("keycode", ctypes.c_uint),
                ("same_screen", ctypes.c_int),
            ]

        class XEvent(ctypes.Union):
            _fields_ = [("type", ctypes.c_int), ("xkey", XKeyEvent),
                        ("pad", ctypes.c_long * 24)]

        xlib.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.POINTER(XEvent)]

        GRAB_MODE_ASYNC, KEY_PRESS = 1, 2
        registered: dict[tuple[int, int], str] = {}  # (keycode, mods) -> action
        for mods, keysym_name, action in self._bindings:
            keysym = xlib.XStringToKeysym(keysym_name.encode())
            keycode = xlib.XKeysymToKeycode(display, keysym) if keysym else 0
            if not keycode:
                self.failed.append(action)
                continue
            # grab all NumLock/CapsLock combinations
            for extra in (0, _NUMLOCK, _CAPSLOCK, _NUMLOCK | _CAPSLOCK):
                xlib.XGrabKey(display, keycode, mods | extra, root,
                              1, GRAB_MODE_ASYNC, GRAB_MODE_ASYNC)
            registered[(keycode, mods)] = action
            self.grabbed.append(action)
        xlib.XSync(display, 0)
        log.info("X11 hotkeys: grabbed %s%s", self.grabbed,
                 f", failed {self.failed}" if self.failed else "")

        event = XEvent()
        while not self._stop:
            while xlib.XPending(display):
                xlib.XNextEvent(display, ctypes.byref(event))
                if event.type == KEY_PRESS:
                    key = (event.xkey.keycode, event.xkey.state & _RELEVANT_MODS)
                    action = registered.get(key)
                    if action:
                        self.activated.emit(action)
            self.msleep(30)

        for (keycode, mods), _action in registered.items():
            for extra in (0, _NUMLOCK, _CAPSLOCK, _NUMLOCK | _CAPSLOCK):
                xlib.XUngrabKey(display, keycode, mods | extra, root)
        xlib.XSync(display, 0)
        xlib.XCloseDisplay(display)


# -- Wayland backend (XDG Desktop Portal) -------------------------------------

_PORTAL_SERVICE = "org.freedesktop.portal.Desktop"
_PORTAL_PATH = "/org/freedesktop/portal/desktop"
_PORTAL_IFACE = "org.freedesktop.portal.GlobalShortcuts"


class _PortalHotkeys(QObject):
    activated = Signal(str)

    def __init__(self, bindings: dict[str, str], parent=None):
        # bindings: action -> "Ctrl+Alt+C"
        super().__init__(parent)
        from PySide6.QtDBus import QDBusConnection, QDBusInterface

        self._bindings = bindings
        self._bus = QDBusConnection.sessionBus()
        self._iface = QDBusInterface(_PORTAL_SERVICE, _PORTAL_PATH, _PORTAL_IFACE, self._bus)
        if not self._iface.isValid():
            raise RuntimeError("GlobalShortcuts portal is unavailable")

        token = f"kortalk{os.getpid()}"
        sender = self._bus.baseService()[1:].replace(".", "_")
        request_path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        self._bus.connect(_PORTAL_SERVICE, request_path, "org.freedesktop.portal.Request",
                          "Response", self._on_session_created)
        reply = self._iface.call("CreateSession", {
            "handle_token": token,
            "session_handle_token": f"kortalk_{os.getpid()}",
        })
        if reply.errorName():
            raise RuntimeError(f"CreateSession: {reply.errorMessage()}")

    @Slot("uint", "QVariantMap")
    def _on_session_created(self, code: int, results: dict) -> None:
        from PySide6.QtDBus import QDBusObjectPath

        if code != 0:
            return
        session = results.get("session_handle", "")
        shortcuts = [
            [action, {"description": f"kortalk: {action}",
                      "preferred_trigger": to_portal_trigger(seq)}]
            for action, seq in self._bindings.items() if seq
        ]
        reply = self._iface.call("BindShortcuts", QDBusObjectPath(session), shortcuts, "",
                                 {"handle_token": f"kortalk_bind{os.getpid()}"})
        if reply.errorName():
            log.warning("portal BindShortcuts: %s", reply.errorMessage())
        else:
            log.info("portal GlobalShortcuts: requested bindings %s",
                     [a for a, _s in self._bindings.items()])
        self._bus.connect(_PORTAL_SERVICE, _PORTAL_PATH, _PORTAL_IFACE,
                          "Activated", self._on_activated)

    @Slot("QDBusObjectPath", "QString", "qulonglong", "QVariantMap")
    def _on_activated(self, _session, shortcut_id: str, _ts, _opts) -> None:
        self.activated.emit(shortcut_id)


# -- facade -------------------------------------------------------------------

class GlobalHotkeys(QObject):
    """Registers global hotkeys in whatever way suits the session."""

    activated = Signal(str)  # action: "popup" | "window" | "prompt:<name>"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._x11: _X11HotkeyThread | None = None
        self._portal: _PortalHotkeys | None = None
        self.backend = "none"
        self.error = ""

    def apply(self, bindings: dict[str, str]) -> None:
        """bindings: action -> 'Ctrl+Alt+C' (empty string = unassigned)."""
        self.stop()
        bindings = {a: s for a, s in bindings.items() if s}
        if not bindings:
            return

        platform = QGuiApplication.platformName()
        if platform == "xcb":
            parsed = []
            for action, seq in bindings.items():
                result = parse_sequence(seq)
                if result:
                    parsed.append((result[0], result[1], action))
            if not parsed:
                self.error = "failed to parse the key sequences"
                return
            self._x11 = _X11HotkeyThread(parsed)
            self._x11.activated.connect(self.activated)
            self._x11.start()
            self.backend = "x11"
        else:
            try:
                self._portal = _PortalHotkeys(bindings, self)
                self._portal.activated.connect(self.activated)
                self.backend = "portal"
            except Exception as exc:  # noqa: BLE001 — hotkeys must not kill the app
                self.backend = "none"
                self.error = str(exc)
                log.warning("hotkeys unavailable: %s", exc)

    def stop(self) -> None:
        if self._x11 is not None:
            self._x11.stop()
            self._x11 = None
        self._portal = None
        self.backend = "none"
        self.error = ""
