"""Post the tracker's figures to social media.

  python tools/post_social.py auto     -> run by the collector every 5 min; posts daily at 7:15am, weekly Mondays 7:30am
  python tools/post_social.py daily    -> post yesterday's figures now (+ cancellations already listed for today)
  python tools/post_social.py weekly   -> post the last 7 days now
  add --dry-run to only build the image and text

Posts to each platform whose secrets are set (others are skipped):
  Bluesky:        BLUESKY_HANDLE, BLUESKY_APP_PASSWORD
  Facebook Page:  FB_PAGE_ID, FB_PAGE_TOKEN
"""
import csv
import datetime as dt
import json
import os
import re
import sys
from collections import Counter
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")
OUT_DIR = os.path.join(DATA, "social")
SITE = "https://craigstarling-eng.github.io/Keolis-NthnBeaches-service-cancellations/"
SYD = ZoneInfo("Australia/Sydney")
FB_API = "https://graph.facebook.com/v26.0"


def rows(path):
    with open(os.path.join(DATA, path), newline="") as f:
        return list(csv.DictReader(f))


def n(v):
    try:
        return int(v or 0)
    except ValueError:
        return 0


def pct(a, b):
    return f"{100 * a / b:.1f}%" if b else "–"


def worst_route(route_rows, dates):
    agg = Counter()
    for r in route_rows:
        if r["date"] in dates:
            agg[r["route"]] += n(r["cancelled"])
    return agg.most_common(1)[0] if agg and agg.most_common(1)[0][1] else None


def daily_figures():
    today = dt.datetime.now(SYD).date()
    y = (today - dt.timedelta(days=1)).isoformat()
    day = next((r for r in rows("history/r8_daily.csv") if r["date"] == y), None)
    if not day or not n(day["scheduled"]):
        sys.exit(f"No figures for {y} yet; nothing to post.")
    live = json.load(open(os.path.join(DATA, "live.json")))
    wr = worst_route(rows("history/r8_routes_daily.csv"), {y})
    sched, cxl = n(day["scheduled"]), n(day["cancelled"])
    obs, on = n(day["observed"]), n(day.get("ontime"))
    gap = n(day.get("longest_gap_min"))
    when = dt.date.fromisoformat(y).strftime("%A %-d %B")
    lines = [f"{pct(cxl, sched)} of {sched:,} scheduled trips"]
    if obs >= 100:
        lines.append(f"{pct(on, obs)} left their first stop on time")
    if wr:
        lines.append(f"Most cancellations: route {wr[0]} ({wr[1]})")
    if gap:
        lines.append(f"Longest wait caused: {gap} min on route {day.get('longest_gap_route', '').split(' to ')[0].strip()}")
    ahead = live.get("count") if live.get("date") == today.isoformat() else None
    text = (f"Northern Beaches buses, {when}: {cxl} Keolis services cancelled ({pct(cxl, sched)} of scheduled trips)."
            + (f" {pct(on, obs)} left their first stop on time." if obs >= 100 else "")
            + (f" Most cancellations: route {wr[0]}." if wr else "")
            + (f" Already {ahead} cancellations listed for today." if ahead else ""))
    return {"title": "Yesterday", "when": when, "big": cxl, "lines": lines, "text": text,
            "alt": f"Northern Beaches bus figures for {when}: {cxl} services cancelled, " + "; ".join(lines) + "."}


