"""Token usage, quota and session status from the log files agents already write locally.

Nothing here logs in anywhere or calls a vendor API: every number comes from files on this PC.
"""
import datetime as dt
import glob
import json
import os
import re
import time
import urllib.request
from dataclasses import dataclass, field

from . import config as cfgmod

TS_KEYS = ("timestamp", "time", "created_at", "createdAt", "ts", "startTime", "lastUpdated")


def parse_ts(v, default=None):
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return v / 1000 if v > 1e12 else float(v)
    if isinstance(v, dict):
        return parse_ts(v.get("created") or v.get("start"), default)
    try:
        return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return default


@dataclass
class Event:
    ts: float
    fresh: int          # new input tokens (incl. cache writes) + output tokens
    cache_read: int
    output: int
    model: str = ""


@dataclass
class FileState:
    path: str
    kind: str
    offset: int = 0
    mtime: float = 0.0
    events: list = field(default_factory=list)
    seen: set = field(default_factory=set)
    title: str = ""
    cwd: str = ""
    session: str = ""
    first_ts: float = 0.0
    last_ts: float = 0.0
    last_kind: tuple = ()
    rate_limits: dict = None
    rate_ts: float = 0.0
    model: str = ""
    tokens_used: int = 0   # total reported by the agent's own database, when it has one
    imported_before: float = 0.0  # history copied in from another agent before this time is not this agent's usage
    prompts: list = field(default_factory=list)  # times of the prompts you typed (plans often count these)
    limit_hits: dict = field(default_factory=dict)  # (limit type, resets at) -> when you hit it

    def own_events(self):
        return [e for e in self.events if e.ts > self.imported_before] if self.imported_before else self.events

    def own_prompts(self):
        return [t for t in self.prompts if t > self.imported_before] if self.imported_before else self.prompts


# ---------------------------------------------------------------- readers (one JSON object at a time)

def _num(d, *keys):
    return sum(int(d.get(k) or 0) for k in keys if isinstance(d.get(k), (int, float)))


def read_claude(st, o):
    ts = parse_ts(o.get("timestamp"), st.last_ts or time.time())
    t = o.get("type")
    st.cwd = o.get("cwd") or st.cwd
    st.session = o.get("sessionId") or st.session
    if t == "custom-title":
        st.title = o.get("customTitle") or st.title
    elif t == "agent-name" and not st.title:
        st.title = o.get("agentName") or ""
    elif t == "assistant":
        m = o.get("message") or {}
        u = m.get("usage") or {}
        ql = o.get("quotaLimits")  # on the error Claude Code logs when you hit a rate limit (HTTP 429)
        if isinstance(ql, dict) and ql.get("status") == "rejected" and ql.get("resetsAt"):
            hit = (ql.get("rateLimitType") or "", float(ql["resetsAt"]))
            st.limit_hits[hit] = min(ts, st.limit_hits.get(hit, ts))
        key = (m.get("id"), o.get("requestId"))
        if u and key not in st.seen:
            st.seen.add(key)
            st.events.append(Event(ts, _num(u, "input_tokens", "cache_creation_input_tokens", "output_tokens"),
                                   _num(u, "cache_read_input_tokens"), _num(u, "output_tokens"), m.get("model") or ""))
        st.model = m.get("model") or st.model
        if not o.get("isSidechain"):
            st.last_kind = ("assistant", m.get("stop_reason") or "")
    elif t == "user" and not o.get("isSidechain"):
        c = (o.get("message") or {}).get("content")
        is_tool = isinstance(c, list) and any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c)
        st.last_kind = ("tool_result",) if is_tool else ("user",)
        key = ("prompt", o.get("uuid"))
        if not is_tool and not o.get("isMeta") and key not in st.seen:  # a prompt you typed, not a tool result
            st.seen.add(key)
            st.prompts.append(ts)
    else:
        return
    st.first_ts = st.first_ts or ts
    st.last_ts = max(st.last_ts, ts)


