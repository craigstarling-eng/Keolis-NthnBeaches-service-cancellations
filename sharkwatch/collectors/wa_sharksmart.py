"""WA SharkSmart — three public JSON feeds used by sharksmart.com.au/shark-activity:

  detection-feed  tagged-shark detections from the Shark Monitoring Network receivers
  twitter-feed    Water Police / SLSWA / helicopter sighting reports (the @SLSWA posts)
  alerts          official Shark ADVICE / WARNING notices

Detection posts also appear in twitter-feed; those are skipped there so nothing is
counted twice.
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from common import clean, event, get, make_id, save_raw

SOURCE = "wa_sharksmart"
TZ = ZoneInfo("Australia/Perth")
BASE = "https://www.sharksmart.com.au/shark-activity"
PAGE = BASE
FEEDS = {
    "detection": f"{BASE}/detection-feed?date_range=02",
    "twitter": f"{BASE}/twitter-feed?date_range=02",
    "alerts": f"{BASE}/alerts",
}
SPECIES = {"white": "White Shark", "tiger": "Tiger Shark", "bronze whaler": "Bronze Whaler",
           "dusky": "Dusky Whaler", "bull": "Bull Shark", "mako": "Mako Shark", "hammerhead": "Hammerhead",
           "grey nurse": "Grey Nurse Shark", "whaler": "Whaler", "wobbegong": "Wobbegong", "lemon": "Lemon Shark"}


def ts(v) -> str:
    try:
        return datetime.fromtimestamp(float(v), tz=timezone.utc).astimezone(TZ).isoformat()
    except (TypeError, ValueError):
        return clean(v)


def species_in(text: str) -> str:
    t = text.lower()
    if "unknown" in t:
        return "Unknown"
    for k, name in SPECIES.items():
        if k in t:
            return name
    return "Unknown"


def parse_detections(j) -> list[dict]:
    groups = j.get("feed", j) if isinstance(j, dict) else j
    if isinstance(groups, dict):
        groups = [groups]
    out = []
    for g in groups or []:
        if not isinstance(g, dict):
            continue
        lat, lon = g.get("latitude") or g.get("lat"), g.get("longitude") or g.get("lng")
        for item in g.get("list", []):
            text, info = (item[0], item[1]) if isinstance(item, list) and len(item) > 1 else (str(item), {})
            info = info if isinstance(info, dict) else {}
            sp = species_in(clean(info.get("species")) or text)
            out.append(event(
                SOURCE, "WA", "detection", ts(info.get("timestamp")), info.get("location") or "",
                lat=lat, lon=lon, species=sp, details=text,
                reported_by="WA Shark Monitoring Network", url=PAGE,
                id=make_id(SOURCE, "det", info.get("timestamp"), info.get("location"), sp),
            ))
    return out


SIGHT_RE = re.compile(
    r"^(?P<by>.*?)\s+report(?:s|ed)?\s+(?:a\s+)?(?:(?P<size>[\d.]+)\s*m\s+)?(?P<what>.*?)\s+shark\s+sighted"
    r"\s+at\s+\S+\s+[\d/]+,\s*(?P<loc>[^.]+)\.", re.I)


def parse_tweets(j) -> list[dict]:
    out = []
    for t in j or []:
        text = clean(re.sub(r"<[^>]+>", "", html.unescape(t.get("title") or "")))
        low = text.lower()
        if not text or ("tagged" in low and "detected" in low):  # detections come from detection-feed
            continue
        m = SIGHT_RE.search(text)
        kind = "tagging" if "caught &amp; released" in low or "caught & released" in low else "sighting" if "sight" in low else "closure" if "#closed" in low or "closed" in low[:20] else "alert"
        loc = m.group("loc").strip() if m else ""
        if not loc:  # e.g. "... sighted at 12:51hrs 18/11, Eleanor Rocks, Perth Metro."
            m2 = re.search(r"\d{1,2}/\d{1,2},\s*([^.]+)\.", text)
            loc = m2.group(1).strip() if m2 else ""
        by = (m.group("by") if m else "").strip()
        if "heli" in by.lower():
            by = "SLS helicopter"
        out.append(event(
            SOURCE, "WA", kind, ts(t.get("timestamp")), loc or text[:80],
            species=species_in(m.group("what") if m else text),
            size_m=m.group("size") if m and m.group("size") else "",
            reported_by=by or "SLSWA / Water Police", details=text, url=PAGE,
            id=make_id(SOURCE, "tw", t.get("activity_id") or text),
        ))
    return out


def parse_alerts(j) -> list[dict]:
    out = []
    for a in j or []:
        title = clean(a.get("facebook_title") or a.get("title"))
        if not title:
            continue
        loc = re.sub(r"^shark\s+(advice|warning|alert)\s+(for|at)\s+", "", title, flags=re.I)
        level = "warning" if "warning" in title.lower() else "advice"
        body = re.sub(r"<[^>]+>", " ", html.unescape(a.get("description") or ""))
        when = a.get("published_timestamp") or a.get("created_timestamp") or a.get("modified_timestamp")
        out.append(event(
            SOURCE, "WA", "alert", ts(when), loc, details=f"{level}: {clean(body)[:400]}",
            reported_by="WA DPIRD", url=clean(a.get("url")) or PAGE,
            id=make_id(SOURCE, "alert", title, a.get("id") or ""),
        ))
    return out


def collect() -> list[dict]:
    events = []
    for name, url in FEEDS.items():
        try:
            j = get(url, expect_file=True).json()
            save_raw(f"{SOURCE}_{name}.json", str(j)[:200000])
            found = {"detection": parse_detections, "twitter": parse_tweets, "alerts": parse_alerts}[name](j)
            print(f"[{SOURCE}] {name}: {len(found)}")
            events += found
        except Exception as e:  # noqa: BLE001
            print(f"[{SOURCE}] {name} failed: {e}")
    if not events:
        raise RuntimeError("all WA feeds failed")
    return events


if __name__ == "__main__":
    from common import merge
    print("added", merge(collect()))
