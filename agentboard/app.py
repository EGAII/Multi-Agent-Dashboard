"""The AgentBoard window: a frameless WebView2 panel (pywebview) showing agentboard/web, backed by core."""
import ctypes
import ctypes.wintypes as W
import datetime as dt
import os
import winreg

import webview
from webview.window import FixPoint

from .core import config as cfgmod
from .core.demo import DemoEngine
from .core.engine import Engine
from .core.focus import Focuser

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "index.html")
WIDTH, HEIGHT = 720, 300  # landscape; the page then sets the width for its layout and fits the height
MARGIN = 16


def _light_theme():
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        return bool(winreg.QueryValueEx(k, "AppsUseLightTheme")[0])
    except OSError:
        return True


def _work_area(hwnd=None):
    """Desktop area without the taskbar, in physical pixels: (left, top, right, bottom).
    With a window handle, it is the area of the monitor that window is on."""
    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", W.DWORD), ("rcMonitor", W.RECT), ("rcWork", W.RECT), ("dwFlags", W.DWORD)]
    u32 = ctypes.windll.user32
    if hwnd:
        u32.MonitorFromWindow.restype = W.HMONITOR
        mon = u32.MonitorFromWindow(W.HWND(hwnd), 2)  # MONITOR_DEFAULTTONEAREST
        mi = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        if mon and u32.GetMonitorInfoW(mon, ctypes.byref(mi)):
            r = mi.rcWork
            return r.left, r.top, r.right, r.bottom
    r = W.RECT()
    u32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
    return r.left, r.top, r.right, r.bottom


