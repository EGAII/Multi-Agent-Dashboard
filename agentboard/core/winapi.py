"""Small ctypes wrappers around the Win32 window functions the app needs."""
import ctypes
import ctypes.wintypes as W

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

GW_OWNER = 4
SW_RESTORE = 9
SW_SHOW = 5
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x2

user32.GetWindowTextLengthW.argtypes = [W.HWND]
user32.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
user32.IsWindowVisible.argtypes = [W.HWND]
user32.IsIconic.argtypes = [W.HWND]
user32.GetWindow.argtypes = [W.HWND, ctypes.c_uint]
user32.GetWindow.restype = W.HWND
user32.ShowWindow.argtypes = [W.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [W.HWND]
user32.BringWindowToTop.argtypes = [W.HWND]
user32.GetForegroundWindow.restype = W.HWND
user32.AttachThreadInput.argtypes = [W.DWORD, W.DWORD, W.BOOL]

_ENUM = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)


def window_text(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    b = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, b, n + 1)
    return b.value


def class_name(hwnd):
    b = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, b, 256)
    return b.value


def window_pid(hwnd):
    pid = W.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def windows_by_pid():
    """pid -> list of (hwnd, title, class) for visible, unowned top-level windows with a title."""
    out = {}

    def cb(h, _):
        if user32.IsWindowVisible(h) and not user32.GetWindow(h, GW_OWNER) and user32.GetWindowTextLengthW(h) > 0:
            out.setdefault(window_pid(h), []).append((h, window_text(h), class_name(h)))
        return True

    user32.EnumWindows(_ENUM(cb), 0)
    return out


def focus(hwnd):
    """Restore and bring a window to the foreground. Returns True if it became the foreground window."""
    if not hwnd:
        return False
    hwnd = W.HWND(hwnd) if isinstance(hwnd, int) else hwnd
    user32.ShowWindow(hwnd, SW_RESTORE if user32.IsIconic(hwnd) else SW_SHOW)
    fg = user32.GetForegroundWindow()
    me = kernel32.GetCurrentThreadId()
    other = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(other and other != me and user32.AttachThreadInput(me, other, True))
    # a tap of Alt lets SetForegroundWindow succeed even when Windows' focus-stealing guard kicks in
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    user32.BringWindowToTop(hwnd)
    ok = bool(user32.SetForegroundWindow(hwnd))
    if attached:
        user32.AttachThreadInput(me, other, False)
    return ok
