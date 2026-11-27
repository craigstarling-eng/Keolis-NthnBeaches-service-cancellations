#!/usr/bin/env python3
"""Northern Beaches bus service tracker (Keolis Northern Beaches, Sydney Region 8).

Runs every ~10 minutes on GitHub Actions. Each run:
  1. Keeps timetables cached (Region 8 in full, other Sydney regions for comparison).
  2. Reads the live TfNSW bus trip-updates feed (all Sydney buses).
  3. Records cancelled trips and how late running trips are.
  4. Writes public data files in data/ for the dashboard and for download.

Working state (timetables, today's per-trip delays) lives in cache/, which the
workflow keeps between runs with actions/cache. It is not published.
"""
import csv
import datetime as dt
import gzip
import io
import json
import os
import re
import statistics
import sys
import zipfile
from collections import Counter, defaultdict
from zoneinfo import ZoneInfo

import requests
from google.transit import gtfs_realtime_pb2

API = "https://api.transport.nsw.gov.au/v1/gtfs"
SYD = ZoneInfo("Australia/Sydney")
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
HIST = os.path.join(DATA, "history")
CACHE = os.path.join(HERE, "cache")
STATIC_DIR = os.path.join(CACHE, "static")
STATE_DIR = os.path.join(CACHE, "state")

HOME_CODE = "GSBC008"                     # Keolis Northern Beaches
CROSS_CODE = "GSBC014"                    # CDC NSW (Forest district): only its routes through the Northern Beaches are tracked
DETAILED = {HOME_CODE, CROSS_CODE}
NB_SUBURBS = {s.lower() for s in (
    "Allambie Heights", "Avalon Beach", "Avalon", "Balgowlah", "Balgowlah Heights", "Bayview", "Beacon Hill", "Belrose",
    "Bilgola Beach", "Bilgola Plateau", "Bilgola", "Brookvale", "Church Point", "Clareville", "Clontarf", "Collaroy",
    "Collaroy Plateau", "Cromer", "Curl Curl", "North Curl Curl", "South Curl Curl", "Davidson", "Dee Why",
    "Duffys Forest", "Elanora Heights", "Fairlight", "Forestville", "Frenchs Forest", "Freshwater", "Ingleside",
    "Killarney Heights", "Manly", "Manly Vale", "Mona Vale", "Narrabeen", "North Narrabeen", "Narraweena", "Newport",
    "North Balgowlah", "North Manly", "Oxford Falls", "Palm Beach", "Queenscliff", "Seaforth", "Terrey Hills",
    "Warriewood", "Wheeler Heights", "Whale Beach", "Cottage Point")}
REGION_CODES = [f"GSBC{n:03d}" for n in range(1, 16)] + ["SBSC006"]
HOME_MAX_AGE_DAYS = 3
OTHER_MAX_AGE_DAYS = 7
MAX_REGION_DOWNLOADS_PER_RUN = 3
LATE_SECONDS = 300                        # "late" = more than 5 minutes behind
MAX_PLAUSIBLE_DELAY = 90 * 60             # readings beyond 90 min late are treated as feed glitches and ignored
EARLY_SECONDS = -60                       # TfNSW on-time window: no more than 1 min early, 5 min late
AM_PEAK = range(6, 9)                     # trips starting 06:00-08:59
PM_PEAK = range(16, 19)                   # trips starting 16:00-18:59
SCHEDULE_DAYS_BACK, SCHEDULE_DAYS_AHEAD = 2, 14
STATIC_VERSION = 5                        # bump to force timetables to reload
SCHOOL_ROUTE_TYPES = {"712"}              # GTFS extended type: school bus
SCHOOL_NAME = re.compile(r"^\d+n$")        # TfNSW school services are numbered like 560n


def is_school_route(route):
    """School services are excluded from all statistics."""
    return route.get("route_type", "") in SCHOOL_ROUTE_TYPES or bool(SCHOOL_NAME.match(route.get("route_short_name", "").strip()))

CXL_LOG = os.path.join(DATA, "cancellations.csv")
CDC_CXL_LOG = os.path.join(DATA, "cdc_cancellations.csv")
CDC_FILE = os.path.join(DATA, "cdc.json")
CXL_FIELDS = ["service_date", "trip_id", "route", "start_time", "headsign", "first_seen", "school", "reinstated"]
# Days when tracking began part-way through: only trips starting after this time are counted,
# so the rate isn't diluted by trips we never had the chance to see.
PARTIAL_DAYS = {"2026-11-26": "12:28"}
R8_DAILY = os.path.join(HIST, "r8_daily.csv")
R8_DAILY_FIELDS = ["date", "scheduled", "cancelled", "observed", "late",
                   "am_scheduled", "am_cancelled", "pm_scheduled", "pm_cancelled",
                   "longest_gap_min", "longest_gap_route",
                   "am_observed", "am_late", "pm_observed", "pm_late", "late_minutes",
                   "ontime", "anywhere_observed", "anywhere_late"]
R8_ROUTES = os.path.join(HIST, "r8_routes_daily.csv")
R8_ROUTES_FIELDS = ["date", "route", "scheduled", "cancelled", "observed", "late", "ontime"]
REGIONS_DAILY = os.path.join(HIST, "regions_daily.csv")
CDC_DAILY = os.path.join(HIST, "cdc_nb_daily.csv")
CDC_DAILY_FIELDS = ["date", "scheduled", "cancelled", "observed", "late", "ontime", "longest_gap_min"]
CDC_ROUTES = os.path.join(HIST, "cdc_nb_routes_daily.csv")
CDC_ROUTES_FIELDS = ["date", "route", "scheduled", "cancelled", "observed", "late", "ontime"]
CDC_START = os.path.join(HIST, "cdc_tracking_start.txt")
TIMETABLE = os.path.join(DATA, "timetable_today.json")
TRIP_STATS = os.path.join(DATA, "trip_stats.json")
TRIPS_HIST = os.path.join(HIST, "trips")
TRIP_STATS_DAYS = 30
CROWDED = 3                               # GTFS occupancy: 3 = standing room only, 4 = crushed, 5 = full
REGIONS_FIELDS = ["date", "code", "name", "scheduled", "cancelled", "observed", "late", "ontime"]


# ---------------------------------------------------------------- helpers
def now_syd():
    return dt.datetime.now(SYD)


def headers():
    key = os.environ.get("TFNSW_API_KEY", "").strip()
    if not key:
        sys.exit("TFNSW_API_KEY is not set (add it as a GitHub secret).")
    return {"Authorization": f"apikey {key}"}


def read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path, obj, pretty=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=1 if pretty else None, separators=None if pretty else (",", ":"))


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path, fields, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def upsert_csv(path, fields, new_rows, key_fields, keep_max=()):
    """Replace rows sharing a key; for keep_max fields keep the larger value
    (protects history if the working cache was lost mid-day)."""
    existing = {tuple(r[k] for k in key_fields): r for r in read_csv(path)}
    for r in new_rows:
        k = tuple(str(r[f]) for f in key_fields)
        old = existing.get(k)
        if old:
            for f in keep_max:
                try:
                    r[f] = max(int(r[f]), int(old.get(f) or 0))
                except (TypeError, ValueError):
                    pass
        existing[k] = {f: r.get(f, "") for f in fields}
    rows = sorted(existing.values(), key=lambda r: tuple(r[k] for k in key_fields))
    write_csv(path, fields, rows)
    return rows


def hhmm_to_min(t):
    try:
        h, m = t.split(":")[:2]
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def min_to_hhmm(m):
    return f"{(m // 60) % 24:02d}:{m % 60:02d}"


def iso(d):
    return d.isoformat()


# ---------------------------------------------------------------- timetables
def gtfs_zip(hdr, code):
    r = requests.get(f"{API}/schedule/buses/{code}", headers=hdr, timeout=600)
    if r.status_code in (400, 404):
        return None
    r.raise_for_status()
    return zipfile.ZipFile(io.BytesIO(r.content))


