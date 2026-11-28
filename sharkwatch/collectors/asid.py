"""Australian Shark-Incident Database (Taronga / Flinders / NSW DPI) via Zenodo.

Licence note: Taronga requires attribution and says the data "cannot be used for
commercial purposes without express permission". Fine for a free, ad-free site;
get written permission before monetising.
"""
from __future__ import annotations

import io

import pandas as pd

from common import clean, event, get, make_id, save_raw

SOURCE = "asid"
# Concept record (always resolves to the latest version)
ZENODO_LATEST = "https://zenodo.org/api/records/5612260/versions/latest"
FALLBACK_RECORD = "https://zenodo.org/api/records/18752301"

STATES = {
    "nsw": "NSW", "new south wales": "NSW", "qld": "QLD", "queensland": "QLD",
    "wa": "WA", "western australia": "WA", "sa": "SA", "south australia": "SA",
    "vic": "VIC", "victoria": "VIC", "tas": "TAS", "tasmania": "TAS",
    "nt": "NT", "northern territory": "NT", "act": "ACT",
}


def col(df, *names):
    norm = {c.lower().replace(".", " ").replace("_", " ").strip(): c for c in df.columns}
    for n in names:
        if n in norm:
            return norm[n]
    for n in names:
        for k, c in norm.items():
            if n in k:
                return c
    return None


def parse_df(df: pd.DataFrame, url: str) -> list[dict]:
    c = {
        "uin": col(df, "uin"),
        "year": col(df, "incident year", "year"),
        "month": col(df, "incident month", "month"),
        "day": col(df, "incident day", "day"),
        "state": col(df, "state"),
        "location": col(df, "location"),
        "lat": col(df, "latitude"),
        "lon": col(df, "longitude"),
        "species": col(df, "shark common name", "shark species", "species"),
        "length": col(df, "shark length m", "shark length"),
        "injury": col(df, "victim injury", "injury"),
        "activity": col(df, "victim activity", "activity"),
        "provoked": col(df, "provoked unprovoked", "provoked"),
        "site": col(df, "site category"),
    }
    out = []
    for i, row in df.iterrows():
        g = lambda k: clean(row[c[k]]) if c[k] else ""  # noqa: E731
        y = g("year").split(".")[0]
        if not y.isdigit():
            continue
        m, d = g("month").split(".")[0], g("day").split(".")[0]
        when = y + (f"-{int(m):02d}" if m.isdigit() else "") + (f"-{int(d):02d}" if m.isdigit() and d.isdigit() else "")
        details = "; ".join(f"{k}: {g(k)}" for k in ("injury", "activity", "provoked", "site") if g(k))
        out.append(event(
            SOURCE, STATES.get(g("state").lower(), g("state")), "incident", when, g("location"),
            lat=g("lat"), lon=g("lon"), species=g("species") or "Unknown", size_m=g("length"),
            details=details, reported_by="Australian Shark-Incident Database (Taronga)", url=url,
            id=make_id(SOURCE, g("uin") or i),
        ))
    return out


def collect() -> list[dict]:
    rec = None
    for u in (ZENODO_LATEST, FALLBACK_RECORD):
        try:
            rec = get(u).json()
            break
        except Exception as e:  # noqa: BLE001
            print(f"[{SOURCE}] {u}: {e}")
    if not rec:
        return []
    page = rec.get("links", {}).get("html", FALLBACK_RECORD)
    for f in rec.get("files", []):
        name = f.get("key", "")
        if not name.lower().endswith((".xlsx", ".csv")):
            continue
        link = f["links"]["self"]
        content = get(link, timeout=60).content
        save_raw(f"{SOURCE}_{name.replace(' ', '_')}", content)
        for header in (0, 1, 2):  # header row isn't always the first row
            df = (pd.read_csv(io.BytesIO(content), header=header) if name.endswith(".csv")
                  else pd.read_excel(io.BytesIO(content), header=header))
            if col(df, "incident year", "year"):
                break
        events = parse_df(df, page)
        print(f"[{SOURCE}] {len(events)} events from {name}")
        return events
    print(f"[{SOURCE}] no data file found in record")
    return []


if __name__ == "__main__":
    from common import merge
    print("added", merge(collect()))
