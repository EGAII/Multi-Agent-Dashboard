"""Made-up agents and usage for `python -m agentboard --demo`: try the UI or take screenshots without real sessions."""
import datetime as dt
import threading
import time

from . import plans, usage

TYPES = [
    # id, name, colour, local, running, working, waiting, today, cache, requests, quota kind,
    # (5h tokens, 5h %, 5h basis, minutes to reset), (7d tokens, 7d %, 7d basis, minutes to reset)
    ("claude", "Claude Code", "#d4704f", False, 7, 2, 2, 4_820_000, 96_400_000, 0, "official",
     (2_140_000, 62, "official", 134), (21_400_000, 38, "official", 3 * 1440 + 200)),
    ("codex", "Codex", "#10a37f", False, 3, 1, 1, 1_930_000, 31_000_000, 0, "official",
     (980_000, 41, "official", 221), (7_240_000, 18, "official", 5 * 1440 + 90)),
    ("gemini", "Gemini CLI", "#4285f4", False, 1, 1, 0, 642_000, 3_100_000, 212, "budget",
     (410_000, None, None, 160), (2_900_000, None, None, None)),
    ("ollama", "Ollama", "#0891b2", True, 1, 0, 0, 310_000, 0, 0, "unlimited",
     (120_000, None, None, 95), (1_480_000, None, None, None)),
]
# the Overview page: each agent's usual tokens on a weekday, and its models' shares
DEMO_DAYS = 120  # made-up history for the Overview page, about four months
DAILY = {"claude": 4_600_000, "codex": 1_700_000, "gemini": 600_000, "ollama": 250_000}
MODELS = {"claude": [("claude-opus-5-5", 0.64), ("claude-sonnet-5-5", 0.26), ("claude-fable-5-1", 0.10)],
          "codex": [("gpt-5.5-codex", 0.85), ("gpt-5.6-luna", 0.15)], "gemini": [("gemini-3-pro", 1.0)],
          "ollama": [("qwen3:8b", 1.0)]}
# configured but unused agent types: not shown until you add a monitor for them
IDLE_TYPES = [("grok", "Grok", "#db2777"), ("qwen", "Qwen Code", "#7c3aed"), ("opencode", "opencode", "#a16207"),
              ("aider", "Aider", "#65a30d")]
# per-model weekly windows: key, label, (tokens, %, basis, minutes to reset)
MODEL_WINDOWS = {"claude": [("fable", "Fable", (3_150_000, 46, "official", 4 * 1440 + 60))]}
AGENTS = [
    # type, name, status, since, surface kind, surface, tokens, model
    ("claude", "Migrate billing to the v2 API", "approval", "1m ago", "cli", "Claude Code CLI · Windows Terminal", 1_240_000, "claude-opus-5-5"),
    ("codex", "Fix flaky auth tests", "your_turn", "4m ago", "cli", "Codex CLI · Windows Terminal", 512_000, "gpt-5.5-codex"),
    ("claude", "Write the release notes", "your_turn", "12m ago", "app", "Claude desktop app", 388_000, "claude-sonnet-5-5"),
    ("claude", "Refactor the search indexer", "working", "just now", "cli", "Claude Code CLI · Windows Terminal", 2_960_000, "claude-opus-5-5"),
    ("codex", "Add rate-limit headers", "working", "just now", "ide", "Codex · VS Code terminal", 1_410_000, "gpt-5.5-codex"),
    ("gemini", "Summarise the design docs", "working", "just now", "cli", "Gemini CLI · Windows Terminal", 642_000, "gemini-3-pro"),
    ("claude", "Review the onboarding flow", "working", "just now", "app", "Claude desktop app", 734_000, "claude-sonnet-5-5"),
    ("claude", "Tidy the CI pipeline", "idle", "40m ago", "cli", "Claude Code CLI · Windows Terminal", 215_000, "claude-sonnet-5-5"),
    ("codex", "Port the CSV exporter", "idle", "1h ago", "cli", "Codex CLI · Windows Terminal", 96_000, "gpt-5.5-codex"),
    ("claude", "Draft the Q4 roadmap notes", "idle", "2h ago", "app", "Claude desktop app", 128_000, "claude-opus-5-5"),
    ("claude", "Upgrade the date library", "idle", "5h ago", "ide", "Claude Code · VS Code terminal", 87_000, "claude-sonnet-5-5"),
    ("ollama", "Ollama (qwen3:8b)", "idle", "25m ago", "cli", "Ollama · Console window", 310_000, "qwen3:8b"),
]


