"""Shared schema, HTTP helper and storage for Shark Watch AU collectors.

Every collector returns a list of Event dicts with the FIELDS below.
store.merge() appends new events to data/events.csv (deduped by id), so
live feeds that only show the last few days build up a full history.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
EVENTS_CSV = DATA / "events.csv"

FIELDS = [
    "id",           # stable hash, used for dedupe
    "source",       # e.g. sa_pirsa, nsw_sharksmart, qld_scp, asid
    "state",        # NSW, WA, SA, QLD, VIC, TAS, NT
    "event_type",   # sighting | detection | drone | tagging | catch | incident | alert
    "occurred_at",  # ISO 8601 (local time where known), or YYYY-MM-DD / YYYY-MM
    "location",
    "lat",
    "lon",
    "species",
    "size_m",
    "count",
    "reported_by",
    "details",
    "url",
    "collected_at",
]

UA = "SharkWatchAU/0.1 (+https://nbbuswatch.github.io/sharkwatch; public-safety info aggregator)"
SESSION = requests.Session()
SESSION.headers["User-Agent"] = UA


def get(url: str, **kw) -> requests.Response:
    """GET with 3 retries and a polite delay."""
    last = None
    for attempt in range(3):
        try:
            r = SESSION.get(url, timeout=kw.pop("timeout", 30), **kw)
            r.raise_for_status()
            time.sleep(1)
            return r
        except requests.RequestException as e:  # noqa: PERF203
            last = e
            time.sleep(2 * (attempt + 1))
    raise last  # type: ignore[misc]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_id(*parts) -> str:
    key = "|".join(str(p).strip().lower() for p in parts if p is not None)
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def clean(s) -> str:
    if s is None:
        return ""
    s = str(s)
    if s.lower() in {"nan", "none", "nat"}:
        return ""
    return re.sub(r"\s+", " ", s).strip()


def event(source, state, event_type, occurred_at, location, **kw) -> dict:
    e = {f: "" for f in FIELDS}
    e.update(
        source=source,
        state=state,
        event_type=event_type,
        occurred_at=clean(occurred_at),
        location=clean(location),
        collected_at=now_iso(),
    )
    for k, v in kw.items():
        if k in e:
            e[k] = clean(v)
    e["id"] = kw.get("id") or make_id(source, event_type, e["occurred_at"], e["location"], e["species"], e["details"][:80])
    return e


def save_raw(name: str, content: str | bytes) -> Path:
    RAW.mkdir(parents=True, exist_ok=True)
    p = RAW / name
    if isinstance(content, bytes):
        p.write_bytes(content)
    else:
        p.write_text(content, encoding="utf-8")
    return p


def load_events() -> dict[str, dict]:
    if not EVENTS_CSV.exists():
        return {}
    with EVENTS_CSV.open(newline="", encoding="utf-8") as f:
        return {r["id"]: r for r in csv.DictReader(f)}


def merge(new_events: list[dict]) -> int:
    """Add events not already stored. Returns number added."""
    DATA.mkdir(parents=True, exist_ok=True)
    existing = load_events()
    added = 0
    for e in new_events:
        if e["id"] not in existing:
            existing[e["id"]] = e
            added += 1
    rows = sorted(existing.values(), key=lambda r: (r.get("occurred_at") or ""), reverse=True)
    with EVENTS_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return added


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes"}
