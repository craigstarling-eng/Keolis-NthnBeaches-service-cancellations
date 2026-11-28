"""Offline tests: sample data shaped like each real source."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collectors"))

import asid  # noqa: E402
import qld_scp  # noqa: E402
import sa_pirsa  # noqa: E402
from browser_capture import extract  # noqa: E402

SA_HTML = """<html><body><h2>Recent sightings</h2><table>
<tr><th>Date</th><th>Time</th><th>Location</th><th>Shark species</th><th>Reported by</th></tr>
<tr><td>22 Nov 2026</td><td>2:17pm</td><td>Carrickalinga</td><td>Unknown</td><td>SAPOL</td></tr>
<tr><td>17 Oct 2026</td><td>12:00pm</td><td>Streaky Bay (aka Cape Bauer)</td><td>White Shark</td><td>Public</td></tr>
<tr><td>04 Oct 2026</td><td>10:30am</td><td>Louth Bay</td><td>Unknown</td><td>Public</td></tr>
</table></body></html>"""

SA_ARCHIVE = """<table>
<tr><td>Date of Report</td><td>Time of Report</td><td>Date of Sighting</td><td>Time of Sighting</td>
<td>Sighting Location</td><td>Shark Species</td><td>Identifier</td></tr>
<tr><td>31/12/08</td><td>16:55</td><td>31/12/08</td><td>16:45</td><td>150m off Port Willunga</td><td>Unknown</td><td>F</td></tr>
</table>"""


def test_sa_live():
    ev = sa_pirsa.parse_html(SA_HTML, "u")
    assert len(ev) == 3
    assert ev[0]["occurred_at"] == "2026-11-22T14:17:00+10:30"
    assert ev[1]["species"] == "White Shark" and ev[1]["state"] == "SA"
    assert len({e["id"] for e in ev}) == 3


def test_sa_archive():
    ev = sa_pirsa.parse_html(SA_ARCHIVE, "u")
    assert ev[0]["occurred_at"].startswith("2008-12-31T16:45")
    assert ev[0]["reported_by"] == "Aerial patrol (UniSA/SES)"


def test_qld():
    df = pd.DataFrame({
        "Species Name": ["Tiger Shark", "Bull Whaler"],
        "Date": ["03/07/2023", "15/01/2024"],
        "Area": ["Gold Coast", "Cairns"],
        "Location": ["Burleigh Heads", "Palm Cove"],
        "Length (m)": [3.1, 2.2],
        "Fate": ["Released alive", "Deceased"],
        "Number": [1, 1],
    })
    ev = qld_scp.parse_df(df, "https://x/file.csv", "statewide")
    assert ev[0]["occurred_at"] == "2023-07-03"
    assert ev[0]["location"] == "Burleigh Heads, Gold Coast"
    assert "Released alive" in ev[0]["details"]


def test_asid():
    df = pd.DataFrame({
        "UIN": [1, 2], "Incident.month": [1, 11], "Incident.year": [2026, 1791],
        "State": ["NSW", "WA"], "Location": ["Dee Why", "Somewhere"],
        "Latitude": [-33.75, -31.9], "Longitude": [151.29, 115.8],
        "Shark.common.name": ["bull shark", None], "Victim.injury": ["injured", "fatal"],
    })
    ev = asid.parse_df(df, "z")
    assert ev[0]["occurred_at"] == "2026-01" and ev[0]["state"] == "NSW"
    assert ev[1]["species"] == "Unknown"


def test_browser_extract_geojson_and_flat():
    payloads = [
        {"url": "a", "json": {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [151.2879, -33.7969]},
             "properties": {"id": 991, "alertType": "Tagged shark detection",
                            "title": "Tagged White Shark detected by North Steyne receiver",
                            "detectedAt": "2026-11-28T06:10:30+11:00", "beach": "North Steyne Beach"}}]}},
        {"url": "b", "json": {"alerts": [
            {"alertId": "x7", "category": "Drone", "message": "Bull shark sighted by SLS drone",
             "date": 1795850646528, "lat": -33.89, "lng": 151.27, "location": "Bondi Beach"},
            {"id": 5, "name": "settings", "theme": "dark"},  # noise: must be ignored
        ]}},
    ]
    ev = extract(payloads, "nsw_sharksmart", "NSW", "page")
    assert len(ev) == 2, ev
    det = next(e for e in ev if e["location"] == "North Steyne Beach")
    assert det["event_type"] == "detection" and det["species"] == "White Shark"
    assert det["lat"] == "-33.7969" and det["lon"] == "151.2879"
    drone = next(e for e in ev if e["location"] == "Bondi Beach")
    assert drone["event_type"] == "drone" and drone["occurred_at"].startswith("2026-")