def zrows(z, name):
    if name not in z.namelist():
        return
    with z.open(name) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, "utf-8-sig"))


def service_dates(z, days):
    """Return {service_id: set of ISO dates it runs} for the given days."""
    active = defaultdict(set)
    wd = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for c in zrows(z, "calendar.txt"):
        try:
            start = dt.datetime.strptime(c["start_date"], "%Y%m%d").date()
            end = dt.datetime.strptime(c["end_date"], "%Y%m%d").date()
        except (KeyError, ValueError):
            continue
        for d in days:
            if start <= d <= end and c.get(wd[d.weekday()]) == "1":
                active[c["service_id"]].add(iso(d))
    wanted = {iso(d) for d in days}
    for c in zrows(z, "calendar_dates.txt"):
        try:
            d = dt.datetime.strptime(c["date"], "%Y%m%d").date().isoformat()
        except (KeyError, ValueError):
            continue
        if d not in wanted:
            continue
        if c.get("exception_type") == "1":
            active[c["service_id"]].add(d)
        elif c.get("exception_type") == "2":
            active[c["service_id"]].discard(d)
    return active


def schedule_window():
    today = now_syd().date()
    return [today + dt.timedelta(days=i) for i in range(-SCHEDULE_DAYS_BACK, SCHEDULE_DAYS_AHEAD + 1)]


# ---------------------------------------------------------------- school bus finder data
SCHOOL_FILE = os.path.join(DATA, "school.json")
SCHOOL_WORDS = re.compile(r"\b(school|college|grammar|campus|high|ps|hs|nbsc|academy|steiner|montessori|"
                          r"(public|primary|catholic|anglican|christian)\s+(school|primary|college))\b|\bprimary$|\bhigh$", re.I)
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def encode_polyline(points):
    """Google encoded polyline (precision 5)."""
    out, plat, plon = [], 0, 0
    for lat, lon in points:
        ilat, ilon = round(lat * 1e5), round(lon * 1e5)
        for v in (ilat - plat, ilon - plon):
            v = ~(v << 1) if v < 0 else v << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1f)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        plat, plon = ilat, ilon
    return "".join(out)


def thin(points, max_points=180):
    if len(points) <= max_points:
        return points
    step = (len(points) - 1) / (max_points - 1)
    return [points[round(i * step)] for i in range(max_points)]


def school_key(name):
    """School name from a stop or headsign, e.g. 'Military Rd opp Mosman High School, Mosman' -> 'Mosman High School'."""
    base = re.split(r"\s+(?:opp|at|near|before|after|outside|nr)\s+", name.split(",")[0].strip())[-1].strip()
    if not SCHOOL_WORDS.search(base) or re.search(r"\b(st|rd|ave|street|road|church)$", base, re.I):
        return None
    return base


def school_canon(name):
    """Comparison key that treats 'Dee Why PS', 'Dee Why Public School' and 'St. Kevins Primary' style variants as one."""
    k = re.sub(r"['.]", "", name.lower())
    k = re.sub(r"\s*(&|-)\s*infants$", "", k)
    k = re.sub(r"\bps$", "public school", k)
    k = re.sub(r"\bhs$", "high school", k)
    k = re.sub(r"\b(high|primary|grammar)$", r"\1 school", k)
    k = k.replace("narabeen", "narrabeen")
    k = {"north curl curl public school": "curl curl north public school",
         "north narrabeen public school": "narrabeen north public school"}.get(k, k)
    return re.sub(r"\s+", " ", k).strip()


def tidy_schools(raw):
    """Merge name variants; show the fullest spelling."""
    groups = defaultdict(lambda: {"names": [], "stops": set()})
    for n, st in raw.items():
        g = groups[school_canon(n)]
        g["names"].append(n)
        g["stops"] |= st
    for k in list(groups):  # 'X Primary School' with no other match joins 'X Public School'
        if k.endswith(" primary school"):
            alt = k[:-len("primary school")] + "public school"
            if alt in groups and k in groups:
                groups[alt]["names"] += groups[k]["names"]
                groups[alt]["stops"] |= groups.pop(k)["stops"]
    out = {}
    for k, g in groups.items():
        best = max(g["names"], key=lambda n: (not re.search(r"\b(PS|HS|Ps)\b", n), "School" in n or "College" in n, len(n)))
        best = re.sub(r"\bPS$", "Public School", re.sub(r"\bHS$", "High School", best)).replace("Narabeen", "Narrabeen")
        out[best] = g["stops"]
    return out


def build_school(z, school_routes, routes):
    """All school services in the region, with stops, times and route shapes, for the School bus tab."""
    if not school_routes:
        return
    trips, days_by_service = {}, {}
    for c in zrows(z, "calendar.txt"):
        days_by_service[c["service_id"]] = [d for d, k in zip(DAY_NAMES, ("monday", "tuesday", "wednesday", "thursday",
                                                                            "friday", "saturday", "sunday")) if c.get(k) == "1"]
    for t in zrows(z, "trips.txt"):
        if t["route_id"] in school_routes:
            trips[t["trip_id"]] = {"route": routes.get(t["route_id"], t["route_id"]), "headsign": t.get("trip_headsign", "").strip(),
                                   "shape": t.get("shape_id", ""), "service": t["service_id"], "stops": []}
    for st in zrows(z, "stop_times.txt"):
        tr = trips.get(st["trip_id"])
        if tr is None:
            continue
        m = hhmm_to_min((st.get("departure_time") or st.get("arrival_time") or "")[:5])
        try:
            tr["stops"].append((int(st["stop_sequence"]), st["stop_id"], m))
        except ValueError:
            pass
    used_stops = {sid for tr in trips.values() for _, sid, _ in tr["stops"]}
    stops = {}
    for x in zrows(z, "stops.txt"):
        if x["stop_id"] in used_stops:
            try:
                stops[x["stop_id"]] = [(x.get("stop_name") or "").strip(), round(float(x["stop_lat"]), 5), round(float(x["stop_lon"]), 5)]
            except (ValueError, KeyError):
                pass
    stop_ids = sorted(stops)
    sidx = {sid: i for i, sid in enumerate(stop_ids)}
    shapes_wanted = {tr["shape"] for tr in trips.values() if tr["shape"]}
    shape_pts = defaultdict(list)
    for p in zrows(z, "shapes.txt"):
        if p["shape_id"] in shapes_wanted:
            try:
                shape_pts[p["shape_id"]].append((int(p["shape_pt_sequence"]), float(p["shape_pt_lat"]), float(p["shape_pt_lon"])))
            except (ValueError, KeyError):
                pass
    shape_list, shape_idx = [], {}
    for sid_, pts in shape_pts.items():
        pts.sort()
        shape_idx[sid_] = len(shape_list)
        shape_list.append(encode_polyline(thin([(a, b) for _, a, b in pts])))
    # de-duplicate identical services (same route, stops and times) running on different days
    unique = {}
    for tr in trips.values():
        tr["stops"].sort()
        seq = tuple((sidx[sid], m) for _, sid, m in tr["stops"] if sid in sidx and m is not None)
        if len(seq) < 2:
            continue
        key = (tr["route"], tr["headsign"], seq)
        u = unique.setdefault(key, {"days": set(), "shape": shape_idx.get(tr["shape"], -1)})
        u["days"].update(days_by_service.get(tr["service"], []))
    out_trips = []
    for (route, hs, seq), u in sorted(unique.items(), key=lambda kv: (kv[0][2][0][1], kv[0][0])):
        days = [d for d in DAY_NAMES if d in u["days"]]
        label = "School days" if days == DAY_NAMES[:5] or not days else " ".join(days)
        out_trips.append([route, hs, u["shape"], [list(x) for x in seq], label])
    # schools: stops whose names mention a school, plus each trip's school-end stop when the headsign names a school
    schools = defaultdict(set)
    for sid, (name, _, _) in stops.items():
        k = school_key(name)
        if k:
            schools[k].add(sidx[sid])
    for route, hs, _, seq, _ in out_trips:
        k = school_key(hs)  # headsign is the destination, so the school is the last stop
        if k:
            schools[k].add(seq[-1][0])
    schools = tidy_schools(schools)
    write_json(SCHOOL_FILE, {"built": iso(now_syd().date()), "stops": [stops[s] for s in stop_ids],
                             "schools": {k: sorted(v) for k, v in sorted(schools.items())},
                             "trips": out_trips, "shapes": shape_list})
    print(f"  school finder: {len(out_trips)} school services, {len(schools)} schools")


