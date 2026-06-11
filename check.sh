#!/usr/bin/env bash
# Mac mini refurbished stock watcher.
# Fetches Apple's US refurbished Mac page, detects Mac mini listings, and
# alerts via Telegram only when stock flips from out -> in (no repeat spam).
set -euo pipefail

UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
URL="https://www.apple.com/shop/refurbished/mac"

curl -s --max-time 30 -A "$UA" "$URL" -o page.html || { echo "fetch failed"; exit 0; }

BYTES=$(wc -c < page.html | tr -d ' ')
LISTINGS=$(grep -o '"title":"Refurbished Mac mini[^"]*"' page.html | sed 's/.*"title":"//; s/"$//' | sort -u || true)
TOTAL=$(grep -o '"title":"Refurbished [^"]*"' page.html | sort -u | wc -l | tr -d ' ')
MINI=$(printf '%s\n' "$LISTINGS" | grep -c . || true)

if [ -n "$LISTINGS" ]; then CUR="in"; else CUR="out"; fi
PREV=$(cat state.txt 2>/dev/null || echo "out")
echo "bytes=$BYTES total=$TOTAL mini=$MINI current=$CUR prev=$PREV"

send_telegram() {
  curl -s --max-time 30 "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=$1" >/dev/null
}

# One-time connectivity self-test: confirms the runner can reach Apple + Telegram.
if [ "${TEST_MODE:-0}" = "1" ]; then
  send_telegram "Mac mini watch self-test from GitHub. Reached Apple: page=${BYTES} bytes, total refurbished Mac products seen=${TOTAL}, Mac mini listings right now=${MINI}. (You can ignore this.)"
  echo "test ping sent"
  exit 0
fi

# Production: alert only on the out -> in transition.
if [ "$CUR" = "in" ] && [ "$PREV" != "in" ]; then
  send_telegram "Mac mini IN STOCK at Apple Refurbished:
$LISTINGS

Buy: $URL"
  echo "alert sent"
fi

echo "$CUR" > state.txt
