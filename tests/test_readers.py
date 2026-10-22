"""Reader tests with sample logs in each agent's format. Run: python tests/test_readers.py"""
import datetime as dt
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agentboard.core import config as cfgmod  # noqa: E402
from agentboard.core.usage import DayStore, FileState, HitStore, UsageModel  # noqa: E402

NOW = time.time()
ISO = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat().replace("+00:00", "Z")  # noqa: E731


def write(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        if isinstance(lines, dict):
            json.dump(lines, f)
        else:
            f.write("\n".join(json.dumps(x) for x in lines) + "\n")


def main():
    tmp = tempfile.mkdtemp(prefix="agentboard-test-")
    write(f"{tmp}/gemini/chats/session-a.jsonl", [
        {"sessionId": "s1", "projectHash": "h", "startTime": ISO(NOW - 120), "kind": "main", "directories": ["C:\\proj\\demo"]},
        {"id": "m1", "type": "user", "content": "hi", "timestamp": ISO(NOW - 60)},
        {"id": "m2", "type": "gemini", "content": "ok", "timestamp": ISO(NOW - 50), "model": "gemini-3-pro",
         "tokens": {"input": 1200, "output": 300, "cached": 200, "thoughts": 50, "tool": 0, "total": 1550}},
    ])
    write(f"{tmp}/grok/s1.jsonl", [
        {"timestamp": ISO(NOW - 40), "role": "assistant", "model": "grok-4",
         "usage": {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200, "cached_tokens": 100}}])
    write(f"{tmp}/local/s1.jsonl", [{"created_at": ISO(NOW - 30), "model": "qwen3:8b", "prompt_eval_count": 500, "eval_count": 120}])
    write(f"{tmp}/opencode/ses1/msg1.json", {
        "id": "msg1", "sessionID": "ses1", "role": "assistant", "modelID": "qwen3:8b", "providerID": "ollama",
        "time": {"created": int((NOW - 20) * 1000), "completed": int((NOW - 15) * 1000)}, "path": {"cwd": "C:\\proj"},
        "tokens": {"input": 800, "output": 100, "reasoning": 20, "cache": {"read": 300, "write": 50}}})
    write(f"{tmp}/codex/rollout-x.jsonl", [
        {"timestamp": ISO(NOW - 100), "type": "session_meta", "payload": {"id": "c1", "cwd": "C:\\proj"}},
        {"timestamp": ISO(NOW - 95), "type": "event_msg", "payload": {"type": "user_message"}},
        {"timestamp": ISO(NOW - 90), "type": "event_msg", "payload": {"type": "task_started"}},
        {"timestamp": ISO(NOW - 80), "type": "event_msg", "payload": {"type": "token_count", "info": {"last_token_usage": {
            "input_tokens": 5000, "cached_input_tokens": 4000, "output_tokens": 600, "total_tokens": 5600}},
            "rate_limits": {"primary": {"used_percent": 42.5, "window_minutes": 300, "resets_in_seconds": 3600},
                            "secondary": {"used_percent": 12.0, "window_minutes": 10080, "resets_at": int(NOW + 86400 * 3)}}}},
    ])
    cfgmod.APP_DIR = f"{tmp}/app"
    write(f"{tmp}/app/claude/s.json", {"_saved": NOW, "rate_limits": {
        "five_hour": {"used_percentage": 37, "resets_at": ISO(NOW + 7200)}, "seven_day": {"used_percentage": 64, "resets_at": ISO(NOW + 86400)},
        "model_scoped": [{"display_name": "Fable", "utilization": 0.25, "resets_at": ISO(NOW + 3 * 86400)}]}})
    write(f"{tmp}/claude/C--proj/b.jsonl", [
        {"type": "assistant", "timestamp": ISO(NOW - 20), "cwd": "C:\\proj", "sessionId": "b", "requestId": "r2",
         "message": {"id": "m2", "model": "claude-fable-5-1", "stop_reason": "end_turn",
                     "usage": {"input_tokens": 100, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 50}}},
    ])
    write(f"{tmp}/claude/C--proj/a.jsonl", [
        {"type": "user", "timestamp": ISO(NOW - 12), "uuid": "u1", "sessionId": "a", "message": {"role": "user", "content": "hi"}},
        {"type": "user", "timestamp": ISO(NOW - 11), "uuid": "u2", "sessionId": "a",
         "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}},
        {"type": "user", "timestamp": ISO(NOW - 11), "uuid": "u3", "isMeta": True, "sessionId": "a", "message": {"role": "user", "content": "x"}},
        {"type": "assistant", "timestamp": ISO(NOW - 10), "cwd": "C:\\proj", "sessionId": "a", "requestId": "r1",
         "message": {"id": "m1", "model": "claude-x", "stop_reason": "end_turn",
                     "usage": {"input_tokens": 10, "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 9000, "output_tokens": 200}}},
        {"type": "assistant", "timestamp": ISO(NOW - 10), "cwd": "C:\\proj", "sessionId": "a", "requestId": "r1",
         "message": {"id": "m1", "model": "claude-x", "stop_reason": "end_turn",
                     "usage": {"input_tokens": 10, "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 9000, "output_tokens": 200}}},
    ])

    cfg = cfgmod.load()
    paths = {"gemini": [f"{tmp}/gemini/chats/*.jsonl"], "grok": [f"{tmp}/grok/*.jsonl"], "opencode": [f"{tmp}/opencode/*/*.json"],
             "codex": [f"{tmp}/codex/*.jsonl"], "claude": [f"{tmp}/claude/*/*.jsonl"]}
    for a in cfg["agents"]:
        a["logs"] = paths.get(a["id"], [])
    cfg["agents"].append({"id": "local", "name": "Local", "reader": "generic", "logs": [f"{tmp}/local/*.jsonl"],
                          "quota": {"kind": "unlimited"}, "local": True, "match": {}})
    m = UsageModel(cfg)
    m.refresh()
    s = m.summarize([])
    # the day store keeps past days for the Overview: a day only grows, and today is saved once it is over
    days_file = f"{tmp}/days.json"
    DayStore(days_file).merge({"2026-10-20": {"claude": 500}, "2026-10-22": {"claude": 90}}, "2026-10-22")
    merged = DayStore(days_file).merge({"2026-10-20": {"claude": 300, "codex": 7}}, "2026-10-22")
    with open(days_file, encoding="utf-8") as f:
        saved = json.load(f)["days"]
    # a reported window of another length (Codex's free plan has one of 30 days) gets its own bar
    free = FileState("free.jsonl", "codex", rate_ts=NOW,
                     rate_limits={"primary": {"used_percent": 3.0, "window_minutes": 43200, "resets_at": NOW + 20 * 86400}})
    free_wins = m._windows(next(a for a in cfg["agents"] if a["id"] == "codex"), [free], [], [], NOW)
    # a quota read from the CLI (Claude Code's /usage) wins over the status line while it is recent
    claude_cfg = next(a for a in cfg["agents"] if a["id"] == "claude")
    m.reads["claude"] = {"ok": True, "at": NOW, "windows": [
        {"label": "5h", "pct": 42.0, "reset": NOW + 3600, "window_minutes": 300},
        {"label": "7d", "pct": 12.0, "reset": NOW + 86400, "window_minutes": 10080},
        {"label": "Fable", "pct": 30.0, "reset": NOW + 86400, "window_minutes": 10080, "scoped": True}]}
    read_wins = [(w["label"], w["basis"], w["pct"]) for w in m._windows(claude_cfg, m.by_type["claude"], [], [], NOW)]
    m.reads["claude"]["at"] = NOW - 3600  # an hour old: back to the status line
    stale_wins = [w["pct"] for w in m._windows(claude_cfg, m.by_type["claude"], [], [], NOW)]
    # a rate limit hit teaches the 5h limit: the tokens from the window's start up to the hit
    hit_at, big = NOW - 3 * 86400, {"input_tokens": 0, "cache_creation_input_tokens": 0, "output_tokens": 30000}
    old = NOW - 40 * 86400  # too old: Claude Code has deleted most of that window's logs by now
    write(f"{tmp}/hits/C--p/h.jsonl", [
        {"type": "assistant", "timestamp": ISO(old - 3600), "sessionId": "h", "requestId": "o1", "message": {"id": "o1", "usage": big}},
        {"type": "assistant", "timestamp": ISO(old), "sessionId": "h", "isApiErrorMessage": True, "message": {"id": "o2"},
         "quotaLimits": {"status": "rejected", "resetsAt": old + 3600, "rateLimitType": "five_hour"}},
        {"type": "assistant", "timestamp": ISO(hit_at - 7200), "sessionId": "h", "requestId": "h1", "message": {"id": "h1", "usage": big}},
        {"type": "assistant", "timestamp": ISO(hit_at - 3600), "sessionId": "h", "requestId": "h2", "message": {"id": "h2", "usage": big}},
        {"type": "assistant", "timestamp": ISO(hit_at), "sessionId": "h", "isApiErrorMessage": True, "message": {"id": "h3"},
         "quotaLimits": {"status": "rejected", "resetsAt": hit_at + 3600, "rateLimitType": "five_hour"}},
    ])
    hit_cfg = cfgmod.load()
    hit_cfg["agents"] = [dict(next(a for a in hit_cfg["agents"] if a["id"] == "claude"), logs=[f"{tmp}/hits/*/*.jsonl"])]
    hm = UsageModel(hit_cfg, limits_file=f"{tmp}/limits.json")
    hm.refresh()
    hit_limits = hm.summarize([])["claude"]["hit_limits"]
    with open(f"{tmp}/limits.json", encoding="utf-8") as f:
        kept = [(h["type"], h["tokens"]) for h in json.load(f)["hits"]["claude"]]

    checks = [
        ("gemini fresh tokens", s["gemini"]["today"], 1350),
        ("gemini cache reads", s["gemini"]["today_cache"], 200),
        ("grok (OpenAI chat usage)", s["grok"]["today"], 1100),
        ("local (Ollama counters)", s["local"]["today"], 620),
        ("opencode fresh tokens", s["opencode"]["today"], 970),
        ("opencode cache reads", s["opencode"]["today_cache"], 300),
        ("codex fresh tokens", s["codex"]["today"], 1600),
        ("codex quota windows", [round(w["pct"], 1) for w in s["codex"]["quota"].get("windows", [])], [42.5, 12.0]),
        ("codex quota labels", [w["label"] for w in s["codex"]["quota"].get("windows", [])], ["5h", "7d"]),
        ("claude dedupe + fresh", s["claude"]["today"], 1360),
        ("claude statusline quota", [w["pct"] for w in s["claude"]["quota"].get("windows", [])], [37.0, 64.0, 25.0]),
        ("claude 5h / 7d / Fable windows", [(w["label"], w["tokens"], w["basis"], round(w["pct"])) for w in s["claude"]["windows"]],
         [("5h", 1360, "official", 37), ("7d", 1360, "official", 64), ("Fable", 150, "official", 25)]),
        ("claude prompts and requests (no tool results or meta)",
         [(w["requests"], w["prompts"]) for w in s["claude"]["windows"][:2]], [(2, 1), (2, 1)]),
        ("codex prompts", s["codex"]["windows"][1]["prompts"], 1),
        ("gemini requests and prompts today (Pacific day)", (s["gemini"]["daily"]["requests"], s["gemini"]["daily"]["prompts"]), (1, 1)),
        ("local quota", s["local"]["quota"]["kind"], "unlimited"),
        ("overview: claude tokens today", m.overview["days"].get(dt.date.today().isoformat(), {}).get("claude"), 1360),
        ("day store: a stored day only grows", merged["2026-10-20"], {"claude": 500, "codex": 7}),
        ("day store: today is not saved yet", sorted(saved), ["2026-10-20"]),
        ("limit hit: 5h learned from the window's tokens", hit_limits.get("5h", {}).get("tokens"), 60000),
        ("limit hit: kept in limits.json, the 40-day-old hit left out", kept, [("five_hour", 60000)]),
        ("limit hit: left out once a bigger window is seen", HitStore(f"{tmp}/limits.json").learned("claude", seen={"5h": 100_000}), {}),
        ("codex 30-day window only (free plan)", [(w["label"], w["basis"], w["pct"], w.get("name")) for w in free_wins],
         [("30d", "official", 3.0, "30-day")]),
        ("claude /usage read: 5h, 7d, Fable", read_wins, [("5h", "official", 42.0), ("7d", "official", 12.0), ("Fable", "official", 30.0)]),
        ("claude /usage read: stale after 30 min", stale_wins, [37.0, 64.0, 25.0]),
    ]
    bad = 0
    for name, got, want in checks:
        ok = got == want
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name}: got {got}, want {want}")
    print(f"\n{len(checks) - bad}/{len(checks)} passed")
    return bad


if __name__ == "__main__":
    sys.exit(main())