def read_codex(st, o):
    ts = parse_ts(o.get("timestamp"), st.last_ts or time.time())
    p = o.get("payload") if isinstance(o.get("payload"), dict) else {}
    t, pt = o.get("type"), p.get("type")
    if t == "session_meta":
        st.cwd, st.session = p.get("cwd") or st.cwd, p.get("id") or p.get("session_id") or st.session
    elif t == "turn_context":
        st.model = p.get("model") or st.model
    elif pt == "token_count":
        last = ((p.get("info") or {}).get("last_token_usage")) or {}
        if last:
            fresh = max(0, _num(last, "input_tokens") - _num(last, "cached_input_tokens")) + _num(last, "output_tokens")
            if not fresh and _num(last, "total_tokens"):  # some builds (e.g. Codex in the ChatGPT app) log only the total
                fresh = max(0, _num(last, "total_tokens") - _num(last, "cached_input_tokens"))
            st.events.append(Event(ts, fresh, _num(last, "cached_input_tokens"), _num(last, "output_tokens"), st.model))
        if p.get("rate_limits"):
            st.rate_limits, st.rate_ts = p["rate_limits"], ts
    elif pt == "task_started":
        st.last_kind = ("working",)
    elif pt == "task_complete":
        st.last_kind = ("done",)
    elif pt == "user_message":
        st.last_kind = ("working",)
        st.prompts.append(ts)
    elif pt in ("turn_aborted", "error"):
        st.last_kind = ("done",)
    else:
        return
    st.first_ts = st.first_ts or ts
    st.last_ts = max(st.last_ts, ts)


def read_gemini(st, o):
    if "messages" in o and isinstance(o["messages"], list):      # legacy single-JSON session
        st.session = o.get("sessionId") or st.session
        for m in o["messages"]:
            read_gemini(st, m)
        return
    if "sessionId" in o and "type" not in o:                     # metadata line
        st.session = o.get("sessionId") or st.session
        dirs = o.get("directories") or []
        st.cwd = dirs[0] if dirs else st.cwd
        st.first_ts = st.first_ts or parse_ts(o.get("startTime"), 0)
        return
    t = o.get("type")
    if t not in ("user", "gemini"):
        return
    ts = parse_ts(o.get("timestamp"), st.last_ts or time.time())
    tk = o.get("tokens") or {}
    if t == "gemini" and tk:
        key = o.get("id") or (ts, json.dumps(tk))
        if key not in st.seen:
            st.seen.add(key)
            fresh = max(0, _num(tk, "input") - _num(tk, "cached")) + _num(tk, "output", "thoughts")
            st.events.append(Event(ts, fresh, _num(tk, "cached"), _num(tk, "output"), o.get("model") or ""))
        st.model = o.get("model") or st.model
    elif t == "user" and ("prompt", o.get("id") or ts) not in st.seen:
        st.seen.add(("prompt", o.get("id") or ts))
        st.prompts.append(ts)
    st.last_kind = ("assistant", "end_turn") if t == "gemini" else ("user",)
    st.first_ts = st.first_ts or ts
    st.last_ts = max(st.last_ts, ts)


def read_opencode(st, o):
    tk = o.get("tokens") or {}
    ts = parse_ts((o.get("time") or {}).get("created"), st.last_ts or time.time())
    st.session = o.get("sessionID") or st.session
    st.cwd = ((o.get("path") or {}).get("cwd")) or st.cwd
    if o.get("role") == "assistant" and tk:
        cache = tk.get("cache") or {}
        st.events.append(Event(ts, _num(tk, "input", "output", "reasoning") + _num(cache, "write"), _num(cache, "read"),
                               _num(tk, "output"), o.get("modelID") or ""))
        st.model = o.get("modelID") or st.model
        st.last_kind = ("assistant", "end_turn" if (o.get("time") or {}).get("completed") else "tool_use")
    elif o.get("role") == "user":
        st.last_kind = ("user",)
        st.prompts.append(ts)
    st.first_ts = st.first_ts or ts
    st.last_ts = max(st.last_ts, ts)


USAGE_SHAPES = [  # checked in order, anywhere inside a JSON object:
    # (input keys, cached keys, output keys, input count already includes the cached tokens)
    (("input_tokens",), ("cached_input_tokens",), ("output_tokens",), True),                                     # OpenAI Responses / Codex
    (("input_tokens", "cache_creation_input_tokens"), ("cache_read_input_tokens",), ("output_tokens",), False),  # Anthropic
    (("prompt_tokens",), ("cached_tokens",), ("completion_tokens",), True),                                      # OpenAI Chat
    (("promptTokenCount",), ("cachedContentTokenCount",), ("candidatesTokenCount", "thoughtsTokenCount"), True),  # Gemini API
    (("prompt_eval_count",), (), ("eval_count",), False),                                                        # Ollama
    (("input",), ("cached",), ("output", "thoughts"), True),                                                     # Gemini CLI
]