def nb_stop(name):
    """True if a TfNSW stop name ('X Rd after Y St, Suburb') is in the Northern Beaches council area."""
    return name.rsplit(",", 1)[-1].strip().lower() in NB_SUBURBS if "," in name else False


def build_region(hdr, code, detailed):
    print(f"Downloading timetable {code}{' (detailed)' if detailed else ''}...")
    z = gtfs_zip(hdr, code)
    if z is None:
        return {"code": code, "missing": True, "built": now_syd().isoformat(timespec="seconds")}
    agencies = {a["agency_id"]: a.get("agency_name", "") for a in zrows(z, "agency.txt")}
    routes, school = {}, []
    for r in zrows(z, "routes.txt"):
        routes[r["route_id"]] = (r.get("route_short_name") or r["route_id"]).strip()
        if is_school_route(r):
            school.append(r["route_id"])
    school_set = set(school)
    active = service_dates(z, schedule_window())
    per_date = Counter()
    trips = {}
    for t in zrows(z, "trips.txt"):
        if t["route_id"] not in routes:
            continue
        if t["route_id"] not in school_set:
            for d in active.get(t["service_id"], ()):
                per_date[d] += 1
        if detailed:
            trips[t["trip_id"]] = [t["route_id"], t.get("trip_headsign", "").strip(), "", t["service_id"]]
    out = {
        "code": code,
        "v": STATIC_VERSION,
        "built": now_syd().isoformat(timespec="seconds"),
        "agencies": agencies,
        "school_routes": school,
        "name": " / ".join(sorted(set(agencies.values()))) or code,
        "scheduled_by_date": dict(per_date),
    }
    if detailed:
        first, nb_routes = {}, set()
        all_stops = {x["stop_id"]: (x.get("stop_name") or "").strip() for x in zrows(z, "stops.txt")}
        nb_stops = {sid for sid, nm in all_stops.items() if nb_stop(nm)}
        for st in zrows(z, "stop_times.txt"):
            tid = st["trip_id"]
            if tid not in trips:
                continue
            try:
                seq = int(st["stop_sequence"])
            except ValueError:
                continue
            if tid not in first or seq < first[tid][0]:
                first[tid] = (seq, st.get("departure_time") or st.get("arrival_time") or "", st.get("stop_id", ""))
            if st.get("stop_id") in nb_stops:
                nb_routes.add(trips[tid][0])
        stop_names = all_stops
        for tid, (_, t, sid_) in first.items():
            trips[tid][2] = t[:5]
            trips[tid].append(stop_names.get(sid_, ""))
        out["routes"] = routes
        out["trips"] = trips
        out["services"] = {k: sorted(v) for k, v in active.items()}
        out["nb_routes"] = sorted(nb_routes - school_set)
        if code == HOME_CODE:
            try:
                build_school(z, school_set, routes)
            except Exception as exc:  # the school finder is a bonus; never break tracking
                print("school data not built:", exc)
    print(f"  {out['name']}: {len(routes)} routes" + (f", {len(out.get('nb_routes', []))} through the Northern Beaches" if detailed else ""))
    return out


def load_regions(hdr):
    os.makedirs(STATIC_DIR, exist_ok=True)
    regions, downloads = {}, 0
    for code in [HOME_CODE] + [c for c in REGION_CODES if c != HOME_CODE]:
        path = os.path.join(STATIC_DIR, f"{code}.json")
        cached = read_json(path, None)
        home = code == HOME_CODE
        detailed = code in DETAILED
        max_age = HOME_MAX_AGE_DAYS if detailed else OTHER_MAX_AGE_DAYS
        stale = True
        if cached:
            try:
                stale = (cached.get("v") != STATIC_VERSION or (detailed and "trips" not in cached) or
                         (now_syd() - dt.datetime.fromisoformat(cached["built"])).days >= max_age)
            except (KeyError, ValueError):
                pass
        if stale and (detailed or downloads < MAX_REGION_DOWNLOADS_PER_RUN):
            try:
                fresh = build_region(hdr, code, detailed=detailed)
                write_json(path, fresh)
                cached = fresh
                if not home:
                    downloads += 1
            except Exception as exc:  # keep going with whatever we had
                print(f"  could not refresh {code}: {exc}")
                if home and not cached:
                    raise
        if cached and not cached.get("missing"):
            regions[code] = cached
    return regions


# ---------------------------------------------------------------- realtime
def trip_delay(tu):
    if tu.HasField("delay"):
        return tu.delay
    for stu in tu.stop_time_update:
        if stu.HasField("departure") and stu.departure.HasField("delay"):
            return stu.departure.delay
        if stu.HasField("arrival") and stu.arrival.HasField("delay"):
            return stu.arrival.delay
    return None


def trip_start_delay(tu):
    """Delay leaving the first stop (stop_sequence 1), as Transport for NSW measures on-time running."""
    for stu in tu.stop_time_update:
        if stu.stop_sequence == 1:
            if stu.HasField("departure") and stu.departure.HasField("delay"):
                return stu.departure.delay
            if stu.HasField("arrival") and stu.arrival.HasField("delay"):
                return stu.arrival.delay
    return None


def service_date(feed_date, feed_start, static_start, today):
    """After-midnight trips (timetabled 24:xx+) belong to the previous day's service; the feed
    sometimes labels them with the calendar date instead."""
    sdate = feed_date or today
    feed_hour = int(feed_start[:2]) if feed_start[:2].isdigit() else None
    if static_start[:2].isdigit() and int(static_start[:2]) >= 24 and (
            (feed_hour is not None and feed_hour < 4) or
            (feed_hour is None and sdate == today and now_syd().hour < 8)):
        sdate = (dt.datetime.strptime(sdate, "%Y%m%d") - dt.timedelta(days=1)).strftime("%Y%m%d")
    return f"{sdate[:4]}-{sdate[4:6]}-{sdate[6:8]}"


def read_occupancy(hdr, home):
    """How full each Northern Beaches bus is right now (where the bus reports it)."""
    try:
        r = requests.get(f"{API}/vehiclepos/buses", headers=hdr, timeout=120)
        r.raise_for_status()
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(r.content)
    except Exception as exc:
        print("occupancy feed unavailable:", exc)
        return {}
    today = now_syd().strftime("%Y%m%d")
    out = {}
    for e in feed.entity:
        if not e.HasField("vehicle"):
            continue
        v = e.vehicle
        info = home["trips"].get(v.trip.trip_id)
        if not info or not v.HasField("occupancy_status"):
            continue
        d = service_date(v.trip.start_date, v.trip.start_time, info[2], today)
        out[(d, v.trip.trip_id)] = int(v.occupancy_status)
    return out


