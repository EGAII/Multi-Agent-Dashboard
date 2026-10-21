"""Claude desktop app sessions.

The app keeps one record per Code session in %APPDATA%\\Claude\\claude-code-sessions\\<account>\\<org>\\local_<id>.json.
Each record links the app's session id ("local_<id>", what its claude://code/continue deep link takes) to the
Claude Code CLI session it runs. Only the ids, title, archive flag and last activity time are read.
"""
import glob
import json
import os
import re

ROOT = os.path.join(os.environ.get("APPDATA", ""), "Claude", "claude-code-sessions")
LOCAL_ID = re.compile(r"^local_[A-Za-z0-9-]{1,64}$")  # the same check the app applies to the deep link
_cache = {}  # path -> (mtime, record or None)


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    sid = d.get("sessionId") or ""
    if not LOCAL_ID.match(sid) or not d.get("cliSessionId"):
        return None
    return {"id": sid, "cli": d["cliSessionId"], "title": d.get("title") or "",
            "archived": bool(d.get("isArchived")), "last": (d.get("lastActivityAt") or 0) / 1000}


def sessions():
    """-> {cli session id: {"id": "local_...", "title", "archived", "last"}}; files are re-read only when they change."""
    out, seen = {}, set()
    for path in glob.glob(os.path.join(ROOT, "*", "*", "local_*.json")):
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        seen.add(path)
        hit = _cache.get(path)
        if hit is None or hit[0] != mtime:
            hit = _cache[path] = (mtime, _read(path))
        rec = hit[1]
        if rec and (rec["cli"] not in out or rec["last"] > out[rec["cli"]]["last"]):
            out[rec["cli"]] = rec
    for path in set(_cache) - seen:
        del _cache[path]
    return out


def open_session(local_id):
    """Ask the running Claude app to show this session (the app's own deep link)."""
    if not LOCAL_ID.match(local_id or ""):
        raise ValueError("not a Claude app session id")
    os.startfile(f"claude://code/continue?session={local_id}")
