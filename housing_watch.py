#!/usr/bin/env python3
"""
Milpitas Section 8 housing watcher.

Fetches AffordableHousing.com's Milpitas Section 8 listings, keeps the ones that
match the criteria (1 or 2 bedrooms, rent at or under the max), and sends a
Telegram message ONLY when a new matching listing appears. State is kept in
housing_state.txt so the same listing never pings twice.

Guardrail: if the fetch fails or the page returns zero cards (a likely sign the
site changed or blocked us, not a real "no listings"), it does NOT wipe state
and sends a one-time "watcher may be broken" heads-up instead of going silent.

Env:
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID  - required to send
  TEST_MODE=1                       - send a self-test ping and exit
"""

import os
import re
import urllib.request
import urllib.parse

URL = "https://www.affordablehousing.com/milpitas-ca/section8-owners/"
MAX_RENT = 3200
WANT_BEDS = {1, 2}
STATE_FILE = os.path.join(os.path.dirname(__file__), "housing_state.txt")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")

TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode("utf-8", "replace")


def send_telegram(text):
    if not TOKEN or not CHAT_ID:
        print("no telegram creds; would have sent:\n" + text)
        return
    data = urllib.parse.urlencode(
        {"chat_id": CHAT_ID, "text": text}).encode()
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=30).read()


# Listings come as two card types that both carry 'jq-cardhover':
#   free:    <div class='freecard jq-cardhover' ... data-communityid='NNN'>
#   premium: <div class='premiumcard jq-cardhover' ... data-communityid='NNN'>
# We split on that boundary and pull each field from its OWN container so a
# stray dollar amount elsewhere in the card can't pollute the rent.
CARD_SPLIT_RE = re.compile(r"(?=(?:free|premium)card jq-cardhover')")
ID_RE = re.compile(r"data-communityid='(\d+)'")
# Price sits immediately after the price container, optionally wrapped in <a>.
PRICE_RE = re.compile(r"card--price'>\s*(?:<a[^>]*>)?\s*\$([\d,]+)")
# Beds sit inside the details container: e.g. "1-2 <span ...>beds</span>".
BEDS_RE = re.compile(r"card--details'>.*?([\d]+(?:-[\d]+)?)\s*<span class=\"card--value\">bed", re.S)
ADDR_PROP_RE = re.compile(r"tnresult--propertyaddress'>([^<]+)<")
ADDR_ALT_RE = re.compile(r"alt='([^']+)'")
HREF_RE = re.compile(r"href='(https://www\.affordablehousing\.com/milpitas-ca/[^']+)'")


def parse_beds(text):
    """'1-2' -> {1,2}; '3' -> {3}. Returns the set of bedroom counts offered."""
    m = BEDS_RE.search(text)
    if not m:
        return set(), "?"
    tok = m.group(1)
    if "-" in tok:
        lo, hi = tok.split("-", 1)
        try:
            return set(range(int(lo), int(hi) + 1)), tok
        except ValueError:
            return set(), tok
    try:
        return {int(tok)}, tok
    except ValueError:
        return set(), tok


def parse_cards(html):
    out = []
    for body in CARD_SPLIT_RE.split(html):
        idm = ID_RE.search(body[:200])  # id is in the opening tag
        if not idm:
            continue
        addr_m = ADDR_PROP_RE.search(body) or ADDR_ALT_RE.search(body)
        href_m = HREF_RE.search(body)
        beds, beds_raw = parse_beds(body)
        pm = PRICE_RE.search(body)
        price = int(pm.group(1).replace(",", "")) if pm else None
        out.append({
            "id": idm.group(1),
            "address": addr_m.group(1).strip() if addr_m else "(address n/a)",
            "url": href_m.group(1) if href_m else URL,
            "beds": beds,
            "beds_raw": beds_raw,
            "min_price": price,
            "price_raw": f"${price:,}" if price is not None else "n/a",
        })
    return out


def matches(card):
    if not (card["beds"] & WANT_BEDS):
        return False
    if card["min_price"] is None:
        return False
    return card["min_price"] <= MAX_RENT


def load_state():
    try:
        with open(STATE_FILE) as f:
            return set(line.strip() for line in f if line.strip())
    except FileNotFoundError:
        return set()


def save_state(ids):
    with open(STATE_FILE, "w") as f:
        f.write("\n".join(sorted(ids)) + "\n")


def fmt(card):
    return f"• {card['address']} — {card['beds_raw']} bed, {card['price_raw']}\n  {card['url']}"


def main():
    test = os.environ.get("TEST_MODE", "0") == "1"

    try:
        html = fetch(URL)
    except Exception as e:
        print(f"fetch failed: {e}")
        if test:
            send_telegram(f"Milpitas housing watch self-test: FETCH FAILED ({e}).")
        return  # don't touch state on a failed fetch

    cards = parse_cards(html)
    good = [c for c in cards if matches(c)]
    print(f"cards={len(cards)} matches={len(good)}")

    if test:
        lines = "\n".join(fmt(c) for c in good) or "(none right now)"
        send_telegram(
            "Milpitas Section 8 housing watch self-test.\n"
            f"Reached the site, saw {len(cards)} listings, "
            f"{len(good)} match (1-2 bed, ${MAX_RENT} or less):\n\n{lines}\n\n"
            "You'll get a ping like this whenever a NEW match appears. (Ignore this test.)")
        return

    # Guardrail: zero cards almost always means the site changed or blocked us.
    if len(cards) == 0:
        state = load_state()
        if "__BROKEN__" not in state:
            send_telegram(
                "Heads up: the Milpitas housing watcher fetched the page but found "
                "0 listings. The site may have changed or blocked the check. "
                "Krystle should take a look. (You won't get this again until it recovers.)")
            state.add("__BROKEN__")
            save_state(state)
        return

    prev = load_state()
    prev.discard("__BROKEN__")  # recovered
    cur_ids = {c["id"] for c in good}
    new = [c for c in good if c["id"] not in prev]

    if new:
        body = "\n".join(fmt(c) for c in new)
        send_telegram(
            f"New Milpitas Section 8 match{'es' if len(new) > 1 else ''} "
            f"(1-2 bed, ${MAX_RENT} or less):\n\n{body}\n\nAll listings: {URL}")
        print(f"alerted on {len(new)} new")

    save_state(cur_ids)


if __name__ == "__main__":
    main()
