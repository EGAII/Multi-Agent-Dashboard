"""python -m agentboard            -> open the AgentBoard window
python -m agentboard --once     -> print one snapshot as text (for checking setup)
python -m agentboard --tabs     -> list Windows Terminal tabs the app can see
python -m agentboard --demo     -> open the window with made-up agents (no real sessions needed)
"""
import datetime as dt
import sys


def fmt(n):
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.0f}k" if n >= 1e3 else str(int(n))


def once():
    from .core.engine import Engine
    sys.stdout.reconfigure(encoding="utf-8")
    e = Engine()
    e.tick(force_usage=True)
    snap = e.snapshot()
    print(f"== Today {fmt(snap['totals']['today'])} tokens · {snap['totals']['agents']} agents running")
    for t in (t for t in snap["types"] if t["visible"]):
        wins = []
        for w in t["windows"]:
            pct = f" {w['pct']:.0f}% ({w['basis']})" if w["pct"] is not None else ""
            when = dt.datetime.fromtimestamp(w["end"]) if w["end"] else None
            end = f", resets {when:%H:%M}" if when and when.date() == dt.date.today() else f", resets {when:%b %d %H:%M}" if when else ""
            wins.append(f"{w['key']} {fmt(w['tokens'])}{pct}{end}")
        print(f"  {t['name']:22} running={t['running']} today={fmt(t['today'])} | {' | '.join(wins)}")
    print("\n== Agents")
    for a in snap["agents"]:
        print(f"  [{a['status']:9}] {a['name'][:40]:40} {a['surface']:42} tokens={fmt(a['tokens']):>6} {a['since']}")


def tabs():
    from .core.focus import Focuser
    sys.stdout.reconfigure(encoding="utf-8")
    f = Focuser()
    r = f.list_tabs()
    for t in r.get("tabs", []):
        print(f"  window {t['hwnd']} tab {t['index']}: {t['name']}{'  (selected)' if t.get('selected') else ''}")
    if not r.get("ok"):
        print("error:", r.get("error"))
    f.close()


if __name__ == "__main__":
    if "--once" in sys.argv:
        once()
    elif "--tabs" in sys.argv:
        tabs()
    else:
        from .app import main
        main(demo="--demo" in sys.argv)
