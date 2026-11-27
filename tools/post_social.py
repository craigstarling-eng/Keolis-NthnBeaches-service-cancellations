"""Post the tracker's figures to the Facebook Page.

  python tools/post_social.py auto       -> run by the collector every 5 min; posts the afternoon summary once a day
  python tools/post_social.py summary    -> post the summary now
  python tools/post_social.py weekly     -> post last week's worst routes now (Mondays 9am automatically)
  add --dry-run to only build the image and text (saved in data/social/)

One post a day at POST_AT (Sydney time): today so far, the last 7 days and the last 30 days, with a link to the site.
Needs the GitHub secrets FB_PAGE_ID and FB_PAGE_TOKEN; without them nothing is posted.
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
STATE = os.path.join(OUT_DIR, "posted.json")
SITE = "https://nbbuswatch.github.io/"
SYD = ZoneInfo("Australia/Sydney")
FB_API = "https://graph.facebook.com/v26.0"
PAGE_ID = "1455302057670675"   # Northern Beaches Bus Watch (public)
POST_AT = (17, 0)        # 5:00 pm, after the school run and as the evening peak starts
WEEKLY_AT = (9, 0)       # Mondays 9:00 am: worst routes of the week just gone
MAX_TRIES = 3


def rows(path):
    with open(os.path.join(DATA, path), newline="") as f:
        return list(csv.DictReader(f))


def n(v):
    try:
        return int(float(v or 0))
    except ValueError:
        return 0


def pct(a, b):
    return f"{100 * a / b:.1f}%" if b else "–"


def period(daily, route_rows, dates):
    days = [r for r in daily if r["date"] in dates and n(r["scheduled"])]
    sched = sum(n(r["scheduled"]) for r in days)
    cxl = sum(n(r["cancelled"]) for r in days)
    obs = sum(n(r["observed"]) for r in days)
    on = sum(n(r.get("ontime")) for r in days)
    worst = Counter()
    for r in route_rows:
        if r["date"] in dates:
            worst[r["route"]] += n(r["cancelled"])
    top = worst.most_common(1)
    return {"days": len(days), "sched": sched, "cxl": cxl, "obs": obs, "on": on,
            "worst": top[0] if top and top[0][1] else None,
            "first": min((r["date"] for r in days), default=None)}


def figures():
    now = dt.datetime.now(SYD)
    today = now.date()
    daily = rows("history/r8_daily.csv")
    routes = rows("history/r8_routes_daily.csv")
    span = lambda k: {(today - dt.timedelta(days=i)).isoformat() for i in range(k)}
    d, w, m = period(daily, routes, span(1)), period(daily, routes, span(7)), period(daily, routes, span(30))
    if not d["sched"]:
        sys.exit("No figures for today yet; nothing to post.")
    def since(p, full):
        if p["days"] >= full:
            return ""
        return f" (since {dt.date.fromisoformat(p['first']).strftime('%-d %b')})"
    ontime = lambda p, need: f", {pct(p["on"], p["obs"])} left their first stop on time" if p["obs"] >= need else ""
    when = today.strftime("%A %-d %B")
    text = "\n".join([
        f"Northern Beaches buses – {when}",
        "",
        f"🚌 Today so far: {d['cxl']} Keolis services cancelled ({pct(d['cxl'], d['sched'])} of today's trips){ontime(d, 100)}.",
        f"📅 Last 7 days{since(w, 7)}: {w['cxl']:,} cancelled ({pct(w['cxl'], w['sched'])}){ontime(w, 300)}.",
        f"🗓️ Last 30 days{since(m, 30)}: {m['cxl']:,} cancelled ({pct(m['cxl'], m['sched'])}){ontime(m, 300)}.",
    ] + ([f"Most cancelled route this week: {w['worst'][0]} ({w['worst'][1]})."] if w["worst"] else []) + [
        "",
        "School and charter services are left out. Today's count includes cancellations already listed for tonight.",
        "",
        f"Check your bus and see the full figures: {SITE}",
    ])
    blocks = [("Today so far", d, ""), ("Last 7 days", w, since(w, 7).strip(" ()")),
              ("Last 30 days", m, since(m, 30).strip(" ()"))]
    alt = (f"Northern Beaches bus figures for {when}: today {d['cxl']} cancelled, last 7 days {w['cxl']}, "
           f"last 30 days {m['cxl']}.")
    return {"when": when, "blocks": blocks, "text": text, "alt": alt, "worst": w["worst"]}


def weekly_figures():
    """Monday to Sunday just gone: totals, change on the week before, and the five worst routes."""
    today = dt.datetime.now(SYD).date()
    end = today - dt.timedelta(days=today.weekday() + 1)          # last Sunday
    week = {(end - dt.timedelta(days=i)).isoformat() for i in range(7)}
    prev = {(end - dt.timedelta(days=i)).isoformat() for i in range(7, 14)}
    daily = rows("history/r8_daily.csv")
    routes = rows("history/r8_routes_daily.csv")
    w, p = period(daily, routes, week), period(daily, routes, prev)
    if not w["sched"]:
        sys.exit("No figures for last week yet; nothing to post.")
    per = {}
    for r in routes:
        if r["date"] in week:
            a = per.setdefault(r["route"], [0, 0])
            a[0] += n(r["scheduled"]); a[1] += n(r["cancelled"])
    worst = sorted(((rt, c, sc) for rt, (sc, c) in per.items() if c and sc >= 20), key=lambda x: (-x[1], -x[1] / x[2]))[:5]
    first = dt.date.fromisoformat(min(d for d in week)).strftime("%-d %b")
    when = f"{first} – {end.strftime('%-d %b')}"
    partial = f" ({w['days']} days tracked)" if w["days"] < 7 else ""
    change = ""
    if p["days"] == 7 and w["days"] == 7 and p["cxl"]:
        diff = w["cxl"] - p["cxl"]
        change = f" That's {abs(diff)} {'more' if diff > 0 else 'fewer'} than the week before." if diff else " Same as the week before."
    lines = [f"{i}. Route {rt}: {c} cancelled ({pct(c, sc)} of its trips)" for i, (rt, c, sc) in enumerate(worst, 1)]
    text = "\n".join([
        f"Worst Northern Beaches bus routes, week of {when}{partial} 🚌",
        "",
        f"{w['cxl']:,} Keolis services were cancelled ({pct(w['cxl'], w['sched'])} of scheduled trips).{change}",
        "",
        "Most cancelled routes:",
        *lines,
        "",
        "School and charter services are left out.",
        "",
        f"Check your route: {SITE}",
    ])
    return {"when": when + partial, "total": w, "worst": worst, "text": text}


def draw_weekly(f, path):
    im = Image.new("RGB", (1080, 1080), "#0f2747")
    d = ImageDraw.Draw(im)
    d.text((70, 70), "Worst routes last week", font=font(56, True), fill="white")
    d.text((70, 145), f"Keolis Northern Beaches · {f['when']}", font=font(32), fill="#9fc3f0")
    t = f["total"]
    d.text((70, 215), f"{t['cxl']:,} cancelled · {pct(t['cxl'], t['sched'])} of {t['sched']:,} trips", font=font(38, True), fill="white")
    top = max((c for _, c, _ in f["worst"]), default=1)
    y = 310
    for rt, c, sc in f["worst"]:
        d.rounded_rectangle((60, y, 1020, y + 105), radius=20, fill="#16335c")
        d.text((90, y + 22), rt, font=font(52, True), fill="white")
        w = int(420 * c / top)
        d.rounded_rectangle((300, y + 30, 300 + max(w, 8), y + 75), radius=8, fill="#e8a33d")
        lab = f"{c}  ({pct(c, sc)})"
        d.text((1000 - d.textlength(lab, font=font(32, True)), y + 33), lab, font=font(32, True), fill="white")
        y += 120
    d.text((70, 970), "Cancelled trips (share of route's trips) · School & charter excluded", font=font(24), fill="#6f8fb8")
    d.text((70, 1010), SITE.replace("https://", "").rstrip("/"), font=font(24), fill="#6f8fb8")
    im.save(path, "PNG", optimize=True)


def font(size, bold=False):
    p = f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf"
    return ImageFont.truetype(p, size) if os.path.exists(p) else ImageFont.load_default(size)


def draw_card(f, path):
    im = Image.new("RGB", (1080, 1080), "#0f2747")
    d = ImageDraw.Draw(im)
    d.text((70, 70), "Northern Beaches buses", font=font(52, True), fill="white")
    d.text((70, 140), f"Keolis cancellations · {f['when']}", font=font(34), fill="#9fc3f0")
    y = 215
    for label, p, note in f["blocks"]:
        d.rounded_rectangle((60, y, 1020, y + 205), radius=24, fill="#16335c")
        d.text((95, y + 25), label, font=font(36, True), fill="#9fc3f0")
        if note:
            d.text((95 + d.textlength(label, font=font(36, True)) + 18, y + 33), note, font=font(26), fill="#6f8fb8")
        d.text((92, y + 75), f"{p['cxl']:,}", font=font(96, True), fill="white")
        x = 110 + d.textlength(f"{p['cxl']:,}", font=font(96, True))
        d.text((x, y + 95), "cancelled", font=font(36), fill="white")
        d.text((x, y + 140), f"{pct(p['cxl'], p['sched'])} of {p['sched']:,} trips", font=font(30), fill="#cfe0f5")
        if p["obs"] >= 100:
            on = f"{pct(p['on'], p['obs'])}"
            d.text((1000 - d.textlength(on, font=font(52, True)), y + 80), on, font=font(52, True), fill="white")
            d.text((1000 - d.textlength("on time", font=font(28)), y + 145), "on time", font=font(28), fill="#cfe0f5")
        y += 230
    if f["worst"]:
        d.text((70, 915), f"Most cancelled route this week: {f['worst'][0]} ({f['worst'][1]})", font=font(32), fill="#cfe0f5")
    d.text((70, 970), "School & charter services excluded · Data: Transport for NSW", font=font(26), fill="#6f8fb8")
    d.text((70, 1010), SITE.replace("https://", "").rstrip("/"), font=font(24), fill="#6f8fb8")
    im.save(path, "PNG", optimize=True)


def post_facebook(text, image_path):
    page, token = (os.environ.get("FB_PAGE_ID", "").strip() or PAGE_ID), os.environ.get("FB_PAGE_TOKEN", "").strip()
    with open(image_path, "rb") as fh:
        r = requests.post(f"{FB_API}/{page}/photos", data={"caption": text, "access_token": token},
                          files={"source": ("card.png", fh, "image/png")}, timeout=60)
    if r.status_code >= 400:
        print("Facebook error:", re.sub(r"access_token=[^&\s]+", "access_token=***", r.text[:500]))
        return False
    print("Facebook: posted")
    return True


def configured():
    return bool((os.environ.get("FB_PAGE_ID", "").strip() or PAGE_ID) and os.environ.get("FB_PAGE_TOKEN", "").strip())


def publish(kind="summary", dry=False):
    os.makedirs(OUT_DIR, exist_ok=True)
    img = os.path.join(OUT_DIR, f"{kind}.png")
    if kind == "weekly":
        f = weekly_figures()
        draw_weekly(f, img)
    else:
        f = figures()
        draw_card(f, img)
    print(f["text"])
    if dry:
        return True
    try:
        return post_facebook(f["text"], img)
    except Exception as exc:
        print(f"Facebook failed: {type(exc).__name__}: {str(exc)[:200]}")
        return False


def auto():
    """Called every few minutes. Daily summary after POST_AT; weekly worst routes on Mondays after WEEKLY_AT.
    Each posts once, retrying a failed post up to MAX_TRIES times."""
    now = dt.datetime.now(SYD)
    if not configured():
        return
    try:
        with open(STATE) as fh:
            state = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    fb = state.setdefault("facebook", {})
    today = now.date().isoformat()
    due = []
    if now.weekday() == 0 and (now.hour, now.minute) >= WEEKLY_AT:
        due.append("weekly")
    if (now.hour, now.minute) >= POST_AT:
        due.append("summary")
    changed = False
    for kind in due:
        tries = fb.get(f"{kind}_tries", {}).get(today, 0)
        if fb.get(kind) == today or tries >= MAX_TRIES:
            continue
        try:
            ok = publish(kind)
        except SystemExit as e:  # no figures yet
            print(e)
            ok = False
        if ok:
            fb[kind] = today
        else:
            fb[f"{kind}_tries"] = {today: tries + 1}
        changed = True
    if changed:
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(STATE, "w") as fh:
            json.dump(state, fh, indent=1)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "auto"
    dry = "--dry-run" in sys.argv
    if mode == "auto":
        return auto()
    if not configured() and not dry:
        sys.exit("The Facebook Page isn't set up yet (add the FB_PAGE_ID and FB_PAGE_TOKEN secrets first).")
    if not publish(mode if mode in ("summary", "weekly") else "summary", dry=dry):
        sys.exit(1)


if __name__ == "__main__":
    main()
