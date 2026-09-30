#!/usr/bin/env python3
"""
Stop watcher. Runs every 5 minutes in market hours (systemd/buffet-bot-watch.timer),
deterministically, with no language model involved -- the same reasoning as
lib/risk.py: moving a stop is a calculation, not a judgement.

What it does, per position it manages (the `stops` table, which the execution
agent fills via `kb.py stop-open`):

  - Marks the row active once Webull shows the position (entry filled), or
    closes it if the entry died unfilled.
  - Makes sure a real stop order is working at Webull. Option stops are DAY
    only (Webull rule), so they are re-placed every morning; a buy's attached
    stop can also lapse. Fractional shares can never carry a broker stop
    (Webull allows MARKET only for fractional), so for those the watcher IS
    the stop: price at or below it -> market sell.
  - Trails. Stock: once the price is up 1R (R = entry minus initial stop), the
    stop goes to breakeven, then follows the high by 1R. Option: once the
    premium is up option_activate_pct, the stop follows the highest premium
    seen by option_trail_pct. Stops only ever move up.
  - Closes any option on its expiry day at expiry_close_time. An option left
    to expire in the money is exercised into 100 shares per contract, which
    a small account cannot pay for.

It only ever SELLS. It never opens or adds to a position.

It acts only when config.json execution_mode is live_direct. In any other mode,
or with --dry-run, it prints what it would do and changes nothing.

Run under the Webull MCP venv, which has the SDK:
    ~/mcp/webull-mcp/venv/bin/python3 lib/trail.py [--dry-run] [--force-hours]
"""

import argparse
import json
import os
import sqlite3
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.expanduser("~/mcp/webull-mcp"))

from risk import HOLIDAYS_2026_2027  # noqa: E402
from webull_broker import Broker, BrokerError  # noqa: E402

DB_PATH = os.environ.get("TA_DB", os.path.join(ROOT, "kb.sqlite"))
CONFIG_PATH = os.environ.get("TA_CONFIG", os.path.join(ROOT, "config.json"))
ET = ZoneInfo("America/New_York")

DEFAULT_RULES = {
    "stock_breakeven_at_r": 1.0,
    "stock_trail_r": 1.0,
    "option_activate_pct": 30.0,
    "option_trail_pct": 25.0,
    "min_move_pct": 0.5,
    "expiry_close_time": "15:40",
}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=TRUNCATE")
    return conn


def market_hours(now_et):
    """This machine's clock, NTP-synced, deciding only whether the watcher
    bothers to run. It never approves an entry -- that is risk.py's
    market_hours check, which asks the venue."""
    if now_et.weekday() >= 5 or now_et.date().isoformat() in HOLIDAYS_2026_2027:
        return False
    hm = now_et.strftime("%H:%M")
    return "09:30" <= hm < "16:00"


def round_price(p, instrument):
    return round(p, 2)


def new_stop_for(row, price, high, rules):
    """The stop this position should have now. Never lower than the current one."""
    entry, initial, current = row["entry_price"], row["initial_stop"], row["current_stop"]
    target = current
    if row["instrument"] == "OPTION":
        if high >= entry * (1 + rules["option_activate_pct"] / 100):
            target = max(target, high * (1 - rules["option_trail_pct"] / 100))
    else:
        r = entry - initial
        if r > 0 and high >= entry + rules["stock_breakeven_at_r"] * r:
            target = max(target, entry, high - rules["stock_trail_r"] * r)
    return round_price(target, row["instrument"])


