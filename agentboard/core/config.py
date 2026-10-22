"""Agent type definitions and user settings.

Built-in agent types live in DEFAULT_CONFIG. A user file at ~/.agentboard/config.json can
override any field of a built-in type (matched by "id") or add new types, e.g. another
local-model agent:

    {"agents": [{"id": "myagent", "name": "My Agent", "color": "#0ea5e9",
                 "match": {"cmd_regex": "\\bmyagent\\b"},
                 "reader": "generic", "logs": ["~/.myagent/**/*.jsonl"],
                 "quota": {"kind": "none"}}]}

Readers: claude, codex, gemini, opencode, generic (any JSON/JSONL with token fields), none.
Quota kinds:
  from_log       official limits recorded by the agent itself (Codex rate_limits, Claude statusline)
  tokens_window  your own token budget per rolling window: {"limit_tokens": 2000000, "window_hours": 5}
                 plus, optionally, per 7 days: {"limit_tokens_7d": 30000000}, and per model window:
                 {"limit_tokens_fable": 10000000}
model_windows    extra weekly bars for one model family: [{"key": "fable", "label": "Fable", "match": "fable"}]
                 ("match" is a case-insensitive part of the model id)
  requests_day   your own request budget per day: {"limit_requests": 1000}
  unlimited      local models
  none           show tokens only
"""
import copy
import json
import os

HOME = os.path.expanduser("~")
APP_DIR = os.path.join(HOME, ".agentboard")
USER_CONFIG = os.path.join(APP_DIR, "config.json")
DAYS_FILE = os.path.join(APP_DIR, "days.json")  # tokens per agent type on each past day, for the Overview page
LIMITS_FILE = os.path.join(APP_DIR, "limits.json")  # limits learned from the rate limits you hit

DEFAULT_CONFIG = {
    "refresh_seconds": 3,
    "usage_refresh_seconds": 15,
    "always_on_top": True,
    "history_days": 31,
    "agents": [
        {"id": "claude", "name": "Claude Code", "color": "#d4704f",
         "match": {"exe": ["claude.exe", "claude"], "cmd_regex": r"@anthropic-ai[\\/]claude-code"},
         "reader": "claude", "logs": ["~/.claude/projects/*/*.jsonl"],
         "quota": {"kind": "from_log", "fallback": {"kind": "tokens_window", "window_hours": 5, "limit_tokens": None}},
         # Claude plans also meter some models per week on their own; this one gets its own bar
         "model_windows": [{"key": "fable", "label": "Fable", "match": "fable"}]},
        {"id": "codex", "name": "Codex", "color": "#10a37f",
         "match": {"exe": ["codex.exe", "codex"], "cmd_regex": r"@openai[\\/]codex"},
         "reader": "codex", "logs": ["~/.codex/sessions/**/*.jsonl"],
         "quota": {"kind": "from_log", "fallback": {"kind": "none"}}},
        {"id": "gemini", "name": "Gemini CLI", "color": "#4285f4",
         "match": {"exe": ["gemini.exe"], "cmd_regex": r"@google[\\/]gemini-cli|[\\/]gemini(\.js)?(\s|$)"},
         "reader": "gemini", "logs": ["~/.gemini/tmp/*/chats/*.jsonl", "~/.gemini/tmp/*/chats/*.json"],
         "quota": {"kind": "requests_day", "limit_requests": None}},
        {"id": "grok", "name": "Grok", "color": "#db2777",
         "match": {"exe": ["grok.exe", "grok", "xai-grok-pager.exe"], "cmd_regex": r"grok-cli|[\\/]grok(\.js)?(\s|$)"},
         "reader": "generic", "logs": ["~/.grok/**/*.jsonl", "~/.grok/**/*.json"],
         "quota": {"kind": "none"}},
        {"id": "qwen", "name": "Qwen Code", "color": "#7c3aed",
         "match": {"exe": ["qwen.exe"], "cmd_regex": r"@qwen-code[\\/]qwen-code"},
         "reader": "generic", "logs": ["~/.qwen/tmp/*/chats/*.jsonl", "~/.qwen/tmp/*/chats/*.json"],
         "quota": {"kind": "none"}},
        {"id": "opencode", "name": "opencode", "color": "#a16207",
         "match": {"exe": ["opencode.exe", "opencode"], "cmd_regex": r"opencode-ai|[\\/]opencode(\.js)?(\s|$)"},
         "reader": "opencode", "logs": ["~/.local/share/opencode/storage/message/*/*.json"],
         "quota": {"kind": "none"}},
        {"id": "aider", "name": "Aider", "color": "#65a30d",
         "match": {"exe": ["aider.exe"], "cmd_regex": r"[\\/]aider(\.exe)?(\s|$)|-m aider"},
         "reader": "none", "quota": {"kind": "none"}},
        {"id": "ollama", "name": "Local model (Ollama)", "color": "#0891b2", "local": True,
         "match": {"cmd_regex": r"ollama(\.exe)?\"?\s+(run|launch)\b"},
         "reader": "none", "quota": {"kind": "unlimited"}},
    ],
}

# Processes that can carry an agent's name on their command line but are never the agent itself.
SHELLS = {"cmd.exe", "powershell.exe", "pwsh.exe", "conhost.exe", "openconsole.exe", "windowsterminal.exe",
          "explorer.exe", "py.exe", "bash.exe", "wsl.exe", "git.exe", "where.exe"}


def _merge(base, override):
    out = copy.deepcopy(base)
    for k, v in override.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load():
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if os.path.exists(USER_CONFIG):
        try:
            user = json.load(open(USER_CONFIG, encoding="utf-8"))
        except Exception as e:  # a broken user file should not stop the app
            cfg["config_error"] = f"{USER_CONFIG}: {e}"
            return cfg
        by_id = {a["id"]: i for i, a in enumerate(cfg["agents"])}
        for a in user.pop("agents", []):
            if a.get("id") in by_id:
                cfg["agents"][by_id[a["id"]]] = _merge(cfg["agents"][by_id[a["id"]]], a)
            else:
                cfg["agents"].append(a)
        cfg = _merge(cfg, user)
    for a in cfg["agents"]:
        a["logs"] = [os.path.expanduser(p) for p in a.get("logs", [])]
    return cfg


def write_example():
    """Create ~/.agentboard/config.json with commented-by-example settings if it does not exist."""
    os.makedirs(APP_DIR, exist_ok=True)
    if not os.path.exists(USER_CONFIG):
        example = {
            "always_on_top": True,
            "agents": [
                {"id": "claude", "quota": {"fallback": {"limit_tokens": None, "limit_tokens_7d": None, "limit_tokens_fable": None}}},
                {"id": "gemini", "quota": {"limit_requests": None}},
                {"id": "my-local-agent", "name": "My local agent", "color": "#0ea5e9", "local": True,
                 "match": {"cmd_regex": r"\bmy-local-agent\b"}, "reader": "generic",
                 "logs": ["~/.my-local-agent/**/*.jsonl"], "quota": {"kind": "unlimited"}},
            ],
        }
        json.dump(example, open(USER_CONFIG, "w", encoding="utf-8"), indent=2)
    return USER_CONFIG
