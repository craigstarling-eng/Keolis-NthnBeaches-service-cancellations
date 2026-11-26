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
REGION_CODES = [f"GSBC{n:03d}" for n in range(1, 16)]
HOME_MAX_AGE_DAYS = 3
OTHER_MAX_AGE_DAYS = 7
MAX_REGION_DOWNLOADS_PER_RUN = 3
LATE_SECONDS = 300                        # "late" = more than 5 minutes behind
AM_PEAK = range(6, 9)                     # trips starting 06:00-08:59
PM_PEAK = range(16, 19)                   # trips starting 16:00-18:59
SCHEDULE_DAYS_BACK, SCHEDULE_DAYS_AHEAD = 2, 14
STATIC_VERSION = 2                        # bump to force timetables to reload
SCHOOL_ROUTE_TYPES = {"712"}              # GTFS extended type: school bus
SCHOOL_NAME = re.compile(r"^\d+n$")        # TfNSW school services are numbered like 560n


def is_school_route(route):
    """School services are excluded from all statistics."""
    return route.get("route_type", "") in SCHOOL_ROUTE_TYPES or bool(SCHOOL_NAME.match(route.get("route_short_name", "").strip()))

CXL_LOG = os.path.join(DATA, "cancellations.csv")
CXL_FIELDS = ["service_date", "trip_id", "route", "start_time", "headsign", "first_seen", "school"]
R8_DAILY = os.path.join(HIST, "r8_daily.csv")
R8_DAILY_FIELDS = ["date", "scheduled", "cancelled", "observed", "late",
                   "am_scheduled", "am_cancelled", "pm_scheduled", "pm_cancelled",
                   "longest_gap_min", "longest_gap_route"]
R8_ROUTES = os.path.join(HIST, "r8_routes_daily.csv")
R8_ROUTES_FIELDS = ["date", "route", "scheduled", "cancelled", "observed", "late"]
REGIONS_DAILY = os.path.join(HIST, "regions_daily.csv")
REGIONS_FIELDS = ["date", "code", "name", "scheduled", "cancelled", "observed", "late"]


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
        first = {}
        for st in zrows(z, "stop_times.txt"):
            tid = st["trip_id"]
            if tid not in trips:
                continue
            try:
                seq = int(st["stop_sequence"])
            except ValueError:
                continue
            if tid not in first or seq < first[tid][0]:
                first[tid] = (seq, st.get("departure_time") or st.get("arrival_time") or "")
        for tid, (_, t) in first.items():
            trips[tid][2] = t[:5]
        out["routes"] = routes
        out["trips"] = trips
        out["services"] = {k: sorted(v) for k, v in active.items()}
    print(f"  {out['name']}: {len(routes)} routes")
    return out


def load_regions(hdr):
    os.makedirs(STATIC_DIR, exist_ok=True)
    regions, downloads = {}, 0
    for code in [HOME_CODE] + [c for c in REGION_CODES if c != HOME_CODE]:
        path = os.path.join(STATIC_DIR, f"{code}.json")
        cached = read_json(path, None)
        home = code == HOME_CODE
        max_age = HOME_MAX_AGE_DAYS if home else OTHER_MAX_AGE_DAYS
        stale = True
        if cached:
            try:
                stale = (cached.get("v") != STATIC_VERSION or
                         (now_syd() - dt.datetime.fromisoformat(cached["built"])).days >= max_age)
            except (KeyError, ValueError):
                pass
        if stale and (home or downloads < MAX_REGION_DOWNLOADS_PER_RUN):
            try:
                fresh = build_region(hdr, code, detailed=home)
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


