# Mac mini refurb watch

Checks Apple's US refurbished Mac page every ~15 minutes and sends a Telegram
message the moment a **Mac mini** appears in stock. Runs entirely on GitHub
Actions (free, always on), so it works whether or not any personal computer is on.

- Alerts fire only on the out-to-in transition, so you get one ping when stock
  appears, not a message every 15 minutes.
- `check.sh` does the work; `state.txt` remembers the last seen status.
- Telegram bot token + chat ID live in repo **Secrets** (`TELEGRAM_TOKEN`,
  `TELEGRAM_CHAT_ID`), never in the code.

To pause alerts: disable the workflow in the repo's **Actions** tab.
