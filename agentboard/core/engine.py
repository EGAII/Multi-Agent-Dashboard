"""Background refresh loop and the JSON snapshot the UI renders."""
import os
import threading
import time

from . import claude_app, plans, quota_read
from . import config as cfgmod
from .procs import Scanner
from .usage import UsageModel

STATUS_ORDER = {"approval": 0, "your_turn": 1, "working": 2, "idle": 3}


class Engine:
    def __init__(self, cfg=None):
        self.cfg = cfg or cfgmod.load()
        self.scanner = Scanner(self.cfg)
        self.usage = UsageModel(self.cfg, days_file=cfgmod.DAYS_FILE, limits_file=cfgmod.LIMITS_FILE)
        self.agents, self.summary = [], {}
        self.error = None
        self._last_usage = 0.0
        self._stop = threading.Event()
        self._lock = threading.Lock()  # the refresh loop and a "Read quota" click both tick

    def tick(self, force_usage=False):
        with self._lock:
            agents = self.scanner.scan()
            if force_usage or time.time() - self._last_usage >= self.cfg.get("usage_refresh_seconds", 15):
                self.usage.refresh()
                self._last_usage = time.time()
            self.usage.attach_sessions(agents)
            self._link_claude_app(agents)
            self.summary = self.usage.summarize(agents)
            self.agents = agents
            return agents, self.summary

    def read_quota_now(self, type_id):
        """Ask the agent's own CLI for its quota (quota_read.py); the windows feed the next summary."""
        r = quota_read.READERS[type_id]()
        self.usage.reads[type_id] = r
        return r

    def read_quota(self, type_id):
        """"Read quota": ask the CLI now where there is a way (Claude Code, Codex), then re-read the logs.
        -> (the CLI's answer or None, windows with an official %, limits learned from hits)."""
        r = self.read_quota_now(type_id) if type_id in quota_read.READERS else None
        self.tick(force_usage=True)
        s = self.summary.get(type_id) or {}
        return r, [w for w in s.get("windows", []) if w.get("basis") == "official"], s.get("hit_limits") or {}

    def _read_loop(self):
        """Read the quota of the agents in use at start, then every few minutes; half-hourly while one has none."""
        while not self.summary and not self._stop.is_set():
            self._stop.wait(0.5)
        every, next_at = self.cfg.get("quota_read_minutes", 5) * 60, {}
        while not self._stop.is_set():
            for tid in quota_read.READERS:
                if time.time() >= next_at.get(tid, 0) and (self.summary.get(tid) or {}).get("visible"):
                    try:
                        ok = self.read_quota_now(tid)["ok"]
                    except Exception:  # a CLI that misbehaves must not stop the others
                        ok = False
                    next_at[tid] = time.time() + (every if ok else max(every, 1800))
            self._stop.wait(20)

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
        if self.cfg.get("quota_read", True):
            threading.Thread(target=self._read_loop, daemon=True).start()

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
        # every configured agent type goes to the page, so a monitor can be added for one that is not in use yet;
        # "visible" marks the ones shown by default (running now or used recently)
        types = list(self.summary.values())
        conf = {a["id"]: a for a in self.cfg["agents"]}
        total_today = sum(s["today"] for s in types) or 0
        out_types = []
        for s in sorted(types, key=lambda s: (not s.get("visible"), -s["running"], -s["today"])):
            q = s["quota"]
            requests = {"used": q["used"], "limit": q.get("limit")} if q.get("unit") == "req" else None
            out_types.append({k: s[k] for k in ("id", "name", "color", "local", "running", "working", "waiting", "today",
                                                 "today_cache", "requests_today", "week", "models", "windows")}
                             | {"loaded": s.get("loaded"), "quota_kind": q["kind"], "requests": requests,
                                "visible": bool(s.get("visible")), "daily": s.get("daily"),
                                "plans": plans.for_type(s["id"]), "hit_limits": s.get("hit_limits") or {},
                                # "Read quota" on every card but local models: it asks the CLI where it can
                                "read_quota": not conf.get(s["id"], {}).get("local"),
                                "last_read": {k: v for k, v in (self.usage.reads.get(s["id"]) or {}).items() if k != "windows"}})
        agents = []
        for a in sorted(self.agents, key=lambda a: (STATUS_ORDER.get(a.status, 9), a.type_name, a.name)):
            kind, label = a.surface
            # sid names the session rather than the process, so pins and highlights survive a restart
            sid = (f"app:{a.extra['app_session']}" if a.extra.get("app_session") else
                   f"{a.type_id}:{os.path.splitext(os.path.basename(a.session_file))[0]}" if a.session_file else
                   f"{a.type_id}:{a.name}")
            agents.append({"key": a.key, "sid": sid, "pid": a.pid, "type_id": a.type_id, "type_name": a.type_name, "color": a.color,
                           "name": a.name, "status": a.status, "since": a.status_detail, "surface_kind": kind,
                           "surface": label, "host": a.host, "tokens": a.session_tokens, "model": a.model,
                           "folder": os.path.basename(a.cwd.rstrip("\\/"))})
        return {"ts": time.time(), "ready": True, "error": self.error or self.cfg.get("config_error"),
                "totals": {"today": total_today, "agents": len(agents),
                           "working": sum(a["status"] == "working" for a in agents),
                           "waiting": sum(a["status"] in ("your_turn", "approval") for a in agents)},
                "types": out_types, "agents": agents, "overview": self.usage.overview}
