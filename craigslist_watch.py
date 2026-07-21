#!/usr/bin/env python3
"""
Milpitas small-building rental watcher (Craigslist).

Watches Craigslist South Bay rentals for units his father-in-law wants:
ADU / in-law, duplex, triplex, fourplex, cottage, or a house-type unit --
NOT apartment-complex listings. 1-2 bedrooms, rent at or under the max.

Craigslist blocks plain scraping, so this uses the same JSON API Craigslist's
own website calls (sapi.craigslist.org). Two signals are merged:
  1. Structured housing_type filter (landlord-selected: duplex, in-law,
     cottage, house, flat) -> reliably excludes apartment buildings.
  2. A keyword catch for anything titled "triplex / fourplex / X-plex", since
     Craigslist has no structured type for those and owners often file them
     under "apartment".

Sends a Telegram message ONLY when a new matching post appears. State lives in
craigslist_state.txt so nothing pings twice.

Env: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID (required to send); TEST_MODE=1 self-test.
"""

import os
import re
import json
import urllib.request
import urllib.parse

MAX_RENT = 3200
STATE_FILE = os.path.join(os.path.dirname(__file__), "craigslist_state.txt")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# Craigslist housing_type codes matching his ask, structurally excluding
# apartment complexes. 4=duplex, 7=in-law (= ADU). Triplex/fourplex have no
# structured code, so they're caught by the keyword pass below.
GOOD_TYPES = [4, 7]

# Keyword catch for the unit types he wants, in case a landlord mis-tagged the
# structured type (common on Craigslist).
UNIT_RE = re.compile(
    r"\b(adu|jadu|in.?law|granny|casita|duplex|tri.?plex|3.?plex|"
    r"four.?plex|4.?plex|multiplex|[0-9]+.?plex|back.?house|guest.?house)\b", re.I)

# Reject housing-WANTED ads and room-shares -- he wants a whole unit.
REJECT_RE = re.compile(
    r"(looking for|wanted|in search of|\biso\b|private room|room for rent|"
    r"room available|furnished room|shared (house|room)|roommate|share house)", re.I)
BASE = ("https://sapi.craigslist.org/web/v8/postings/search/full"
        "?batch=1-0-360-0-0&cc=US&lang=en&searchPath=apa"
        f"&max_price={MAX_RENT}&min_bedrooms=1&max_bedrooms=2&query=milpitas")

# Human-clickable search of all Milpitas rentals in range, newest first (opens
# fine in a browser; only servers get blocked). The watcher does the type
# filtering; this link is the raw feed to eyeball.
SEARCH_URL = ("https://sfbay.craigslist.org/search/apa?query=milpitas"
              f"&max_price={MAX_RENT}&min_bedrooms=1&max_bedrooms=2&sort=date")

TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def fetch_json(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "application/json",
        "Referer": "https://sfbay.craigslist.org/"})
    raw = urllib.request.urlopen(req, timeout=40).read()
    if raw[:15].lstrip().lower().startswith(b"<!doctype") or b"blocked" in raw[:64].lower():
        raise RuntimeError("craigslist blocked the request")
    return json.loads(raw)


def send_telegram(text):
    if not TOKEN or not CHAT_ID:
        print("no telegram creds; would have sent:\n" + text)
        return
    data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text}).encode()
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=30).read()


def parse_item(it, loc_desc):
    """Pull id, price, title, sqft, location, and the raw attribute text."""
    pid = it[0]
    price = it[3] if isinstance(it[3], int) else None
    slug = None
    sqft = None
    attrs = ""
    for f in it:
        if isinstance(f, list) and f and f[0] == 6:
            slug = f[1]
        elif isinstance(f, list) and f and f[0] == 5 and len(f) >= 3:
            sqft = f[2]
        elif isinstance(f, str):
            attrs = f
    title = (slug or "").replace("-", " ").strip().title() or "(untitled)"
    loc = ""
    try:
        idx = int(str(it[4]).split(":")[0])
        loc = loc_desc[idx] if 0 < idx < len(loc_desc) else ""
    except (ValueError, IndexError, TypeError):
        pass
    return {"id": str(pid), "price": price, "title": title,
            "sqft": sqft, "loc": loc, "attrs": attrs}


