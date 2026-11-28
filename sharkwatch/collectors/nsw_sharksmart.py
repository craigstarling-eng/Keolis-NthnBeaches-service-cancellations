"""NSW SharkSmart — tagged-shark detections, drone sightings, drumline tagging, incidents.
The public map (map-v2.pivotanalytics.com.au, embedded on sharksmart.nsw.gov.au) has no
published API, so we capture what the map loads. See browser_capture.py."""
from browser_capture import collect as _collect

SOURCE = "nsw_sharksmart"
PAGE = "https://www.sharksmart.nsw.gov.au/shark-activity"


def collect():
    return _collect(SOURCE, "NSW", PAGE, click_texts=("Open shark map",), wait_ms=25000)


if __name__ == "__main__":
    from common import merge
    print("added", merge(collect()))
