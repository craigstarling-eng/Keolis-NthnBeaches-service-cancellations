"""Daily / weekly Facebook post for Sydney Incident Watch.

  python scripts/post.py daily   -> today's summary   (run ~5:30pm Sydney)
  python scripts/post.py weekly  -> last Mon-Sun      (run Monday ~8am Sydney)

Always writes post/latest.png + post/latest.txt (so you can copy-paste by hand).
Posts to the Facebook Page only if FB_PAGE_ID and FB_PAGE_TOKEN are set.
REGION env picks the region (default northern-beaches); SITE_URL the link.
"""
import datetime as dt, json, os, random, sys, urllib.parse, urllib.request, uuid
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(__file__))
from regions import REGIONS

TZ = ZoneInfo("Australia/Sydney")
ROOT = os.path.join(os.path.dirname(__file__), "..")
DATA, OUT = os.path.join(ROOT, "data"), os.path.join(ROOT, "post")
REGION = os.environ.get("REGION", "northern-beaches")
SITE = os.environ.get("SITE_URL", "https://nbbuswatch.github.io/incidentwatch/")
CATS = [("police", "Police", "👮", (37, 125, 224)), ("fire", "Fire", "🔥", (244, 114, 48)), ("road", "Road", "🚗", (0, 168, 136))]
TAGS = {
    "northern-beaches": "#NorthernBeaches #Manly #DeeWhy #MonaVale #SydneyNews",
    "all": "#Sydney #SydneyNews #NSW",
}
NOW = dt.datetime.now(TZ)


def load(n):
    return json.load(open(os.path.join(DATA, n)))


def summed(stats, days):
    out = {k: {"n": 0, "types": {}, "charged": 0} for k, *_ in CATS}
    for d in days:
        for k, *_ in CATS:
            x = stats.get(d, {}).get(k)
            if not x:
                continue
            out[k]["n"] += x["n"]
            out[k]["charged"] += x.get("charged", 0)
            for t, v in x.get("types", {}).items():
                out[k]["types"][t] = out[k]["types"].get(t, 0) + v
    return out


def iso(d):
    return d.isoformat()


KEEP = {"domestic violence": "DV", "drugs": "drugs", "other": "other"}


def plural(t, n):
    t = t.lower()
    if t in KEEP or "/" in t:
        return KEEP.get(t, t)
    if n == 1:
        return t
    return t + "es" if t.endswith(("sh", "ch", "x", "s")) else t + "s"


def top_types(c, k=3):
    return ", ".join(f"{v} {plural(t, v)}" for t, v in sorted(c["types"].items(), key=lambda x: -x[1])[:k])


def compare(n, avg):
    if avg is None:
        return ""
    if avg < 0.5 and n == 0:
        return "about usual"
    diff = round((n - avg) / avg * 100) if avg else 100
    if abs(diff) < 10:
        return "about usual"
    return f"{'up' if diff > 0 else 'down'} {abs(diff)}% on usual"


# ---------------------------------------------------------------- image
BGS = [((255, 248, 240), (255, 231, 214)), ((240, 248, 255), (214, 232, 252)), ((242, 252, 247), (212, 244, 230)),
       ((250, 245, 255), (233, 222, 252)), ((255, 252, 238), (252, 240, 200)), ((245, 247, 250), (225, 230, 238)),
       ((255, 243, 246), (252, 220, 230)), ((238, 250, 252), (205, 238, 245))]


def font(size, bold=False):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf" % ("-Bold" if bold else ""),
              "/usr/share/fonts/truetype/liberation/LiberationSans-%s.ttf" % ("Bold" if bold else "Regular")):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def card(title, subtitle, rows, footer, path):
    W = H = 1080
    top, bot = random.choice(BGS)
    img = Image.new("RGB", (W, H), top)
    d = ImageDraw.Draw(img)
    for yy in range(H):  # soft vertical gradient
        t = yy / H
        d.line([(0, yy), (W, yy)], fill=tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)))
    ink, sub = (25, 25, 25), (95, 102, 112)
    d.text((70, 70), "SYDNEY INCIDENT WATCH", font=font(30, True), fill=sub)
    d.text((70, 115), title, font=font(64, True), fill=ink)
    d.text((70, 195), subtitle, font=font(34), fill=sub)
    y = 290
    for (k, name, _, col), (n, cmp, tops) in rows:
        d.rounded_rectangle([60, y, W - 60, y + 210], radius=28, fill=(255, 255, 255))
        d.rounded_rectangle([60, y, 78, y + 210], radius=9, fill=col)
        d.text((110, y + 28), name.upper(), font=font(30, True), fill=sub)
        d.text((110, y + 66), str(n), font=font(96, True), fill=ink)
        nx = 110 + d.textlength(str(n), font=font(96, True)) + 30
        d.text((nx, y + 92), cmp, font=font(32), fill=sub)
        txt = tops or "Nothing recorded"
        f = font(28)
        while d.textlength(txt, font=f) > W - 200 and "," in txt:
            txt = txt.rsplit(",", 1)[0]
        d.text((110, y + 170), txt, font=f, fill=ink)
        y += 235
    d.text((70, H - 70), footer, font=font(28), fill=sub)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path, optimize=True)


