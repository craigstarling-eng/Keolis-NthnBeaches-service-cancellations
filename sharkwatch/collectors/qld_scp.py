"""Queensland Shark Control Program catch statistics (data.qld.gov.au, CC BY 4.0).

Uses the CKAN API to list every CSV/XLS(X) resource in the two datasets,
downloads each one and maps its columns into the shared schema.
Updated by QLD roughly annually/periodically -> run daily, not every 15 min.
"""
from __future__ import annotations

import io

import pandas as pd

from common import clean, event, get, make_id, save_raw

SOURCE = "qld_scp"
CKAN = "https://www.data.qld.gov.au/api/3/action/package_show?id={id}"
DATASETS = {
    "bb4eda21-d85d-4782-803d-c67393fb2da9": "statewide",  # Shark Control Program catch statistics by year
    "def48c7b-fe46-4566-8f64-fc831156f464": "gbrmp",      # Great Barrier Reef Marine Park subset
}

COLS = {
    "species": ["species name", "species", "common name"],
    "date": ["date of capture", "capture date", "date"],
    "area": ["area"],
    "location": ["location", "beach"],
    "length": ["length (m)", "length", "size"],
    "sex": ["sex"],
    "fate": ["fate"],
    "number": ["number", "no.", "count", "total"],
    "gear": ["gear", "equipment", "method"],
}


def pick(df: pd.DataFrame, key: str) -> str | None:
    lower = {c.lower().strip(): c for c in df.columns}
    for cand in COLS[key]:
        for lc, orig in lower.items():
            if lc == cand or lc.startswith(cand):
                return orig
    return None


def read_table(content: bytes, fmt: str) -> pd.DataFrame:
    """Sniff the real format: some QLD 'csv' files are actually Excel and vice versa."""
    if content[:2] == b"PK":
        return pd.read_excel(io.BytesIO(content), engine="openpyxl")
    if content[:4] == b"\xd0\xcf\x11\xe0":
        return pd.read_excel(io.BytesIO(content), engine="xlrd")
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return pd.read_csv(io.BytesIO(content), encoding=enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("unreadable file")


def to_date(v) -> str:
    v = clean(v)
    if not v:
        return ""
    try:
        return pd.to_datetime(v, dayfirst=True).date().isoformat()
    except (ValueError, TypeError):
        return v


def _rehead(df: pd.DataFrame) -> pd.DataFrame:
    """If the real header row is a few rows down (title rows above it), move it up."""
    if pick(df, "species"):
        return df
    for i in range(min(10, len(df))):
        vals = [str(v).strip().lower() for v in df.iloc[i].tolist()]
        if any(v.startswith("species") or v == "common name" for v in vals):
            new = df.iloc[i + 1:].copy()
            new.columns = [str(v).strip() for v in df.iloc[i].tolist()]
            return new.reset_index(drop=True)
    return df


def parse_df(df: pd.DataFrame, url: str, dataset: str) -> list[dict]:
    df = _rehead(df)
    c = {k: pick(df, k) for k in COLS}
    if not c["species"] or not c["date"]:
        print(f"[{SOURCE}] unrecognised columns in {url}: {list(df.columns)[:10]}")
        return []
    out = []
    for i, row in df.iterrows():
        g = lambda k: clean(row[c[k]]) if c[k] else ""  # noqa: E731
        if not g("species"):
            continue
        loc = ", ".join(x for x in (g("location"), g("area")) if x)
        details = "; ".join([f"dataset: {dataset}"] + [f"{k}: {g(k)}" for k in ("fate", "sex", "gear") if g(k)])
        out.append(event(
            SOURCE, "QLD", "catch", to_date(row[c["date"]]), loc,
            species=g("species"), size_m=g("length"), count=g("number") or "1",
            details=details, reported_by="QLD Shark Control Program", url=url,
            id=make_id(SOURCE, dataset, url.rsplit("/", 1)[-1], i),
        ))
    return out


DATASTORE = "https://www.data.qld.gov.au/api/3/action/datastore_search?resource_id={rid}&limit=5000&offset={off}"
SLUGS = {
    "statewide": "shark-control-program-shark-catch-statistics",
    "gbrmp": "qld-shark-control-program-catch-statistics-great-barrier-reef-marine-park",
}


def package(ds_id: str, label: str) -> dict | None:
    for key in (ds_id, SLUGS[label]):
        try:
            j = get(CKAN.format(id=key), expect_file=True).json()
            if j.get("success") and j.get("result"):
                return j["result"]
            print(f"[{SOURCE}] package_show {key}: {str(j)[:200]}")
        except Exception as e:  # noqa: BLE001
            print(f"[{SOURCE}] package_show {key}: {e}")
    return None


def datastore_df(rid: str) -> pd.DataFrame | None:
    """Read a resource through the CKAN datastore API (works when file downloads are blocked)."""
    rows, off = [], 0
    while True:
        j = get(DATASTORE.format(rid=rid, off=off), expect_file=True).json()
        if not j.get("success"):
            return None
        recs = j["result"].get("records", [])
        rows += recs
        if len(recs) < 5000:
            break
        off += 5000
    if not rows:
        return None
    df = pd.DataFrame(rows)
    return df.drop(columns=[c for c in df.columns if c in ("_id", "_full_text")])


def collect() -> list[dict]:
    events = []
    for ds_id, label in DATASETS.items():
        pkg = package(ds_id, label)
        if not pkg:
            continue
        for res in pkg.get("resources", []):
            fmt = (res.get("format") or res["url"].rsplit(".", 1)[-1]).lower()
            if fmt not in {"csv", "xls", "xlsx"}:
                continue
            url = res["url"]
            df = None
            try:
                if res.get("datastore_active"):
                    df = datastore_df(res["id"])
                if df is None:
                    content = get(url, expect_file=True, timeout=60).content
                    save_raw(f"{SOURCE}_{label}_{res['id'][:8]}.{fmt}", content)
                    df = read_table(content, fmt)
                found = parse_df(df, url, label)
                print(f"[{SOURCE}] {len(found)} rows <- {res.get('name', url)[:70]}")
                events += found
            except Exception as e:  # noqa: BLE001
                print(f"[{SOURCE}] skip {res.get('name', url)[:70]}: {e}")
    print(f"[{SOURCE}] {len(events)} events")
    return events


if __name__ == "__main__":
    from common import merge
    print("added", merge(collect()))
