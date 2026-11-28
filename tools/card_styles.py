"""Pool of 20 light, colourful designs for the Facebook post images.

10 different layouts x 2 colourways. The daily post rotates through them by date (a different layout every day);
the Monday weekly post uses the day's theme colours on its own layout.

  python tools/card_styles.py   -> renders every style with today's figures into data/social/styles/
"""
import math
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(HERE, "..", "fonts")
NAVY, GREY, LIGHT = "#0f2747", "#4a5a6e", "#6b7a8c"
RATINGS = [("Good", "#2e9e3e"), ("Fair", "#f2c200"), ("Poor", "#f08a00"), ("Bad", "#d62d20"), ("Extremely bad", "#8e0e0e")]
FOOT = "Keolis Region 8 only · School & charter excluded · Data: TfNSW"
SITE = "nbbuswatch.github.io"

_fonts = {}


def F(size, w="Regular"):
    key = (size, w)
    if key not in _fonts:
        p = os.path.join(FONT_DIR, f"Inter-{w}.otf")
        if os.path.exists(p):
            _fonts[key] = ImageFont.truetype(p, size)
        else:
            dv = f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'' if w in ('Regular', 'Medium') else '-Bold'}.ttf"
            _fonts[key] = ImageFont.truetype(dv, size) if os.path.exists(dv) else ImageFont.load_default(size)
    return _fonts[key]


def FA(size):
    """Anton, tall condensed poster font."""
    key = (size, "anton")
    if key not in _fonts:
        p = os.path.join(FONT_DIR, "Anton-Regular.ttf")
        _fonts[key] = ImageFont.truetype(p, size) if os.path.exists(p) else F(size, "Black")
    return _fonts[key]


def FS(size, bold=True):
    """Newspaper serif."""
    key = (size, "serif", bold)
    if key not in _fonts:
        p = os.path.join(FONT_DIR, f"LiberationSerif-{'Bold' if bold else 'Regular'}.ttf")
        _fonts[key] = ImageFont.truetype(p, size) if os.path.exists(p) else F(size, "Bold" if bold else "Regular")
    return _fonts[key]


def fitf(dr, text, mk, size, maxw, minsize=14):
    while size > minsize and dr.textlength(text, font=mk(size)) > maxw:
        size -= 2
    return mk(size)


def rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mix(a, b, t):
    a, b = rgb(a) if isinstance(a, str) else a, rgb(b) if isinstance(b, str) else b
    return tuple(int(x + (y - x) * t) for x, y in zip(a, b))


def fit(dr, text, w, size, maxw, minsize=14):
    while size > minsize and dr.textlength(text, font=F(size, w)) > maxw:
        size -= 2
    return F(size, w)


# ---------------------------------------------------------------- helpers
def vgrad(im, box, top, bottom):
    x0, y0, x1, y1 = box
    dr = ImageDraw.Draw(im)
    for y in range(y0, y1):
        dr.line((x0, y, x1, y), fill=mix(top, bottom, (y - y0) / max(y1 - y0 - 1, 1)))


def card(im, box, radius=28, fill="white", blur=16, off=7, alpha=50):
    x0, y0, x1, y1 = box
    sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((x0, y0 + off, x1, y1 + off), radius, fill=(15, 39, 71, alpha))
    im.alpha_composite(sh.filter(ImageFilter.GaussianBlur(blur)))
    ImageDraw.Draw(im).rounded_rectangle(box, radius, fill=fill)
    return ImageDraw.Draw(im)


