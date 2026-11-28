"""Telegram cancellation alerts for Northern Beaches buses.

  python tools/telegram_bot.py replies   -> answer new messages (run every ~30 s)
  python tools/telegram_bot.py alerts    -> message subscribers about newly cancelled buses (after each data update)

Needs the GitHub secret TELEGRAM_BOT_TOKEN. Does nothing if it isn't set.

Subscribers' chat IDs are private, so they are stored encrypted (with a key derived from
the bot token) in data/private/telegram.enc. Only the bot's public username is published.
"""
import base64
import datetime as dt
import hashlib
import json
import os
import re
import sys
from zoneinfo import ZoneInfo

import requests
from cryptography.fernet import Fernet, InvalidToken

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")
STORE = os.path.join(DATA, "private", "telegram.enc")
PUBLIC = os.path.join(DATA, "telegram.json")
SITE = "https://nbbuswatch.github.io/"
SYD = ZoneInfo("Australia/Sydney")
MAX_SUBS = 10
DEFAULT_WINDOW = 15  # minutes either side of the time given

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
API = f"https://api.telegram.org/bot{TOKEN}"

HELP = (
    "🚌 Northern Beaches bus alerts\n\n"
    "I'll message you when your usual bus is cancelled, often hours before it's due.\n\n"
    "To add a bus, send the route and time, and optionally where it's heading:\n"
    "  199 7:30\n"
    "  B1 17:15 Mona Vale\n"
    "  E65 8:05 City\n\n"
    "Times are when the bus leaves the start of its route (as shown on the tracker's My bus tab). "
    "I'll watch buses within 15 minutes of that time.\n\n"
    "/list — your alerts\n/remove 2 — remove alert number 2\n/stop — remove all alerts\n\n"
    f"Tracker: {SITE}"
)


# ---------------------------------------------------------------- storage
def fernet():
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("nb-bus-store:" + TOKEN).encode()).digest()))


def load_store():
    try:
        with open(STORE, "rb") as f:
            return json.loads(fernet().decrypt(f.read()))
    except (FileNotFoundError, InvalidToken, json.JSONDecodeError):
        return {"offset": 0, "subs": {}, "sent": {}}


def save_store(store):
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    with open(STORE, "wb") as f:
        f.write(fernet().encrypt(json.dumps(store, separators=(",", ":")).encode()))


def tg(method, **params):
    r = requests.post(f"{API}/{method}", json=params, timeout=40)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"{method}: {data.get('description', r.status_code)}")
    return data["result"]


def send(chat_id, text):
    try:
        tg("sendMessage", chat_id=chat_id, text=text, disable_web_page_preview=True)
        return True
    except RuntimeError as exc:
        print("send failed:", exc)
        return "blocked" not in str(exc) and "chat not found" not in str(exc)


