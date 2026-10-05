"""
Reconstruye data/history.csv a partir de los commits de state.json.
Cada corrida de GitHub Actions commitea state.json cuando algo cambia, asi que
el historial de git ya es un historial de precios. Se usa una sola vez:

    python tools/backfill_history.py
"""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from history import HISTORY_FILE, append_events, make_event  # noqa: E402


def git(*args):
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout


def main():
    if HISTORY_FILE.exists():
        sys.exit(f"{HISTORY_FILE} ya existe, borralo si quieres regenerarlo.")

    commits = git("log", "--reverse", "--format=%H %cI", "--", "state.json").split("\n")
    last = {}  # key -> ultima oferta escrita (con su in_stock)
    events = []
    for line in filter(None, commits):
        sha, date = line.split()
        date = datetime.fromisoformat(date).astimezone(timezone.utc).isoformat(timespec="seconds")
        try:
            state = json.loads(git("show", f"{sha}:state.json"))
        except json.JSONDecodeError:
            continue
        live = {k: v for k, v in state.items() if not v.get("_stale")}
        for key, offer in live.items():
            prev = last.get(key)
            in_stock = bool(offer.get("in_stock", True))
            if prev is None or (prev["price"], prev["in_stock"]) != (offer["price"], in_stock):
                events.append(make_event(date, key, offer))
                last[key] = {**offer, "in_stock": in_stock}
        for key, prev in last.items():
            if key not in live and prev["in_stock"]:
                events.append(make_event(date, key, prev, in_stock=False))
                prev["in_stock"] = False

    append_events(events)
    print(f"{len(events)} eventos desde {len(commits) - 1} commits -> {HISTORY_FILE}")


if __name__ == "__main__":
    main()