def _match_shape(o):
    for ins, cached, outs, includes_cache in USAGE_SHAPES:
        if not (any(k in o for k in ins) and any(k in o for k in outs)):
            continue
        if includes_cache and cached and ins == ("input_tokens",) and not any(k in o for k in cached):
            continue  # plain input/output_tokens without OpenAI's cached field: use the Anthropic rule
        c = _num(o, *cached)
        return max(0, _num(o, *ins) - (c if includes_cache else 0)) + _num(o, *outs), c, _num(o, *outs)
    return None


def _find_usage(o, depth=0):
    if depth > 6:
        return None
    if isinstance(o, dict):
        r = _match_shape(o)
        if r:
            return r
        for v in o.values():
            r = _find_usage(v, depth + 1)
            if r:
                return r
    elif isinstance(o, list):
        for v in o:
            r = _find_usage(v, depth + 1)
            if r:
                return r
    return None


def read_generic(st, o):
    ts = None
    for k in TS_KEYS:
        if k in o:
            ts = parse_ts(o[k])
            if ts:
                break
    ts = ts or st.mtime or time.time()
    st.cwd = o.get("cwd") or st.cwd
    u = _find_usage(o)
    if u:
        key = o.get("id") or o.get("uuid") or (ts, u)
        if key not in st.seen:
            st.seen.add(key)
            model = o.get("model") or o.get("modelID") or ""
            st.events.append(Event(ts, u[0], u[1], u[2], model if isinstance(model, str) else ""))
            st.model = model if isinstance(model, str) and model else st.model
        st.last_kind = ("assistant", "end_turn")
    role = o.get("role") or o.get("type")
    if role in ("user", "human"):
        st.last_kind = ("user",)
    st.first_ts = st.first_ts or ts
    st.last_ts = max(st.last_ts, ts)


READERS = {"claude": read_claude, "codex": read_codex, "gemini": read_gemini, "opencode": read_opencode,
           "generic": read_generic}


# ---------------------------------------------------------------- incremental file cache

class LogCache:
    def __init__(self):
        self.files = {}

    def update(self, agent_type, history_days):
        kind = agent_type.get("reader", "none")
        if kind not in READERS:
            return []
        cutoff = time.time() - history_days * 86400
        out = []
        for pattern in agent_type.get("logs", []):
            for path in glob.glob(pattern, recursive=True):
                try:
                    mtime, size = os.path.getmtime(path), os.path.getsize(path)
                except OSError:
                    continue
                if mtime < cutoff:
                    continue
                st = self.files.get(path)
                if st is None or size < st.offset or (path.endswith(".json") and mtime != st.mtime):
                    st = self.files[path] = FileState(path, kind)
                if mtime != st.mtime or size != st.offset:
                    self._read(st, size)
                    st.mtime = mtime
                out.append(st)
        return out

    def _read(self, st, size):
        reader = READERS[st.kind]
        try:
            with open(st.path, "rb") as f:
                if st.path.endswith(".json"):
                    try:
                        reader(st, json.loads(f.read().decode("utf-8", "ignore")))
                    except ValueError:
                        pass
                    st.offset = size
                    return
                f.seek(st.offset)
                data = f.read()
        except OSError:
            return
        end = data.rfind(b"\n")
        if end < 0:
            return
        for line in data[:end].splitlines():
            if not line.strip():
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if isinstance(o, dict):
                reader(st, o)
        st.offset += end + 1


# ---------------------------------------------------------------- aggregation

def project_dir_name(cwd):
    return re.sub(r"[^A-Za-z0-9]", "-", cwd or "")


def five_hour_block(events, now, hours=5):
    """Claude-style billing blocks: a block starts at the hour of the first message and lasts `hours`.
    Returns (tokens in the current block, when it ends, the biggest block total in `events`)."""
    start = end = None
    tokens = peak = 0
    for e in sorted(events, key=lambda e: e.ts):
        if end is None or e.ts >= end:
            start = e.ts - (e.ts % 3600)
            end = start + hours * 3600
            tokens = 0
        tokens += e.fresh
        peak = max(peak, tokens)
    if end and now < end:
        return tokens, end, peak
    return 0, None, peak