# ---------------------------------------------------------------- timetable helpers
def timetable():
    try:
        with open(os.path.join(DATA, "timetable_today.json")) as f:
            return json.load(f).get("routes", {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def hm(t):
    try:
        h, m = t.split(":")[:2]
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def fmt(m):
    return f"{(m // 60) % 24:02d}:{m % 60:02d}"


def find_route(name, tt):
    for r in tt:
        if r.lower() == name.lower():
            return r
    return None


def describe(sub):
    when = f"{sub['time']}" + (f" (±{sub['window']} min)" if sub["window"] else "")
    return f"{sub['route']} at {when}" + (f" towards {sub['dir']}" if sub["dir"] else " (either direction)")


# ---------------------------------------------------------------- replies
def parse_add(text, tt):
    m = re.match(r"^\s*([A-Za-z]*\d+[A-Za-z]?)\s+(\d{1,2})[:.]?(\d{2})\s*([ap]m)?\s*(.*)$", text, re.I)
    if not m:
        return None, None
    route = find_route(m.group(1), tt) or m.group(1).upper()
    h, mi = int(m.group(2)), int(m.group(3))
    if m.group(4):
        h = h % 12 + (12 if m.group(4).lower() == "pm" else 0)
    if h > 27 or mi > 59:
        return None, "That time doesn't look right. Try something like: 199 7:30"
    if route not in tt:
        return None, f"I can't find route {route} in today's Northern Beaches timetable. Check the route on the tracker's My bus tab."
    dir_text = m.group(5).strip()
    heads = sorted({t[2] for t in tt[route]})
    direction = ""
    if dir_text:
        match = [hs for hs in heads if dir_text.lower() in hs.lower()]
        if not match:
            return None, f"Route {route} goes towards: {', '.join(heads)}. Send it again with one of those, e.g. {route} {h}:{mi:02d} {heads[0]}"
        direction = match[0]
    return {"route": route, "time": f"{h:02d}:{mi:02d}", "dir": direction, "window": DEFAULT_WINDOW}, None


def parse_start_payload(payload, tt):
    """Deep link from the tracker: <route>_<HHMM>_<base64url headsign>, exact trip."""
    try:
        route, hhmm, enc = payload.split("_", 2)
        hs = base64.urlsafe_b64decode(enc + "=" * (-len(enc) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    route = find_route(route, tt)
    if not route or not re.fullmatch(r"\d{4}", hhmm):
        return None
    return {"route": route, "time": f"{hhmm[:2]}:{hhmm[2:]}", "dir": hs, "window": 0}


def add_sub(store, chat, sub):
    subs = store["subs"].setdefault(str(chat), [])
    if any(s["route"] == sub["route"] and s["time"] == sub["time"] and s["dir"] == sub["dir"] for s in subs):
        return send(chat, f"You already have an alert for {describe(sub)}.")
    if len(subs) >= MAX_SUBS:
        return send(chat, f"You can have up to {MAX_SUBS} alerts. Remove one first with /remove.")
    subs.append(sub)
    send(chat, f"✅ Alert added: {describe(sub)}.\n\nI'll message you if it's cancelled. Send /list to see your alerts.")


def handle(store, msg, tt):
    chat = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()
    key = str(chat)
    if text.startswith("/start"):
        payload = text[6:].strip()
        sub = parse_start_payload(payload, tt) if payload else None
        if sub:
            send(chat, HELP)
            return add_sub(store, chat, sub)
        return send(chat, HELP)
    if text.startswith("/help"):
        return send(chat, HELP)
    if text.startswith("/list"):
        subs = store["subs"].get(key, [])
        if not subs:
            return send(chat, "You have no alerts yet. Send a route and time, e.g. 199 7:30")
        return send(chat, "Your alerts:\n" + "\n".join(f"{i + 1}. {describe(s)}" for i, s in enumerate(subs)) + "\n\nRemove one with /remove and its number.")
    if text.startswith("/remove"):
        subs = store["subs"].get(key, [])
        m = re.search(r"\d+", text)
        if not m or not (1 <= int(m.group()) <= len(subs)):
            return send(chat, "Send /remove and the number from /list, e.g. /remove 1")
        gone = subs.pop(int(m.group()) - 1)
        if not subs:
            store["subs"].pop(key, None)
        return send(chat, f"Removed: {describe(gone)}.")
    if text.startswith("/stop"):
        store["subs"].pop(key, None)
        return send(chat, "All your alerts are removed. Send a route and time any time to start again.")
    sub, err = parse_add(text, tt)
    if sub:
        return add_sub(store, chat, sub)
    return send(chat, err or "Sorry, I didn't understand that. Send a route and time, e.g. 199 7:30, or /help.")


def replies():
    store = load_store()
    try:
        updates = tg("getUpdates", offset=store.get("offset", 0), timeout=0, allowed_updates=["message"])
    except Exception as exc:
        print("getUpdates failed:", exc)
        return
    if not updates:
        return
    tt = timetable()
    for u in updates:
        store["offset"] = u["update_id"] + 1
        msg = u.get("message")
        if msg and msg.get("chat", {}).get("type") == "private":
            try:
                handle(store, msg, tt)
            except Exception as exc:
                print("handle failed:", exc)
    save_store(store)


# ---------------------------------------------------------------- alerts
def alerts():
    store = load_store()
    try:
        with open(os.path.join(DATA, "live.json")) as f:
            live = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return
    tt = timetable()
    now = dt.datetime.now(SYD)
    now_m = now.hour * 60 + now.minute + (1440 if now.hour < 4 else 0)
    day = live.get("date")
    cancelled = [t for t in live.get("trips", []) if t.get("trip_id")]
    cset = {t["trip_id"] for t in cancelled}
    sent = store.setdefault("sent", {})
    changed = False
    for chat, subs in list(store["subs"].items()):
        for sub in subs:
            if chat not in store["subs"]:   # blocked the bot part-way through: stop messaging them
                break
            centre = hm(sub["time"])
            for t in cancelled:
                if t["route"] != sub["route"] or (sub["dir"] and t["headsign"] != sub["dir"]):
                    continue
                m = hm(t["start_time"])
                if m is None or abs(m - centre) > sub["window"] or m < now_m - 5:
                    continue
                key = f"{chat}|{day}|{t['trip_id']}"
                if key in sent:
                    continue
                later = [hm(x[1]) for x in tt.get(t["route"], []) if x[2] == t["headsign"] and hm(x[1]) > m and x[0] not in cset]
                nxt = f"Next bus: {fmt(min(later))} ({min(later) - m} min later)." if later else "No later bus in this direction today."
                first = next((x[3] for x in tt.get(t["route"], []) if x[0] == t["trip_id"]), "")
                text = (f"⚠️ Cancelled: route {t['route']} to {t['headsign']}, {fmt(m)}"
                        + (f" from {first}" if first else "") + f".\n{nxt}\n\nLive status: {SITE}#mybus")
                ok = send(int(chat), text)
                sent[key] = now.isoformat(timespec="minutes")
                changed = True
                if not ok:  # user blocked the bot: drop their alerts
                    store["subs"].pop(chat, None)
                    break
    cutoff = (now.date() - dt.timedelta(days=2)).isoformat()
    for k in [k for k in sent if k.split("|")[1] < cutoff]:
        del sent[k]
        changed = True
    if changed:
        save_store(store)


def publish_username():
    try:
        with open(PUBLIC) as f:
            if json.load(f).get("bot"):
                return
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    try:
        me = tg("getMe")
    except Exception as exc:
        print("getMe failed:", exc)
        return
    with open(PUBLIC, "w") as f:
        json.dump({"bot": me["username"]}, f)


def main():
    if not TOKEN:
        return
    mode = sys.argv[1] if len(sys.argv) > 1 else "replies"
    publish_username()
    if mode == "alerts":
        alerts()
    else:
        replies()


if __name__ == "__main__":
    main()