def read_feed(hdr, prefix_to_code, home, school_routes, cross=None):
    r = requests.get(f"{API}/realtime/buses", headers=hdr, timeout=120)
    r.raise_for_status()
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(r.content)
    CANCELED = gtfs_realtime_pb2.TripDescriptor.CANCELED
    today = now_syd().strftime("%Y%m%d")
    home_trips, home_routes = home["trips"], home["routes"]
    cross_routes = (cross or {}).get("routes", {})
    cross_trips = (cross or {}).get("trips", {})
    seen = []
    for e in feed.entity:
        if not e.HasField("trip_update"):
            continue
        tu = e.trip_update
        td = tu.trip
        info = home_trips.get(td.trip_id)
        rid = td.route_id or (info[0] if info else "")
        if rid and rid in cross_routes and rid not in home_routes:
            info = cross_trips.get(td.trip_id)
            code = CROSS_CODE
        else:
            prefix = rid.split("_")[0] if "_" in rid else ""
            code = HOME_CODE if (info or rid in home_routes) else prefix_to_code.get(prefix, f"agency:{prefix}" if prefix else "")
        if not code:
            continue
        static_start = info[2] if info else ""
        sdate = service_date(td.start_date, td.start_time, static_start, today).replace("-", "")
        seen.append({
            "code": code,
            "date": f"{sdate[:4]}-{sdate[4:6]}-{sdate[6:8]}",
            "trip_id": td.trip_id,
            "route_id": rid,
            "cancelled": td.schedule_relationship == CANCELED,
            "school": rid in school_routes,
            "delay": trip_delay(tu),
            "start_delay": trip_start_delay(tu),
            "start_time": (static_start or td.start_time)[:5],
        })
    return seen


# ---------------------------------------------------------------- state
def load_state(date):
    return read_json(os.path.join(STATE_DIR, f"{date}.json"), {"regions": {}})


def save_state(date, state):
    write_json(os.path.join(STATE_DIR, f"{date}.json"), state)


def prune_state(keep):
    if not os.path.isdir(STATE_DIR):
        return
    for f in os.listdir(STATE_DIR):
        if f.endswith(".json") and f[:-5] not in keep:
            os.remove(os.path.join(STATE_DIR, f))


def apply_observations(seen):
    """Merge this run into per-day state. Returns new home cancellations."""
    by_date = defaultdict(list)
    for s in seen:
        by_date[s["date"]].append(s)
    touched = {}
    for date, items in by_date.items():
        st = load_state(date)
        for s in items:
            if s["school"]:
                continue
            reg = st["regions"].setdefault(s["code"], {"cxl": [], "delay": {}})
            start = reg.setdefault("start", {})
            if s["cancelled"]:
                if s["trip_id"] not in reg["cxl"]:
                    reg["cxl"].append(s["trip_id"])
                reg["delay"].pop(s["trip_id"], None)
                start.pop(s["trip_id"], None)
            elif s["trip_id"] not in reg["cxl"]:
                if s["delay"] is not None and s["delay"] > MAX_PLAUSIBLE_DELAY:
                    s["delay"] = None
                if s["start_delay"] is not None and s["start_delay"] > MAX_PLAUSIBLE_DELAY:
                    s["start_delay"] = None
                if s["delay"] is not None:
                    prev = reg["delay"].get(s["trip_id"])
                    reg["delay"][s["trip_id"]] = s["delay"] if prev is None else max(prev, s["delay"])
                if s["start_delay"] is not None:
                    start[s["trip_id"]] = s["start_delay"]  # latest reading is closest to actual departure
        save_state(date, st)
        touched[date] = st
    return touched


# ---------------------------------------------------------------- home region analysis
def home_scheduled(home, date):
    """List of (trip_id, route_id, headsign, start_min) scheduled on date."""
    out = []
    services = home.get("services", {})
    school = set(home.get("school_routes", []))
    for tid, info in home["trips"].items():
        rid, hs, start, sid = info[:4]
        if rid not in school and date in services.get(sid, ()):
            out.append((tid, rid, hs, hhmm_to_min(start)))
    return out


def find_gaps(sched, cancelled, routes):
    """Longest waits created by cancellations, per route + direction."""
    groups = defaultdict(list)
    for tid, rid, hs, m in sched:
        if m is not None:
            groups[(rid, hs)].append((m, tid))
    gaps = []
    for (rid, hs), trips in groups.items():
        trips.sort()
        if len(trips) < 2:
            continue
        diffs = [b[0] - a[0] for a, b in zip(trips, trips[1:]) if b[0] > a[0]]
        normal = statistics.median(diffs) if diffs else None
        i = 0
        while i < len(trips):
            if trips[i][1] in cancelled:
                j = i
                while j < len(trips) and trips[j][1] in cancelled:
                    j += 1
                prev_run = trips[i - 1][0] if i > 0 else None
                next_run = trips[j][0] if j < len(trips) else None
                n = j - i
                if prev_run is not None and next_run is not None:
                    gap = next_run - prev_run
                    gaps.append({"route": routes.get(rid, rid), "headsign": hs, "from": min_to_hhmm(prev_run),
                                 "to": min_to_hhmm(next_run), "gap_min": gap, "normal_min": normal,
                                 "cancelled_in_row": n})
                elif prev_run is not None:
                    gaps.append({"route": routes.get(rid, rid), "headsign": hs, "from": min_to_hhmm(prev_run),
                                 "to": "end of day", "gap_min": None, "normal_min": normal,
                                 "cancelled_in_row": n})
                i = j
            else:
                i += 1
    gaps.sort(key=lambda g: (g["gap_min"] is None, -(g["gap_min"] or 0), -g["cancelled_in_row"]))
    return gaps


