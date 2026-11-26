#!/usr/bin/env python3
"""Keolis Northern Beaches (Region 8 / GSBC008) bus cancellation tracker.

Runs on a schedule (GitHub Actions). Each run:
  1. Loads the Region 8 timetable (cached, refreshed every 3 days).
  2. Reads the live TfNSW bus trip-updates feed.
  3. Logs every newly cancelled Region 8 trip to data/cancellations.csv.
  4. Writes data/live.json (today) and data/summary.json (history) for the dashboard.
"""
import csv
import datetime as dt
import io
import json
import os
import sys
import zipfile
from collections import Counter
from zoneinfo import ZoneInfo

import requests
from google.transit import gtfs_realtime_pb2

API = "https://api.transport.nsw.gov.au/v1/gtfs"
SYD = ZoneInfo("Australia/Sydney")
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
STATIC = os.path.join(DATA, "static_r8.json")
LOG = os.path.join(DATA, "cancellations.csv")
LIVE = os.path.join(DATA, "live.json")
SUMMARY = os.path.join(DATA, "summary.json")
FIELDS = ["service_date", "trip_id", "route", "start_time", "headsign", "first_seen"]
STATIC_MAX_AGE_DAYS = 3


def headers():
    key = os.environ.get("TFNSW_API_KEY", "").strip()
    if not key:
        sys.exit("TFNSW_API_KEY is not set (add it as a GitHub secret).")
    return {"Authorization": f"apikey {key}"}


def now_syd():
    return dt.datetime.now(SYD)


# ---------------------------------------------------------------- timetable
def build_static(hdr):
    print("Downloading Region 8 timetable (GSBC008)...")
    r = requests.get(f"{API}/schedule/buses/GSBC008", headers=hdr, timeout=300)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))

    def rows(name):
        with z.open(name) as f:
            yield from csv.DictReader(io.TextIOWrapper(f, "utf-8-sig"))

    routes = {x["route_id"]: (x.get("route_short_name") or x["route_id"]).strip() for x in rows("routes.txt")}
    trips = {}
    for t in rows("trips.txt"):
        rid = t["route_id"]
        if rid in routes:
            trips[t["trip_id"]] = [rid, t.get("trip_headsign", "").strip(), ""]
    first = {}
    for st in rows("stop_times.txt"):
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

    static = {
        "built": now_syd().isoformat(timespec="seconds"),
        "routes": routes,           # route_id -> short name
        "trips": trips,             # trip_id -> [route_id, headsign, start HH:MM]
    }
    with open(STATIC, "w") as f:
        json.dump(static, f, separators=(",", ":"))
    print(f"Timetable cached: {len(routes)} routes, {len(trips)} trips.")
    return static


def load_static(hdr):
    try:
        with open(STATIC) as f:
            s = json.load(f)
        age = now_syd() - dt.datetime.fromisoformat(s["built"])
        if age.days < STATIC_MAX_AGE_DAYS:
            return s
    except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError):
        pass
    return build_static(hdr)


# ---------------------------------------------------------------- realtime
def fetch_cancelled(hdr, static):
    r = requests.get(f"{API}/realtime/buses", headers=hdr, timeout=120)
    r.raise_for_status()
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(r.content)
    CANCELED = gtfs_realtime_pb2.TripDescriptor.CANCELED
    routes, trips = static["routes"], static["trips"]
    out = []
    for e in feed.entity:
        if not e.HasField("trip_update"):
            continue
        td = e.trip_update.trip
        if td.schedule_relationship != CANCELED:
            continue
        info = trips.get(td.trip_id)
        rid = td.route_id or (info[0] if info else "")
        if rid not in routes and not info:
            continue  # not a Region 8 (Keolis Northern Beaches) trip
        if not rid and info:
            rid = info[0]
        start = (td.start_time or (info[2] if info else ""))[:5]
        sdate = td.start_date or now_syd().strftime("%Y%m%d")
        out.append({
            "service_date": f"{sdate[:4]}-{sdate[4:6]}-{sdate[6:8]}",
            "trip_id": td.trip_id,
            "route": routes.get(rid, rid),
            "start_time": start,
            "headsign": info[1] if info else "",
        })
    return out


# ---------------------------------------------------------------- storage
def read_log():
    if not os.path.exists(LOG):
        return []
    with open(LOG, newline="") as f:
        return list(csv.DictReader(f))


def append_log(rows):
    new_file = not os.path.exists(LOG)
    with open(LOG, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------- outputs
def write_outputs(log, status):
    today = now_syd().date()
    tstr = today.isoformat()
    todays = sorted((r for r in log if r["service_date"] == tstr), key=lambda r: r["start_time"] or "99")
    live = {
        "updated": now_syd().isoformat(timespec="seconds"),
        "status": status,
        "date": tstr,
        "count": len(todays),
        "trips": [{k: r.get(k, "") for k in ("start_time", "route", "headsign")} for r in todays],
    }
    with open(LIVE, "w") as f:
        json.dump(live, f, indent=1)

    daily = Counter(r["service_date"] for r in log)

    def window(days):
        start = (today - dt.timedelta(days=days - 1)).isoformat()
        return [r for r in log if start <= r["service_date"] <= tstr]

    hours, weekdays = [0] * 24, [0] * 7
    for r in log:
        try:
            hours[int(r["start_time"][:2]) % 24] += 1
        except (ValueError, TypeError):
            pass
        weekdays[dt.date.fromisoformat(r["service_date"]).weekday()] += 1

    def top(rows, n=10):
        return [{"route": k, "count": v} for k, v in Counter(r["route"] for r in rows).most_common(n)]

    summary = {
        "updated": live["updated"],
        "first_date": min(daily) if daily else tstr,
        "daily": dict(sorted(daily.items())),
        "totals": {"7": len(window(7)), "30": len(window(30)), "all": len(log)},
        "top_routes_30": top(window(30)),
        "top_routes_all": top(log),
        "by_hour": hours,
        "by_weekday": weekdays,
    }
    with open(SUMMARY, "w") as f:
        json.dump(summary, f, separators=(",", ":"))


def main():
    os.makedirs(DATA, exist_ok=True)
    hdr = headers()
    static = load_static(hdr)
    log = read_log()
    status = "ok"
    try:
        found = fetch_cancelled(hdr, static)
        seen = {(r["service_date"], r["trip_id"]) for r in log}
        stamp = now_syd().isoformat(timespec="seconds")
        new = []
        for c in found:
            key = (c["service_date"], c["trip_id"])
            if key in seen:
                continue
            seen.add(key)
            c["first_seen"] = stamp
            new.append(c)
        if new:
            append_log(new)
            log.extend(new)
        print(f"Cancelled now in feed: {len(found)}; newly logged: {len(new)}")
    except Exception as exc:  # keep the dashboard honest if the feed is down
        status = f"feed error: {exc}"
        print(status)
    write_outputs(log, status)
    if status != "ok":
        sys.exit(1)


if __name__ == "__main__":
    main()
