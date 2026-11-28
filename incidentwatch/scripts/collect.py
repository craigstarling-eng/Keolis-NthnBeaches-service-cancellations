"""Sydney Incident Watch collector.

Runs every 15 minutes (GitHub Actions). Pulls:
  - Police:  NSW Police media releases (police.nsw.gov.au/news)
  - Fire:    NSW RFS current incidents feed + Fire & Rescue NSW media releases
  - Road:    Live Traffic NSW incidents (TfNSW Open Data, needs TFNSW_API_KEY)
Keeps the last 14 days of individual incidents in data/events.json, archives
older ones to data/archive/YYYY-MM.json, and keeps daily counts per region
forever in data/stats.json.
"""
import base64, datetime as dt, hashlib, html, json, os, re, sys, urllib.request
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))
from regions import REGIONS

TZ = ZoneInfo("Australia/Sydney")
ROOT = os.path.join(os.path.dirname(__file__), "..")
DATA = os.path.join(ROOT, "data")
KEEP_DAYS = 14
UA = {"User-Agent": "SydneyIncidentWatch/1.0 (+https://github.com)"}

SUBURBS = json.load(open(os.path.join(DATA, "suburbs.json")))
# longest names first so "North Manly" wins over "Manly"
SUBURB_RE = re.compile(r"\b(" + "|".join(re.escape(s) for s in sorted(SUBURBS, key=len, reverse=True)) + r")\b", re.I)

NOW = dt.datetime.now(TZ)


def get(url, headers=None, timeout=40):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def locate(text, prefer=None):
    """Return (SUBURB, info) for the best suburb match in text, or (None, None)."""
    if prefer:
        key = prefer.strip().upper()
        if key in SUBURBS:
            return key, SUBURBS[key]
    m = SUBURB_RE.findall(text or "")
    if not m:
        return None, None
    key = m[-1].upper()  # place names are usually at the end of headlines
    return key, SUBURBS[key]


def event(cat, eid, typ, title, suburb, info, ts, url, src, **extra):
    e = {"id": eid, "cat": cat, "type": typ, "title": title.strip(),
         "suburb": suburb.title() if suburb else None,
         "region": info[0] if info else None, "council": info[1] if info else None,
         "ts": ts.astimezone(TZ).isoformat(timespec="minutes"), "url": url, "src": src}
    e.update({k: v for k, v in extra.items() if v not in (None, "", [])})
    return e


# ---------------------------------------------------------------- police
POLICE_TYPES = [
    ("Crash", r"crash|collision|pedestrian|rollover|struck by"),
    ("Missing person", r"missing|appeal to locate|located"),
    ("Domestic violence", r"\bdv\b|domestic"),
    ("Assault / violence", r"assault|stab|shoot|shot|murder|homicide|attack|siege|threaten|intimidat|affray|manslaughter|wound|firearm|weapon"),
    ("Robbery / break-in / theft", r"robbery|break(-| and )?(in|enter)|burglar|home invasion|theft|stolen|steal|ram raid|carjack"),
    ("Drugs", r"drug|cannabis|cocaine|meth|mdma|heroin|prohibited|cultivat"),
    ("Fire / arson", r"\bfire\b|blaze|set alight|arson|burn"),
    ("Driving offences", r"drink|drug driv|speed|pursuit|licence|rbt|dangerous driv|road rage|hoon|police chase"),
    ("Fraud / scams", r"fraud|scam|identity"),
]


def police_type(title):
    t = title.lower()
    for name, pat in POLICE_TYPES:
        if re.search(pat, t):
            return name
    return "Other"


def parse_when(text):
    text = text.strip().lower()
    m = re.match(r"(\d+|an?|one)\s+(minute|hour|day|week)s?\s+ago", text)
    if m:
        n = 1 if m.group(1) in ("a", "an", "one") else int(m.group(1))
        return NOW - dt.timedelta(**{m.group(2) + "s": n})
    if "just now" in text or "seconds ago" in text:
        return NOW
    m = re.search(r"(\d{1,2} \w+ \d{4} \d{1,2}:\d{2}(:\d{2})? ?[ap]m)", text)
    if m:
        for f in ("%d %B %Y %I:%M:%S %p", "%d %B %Y %I:%M %p"):
            try:
                return dt.datetime.strptime(m.group(1).upper(), f).replace(tzinfo=TZ)
            except ValueError:
                pass
    return None


