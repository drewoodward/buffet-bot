#!/usr/bin/env bash
# One-off: Telegram reminder for the go-live session on 30 Sep 2026. Delete after.
set -a; . /home/millie/buffet-bot/.env; set +a
curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
  --data-urlencode "text=Reminder: buffet-bot go-live is this morning. By 9:35 ET, open Claude Code in ~/buffet-bot and say: run GO-LIVE-PLAN.md
It tests on the sandbox first, then switches to real-money trading before the 10:00 cycle. Say \"run step A only\" to stay in paper mode. Keep the machine awake until 4pm." > /dev/null