def _counts(events, prompts, start):
    """What a window holds from `start` on: tokens, API requests (one per reply) and the prompts you typed."""
    ev = [e for e in events if e.ts >= start]
    return {"tokens": sum(e.fresh for e in ev), "requests": len(ev),
            "prompts": None if prompts is None else sum(1 for t in prompts if t >= start)}


def pt_midnight(now, days_ahead):
    """Last (0) or next (1) midnight in US Pacific time, where Google resets its daily quotas."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/Los_Angeles")
    except Exception:  # no time-zone data: Pacific daylight time is close enough
        tz = dt.timezone(dt.timedelta(hours=-7))
    d = dt.datetime.fromtimestamp(now, tz).date() + dt.timedelta(days=days_ahead)
    return dt.datetime.combine(d, dt.time(), tz).timestamp()


def _with_basis(w, official, limit):
    """Fill in a window's percentage of its quota and where the quota comes from: the provider's own figure,
    else your limit from the settings file. With neither, there is no % (the page can still estimate one
    from a plan, a custom limit or a limit learned from earlier official readings)."""
    w.update(basis=None, pct=None, ref=None)
    if official:
        w.update(basis="official", pct=official["pct"])
    elif limit:
        w.update(basis="limit", pct=100 * w["tokens"] / limit, ref=limit)
    return w


def read_statusline_limits(max_age=6 * 3600):
    """Rate-limit data captured by hooks/claude_statusline.py (optional; see README)."""
    best = None
    for p in glob.glob(os.path.join(cfgmod.APP_DIR, "claude", "*.json")):
        try:
            if time.time() - os.path.getmtime(p) > max_age:
                continue
            d = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rl = d.get("rate_limits") or d.get("rateLimits") or d.get("usage_limits")
        if isinstance(rl, dict) and (best is None or d.get("_saved", 0) > best[1]):
            best = (rl, d.get("_saved", 0))
    return best[0] if best else None


def _window_from(item, ref_ts):
    """Normalise one rate-limit window from any of the shapes agents use."""
    if not isinstance(item, dict):
        return None
    pct = item.get("used_percent", item.get("used_percentage", item.get("utilization", item.get("percent"))))
    if pct is None:
        return None
    pct = float(pct) * (100 if float(pct) <= 1 and "utilization" in item else 1)
    reset = item.get("resets_at") or item.get("reset_at") or item.get("resetsAt")
    reset = parse_ts(reset) if reset else None
    if reset is None and item.get("resets_in_seconds") is not None:
        reset = ref_ts + float(item["resets_in_seconds"])
    mins = item.get("window_minutes") or item.get("windowMinutes")
    return {"pct": min(100.0, pct), "reset": reset, "window_minutes": mins}


def official_windows(rate_limits, ref_ts):
    """-> list of {'label', 'pct', 'reset'} from Codex rate_limits or Claude statusline data."""
    out = []
    if not isinstance(rate_limits, dict):
        return out
    for key, label in (("primary", None), ("five_hour", "5h"), ("secondary", None), ("seven_day", "7d"),
                       ("weekly", "7d"), ("seven_day_opus", "7d Opus")):
        w = _window_from(rate_limits.get(key), ref_ts)
        if w:
            m = w.get("window_minutes")
            w["label"] = label or (f"{int(m) // 60}h" if m and m < 1440 else f"{int(m) // 1440}d" if m else key)
            out.append(w)
    # per-model weekly windows, e.g. {"display_name": "Fable", "utilization": .., "resets_at": ..} (Claude Code)
    for item in rate_limits.get("model_scoped") or []:
        w = _window_from(item, ref_ts)
        if w and isinstance(item, dict) and item.get("display_name"):
            w.update(label=item["display_name"], scoped=True)
            out.append(w)
    return out


def codex_threads():
    """Thread titles and token totals from Codex's own state database (opened read-only)."""
    dbs = sorted(glob.glob(os.path.join(cfgmod.HOME, ".codex", "state_*.sqlite")))
    if not dbs:
        return {}
    try:
        import sqlite3
        con = sqlite3.connect("file:" + dbs[-1].replace("\\", "/") + "?mode=ro", uri=True, timeout=1)
        try:
            rows = con.execute("select rollout_path, title, tokens_used, model from threads").fetchall()
        finally:
            con.close()
    except Exception:
        return {}
    return {os.path.normcase(r[0] or ""): r for r in rows}