# ---------------------------------------------------------------- posts
def daily(stats, name):
    today = NOW.date()
    cur = summed(stats, [iso(today)])
    hist_days = [iso(today - dt.timedelta(days=i)) for i in range(1, 29)]
    have = [x for x in hist_days if x in stats]
    base = summed(stats, have) if len(have) >= 7 else None
    week = summed(stats, [iso(today - dt.timedelta(days=i)) for i in range(today.weekday() + 1)])
    rows, lines = [], []
    for c in CATS:
        k = c[0]
        n = cur[k]["n"]
        avg = base[k]["n"] / len(have) if base else None
        cmp = compare(n, avg)
        tops = top_types(cur[k])
        extra = f" ({cur[k]['charged']} with arrests/charges)" if k == "police" and cur[k]["charged"] else ""
        lines.append(f"{c[2]} {c[1]}: {n}{extra}" + (f" — {tops}" if tops else "") + (f" ({cmp})" if cmp else ""))
        rows.append((c, (n, cmp, tops)))
    total = sum(cur[k]["n"] for k, *_ in CATS)
    wk = " · ".join(f"{c[2]} {week[c[0]]['n']}" for c in CATS)
    head = f"🚨 {name} — {NOW:%a %-d %b}" if total else f"✅ {name} — {NOW:%a %-d %b}: a quiet day"
    msg = "\n".join([head, "", *lines, "", f"📅 Week so far: {wk}", "",
                     f"🔗 Full breakdown & incident log: {SITE}#{REGION}", "",
                     "Counts publicised police incidents, RFS/FRNSW fire incidents and Live Traffic road incidents from official NSW data — not every callout.",
                     "", TAGS.get(REGION, "#Sydney #SydneyNews")])
    card(name, f"{NOW:%A %-d %B} · today so far", rows, SITE.replace("https://", "").rstrip("/"), os.path.join(OUT, "latest.png"))
    return msg


def weekly(stats, name):
    today = NOW.date()
    mon = today - dt.timedelta(days=today.weekday() + 7)
    days = [iso(mon + dt.timedelta(days=i)) for i in range(7)]
    prev = [iso(mon - dt.timedelta(days=7 - i)) for i in range(7)]
    cur, before = summed(stats, days), summed(stats, prev)
    have_prev = any(x in stats for x in prev)
    rows, lines = [], []
    for c in CATS:
        k = c[0]
        n, p = cur[k]["n"], before[k]["n"]
        cmp = "" if not have_prev else ("same as last week" if n == p else f"{'up' if n > p else 'down'} {abs(n - p)} on last week")
        tops = top_types(cur[k])
        lines.append(f"{c[2]} {c[1]}: {n}" + (f" — {tops}" if tops else "") + (f" ({cmp})" if cmp else ""))
        rows.append((c, (n, cmp, tops)))
    busiest = max(days, key=lambda x: sum(stats.get(x, {}).get(k, {}).get("n", 0) for k, *_ in CATS))
    span = f"{mon:%-d %b} – {mon + dt.timedelta(days=6):%-d %b}"
    msg = "\n".join([f"📊 {name} — week of {span}", "", *lines, "",
                     f"📈 Busiest day: {dt.date.fromisoformat(busiest):%A}", "",
                     f"🔗 Full breakdown: {SITE}#{REGION}", "",
                     "Counts publicised police incidents, RFS/FRNSW fire incidents and Live Traffic road incidents from official NSW data — not every callout.",
                     "", TAGS.get(REGION, "#Sydney #SydneyNews")])
    card(name, f"Weekly summary · {span}", rows, SITE.replace("https://", "").rstrip("/"), os.path.join(OUT, "latest.png"))
    return msg


def fb_post(msg, img_path):
    page, token = os.environ.get("FB_PAGE_ID"), os.environ.get("FB_PAGE_TOKEN")
    if not (page and token):
        print("FB_PAGE_ID / FB_PAGE_TOKEN not set — wrote post/latest.* only")
        return
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in (("caption", msg), ("access_token", token)):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="source"; filename="post.png"\r\nContent-Type: image/png\r\n\r\n'.encode()
                 + open(img_path, "rb").read() + b"\r\n")
    body = b"".join(parts) + f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"https://graph.facebook.com/v21.0/{page}/photos", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        print("Posted:", r.read().decode()[:200])


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    force = "--force" in sys.argv
    state_path = os.path.join(OUT, "state.json")
    state = json.load(open(state_path)) if os.path.exists(state_path) else {}
    key = f"{mode}:{NOW.date()}"
    if mode == "daily" and not force and not (17 <= NOW.hour <= 19):
        print(f"Not posting: it's {NOW:%H:%M} in Sydney (daily post runs 5–7pm)")
        return
    if mode == "weekly" and not force and not (NOW.weekday() == 0 and 7 <= NOW.hour <= 9):
        print(f"Not posting: weekly post runs Monday 7–9am Sydney")
        return
    if state.get(mode) == str(NOW.date()) and not force:
        print("Already posted", key)
        return
    stats = load("stats.json").get(REGION, {})
    name = "All of Sydney" if REGION == "all" else REGIONS[REGION][0]
    msg = daily(stats, name) if mode == "daily" else weekly(stats, name)
    try:
        status = load("status.json")
        down = [n for k, n in (("police", "police releases"), ("rfs", "RFS feed"), ("frnsw", "FRNSW releases"), ("road", "Live Traffic"))
                if status.get(k, {}).get("error") and status[k]["error"][:16] > status[k].get("ok", "")[:16]]
        if down:
            msg = msg.replace(": a quiet day", "", 1).replace("✅", "ℹ️", 1).replace("\n\n🔗", f"\n\n⚠️ Some data was unavailable ({', '.join(down)}) — counts may be low.\n\n🔗", 1)
    except FileNotFoundError:
        pass
    open(os.path.join(OUT, "latest.txt"), "w").write(msg)
    print(msg)
    fb_post(msg, os.path.join(OUT, "latest.png"))
    state[mode] = str(NOW.date())
    json.dump(state, open(state_path, "w"))


if __name__ == "__main__":
    main()
