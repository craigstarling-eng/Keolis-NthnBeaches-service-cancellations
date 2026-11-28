"""One-off check: what vehicle IDs do Keolis Northern Beaches buses send in the live feed?"""
import os, collections, requests
from google.transit import gtfs_realtime_pb2
hdr = {"Authorization": "apikey " + os.environ["TFNSW_API_KEY"]}
r = requests.get("https://api.transport.nsw.gov.au/v1/gtfs/vehiclepos/buses", headers=hdr, timeout=120)
r.raise_for_status()
f = gtfs_realtime_pb2.FeedMessage(); f.ParseFromString(r.content)
shown = 0
for e in f.entity:
    if not e.HasField("vehicle"):
        continue
    v = e.vehicle
    short = v.trip.route_id.split("_")[-1]
    if short in {"B1", "199", "E65", "136", "151", "155", "178", "180X"} and shown < 20:
        shown += 1
        print(f"route={v.trip.route_id} entity={e.id} id={v.vehicle.id!r} label={v.vehicle.label!r} plate={v.vehicle.license_plate!r}")
        print("   raw:", str(v.vehicle).replace("\n", " | ")[:300])
print("buses in feed:", sum(1 for e in f.entity if e.HasField("vehicle")), "matched:", shown)