def read_feed(hdr, prefix_to_code, home, school_routes):
    r = requests.get(f"{API}/realtime/buses", headers=hdr, timeout=120)
    r.raise_for_status()
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(r.content)
    CANCELED = gtfs_realtime_pb2.TripDescriptor.CANCELED
    today = now_syd().strftime("%Y%m%d")
    home_trips, home_routes = home["trips"], home["routes"]
    seen = []
    for e in feed.entity:
        if not e.HasField("trip_update"):
            continue
        tu = e.trip_update
        td = tu.trip
        info = home_trips.get(td.trip_id)
        rid = td.route_id or (info[0] if info else "")
        prefix = rid.split("_")[0] if "_" in rid else ""
        code = HOME_CODE if (info or rid in home_routes) else prefix_to_code.get(prefix, f"agency:{prefix}" if prefix else "")
        if not code:
            continue
        sdate = td.start_date or today
        seen.append({
            "code": code,
            "date": f"{sdate[:4]}-{sdate[4:6]}-{sdate[6:8]}",
            "trip_id": td.trip_id,
            "route_id": rid,
            "cancelled": td.schedule_relationship == CANCELED,
            "school": rid in school_routes,
            "delay": trip_delay(tu),
            "start_time": (td.start_time or (info[2] if info else ""))[:5],
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
            if s["cancelled"]:
                if s["trip_id"] not in reg["cxl"]:
                    reg["cxl"].append(s["trip_id"])
                reg["delay"].pop(s["trip_id"], None)
            elif s["delay"] is not None and s["trip_id"] not in reg["cxl"]:
                prev = reg["delay"].get(s["trip_id"])
                reg["delay"][s["trip_id"]] = s["delay"] if prev is None else max(prev, s["delay"])
        save_state(date, st)
        touched[date] = st
    return touched


# ---------------------------------------------------------------- home region analysis
def home_scheduled(home, date):
    """List of (trip_id, route_id, headsign, start_min) scheduled on date."""
    out = []
    services = home.get("services", {})
    school = set(home.get("school_routes", []))
    for tid, (rid, hs, start, sid) in home["trips"].items():
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
    cxl_all = [r for r in cxl_rows if r["service_date"] == date]
    school_names = {routes.get(rid, rid) for rid in home.get("school_routes", [])}
    cxl_today = [r for r in cxl_all if r["route"] not in school_names]
    school_cxl = [r for r in cxl_all if r["route"] in school_names]
    cancelled = {r["trip_id"] for r in cxl_today}
    delays = state["regions"].get(HOME_CODE, {}).get("delay", {})
    delays = {t: d for t, d in delays.items() if t not in cancelled}

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
    r_obs, r_late = Counter(), Counter()
    for tid, d in delays.items():
        rn = trip_route.get(tid)
        if rn:
            r_obs[rn] += 1
            if d > LATE_SECONDS:
                r_late[rn] += 1
    route_rows = []
    for rn in sorted(set(r_sched) | set(r_cxl) | set(r_obs)):
        route_rows.append({"date": date, "route": rn, "scheduled": r_sched[rn], "cancelled": r_cxl[rn],
                           "observed": r_obs[rn], "late": r_late[rn]})

    late_trips = []
    for tid, d in sorted(delays.items(), key=lambda kv: -kv[1])[:15]:
        if d <= LATE_SECONDS:
            break
        info = home["trips"].get(tid)
        late_trips.append({"route": trip_route.get(tid, "?"), "headsign": info[1] if info else "",
                           "start_time": info[2] if info else "", "late_min": round(d / 60)})

    daily = {"date": date, "scheduled": len(sched), "cancelled": len(cancelled),
             "observed": len(delays), "late": sum(1 for d in delays.values() if d > LATE_SECONDS),
             "am_scheduled": len(am_s), "am_cancelled": am_c, "pm_scheduled": len(pm_s), "pm_cancelled": pm_c,
             "longest_gap_min": longest["gap_min"] if longest else "",
             "longest_gap_route": f'{longest["route"]} to {longest["headsign"]}' if longest else ""}
    return daily, route_rows, gaps[:12], late_trips, cxl_today, school_cxl


def region_rows(regions, date, state):
    rows = []
    codes = set(regions) | set(state["regions"])
    for code in sorted(codes):
        reg = regions.get(code, {})
        st = state["regions"].get(code, {"cxl": [], "delay": {}})
        delays = st.get("delay", {})
        sched = reg.get("scheduled_by_date", {}).get(date, 0)
        if not sched and not st.get("cxl") and not delays:
            continue
        rows.append({"date": date, "code": code,
                     "name": reg.get("name") or (code.replace("agency:", "Operator ") if code.startswith("agency:") else code),
                     "scheduled": sched, "cancelled": len(st.get("cxl", [])), "observed": len(delays),
                     "late": sum(1 for d in delays.values() if d > LATE_SECONDS)})
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
    cxl = [r for r in read_csv(CXL_LOG) if r["route"] not in school_names and not SCHOOL_NAME.match(r["route"])]
    tstr = iso(today)

    def window(rows, days):
        start = iso(today - dt.timedelta(days=days - 1))
        return [r for r in rows if start <= r["date"] <= tstr]

    F = ["scheduled", "cancelled", "observed", "late", "am_scheduled", "am_cancelled", "pm_scheduled", "pm_cancelled"]
    totals = {k: sum_rows(window(r8, n) if n else r8, F) for k, n in (("7", 7), ("30", 30), ("all", 0))}
    for t in totals.values():
        t["cancel_pct"] = pct(t["cancelled"], t["scheduled"])
        t["late_pct"] = pct(t["late"], t["observed"])
        t["am_pct"] = pct(t["am_cancelled"], t["am_scheduled"])
        t["pm_pct"] = pct(t["pm_cancelled"], t["pm_scheduled"])
        off_s = t["scheduled"] - t["am_scheduled"] - t["pm_scheduled"]
        off_c = t["cancelled"] - t["am_cancelled"] - t["pm_cancelled"]
        t["off_pct"] = pct(off_c, off_s)

    # league table (30 days)
    agg = defaultdict(lambda: Counter())
    for r in window(routes_hist, 30):
        for f in ("scheduled", "cancelled", "observed", "late"):
            agg[r["route"]][f] += int(r[f] or 0)
    league = []
    for rn, c in agg.items():
        if c["scheduled"] >= 50:
            league.append({"route": rn, **c, "cancel_pct": pct(c["cancelled"], c["scheduled"]),
                           "late_pct": pct(c["late"], c["observed"])})
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
        "daily": [{"date": r["date"], **{k: int(r[k] or 0) for k in ("scheduled", "cancelled", "observed", "late", "longest_gap_min")}} for r in r8],
        "totals": totals,
        "league": league[:15],
        "by_hour": hours,
        "by_weekday_avg": [round(s / d, 1) if d else None for s, d in zip(wd_sum, wd_days)],
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
        t30 = sum_rows(window(rows, 30), ("scheduled", "cancelled", "observed", "late"))
        tall = sum_rows(rows, ("scheduled", "cancelled", "observed", "late"))
        routes_out[rn] = {
            "d30": {**t30, "cancel_pct": pct(t30["cancelled"], t30["scheduled"]), "late_pct": pct(t30["late"], t30["observed"])},
            "all": {**tall, "cancel_pct": pct(tall["cancelled"], tall["scheduled"]), "late_pct": pct(tall["late"], tall["observed"])},
            "daily": [[r["date"], int(r["scheduled"] or 0), int(r["cancelled"] or 0), int(r["observed"] or 0), int(r["late"] or 0)] for r in rows[-90:]],
            "recent": recent_cxl.get(rn, []),
        }
    write_json(os.path.join(DATA, "routes.json"), routes_out)

    # regions comparison
    def reg_table(rows):
        agg = {}
        for r in rows:
            a = agg.setdefault(r["code"], {"code": r["code"], "name": r["name"], "scheduled": 0, "cancelled": 0, "observed": 0, "late": 0})
            a["name"] = r["name"] or a["name"]
            for f in ("scheduled", "cancelled", "observed", "late"):
                a[f] += int(r[f] or 0)
        out = []
        for a in agg.values():
            if not a["scheduled"]:
                continue  # can't give a fair rate without the timetable
            a["cancel_pct"] = pct(a["cancelled"], a["scheduled"])
            a["late_pct"] = pct(a["late"], a["observed"])
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


def main():
    os.makedirs(DATA, exist_ok=True)
    old = os.path.join(DATA, "static_r8.json")  # replaced by cache/
    if os.path.exists(old):
        os.remove(old)
    hdr = headers()
    regions = load_regions(hdr)
    home = regions[HOME_CODE]
    prefix_to_code, school_routes = {}, set()
    for code, reg in regions.items():
        for aid in reg.get("agencies", {}):
            prefix_to_code[aid] = code
        school_routes.update(reg.get("school_routes", []))

    migrate_school_exclusion(home)
    SCHOOL_ROUTE_NAMES_CACHE.update(home["routes"].get(r, r) for r in home.get("school_routes", []))
    status = "ok"
    try:
        seen = read_feed(hdr, prefix_to_code, home, school_routes)
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
    if new:
        cxl_rows.extend(new)
        write_csv(CXL_LOG, CXL_FIELDS, cxl_rows)
    print(f"Feed trips: {len(seen)}; new home cancellations: {len(new)}")

    touched = apply_observations(seen)
    today = now_syd().date()
    dates = sorted(set(touched) | {iso(today)})
    dates = [d for d in dates if d >= iso(today - dt.timedelta(days=1))]
    live_extra = {}
    for date in dates:
        state = touched.get(date) or load_state(date)
        daily, route_rows, gaps, late_trips, cxl_today, school_cxl = home_day(home, date, cxl_rows, state)
        upsert_csv(R8_DAILY, R8_DAILY_FIELDS, [daily], ["date"], keep_max=("observed", "late"))
        upsert_csv(R8_ROUTES, R8_ROUTES_FIELDS, route_rows, ["date", "route"], keep_max=("observed", "late"))
        upsert_csv(REGIONS_DAILY, REGIONS_FIELDS, region_rows(regions, date, state), ["date", "code"],
                   keep_max=("cancelled", "observed", "late"))
        if date == iso(today):
            live_extra = {
                "scheduled": daily["scheduled"], "count": daily["cancelled"],
                "cancel_pct": pct(daily["cancelled"], daily["scheduled"]),
                "observed": daily["observed"], "late": daily["late"], "late_pct": pct(daily["late"], daily["observed"]),
                "am": {"scheduled": daily["am_scheduled"], "cancelled": daily["am_cancelled"]},
                "pm": {"scheduled": daily["pm_scheduled"], "cancelled": daily["pm_cancelled"]},
                "gaps": gaps, "late_trips": late_trips,
                "trips": [{k: r[k] for k in ("start_time", "route", "headsign")}
                          for r in sorted(cxl_today, key=lambda r: r["start_time"] or "99")],
                "school_cancelled": [{k: r[k] for k in ("start_time", "route", "headsign")}
                                     for r in sorted(school_cxl, key=lambda r: r["start_time"] or "99")],
            }
    prune_state({iso(today - dt.timedelta(days=i)) for i in range(0, 3)} | set(touched))
    build_outputs(today, live_extra, status)
    if status != "ok":
        sys.exit(1)


if __name__ == "__main__":
    main()
