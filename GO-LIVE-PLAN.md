# Go-live plan — approved by Andre 29 Sep 2026

Andre approved both steps: "yes, do both tomorrow". Run this during market
hours with Andre present. Start by 09:35 ET: step B has to finish before the
10:00 ET intraday cycle fires. If step A isn't done by about 09:55, don't
switch anything. Stay in paper mode and switch after the 10:00 cycle has run.

Context is in `CLAUDE.md`: the "Stops, trailing stops, options, fractional
shares" bullet and "Execution safety". The broker code is
`~/mcp/webull-mcp/webull_broker.py` and the watcher is `lib/trail.py`. Run the
broker code with `~/mcp/webull-mcp/venv/bin/python3` after
`set -a; . ~/buffet-bot/.env; set +a`.

## A. Sandbox test (play money). Step B only happens if this passes.

1. Confirm `WEBULL_ENV=sandbox`.
2. Buy 1 share of QQQ with a stop attached:
   `buy_stock('QQQ', 1, <near last>, stop_price=<about 2% below>)`.
   - Confirm Webull accepts the combined order.
   - Confirm `open_orders()` has a `client_order_id` field and shows both the
     buy and the stop.
3. `move_stock_stop` on the stop. This may be refused until the buy fills;
   write down what happens.
4. If the buy filled, confirm `positions()` field names: `symbol`,
   `quantity`, `last_price`, `cost_price`, `instrument_type`. Fix
   `webull_broker.positions()` if they differ. Then sell the shares and
   cancel the stop.
5. Fractional: buy 0.02 of QQQ (a market order, about $15), then sell it.
6. Option: find today's QQQ contracts near the current price, get a quote,
   buy 1 contract at the ask with a stop at 50% of the price paid. Then cancel
   it, or sell it if it filled.
7. Run `lib/trail.py --dry-run --force-hours` against a copy of the database
   that has a `stop-open` row for any test position still open.
8. Cancel anything left over. Confirm there are no open orders and no
   positions.
9. Record the results in `CLAUDE.md`.

## B. Go live

1. In `.env`, switch to the production credentials and set
   `WEBULL_ENV=production`. Check that `account()` shows about $271 and that
   `positions()` works.
2. Production test that must not fill: place a whole-share limit buy on a
   cheap stock about 3% below market, confirm it's open, cancel it, and
   confirm it was cancelled.
3. In `config.json`, set `execution_mode` to `live_direct` and turn off both
   safety checks (`require_confirmation_link: false`,
   `require_attended_session: false`). Update the notes there to say Andre
   chose this on 29 Sep 2026.
4. In `run-cycle.sh`, add `mcp__webull__*` to `--allowedTools`. Update
   `CLAUDE.md` "Execution safety" and the opening of `execution-agent.md` to
   describe the autonomous design, keeping the history.
5. Install and start the watcher:
   `cp systemd/buffet-bot-watch.* ~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user enable --now buffet-bot-watch.timer`
6. Commit (never `.env`) and send Andre a Telegram message saying live
   trading is on.