def weekly_figures():
    today = dt.datetime.now(SYD).date()
    dates = {(today - dt.timedelta(days=i)).isoformat() for i in range(1, 8)}
    days = [r for r in rows("history/r8_daily.csv") if r["date"] in dates]
    if not days:
        sys.exit("No figures for the last week yet; nothing to post.")
    sched = sum(n(r["scheduled"]) for r in days)
    cxl = sum(n(r["cancelled"]) for r in days)
    obs = sum(n(r["observed"]) for r in days)
    on = sum(n(r.get("ontime")) for r in days)
    wr = worst_route(rows("history/r8_routes_daily.csv"), dates)
    worst_day = max(days, key=lambda r: n(r["cancelled"]))
    first = dt.date.fromisoformat(min(r["date"] for r in days)).strftime("%-d %b")
    last = dt.date.fromisoformat(max(r["date"] for r in days)).strftime("%-d %b")
    when = f"{first} – {last}" + (f" ({len(days)} day{'s' if len(days) != 1 else ''} tracked)" if len(days) < 7 else "")
    lines = [f"{pct(cxl, sched)} of {sched:,} scheduled trips",
             f"Worst day: {dt.date.fromisoformat(worst_day['date']).strftime('%A')} ({n(worst_day['cancelled'])} cancelled)"]
    if obs >= 300:
        lines.insert(1, f"{pct(on, obs)} left their first stop on time")
    if wr:
        lines.append(f"Most cancellations: route {wr[0]} ({wr[1]})")
    text = (f"Northern Beaches buses, week of {when}: {cxl} Keolis services cancelled ({pct(cxl, sched)} of scheduled trips)."
            + (f" {pct(on, obs)} left their first stop on time." if obs >= 300 else "")
            + (f" Most cancellations: route {wr[0]}." if wr else ""))
    return {"title": "This week", "when": when, "big": cxl, "lines": lines, "text": text,
            "alt": f"Northern Beaches bus figures for {when}: {cxl} services cancelled, " + "; ".join(lines) + "."}


