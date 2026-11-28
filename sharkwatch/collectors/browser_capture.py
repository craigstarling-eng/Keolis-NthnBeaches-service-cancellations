"""Headless-browser capture for map apps with no published API (NSW + WA SharkSmart).

How it works:
 1. Opens the official page in headless Chromium (Playwright), clicks "open map" if needed.
 2. Records every JSON response the page (and its map iframe) loads.
 3. Saves them to data/raw/<source>_capture.json and lists the endpoints in
    data/raw/<source>_endpoints.json  -> after the first run you can see the real API.
 4. Walks the JSON for records that look like shark events (have coordinates or a
    date + text) and maps them into the shared schema.

If the site changes, step 3 still records what it loaded, so fixing the mapping
is a quick job rather than starting over.
"""
from __future__ import annotations

import json
import re

from common import clean, event, make_id, save_raw, write_json, RAW

LAT_KEYS = ("lat", "latitude", "y")
LON_KEYS = ("lon", "lng", "long", "longitude", "x")
DATE_KEYS = ("date", "datetime", "time", "timestamp", "detected", "created", "published", "occurred", "reported", "start")
TEXT_KEYS = ("title", "headline", "name", "message", "description", "body", "summary", "text", "content", "details")
LOC_KEYS = ("beach", "location", "locationname", "place", "site", "receiver", "station", "suburb", "area", "region")
SPECIES_KEYS = ("species", "sharktype", "shark_type", "sharkspecies", "type_of_shark", "commonname")
TYPE_KEYS = ("type", "alerttype", "alert_type", "category", "kind", "eventtype", "source")


def _find(d: dict, keys, exclude=(), exact=False) -> str:
    low = {str(k).lower().replace("-", "").replace(" ", "").replace("_", ""): v for k, v in d.items()}
    for k in keys:
        k = k.replace("_", "")
        for lk, v in low.items():
            if lk in exclude or isinstance(v, (dict, list)) or v in (None, ""):
                continue
            if lk == k or (not exact and (lk.endswith(k) or lk.startswith(k))):
                return clean(v)
    return ""


def _coords(d: dict):
    lat, lon = _find(d, LAT_KEYS, exact=True), _find(d, LON_KEYS, exact=True)
    geo = next((d[k] for k in ("geometry", "location", "position", "coords") if isinstance(d.get(k), dict)), None)
    if (not lat or not lon) and geo:
        cs = geo.get("coordinates")
        if isinstance(cs, list) and len(cs) >= 2 and all(isinstance(x, (int, float)) for x in cs[:2]):
            lon, lat = str(cs[0]), str(cs[1])
        else:
            lat, lon = lat or _find(geo, LAT_KEYS, exact=True), lon or _find(geo, LON_KEYS, exact=True)
    try:
        la, lo = float(lat), float(lon)
        if -45 < la < -9 and 110 < lo < 155:  # inside Australia
            return str(la), str(lo)
    except (TypeError, ValueError):
        pass
    return "", ""


def _to_iso(v: str) -> str:
    """Epoch seconds/ms -> ISO (UTC); anything else returned unchanged."""
    if re.fullmatch(r"\d{10}(\d{3})?(\.\d+)?", v or ""):
        from datetime import datetime, timezone
        n = float(v)
        n = n / 1000 if n > 1e11 else n
        return datetime.fromtimestamp(n, tz=timezone.utc).isoformat(timespec="seconds")
    return v


def classify(text: str) -> str:
    t = text.lower()
    for word, kind in (("drone", "drone"), ("detect", "detection"), ("receiver", "detection"),
                       ("listening station", "detection"), ("tagged and released", "tagging"),
                       ("drumline", "tagging"), ("bite", "incident"), ("interaction", "incident"),
                       ("incident", "incident"), ("sight", "sighting"), ("spotted", "sighting")):
        if word in t:
            return kind
    return "alert"


def _walk(obj, out: list):
    if isinstance(obj, dict):
        props = obj.get("properties") if isinstance(obj.get("properties"), dict) else None
        rec = {**obj, **props} if props else obj
        out.append(rec)
        for v in obj.values():
            if isinstance(v, (dict, list)):
                _walk(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, out)


