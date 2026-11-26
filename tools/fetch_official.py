"""Download Transport for NSW's official bus performance spreadsheets into data/official/raw/."""
import json
import os
import re
import sys
import urllib.request

BASE = "https://opendata.transport.nsw.gov.au/data/api/3/action/package_show?id="
DATASETS = ["874aa503-80e0-4335-8ef1-5ffcd5269197", "buses-on-time-running",
            "2-greater-sydney-outer-metropolitan-bus-service-performance-data"]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "official", "raw")
os.makedirs(OUT, exist_ok=True)
key = os.environ.get("TFNSW_API_KEY", "").strip()


def get(url, auth=False):
    req = urllib.request.Request(url, headers={"User-Agent": "nb-bus-tracker"})
    if auth and key:
        req.add_header("Authorization", f"apikey {key}")
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


found = []
for ds in DATASETS:
    try:
        pkg = json.loads(get(BASE + ds))["result"]
    except Exception as exc:
        print("dataset", ds, "failed:", exc)
        continue
    for res in pkg.get("resources", []):
        url = res.get("url", "")
        print(ds, "|", res.get("name"), "|", res.get("format"), "|", url)
        if re.search(r"\.(xlsx|csv)(\?|$)", url, re.I):
            name = re.sub(r"[^A-Za-z0-9._-]", "_", url.rsplit("/", 1)[-1].split("?")[0])
            for auth in (False, True):
                try:
                    data = get(url, auth)
                    if data[:2] == b"PK" or url.lower().endswith(".csv"):
                        with open(os.path.join(OUT, name), "wb") as f:
                            f.write(data)
                        found.append(name)
                        print("  saved", name, len(data))
                        break
                except Exception as exc:
                    print("  download failed", "(with key)" if auth else "", exc)
print("files:", found)
if not found:
    sys.exit(1)