class Api:
    """Methods callable from the page as window.pywebview.api.<name>(). Internals are underscored so they stay private."""

    def __init__(self, cfg, demo=False):
        self._cfg = cfg
        self._engine = DemoEngine() if demo else Engine(cfg)
        self._focuser = Focuser()
        self._snap = {"ready": False}
        self._window = None
        self._pinned = bool(cfg.get("always_on_top", True))
        self._restore = None  # (x, y, width, height) while maximized

    def _start(self):
        self._focuser.warm_up()
        self._engine.run(self._on_update)

    def _on_update(self, snap):
        self._snap = snap

    def _on_closed(self):
        self._engine.stop()
        self._focuser.close()

    def _area(self):
        """Work area of the window's monitor, in the logical pixels pywebview uses."""
        native = self._window.native
        scale = getattr(native, "_scale", 1) or 1
        try:
            hwnd = int(native.Handle.ToInt64())
        except Exception:
            hwnd = None
        return [v / scale for v in _work_area(hwnd)]

    # ------------------------------------------------------------ called from JavaScript
    def snapshot(self):
        return self._snap

    def state(self):
        return {"pinned": self._pinned, "maximized": self._restore is not None}

    def read_quota(self, type_id):
        """Read an agent type's quota now: ask its CLI where there is a way (Claude Code's /usage, Codex's rate
        limits; see core/quota_read.py), re-read its logs, and say what there is."""
        name = next((a["name"] for a in self._cfg["agents"] if a["id"] == type_id), type_id)
        if getattr(self._engine, "demo", False):
            return {"ok": True, "found": False, "message": f"Demo: this would ask {name} for its quota"}
        try:
            r, wins, hits = self._engine.read_quota(type_id)
            self._snap = self._engine.snapshot()
        except Exception as e:  # report instead of breaking the page
            return {"ok": False, "found": False, "message": f"Could not read the quota: {e!r}"}
        if wins:
            return {"ok": True, "found": True, "message": f"{name}: " + " · ".join(f"{w['label']} {w['pct']:.0f}%" for w in wins)}
        if hits:
            key, h = next(iter(hits.items()))
            when = dt.datetime.fromtimestamp(h["last"]).strftime("%b %d").replace(" 0", " ")
            note = f"{r['message']}. " if r and r.get("message") else ""
            return {"ok": True, "found": True, "message": f"{note}{key} estimated from your limit hit on {when}"}
        if r:
            return {"ok": False, "found": False, "message": r["message"] or f"{name} reported no quota"}
        return {"ok": False, "found": False, "message": f"{name} doesn't share its quota; pick a plan to estimate it"}

    def jump(self, key):
        if getattr(self._engine, "demo", False):
            name = next((a["name"] for a in self._snap.get("agents", []) if a["key"] == key), "this agent")
            return {"ok": True, "message": f"Demo: this would switch to “{name}”"}
        ag = self._engine.agent(key)
        if not ag:
            return {"ok": False, "message": "That agent has just stopped."}
        try:
            msg = self._focuser.focus_agent(ag)
        except Exception as e:  # report instead of breaking the page
            return {"ok": False, "message": f"Could not switch: {e!r}"}
        return {"ok": not msg.startswith("Could not"), "message": msg}

    def fit(self, content_h, inner_h):
        """Make the window as tall as the page content; its width stays fixed. A window in the lower half of
        the screen grows upwards, so it never runs under the taskbar. Does nothing while maximized."""
        if self._restore is not None:
            return None
        w = self._window
        l, t, r, b = self._area()
        height, y = w.height, w.y
        try:  # window frame around the page, from the form itself (the page's own size may lag a resize)
            native = w.native
            chrome = (native.Size.Height - native.ClientSize.Height) / (getattr(native, "_scale", 1) or 1)
        except Exception:
            chrome = height - inner_h
        target = int(min(max(content_h + chrome, 200), b - t - 2 * MARGIN))
        if abs(target - height) < 4:
            return height
        low = y + height / 2 > (t + b) / 2
        w.resize(w.width, target, fix_point=(FixPoint.SOUTH if low else FixPoint.NORTH) | FixPoint.WEST)
        top = y + height - target if low else y
        if top < t + MARGIN or top + target > b - MARGIN:  # keep it on screen
            w.move(w.x, int(min(max(top, t + MARGIN), b - MARGIN - target)))
        return target

    def set_width(self, content_w):
        """Make the page area this wide (single-column mode is narrow). A window on the right half of the
        screen keeps its right edge, so it stays docked where it was. Does nothing while maximized."""
        if self._restore is not None:
            return None
        w = self._window
        try:
            native = w.native
            chrome = (native.Size.Width - native.ClientSize.Width) / (getattr(native, "_scale", 1) or 1)
        except Exception:
            chrome = 0
        target = int(content_w + chrome)
        if abs(target - w.width) < 3:
            return w.width
        l, t, r, b = self._area()
        right = w.x + w.width / 2 > (l + r) / 2
        w.resize(target, w.height, fix_point=(FixPoint.EAST if right else FixPoint.WEST) | FixPoint.NORTH)
        return target

    def toggle_maximize(self):
        """Fill the monitor's work area (not over the taskbar), or go back to the panel size and place."""
        w = self._window
        if self._restore is None:
            self._restore = (w.x, w.y, w.width, w.height)
            l, t, r, b = self._area()
            w.move(int(l), int(t))
            w.resize(int(r - l), int(b - t))
            return True
        x, y, width, height = self._restore
        self._restore = None
        w.resize(width, height)
        w.move(x, y)
        return False

    def toggle_pin(self):
        self._pinned = not self._pinned
        self._window.on_top = self._pinned
        return self._pinned

    def open_settings(self):
        path = cfgmod.write_example()
        try:
            os.startfile(path)
        except OSError as e:
            return f"Settings file: {path} ({e})"
        return "Opened settings. Restart AgentBoard after saving."

    def minimize(self):
        self._window.minimize()

    def close(self):
        self._window.destroy()  # the closed event stops the engine and helpers


def create(demo=False, x=None, y=None):
    """Create the window (not yet shown). Returns the Api; call webview.start(api._start, ...) to run."""
    cfg = cfgmod.load()
    api = Api(cfg, demo=demo)
    if x is None:
        try:
            s = ctypes.windll.user32.GetDpiForSystem() / 96
        except (AttributeError, OSError):
            s = 1
        l, t, r, b = (v / s for v in _work_area())
        x, y = int(max(0, r - WIDTH - MARGIN)), int(max(0, b - HEIGHT - MARGIN))
    # Only the bare title bar drags the window, so its buttons stay clickable.
    webview.settings["DRAG_REGION_DIRECT_TARGET_ONLY"] = True
    api._window = webview.create_window(
        "AgentBoard", url=WEB, js_api=api, width=WIDTH, height=HEIGHT, x=x, y=y,
        frameless=True, easy_drag=False, resizable=False, on_top=api._pinned,
        background_color="#f4f4f5" if _light_theme() else "#0e0e10")
    api._window.events.closed += api._on_closed
    return api


def main(demo=False):
    api = create(demo)
    webview.start(api._start, http_server=True, private_mode=False)