class Watcher:
    def __init__(self, dry_run):
        with open(CONFIG_PATH) as f:
            self.cfg = json.load(f)
        self.rules = {**DEFAULT_RULES, **self.cfg.get("trailing", {})}
        self.live = self.cfg.get("execution_mode") == "live_direct" and not dry_run
        self.conn = connect()
        self.broker = Broker()
        self.actions = []
        self.cycle_id = f"watch-{datetime.now(ET).date().isoformat()}"

    # -- bookkeeping ---------------------------------------------------------

    def act(self, row, what, fn=None, **update):
        """Do one broker action (live) or describe it (dry run), then record it."""
        label = f"{row['key']}: {what}"
        result = None
        if self.live and fn is not None:
            try:
                result = fn()
            except BrokerError as e:
                self.log(row, f"FAILED {what}", str(e))
                self.actions.append(f"FAILED {label} -- {e}")
                return None
        if self.live:
            if update:
                self.update(row, **update)
            self.log(row, what, json.dumps(result)[:500] if result else None)
        self.actions.append(("" if self.live else "[dry run] ") + label)
        return result or {}

    def update(self, row, **fields):
        fields["updated_at"] = now_iso()
        cols = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE stops SET {cols} WHERE id=?", (*fields.values(), row["id"]))
        self.conn.commit()

    def log(self, row, decision, detail):
        self.conn.execute(
            "INSERT INTO decisions(cycle_id,actor,decision,inputs,reasoning,created_at) VALUES(?,?,?,?,?,?)",
            (self.cycle_id, "trail", decision, json.dumps(dict(row)), detail, now_iso()))
        self.conn.commit()

    def notify(self):
        if not self.actions or not self.live:
            return
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat = self.cfg.get("telegram_chat_id") or os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat:
            return
        text = "Stop watcher:\n" + "\n".join(self.actions)
        data = urllib.parse.urlencode({"chat_id": chat, "text": text[:4000]}).encode()
        try:
            urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=15)
        except Exception as e:
            print(f"telegram failed: {e}", file=sys.stderr)

    # -- broker helpers ------------------------------------------------------

    def place_stop(self, row, qty, stop):
        b = self.broker
        if row["instrument"] == "OPTION":
            return lambda: b.place_option_stop(row["ticker"], row["option_type"], row["strike"], row["expiry"],
                                               int(qty), stop)
        return lambda: b.place_stock_stop(row["ticker"], int(qty), stop)

    def exit_now(self, row, pos, reason):
        """Sell the whole position now. Stock: market. Option: limit 10% under
        last (Webull allows no market orders on options)."""
        b = self.broker
        if row["stop_ref"] and row["stop_ref"] in (self.open_refs or set()):
            self.act(row, f"cancel stop {row['stop_ref']} before exit", lambda: b.cancel(row["stop_ref"]))
        if row["instrument"] == "OPTION":
            limit = max(0.01, round(pos["last_price"] * 0.9, 2))
            fn = lambda: b.sell_option(row["ticker"], row["option_type"], row["strike"], row["expiry"],
                                       int(pos["quantity"]), limit)
            what = f"EXIT ({reason}) sell {int(pos['quantity'])} @ {limit} limit, last {pos['last_price']}"
        else:
            fn = lambda: b.sell_stock(row["ticker"], pos["quantity"])
            what = f"EXIT ({reason}) market sell {pos['quantity']} sh, last {pos['last_price']}"
        res = self.act(row, what, fn, status="exiting", note=reason)
        if res is not None and self.live and res.get("ref"):
            self.update(row, stop_ref=res["ref"])

    # -- main loop -----------------------------------------------------------

    def run(self, now_et):
        positions = {p["key"]: p for p in self.broker.positions()}
        try:
            orders = self.broker.open_orders()
            refs = [o.get("client_order_id") for o in orders]
            if any(r is None for r in refs):
                raise BrokerError(f"open order without client_order_id; keys: {sorted(orders[0].keys())}")
            self.open_refs = set(refs)
        except BrokerError as e:
            # Without the order list we cannot tell whether a stop is working,
            # so we must not place another one -- a duplicate stop could sell
            # shares twice. Trailing moves (replace by ref) still run.
            print(f"open orders unavailable, not re-placing stops: {e}", file=sys.stderr)
            self.open_refs = None

        rows = self.conn.execute(
            "SELECT * FROM stops WHERE status IN ('pending_fill','active','exiting') ORDER BY id").fetchall()
        for row in rows:
            self.handle(row, positions.get(row["key"]), now_et)
        self.notify()
        for a in self.actions:
            print(a)

    def handle(self, row, pos, now_et):
        b = self.broker
        working = self.open_refs

        if pos is None or pos["quantity"] <= 0:
            if row["status"] == "pending_fill" and working is not None and row["entry_ref"] in working:
                return  # entry still waiting to fill
            if row["stop_ref"] and working is not None and row["stop_ref"] in working:
                self.act(row, f"position gone, cancel leftover order {row['stop_ref']}",
                         lambda: b.cancel(row["stop_ref"]))
            reason = "entry never filled" if row["status"] == "pending_fill" else "position closed"
            self.act(row, reason, None, status="closed", closed_at=now_iso(), note=reason)
            return

        if row["status"] == "exiting":
            # The row closes once the position is gone. Until then: an option
            # exit is a limit order that can sit unfilled while the premium
            # falls, so re-price it every run; any exit order that died gets
            # re-sent.
            if working is None:
                return
            if row["stop_ref"] in working:
                if row["instrument"] != "OPTION":
                    return
                self.act(row, f"re-price unfilled exit {row['stop_ref']}", lambda: b.cancel(row["stop_ref"]))
                self.open_refs.discard(row["stop_ref"])
            self.exit_now(row, pos, "exit retry")
            return

        if row["status"] == "pending_fill":
            self.act(row, f"entry filled, {pos['quantity']} @ ~{pos['cost_price']}", None,
                     status="active", quantity=pos["quantity"])

        qty, price = pos["quantity"], pos["last_price"]
        high = max(row["high_water"] or price, price)
        if self.live and high != row["high_water"]:
            self.update(row, high_water=high)

        # 1. Options on expiry day: out before the close, whatever the stop says.
        if row["instrument"] == "OPTION" and row["expiry"] == now_et.date().isoformat() \
                and now_et.strftime("%H:%M") >= self.rules["expiry_close_time"]:
            self.exit_now(row, pos, "expires today")
            return

        # 2. Stop hit with no broker stop to catch it (fractional, or a lapsed stop).
        broker_stop_working = (row["stop_ref"] is not None and working is not None and row["stop_ref"] in working)
        if price <= row["current_stop"] and (row["fractional"] or (working is not None and not broker_stop_working)):
            self.exit_now(row, pos, f"stop {row['current_stop']} hit")
            return

        target = new_stop_for(row, price, high, self.rules)
        moved = target > row["current_stop"] * (1 + self.rules["min_move_pct"] / 100)
        stop = target if moved else row["current_stop"]

        # 3. Fractional: the watcher is the stop. Just record the new level.
        if row["fractional"]:
            if moved:
                self.act(row, f"trail stop {row['current_stop']} -> {stop} (watched, fractional)", None,
                         current_stop=stop)
            return

        # 4. No working broker stop (option DAY stop lapsed overnight, or the
        #    buy's attached stop didn't survive): place one at the current level.
        if working is not None and not broker_stop_working:
            res = self.act(row, f"place stop {int(qty)} @ {stop}", self.place_stop(row, qty, stop))
            if res is not None and self.live and res.get("ref"):
                self.update(row, stop_ref=res["ref"], current_stop=stop)
            return

        # 5. Trail the working stop up.
        if moved:
            if row["instrument"] == "OPTION":
                # Option replace needs leg ids from the order detail; cancel and
                # re-place is simpler and the gap is one API round trip.
                self.act(row, f"cancel stop {row['stop_ref']} to raise it", lambda: b.cancel(row["stop_ref"]))
                res = self.act(row, f"trail stop {row['current_stop']} -> {stop}", self.place_stop(row, qty, stop))
                if res is not None and self.live and res.get("ref"):
                    self.update(row, stop_ref=res["ref"], current_stop=stop)
            else:
                self.act(row, f"trail stop {row['current_stop']} -> {stop}",
                         lambda: b.move_stock_stop(row["stop_ref"], stop), current_stop=stop)


def main():
    ap = argparse.ArgumentParser(description="buffet-bot stop watcher")
    ap.add_argument("--dry-run", action="store_true", help="print what would happen, change nothing")
    ap.add_argument("--force-hours", action="store_true", help="run even outside market hours (testing)")
    args = ap.parse_args()

    now_et = datetime.now(ET)
    if not args.force_hours and not market_hours(now_et):
        return
    try:
        w = Watcher(args.dry_run)
        if not w.live:
            print(f"[dry run] execution_mode={w.cfg.get('execution_mode')}, broker env={w.broker.env}")
        w.run(now_et)
    except BrokerError as e:
        print(f"watcher could not run: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