class DemoEngine:
    demo = True

    def __init__(self):
        self._stop = threading.Event()
        self._t0 = time.time()

    def run(self, on_update):
        def loop():
            while not self._stop.is_set():
                on_update(self.snapshot())
                self._stop.wait(3)
        threading.Thread(target=loop, daemon=True).start()

    def stop(self):
        self._stop.set()

    def agent(self, key):
        return None

    def snapshot(self):
        now = time.time()
        grow = 1 + min(0.05, (now - self._t0) / 3600)  # numbers creep up a little while the demo runs
        total = sum(t[7] for t in TYPES) * grow
        types = []
        for tid, name, color, local, running, working, waiting, today, cache, reqs, kind, w5, w7 in TYPES:
            windows = []
            for key, label, (tokens, pct, basis, mins) in [("5h", "5h", w5), ("7d", "7d", w7)] + MODEL_WINDOWS.get(tid, []):
                tokens = int(tokens * grow)
                windows.append({"key": key, "label": label, "tokens": tokens, "pct": min(100, pct * grow) if pct else None,
                                "basis": basis, "end": now + mins * 60 if mins else None, "ref": None,
                                "requests": tokens // 5200, "prompts": tokens // 40000})
            daily = {"tokens": int(today * grow), "requests": reqs or int(today * grow) // 5200,
                     "prompts": int(today * grow) // 40000, "end": usage.pt_midnight(now, 1)}
            types.append({"id": tid, "name": name, "color": color, "local": local, "running": running, "working": working,
                          "waiting": waiting, "today": int(today * grow), "today_cache": cache, "requests_today": reqs,
                          "week": w7[0], "models": [], "windows": windows, "loaded": ["qwen3:8b"] if local else None,
                          "quota_kind": kind, "requests": {"used": reqs, "limit": 1000} if reqs else None, "visible": True,
                          "daily": daily, "plans": plans.for_type(tid), "read_quota": not local})
        for tid, name, color in IDLE_TYPES:
            types.append({"id": tid, "name": name, "color": color, "local": False, "running": 0, "working": 0, "waiting": 0,
                          "today": 0, "today_cache": 0, "requests_today": 0, "week": 0, "models": [], "loaded": None,
                          "windows": [{"key": k, "label": k, "tokens": 0, "pct": None, "basis": None, "end": None, "ref": None,
                                       "requests": 0, "prompts": 0} for k in ("5h", "7d")],
                          "quota_kind": "none", "requests": None, "visible": False,
                          "daily": {"tokens": 0, "requests": 0, "prompts": 0, "end": usage.pt_midnight(now, 1)},
                          "plans": plans.for_type(tid)})
        colors = {t[0]: (t[1], t[2]) for t in TYPES}
        agents = []
        for i, (tid, name, status, since, skind, surface, tokens, model) in enumerate(AGENTS):
            agents.append({"key": f"demo-{i}", "sid": f"demo:{name}", "pid": 4100 + i * 37, "type_id": tid, "type_name": colors[tid][0],
                           "color": colors[tid][1], "name": name, "status": status, "since": since, "surface_kind": skind,
                           "surface": surface, "host": "", "tokens": tokens, "model": model, "folder": "demo"})
        return {"ts": now, "ready": True, "error": None,
                "totals": {"today": int(total), "agents": len(agents), "working": sum(a["status"] == "working" for a in agents),
                           "waiting": sum(a["status"] in ("your_turn", "approval") for a in agents)},
                "types": types, "agents": agents, "overview": self._overview()}

    @staticmethod
    def _overview():
        """About four months of made-up daily use: busier on weekdays, a few quiet days, Codex and Gemini on some days only."""
        today = dt.date.today()
        days, totals = {}, {"7d": {}, "30d": {}}
        for back in range(DEMO_DAYS):
            d = today - dt.timedelta(days=back)
            seed = (d.toordinal() * 2654435761) % 1000 / 1000  # steady pseudo-random per day
            if d.weekday() >= 5 and seed < 0.6 or seed < 0.06:
                continue  # weekends are mostly quiet, and the odd weekday off
            day = {}
            for i, (tid, base) in enumerate(DAILY.items()):
                share = ((d.toordinal() * (i + 3) * 40503) % 1000) / 1000
                if tid != "claude" and share < 0.35:
                    continue
                day[tid] = int(base * (0.35 + 1.3 * share) * (0.5 if d.weekday() >= 5 else 1))
                for span, key in ((7, "7d"), (30, "30d")):
                    if back < span:
                        for model, part in MODELS[tid]:
                            totals[key][(tid, model)] = totals[key].get((tid, model), 0) + int(day[tid] * part)
            days[d.isoformat()] = day
        return {"first": (today - dt.timedelta(days=DEMO_DAYS - 1)).isoformat(), "today": today.isoformat(),
                "days": days, "models": {k: sorted(({"type_id": t, "model": m, "tokens": n} for (t, m), n in v.items()),
                                                   key=lambda r: -r["tokens"]) for k, v in totals.items()}}
