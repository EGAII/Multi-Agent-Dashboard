"""Ask the agents' own CLIs for the quota they show you: Claude Code's /usage and Codex's rate limits.

AgentBoard runs your installed CLI in the background, with no window:
- Claude Code gets `/usage` the way its Agent SDK sends it. That is a local command: no model request, no tokens,
  no saved session, and your settings, plugins, hooks and MCP servers are skipped (--restricted). Its reply carries
  the plan's usage rows (5-hour, weekly, per-model weekly) when the login allows it.
- Codex's app server answers `account/rateLimits/read`, the figures its /status shows. No thread is started.
Each CLI talks to its own service with your login, as it does when you type /usage or /status; AgentBoard never
sees the login. Every function returns {"ok", "windows", "message", "at"}; windows use official_windows' shape.
"""
import glob
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time

from . import config as cfgmod
from .usage import official_windows, parse_ts

NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW: console CLIs run without flashing a window


def _version(s):
    return tuple(int(x) for x in re.findall(r"\d+", s or "")[:3])


def find_claude():
    """The newest Claude Code: the Claude desktop app keeps its own under %APPDATA%\\Claude\\claude-code\\<version>,
    else `claude` on PATH. Older versions lack the structured /usage report."""
    found = []
    for exe in glob.glob(os.path.join(os.environ.get("APPDATA", ""), "Claude", "claude-code", "*", "*", "claude.exe")):
        found.append((_version(os.path.basename(os.path.dirname(os.path.dirname(exe)))), exe))
    if found:
        return max(found)[1]
    return shutil.which("claude")


def _result(ok, windows=(), message=""):
    return {"ok": ok, "windows": list(windows), "message": message, "at": time.time()}


def _claude_windows(limits):
    """/usage rows -> windows. Classify on `kind`, never on the label (the server's own advice)."""
    out = []
    for r in limits or []:
        if not isinstance(r, dict) or r.get("percent") is None:
            continue
        pct, kind = min(100.0, float(r["percent"])), r.get("kind")
        reset = parse_ts(r["resets_at"]) if r.get("resets_at") else None
        model = (((r.get("scope") or {}).get("model") or {}).get("display_name"))
        if kind == "session":
            out.append({"label": "5h", "pct": pct, "reset": reset, "window_minutes": 300})
        elif kind == "weekly_all":
            out.append({"label": "7d", "pct": pct, "reset": reset, "window_minutes": 10080})
        elif kind == "weekly_scoped" and model:
            out.append({"label": model, "pct": pct, "reset": reset, "window_minutes": 10080, "scoped": True})
    return out


def claude_usage(timeout=60):
    exe = find_claude()
    if not exe:
        return _result(False, message="Claude Code isn't installed")
    line = json.dumps({"type": "user", "message": {"role": "user", "content": "/usage"}}) + "\n"
    base = [exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--strict-mcp-config", "--tools", "", "--no-session-persistence", "--model", "haiku"]
    os.makedirs(cfgmod.APP_DIR, exist_ok=True)  # a quiet working folder with no project settings
    out = ""
    for quiet in (["--restricted"], ["--setting-sources", "project"]):  # older versions lack --restricted
        try:
            r = subprocess.run(base + quiet, input=line, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=timeout, cwd=cfgmod.APP_DIR, creationflags=NO_WINDOW)
        except (OSError, subprocess.SubprocessError) as e:
            return _result(False, message=f"Couldn't run Claude Code: {e}")
        out = r.stdout
        if "unknown option" not in (r.stderr or ""):
            break
    for raw in out.splitlines():
        try:
            o = json.loads(raw)
        except ValueError:
            continue
        if o.get("type") != "assistant" or "usage_report" not in o:
            continue
        rl = (o.get("usage_report") or {}).get("rate_limits")
        if not rl:  # the login can't see the plan's usage (its token lacks the user:profile scope)
            return _result(False, message="Claude Code has no plan usage: sign in again with /login in Claude Code")
        wins = _claude_windows(rl.get("limits"))
        return _result(bool(wins), wins, "" if wins else "Claude Code reported no limits for this plan")
    return _result(False, message="Claude Code gave no usage report (update it, or sign in with a Claude.ai account)")


def codex_rate_limits(timeout=30):
    exe = shutil.which("codex")
    if not exe:
        return _result(False, message="Codex isn't installed")
    try:
        p = subprocess.Popen([exe, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    except OSError as e:
        return _result(False, message=f"Couldn't run Codex: {e}")
    lines = queue.Queue()
    threading.Thread(target=lambda: [lines.put(x) for x in p.stdout], daemon=True).start()

    def send(msg):
        p.stdin.write(json.dumps(msg) + "\n")
        p.stdin.flush()

    def answer(req_id, deadline):
        while time.time() < deadline:
            try:
                o = json.loads(lines.get(timeout=max(0.1, deadline - time.time())))
            except (queue.Empty, ValueError):
                continue
            if o.get("id") == req_id:
                return o
        return None

    try:
        deadline = time.time() + timeout
        send({"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "agentboard", "version": "1"}}})
        if not answer(1, deadline):
            return _result(False, message="Codex didn't answer")
        send({"method": "initialized"})
        send({"id": 2, "method": "account/rateLimits/read"})
        o = answer(2, deadline)
    except OSError as e:
        return _result(False, message=f"Couldn't talk to Codex: {e}")
    finally:
        try:
            p.kill()
        except OSError:
            pass
    if not o or "result" not in o:
        return _result(False, message=((o or {}).get("error") or {}).get("message") or "Codex didn't report its limits")
    res = o["result"]
    snap = (res.get("rateLimitsByLimitId") or {}).get("codex") or res.get("rateLimits") or {}
    wins = official_windows({k: {"used_percent": w.get("usedPercent"), "window_minutes": w.get("windowDurationMins"),
                                 "resets_at": w.get("resetsAt")}
                             for k in ("primary", "secondary") if isinstance(w := snap.get(k), dict)}, time.time())
    plan = snap.get("planType")
    return dict(_result(bool(wins), wins, "" if wins else "Codex reported no limits"), plan=plan)


READERS = {"claude": claude_usage, "codex": codex_rate_limits}  # agent type id -> how to ask it