def extract(payloads: list[dict], source: str, state: str, page_url: str) -> list[dict]:
    events, seen = [], set()
    for p in payloads:
        cands: list[dict] = []
        _walk(p["json"], cands)
        for d in cands:
            lat, lon = _coords(d)
            when = _to_iso(_find(d, DATE_KEYS, exclude=("timezone",)))
            text = _find(d, TEXT_KEYS)
            if not ((lat and (when or text)) or (when and text)):
                continue
            if not re.search(r"\d{4}|\d{10}", when or ""):  # needs something date-like
                if not lat:
                    continue
            loc = _find(d, LOC_KEYS) or text[:80]
            species = _find(d, SPECIES_KEYS)
            kind_hint = " ".join([_find(d, TYPE_KEYS), text, loc])
            raw_id = _find(d, ("id", "uid", "guid", "alertid", "objectid"))
            e = event(
                source, state, classify(kind_hint), when, loc, lat=lat, lon=lon,
                species=species or ("White Shark" if "white" in text.lower() else
                                    "Bull Shark" if "bull" in text.lower() else
                                    "Tiger Shark" if "tiger" in text.lower() else ""),
                details=text[:400], url=page_url,
                id=make_id(source, raw_id) if raw_id else None,
            )
            if e["id"] not in seen:
                seen.add(e["id"])
                events.append(e)
    return events


# Responses that are never shark events (map tiles, social widgets, signage layers)
SKIP_URL_PARTS = ("googleapis", "gstatic", "twitter.com/settings", "syndication.twitter", "facebook",
                  "doubleclick", "google-analytics", "googletagmanager", "hotjar", "/ben-signs",
                  "/safety-layers", "mapbox", "arcgisonline", "openstreetmap", "sentry")


def wanted(url: str, hosts) -> bool:
    u = url.lower()
    if any(p in u for p in SKIP_URL_PARTS):
        return False
    return not hosts or any(h in u for h in hosts)


def capture(source: str, page_url: str, click_texts=("Open shark map",), wait_ms=15000,
            hosts=()) -> list[dict]:
    """Return list of {"url", "json"} for every relevant JSON response the page loads."""
    from playwright.sync_api import sync_playwright

    payloads: list[dict] = []

    def on_response(resp):
        ct = (resp.headers.get("content-type") or "").lower()
        if "json" not in ct and not resp.url.split("?")[0].endswith((".json", ".geojson")):
            return
        if not wanted(resp.url, hosts):
            return
        try:
            payloads.append({"url": resp.url, "status": resp.status, "json": resp.json()})
        except Exception:  # noqa: BLE001
            pass

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1400, "height": 1000}, locale="en-AU",
                                  timezone_id="Australia/Sydney")
        ctx.on("response", on_response)
        page = ctx.new_page()
        page.goto(page_url, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(5000)
        for label in click_texts:
            try:
                page.get_by_text(label, exact=False).first.click(timeout=5000)
            except Exception:  # noqa: BLE001
                pass
        page.wait_for_timeout(wait_ms)
        iframes = [f.url for f in page.frames if f.url and f.url != page_url and wanted(f.url, ())]
        save_raw(f"{source}_page.txt", page.inner_text("body"))
        browser.close()

    write_json(RAW / f"{source}_capture.json", payloads)
    write_json(RAW / f"{source}_endpoints.json", {
        "page": page_url,
        "iframes": iframes,
        "json_endpoints": [
            {"url": p["url"], "status": p["status"],
             "top_keys": list(p["json"].keys())[:15] if isinstance(p["json"], dict) else f"list[{len(p['json'])}]"}
            for p in payloads
        ],
    })
    print(f"[{source}] captured {len(payloads)} JSON responses; iframes: {iframes}")
    return payloads


def collect(source: str, state: str, page_url: str, **kw) -> list[dict]:
    try:
        payloads = capture(source, page_url, **kw)
    except Exception as e:  # noqa: BLE001
        print(f"[{source}] capture failed: {e}")
        return []
    events = extract(payloads, source, state, page_url)
    print(f"[{source}] {len(events)} events")
    return events


if __name__ == "__main__":
    import sys
    data = json.loads(open(sys.argv[1]).read())
    for e in extract(data, "test", "NSW", "local")[:20]:
        print(e)