def codex_imports():
    """Codex threads created by importing another agent's sessions -> import time (seconds)."""
    try:
        d = json.load(open(os.path.join(cfgmod.HOME, ".codex", "external_agent_session_imports.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {r.get("imported_thread_id"): parse_ts(r.get("imported_at"), 0) for r in d.get("records", []) if r.get("imported_thread_id")}


def ollama_models():
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=0.4) as r:
            return [m.get("name", "") for m in json.load(r).get("models", [])]
    except Exception:
        return None


class DayStore:
    """Tokens per agent type on each past day, kept in a small JSON file so the Overview page can still show a
    month after the agents have deleted its logs (Claude Code removes transcripts after 30 days by default).
    It holds only dates and token counts. A day's count only grows: a log that was cleaned up never shrinks it.
    Without a path it keeps nothing and just passes the days through."""

    def __init__(self, path=None):
        self.path, self.days = path, {}
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self.days = {d: {t: int(n) for t, n in by.items()} for d, by in data.get("days", {}).items() if isinstance(by, dict)}
        except (OSError, ValueError, AttributeError, TypeError):
            self.days = {}

    def merge(self, days, today):
        """The stored days with these (read from the logs) on top; saves when a day before today grew."""
        changed = False
        for d, by in days.items():
            if d >= today:
                continue  # today is still growing: it is saved once it is over
            for t, n in by.items():
                if n > self.days.get(d, {}).get(t, 0):
                    self.days.setdefault(d, {})[t] = n
                    changed = True
        if changed and self.path:
            self._save()
        out = {d: dict(by) for d, by in self.days.items()}
        for d, by in days.items():
            slot = out.setdefault(d, {})
            for t, n in by.items():
                slot[t] = max(slot.get(t, 0), n)
        return out

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "days": dict(sorted(self.days.items()))}, f, separators=(",", ":"))
            os.replace(tmp, self.path)
        except OSError:
            pass  # read-only home: the page still shows what the logs have


HIT_WINDOWS = {"five_hour": ("5h", 5), "seven_day": ("7d", 168)}  # limit type in the logs -> window key, hours
HIT_MAX_AGE = 25 * 86400  # Claude Code deletes transcripts after 30 days: an older hit's window is no longer complete


class HitStore:
    """Limits learned from the rate limits you hit. When an agent is stopped by its 5-hour (or weekly) limit, the
    tokens you used in that window came to 100%, so they make an estimate of the limit. Kept in a small JSON file,
    so the estimate survives the agent deleting its logs. It holds only limit types, times and token counts."""

    def __init__(self, path=None):
        self.path, self.hits = path, {}
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                self.hits = {t: [h for h in v if isinstance(h, dict)] for t, v in json.load(f).get("hits", {}).items()}
        except (OSError, ValueError, AttributeError, TypeError):
            self.hits = {}

    def add(self, type_id, kind, resets_at, hit_at, tokens):
        """Record one hit; a window seen again keeps its larger count (more of its logs may have been read)."""
        rows = self.hits.setdefault(type_id, [])
        row = next((h for h in rows if h.get("type") == kind and abs(h.get("resets_at", 0) - resets_at) < 60), None)
        if row and row.get("tokens", 0) >= tokens:
            return
        if row:
            row.update(tokens=tokens, hit_at=min(row.get("hit_at", hit_at), hit_at))
        else:
            rows.append({"type": kind, "resets_at": resets_at, "hit_at": hit_at, "tokens": tokens})
            rows.sort(key=lambda h: h["hit_at"])
            del rows[:-20]  # the last 20 hits are plenty
        self._save()

    def learned(self, type_id, seen=None):
        """-> {"5h": {"tokens", "hits", "last"}, ...}: the middle of the last five hits of each kind. `seen` is the
        most a window has held without a hit ({"5h": tokens}); a learned limit below it is out of date (a bigger
        plan, or a count from logs that were already gone), so it is left out."""
        out = {}
        for kind, (key, _) in HIT_WINDOWS.items():
            rows = [h for h in self.hits.get(type_id, []) if h.get("type") == kind][-5:]
            if rows:
                v = sorted(h["tokens"] for h in rows)
                tokens = v[len(v) // 2]
                if (seen or {}).get(key, 0) > tokens * 1.1:
                    continue
                out[key] = {"tokens": tokens, "hits": len(rows), "last": rows[-1]["hit_at"]}
        return out

    def _save(self):
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "hits": self.hits}, f, separators=(",", ":"))
            os.replace(tmp, self.path)
        except OSError:
            pass


