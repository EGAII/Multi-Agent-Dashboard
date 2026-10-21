"""Helper run in its own console-less process.

  pythonw console_probe.py <pid> [<pid> ...]
      For each pid: attach to its console and report the console title (what a terminal tab shows)
      and the console window handle. Prints one JSON object.

  pythonw console_probe.py --mark <pid> <marker>
      Give the pid's console a unique temporary title so its exact terminal tab can be found,
      print {"ok": true, "old": <previous title>}, keep the marker in place (agents that animate
      their title would paint over it) until a line arrives on stdin or 3 s pass, then put the
      agent's latest title back.
"""
import ctypes
import ctypes.wintypes as W
import json
import sys
import threading
import time

k32 = ctypes.windll.kernel32
u32 = ctypes.windll.user32
k32.GetConsoleWindow.restype = W.HWND
u32.GetAncestor.restype = W.HWND
u32.GetAncestor.argtypes = [W.HWND, ctypes.c_uint]
GA_ROOTOWNER = 3


def _title():
    b = ctypes.create_unicode_buffer(1024)
    k32.GetConsoleTitleW(b, 1024)
    return b.value


def probe(pid):
    k32.FreeConsole()
    if not k32.AttachConsole(pid):
        return None
    try:
        hwnd = k32.GetConsoleWindow()
        root = u32.GetAncestor(hwnd, GA_ROOTOWNER) if hwnd else None
        owner_pid = W.DWORD()
        if root:
            u32.GetWindowThreadProcessId(root, ctypes.byref(owner_pid))
        return {"title": _title(), "hwnd": int(hwnd or 0), "root_hwnd": int(root or 0), "root_pid": owner_pid.value,
                "root_visible": bool(root and u32.IsWindowVisible(root))}
    finally:
        k32.FreeConsole()


def mark(pid, marker, hold=3.0):
    k32.FreeConsole()
    if not k32.AttachConsole(pid):
        sys.stdout.write(json.dumps({"ok": False}) + "\n")
        sys.stdout.flush()
        return
    # stdout is the parent's pipe, not the console, so staying attached while we report is safe
    old = _title()
    k32.SetConsoleTitleW(marker)
    done = threading.Event()
    threading.Thread(target=lambda: (sys.stdin.readline(), done.set()), daemon=True).start()
    sys.stdout.write(json.dumps({"ok": True, "old": old}) + "\n")
    sys.stdout.flush()
    deadline = time.time() + hold  # never keep the marker longer than this, even if the parent goes away
    while not done.is_set() and time.time() < deadline:
        current = _title()
        if current != marker:  # an agent animating its title (spinners) painted over the marker: put it back
            old = current
            k32.SetConsoleTitleW(marker)
        done.wait(0.01)
    if _title() == marker:
        k32.SetConsoleTitleW(old)
    k32.FreeConsole()


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--mark":
        mark(int(sys.argv[2]), sys.argv[3])
    else:
        out = {}
        for a in sys.argv[1:]:
            try:
                r = probe(int(a))
            except Exception as e:  # keep probing the remaining pids
                r = {"error": str(e)}
            if r:
                out[a] = r
        sys.stdout.write(json.dumps(out))
