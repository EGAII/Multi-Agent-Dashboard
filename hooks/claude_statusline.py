"""Optional Claude Code status line for AgentBoard.

Claude Code pipes session data (model, context, and for subscribers the 5-hour / 7-day usage) into the
status-line command on every refresh. This script prints a short status line and saves that data to
~/.agentboard/claude/<session>.json, where AgentBoard picks up the official quota numbers.

Enable it in ~/.claude/settings.json, pointing at a copy in a folder that stays put (adjust the paths):
  "statusLine": {"type": "command", "command": "python C:/Users/you/.agentboard/claude_statusline.py"}
"""
import glob
import json
import os
import sys
import time

try:
    data = json.loads(sys.stdin.buffer.read().decode("utf-8-sig") or "{}")
except ValueError:
    sys.exit(0)  # never break Claude Code's status line
data["_saved"] = time.time()
folder = os.path.join(os.path.expanduser("~"), ".agentboard", "claude")
os.makedirs(folder, exist_ok=True)
with open(os.path.join(folder, f"{data.get('session_id') or 'session'}.json"), "w", encoding="utf-8") as f:
    json.dump(data, f)
for old in glob.glob(os.path.join(folder, "*.json")):  # keep the folder small
    if time.time() - os.path.getmtime(old) > 2 * 86400:
        os.remove(old)

sys.stdout.reconfigure(encoding="utf-8")  # Claude Code reads UTF-8; the console code page may not have "·"
parts = [((data.get("model") or {}).get("display_name")) or ""]
limits = data.get("rate_limits") or {}
for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
    w = limits.get(key) or {}
    pct = w.get("used_percentage", w.get("utilization"))
    if pct is not None:
        parts.append(f"{label} {float(pct):.0f}%")
print("  ·  ".join(p for p in parts if p))