class UsageModel:
    def __init__(self, cfg, days_file=None, limits_file=None):
        self.cfg = cfg
        self.cache = LogCache()
        self.store = DayStore(days_file)
        self.hit_store = HitStore(limits_file)
        self.by_type = {}
        self.summary = {}
        self.overview = {"days": {}, "models": {"7d": [], "30d": []}}

    def refresh(self):
        days = self.cfg.get("history_days", 31)
        for a in self.cfg["agents"]:
            self.by_type[a["id"]] = self.cache.update(a, days)
            if a.get("reader") == "codex":
                meta, imports = codex_threads(), codex_imports()
                for st in self.by_type[a["id"]]:
                    row = meta.get(os.path.normcase(st.path))
                    if row:
                        st.title, st.tokens_used, st.model = row[1] or st.title, int(row[2] or 0), row[3] or st.model
                    if st.session in imports:
                        st.imported_before = imports[st.session] + 60
        self.overview = self._overview(time.time())
        self._learn_hits()

    def _learn_hits(self):
        """Each rate limit hit in the logs: the tokens from the window's start up to the hit make its 100%."""
        for a in self.cfg["agents"]:
            files = self.by_type.get(a["id"], [])
            hits = {}
            for f in files:
                for k, at in f.limit_hits.items():
                    hits[k] = min(at, hits.get(k, at))
            if not hits:
                continue
            ev = [e for f in files for e in f.own_events()]
            for (kind, resets_at), hit_at in hits.items():
                if kind not in HIT_WINDOWS or time.time() - hit_at > HIT_MAX_AGE:
                    continue
                start = resets_at - HIT_WINDOWS[kind][1] * 3600
                tokens = sum(e.fresh for e in ev if start <= e.ts <= hit_at)
                if tokens >= 10_000:  # less means the window's logs are gone: no estimate from it
                    self.hit_store.add(a["id"], kind, resets_at, hit_at, tokens)

    def _overview(self, now):
        """For the Overview page: tokens by model over 7 and 30 days, and tokens per agent type on each day (local
        dates) that the logs or the day store have, so the page can step back through the months."""
        today = dt.date.today().isoformat()
        days, models = {}, {"7d": {}, "30d": {}}
        for a in self.cfg["agents"]:
            tid = a["id"]
            for f in self.by_type.get(tid, []):
                for e in f.own_events():
                    if not e.fresh:
                        continue
                    day = days.setdefault(dt.date.fromtimestamp(e.ts).isoformat(), {})
                    day[tid] = day.get(tid, 0) + e.fresh
                    model = e.model if e.model and not e.model.startswith("<") else "unknown model"
                    for key, span in (("7d", 7), ("30d", 30)):
                        if e.ts >= now - span * 86400:
                            models[key][(tid, model)] = models[key].get((tid, model), 0) + e.fresh
        days = self.store.merge(days, today)
        return {"first": min(days, default=today), "today": today, "days": days,
                "models": {k: sorted(({"type_id": t, "model": m, "tokens": n} for (t, m), n in v.items()),
                                     key=lambda r: -r["tokens"]) for k, v in models.items()}}

    def summarize(self, agents):
        now = time.time()
        today0 = dt.datetime.combine(dt.date.today(), dt.time()).timestamp()
        out = {}
        for a in self.cfg["agents"]:
            files = self.by_type.get(a["id"], [])
            ev = [e for f in files for e in f.own_events()]
            prompts = [t for f in files for t in f.own_prompts()]
            running = [x for x in agents if x.type_id == a["id"]]
            today = [e for e in ev if e.ts >= today0]
            week = [e for e in ev if e.ts >= now - 7 * 86400]
            q = a.get("quota", {"kind": "none"})
            block_hours = (q.get("fallback") or q).get("window_hours", 5)
            blk_tokens, blk_end, blk_peak = five_hour_block(ev, now, block_hours)
            s = {"id": a["id"], "name": a["name"], "color": a.get("color", "#888"), "local": a.get("local", False),
                 "running": len(running), "working": sum(x.status == "working" for x in running),
                 "waiting": sum(x.status in ("your_turn", "approval") for x in running),
                 "today": sum(e.fresh for e in today), "today_cache": sum(e.cache_read for e in today),
                 "requests_today": len(today), "week": sum(e.fresh for e in week),
                 "block": blk_tokens, "block_end": blk_end, "block_hours": block_hours, "block_peak": blk_peak,
                 "models": sorted({e.model for e in today if e.model and not e.model.startswith("<")})[:3], "quota": self._quota(a, files, ev, blk_tokens, blk_end, today),
                 "windows": self._windows(a, files, ev, prompts, now),
                 # daily quotas (Gemini CLI, for one) reset at midnight Pacific time
                 "daily": {"end": pt_midnight(now, 1), **_counts(ev, prompts, pt_midnight(now, 0))}}
            s["hit_limits"] = self.hit_store.learned(a["id"], seen={"5h": blk_peak, "7d": s["week"]})
            if a["id"] == "ollama":
                s["loaded"] = ollama_models()
            s["visible"] = bool(s["running"] or s["today"] or s["week"] or s.get("loaded"))
            out[a["id"]] = s
        self.summary = out
        return out

    @staticmethod
    def _official(a, files):
        """Rate-limit windows as the provider reports them: Codex session logs, or Claude's status line (account-wide)."""
        if a.get("quota", {}).get("kind") != "from_log":
            return []
        if a["reader"] == "codex":
            latest = max((f for f in files if f.rate_limits), key=lambda f: f.rate_ts, default=None)
            return official_windows(latest.rate_limits, latest.rate_ts) if latest else []
        if a["reader"] == "claude":
            return official_windows(read_statusline_limits(), time.time())
        return []

    def _windows(self, a, files, ev, prompts, now):
        """Tokens used in the 5-hour and 7-day windows, then any window of another length the provider reports
        (Codex's free plan has one of 30 days), then any per-model weekly windows the agent type defines
        ("model_windows", e.g. Fable for Claude). Each gets a percentage: the provider's own figure when one is
        reported, else against your own limit from the settings; otherwise none."""
        official, scoped, other = {}, [], {}
        for w in self._official(a, files):
            if w.get("reset") and w["reset"] < now:  # a passed reset is stale
                continue
            if w.get("scoped"):
                scoped.append(w)
            elif w["label"] in ("5h", "7d"):
                official.setdefault(w["label"], w)
            elif w.get("window_minutes"):  # its length tells where it starts, so its tokens can be counted
                other.setdefault(w["label"], w)
        q = a.get("quota") or {}
        fb = q.get("fallback") or q
        out = []
        for key, hours in (("5h", 5), ("7d", 168)):
            o = official.get(key)
            if o and o.get("reset"):  # count over the provider's own window
                start, end = o["reset"] - hours * 3600, o["reset"]
            elif key == "5h":
                _, end, _ = five_hour_block(ev, now)
                start = end - hours * 3600 if end else now  # no active block: nothing in it yet
            else:
                start, end = now - 7 * 86400, None
            limit = fb.get("limit_tokens" if key == "5h" else "limit_tokens_7d")
            out.append(_with_basis({"key": key, "label": key, "end": end, **_counts(ev, prompts, start)}, o, limit))
        for label, o in sorted(other.items(), key=lambda kv: float(kv[1]["window_minutes"])):
            mins = float(o["window_minutes"])
            start = (o["reset"] if o.get("reset") else now) - mins * 60
            name = f"{round(mins / 60)}-hour" if mins < 1440 else f"{round(mins / 1440)}-day"
            out.append(_with_basis({"key": label, "label": label, "name": name, "end": o.get("reset"),
                                    **_counts(ev, prompts, start)}, o, None))
        for mw in a.get("model_windows", []):
            pat = mw["match"].lower()
            mev = [e for e in ev if pat in (e.model or "").lower()]
            o = next((w for w in scoped if pat in w["label"].lower()), None)
            start = o["reset"] - 7 * 86400 if o and o.get("reset") else now - 7 * 86400
            # prompts are not tied to a model, so a model's window counts its tokens and requests only
            w = {"key": mw["key"], "label": mw.get("label", mw["key"]), "end": o.get("reset") if o else None,
                 **_counts(mev, None, start)}
            out.append(_with_basis(w, o, fb.get(f"limit_tokens_{mw['key']}")))
        return out

    def _quota(self, a, files, ev, blk_tokens, blk_end, today):
        q = a.get("quota", {"kind": "none"})
        kind = q.get("kind", "none")
        if kind == "from_log":
            wins = self._official(a, files)
            if wins:
                return {"kind": "official", "windows": wins}
            q = q.get("fallback") or {"kind": "none"}
            kind = q.get("kind", "none")
        if kind == "tokens_window":
            lim = q.get("limit_tokens")
            return {"kind": "budget", "label": f"{q.get('window_hours', 5)}h block", "used": blk_tokens, "limit": lim,
                    "pct": (100 * blk_tokens / lim) if lim else None, "reset": blk_end}
        if kind == "requests_day":
            lim = q.get("limit_requests")
            tomorrow = dt.datetime.combine(dt.date.today() + dt.timedelta(days=1), dt.time()).timestamp()
            return {"kind": "budget", "label": "requests today", "used": len(today), "limit": lim,
                    "pct": (100 * len(today) / lim) if lim else None, "reset": tomorrow, "unit": "req"}
        if kind == "unlimited":
            return {"kind": "unlimited"}
        return {"kind": "none"}

    # ------------------------------------------------------------ link processes to sessions + status

    def attach_sessions(self, agents):
        now = time.time()
        taken = set()
        for ag in sorted(agents, key=lambda x: x.started):
            files = [f for f in self.by_type.get(ag.type_id, []) if f.path not in taken]
            st = self._match(ag, files)
            if st:
                taken.add(st.path)
                ag.session_file = st.path
                ag.session_tokens = sum(e.fresh for e in st.own_events()) or st.tokens_used
                ag.model = st.model
                ag.last_activity = st.last_ts
                ag.name = (st.title or "").strip()
            ag.status, ag.status_detail = infer_status(ag, st, now)
        # readable, unique names
        counts = {}
        for ag in agents:
            folder = os.path.basename(ag.cwd.rstrip("\\/"))
            if re.fullmatch(r"[0-9a-f]{12,}|app-[\d.]+|System32", folder or "", re.I):  # app-internal folders say nothing
                folder = f"{ag.type_name} ({ag.host})" if ag.host != "Console" else ag.type_name
            base = ag.name or folder or ag.type_name
            ag.name = base if len(base) <= 42 else base[:40] + "…"
            counts[(ag.type_id, ag.name)] = counts.get((ag.type_id, ag.name), 0) + 1
        seen = {}
        for ag in agents:
            k = (ag.type_id, ag.name)
            if counts[k] > 1:
                seen[k] = seen.get(k, 0) + 1
                ag.name = f"{ag.name} #{seen[k]}"

    def _match(self, ag, files):
        files = [f for f in files if not f.imported_before or f.last_ts > f.imported_before]
        if not files:
            return None
        if ag.type_id == "claude":
            pdir = project_dir_name(ag.cwd).lower()
            files = [f for f in files if os.path.basename(os.path.dirname(f.path)).lower() == pdir] or []
        else:
            same = [f for f in files if f.cwd and os.path.normcase(f.cwd) == os.path.normcase(ag.cwd)]
            files = same or files
        if ag.resume_id:
            for f in files:
                if os.path.basename(f.path).startswith(ag.resume_id):
                    return f
        new = [f for f in files if f.first_ts >= ag.started - 120]
        if new:
            return min(new, key=lambda f: f.first_ts)
        active = [f for f in files if f.last_ts >= ag.started - 5]
        return max(active, key=lambda f: f.last_ts) if active else None


def infer_status(ag, st, now):
    """-> (status, detail). status: working | your_turn | approval | idle"""
    if st is None or not st.last_ts:
        return ("working", f"CPU {ag.cpu:.0f}%") if ag.cpu > 3 else ("idle", "")
    age = now - st.last_ts
    lk = st.last_kind or ()
    if lk[:1] == ("working",) or lk[:1] in (("user",), ("tool_result",)):
        status = "working" if age < 900 or ag.cpu > 3 else "idle"
    elif lk == ("assistant", "tool_use"):
        status = "working" if age < 45 or ag.cpu > 3 else "approval"
    elif lk[:1] in (("assistant",), ("done",)):
        status = "your_turn"
    else:
        status = "working" if age < 30 else "idle"
    if status in ("your_turn", "approval") and age > 6 * 3600:
        status = "idle"
    return status, _ago(age)


def _ago(sec):
    if sec < 60:
        return "just now"
    if sec < 3600:
        return f"{int(sec // 60)}m ago"
    if sec < 86400:
        return f"{int(sec // 3600)}h ago"
    return f"{int(sec // 86400)}d ago"