def police():
    base = "https://www.police.nsw.gov.au"
    page = get(base + "/news")
    out = []
    for href, inner in re.findall(r'<a[^>]+href="([^"]*news_article[^"]*)"[^>]*>(.*?)</a>', page, re.S):
        href = html.unescape(href)
        if href.startswith("/"):
            href = base + href
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", inner))).strip()
        when = None
        m = re.match(r"((?:\d+|an?|one)\s+(?:minute|hour|day|week)s?\s+ago|just now)\s*(.*)", text, re.I)
        if m:
            when, title = parse_when(m.group(1)), m.group(2)
        else:
            m = re.match(r"(?:\w+day,\s*)?(\d{1,2} \w+ \d{4} \d{1,2}:\d{2}(?::\d{2})? ?[AP]M)\s*-?\s*(.*)", text, re.I)
            if m:
                when, title = parse_when(m.group(1)), m.group(2)
            else:
                title = text
        if not title:
            continue
        mid = None
        q = re.search(r"sq_content_src=([^&]+)", href)
        if q:
            try:
                raw = urllib.request.unquote(q.group(1)).lstrip("+")
                dec = base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8", "ignore")
                n = re.search(r"media%2F(\d+)|media/(\d+)", urllib.request.unquote(dec))
                mid = n and (n.group(1) or n.group(2))
            except Exception:
                pass
        eid = "police-" + (mid or hashlib.md5(title.encode()).hexdigest()[:10])
        tail = title.rsplit(" - ", 1)[1] if " - " in title else None
        sub, info = locate(title, prefer=tail)
        if not info:  # outside Greater Sydney
            continue
        out.append(event("police", eid, police_type(title), title, sub, info, when or NOW, href,
                         "NSW Police", charged=bool(re.search(r"charged|arrested|charges", title, re.I)) or None))
    return out


# ---------------------------------------------------------------- fire
def rfs():
    data = json.loads(get("https://www.rfs.nsw.gov.au/feeds/majorIncidents.json"))
    out = []
    for f in data.get("features", []):
        p = f.get("properties", {})
        desc = html.unescape(p.get("description", ""))
        fields = {}
        for part in re.split(r"<br\s*/?>", desc):
            if ":" in part:
                k, v = part.split(":", 1)
                fields[k.strip().upper()] = re.sub(r"<[^>]+>", "", v).strip()
        # Trust the council area first: titles can name places that share a Sydney suburb's name.
        from regions import region_for_lga, clean_lga
        council = fields.get("COUNCIL AREA", "")
        reg = region_for_lga(council)
        if not reg:
            continue
        loc = fields.get("LOCATION", "").split(",")[0].strip()
        sub = loc.upper() if loc.upper() in SUBURBS else None
        info = [reg, clean_lga(council)]
        try:
            when = dt.datetime.strptime(p.get("pubDate", ""), "%d/%m/%Y %I:%M:%S %p").replace(tzinfo=TZ)
        except ValueError:
            when = NOW
        lat = lng = None
        geom = f.get("geometry") or {}
        for g in geom.get("geometries", [geom]):
            if g.get("type") == "Point":
                lng, lat = g["coordinates"][:2]
                break
        typ = (fields.get("TYPE") or p.get("category") or "Fire").strip().capitalize()
        eid = "rfs-" + (re.sub(r"\D", "", p.get("guid", "")) or hashlib.md5(p.get("title", "").encode()).hexdigest()[:10])
        out.append(event("fire", eid, typ, p.get("title", "").title(), sub, info, when,
                         "https://www.rfs.nsw.gov.au/fire-information/fires-near-me",
                         fields.get("RESPONSIBLE AGENCY") or "NSW RFS",
                         status=fields.get("STATUS"), alert=p.get("category"), size=fields.get("SIZE"),
                         lat=lat, lng=lng))
    return out


def frnsw():
    rss = get("https://www.fire.nsw.gov.au/feeds/news.rss")
    out = []
    for item in re.findall(r"<item>(.*?)</item>", rss, re.S):
        title = html.unescape(re.sub(r"<!\[CDATA\[|\]\]>", "", (re.search(r"<title>(.*?)</title>", item, re.S) or [None, ""])[1])).strip()
        link = (re.search(r"<link>(.*?)</link>", item, re.S) or [None, ""])[1].strip()
        pub = (re.search(r"<pubDate>(.*?)</pubDate>", item, re.S) or [None, ""])[1].strip()
        sub, info = locate(title)
        if not info:
            continue
        try:
            when = dt.datetime.strptime(pub[:25].strip(), "%a, %d %b %Y %H:%M:%S")
            when = when.replace(tzinfo=dt.timezone.utc) if pub.endswith(("GMT", "+0000", "Z")) else when.replace(tzinfo=TZ)
        except ValueError:
            when = NOW
        t = title.lower()
        typ = ("Structure fire" if re.search(r"house|home|unit|apartment|building|factory|shed|garage|business", t)
               else "Vehicle fire" if re.search(r"car|vehicle|truck|bus", t)
               else "Hazmat" if re.search(r"hazmat|chemical|gas leak", t)
               else "Rescue" if "rescue" in t else "Fire")
        out.append(event("fire", "frnsw-" + hashlib.md5((link or title).encode()).hexdigest()[:10], typ, title, sub, info,
                         when, link, "Fire & Rescue NSW"))
    return out


