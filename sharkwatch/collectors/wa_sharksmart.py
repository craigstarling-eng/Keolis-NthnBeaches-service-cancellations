"""WA SharkSmart — Water Police / SLSWA sightings and Shark Monitoring Network detections.
The Shark Activity Map has no published API, so we capture what it loads."""
from browser_capture import collect as _collect

SOURCE = "wa_sharksmart"
PAGE = "https://www.sharksmart.com.au/shark-activity"


def collect():
    return _collect(SOURCE, "WA", PAGE, click_texts=("Accept", "I agree", "Map"))


if __name__ == "__main__":
    from common import merge
    print("added", merge(collect()))
