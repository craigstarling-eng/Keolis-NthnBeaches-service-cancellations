# Sydney Incident Watch

A free, independent daily breakdown of **police, fire and road incidents** across Sydney's regions, built only from official NSW open data. Runs on GitHub Actions and GitHub Pages at no cost.

- Site: `https://nbbuswatch.github.io/incidentwatch/` (pick a region; add `#northern-beaches`, `#eastern-suburbs` and so on to link straight to one)
- Updates every 15 minutes. The daily Facebook post goes out at 5:30pm and the weekly summary on Monday at 8am.

## Data sources

| Category | Source | Notes |
|---|---|---|
| 👮 Police | NSW Police media releases (police.nsw.gov.au/news) | Only incidents police **publicise**. The time shown is when the release was published. |
| 🔥 Fire | NSW RFS current incidents feed + Fire & Rescue NSW media releases | The RFS feed covers bush/grass fires and other RFS-listed jobs. FRNSW has no live feed, so only its releases are counted. |
| 🚗 Road | Live Traffic NSW hazards API (TfNSW Open Data) | Unplanned incidents: crashes, breakdowns and hazards, plus who attended (police, fire, ambulance) where listed. |

Incidents are placed in a region by suburb name, using the suburb-to-council list in `data/suburbs.json` (built from github.com/michalsn/australian-suburbs, MIT licence).

**Regions:** Northern Beaches · North Shore & Ryde · Hills & Hornsby · City & Inner West · Eastern Suburbs · St George & Sutherland · Canterbury-Bankstown · Parramatta, Cumberland & Blacktown · South West Sydney · Penrith, Hawkesbury & Blue Mountains · Central Coast. Each region is a group of council areas, defined in `scripts/regions.py`.

## Setup

This lives in the `incidentwatch/` folder of the `nbbuswatch.github.io` repo, which serves it at https://nbbuswatch.github.io/incidentwatch/. It reuses the repo's `TFNSW_API_KEY` secret. Its workflows are `incidentwatch-collect` and `incidentwatch-post`.

- **Facebook:** add the secrets `IW_FB_PAGE_ID` and `IW_FB_PAGE_TOKEN` for a separate Incident Watch Page. They are deliberately separate from the Bus Watch secrets, so nothing ever posts to the Bus Watch page. Without them, posts are saved to `incidentwatch/post/latest.png` and `latest.txt` for copy-paste.
- **Region for posts:** set the repository variable `IW_REGION`, e.g. `eastern-suburbs` or `all`. The default is `northern-beaches`.
- **Live Traffic:** your TfNSW Open Data application must include the **Live Traffic Hazards** API.

## Honesty rules (shown on the site)

- Counts cover publicised and official-feed incidents, **not every job police or firefighters attend**. Every post says this.
- When a source is down, the site shows ⚠️ next to it and posts say counts may be low. A post never calls a day "quiet" while data is missing.
- No commentary, no blame. The site and posts only state what the data shows.

## Files

- `scripts/collect.py`: pulls the feeds, keeps 14 days of incidents in `data/events.json`, keeps daily counts for all time in `data/stats.json`, and archives older incidents to `data/archive/`.
- `scripts/post.py`: builds the Facebook post image and text, and posts them.
- `scripts/regions.py`: region definitions.
- `scripts/build_suburbs.py`: rebuilds `data/suburbs.json`. This only needs to run again if you change the regions.
- `index.html`: the website.
