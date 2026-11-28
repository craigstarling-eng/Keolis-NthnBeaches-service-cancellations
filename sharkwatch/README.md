# Shark Watch AU — data collectors

Pulls shark sightings, tagged-shark detections, catch data and historical bite
incidents from official Australian sources into one dataset, every 15 minutes,
using GitHub Actions. This is the data layer for the Shark Watch website.

> Information only — not a safety service. The site must always point people to
> the official SharkSmart apps (NSW, WA), PIRSA (SA) and lifesavers.

## Sources

| Source | State | What | How | Schedule |
|---|---|---|---|---|
| `sa_pirsa` | SA | Public sightings table (police, public, SLS, aerial patrol) | HTML scrape | 15 min |
| `nsw_sharksmart` | NSW | Tagged detections, drone sightings, drumline tagging, incidents | Headless browser captures the map's JSON | 15 min |
| `wa_sharksmart` | WA | Water Police / SLSWA sightings, Shark Monitoring Network detections | Headless browser captures the map's JSON | 15 min |
| `qld_scp` | QLD | Shark Control Program catches, 2001 → now (CC BY 4.0) | data.qld.gov.au CKAN API | daily |
| `asid` | All | Australian Shark-Incident Database, 1791 → now | Zenodo API | daily |

Not covered (no public feed): VIC, TAS, NT. Dorsal is private — don't scrape it.

## Output (in `data/`)

- `events.csv` — every event, one schema (see `collectors/common.py → FIELDS`). Grows over time; deduped by `id`.
- `latest.json` — newest 200 live events (for the homepage feed).
- `summary.json` — counts: last 24h / 7d by state, top beaches and species (30d), monthly trend, incidents by year.
- `status.json` — last run result per source (use it for a "data health" badge).
- `raw/*_endpoints.json` — what the NSW/WA maps actually loaded (see below).

`event_type` values: `sighting`, `detection`, `drone`, `tagging`, `catch`, `incident`, `alert`, `closure`.

## How it runs

Lives in the `sharkwatch/` folder of the nbbuswatch.github.io repo and runs from
`.github/workflows/sharkwatch.yml`, separately from the bus collector. Data is
public at `https://nbbuswatch.github.io/sharkwatch/data/…`.

Manual run: **Actions → Shark Watch collect → Run workflow** (group `all`, backfill `1` the first time).

## The NSW / WA first run

NSW and WA don't publish an API. Their maps load JSON in the browser, so the
collector opens the page headlessly and keeps every JSON response. After the
first run, `data/raw/nsw_sharksmart_endpoints.json` lists the real endpoints
(the NSW map is served from `map-v2.pivotanalytics.com.au`). The generic
extractor maps anything with coordinates/dates into events; once the endpoints
are known, swap in an exact mapping that calls them directly.

## Licences / attribution to show on the site

- NSW & WA SharkSmart, PIRSA — © state governments; credit and link back.
- QLD Shark Control Program — CC BY 4.0, "Department of Primary Industries, Queensland".
- ASID — credit "Australian Shark-Incident Database, Taronga Conservation Society Australia".
  **Non-commercial only without written permission** — ask Taronga before adding ads.

## Run locally

```bash
pip install -r requirements.txt && python -m playwright install chromium
cd collectors
python run.py all
python -m pytest ../tests   # offline parser tests
```
