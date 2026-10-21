"""Find running agent processes and the window that hosts each one."""
import os
import re
import time
from dataclasses import dataclass, field

import psutil

from . import config as cfgmod
from . import winapi

HOSTS = {  # ancestor process name -> host label (first match while walking up the tree)
    "windowsterminal.exe": "Windows Terminal",
    "code.exe": "VS Code", "cursor.exe": "Cursor", "windsurf.exe": "Windsurf", "kiro.exe": "Kiro",
    "idea64.exe": "JetBrains", "pycharm64.exe": "JetBrains", "webstorm64.exe": "JetBrains",
    "claude.exe": "Claude app", "chatgpt.exe": "ChatGPT app", "codex.exe": "Codex app",
    "wezterm-gui.exe": "WezTerm", "alacritty.exe": "Alacritty", "tabby.exe": "Tabby", "hyper.exe": "Hyper",
    "mintty.exe": "Git Bash", "conemu64.exe": "ConEmu", "warp.exe": "Warp",
}
UUID_FLAG = re.compile(r"--(?:resume|session-id)[= ]([0-9a-f-]{36})")
CLI_NAMES = {"claude": "Claude Code CLI", "codex": "Codex CLI", "gemini": "Gemini CLI", "grok": "Grok CLI",
             "qwen": "Qwen Code CLI", "opencode": "opencode", "aider": "Aider", "ollama": "Ollama"}
APP_SURFACES = {"Claude app": ("claude", "Claude desktop app"), "ChatGPT app": (None, "ChatGPT app"),
                "Codex app": ("codex", "Codex desktop app")}
IDE_NAMES = {"VS Code", "Cursor", "Windsurf", "Kiro", "JetBrains"}


@dataclass
class Agent:
    pid: int
    type_id: str
    type_name: str
    color: str
    exe: str
    cwd: str
    started: float
    cmd: str = ""
    resume_id: str = ""
    host: str = "Console"
    host_pid: int = 0
    cpu: float = 0.0
    # filled in by usage/status
    name: str = ""
    status: str = "idle"          # working | your_turn | approval | idle
    status_detail: str = ""
    session_file: str = ""
    session_tokens: int = 0
    model: str = ""
    last_activity: float = 0.0
    extra: dict = field(default_factory=dict)

    @property
    def key(self):
        return f"{self.type_id}:{self.pid}"

    @property
    def surface(self):
        """(kind, label) for where the agent runs: ('cli', 'Claude Code CLI · Windows Terminal'), ('app', ...), ('ide', ...)."""
        if self.host in APP_SURFACES:
            native, label = APP_SURFACES[self.host]
            return "app", label if native == self.type_id else f"{self.type_name} · {label}"
        if self.host in IDE_NAMES:
            return "ide", f"{self.type_name} · {self.host} terminal"
        cli = CLI_NAMES.get(self.type_id, f"{self.type_name} CLI")
        return "cli", f"{cli} · {'Console window' if self.host == 'Console' else self.host}"


class Scanner:
    def __init__(self, cfg):
        self.cfg = cfg
        self.types = []
        for a in cfg["agents"]:
            m = a.get("match", {})
            self.types.append((a, {e.lower() for e in m.get("exe", [])},
                               re.compile(m["cmd_regex"], re.I) if m.get("cmd_regex") else None))
        self._cpu = {}  # pid -> psutil.Process (keeps cpu_percent state between scans)

    def _classify(self, p):
        name = (p.info["name"] or "").lower()
        cmd = p.info["cmdline"] or []
        if any(c.startswith("--type=") for c in cmd):          # Electron helper processes
            return None
        exe = p.info["exe"] or ""
        if "\\app-" in exe.lower() and len(cmd) <= 1:            # Electron main process of a desktop app
            return None
        line = " ".join(cmd)
        for a, exes, rx in self.types:
            if name in exes:
                return a
            if rx and name not in cfgmod.SHELLS and rx.search(line):
                return a
        return None

    def scan(self):
        found = {}
        me = os.getpid()
        for p in psutil.process_iter(["pid", "ppid", "name", "exe", "cmdline", "create_time"]):
            if p.info["pid"] == me or p.info["ppid"] == me:  # the dashboard itself and its helpers
                continue
            try:
                a = self._classify(p)
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue
            if a:
                found[p.info["pid"]] = (p, a)
        # keep only the top-most process of a chain of the same agent type (wrappers spawn children)
        windows = winapi.windows_by_pid()
        agents = []
        for pid, (p, a) in found.items():
            try:
                parents = p.parents()
            except psutil.Error:
                parents = []
            if any(pp.pid in found and found[pp.pid][1]["id"] == a["id"] for pp in parents):
                continue
            try:
                cwd = p.cwd()
            except psutil.Error:
                cwd = ""
            cmd = " ".join(p.info["cmdline"] or [])
            ag = Agent(pid=pid, type_id=a["id"], type_name=a["name"], color=a.get("color", "#888888"),
                       exe=p.info["name"] or "", cwd=cwd, started=p.info["create_time"] or time.time(), cmd=cmd)
            m = UUID_FLAG.search(cmd)
            ag.resume_id = m.group(1) if m else ""
            ag.host, ag.host_pid = self._host(parents, windows)
            ag.cpu = self._cpu_percent(pid)
            agents.append(ag)
        live = {a.pid for a in agents}
        self._cpu = {k: v for k, v in self._cpu.items() if k in live}
        agents.sort(key=lambda a: (a.type_id, a.started))
        return agents

    def _host(self, parents, windows):
        for pp in parents:
            try:
                n = pp.name().lower()
            except psutil.Error:
                continue
            if n in HOSTS and (n == "windowsterminal.exe" or pp.pid in windows):
                return HOSTS[n], pp.pid
        return "Console", 0

    def _cpu_percent(self, pid):
        p = self._cpu.get(pid)
        try:
            if p is None:
                p = self._cpu[pid] = psutil.Process(pid)
                p.cpu_percent(None)
                return 0.0
            return p.cpu_percent(None)
        except psutil.Error:
            return 0.0
