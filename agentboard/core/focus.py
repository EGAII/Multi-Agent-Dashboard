"""Jump to the window (and Windows Terminal tab) that hosts an agent."""
import json
import os
import secrets
import subprocess
import sys
import threading
import time

from . import claude_app, winapi

HERE = os.path.dirname(os.path.abspath(__file__))
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW
APP_HOSTS = {"Claude app", "ChatGPT app", "Codex app"}
IDE_HOSTS = {"VS Code", "Cursor", "Windsurf", "Kiro", "JetBrains"}


def _pythonw():
    d = os.path.dirname(sys.executable)
    p = os.path.join(d, "pythonw.exe")
    return p if os.path.exists(p) else sys.executable


class Focuser:
    def __init__(self):
        self._ps = None
        self._lock = threading.Lock()
        self._presses = {}  # agent key -> click count, to cycle through tabs with identical titles

    # ---------------------------------------------------------------- helpers
    def probe(self, pids):
        """Console title + console window for each pid (see console_probe.py)."""
        if not pids:
            return {}
        try:
            r = subprocess.run([_pythonw(), os.path.join(HERE, "console_probe.py"), *map(str, pids)],
                               capture_output=True, timeout=6, creationflags=NO_WINDOW)
            return json.loads(r.stdout.decode("utf-8", "ignore") or "{}")
        except (subprocess.SubprocessError, ValueError, OSError):
            return {}

    def _wt(self, req):
        with self._lock:
            for _ in range(2):
                if self._ps is None or self._ps.poll() is not None:
                    self._ps = subprocess.Popen(
                        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                         "-File", os.path.join(HERE, "wt_tabs.ps1")],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
                try:
                    self._ps.stdin.write((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
                    self._ps.stdin.flush()
                    line = self._ps.stdout.readline().decode("utf-8-sig", "ignore")
                    return json.loads(line) if line.strip() else {"ok": False, "error": "no answer"}
                except (OSError, ValueError) as e:
                    self._ps = None
                    err = str(e)
            return {"ok": False, "error": err}

    def _select_marked(self, pid):
        """Give the agent's console a unique title for a moment, select the tab showing it, restore the title."""
        marker = f"AgentBoard {pid} {secrets.token_hex(3)}"
        try:
            helper = subprocess.Popen([_pythonw(), os.path.join(HERE, "console_probe.py"), "--mark", str(pid), marker],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, creationflags=NO_WINDOW)
        except OSError:
            return {"ok": False}
        r = {"ok": False}
        try:
            first = json.loads(helper.stdout.readline().decode("utf-8", "ignore") or "{}")
            if first.get("ok"):
                for wait in (0.06, 0.12, 0.25, 0.4):  # give the terminal a moment to show the new title
                    time.sleep(wait)
                    r = self._wt({"op": "select", "titles": [marker], "nth": 0, "exact": True})
                    if r.get("ok"):
                        r["exact"] = True
                        break
        except (OSError, ValueError):
            pass
        finally:
            try:
                helper.stdin.write(b"restore\n")
                helper.stdin.flush()
                helper.wait(timeout=3)
            except (OSError, subprocess.SubprocessError):
                helper.kill()
        return r

    def warm_up(self):
        """Start the PowerShell helper early so the first click is fast."""
        threading.Thread(target=lambda: self._wt({"op": "list"}), daemon=True).start()

    def list_tabs(self):
        return self._wt({"op": "list"})

    def close(self):
        if self._ps and self._ps.poll() is None:
            self._ps.kill()

    # ---------------------------------------------------------------- main entry
    def focus_agent(self, ag):
        """Returns a short message describing what happened."""
        wins = winapi.windows_by_pid()
        folder = os.path.basename(ag.cwd.rstrip("\\/"))
        app_session = ag.extra.get("app_session")
        if ag.host == "Claude app" and app_session:
            claude_app.open_session(app_session)  # the app switches to exactly this session
            time.sleep(0.3)
            cands = wins.get(ag.host_pid, [])
            if cands:
                winapi.focus(cands[0][0])
            return f"Opened “{ag.name}” in the Claude app"
        if ag.host in APP_HOSTS or ag.host in IDE_HOSTS:
            cands = wins.get(ag.host_pid, [])
            if ag.host in IDE_HOSTS and folder:
                cands = sorted(cands, key=lambda w: folder.lower() not in w[1].lower())
            if cands and winapi.focus(cands[0][0]):
                where = "its window" if ag.host in IDE_HOSTS else "the app"
                return f"Opened {ag.host} ({where}). Select the session there: {ag.name}"
            return f"Could not find the {ag.host} window"

        info = self.probe([ag.pid]).get(str(ag.pid), {})
        title = info.get("title", "")
        is_wt = ag.host == "Windows Terminal" or _is_wt_pid(info.get("root_pid"))
        if is_wt:
            r = self._select_marked(ag.pid)  # exact: works even when several tabs share a title
            if not r.get("ok"):           # e.g. the profile suppresses app titles: fall back to matching the title
                n = self._presses[ag.key] = self._presses.get(ag.key, -1) + 1
                r = self._wt({"op": "select", "titles": [title, ag.name, folder], "nth": n})
            hwnd = r.get("hwnd") if r.get("ok") else None
            if not hwnd:  # fall back to the terminal window itself
                for pid, ws in wins.items():
                    if pid == ag.host_pid or _is_wt_pid(pid):
                        hwnd = ws[0][0]
                        break
            if hwnd and winapi.focus(int(hwnd)):
                if r.get("exact"):
                    return f"Switched to its tab “{title or ag.name}”"
                if r.get("ok"):
                    more = f" (1 of {r['matches']} tabs with this title, click again for the next)" if r.get("matches", 1) > 1 else ""
                    return f"Switched to tab “{r.get('name', title)}”{more}"
                return "Opened Windows Terminal, but could not tell which tab (title: " + (title or "unknown") + ")"
            return "Could not find the Windows Terminal window"

        hwnd = info.get("root_hwnd") if info.get("root_visible") else info.get("hwnd")
        if hwnd and winapi.focus(int(hwnd)):
            return f"Switched to console “{title or ag.name}”"
        return "Could not find a window for this agent (it may run in the background)"


def _is_wt_pid(pid):
    if not pid:
        return False
    try:
        import psutil
        return psutil.Process(int(pid)).name().lower() == "windowsterminal.exe"
    except Exception:
        return False