def home_day(home, date, cxl_rows, state):
    routes = home["routes"]
    name_to_rid = {}
    for rid, nm in routes.items():
        name_to_rid.setdefault(nm, rid)
    sched = home_scheduled(home, date)
    cutoff = hhmm_to_min(PARTIAL_DAYS.get(date, "00:00"))
    sched = [t for t in sched if t[3] is None or t[3] >= cutoff]
    cxl_all = [r for r in cxl_rows if r["service_date"] == date and not r.get("reinstated")
               and (hhmm_to_min(r["start_time"]) is None or hhmm_to_min(r["start_time"]) >= cutoff)]
    school_names = {routes.get(rid, rid) for rid in home.get("school_routes", [])}
    cxl_today = [r for r in cxl_all if r["route"] not in school_names]
    school_cxl = [r for r in cxl_all if r["route"] in school_names]
    cancelled = {r["trip_id"] for r in cxl_today}
    home_state = state["regions"].get(HOME_CODE, {})
    anywhere = {t: d for t, d in home_state.get("delay", {}).items() if t not in cancelled and d <= MAX_PLAUSIBLE_DELAY}
    delays = {t: d for t, d in home_state.get("start", {}).items() if t not in cancelled and d <= MAX_PLAUSIBLE_DELAY}

    def is_peak(m, rng):
        return m is not None and (m // 60) % 24 in rng

    am_s = [t for t in sched if is_peak(t[3], AM_PEAK)]
    pm_s = [t for t in sched if is_peak(t[3], PM_PEAK)]
    am_c = sum(1 for r in cxl_today if is_peak(hhmm_to_min(r["start_time"]), AM_PEAK))
    pm_c = sum(1 for r in cxl_today if is_peak(hhmm_to_min(r["start_time"]), PM_PEAK))
    gaps = find_gaps(sched, cancelled, routes)
    longest = next((g for g in gaps if g["gap_min"]), None)

    # per route
    r_sched = Counter(routes.get(t[1], t[1]) for t in sched)
    r_cxl = Counter(r["route"] for r in cxl_today)
    trip_route = {t[0]: routes.get(t[1], t[1]) for t in sched}
    for tid, (rid, *_rest) in home["trips"].items():
        trip_route.setdefault(tid, routes.get(rid, rid))
    r_obs, r_late, r_on = Counter(), Counter(), Counter()
    for tid, d in delays.items():
        rn = trip_route.get(tid)
        if rn:
            r_obs[rn] += 1
            if d > LATE_SECONDS:
                r_late[rn] += 1
            elif d >= EARLY_SECONDS:
                r_on[rn] += 1
    route_rows = []
    for rn in sorted(set(r_sched) | set(r_cxl) | set(r_obs)):
        route_rows.append({"date": date, "route": rn, "scheduled": r_sched[rn], "cancelled": r_cxl[rn],
                           "observed": r_obs[rn], "late": r_late[rn], "ontime": r_on[rn]})

    late_trips = []
    for tid, d in sorted(anywhere.items(), key=lambda kv: -kv[1])[:15]:
        if d <= LATE_SECONDS:
            break
        info = home["trips"].get(tid)
        late_trips.append({"route": trip_route.get(tid, "?"), "headsign": info[1] if info else "",
                           "start_time": info[2] if info else "", "late_min": round(d / 60)})

    def peak_late(rng):
        obs = [d for t, d in delays.items() if is_peak(hhmm_to_min((home["trips"].get(t) or ["", "", ""])[2]), rng)]
        return len(obs), sum(1 for d in obs if d > LATE_SECONDS)

    am_o, am_l = peak_late(AM_PEAK)
    pm_o, pm_l = peak_late(PM_PEAK)
    late_minutes = round(sum(d for d in delays.values() if d > LATE_SECONDS) / 60)

    daily = {"date": date, "scheduled": len(sched), "cancelled": len(cancelled),
             "observed": len(delays), "late": sum(1 for d in delays.values() if d > LATE_SECONDS),
             "am_scheduled": len(am_s), "am_cancelled": am_c, "pm_scheduled": len(pm_s), "pm_cancelled": pm_c,
             "longest_gap_min": longest["gap_min"] if longest else "",
             "longest_gap_route": f'{longest["route"]} to {longest["headsign"]}' if longest else "",
             "am_observed": am_o, "am_late": am_l, "pm_observed": pm_o, "pm_late": pm_l, "late_minutes": late_minutes,
             "ontime": sum(1 for d in delays.values() if EARLY_SECONDS <= d <= LATE_SECONDS),
             "anywhere_observed": len(anywhere), "anywhere_late": sum(1 for d in anywhere.values() if d > LATE_SECONDS)}
    return daily, route_rows, gaps[:12], late_trips, cxl_today, school_cxl


def region_rows(regions, date, state):
    rows = []
    codes = set(regions) | set(state["regions"])
    for code in sorted(codes):
        reg = regions.get(code, {})
        st = state["regions"].get(code, {"cxl": [], "delay": {}})
        delays = st.get("start", {})
        sched = reg.get("scheduled_by_date", {}).get(date, 0)
        if not sched and not st.get("cxl") and not st.get("delay"):
            continue
        rows.append({"date": date, "code": code,
                     "name": reg.get("name") or (code.replace("agency:", "Operator ") if code.startswith("agency:") else code),
                     "scheduled": sched, "cancelled": len(st.get("cxl", [])), "observed": len(delays),
                     "late": sum(1 for d in delays.values() if d > LATE_SECONDS),
                     "ontime": sum(1 for d in delays.values() if EARLY_SECONDS <= d <= LATE_SECONDS)})
    return rows


# ---------------------------------------------------------------- outputs
def pct(a, b):
    return round(100 * a / b, 1) if b else None


def sum_rows(rows, fields):
    return {f: sum(int(r.get(f) or 0) for r in rows) for f in fields}


SCHOOL_ROUTE_NAMES_CACHE = set()


def build_outputs(today, live_extra, status):
    school_names = set(SCHOOL_ROUTE_NAMES_CACHE)
    r8 = read_csv(R8_DAILY)
    routes_hist = [r for r in read_csv(R8_ROUTES) if r["route"] not in school_names and not SCHOOL_NAME.match(r["route"])]
    regions_hist = read_csv(REGIONS_DAILY)
    cxl = [r for r in read_csv(CXL_LOG) if r["route"] not in school_names and not SCHOOL_NAME.match(r["route"])
           and not r.get("reinstated")]
    tstr = iso(today)

    def window(rows, days):
        start = iso(today - dt.timedelta(days=days - 1))
        return [r for r in rows if start <= r["date"] <= tstr]

    F = ["scheduled", "cancelled", "observed", "late", "am_scheduled", "am_cancelled", "pm_scheduled", "pm_cancelled",
         "am_observed", "am_late", "pm_observed", "pm_late", "late_minutes",
         "ontime", "anywhere_observed", "anywhere_late"]
    totals = {k: sum_rows(window(r8, n) if n else r8, F) for k, n in (("7", 7), ("30", 30), ("all", 0))}
    for t in totals.values():
        t["cancel_pct"] = pct(t["cancelled"], t["scheduled"])
        t["late_pct"] = pct(t["late"], t["observed"])
        t["am_pct"] = pct(t["am_cancelled"], t["am_scheduled"])
        t["pm_pct"] = pct(t["pm_cancelled"], t["pm_scheduled"])
        off_s = t["scheduled"] - t["am_scheduled"] - t["pm_scheduled"]
        off_c = t["cancelled"] - t["am_cancelled"] - t["pm_cancelled"]
        t["off_pct"] = pct(off_c, off_s)
        t["am_late_pct"] = pct(t["am_late"], t["am_observed"])
        t["pm_late_pct"] = pct(t["pm_late"], t["pm_observed"])
        t["off_late_pct"] = pct(t["late"] - t["am_late"] - t["pm_late"], t["observed"] - t["am_observed"] - t["pm_observed"])
        t["avg_late_min"] = round(t["late_minutes"] / t["late"], 1) if t["late"] else None
        t["ontime_pct"] = pct(t["ontime"], t["observed"])
        t["anywhere_late_pct"] = pct(t["anywhere_late"], t["anywhere_observed"])

    # league table (30 days)
    agg = defaultdict(lambda: Counter())
    for r in window(routes_hist, 30):
        for f in ("scheduled", "cancelled", "observed", "late", "ontime"):
            agg[r["route"]][f] += int(r[f] or 0)
    league = []
    for rn, c in agg.items():
        if c["scheduled"] >= 50:
            league.append({"route": rn, **c, "cancel_pct": pct(c["cancelled"], c["scheduled"]),
                           "late_pct": pct(c["late"], c["observed"])})
    league_late = sorted([dict(route=rn, **c, late_pct=pct(c["late"], c["observed"]), cancel_pct=pct(c["cancelled"], c["scheduled"]))
                          for rn, c in agg.items() if c["observed"] >= 30],
                         key=lambda x: (-(x["late_pct"] or 0), -x["late"]))
    league.sort(key=lambda x: (-(x["cancel_pct"] or 0), -x["cancelled"]))

    # by hour / weekday (cancellations)
    hours = [0] * 24
    for r in cxl:
        m = hhmm_to_min(r["start_time"])
        if m is not None:
            hours[(m // 60) % 24] += 1
    wd_sum, wd_days = [0] * 7, [0] * 7
    for r in r8:
        w = dt.date.fromisoformat(r["date"]).weekday()
        wd_sum[w] += int(r["cancelled"] or 0)
        wd_days[w] += 1

    summary = {
        "updated": now_syd().isoformat(timespec="seconds"),
        "first_date": r8[0]["date"] if r8 else tstr,
        "days_tracked": len(r8),
        "daily": [{"date": r["date"], **{k: int(r.get(k) or 0) for k in ("scheduled", "cancelled", "observed", "late", "ontime", "longest_gap_min")}} for r in r8],
        "totals": totals,
        "league": league[:15],
        "league_late": league_late[:15],
        "by_hour": hours,
        "by_weekday_avg": [round(s / d, 1) if d else None for s, d in zip(wd_sum, wd_days)],
        "school_cxl_30": dict(Counter(r["route"] for r in read_csv(CXL_LOG)
                                      if r.get("school") and not r.get("reinstated")
                                      and r["service_date"] >= iso(today - dt.timedelta(days=29)))),
    }
    write_json(os.path.join(DATA, "summary.json"), summary)

    # routes detail
    by_route = defaultdict(list)
    for r in routes_hist:
        by_route[r["route"]].append(r)
    recent_cxl = defaultdict(list)
    for r in sorted(cxl, key=lambda r: (r["service_date"], r["start_time"]), reverse=True):
        if len(recent_cxl[r["route"]]) < 25:
            recent_cxl[r["route"]].append([r["service_date"], r["start_time"], r["headsign"]])
    routes_out = {}
    for rn, rows in by_route.items():
        rows.sort(key=lambda r: r["date"])
        t30 = sum_rows(window(rows, 30), ("scheduled", "cancelled", "observed", "late", "ontime"))
        tall = sum_rows(rows, ("scheduled", "cancelled", "observed", "late", "ontime"))
        routes_out[rn] = {
            "d30": {**t30, "cancel_pct": pct(t30["cancelled"], t30["scheduled"]), "late_pct": pct(t30["late"], t30["observed"]), "ontime_pct": pct(t30["ontime"], t30["observed"])},
            "all": {**tall, "cancel_pct": pct(tall["cancelled"], tall["scheduled"]), "late_pct": pct(tall["late"], tall["observed"])},
            "daily": [[r["date"], int(r["scheduled"] or 0), int(r["cancelled"] or 0), int(r["observed"] or 0), int(r["late"] or 0)] for r in rows[-90:]],
            "recent": recent_cxl.get(rn, []),
        }
    write_json(os.path.join(DATA, "routes.json"), routes_out)

    # regions comparison
    def reg_table(rows):
        agg = {}
        for r in rows:
            a = agg.setdefault(r["code"], {"code": r["code"], "name": r["name"], "scheduled": 0, "cancelled": 0, "observed": 0, "late": 0, "ontime": 0})
            a["name"] = r["name"] or a["name"]
            for f in ("scheduled", "cancelled", "observed", "late", "ontime"):
                a[f] += int(r[f] or 0)
        out = []
        for a in agg.values():
            if not a["scheduled"]:
                continue  # can't give a fair rate without the timetable
            a["cancel_pct"] = pct(a["cancelled"], a["scheduled"])
            a["late_pct"] = pct(a["late"], a["observed"])
            a["ontime_pct"] = pct(a["ontime"], a["observed"])
            a["home"] = a["code"] == HOME_CODE
            out.append(a)
        out.sort(key=lambda a: -(a["cancel_pct"] or 0))
        return out

    regions_out = {
        "today": reg_table([r for r in regions_hist if r["date"] == tstr]),
        "d7": reg_table(window(regions_hist, 7)),
        "d30": reg_table(window(regions_hist, 30)),
    }
    write_json(os.path.join(DATA, "regions.json"), regions_out)

    live = {"updated": summary["updated"], "status": status, "date": tstr, **live_extra}
    write_json(os.path.join(DATA, "live.json"), live, pretty=True)


# ---------------------------------------------------------------- main
# ---------------------------------------------------------------- My Bus data
def write_timetable(home, today, cross=None):
    """Today's regular (non-school) trips by route, written once a day. CDC routes through the
    Northern Beaches are added as 'route (CDC)', with trip ids prefixed 'c:'."""
    old = read_json(TIMETABLE, {})
    if old.get("date") == today and (old.get("cdc") or not cross):
        return
    by_route = defaultdict(list)
    for tid, rid, hs, m in home_scheduled(home, today):
        info = home["trips"][tid]
        by_route[home["routes"].get(rid, rid)].append([tid, info[2], hs, info[4] if len(info) > 4 else ""])
    if cross:
        for tid, rid, hs, m in cross_scheduled(cross, today):
            info = cross["trips"][tid]
            by_route[cross["routes"].get(rid, rid) + " (CDC)"].append(["c:" + tid, info[2], hs, info[4] if len(info) > 4 else ""])
    for v in by_route.values():
        v.sort(key=lambda t: t[1])
    write_json(TIMETABLE, {"date": today, "cdc": bool(cross), "routes": dict(sorted(by_route.items()))})


def record_occupancy(occ):
    """Keep the fullest level seen for each trip, per day."""
    by_date = defaultdict(dict)
    for (d, tid), level in occ.items():
        by_date[d][tid] = level
    for d, levels in by_date.items():
        st = load_state(d)
        reg = st["regions"].setdefault(HOME_CODE, {"cxl": [], "delay": {}})
        o = reg.setdefault("occ", {})
        for tid, level in levels.items():
            o[tid] = max(level, o.get(tid, 0))
        save_state(d, st)


def live_active(home, today, state, occ_now, cross=None):
    """Status of trips that started in the last 3 hours or start in the next hour."""
    now_m = now_syd().hour * 60 + now_syd().minute
    out = {}
    jobs = [(HOME_CODE, "", home_scheduled(home, today))]
    if cross:
        jobs.append((CROSS_CODE, "c:", cross_scheduled(cross, today)))
    for code, pre, sched in jobs:
        reg = state["regions"].get(code, {})
        start, anywhere = reg.get("start", {}), reg.get("delay", {})
        out.update(_active(sched, start, anywhere, occ_now, today, now_m, pre))
    return out


def _active(sched, start, anywhere, occ_now, today, now_m, pre):
    out = {}
    for tid, rid, hs, m in sched:
        if m is None or not (now_m - 180 <= m <= now_m + 60):
            continue
        s, a, o = start.get(tid), anywhere.get(tid), occ_now.get((today, tid))
        s = None if s is not None and s > MAX_PLAUSIBLE_DELAY else s
        a = None if a is not None and a > MAX_PLAUSIBLE_DELAY else a
        if s is None and a is None and o is None:
            continue
        out[pre + tid] = [None if s is None else round(s / 60), None if a is None else round(a / 60), None if pre else o]
    return out


def finalise_trips(home, cxl_rows):
    """Once a day, save yesterday's per-trip results and rebuild the 30-day reliability stats."""
    if now_syd().hour < 4:
        return
    y = iso(now_syd().date() - dt.timedelta(days=1))
    path = os.path.join(TRIPS_HIST, f"{y}.csv.gz")
    if os.path.exists(path):
        return
    st = load_state(y)
    reg = st["regions"].get(HOME_CODE, {})
    start, occ = reg.get("start", {}), reg.get("occ", {})
    cancelled = {r["trip_id"] for r in cxl_rows if r["service_date"] == y and not r.get("reinstated")}
    cutoff = hhmm_to_min(PARTIAL_DAYS.get(y, "00:00"))
    rows = []
    for tid, rid, hs, m in home_scheduled(home, y):
        if m is not None and m < cutoff:
            continue
        info = home["trips"][tid]
        rows.append({"route": home["routes"].get(rid, rid), "headsign": hs, "start": info[2],
                     "first_stop": info[4] if len(info) > 4 else "", "cancelled": int(tid in cancelled),
                     "start_delay": "" if tid in cancelled or tid not in start else start[tid],
                     "occupancy": occ.get(tid, "")})
    if not rows:
        return
    os.makedirs(TRIPS_HIST, exist_ok=True)
    with gzip.open(path, "wt", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    build_trip_stats()


def build_trip_stats():
    files = sorted(f for f in os.listdir(TRIPS_HIST) if f.endswith(".csv.gz"))[-TRIP_STATS_DAYS:]
    slots = {}
    for fn in files:
        with gzip.open(os.path.join(TRIPS_HIST, fn), "rt", newline="") as f:
            for r in csv.DictReader(f):
                s = slots.setdefault(r["route"], {}).setdefault(r["headsign"], {}).setdefault(r["start"], [0, 0, 0, 0, 0, 0])
                s[0] += 1
                s[1] += int(r["cancelled"])
                if r["start_delay"] != "":
                    s[2] += 1
                    s[3] += int(int(r["start_delay"]) > LATE_SECONDS)
                if r["occupancy"] != "":
                    s[4] += 1
                    s[5] += int(int(r["occupancy"]) >= CROWDED)
    write_json(TRIP_STATS, {"days": len(files), "from": files[0][:10] if files else None,
                            "fields": ["days", "cancelled", "tracked", "late", "occ_tracked", "crowded"], "slots": slots})


def migrate_school_exclusion(home):
    """One-off: earlier versions counted school services. Rebuild today's figures without them."""
    if not os.path.exists(CXL_LOG):
        return
    with open(CXL_LOG) as f:
        header = f.readline()
    if "school" in header:
        return
    print("Upgrading data: excluding school services")
    school_names = {home["routes"].get(r, r) for r in home.get("school_routes", [])}
    rows = read_csv(CXL_LOG)
    for r in rows:
        r["school"] = "yes" if (r["route"] in school_names or SCHOOL_NAME.match(r["route"])) else ""
    write_csv(CXL_LOG, CXL_FIELDS, rows)
    for path, fields in ((R8_DAILY, R8_DAILY_FIELDS), (R8_ROUTES, R8_ROUTES_FIELDS), (REGIONS_DAILY, REGIONS_FIELDS)):
        kept = [r for r in read_csv(path) if r["route"] not in school_names] if path == R8_ROUTES else read_csv(path)
        today = iso(now_syd().date())
        write_csv(path, fields, [r for r in kept if r["date"] != today])
    if os.path.isdir(STATE_DIR):
        for f in os.listdir(STATE_DIR):
            os.remove(os.path.join(STATE_DIR, f))


def fix_after_midnight(rows, home):
    """Move after-midnight trips that were logged under the calendar date back to their service day."""
    changed = False
    for r in rows:
        info = home["trips"].get(r["trip_id"])
        static_start = info[2] if info else ""
        if (static_start[:2].isdigit() and int(static_start[:2]) >= 24 and r["start_time"][:2].isdigit()
                and int(r["start_time"][:2]) < 4):
            d = dt.date.fromisoformat(r["service_date"]) - dt.timedelta(days=1)
            r["service_date"], r["start_time"] = iso(d), static_start[:5]
            changed = True
    return changed


def migrate_start_measure():
    """One-off: lateness used to be 'late anywhere on the route'. It is now measured leaving the
    first stop, like Transport for NSW. Clear the old lateness figures so they aren't mixed."""
    if not os.path.exists(R8_DAILY):
        return
    with open(R8_DAILY) as f:
        if "ontime" in f.readline():
            return
    print("Upgrading data: lateness now measured at the first stop")
    clear = ("observed", "late", "am_observed", "am_late", "pm_observed", "pm_late", "late_minutes")
    for path, fields in ((R8_DAILY, R8_DAILY_FIELDS), (R8_ROUTES, R8_ROUTES_FIELDS), (REGIONS_DAILY, REGIONS_FIELDS)):
        rows = read_csv(path)
        for r in rows:
            for f in clear:
                if f in r:
                    r[f] = ""
        write_csv(path, fields, rows)


# ---------------------------------------------------------------- CDC NSW routes through the Northern Beaches
def cross_scheduled(cross, date):
    nb = set(cross.get("nb_routes", []))
    services = cross.get("services", {})
    return [(tid, info[0], info[1], hhmm_to_min(info[2])) for tid, info in cross.get("trips", {}).items()
            if info[0] in nb and date in services.get(info[3], ())]


def cdc_start():
    try:
        with open(CDC_START) as f:
            return f.read().strip()
    except FileNotFoundError:
        return None


def log_cross_cancellations(cross, seen, stamp):
    """Permanent log of cancelled CDC trips on routes through the Northern Beaches."""
    nb = set(cross.get("nb_routes", []))
    rows = read_csv(CDC_CXL_LOG)
    known = {(r["service_date"], r["trip_id"]) for r in rows}
    changed = False
    for s in seen:
        if s["code"] != CROSS_CODE or s["school"] or s["route_id"] not in nb:
            continue
        key = (s["date"], s["trip_id"])
        if s["cancelled"] and key not in known:
            known.add(key)
            info = cross["trips"].get(s["trip_id"])
            rows.append({"service_date": s["date"], "trip_id": s["trip_id"],
                         "route": cross["routes"].get(s["route_id"], s["route_id"]),
                         "start_time": s["start_time"] or (info[2] if info else ""),
                         "headsign": info[1] if info else "", "first_seen": stamp, "school": "", "reinstated": ""})
            changed = True
    running = {(s["date"], s["trip_id"]) for s in seen
               if s["code"] == CROSS_CODE and not s["cancelled"] and (s["delay"] is not None or s["start_delay"] is not None)}
    for r in rows:
        if not r.get("reinstated") and (r["service_date"], r["trip_id"]) in running:
            r["reinstated"] = stamp
            changed = True
    if changed:
        write_csv(CDC_CXL_LOG, CXL_FIELDS, rows)
    return rows


def cross_day(cross, date, cxl_rows, state, start_stamp):
    routes = cross["routes"]
    sched = cross_scheduled(cross, date)
    cutoff = 0
    if start_stamp and start_stamp[:10] == date:
        cutoff = hhmm_to_min(start_stamp[11:16]) or 0
    sched = [t for t in sched if t[3] is None or t[3] >= cutoff]
    sched_ids = {t[0] for t in sched}
    cxl = [r for r in cxl_rows if r["service_date"] == date and not r.get("reinstated") and r["trip_id"] in sched_ids]
    cancelled = {r["trip_id"] for r in cxl}
    st = state["regions"].get(CROSS_CODE, {})
    delays = {t: d for t, d in st.get("start", {}).items() if t in sched_ids and t not in cancelled and d <= MAX_PLAUSIBLE_DELAY}
    gaps = find_gaps(sched, cancelled, routes)
    longest = next((g for g in gaps if g["gap_min"]), None)
    daily = {"date": date, "scheduled": len(sched), "cancelled": len(cancelled), "observed": len(delays),
             "late": sum(1 for d in delays.values() if d > LATE_SECONDS),
             "ontime": sum(1 for d in delays.values() if EARLY_SECONDS <= d <= LATE_SECONDS),
             "longest_gap_min": longest["gap_min"] if longest else ""}
    trip_route = {t[0]: routes.get(t[1], t[1]) for t in sched}
    r_sched = Counter(trip_route.values())
    r_cxl = Counter(r["route"] for r in cxl)
    r_obs, r_late, r_on = Counter(), Counter(), Counter()
    for tid, d in delays.items():
        rn = trip_route[tid]
        r_obs[rn] += 1
        r_late[rn] += d > LATE_SECONDS
        r_on[rn] += EARLY_SECONDS <= d <= LATE_SECONDS
    route_rows = [{"date": date, "route": rn, "scheduled": r_sched[rn], "cancelled": r_cxl[rn], "observed": r_obs[rn],
                   "late": r_late[rn], "ontime": r_on[rn]} for rn in sorted(r_sched)]
    return daily, route_rows, gaps[:10], cxl


def build_cross_outputs(cross, today, today_detail):
    rows = read_csv(CDC_DAILY)
    tstr = iso(today)

    def window(rs, days):
        start = iso(today - dt.timedelta(days=days - 1))
        return [r for r in rs if start <= r["date"] <= tstr]

    totals = {}
    for k, n in (("7", 7), ("30", 30)):
        t = sum_rows(window(rows, n), ("scheduled", "cancelled", "observed", "late", "ontime"))
        t.update(cancel_pct=pct(t["cancelled"], t["scheduled"]), late_pct=pct(t["late"], t["observed"]),
                 ontime_pct=pct(t["ontime"], t["observed"]))
        totals[k] = t
    agg = defaultdict(Counter)
    for r in window(read_csv(CDC_ROUTES), 30):
        for f in ("scheduled", "cancelled", "observed", "late"):
            agg[r["route"]][f] += int(r[f] or 0)
    league = sorted(({"route": rn, **c, "cancel_pct": pct(c["cancelled"], c["scheduled"]), "late_pct": pct(c["late"], c["observed"])}
                     for rn, c in agg.items() if c["scheduled"] >= 20), key=lambda x: (-(x["cancel_pct"] or 0), -x["cancelled"]))
    write_json(CDC_FILE, {
        "updated": now_syd().isoformat(timespec="seconds"), "name": "CDC NSW", "region": 14,
        "since": cdc_start(), "days_tracked": len(rows),
        "routes": sorted({cross["routes"].get(r, r) for r in cross.get("nb_routes", [])}, key=lambda x: (len(x), x)),
        "today": today_detail, "totals": totals, "league": league[:10],
        "daily": [{"date": r["date"], **{k: int(r.get(k) or 0) for k in ("scheduled", "cancelled", "observed", "late", "ontime")}} for r in rows[-90:]],
    })


def run_cross(cross, seen, today, stamp, dates):
    if not cross or not cross.get("nb_routes"):
        return
    if not cdc_start():
        os.makedirs(HIST, exist_ok=True)
        with open(CDC_START, "w") as f:
            f.write(stamp)
    rows = log_cross_cancellations(cross, seen, stamp)
    start_stamp = cdc_start()
    detail = {}
    for date in dates:
        if start_stamp and date < start_stamp[:10]:
            continue
        state = load_state(date)
        daily, route_rows, gaps, cxl = cross_day(cross, date, rows, state, start_stamp)
        upsert_csv(CDC_DAILY, CDC_DAILY_FIELDS, [daily], ["date"])
        upsert_csv(CDC_ROUTES, CDC_ROUTES_FIELDS, route_rows, ["date", "route"])
        if date == iso(today):
            now_m = now_syd().hour * 60 + now_syd().minute
            due = sum(1 for r in cxl if (hhmm_to_min(r["start_time"]) or 0) <= now_m)
            detail = {**daily, "cancel_pct": pct(daily["cancelled"], daily["scheduled"]),
                      "ontime_pct": pct(daily["ontime"], daily["observed"]), "late_pct": pct(daily["late"], daily["observed"]),
                      "due": due, "ahead": daily["cancelled"] - due, "gaps": gaps,
                      "trips": [{"start_time": r["start_time"], "route": r["route"], "headsign": r["headsign"], "trip_id": r["trip_id"],
                                 "from": ((cross["trips"].get(r["trip_id"]) or [])[4:5] or [""])[0]}
                                for r in sorted(cxl, key=lambda r: r["start_time"] or "99")]}
    build_cross_outputs(cross, today, detail)


def main():
    os.makedirs(DATA, exist_ok=True)
    old = os.path.join(DATA, "static_r8.json")  # replaced by cache/
    if os.path.exists(old):
        os.remove(old)
    hdr = headers()
    regions = load_regions(hdr)
    home = regions[HOME_CODE]
    cross = regions.get(CROSS_CODE) if regions.get(CROSS_CODE, {}).get("trips") else None
    prefix_to_code, school_routes = {}, set()
    for code, reg in regions.items():
        for aid in reg.get("agencies", {}):
            prefix_to_code[aid] = code
        school_routes.update(reg.get("school_routes", []))

    migrate_school_exclusion(home)
    migrate_start_measure()
    SCHOOL_ROUTE_NAMES_CACHE.update(home["routes"].get(r, r) for r in home.get("school_routes", []))
    status = "ok"
    try:
        seen = read_feed(hdr, prefix_to_code, home, school_routes, cross)
    except Exception as exc:
        seen = []
        status = f"feed error: {exc}"
        print(status)

    # home cancellations -> permanent trip-level log
    cxl_rows = read_csv(CXL_LOG)
    known = {(r["service_date"], r["trip_id"]) for r in cxl_rows}
    stamp = now_syd().isoformat(timespec="seconds")
    new = []
    for s in seen:
        if s["code"] == HOME_CODE and s["cancelled"] and (s["date"], s["trip_id"]) not in known:
            known.add((s["date"], s["trip_id"]))
            info = home["trips"].get(s["trip_id"])
            new.append({"service_date": s["date"], "trip_id": s["trip_id"],
                        "route": home["routes"].get(s["route_id"], s["route_id"]),
                        "start_time": s["start_time"] or (info[2] if info else ""),
                        "headsign": info[1] if info else "", "first_seen": stamp,
                        "school": "yes" if s["school"] else ""})
    # A trip marked cancelled that later shows up running was reinstated: stop counting it.
    running = {(s["date"], s["trip_id"]) for s in seen
               if s["code"] == HOME_CODE and not s["cancelled"] and (s["delay"] is not None or s["start_delay"] is not None)}
    changed = False
    for r in cxl_rows:
        if not r.get("reinstated") and (r["service_date"], r["trip_id"]) in running:
            r["reinstated"] = stamp
            changed = True
    if new:
        cxl_rows.extend(new)
    if new or changed or fix_after_midnight(cxl_rows, home):
        write_csv(CXL_LOG, CXL_FIELDS, cxl_rows)
    print(f"Feed trips: {len(seen)}; new home cancellations: {len(new)}")

    touched = apply_observations(seen)
    occ_now = read_occupancy(hdr, home) if status == "ok" else {}
    if occ_now:
        record_occupancy(occ_now)
    today = now_syd().date()
    write_timetable(home, iso(today), cross)
    finalise_trips(home, cxl_rows)
    dates = sorted(set(touched) | {iso(today), iso(today - dt.timedelta(days=1))})
    dates = [d for d in dates if d >= iso(today - dt.timedelta(days=1))]
    live_extra = {}
    for date in dates:
        state = touched.get(date) or load_state(date)
        daily, route_rows, gaps, late_trips, cxl_today, school_cxl = home_day(home, date, cxl_rows, state)
        upsert_csv(R8_DAILY, R8_DAILY_FIELDS, [daily], ["date"],
                   keep_max=())
        upsert_csv(R8_ROUTES, R8_ROUTES_FIELDS, route_rows, ["date", "route"])
        upsert_csv(REGIONS_DAILY, REGIONS_FIELDS, region_rows(regions, date, state), ["date", "code"],
                   keep_max=("cancelled",))
        if date == iso(today):
            now_m = now_syd().hour * 60 + now_syd().minute
            due = sum(1 for r in cxl_today if (hhmm_to_min(r["start_time"]) or 0) <= now_m)
            live_extra = {
                "count_due": due, "count_ahead": daily["cancelled"] - due,
                "scheduled": daily["scheduled"], "count": daily["cancelled"],
                "cancel_pct": pct(daily["cancelled"], daily["scheduled"]),
                "observed": daily["observed"], "late": daily["late"], "late_pct": pct(daily["late"], daily["observed"]),
                "avg_late_min": round(daily["late_minutes"] / daily["late"], 1) if daily["late"] else None,
                "ontime": daily["ontime"], "ontime_pct": pct(daily["ontime"], daily["observed"]),
                "anywhere_late_pct": pct(daily["anywhere_late"], daily["anywhere_observed"]),
                "am": {"scheduled": daily["am_scheduled"], "cancelled": daily["am_cancelled"]},
                "pm": {"scheduled": daily["pm_scheduled"], "cancelled": daily["pm_cancelled"]},
                "gaps": gaps, "late_trips": late_trips,
                "trips": [{**{k: r[k] for k in ("start_time", "route", "headsign", "trip_id")},
                           "from": ((home["trips"].get(r["trip_id"]) or [])[4:5] or [""])[0]}
                          for r in sorted(cxl_today, key=lambda r: r["start_time"] or "99")],
                "active": live_active(home, date, load_state(date), occ_now, cross),
                "school_cancelled": [{k: r[k] for k in ("start_time", "route", "headsign")}
                                     for r in sorted(school_cxl, key=lambda r: r["start_time"] or "99")],
            }
    try:  # CDC tracking is an extra; never let it break the Keolis figures
        run_cross(cross, seen, today, stamp, dates)
    except Exception as exc:
        print("CDC figures not built:", exc)
    prune_state({iso(today - dt.timedelta(days=i)) for i in range(0, 3)} | set(touched))
    build_outputs(today, live_extra, status)
    if status != "ok":
        sys.exit(1)


if __name__ == "__main__":
    main()