def collect():
    """Return {id: record} of current matches from both signals."""
    out = {}
    # 1) Structured types he asked for (duplex, in-law/ADU).
    url = BASE + "".join(f"&housing_type={t}" for t in GOOD_TYPES)
    d = fetch_json(url)["data"]
    loc_desc = d.get("decode", {}).get("locationDescriptions", [])
    for it in d.get("items", []):
        r = parse_item(it, loc_desc)
        hay = r["title"] + " " + r["attrs"]
        if r["price"] is not None and not REJECT_RE.search(hay):
            r["why"] = "duplex/ADU (tagged)"
            out[r["id"]] = r
    # 2) Keyword catch for ADU/duplex/triplex/fourplex among ALL milpitas results
    #    (catches units the landlord mis-tagged, e.g. a fourplex filed as apartment).
    d2 = fetch_json(BASE)["data"]
    loc2 = d2.get("decode", {}).get("locationDescriptions", [])
    base_count = d2.get("totalResultCount", 0)
    for it in d2.get("items", []):
        r = parse_item(it, loc2)
        if r["price"] is None:
            continue
        hay = r["title"] + " " + r["attrs"]
        if UNIT_RE.search(hay) and not REJECT_RE.search(hay):
            r["why"] = "titled ADU/duplex/plex"
            out.setdefault(r["id"], r)
    return out, base_count


def load_state():
    try:
        with open(STATE_FILE) as f:
            return set(l.strip() for l in f if l.strip())
    except FileNotFoundError:
        return set()


def save_state(ids):
    with open(STATE_FILE, "w") as f:
        f.write("\n".join(sorted(ids)) + "\n")


def fmt(r):
    bits = [f"${r['price']:,}"]
    if r["sqft"]:
        bits.append(f"{r['sqft']} sqft")
    if r["loc"]:
        bits.append(r["loc"])
    return f"• {r['title']} ({r['why']})\n  {' · '.join(bits)}"


def main():
    test = os.environ.get("TEST_MODE", "0") == "1"
    try:
        matches, base_count = collect()
    except Exception as e:
        print(f"fetch failed: {e}")
        if test:
            send_telegram(f"Craigslist Milpitas watch self-test: FETCH FAILED ({e}).")
        else:
            st = load_state()
            if "__BROKEN__" not in st:
                send_telegram(
                    "Heads up: the Craigslist Milpitas watcher couldn't reach the "
                    "listings (site may have changed or blocked it). Krystle should "
                    "check. (You won't get this again until it recovers.)")
                st.add("__BROKEN__")
                save_state(st)
        return

    good = sorted(matches.values(), key=lambda r: r["price"] or 0)
    print(f"base_milpitas_results={base_count} type/plex matches={len(good)}")

    if test:
        lines = "\n".join(fmt(r) for r in good) or "(no small-building matches right now)"
        send_telegram(
            "Craigslist Milpitas watch self-test (ADU / duplex / triplex / fourplex, "
            f"whole units, 1-2 bed, ${MAX_RENT} or less -- no apartment complexes, "
            f"no rooms):\n\n{lines}\n\nEyeball all Milpitas rentals: {SEARCH_URL}\n\n"
            "You'll get a ping like this whenever a NEW one appears. (Ignore this test.)")
        return

    # Guardrail: the broad Milpitas search normally has dozens of results.
    if base_count == 0:
        st = load_state()
        if "__BROKEN__" not in st:
            send_telegram(
                "Heads up: the Craigslist Milpitas watcher ran but saw 0 total "
                "Milpitas rentals, which usually means it got blocked. Krystle "
                "should check. (You won't get this again until it recovers.)")
            st.add("__BROKEN__")
            save_state(st)
        return

    prev = load_state()
    prev.discard("__BROKEN__")
    cur = set(matches.keys())
    new = [r for r in good if r["id"] not in prev]

    if new:
        body = "\n".join(fmt(r) for r in new)
        send_telegram(
            f"New Craigslist Milpitas match{'es' if len(new) > 1 else ''} "
            f"(ADU/duplex/plex/house, 1-2 bed, ${MAX_RENT} or less):\n\n{body}\n\n"
            f"Open the live search (newest first): {SEARCH_URL}")
        print(f"alerted on {len(new)} new")

    save_state(cur)


if __name__ == "__main__":
    main()