# ---------------------------------------------------------------- road
def road():
    key = os.environ.get("TFNSW_API_KEY")
    if not key:
        raise RuntimeError("TFNSW_API_KEY not set")
    data = json.loads(get("https://api.transport.nsw.gov.au/v1/live/hazards/incident/open",
                          {"Authorization": "apikey " + key, "Accept": "application/json"}))
    out = []
    for f in data.get("features", []):
        p = f.get("properties", {})
        if str(p.get("incidentKind", "")).lower() == "planned":
            continue
        roads = p.get("roads") or [{}]
        r0 = roads[0] or {}
        sub, info = locate(r0.get("suburb", "") + " " + p.get("headline", ""), prefer=r0.get("suburb"))
        if not info:
            continue
        ms = p.get("created") or p.get("lastUpdated")
        when = dt.datetime.fromtimestamp(ms / 1000, TZ) if ms else NOW
        cat = (p.get("mainCategory") or p.get("displayName") or "Incident").strip().capitalize()
        street = ", ".join(x for x in [r0.get("mainStreet"), r0.get("crossStreet") and "near " + r0["crossStreet"]] if x)
        title = (p.get("displayName") or cat).strip().capitalize() + (" – " + street if street else "")
        lng, lat = ((f.get("geometry") or {}).get("coordinates") or [None, None])[:2]
        groups = p.get("attendingGroups") or []
        out.append(event("road", "road-" + str(f.get("id") or p.get("id") or hashlib.md5(title.encode()).hexdigest()[:10]),
                         cat, title, sub, info, when, "https://www.livetraffic.com", "Live Traffic NSW",
                         major=bool(p.get("isMajor")) or None, attending=groups, lat=lat, lng=lng))
    return out


# ---------------------------------------------------------------- store
def load(name, default):
    try:
        return json.load(open(os.path.join(DATA, name)))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save(name, obj):
    path = os.path.join(DATA, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    json.dump(obj, open(tmp, "w"), separators=(",", ":"), ensure_ascii=False)
    os.replace(tmp, path)


def build_stats(events, stats):
    days = {}
    for e in events:
        if not e.get("region"):
            continue
        d = e["ts"][:10]
        for reg in (e["region"], "all"):
            c = days.setdefault(reg, {}).setdefault(d, {})
            k = c.setdefault(e["cat"], {"n": 0, "types": {}})
            k["n"] += 1
            k["types"][e["type"]] = k["types"].get(e["type"], 0) + 1
            if e.get("charged"):
                k["charged"] = k.get("charged", 0) + 1
            if e["cat"] == "road" and e.get("major"):
                k["major"] = k.get("major", 0) + 1
    oldest = min((e["ts"][:10] for e in events), default=None)
    for reg in list(REGIONS) + ["all"]:
        s = stats.setdefault(reg, {})
        if oldest:  # recompute the window we still hold events for
            for d in [d for d in s if d >= oldest]:
                del s[d]
        s.update(days.get(reg, {}))
    return stats


def main():
    events = {e["id"]: e for e in load("events.json", [])}
    status = load("status.json", {})
    for name, fn in (("police", police), ("rfs", rfs), ("frnsw", frnsw), ("road", road)):
        try:
            got = fn()
            new = 0
            for e in got:
                if e["id"] in events:
                    old = events[e["id"]]  # keep first-seen time; refresh status fields
                    for k in ("status", "alert", "size", "attending", "major"):
                        if k in e:
                            old[k] = e[k]
                else:
                    events[e["id"]] = e
                    new += 1
            status[name] = {"ok": NOW.isoformat(timespec="minutes"), "seen": len(got), "new": new}
            print(f"{name}: {len(got)} in Sydney, {new} new")
        except Exception as ex:  # one source failing must not stop the others
            status.setdefault(name, {})["error"] = f"{NOW.isoformat(timespec='minutes')} {type(ex).__name__}: {ex}"[:300]
            print(f"{name}: FAILED {ex}", file=sys.stderr)

    cutoff = (NOW - dt.timedelta(days=KEEP_DAYS)).date().isoformat()
    keep, old = [], {}
    for e in sorted(events.values(), key=lambda e: e["ts"], reverse=True):
        (keep.append(e) if e["ts"][:10] >= cutoff else old.setdefault(e["ts"][:7], []).append(e))
    for month, evs in old.items():
        arch = load(f"archive/{month}.json", [])
        ids = {a["id"] for a in arch}
        save(f"archive/{month}.json", arch + [e for e in evs if e["id"] not in ids])

    save("events.json", keep)
    save("stats.json", build_stats(keep, load("stats.json", {})))
    status["updated"] = NOW.isoformat(timespec="minutes")
    status["regions"] = {k: v[0] for k, v in REGIONS.items()}
    save("status.json", status)


if __name__ == "__main__":
    main()
