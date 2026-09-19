#!/usr/bin/env python3
"""
Risk engine. Deterministic, non-negotiable, and deliberately not an agent.

Every rule in section 5 of Andre's spec is a calculation, not a judgement, so a
language model has no business making these calls -- it would be slower, more
expensive, and occasionally creative. The Execution Agent proposes; this decides.

Every check returns its own verdict with the numbers that produced it, so a
rejection tells you which limit bit and by how much, rather than just "blocked".
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DB_PATH = os.environ.get("TA_DB", os.path.join(ROOT, "kb.sqlite"))
CONFIG_PATH = os.environ.get("TA_CONFIG", os.path.join(ROOT, "config.json"))

# NYSE full-day closures. Used only as a fallback -- live cycles ask Alpha
# Vantage for market status first, because a hardcoded calendar goes stale.
HOLIDAYS_2026_2027 = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=TRUNCATE")
    return conn


def _check(name, passed, detail, **numbers):
    return {"check": name, "passed": passed, "detail": detail, **numbers}


def max_loss_dollars(p):
    """Max loss in dollars, which is what gets shown to Andre. Not a percentage."""
    mult = 100.0 if p["instrument"] == "OPTION" else 1.0
    per_unit = (p["limit_price"] - p["stop_price"]) if p["side"] == "BUY" else (p["stop_price"] - p["limit_price"])
    return round(abs(per_unit) * p["quantity"] * mult, 2)


def notional(p):
    mult = 100.0 if p["instrument"] == "OPTION" else 1.0
    return round(p["limit_price"] * p["quantity"] * mult, 2)


def count_recent_day_trades(conn):
    """Counted from our own ledger. Webull's day_trades_left field reported
    UNLIMITED on a $271 margin account, which cannot be right, so we don't use it."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).date().isoformat()
    row = conn.execute("SELECT COUNT(*) c FROM day_trades WHERE trade_date >= ?", (cutoff,)).fetchone()
    return row["c"]


def would_be_day_trade(conn, ticker, side):
    if side != "SELL":
        return False
    today = datetime.now(timezone.utc).date().isoformat()
    row = conn.execute(
        "SELECT COUNT(*) c FROM orders WHERE ticker=? AND side='BUY' AND status='filled' AND date(resolved_at)=?",
        (ticker, today),
    ).fetchone()
    return row["c"] > 0


def market_open_now(market_status=None):
    """Is the market open? Answered from an AUTHORITATIVE source, never a clock.

    This used to read the local clock. That was wrong in a way that only showed
    up under test: the cloud container's clock ran 81 minutes ahead of the
    user's PC, so at 08:17 Eastern -- with the market shut -- this returned
    "regular session, 09:38 ET" and the gate passed. A pre-market order would
    have cleared the one check whose entire job is to stop that.

    The lesson generalises past the skew. A cycle can run in the cloud
    container, on the PC, or half in each, and nothing guarantees those clocks
    agree with each other or with Eastern time. A risk gate must not be
    decided by whichever machine happened to execute it.

    So: pass `market_status` from Alpha Vantage MARKET_STATUS (its US equity
    entry's `current_status`, "open" or "closed") or an equivalent broker
    field. With no authoritative answer this check FAILS CLOSED and says why.
    That is deliberate -- a gate that silently guesses is worse than one that
    refuses to answer, because the guess is invisible and the refusal is not.
    """
    if market_status is not None:
        status = str(market_status).strip().lower()
        if status in ("open", "true", "1"):
            return True, "market reported open by the venue status feed"
        if status in ("closed", "false", "0"):
            return False, "market reported closed by the venue status feed"
        return False, f"unrecognised market status '{market_status}'"

    # No authoritative status. Show what the local clock thinks, clearly
    # labelled as untrusted, and fail the check regardless.
    now = datetime.now(timezone.utc)
    et = now - timedelta(hours=4)
    return False, (
        "market status not verified -- no venue status supplied, and the local "
        f"clock is not trusted for this (it reads {et.strftime('%H:%M')} ET, but "
        "container and device clocks have been observed 81 minutes apart). "
        "Fetch Alpha Vantage MARKET_STATUS and pass it as account.market_status.")


