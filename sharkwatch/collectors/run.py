"""Run collectors and rebuild summary files.

  python collectors/run.py live        # SA + NSW + WA (every 15 min)
  python collectors/run.py historical  # QLD SCP + ASID (daily)
  python collectors/run.py all
  BACKFILL=1 python collectors/run.py live   # also pulls SA yearly archives
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import asid
import nsw_sharksmart
import qld_scp
import sa_pirsa
import wa_sharksmart
from common import DATA, close_browser, load_events, merge, now_iso, write_json

GROUPS = {
    "live": [sa_pirsa, nsw_sharksmart, wa_sharksmart],
    "historical": [qld_scp, asid],
}
GROUPS["all"] = GROUPS["live"] + GROUPS["historical"]


def parse_when(s: str):
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            d = datetime.strptime(s[:10], "%Y-%m-%d")
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def build_summary() -> dict:
    rows = list(load_events().values())
    now = datetime.now(timezone.utc)
    live_types = {"sighting", "detection", "drone", "tagging", "alert"}
    s = {
        "generated_at": now_iso(),
        "total_events": len(rows),
        "by_source": Counter(r["source"] for r in rows),
        "by_state": Counter(r["state"] for r in rows),
        "by_type": Counter(r["event_type"] for r in rows),
        "last_24h": defaultdict(int),
        "last_7d": defaultdict(int),
        "last_30d_by_location": Counter(),
        "last_30d_by_species": Counter(),
        "by_month": defaultdict(Counter),   # YYYY-MM -> {state: n}
        "incidents_by_year": Counter(),
    }
    for r in rows:
        d = parse_when(r["occurred_at"])
        if r["event_type"] == "incident" and r["occurred_at"][:4].isdigit():
            s["incidents_by_year"][r["occurred_at"][:4]] += 1
        if not d:
            continue
        if r["event_type"] in live_types:
            s["by_month"][d.strftime("%Y-%m")][r["state"]] += 1
            age = now - d
            if age <= timedelta(days=1):
                s["last_24h"][r["state"]] += 1
            if age <= timedelta(days=7):
                s["last_7d"][r["state"]] += 1
            if age <= timedelta(days=30):
                s["last_30d_by_location"][f'{r["location"]} ({r["state"]})'] += 1
                s["last_30d_by_species"][r["species"] or "Unknown"] += 1
    s["last_30d_by_location"] = dict(s["last_30d_by_location"].most_common(25))
    s["last_30d_by_species"] = dict(s["last_30d_by_species"].most_common())
    s["by_month"] = {k: dict(v) for k, v in sorted(s["by_month"].items())}
    s["incidents_by_year"] = dict(sorted(s["incidents_by_year"].items()))
    return s


def latest(n=200) -> list[dict]:
    rows = [r for r in load_events().values() if r["event_type"] not in {"catch", "incident"}]
    rows.sort(key=lambda r: r["occurred_at"], reverse=True)
    return rows[:n]


def main(group: str = "live") -> None:
    status = {}
    for mod in GROUPS[group]:
        name = mod.__name__
        try:
            evs = mod.collect()
            added = merge(evs)
            status[name] = {"ok": True, "found": len(evs), "added": added, "at": now_iso()}
        except Exception as e:  # noqa: BLE001  one bad source must not stop the rest
            status[name] = {"ok": False, "error": str(e)[:300], "at": now_iso()}
        print(name, status[name])
    close_browser()
    prev = {}
    sp = DATA / "status.json"
    if sp.exists():
        import json
        prev = json.loads(sp.read_text())
    write_json(sp, {**prev, **status})
    write_json(DATA / "summary.json", build_summary())
    write_json(DATA / "latest.json", latest())


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "live")
