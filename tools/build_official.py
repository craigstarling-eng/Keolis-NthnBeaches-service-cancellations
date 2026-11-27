"""Turn Transport for NSW's official spreadsheets into data/official/official.json for the dashboard.

Uses:
  * otr-for-publishing-smbsct*.xlsx  - monthly on-time running for every Sydney contract region since 2010
  * busperformance_reports*.xlsx     - monthly Sydney-wide cancellations, untracked trips, complaints
"""
import datetime as dt
import glob
import json
import os
import re

import openpyxl

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "..", "data", "official", "raw")
OUT = os.path.join(HERE, "..", "data", "official", "official.json")
HOME_REGION = 8


def newest(pattern):
    files = sorted(glob.glob(os.path.join(RAW, pattern)), key=os.path.getmtime)
    return files[-1] if files else None


def month_key(v):
    if isinstance(v, (dt.datetime, dt.date)):
        return f"{v.year:04d}-{v.month:02d}"
    return None


def parse_otr(path):
    ws = openpyxl.load_workbook(path, read_only=True, data_only=True).worksheets[0]
    months = []
    regions = {}
    for row in ws.iter_rows(values_only=True):
        first = str(row[0] or "").strip()
        if first == "Contract":
            months = [month_key(v) for v in row[2:]]
            continue
        m = re.match(r"^(GSBC|SMBSC|SBSC)0*(\d+)", first)
        if not m or not months:
            continue
        num = int(m.group(2))
        operator = str(row[1] or "").strip()
        reg = regions.setdefault(num, {})
        for mk, v in zip(months, row[2:]):
            if mk and isinstance(v, (int, float)) and 0 < v <= 1:
                reg[mk] = [round(v * 100, 1), operator]
    return regions


def parse_perf(path):
    ws = openpyxl.load_workbook(path, read_only=True, data_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    hdr = [str(h or "").strip() for h in rows[0]]
    ix = {h: i for i, h in enumerate(hdr)}
    out = []
    for r in rows[1:]:
        if r[ix["Region"]] != "GS" or r[ix["OTR"]] is None:
            continue

        def p(col):
            v = r[ix[col]]
            return round(v * 100, 2) if isinstance(v, (int, float)) else None
        out.append({"month": month_key(r[ix["Month"]]), "otr": p("OTR"), "cancelled": p("% of services cancelled"),
                    "untracked": p("Untracked trips"), "complaints": r[ix["Complaints per 100K"]]})
    return out


def main():
    otr_file, perf_file = newest("otr-for-publishing-smbsct*.xlsx"), newest("busperformance_reports*.xlsx")
    regions = parse_otr(otr_file) if otr_file else {}
    months = sorted({m for reg in regions.values() for m in reg})
    home = regions.get(HOME_REGION, {})
    series = []
    for m in months:
        others = [reg[m][0] for n, reg in regions.items() if n != HOME_REGION and m in reg]
        series.append({"month": m, "home": home.get(m, [None])[0], "home_operator": home.get(m, [None, ""])[1],
                       "others_avg": round(sum(others) / len(others), 1) if others else None})
    latest = months[-12:]
    ranking = []
    for n, reg in regions.items():
        vals = [reg[m][0] for m in latest if m in reg]
        if len(vals) >= 6:
            ranking.append({"region": n, "operator": reg[max(m for m in reg)][1], "otr_12m": round(sum(vals) / len(vals), 1),
                            "home": n == HOME_REGION})
    ranking.sort(key=lambda r: -r["otr_12m"])
    out = {
        "built": dt.datetime.now().isoformat(timespec="seconds"),
        "source_files": [os.path.basename(f) for f in (otr_file, perf_file) if f],
        "latest_month": months[-1] if months else None,
        "otr_series": series,
        "otr_ranking_12m": ranking,
        "sydney_monthly": parse_perf(perf_file) if perf_file else [],
        # every region's monthly on-time running, so the site can chart whichever region is chosen
        "by_region": {str(n): {m: v[0] for m, v in sorted(reg.items())} for n, reg in sorted(regions.items())},
    }
    with open(OUT, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"official.json: {len(series)} months, {len(ranking)} regions ranked")


if __name__ == "__main__":
    main()