def evaluate(proposal, account):
    cfg = load_config()
    r = cfg["risk"]
    conn = connect()
    checks = []

    nlv = float(account["net_liquidation_value"])
    day_pl = float(account.get("day_profit_loss", 0))
    bp = float(account.get("buying_power", 0))
    n = notional(proposal)
    mloss = max_loss_dollars(proposal)
    is_entry = proposal["side"] == "BUY"

    # 1. Stop is mandatory. Without one, max loss is undefined and sizing is a guess.
    checks.append(_check(
        "stop_present", proposal.get("stop_price") is not None,
        "every proposal must carry a stop; max loss is undefined without one"))

    # 2. Per-trade size cap.
    cap = nlv * r["per_trade_pct"] / 100
    checks.append(_check(
        "per_trade_cap", n <= cap,
        f"${n:,.2f} against a ${cap:,.2f} cap ({r['per_trade_pct']}% of ${nlv:,.2f})",
        notional=n, cap=round(cap, 2)))

    # 3. Per-ticker exposure cap, counting what is already held.
    held = conn.execute("SELECT COALESCE(market_value,0) mv FROM positions WHERE ticker=?",
                        (proposal["ticker"],)).fetchone()
    held_mv = float(held["mv"]) if held else 0.0
    tcap = nlv * r["per_ticker_pct"] / 100
    combined = held_mv + (n if is_entry else 0)
    checks.append(_check(
        "per_ticker_cap", combined <= tcap,
        f"${combined:,.2f} total in {proposal['ticker']} against a ${tcap:,.2f} cap "
        f"({r['per_ticker_pct']}% of ${nlv:,.2f}); ${held_mv:,.2f} already held",
        combined=round(combined, 2), cap=round(tcap, 2)))

    # 4. Daily loss halt. Blocks new entries only -- you can always close a position.
    halt_at = -abs(nlv * r["daily_loss_halt_pct"] / 100)
    halted = day_pl <= halt_at
    checks.append(_check(
        "daily_loss_halt", (not halted) or (not is_entry),
        f"day P/L ${day_pl:,.2f} against a ${halt_at:,.2f} halt threshold"
        + (" -- halted, new entries blocked (exits still allowed)" if halted else ""),
        day_pl=day_pl, halt_threshold=round(halt_at, 2)))

    # 5. Buying power.
    checks.append(_check(
        "buying_power", (not is_entry) or n <= bp,
        f"${n:,.2f} required against ${bp:,.2f} available", required=n, available=bp))

    # 6. Pattern day trader rule.
    dt_count = count_recent_day_trades(conn)
    is_dt = would_be_day_trade(conn, proposal["ticker"], proposal["side"])
    pdt_ok = True
    pdt_detail = f"{dt_count} day trades in the trailing 5 sessions; equity ${nlv:,.2f}"
    if nlv < r["pdt_equity_floor"] and is_dt and dt_count >= r["pdt_max_day_trades"]:
        pdt_ok = False
        pdt_detail += f" -- this would be day trade #{dt_count + 1} under the ${r['pdt_equity_floor']:,.0f} floor"
    checks.append(_check("pdt", pdt_ok, pdt_detail, day_trades_used=dt_count, would_be_day_trade=is_dt))

    # 7. Duplicate of an order already working.
    dup = conn.execute(
        "SELECT COUNT(*) c FROM orders WHERE ticker=? AND side=? AND status IN ('proposed','awaiting_confirm')",
        (proposal["ticker"], proposal["side"])).fetchone()["c"]
    checks.append(_check("no_duplicate", dup == 0,
                         f"{dup} open {proposal['side']} order(s) already working on {proposal['ticker']}"))

    # 8. Market hours.
    open_now, hours_detail = market_open_now(account.get("market_status"))
    checks.append(_check("market_hours", open_now, hours_detail))

    # 9. Averaging down needs a flagged, explicit rationale.
    avg_ok = True
    avg_detail = "not an averaging-down entry"
    if is_entry and held_mv > 0:
        pos = conn.execute("SELECT COALESCE(unrealized_pl,0) pl FROM positions WHERE ticker=?",
                           (proposal["ticker"],)).fetchone()
        if pos and float(pos["pl"]) < 0:
            if not proposal.get("is_averaging_down"):
                avg_ok = False
                avg_detail = "adding to a losing position but not flagged as averaging down"
            elif not proposal.get("averaging_rationale"):
                avg_ok = False
                avg_detail = "flagged as averaging down but no rationale given"
            elif not cfg["risk"]["allow_averaging_down"]:
                avg_ok = False
                avg_detail = "averaging down is disabled in config"
            else:
                avg_detail = f"averaging down, rationale: {proposal['averaging_rationale']}"
    checks.append(_check("averaging_down", avg_ok, avg_detail))

    failed = [c for c in checks if not c["passed"]]
    return {
        "proposal_id": proposal.get("proposal_id"),
        "ticker": proposal["ticker"],
        "approved": len(failed) == 0,
        "max_loss_dollars": mloss,
        "notional": n,
        "pct_of_account": round(n / nlv * 100, 2) if nlv else None,
        "checks": checks,
        "failed": [c["check"] for c in failed],
        "summary": (f"APPROVED -- max loss ${mloss:,.2f} on ${n:,.2f} notional"
                    if not failed else
                    f"REJECTED -- {', '.join(c['check'] for c in failed)}"),
    }


def main():
    if len(sys.argv) < 3:
        print(json.dumps({"ok": False, "error": "usage: risk.py '<proposal json>' '<account json>'"}))
        sys.exit(2)
    result = evaluate(json.loads(sys.argv[1]), json.loads(sys.argv[2]))
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["approved"] else 1)


if __name__ == "__main__":
    main()
