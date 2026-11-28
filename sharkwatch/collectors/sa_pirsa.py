"""South Australia — PIRSA public shark sightings table.

Live:     recent sightings table (Date | Time | Location | Shark species | Reported by)
Backfill: yearly archive pages 2006-2012-ish (older column layout), run with BACKFILL=1
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from common import clean, env_flag, event, get, save_raw

SOURCE = "sa_pirsa"
TZ = ZoneInfo("Australia/Adelaide")
LIVE_URLS = [
    "https://pir.sa.gov.au/fishing-and-aquaculture/recreational-fishing/reporting/report-a-shark-sighting/shark-recent-sightings-listing",
    "https://pir.sa.gov.au/fishing-and-aquaculture/recreational-fishing/report-a-shark-sighting",
]
ARCHIVE_URL = "https://pir.sa.gov.au/fishing/sharks/shark_sightings_log/shark_sighting_log/archive/{year}_shark_sightings"
ARCHIVE_YEARS = range(2006, 2013)

# Old archive "Identifier" codes
REPORTER_CODES = {
    "P": "Public", "R": "Surf Life Saving SA", "F": "Aerial patrol (UniSA/SES)",
    "S": "SA Police", "A": "Abalone Divers Association", "W": "White Shark Count program",
}


def parse_dt(date_s: str, time_s: str) -> str:
    date_s, time_s = clean(date_s), clean(time_s).lower().replace(" ", "")
    for dfmt in ("%d %b %Y", "%d/%m/%y", "%d/%m/%Y"):
        try:
            d = datetime.strptime(date_s, dfmt)
            break
        except ValueError:
            continue
    else:
        return date_s
    for tfmt in ("%I:%M%p", "%I%p", "%H:%M", "%H%M"):
        try:
            t = datetime.strptime(time_s, tfmt)
            return d.replace(hour=t.hour, minute=t.minute, tzinfo=TZ).isoformat()
        except ValueError:
            continue
    return d.date().isoformat()


def _col(headers: list[str], *keys: str) -> int | None:
    for k in keys:
        for i, h in enumerate(headers):
            if k in h:
                return i
    return None


def parse_html(html: str, url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        headers = [clean(c.get_text()).lower() for c in rows[0].find_all(["th", "td"])]
        if not any("location" in h for h in headers):
            continue
        i_date = _col(headers, "date of sighting", "date")
        i_time = _col(headers, "time of sighting", "time")
        i_loc = _col(headers, "location")
        i_sp = _col(headers, "species")
        i_by = _col(headers, "reported by", "identifier")
        for tr in rows[1:]:
            cells = [clean(c.get_text(" ")) for c in tr.find_all(["td", "th"])]
            if len(cells) < 3 or i_loc is None or i_loc >= len(cells):
                continue
            g = lambda i: cells[i] if i is not None and i < len(cells) else ""  # noqa: E731
            by = g(i_by)
            by = REPORTER_CODES.get(by.upper(), by)
            out.append(event(
                SOURCE, "SA", "sighting", parse_dt(g(i_date), g(i_time)), g(i_loc),
                species=g(i_sp) or "Unknown", reported_by=by, url=url,
            ))
    return out


def collect() -> list[dict]:
    events, seen = [], set()
    urls = list(LIVE_URLS)
    if env_flag("BACKFILL"):
        urls += [ARCHIVE_URL.format(year=y) for y in ARCHIVE_YEARS]
    for url in urls:
        try:
            html = get(url).text
        except Exception as e:  # noqa: BLE001
            print(f"[{SOURCE}] skip {url}: {e}")
            continue
        save_raw(f"{SOURCE}_{abs(hash(url)) % 10**8}.html", html)
        for e in parse_html(html, url):
            if e["id"] not in seen:
                seen.add(e["id"])
                events.append(e)
    print(f"[{SOURCE}] {len(events)} events")
    return events


if __name__ == "__main__":
    from common import merge
    print("added", merge(collect()))
