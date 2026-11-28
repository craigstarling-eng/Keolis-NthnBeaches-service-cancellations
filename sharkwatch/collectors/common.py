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

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0 Safari/537.36 SharkWatchAU (+https://nbbuswatch.github.io/sharkwatch)")
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Accept-Language": "en-AU,en;q=0.9"})


class Resp:
    """Minimal response object so callers work the same for requests and browser fetches."""
    def __init__(self, content: bytes, url: str):
        self.content, self.url = content, url

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.content)


_BROWSER = {}


def browser():
    """One shared headless Chromium for the whole run (Playwright allows only one sync instance)."""
    if "b" not in _BROWSER:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        _BROWSER.update(pw=pw, b=pw.chromium.launch(), warmed=set())
    return _BROWSER["b"]


def _browser_ctx():
    if "ctx" not in _BROWSER:
        _BROWSER["ctx"] = browser().new_context(user_agent=UA, locale="en-AU", timezone_id="Australia/Sydney")
    return _BROWSER["ctx"]


def browser_get(url: str, expect_file: bool = False, wait_selector: str | None = None) -> Resp:
    """Fetch through real headless Chrome (for sites that block plain scripts)."""
    ctx = _browser_ctx()
    origin = "/".join(url.split("/")[:3])
    page = ctx.new_page()
    try:
        if origin not in _BROWSER["warmed"]:  # visit the site once to pick up cookies / pass checks
            page.goto(origin, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
            _BROWSER["warmed"].add(origin)
        if expect_file:
            r = ctx.request.get(url, timeout=90000)
            return Resp(r.body(), url)
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        if wait_selector:
            try:
                page.wait_for_selector(wait_selector, timeout=20000)
            except Exception:  # noqa: BLE001
                print(f"  [browser] '{wait_selector}' never appeared on {url}; title={page.title()!r}")
        page.wait_for_timeout(2000)
        return Resp(page.content().encode(), url)
    finally:
        page.close()


def close_browser():
    if "b" in _BROWSER:
        _BROWSER["b"].close()
        _BROWSER["pw"].stop()
        _BROWSER.clear()


def _looks_blocked(content: bytes, expect_file: bool) -> bool:
    head = content[:600].lstrip().lower()
    if expect_file:
        return not head or head.startswith((b"<!doctype", b"<html")) or b"<html" in head[:200]
    return any(w in head for w in (b"access denied", b"request blocked", b"attention required"))


def get(url: str, expect_file: bool = False, wait_selector: str | None = None, **kw):
    """GET with retries; falls back to headless Chrome if the site blocks scripts."""
    timeout = kw.pop("timeout", 30)
    for attempt in range(2):
        try:
            r = SESSION.get(url, timeout=timeout, **kw)
            if r.status_code in (401, 403, 429) or _looks_blocked(r.content, expect_file):
                break
            r.raise_for_status()
            time.sleep(1)
            return r
        except requests.RequestException:
            time.sleep(2 * (attempt + 1))
    print(f"  [get] plain request blocked, using browser: {url[:100]}")
    r = browser_get(url, expect_file=expect_file, wait_selector=wait_selector)
    if _looks_blocked(r.content, expect_file):
        raise RuntimeError(f"blocked even in browser: {url}")
    return r


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