def font(size, bold=False):
    for p in (f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf",):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size)


def draw_card(f, path):
    im = Image.new("RGB", (1080, 1080), "#0f2747")
    d = ImageDraw.Draw(im)
    d.text((80, 90), "Northern Beaches buses", font=font(50, True), fill="white")
    d.text((80, 165), f"{f['title']} · {f['when']}", font=font(36), fill="#9fc3f0")
    d.text((72, 250), f"{f['big']:,}", font=font(220, True), fill="white")
    d.text((80, 510), "services cancelled", font=font(50), fill="white")
    y = 610
    for line in f["lines"]:
        size = 38
        while size > 24 and d.textlength(line, font=font(size)) > 920:
            size -= 2
        d.text((80, y), line, font=font(size), fill="#cfe0f5")
        y += 66
    d.text((80, 945), "Keolis Northern Beaches (Region 8) · Data: Transport for NSW", font=font(28), fill="#6f8fb8")
    d.text((80, 990), SITE.replace("https://", "").rstrip("/"), font=font(26), fill="#6f8fb8")
    im.save(path, "PNG", optimize=True)


# ---------------------------------------------------------------- platforms
def post_bluesky(text, image_path, alt):
    handle, pw = os.environ.get("BLUESKY_HANDLE", "").strip().lstrip("@"), os.environ.get("BLUESKY_APP_PASSWORD", "").strip()
    pds = "https://bsky.social/xrpc"
    s = requests.post(f"{pds}/com.atproto.server.createSession", json={"identifier": handle, "password": pw}, timeout=30)
    s.raise_for_status()
    sess = s.json()
    auth = {"Authorization": f"Bearer {sess['accessJwt']}"}
    with open(image_path, "rb") as fh:
        blob = requests.post(f"{pds}/com.atproto.repo.uploadBlob", data=fh.read(),
                             headers={**auth, "Content-Type": "image/png"}, timeout=60)
    blob.raise_for_status()
    body = text + "\n\n" + SITE
    if len(body) > 300:
        body = text[: 300 - len(SITE) - 3].rstrip() + "…\n" + SITE
    start = len(body.encode("utf-8")) - len(SITE.encode("utf-8"))
    record = {
        "$type": "app.bsky.feed.post",
        "text": body,
        "createdAt": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "langs": ["en-AU"],
        "facets": [{"index": {"byteStart": start, "byteEnd": start + len(SITE.encode("utf-8"))},
                    "features": [{"$type": "app.bsky.richtext.facet#link", "uri": SITE}]}],
        "embed": {"$type": "app.bsky.embed.images",
                  "images": [{"alt": alt[:1000], "image": blob.json()["blob"],
                              "aspectRatio": {"width": 1080, "height": 1080}}]},
    }
    r = requests.post(f"{pds}/com.atproto.repo.createRecord", headers=auth,
                      json={"repo": sess["did"], "collection": "app.bsky.feed.post", "record": record}, timeout=30)
    r.raise_for_status()
    print("Bluesky: posted")
    return True


def post_facebook(text, image_path):
    page, token = os.environ.get("FB_PAGE_ID", "").strip(), os.environ.get("FB_PAGE_TOKEN", "").strip()
    with open(image_path, "rb") as fh:
        r = requests.post(f"{FB_API}/{page}/photos", data={"caption": text + "\n\nFull figures: " + SITE, "access_token": token},
                          files={"source": ("card.png", fh, "image/png")}, timeout=60)
    if r.status_code >= 400:
        print("Facebook error:", re.sub(r"access_token=[^&\s]+", "access_token=***", r.text[:500]))
        return False
    print("Facebook: posted")
    return True


STATE = os.path.join(OUT_DIR, "posted.json")
PLATFORMS = {
    "bluesky": ("BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD"),
    "facebook": ("FB_PAGE_ID", "FB_PAGE_TOKEN"),
}
DAILY_AT, WEEKLY_AT = (7, 15), (7, 30)   # Sydney time; weekly goes out on Mondays


def configured():
    return [p for p, keys in PLATFORMS.items() if all(os.environ.get(k, "").strip() for k in keys)]


def publish(mode, platforms, dry=False):
    f = weekly_figures() if mode == "weekly" else daily_figures()
    os.makedirs(OUT_DIR, exist_ok=True)
    img = os.path.join(OUT_DIR, f"{mode}.png")
    draw_card(f, img)
    print(f["text"])
    done = []
    if dry:
        return done
    for p in platforms:
        try:
            ok = post_bluesky(f["text"], img, f["alt"]) if p == "bluesky" else post_facebook(f["text"], img)
        except Exception as exc:  # one platform failing shouldn't stop the other
            print(f"{p} failed: {type(exc).__name__}: {str(exc)[:200]}")
            ok = False
        if ok:
            done.append(p)
    return done


def auto():
    """Called every few minutes by the collector. Posts once per day/week per platform, retrying on failure."""
    now = dt.datetime.now(SYD)
    try:
        with open(STATE) as fh:
            state = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    today = now.date().isoformat()
    jobs = []
    if (now.hour, now.minute) >= DAILY_AT:
        jobs.append(("daily", today))
    if now.weekday() == 0 and (now.hour, now.minute) >= WEEKLY_AT:
        jobs.append(("weekly", today))
    changed = False
    for mode, key in jobs:
        due = [p for p in configured() if state.get(p, {}).get(mode) != key
               and state.get(p, {}).get(f"{mode}_tries", {}).get(key, 0) < 3]
        if not due:
            continue
        done = publish(mode, due)
        for p in due:
            st = state.setdefault(p, {})
            if p in done:
                st[mode] = key
            else:
                tries = st.setdefault(f"{mode}_tries", {})
                tries.clear() if key not in tries else None
                tries[key] = tries.get(key, 0) + 1
        changed = True
    if changed:
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(STATE, "w") as fh:
            json.dump(state, fh, indent=1)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "auto"
    if mode == "auto":
        return auto()
    platforms = configured()
    if not platforms and "--dry-run" not in sys.argv:
        sys.exit("No social media accounts are set up yet (add the GitHub secrets first).")
    done = publish(mode, platforms, dry="--dry-run" in sys.argv)
    if "--dry-run" not in sys.argv and len(done) < len(platforms):
        sys.exit(1)


if __name__ == "__main__":
    main()