def dial(im, cx, cy, r, score, label, ring="#ffffff", needle=NAVY, sub=GREY, show_name=True):
    dr = ImageDraw.Draw(im)
    ro = r + 12
    dr.pieslice((cx - ro, cy - ro, cx + ro, cy + ro), 180, 360, fill=ring)
    for i, (_, col) in enumerate(RATINGS):
        dr.pieslice((cx - r, cy - r, cx + r, cy + r), 180 + 36 * i, 216 + 36 * i, fill=col, outline=ring, width=max(3, r // 20))
    hr = r * .45
    dr.pieslice((cx - hr, cy - hr, cx + hr, cy + hr), 180, 360, fill=ring)
    a = math.radians(180 - score * 36)
    k = max(8, r // 10)
    tip = (cx + (r - 6) * math.cos(a), cy - (r - 6) * math.sin(a))
    b1 = (cx + k * math.cos(a + math.pi / 2), cy - k * math.sin(a + math.pi / 2))
    b2 = (cx + k * math.cos(a - math.pi / 2), cy - k * math.sin(a - math.pi / 2))
    dr.polygon([tip, b1, b2], fill=needle)
    dr.ellipse((cx - k * 1.4, cy - k * 1.4, cx + k * 1.4, cy + k * 1.4), fill=needle)
    name, col = RATINGS[min(int(score), 4)]
    if show_name:
        dr.text((cx, cy + 30), label.upper(), font=F(22, "SemiBold"), fill=sub, anchor="mm")
        dr.text((cx, cy + 62), name.upper(), font=fit(dr, name.upper(), "ExtraBold", 30, 2 * r + 30), fill=col, anchor="mm")


def ring(dr, cx, cy, r, pct, col, track, width=24):
    box = (cx - r, cy - r, cx + r, cy + r)
    dr.arc(box, 0, 360, fill=track, width=width)
    if pct is not None:
        dr.arc(box, -90, -90 + 360 * pct / 100, fill=col, width=width)


def bar(dr, x, y, wd, h, frac, col, track="#edf0f4"):
    dr.rounded_rectangle((x, y, x + wd, y + h), h // 2, fill=track)
    if frac:
        dr.rounded_rectangle((x, y, x + max(int(wd * min(frac, 1)), h), y + h), h // 2, fill=col)


def bus(dr, x, y, s, body, win, wheel="#222", light="#ffd54f"):
    dr.rounded_rectangle((x, y, x + 2.2 * s, y + s), int(s * .18), fill=body)
    for i in range(4):
        wx = x + .14 * s + i * .5 * s
        dr.rounded_rectangle((wx, y + .14 * s, wx + .4 * s, y + .48 * s), int(s * .06), fill=win)
    if light:
        dr.rectangle((x + 2.2 * s - .22 * s, y + .6 * s, x + 2.2 * s - .05 * s, y + .82 * s), fill=light)
    for wx in (x + .45 * s, x + 1.7 * s):
        dr.ellipse((wx - .17 * s, y + .83 * s, wx + .17 * s, y + 1.17 * s), fill=wheel)


def footer(dr, y, col, strong=None):
    dr.text((70, y), FOOT, font=F(24), fill=col)
    dr.text((70, y + 36), SITE, font=F(26, "Bold"), fill=strong or col)


# ---------------------------------------------------------------- layouts
# D = dict from post_social.card_data(): when, at, cxl, sched, cxl_pct, on_pct (None if too few), obs,
# am/pm (pct str, cancelled), worst (route, n) or None, wk_cxl, wk_pct, wk_on, score, rating, rcol


def L_beach(D, t):
    im = Image.new("RGBA", (1080, 1080), t["bg"])
    vgrad(im, (0, 0, 1080, 330), t["h1"], t["h2"])
    dr = ImageDraw.Draw(im)
    dr.polygon([(x, 300 + 22 * math.sin(x / 1080 * 3 * math.pi)) for x in range(0, 1081, 10)] + [(1080, 1080), (0, 1080)], fill=t["bg"])
    dr.text((70, 62), "Northern Beaches buses", font=F(54, "ExtraBold"), fill="white")
    dr.text((70, 134), f"{D['when']} · as at {D['at']}", font=F(32, "Medium"), fill=mix("#ffffff", t["h2"], .12))
    dr.rounded_rectangle((70, 192, 340, 240), 24, fill=t["pill"])
    dr.text((205, 216), "Today's bus report", font=F(24, "SemiBold"), fill="white", anchor="mm")
    card(im, (790, 50, 1030, 240), 30, alpha=45)
    dial(im, 910, 158, 82, D["score"], "Today")
    dr = card(im, (60, 330, 1020, 620))
    dr.text((100, 365), "CANCELLED TODAY", font=F(26, "Bold"), fill=t["label"])
    dr.text((95, 400), f"{D['cxl']:,}", font=fit(dr, f"{D['cxl']:,}", "Black", 150, 560), fill=t["hero"])
    dr.text((100, 565), f"{D['cxl_pct']} of {D['sched']:,} trips", font=F(32, "SemiBold"), fill=GREY)
    ring(dr, 860, 475, 100, D["on_pct"], t["ring"], mix(t["ring"], "#ffffff", .85), 26)
    dr.text((860, 462), f"{D['on_pct']:.0f}%" if D["on_pct"] is not None else "–", font=F(52, "ExtraBold"), fill=NAVY, anchor="mm")
    dr.text((860, 510), "on time", font=F(26, "SemiBold"), fill=GREY, anchor="mm")
    tiles = [("Morning peak", D["am"][0], f"{D['am'][1]} cancelled", t["a1"]),
             ("Evening peak", D["pm"][0], f"{D['pm'][1]} cancelled", t["a2"]),
             ("Worst route", D["worst"][0] if D["worst"] else "–", f"{D['worst'][1]} cancelled" if D["worst"] else "none", t["hero"])]
    for i, (lab, big, small, col) in enumerate(tiles):
        x = 60 + i * 330
        dr = card(im, (x, 660, x + 300, 880), 26)
        dr.rounded_rectangle((x, 660, x + 300, 672), 6, fill=col)
        dr.text((x + 28, 695), lab, font=F(26, "SemiBold"), fill=GREY)
        dr.text((x + 26, 735), big, font=fit(dr, big, "ExtraBold", 64, 250), fill=NAVY)
        dr.text((x + 28, 825), small, font=F(24), fill=LIGHT)
    wk = f"Last 7 days: {D['wk_cxl']:,} cancelled ({D['wk_pct']})" + (f" · {D['wk_on']} on time" if D["wk_on"] else "")
    dr.text((70, 912), wk, font=fit(dr, wk, "SemiBold", 28, 940), fill=t["label"])
    footer(dr, 970, "#7c8a9a")
    return im


def L_status(D, t):
    rc = D["rcol"]
    soft = t.get("soft")
    ink = mix(rc, "#000000", .25) if soft else "white"
    im = Image.new("RGBA", (1080, 1080), t["bg"])
    dr = ImageDraw.Draw(im)
    dr.rectangle((0, 0, 1080, 470), fill=mix(rc, "#ffffff", .82) if soft else rc)
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    tex = t["tex"]
    if tex == "stripes":
        for x in range(-500, 1100, 90):
            od.polygon([(x, 0), (x + 40, 0), (x + 510, 470), (x + 470, 470)], fill=(255, 255, 255, 16))
    elif tex == "dots":
        for y in range(20, 470, 44):
            for x in range(20 + (y // 44 % 2) * 22, 1080, 44):
                od.ellipse((x - 5, y - 5, x + 5, y + 5), fill=(255, 255, 255, 30))
    elif tex == "waves":
        for k, y0 in enumerate(range(60, 520, 70)):
            od.line([(x, y0 + 14 * math.sin(x / 60 + k)) for x in range(0, 1081, 8)], fill=(255, 255, 255, 28), width=6)
    elif tex == "bus":
        bus(od, 560, 200, 170, (255, 255, 255, 26), (255, 255, 255, 0), (255, 255, 255, 26), None)
    elif tex == "split":
        od.polygon([(620, 0), (1080, 0), (1080, 470), (380, 470)], fill=(255, 255, 255, 22))
    elif tex == "rings":
        for k in range(6):
            r = 120 + k * 90
            od.ellipse((900 - r, 120 - r, 900 + r, 120 + r), outline=rgb(rc) + (40,), width=10)
    od.rectangle((0, 470, 1080, 1080), fill=(0, 0, 0, 0))
    im.alpha_composite(ov)
    dr = ImageDraw.Draw(im)
    bus(dr, 70, 70, 38, ink, mix(rc, "#ffffff", .82) if soft else rc, ink, None)
    dr.text((175, 66), "NORTHERN BEACHES BUS WATCH", font=F(27, "ExtraBold"), fill=ink)
    dr.text((175, 102), f"{D['when']} · as at {D['at']}", font=F(24, "SemiBold"), fill=ink)
    dr.text((70, 165), "Today's service was", font=F(40, "SemiBold"), fill=ink)
    dr.text((66, 325), D["rating"].upper(), font=fit(dr, D["rating"].upper(), "Black", 132, 940), fill=ink, anchor="ls")
    sub = f"{D['cxl']} trips cancelled" + (f" · {D['on_pct']:.0f}% on time" if D["on_pct"] is not None else "")
    dr.text((70, 365), sub, font=F(36, "Bold"), fill=ink)
    card(im, (820, 40, 1020, 190), 26, alpha=45)
    dial(im, 920, 122, 62, D["score"], "Today")
    dr = card(im, (60, 500, 1020, 950), 30)
    dr.text((100, 530), "How today compares", font=F(34, "ExtraBold"), fill=NAVY)
    rows = [("Cancelled today", D["cxl_f"] * 10, D["cxl_pct"], rc),
            ("Cancelled, last 7 days", D["wk_f"] * 10, D["wk_pct"], "#8a96a8"),
            ("On time today", (D["on_pct"] or 0) / 100, f"{D['on_pct']:.1f}%" if D["on_pct"] is not None else "–", "#2e9e3e"),
            ("On time, last 7 days", D["wk_on_f"], D["wk_on"] or "–", "#8a96a8")]
    y = 590
    for lab, frac, val, col in rows:
        dr.text((100, y), lab, font=F(28, "SemiBold"), fill=GREY)
        dr.text((980, y), val, font=F(30, "ExtraBold"), fill=NAVY, anchor="ra")
        bar(dr, 100, y + 42, 880, 22, frac, col)
        y += 76
    if D["worst"]:
        dr.text((100, y + 4), f"Worst route today: {D['worst'][0]} ({D['worst'][1]} cancelled)", font=F(28, "Bold"), fill=rc)
    footer(dr, 975, "#7c8a9a")
    return im


def L_sky(D, t):
    im = Image.new("RGBA", (1080, 1080))
    vgrad(im, (0, 0, 1080, 1080), t["s1"], t["s2"])
    vgrad(im, (0, 0, 1080, 460), t["s1"], mix(t["s1"], t["s2"], .55))
    glow = Image.new("RGBA", im.size, (0, 0, 0, 0))
    sx, sy = t.get("sun_at", (990, 40))
    ImageDraw.Draw(glow).ellipse((sx - 160, sy - 160, sx + 160, sy + 160), fill=rgb(t["sun"]) + (150,))
    im.alpha_composite(glow.filter(ImageFilter.GaussianBlur(40)))
    dr = ImageDraw.Draw(im)
    dr.ellipse((sx - 90, sy - 90, sx + 90, sy + 90), fill=t["sun"])
    dr.polygon([(x, 395 + 10 * math.sin(x / 70)) for x in range(0, 1081, 10)] + [(1080, 460), (0, 460)], fill=t["sea"])
    dr.rectangle((0, 455, 1080, 480), fill=t["sand"])
    bus(dr, 640, 360, 90, t["bus"], "#e8f3ff")
    dr.text((70, 70), "Northern Beaches", font=F(64, "Black"), fill=NAVY)
    dr.text((70, 145), "bus report", font=F(64, "Black"), fill=t["t2"])
    dr.text((70, 240), f"{D['when']}\nas at {D['at']}", font=F(30, "SemiBold"), fill=NAVY, spacing=8)
    cw = dr.textlength(f"Service: {D['rating']}", font=F(30, "ExtraBold"))
    dr.rounded_rectangle((70, 340, 160 + cw, 400), 30, fill="white")
    dr.ellipse((88, 354, 120, 386), fill=D["rcol"])
    dr.text((135, 370), f"Service: {D['rating']}", font=F(30, "ExtraBold"), fill=NAVY, anchor="lm")
    cards = [("Cancelled today", f"{D['cxl']:,}", f"{D['cxl_pct']} of {D['sched']:,}", "#e8604c"),
             ("On time today", f"{D['on_pct']:.0f}%" if D["on_pct"] is not None else "–", f"of {D['obs']:,} tracked", "#2e9e3e"),
             ("Last 7 days", f"{D['wk_cxl']:,}", f"cancelled ({D['wk_pct']})", t["bus"]),
             ("Worst route", D["worst"][0] if D["worst"] else "–", f"{D['worst'][1]} cancelled today" if D["worst"] else "none today", "#e07b00")]
    for i, (lab, big, small, col) in enumerate(cards):
        x, y = 60 + (i % 2) * 490, 520 + (i // 2) * 210
        dr = card(im, (x, y, x + 470, y + 185), 28, alpha=40)
        dr.ellipse((x + 30, y + 32, x + 50, y + 52), fill=col)
        dr.text((x + 62, y + 42), lab, font=F(28, "Bold"), fill=GREY, anchor="lm")
        dr.text((x + 28, y + 70), big, font=fit(dr, big, "Black", 72, 410), fill=col)
        dr.text((x + 30, y + 150), small, font=F(24, "SemiBold"), fill=LIGHT)
    footer(dr, 960, "#5b6b80")
    return im


def L_bigdial(D, t):
    im = Image.new("RGBA", (1080, 1080), t["bg"])
    dr = ImageDraw.Draw(im)
    # soft blobs
    bl = Image.new("RGBA", im.size, rgb(t["bg"]) + (255,))
    bd = ImageDraw.Draw(bl)
    bd.ellipse((-200, -220, 420, 360), fill=rgb(t["blob1"]) + (255,))
    bd.ellipse((760, 260, 1300, 800), fill=rgb(t["blob2"]) + (255,))
    im.alpha_composite(bl.filter(ImageFilter.GaussianBlur(60)))
    dr = ImageDraw.Draw(im)
    dr.text((540, 70), "NORTHERN BEACHES BUS WATCH", font=F(30, "ExtraBold"), fill=t["acc"], anchor="mm")
    dr.text((540, 112), f"{D['when']} · as at {D['at']}", font=F(28, "SemiBold"), fill=GREY, anchor="mm")
    dr = card(im, (190, 160, 890, 560), 40, alpha=40)
    dial(im, 540, 395, 200, D["score"], "", show_name=False)
    dr = ImageDraw.Draw(im)
    dr.text((540, 470), "TODAY'S SERVICE", font=F(26, "Bold"), fill=GREY, anchor="mm")
    dr.text((540, 518), D["rating"].upper(), font=fit(dr, D["rating"].upper(), "Black", 56, 640), fill=D["rcol"], anchor="mm")
    chips = [(f"{D['cxl']:,}", "cancelled", D["cxl_pct"] + " of trips"),
             (f"{D['on_pct']:.0f}%" if D["on_pct"] is not None else "–", "on time", f"{D['obs']:,} tracked"),
             (D["worst"][0] if D["worst"] else "–", "worst route", f"{D['worst'][1]} cancelled" if D["worst"] else "none")]
    for i, (big, lab, small) in enumerate(chips):
        x = 60 + i * 330
        dr = card(im, (x, 600, x + 300, 820), 30, alpha=40)
        dr.text((x + 150, 670), big, font=fit(dr, big, "Black", 76, 260), fill=t["acc"], anchor="mm")
        dr.text((x + 150, 740), lab, font=F(30, "Bold"), fill=NAVY, anchor="mm")
        dr.text((x + 150, 782), small, font=F(24), fill=LIGHT, anchor="mm")
    dr.rounded_rectangle((60, 850, 1020, 920), 35, fill=t["acc"])
    wk = f"Last 7 days: {D['wk_cxl']:,} cancelled ({D['wk_pct']})" + (f" · {D['wk_on']} on time" if D["wk_on"] else "")
    dr.text((540, 885), wk, font=fit(dr, wk, "Bold", 30, 900), fill="white", anchor="mm")
    footer(dr, 960, "#6b7a8c", NAVY)
    return im


def _stats(D):
    on = f"{D['on_pct']:.0f}%" if D["on_pct"] is not None else "–"
    wr = D["worst"][0] if D["worst"] else "–"
    wn = f"{D['worst'][1]} cancelled" if D["worst"] else "none today"
    return on, wr, wn


def L_ticket(D, t):
    on, wr, wn = _stats(D)
    im = Image.new("RGBA", (1080, 1080), t["bg"])
    dr = ImageDraw.Draw(im)
    for y in range(0, 1080, 60):   # subtle diagonal pattern
        dr.line((0, y, y, 0), fill=mix(t["bg"], "#ffffff", .35), width=14)
    for y in range(0, 1080, 60):
        dr.line((1080 - y, 1080, 1080, 1080 - y), fill=mix(t["bg"], "#ffffff", .35), width=14)
    x0, y0, x1, y1 = 90, 80, 990, 1000
    card(im, (x0, y0, x1, y1), 34, t["paper"], 20, 10, 60)
    dr = ImageDraw.Draw(im)
    dr.rounded_rectangle((x0, y0, x1, y0 + 120), 34, fill=t["acc"])
    dr.rectangle((x0, y0 + 60, x1, y0 + 120), fill=t["acc"])
    bus(dr, x0 + 45, y0 + 38, 30, "white", t["acc"], "white", None)
    dr.text((x0 + 130, y0 + 60), "BUS WATCH DAILY TICKET", font=F(36, "Black"), fill="white", anchor="lm")
    dr.text((x1 - 40, y0 + 60), "No. " + D["when"].split()[1].zfill(2), font=F(26, "Bold"), fill="white", anchor="rm")
    rows = [("DATE", D["when"]), ("CHECKED AT", D["at"]), ("SERVICE", D["rating"].upper()),
            ("TRIPS CANCELLED", f"{D['cxl']:,}  ({D['cxl_pct']})"), ("ON TIME", on), ("WORST ROUTE", f"{wr}  ({wn})"),
            ("LAST 7 DAYS", f"{D['wk_cxl']:,} cancelled ({D['wk_pct']})")]
    y = y0 + 160
    for lab, val in rows:
        dr.text((x0 + 60, y), lab, font=F(24, "Bold"), fill=t["muted"])
        col = D["rcol"] if lab == "SERVICE" else NAVY
        dr.text((x1 - 60, y - 6), val, font=fit(dr, val, "ExtraBold", 36, 560), fill=col, anchor="ra")
        y += 72
        dr.line((x0 + 60, y - 22, x1 - 60, y - 22), fill=mix(t["paper"], "#000000", .08), width=2)
    # perforation + notches
    py = 760
    for x in range(x0 + 30, x1 - 20, 28):
        dr.line((x, py, x + 14, py), fill=t["muted"], width=3)
    dr.ellipse((x0 - 30, py - 30, x0 + 30, py + 30), fill=t["bg"])
    dr.ellipse((x1 - 30, py - 30, x1 + 30, py + 30), fill=t["bg"])
    dial(im, x0 + 190, py + 130, 85, D["score"], "Today", ring=t["paper"])
    dr = ImageDraw.Draw(im)
    import random
    rnd = random.Random(D["when"])
    bx = x0 + 420
    while bx < x1 - 70:
        wdt = rnd.choice((3, 5, 8))
        dr.rectangle((bx, py + 50, bx + wdt, py + 160), fill=NAVY)
        bx += wdt + rnd.choice((4, 6, 9))
    dr.text(((x0 + 420 + x1 - 70) / 2, py + 192), SITE, font=F(26, "Bold"), fill=NAVY, anchor="mm")
    dr.text((540, 1040), FOOT, font=F(22, "SemiBold"), fill=t["foot"], anchor="mm")
    return im


def L_paper(D, t):
    on, wr, wn = _stats(D)
    im = Image.new("RGBA", (1080, 1080), t["bg"])
    dr = ImageDraw.Draw(im)
    dr.text((540, 85), "The Beaches Bus Watch", font=FS(70), fill=NAVY, anchor="mm")
    dr.line((60, 140, 1020, 140), fill=NAVY, width=4)
    dr.line((60, 148, 1020, 148), fill=NAVY, width=1)
    dr.text((60, 170), D["when"].upper(), font=F(22, "Bold"), fill=GREY)
    dr.text((1020, 170), f"LATE EDITION · {D['at'].upper()}", font=F(22, "Bold"), fill=GREY, anchor="ra")
    dr.line((60, 205, 1020, 205), fill=NAVY, width=1)
    dr.text((60, 235), "BUSES", font=F(26, "ExtraBold"), fill=t["acc"])
    noun = "bus trip" if D["cxl"] == 1 else "bus trips"
    head = f"{D['cxl']:,} {noun} cancelled"
    dr.text((60, 275), head, font=fitf(dr, head, FS, 104, 960), fill=NAVY)
    sub = f"Service rated {D['rating'].lower()} across the Northern Beaches today"
    dr.text((60, 410), sub, font=fitf(dr, sub, lambda z: FS(z, False), 40, 960), fill=GREY)
    dr.line((60, 480, 1020, 480), fill=NAVY, width=2)
    # left column: by the numbers
    dr.text((60, 505), "BY THE NUMBERS", font=F(24, "ExtraBold"), fill=t["acc"])
    items = [(D["cxl_pct"], "of scheduled trips cancelled"), (on, "of tracked buses on time"),
             (D["am"][0], "cancelled in the morning peak"), (D["pm"][0], "cancelled in the evening peak"),
             (f"{D['wk_cxl']:,}", f"cancelled over the last 7 days ({D['wk_pct']})")]
    y = 550
    for big, txt in items:
        dr.text((60, y), big, font=FS(46), fill=NAVY)
        dr.text((250, y + 14), txt, font=fit(dr, txt, "Medium", 26, 330), fill=GREY)
        y += 74
    dr.line((610, 500, 610, 910), fill=mix(t["bg"], "#000000", .25), width=1)
    # right column
    dr.text((640, 505), "TODAY'S RATING", font=F(24, "ExtraBold"), fill=t["acc"])
    dial(im, 830, 680, 120, D["score"], "Today", ring=t["bg"])
    dr = ImageDraw.Draw(im)
    dr.text((640, 790), "WORST ROUTE", font=F(24, "ExtraBold"), fill=t["acc"])
    dr.text((640, 825), wr, font=FS(64), fill=NAVY)
    dr.text((640 + dr.textlength(wr, font=FS(64)) + 20, 852), wn, font=F(26, "Medium"), fill=GREY)
    dr.line((60, 930, 1020, 930), fill=NAVY, width=2)
    footer(dr, 955, GREY, NAVY)
    return im


def L_poster(D, t):
    on, wr, wn = _stats(D)
    im = Image.new("RGBA", (1080, 1080), t["bg"])
    dr = ImageDraw.Draw(im)
    dr.ellipse((700, -200, 1300, 400), fill=mix(t["bg"], "#ffffff", .3))
    dr.ellipse((-250, 700, 350, 1300), fill=mix(t["bg"], t["ink"], .08))
    dr.text((70, 70), "TODAY ON THE", font=F(40, "Black"), fill=t["ink"])
    dr.text((70, 118), "NORTHERN BEACHES", font=F(40, "Black"), fill=t["ink"])
    dr.text((70, 175), f"{D['when']} · as at {D['at']}", font=F(26, "SemiBold"), fill=t["ink"])
    card(im, (800, 50, 1020, 215), 28, alpha=40)
    dial(im, 910, 140, 70, D["score"], "Today")
    dr = ImageDraw.Draw(im)
    num = f"{D['cxl']:,}"
    dr.text((60, 640), num, font=fitf(dr, num, FA, 470, 960), fill=t["ink"], anchor="ls")
    dr.text((70, 650), "BUS TRIPS CANCELLED", font=FA(92), fill=t["hot"])
    pills = [f"{D['cxl_pct']} of trips", f"{on} on time", f"Worst: {wr}"]
    x = 70
    for ptxt in pills:
        w_ = dr.textlength(ptxt, font=F(32, "ExtraBold")) + 60
        dr.rounded_rectangle((x, 800, x + w_, 870), 35, fill=t["ink"])
        dr.text((x + w_ / 2, 835), ptxt, font=F(32, "ExtraBold"), fill=t["bg"], anchor="mm")
        x += w_ + 20
    wk = f"Last 7 days: {D['wk_cxl']:,} cancelled ({D['wk_pct']})"
    dr.text((70, 900), wk, font=F(30, "Bold"), fill=t["ink"])
    footer(dr, 965, mix(t["ink"], t["bg"], .3), t["ink"])
    return im


def L_donut(D, t):
    on, wr, wn = _stats(D)
    im = Image.new("RGBA", (1080, 1080), t["bg"])
    dr = ImageDraw.Draw(im)
    dr.text((70, 65), "Where did today's", font=F(50, "ExtraBold"), fill=NAVY)
    dr.text((70, 125), "buses go?", font=F(50, "ExtraBold"), fill=t["acc"])
    dr.text((70, 200), f"Northern Beaches · {D['when']} · as at {D['at']}", font=F(26, "SemiBold"), fill=GREY)
    card(im, (810, 50, 1020, 210), 28, alpha=40)
    dial(im, 915, 135, 66, D["score"], "Today")
    dr = card(im, (60, 260, 1020, 700), 34)
    cx, cy, r = 280, 480, 160
    ran = 1 - D["cxl_f"]
    dr.pieslice((cx - r, cy - r, cx + r, cy + r), -90, -90 + 360 * ran, fill=t["acc"])
    dr.pieslice((cx - r, cy - r, cx + r, cy + r), -90 + 360 * ran, 270, fill=t["hot"])
    dr.ellipse((cx - 100, cy - 100, cx + 100, cy + 100), fill="white")
    dr.text((cx, cy - 18), D["cxl_pct"], font=F(52, "Black"), fill=t["hot"], anchor="mm")
    dr.text((cx, cy + 30), "cancelled", font=F(26, "SemiBold"), fill=GREY, anchor="mm")
    lx = 510
    y = 320
    for col, big, lab in ((t["acc"], f"{D['sched'] - D['cxl']:,}", "trips ran"), (t["hot"], f"{D['cxl']:,}", "trips cancelled"),
                          ("#2e9e3e", on, "buses on time")):
        dr.rounded_rectangle((lx, y + 14, lx + 34, y + 48), 9, fill=col)
        dr.text((lx + 56, y), big, font=F(56, "Black"), fill=NAVY)
        dr.text((lx + 60, y + 70), lab, font=F(26, "SemiBold"), fill=GREY)
        y += 120
    dr = card(im, (60, 730, 1020, 935), 34)
    dr.text((100, 760), "Cancelled by time of day", font=F(30, "ExtraBold"), fill=NAVY)
    for i, (lab, (pv, n_)) in enumerate((("Morning peak", D["am"]), ("Evening peak", D["pm"]))):
        y = 815 + i * 56
        frac = float(pv.rstrip("%")) / 10 if pv not in ("–", "") else 0
        dr.text((100, y), lab, font=F(26, "SemiBold"), fill=GREY)
        bar(dr, 330, y + 4, 520, 26, frac, t["hot"], mix(t["hot"], "#ffffff", .85))
        dr.text((990, y), pv, font=F(28, "ExtraBold"), fill=NAVY, anchor="ra")
    footer(dr, 960, "#7c8a9a", NAVY)
    return im


def L_route(D, t):
    on, wr, wn = _stats(D)
    im = Image.new("RGBA", (1080, 1080), t["bg"])
    dr = ImageDraw.Draw(im)
    dr.rectangle((0, 0, 1080, 210), fill=t["line"])
    bus(dr, 70, 60, 46, "white", t["line"], "white", None)
    dr.text((200, 58), "Today's bus run", font=F(54, "Black"), fill="white")
    sub = f"{D['when']} · as at {D['at']}"
    dr.text((200, 128), sub, font=fit(dr, sub, "SemiBold", 28, 570), fill="white")
    stops = [("Morning peak", f"{D['am'][0]} cancelled", f"{D['am'][1]} trips cancelled"),
             ("Evening peak", f"{D['pm'][0]} cancelled", f"{D['pm'][1]} trips cancelled"),
             ("All day", f"{D['cxl']:,} cancelled", f"{D['cxl_pct']} of {D['sched']:,} · {on} on time"),
             ("Worst route", wr, wn),
             ("Last 7 days", f"{D['wk_cxl']:,} cancelled", D["wk_pct"] + (f" · {D['wk_on']} on time" if D["wk_on"] else ""))]
    lx = 130
    dr.line((lx, 230, lx, 940), fill=t["line"], width=18)
    y = 250
    for i, (lab, big, small) in enumerate(stops):
        dr.ellipse((lx - 26, y + 40, lx + 26, y + 92), fill="white", outline=t["line"], width=10)
        dr = card(im, (200, y, 1020, y + 128), 24, alpha=35)
        dr.text((235, y + 22), lab.upper(), font=F(24, "ExtraBold"), fill=t["line"])
        dr.text((235, y + 56), big, font=fit(dr, big, "Black", 48, 420), fill=NAVY)
        dr.text((990, y + 76), small, font=fit(dr, small, "SemiBold", 26, 330), fill=GREY, anchor="ra")
        y += 140
    card(im, (800, 20, 1040, 190), 28, alpha=30)
    dial(im, 920, 108, 74, D["score"], "Today")
    dr = ImageDraw.Draw(im)
    footer(dr, 962, "#7c8a9a", NAVY)
    return im


def L_bento(D, t):
    on, wr, wn = _stats(D)
    im = Image.new("RGBA", (1080, 1080), t["bg"])
    dr = ImageDraw.Draw(im)
    dr.text((60, 55), "Northern Beaches buses", font=F(50, "Black"), fill=NAVY)
    dr.text((60, 122), f"{D['when']} · as at {D['at']}", font=F(28, "SemiBold"), fill=GREY)
    c = t["tiles"]
    g = 20
    # big tile: cancelled
    dr.rounded_rectangle((60, 190, 600, 560), 36, fill=c[0])
    dr.text((95, 225), "Cancelled today", font=F(32, "Bold"), fill=NAVY)
    num = f"{D['cxl']:,}"
    dr.text((90, 270), num, font=fit(dr, num, "Black", 190, 480), fill=NAVY)
    dr.text((95, 500), f"{D['cxl_pct']} of {D['sched']:,} trips", font=F(30, "SemiBold"), fill=NAVY)
    # dial tile
    dr.rounded_rectangle((620, 190, 1020, 560), 36, fill=c[1])
    dial(im, 820, 400, 140, D["score"], "Today's service", ring=c[1])
    dr = ImageDraw.Draw(im)
    tiles = [(60, 580, 380, 820, c[2], "On time", on), (400, 580, 720, 820, c[3], "Worst route", wr),
             (740, 580, 1020, 820, c[4], "AM peak", D["am"][0])]
    for x0, y0, x1, y1, col, lab, big in tiles:
        dr.rounded_rectangle((x0, y0, x1, y1), 36, fill=col)
        dr.text((x0 + 32, y0 + 30), lab, font=F(30, "Bold"), fill=NAVY)
        dr.text((x0 + 28, y0 + 85), big, font=fit(dr, big, "Black", 96, x1 - x0 - 60), fill=NAVY)
    dr.rounded_rectangle((60, 840, 1020, 930), 36, fill=c[5])
    wk = f"Last 7 days: {D['wk_cxl']:,} cancelled ({D['wk_pct']})" + (f" · {D['wk_on']} on time" if D["wk_on"] else "")
    dr.text((540, 885), wk, font=fit(dr, wk, "ExtraBold", 32, 900), fill=NAVY, anchor="mm")
    footer(dr, 955, "#6b7a8c", NAVY)
    return im


# ---------------------------------------------------------------- themes
BEACH = [
    dict(name="Ocean", h1="#0e7c86", h2="#36b5c1", bg="#fff8ee", hero="#ef6f5e", ring="#2fb5a3", label="#0e7c86", pill="#0b5f67", a1="#f4a340", a2="#6c7ae0"),
    dict(name="Jacaranda", h1="#6f4fc0", h2="#b39ae8", bg="#f8f5fd", hero="#e0567a", ring="#7b5cc7", label="#6a4bb5", pill="#55399b", a1="#f4a340", a2="#2fb5a3"),
]
STATUS = [dict(name="Bold", tex="stripes", bg="#f4f6f9"), dict(name="Soft", tex="rings", bg="#ffffff", soft=True)]
SKY = [
    dict(name="Midday", s1="#78c4ff", s2="#f0f8ff", sun="#ffd54f", sea="#3aa0d8", sand="#f3dfb4", bus="#1d4f91", t2="white"),
    dict(name="Golden hour", s1="#ff9a6b", s2="#fff0e4", sun="#fff1a8", sea="#4b7fc0", sand="#f0cf9e", bus="#1d4f91", t2="white", sun_at=(980, 330)),
]
DIAL = [
    dict(name="Mint", bg="#eefaf4", blob1="#bdf0d6", blob2="#cdebff", acc="#1f8a5b"),
    dict(name="Peach", bg="#fff4ec", blob1="#ffd2b8", blob2="#ffe7a8", acc="#d4572b"),
]
TICKET = [
    dict(name="Opal blue", bg="#cfe4ff", paper="#fffdf7", acc="#1f5fbf", muted="#7c8a9a", foot="#2a4a7a"),
    dict(name="Retro orange", bg="#ffd9b8", paper="#fffaf2", acc="#e0621f", muted="#8a7a6a", foot="#7a3a12"),
]
PAPER = [dict(name="Newsprint", bg="#f6f1e7", acc="#c0392b"), dict(name="Salmon", bg="#fdeee0", acc="#1f5fbf")]
POSTER = [dict(name="Sunshine", bg="#ffd23f", ink="#0f2747", hot="#d6455d"),
          dict(name="Aqua", bg="#7fe3d6", ink="#0f2747", hot="#c43e6b")]
DONUT = [dict(name="Lilac", bg="#f3effd", acc="#6f4fc0", hot="#ef6f5e"), dict(name="Seafoam", bg="#ecf8f5", acc="#1a9a8a", hot="#f08a3a")]
ROUTE = [dict(name="Teal line", bg="#f2f8f8", line="#0e8f8a", acc="#0e8f8a"), dict(name="Orange line", bg="#fff7f0", line="#ea6a1f", acc="#ea6a1f")]
BENTO = [
    dict(name="Pastels", bg="#fbfaf7", acc="#6c7ae0", tiles=["#ffd6cc", "#ffffff", "#cdeccf", "#d8d4fb", "#ffeaa6", "#cfe8ff"]),
    dict(name="Beach hues", bg="#f4fbfb", acc="#0e7c86", tiles=["#bfeee9", "#ffffff", "#ffe0b8", "#ffd0dc", "#d6ecff", "#f9f1c8"]),
]
LAYOUTS = [("Beach", L_beach, BEACH), ("Ticket", L_ticket, TICKET), ("Sky", L_sky, SKY), ("Poster", L_poster, POSTER),
           ("Status", L_status, STATUS), ("Donut", L_donut, DONUT), ("Newspaper", L_paper, PAPER), ("Bento", L_bento, BENTO),
           ("Big dial", L_bigdial, DIAL), ("Route", L_route, ROUTE)]
# 10 layouts x 2 colourways, interleaved so the layout changes every day and each repeats only every 10 days
POOL = [(f"{ln} · {th['name']}", fn, th) for i in range(2) for ln, fn, themes in LAYOUTS for th in [themes[i]]]


def pick(date):
    return POOL[date.toordinal() % len(POOL)]


def draw_daily(D, path, date=None, index=None):
    name, fn, theme = POOL[index] if index is not None else pick(date)
    fn(D, theme).convert("RGB").save(path, "PNG", optimize=True)
    return name


# ---------------------------------------------------------------- weekly (Monday) layout, uses the day's colours
def colours_for(date):
    _, fn, t = pick(date)
    if fn is L_beach:
        return t["h1"], t["h2"], t["bg"], t["hero"]
    if fn is L_sky:
        return mix(t["s1"], t["bus"], .35), t["s1"], "#f5f8fc", "#e8604c"
    if fn is L_poster:
        return mix(t["bg"], t["ink"], .25), t["bg"], "#fbfaf6", t["hot"]
    if "line" in t:
        return t["line"], mix(t["line"], "#ffffff", .4), t["bg"], t["line"]
    if "acc" in t:
        return t["acc"], mix(t["acc"], "#ffffff", .45), t["bg"] if fn is not L_ticket else "#f5f8fc", t.get("hot", "#e8604c")
    return "#1f5fbf", "#4f9be8", t["bg"], "#e8604c"


def draw_weekly(W, path, date):
    h1, h2, bg, hot = colours_for(date)
    h1, h2 = (rgb(h1) if isinstance(h1, str) else h1), (rgb(h2) if isinstance(h2, str) else h2)
    im = Image.new("RGBA", (1080, 1080), bg)
    vgrad(im, (0, 0, 1080, 330), h1, h2)
    dr = ImageDraw.Draw(im)
    dr.polygon([(x, 300 + 22 * math.sin(x / 1080 * 3 * math.pi)) for x in range(0, 1081, 10)] + [(1080, 1080), (0, 1080)], fill=bg)
    dr.text((70, 62), "Week in review", font=F(58, "ExtraBold"), fill="white")
    dr.text((70, 140), "Keolis Northern Beaches buses", font=F(32, "SemiBold"), fill="white")
    dr.text((70, 184), W["when"], font=F(28, "Medium"), fill="white")
    card(im, (790, 50, 1030, 240), 30, alpha=45)
    dial(im, 910, 158, 82, W["score"], "Last week")
    t = W["total"]
    dr = card(im, (60, 330, 1020, 560))
    dr.text((100, 360), "CANCELLED LAST WEEK", font=F(26, "Bold"), fill=GREY)
    dr.text((95, 392), f"{t['cxl']:,}", font=fit(dr, f"{t['cxl']:,}", "Black", 120, 520), fill=hot)
    dr.text((100, 515), f"{W['pct']} of {t['sched']:,} trips", font=F(30, "SemiBold"), fill=GREY)
    if W["on"]:
        dr.text((980, 400), W["on"], font=F(80, "Black"), fill=NAVY, anchor="ra")
        dr.text((980, 500), "on time", font=F(30, "SemiBold"), fill=GREY, anchor="ra")
    y = 588
    line = " · ".join(x for x in (W["bad_day"], W["change"]) if x)
    if line:
        dr.text((70, y), line, font=fit(dr, line, "SemiBold", 32, 940), fill=NAVY)
        y += 52
    if W["worst"]:
        dr.text((70, y), "Most cancelled routes", font=F(30, "ExtraBold"), fill=GREY)
        y += 46
        top = max(c for _, c, _ in W["worst"])
        for rt, c, sc in W["worst"]:
            dr = card(im, (60, y, 1020, y + 76), 22, alpha=35)
            dr.text((95, y + 38), rt, font=fit(dr, rt, "Black", 42, 170), fill=NAVY, anchor="lm")
            bar(dr, 290, y + 26, 450, 24, c / top, hot, mix(hot, "#ffffff", .85))
            dr.text((990, y + 38), f"{c} ({100 * c / sc:.1f}%)", font=F(30, "ExtraBold"), fill=NAVY, anchor="rm")
            y += 88
    footer(dr, 975, "#7c8a9a")
    im.convert("RGB").save(path, "PNG", optimize=True)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, HERE)
    import post_social as ps
    out = os.path.join(ps.OUT_DIR, "styles")
    os.makedirs(out, exist_ok=True)
    D = ps.card_data(*sys.argv[1:2])
    for i, (name, _, _) in enumerate(POOL):
        draw_daily(D, os.path.join(out, f"{i + 1:02d}.png"), index=i)
        print(i + 1, name)
