"""Background refresh loop and the JSON snapshot the UI renders."""
import os
import threading
import time

from . import claude_app
from . import config as cfgmod
from .procs import Scanner
from .usage import UsageModel

STATUS_ORDER = {"approval": 0, "your_turn": 1, "working": 2, "idle": 3}


class Engine:
    def __init__(self, cfg=None):
        self.cfg = cfg or cfgmod.load()
        self.scanner = Scanner(self.cfg)
        self.usage = UsageModel(self.cfg)
        self.agents, self.summary = [], {}
        self.error = None
        self._last_usage = 0.0
        self._stop = threading.Event()

    def tick(self, force_usage=False):
        agents = self.scanner.scan()
        if force_usage or time.time() - self._last_usage >= self.cfg.get("usage_refresh_seconds", 15):
            self.usage.refresh()
            self._last_usage = time.time()
        self.usage.attach_sessions(agents)
        self._link_claude_app(agents)
        self.summary = self.usage.summarize(agents)
        self.agents = agents
        return agents, self.summary

    def run(self, on_update):
        def loop():
            first = True
            while not self._stop.is_set():
                try:
                    self.tick(force_usage=first)
                    self.error = None
                except Exception as e:  # keep the loop alive; the UI shows the error
                    self.error = repr(e)
                on_update(self.snapshot())
                first = False
                self._stop.wait(self.cfg.get("refresh_seconds", 3))
        threading.Thread(target=loop, daemon=True).start()

    def stop(self):
        self._stop.set()

    @staticmethod
    def _link_claude_app(agents):
        """Give Claude desktop agents their app session id (for the exact jump) and the title the app's sidebar shows."""
        app_agents = [a for a in agents if a.host == "Claude app" and a.type_id == "claude"]
        if not app_agents:
            return
        recs = claude_app.sessions()
        for a in app_agents:
            cli_id = os.path.splitext(os.path.basename(a.session_file))[0] if a.session_file else ""
            rec = recs.get(cli_id) or recs.get(a.resume_id)
            if rec:
                a.extra["app_session"] = rec["id"]
                a.name = rec["title"] or a.name

    def agent(self, key):
        return next((a for a in self.agents if a.key == key), None)

    # ---------------------------------------------------------------- what the UI receives
    def snapshot(self):
        types = [s for s in self.summary.values() if s.get("visible")]
        total_today = sum(s["today"] for s in types) or 0
        out_types = []
        for s in sorted(types, key=lambda s: (-s["running"], -s["today"])):
            q = s["quota"]
            # rings: outer = 5-hour window, inner = 7-day window (tokens used, and a % with its basis)
            requests = {"used": q["used"], "limit": q.get("limit")} if q.get("unit") == "req" else None
            out_types.append({k: s[k] for k in ("id", "name", "color", "local", "running", "working", "waiting", "today",
                                                 "today_cache", "requests_today", "week", "models", "windows")}
                             | {"loaded": s.get("loaded"), "quota_kind": q["kind"], "requests": requests})
        agents = []
        for a in sorted(self.agents, key=lambda a: (STATUS_ORDER.get(a.status, 9), a.type_name, a.name)):
            kind, label = a.surface
            agents.append({"key": a.key, "pid": a.pid, "type_id": a.type_id, "type_name": a.type_name, "color": a.color,
                           "name": a.name, "status": a.status, "since": a.status_detail, "surface_kind": kind,
                           "surface": label, "host": a.host, "tokens": a.session_tokens, "model": a.model,
                           "folder": os.path.basename(a.cwd.rstrip("\\/"))})
        return {"ts": time.time(), "ready": True, "error": self.error or self.cfg.get("config_error"),
                "totals": {"today": total_today, "agents": len(agents),
                           "working": sum(a["status"] == "working" for a in agents),
                           "waiting": sum(a["status"] in ("your_turn", "approval") for a in agents)},
                "types": out_types, "agents": agents}
