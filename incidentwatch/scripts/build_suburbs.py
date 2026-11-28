"""One-off: build data/suburbs.json  {SUBURB: [region_id, council, lat, lng]}
Source: github.com/michalsn/australian-suburbs data/suburbs.csv (MIT licence)."""
import csv, json, sys
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from regions import region_for_lga, clean_lga
out, miss = {}, set()
for r in csv.DictReader(open(sys.argv[1], encoding="utf-8")):
    if r["state"] != "NSW":
        continue
    reg = region_for_lga(r["local_goverment_area"])
    if not reg:
        if r["statistic_area"] == "Greater Sydney":
            miss.add(r["local_goverment_area"])
        continue
    lga = {"Botany Bay": "Bayside", "Rockdale": "Bayside"}.get(clean_lga(r["local_goverment_area"]), clean_lga(r["local_goverment_area"]))
    out.setdefault(r["suburb"].upper().strip(), [reg, lga, round(float(r["lat"]), 4), round(float(r["lng"]), 4)])
json.dump(dict(sorted(out.items())), open(sys.argv[2], "w"), separators=(",", ":"))
print(len(out), "suburbs; unmapped Greater Sydney councils:", miss)
