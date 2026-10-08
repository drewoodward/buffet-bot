#!/usr/bin/env python3
"""
Knowledge base for the trading agent system.

Single source of durable truth. Every scheduled run starts as a fresh session
with no memory, so anything that must survive a cycle lives here and nowhere
else. This module owns ALL writes: agents call the CLI, they never touch the
database file directly.

Journal mode is TRUNCATE, and that is not a style choice. The database lives on
a FUSE mount where file deletion is disabled, so DELETE journaling fails outright
-- SQLite cannot remove its rollback journal and reports a disk I/O error. WAL is
worse: its -wal and -shm sidecars are exactly what OneDrive sync mangles.
TRUNCATE zeroes the journal in place instead of unlinking it: same crash safety,
no deletes, one extra file that never moves. Writes are short and single-writer,
so the concurrency WAL would buy us costs nothing to give up.
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

DB_PATH = os.environ.get("TA_DB", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kb.sqlite"))


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=TRUNCATE")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=FULL")
    migrate_accounts(conn)
    return conn


def migrate_accounts(conn):
    """Add the `account` column (7 Oct 2026, when the cash account joined the
    margin one). Every row from before then belongs to the margin account,
    which is what the default says. Idempotent; a no-op on a fresh database,
    where SCHEMA already has the column."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("orders", "stops", "day_trades"):
        if t in tables and "account" not in {r[1] for r in conn.execute(f"PRAGMA table_info({t})")}:
            conn.execute(f"ALTER TABLE {t} ADD COLUMN account TEXT NOT NULL DEFAULT 'margin'")
    # positions was keyed on ticker alone; two accounts can hold the same one.
    if "positions" in tables and "account" not in {r[1] for r in conn.execute("PRAGMA table_info(positions)")}:
        conn.executescript("""
            BEGIN;
            CREATE TABLE positions_new (
              account TEXT NOT NULL DEFAULT 'margin',
              ticker TEXT NOT NULL,
              quantity REAL NOT NULL,
              cost_basis REAL,
              market_value REAL,
              unrealized_pl REAL,
              asset_type TEXT DEFAULT 'EQUITY',
              opened_at TEXT,
              synced_at TEXT NOT NULL,
              PRIMARY KEY (account, ticker)
            );
            INSERT INTO positions_new(account,ticker,quantity,cost_basis,market_value,unrealized_pl,asset_type,opened_at,synced_at)
              SELECT 'margin',ticker,quantity,cost_basis,market_value,unrealized_pl,asset_type,opened_at,synced_at FROM positions;
            DROP TABLE positions;
            ALTER TABLE positions_new RENAME TO positions;
            COMMIT;
        """)
    conn.commit()


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);

-- Snapshot of the real portfolio, refreshed from Webull at the top of every cycle.
CREATE TABLE IF NOT EXISTS positions (
  account TEXT NOT NULL DEFAULT 'margin',
  ticker TEXT NOT NULL,
  quantity REAL NOT NULL,
  cost_basis REAL,
  market_value REAL,
  unrealized_pl REAL,
  asset_type TEXT DEFAULT 'EQUITY',
  opened_at TEXT,
  synced_at TEXT NOT NULL,
  PRIMARY KEY (account, ticker)
);

-- Append-only. A thesis is never UPDATEd; a change writes a new row pointing at
-- the one it supersedes. "What changed and why" is therefore structural.
CREATE TABLE IF NOT EXISTS thesis_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker TEXT NOT NULL,
  version INTEGER NOT NULL,
  thesis TEXT NOT NULL,
  conviction TEXT,
  horizon TEXT,
  staleness_days INTEGER NOT NULL DEFAULT 30,
  supersedes INTEGER,
  change_reason TEXT,
  changed_by TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(ticker, version)
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker TEXT,
  event_type TEXT NOT NULL,
  headline TEXT NOT NULL,
  summary TEXT,
  why_material TEXT,
  materiality_total INTEGER,
  materiality_breakdown TEXT,
  contradicts TEXT,
  source TEXT,
  source_url TEXT,
  published_at TEXT,
  dedupe_key TEXT,
  alerted INTEGER DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_ticker ON events(ticker, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_dedupe ON events(dedupe_key) WHERE dedupe_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS levels (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker TEXT NOT NULL,
  kind TEXT NOT NULL,
  price REAL NOT NULL,
  timeframe TEXT NOT NULL,
  basis TEXT,
  derived_at TEXT NOT NULL,
  staleness_days INTEGER DEFAULT 10,
  active INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_levels_ticker ON levels(ticker, active);

CREATE TABLE IF NOT EXISTS catalysts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker TEXT,
  kind TEXT,
  description TEXT,
  event_date TEXT,
  confirmed INTEGER DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_catalysts_date ON catalysts(event_date);

CREATE TABLE IF NOT EXISTS alpha_reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  report_date TEXT NOT NULL,
  received_at TEXT NOT NULL,
  source_path TEXT,
  raw_text TEXT NOT NULL,
  market_commentary TEXT,
  sha256 TEXT UNIQUE
);

-- Kevin's calls, with outcome fields filled in at horizon. The only way to know
-- whether an input is worth acting on is a scored record.
CREATE TABLE IF NOT EXISTS alpha_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  report_id INTEGER NOT NULL REFERENCES alpha_reports(id),
  ticker TEXT NOT NULL,
  direction TEXT,
  instrument TEXT,
  entry_hint REAL,
  stop_hint REAL,
  target_hint REAL,
  horizon TEXT,
  raw_excerpt TEXT,
  our_verdict TEXT,
  our_reasoning TEXT,
  price_at_call REAL,
  scored_at TEXT,
  price_at_horizon REAL,
  outcome TEXT,
  outcome_pct REAL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alpha_calls_ticker ON alpha_calls(ticker);

CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mode TEXT NOT NULL,
  ticker TEXT NOT NULL,
  side TEXT NOT NULL,
  quantity REAL,
  order_type TEXT,
  limit_price REAL,
  time_in_force TEXT,
  instrument TEXT,
  entry_zone TEXT,
  stop_price REAL,
  target_price TEXT,
  max_loss_dollars REAL,
  rationale TEXT,
  proposal_id TEXT,
  status TEXT NOT NULL,
  reject_reason TEXT,
  broker_ref TEXT,
  submitted_at TEXT,
  resolved_at TEXT,
  fill_price REAL,
  is_day_trade INTEGER DEFAULT 0,
  created_at TEXT NOT NULL,
  account TEXT NOT NULL DEFAULT 'margin'
);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status, created_at DESC);

CREATE TABLE IF NOT EXISTS source_status (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cycle_id TEXT NOT NULL,
  source TEXT NOT NULL,
  status TEXT NOT NULL,
  detail TEXT,
  checked_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_source_cycle ON source_status(cycle_id);

-- Every decision with the inputs that produced it. This is the audit trail.
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cycle_id TEXT NOT NULL,
  actor TEXT NOT NULL,
  decision TEXT NOT NULL,
  inputs TEXT,
  reasoning TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decisions_cycle ON decisions(cycle_id, created_at);

-- Counted ourselves. Webull's day_trades_left field is not trusted.
CREATE TABLE IF NOT EXISTS day_trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker TEXT,
  trade_date TEXT NOT NULL,
  order_id INTEGER,
  created_at TEXT NOT NULL,
  account TEXT NOT NULL DEFAULT 'margin'
);

-- One row per open position the stop watcher (lib/trail.py) manages. The
-- execution agent opens it when an entry is placed; trail.py moves the stop
-- up, re-places expired stops, and closes the row when the position is gone.
-- `key` matches Webull positions: ticker for stock, UNDERLYING:TYPE:STRIKE:EXPIRY
-- for an option.
CREATE TABLE IF NOT EXISTS stops (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  key TEXT NOT NULL,
  ticker TEXT NOT NULL,
  instrument TEXT NOT NULL,
  option_type TEXT,
  strike REAL,
  expiry TEXT,
  quantity REAL NOT NULL,
  entry_price REAL NOT NULL,
  initial_stop REAL NOT NULL,
  current_stop REAL NOT NULL,
  high_water REAL,
  fractional INTEGER NOT NULL DEFAULT 0,
  entry_ref TEXT,
  stop_ref TEXT,
  proposal_id TEXT,
  status TEXT NOT NULL,
  note TEXT,
  opened_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  closed_at TEXT,
  account TEXT NOT NULL DEFAULT 'margin'
);
CREATE INDEX IF NOT EXISTS idx_stops_status ON stops(status);

CREATE TABLE IF NOT EXISTS cycles (
  cycle_id TEXT PRIMARY KEY,
  phase TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT,
  summary TEXT
);
"""


def cmd_init(args):
    conn = connect()
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version','1')")
    conn.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('created_at',?)", (now(),))
    conn.commit()
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    print(json.dumps({"ok": True, "db": DB_PATH, "tables": tables}, indent=2))


def cmd_sync_positions(args):
    """Replace one account's position snapshot wholesale. Payload is either
    {"account": "cash", "positions": [...]} or, as before 7 Oct 2026, the bare
    Webull positions array, which means the margin account. Only that
    account's rows are replaced."""
    payload = json.loads(args.json)
    if isinstance(payload, dict):
        account, positions = payload.get("account", "margin"), payload.get("positions") or []
    else:
        account, positions = "margin", payload
    conn = connect()
    ts = now()
    conn.execute("DELETE FROM positions WHERE account=?", (account,))
    for p in positions:
        conn.execute(
            "INSERT INTO positions(account,ticker,quantity,cost_basis,market_value,unrealized_pl,asset_type,opened_at,synced_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (account, p.get("ticker"), float(p.get("quantity", 0)), _f(p.get("cost_basis")), _f(p.get("market_value")),
             _f(p.get("unrealized_pl")), p.get("asset_type", "EQUITY"), p.get("opened_at"), ts),
        )
    conn.commit()
    print(json.dumps({"ok": True, "account": account, "count": len(positions), "synced_at": ts}))


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def cmd_set_thesis(args):
    p = json.loads(args.json)
    conn = connect()
    row = conn.execute(
        "SELECT id, version FROM thesis_versions WHERE ticker=? ORDER BY version DESC LIMIT 1", (p["ticker"],)
    ).fetchone()
    version = (row["version"] + 1) if row else 1
    supersedes = row["id"] if row else None
    if row and not p.get("change_reason"):
        print(json.dumps({"ok": False, "error": "change_reason is required when superseding an existing thesis"}))
        sys.exit(2)
    cur = conn.execute(
        "INSERT INTO thesis_versions(ticker,version,thesis,conviction,horizon,staleness_days,supersedes,change_reason,changed_by,created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (p["ticker"], version, p["thesis"], p.get("conviction"), p.get("horizon"),
         int(p.get("staleness_days", 30)), supersedes, p.get("change_reason"), p.get("changed_by", "unknown"), now()),
    )
    conn.commit()
    print(json.dumps({"ok": True, "id": cur.lastrowid, "ticker": p["ticker"], "version": version, "supersedes": supersedes}))


def cmd_add_event(args):
    p = json.loads(args.json)
    conn = connect()
    key = p.get("dedupe_key")
    if not key:
        basis = f"{p.get('ticker','')}|{p.get('event_type','')}|{p.get('headline','')}".lower()
        key = hashlib.sha256(basis.encode()).hexdigest()[:32]
    existing = conn.execute("SELECT id FROM events WHERE dedupe_key=?", (key,)).fetchone()
    if existing:
        print(json.dumps({"ok": True, "duplicate": True, "id": existing["id"],
                          "note": "already in the event log; not re-alerted"}))
        return
    cur = conn.execute(
        "INSERT INTO events(ticker,event_type,headline,summary,why_material,materiality_total,materiality_breakdown,"
        "contradicts,source,source_url,published_at,dedupe_key,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (p.get("ticker"), p["event_type"], p["headline"], p.get("summary"), p.get("why_material"),
         p.get("materiality_total"), json.dumps(p.get("materiality_breakdown")) if p.get("materiality_breakdown") else None,
         p.get("contradicts"), p.get("source"), p.get("source_url"), p.get("published_at"), key, now()),
    )
    conn.commit()
    print(json.dumps({"ok": True, "duplicate": False, "id": cur.lastrowid, "dedupe_key": key}))


def cmd_stale(args):
    """The staleness scan. A query, not an opinion."""
    conn = connect()
    out = {"generated_at": now(), "stale_theses": [], "stale_levels": [], "positions_without_thesis": [],
           "upcoming_catalysts": [], "unscored_alpha_calls": []}

    for r in conn.execute("""
        SELECT t.ticker, t.version, t.created_at, t.staleness_days, t.conviction
        FROM thesis_versions t
        JOIN (SELECT ticker, MAX(version) v FROM thesis_versions GROUP BY ticker) m
          ON m.ticker = t.ticker AND m.v = t.version
    """):
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(r["created_at"])).days
        if age > r["staleness_days"]:
            out["stale_theses"].append({"ticker": r["ticker"], "version": r["version"],
                                        "age_days": age, "horizon_days": r["staleness_days"]})

    for r in conn.execute("SELECT id,ticker,kind,price,timeframe,derived_at,staleness_days FROM levels WHERE active=1"):
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(r["derived_at"])).days
        if age > r["staleness_days"]:
            out["stale_levels"].append({"id": r["id"], "ticker": r["ticker"], "kind": r["kind"],
                                        "price": r["price"], "timeframe": r["timeframe"], "age_days": age})

    for r in conn.execute("""
        SELECT DISTINCT p.ticker FROM positions p
        LEFT JOIN thesis_versions t ON t.ticker = p.ticker
        WHERE t.id IS NULL
    """):
        out["positions_without_thesis"].append(r["ticker"])

    horizon = (datetime.now(timezone.utc) + timedelta(days=14)).date().isoformat()
    today = datetime.now(timezone.utc).date().isoformat()
    for r in conn.execute(
        "SELECT ticker,kind,description,event_date FROM catalysts WHERE event_date BETWEEN ? AND ? ORDER BY event_date",
        (today, horizon),
    ):
        out["upcoming_catalysts"].append(dict(r))

    for r in conn.execute(
        "SELECT id,ticker,direction,horizon,created_at FROM alpha_calls WHERE outcome IS NULL ORDER BY created_at"
    ):
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(r["created_at"])).days
        if age >= 1:
            out["unscored_alpha_calls"].append({**dict(r), "age_days": age})

    out["clean"] = not any(out[k] for k in
                           ("stale_theses", "stale_levels", "positions_without_thesis", "unscored_alpha_calls"))
    print(json.dumps(out, indent=2))


def cmd_log_decision(args):
    p = json.loads(args.json)
    conn = connect()
    cur = conn.execute(
        "INSERT INTO decisions(cycle_id,actor,decision,inputs,reasoning,created_at) VALUES(?,?,?,?,?,?)",
        (p["cycle_id"], p["actor"], p["decision"],
         json.dumps(p.get("inputs")) if p.get("inputs") is not None else None, p.get("reasoning"), now()),
    )
    conn.commit()
    print(json.dumps({"ok": True, "id": cur.lastrowid}))


def cmd_log_order(args):
    """Record a proposal at submit time -- step one of the OrderRecord
    lifecycle execution-agent.md describes. resolve-order (below) closes it
    out once a fill, rejection or cancellation lands."""
    p = json.loads(args.json)
    conn = connect()
    cur = conn.execute(
        "INSERT INTO orders(mode,ticker,side,quantity,order_type,limit_price,time_in_force,"
        "instrument,entry_zone,stop_price,target_price,max_loss_dollars,rationale,proposal_id,"
        "status,reject_reason,broker_ref,submitted_at,created_at,account)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (p["mode"], p["ticker"], p["side"], _f(p.get("quantity")), p.get("order_type"),
         _f(p.get("limit_price")), p.get("time_in_force"), p.get("instrument"),
         p.get("entry_zone"), _f(p.get("stop_price")),
         json.dumps(p.get("targets")) if p.get("targets") is not None else None,
         _f(p.get("max_loss_dollars")), p.get("rationale"), p.get("proposal_id"),
         p["status"], p.get("reject_reason"), p.get("broker_ref"),
         p.get("submitted_at") or now(), now(), p.get("account", "margin")),
    )
    conn.commit()
    print(json.dumps({"ok": True, "id": cur.lastrowid}))


def cmd_resolve_order(args):
    """Close out an order log-order opened: a fill, rejection or cancellation
    landed. Matches by proposal_id (preferred) or broker_ref -- whichever the
    caller has on hand. is_day_trade is taken as given, not computed here:
    execution-agent decides whether the position opened the same day: this
    command's job is to record that decision, not make it."""
    p = json.loads(args.json)
    conn = connect()
    where_col, where_val = (("proposal_id", p["proposal_id"]) if p.get("proposal_id")
                             else ("broker_ref", p.get("broker_ref")))
    if not where_val:
        print(json.dumps({"ok": False, "error": "resolve-order needs proposal_id or broker_ref"}))
        sys.exit(2)

    resolved_at = now()
    cur = conn.execute(
        f"UPDATE orders SET status=?, reject_reason=?, broker_ref=COALESCE(?, broker_ref),"
        f" fill_price=?, resolved_at=?, is_day_trade=? WHERE {where_col}=?",
        (p["status"], p.get("reject_reason"), p.get("broker_ref"), _f(p.get("fill_price")),
         resolved_at, 1 if p.get("is_day_trade") else 0, where_val),
    )
    if cur.rowcount == 0:
        conn.rollback()
        print(json.dumps({"ok": False, "error": f"no order row found for {where_col}={where_val}"}))
        sys.exit(2)

    if p.get("is_day_trade"):
        row = conn.execute(f"SELECT id, ticker, account FROM orders WHERE {where_col}=?", (where_val,)).fetchone()
        conn.execute(
            "INSERT INTO day_trades(ticker,trade_date,order_id,created_at,account) VALUES(?,?,?,?,?)",
            (row["ticker"], resolved_at[:10], row["id"], resolved_at, row["account"]),
        )
    conn.commit()
    print(json.dumps({"ok": True, where_col: where_val, "status": p["status"]}))


def cmd_stop_open(args):
    """Hand a new position to the stop watcher. Called by the execution agent
    right after an entry is placed -- before it fills, so a fill between
    cycles is still covered. Status starts 'pending_fill'; trail.py flips it
    to 'active' once Webull shows the position."""
    p = json.loads(args.json)
    conn = connect()
    conn.executescript(SCHEMA)
    instrument = p.get("instrument", "EQUITY").upper()
    if instrument == "OPTION":
        key = f"{p['ticker'].upper()}:{p['option_type'].upper()}:{float(p['strike']):g}:{p['expiry']}"
    else:
        key = p["ticker"].upper()
    ts = now()
    cur = conn.execute(
        "INSERT INTO stops(key,ticker,instrument,option_type,strike,expiry,quantity,entry_price,initial_stop,"
        "current_stop,high_water,fractional,entry_ref,stop_ref,proposal_id,status,opened_at,updated_at,account)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (key, p["ticker"].upper(), instrument, (p.get("option_type") or "").upper() or None, _f(p.get("strike")),
         p.get("expiry"), float(p["quantity"]), float(p["entry_price"]), float(p["stop_price"]),
         float(p["stop_price"]), float(p["entry_price"]), 1 if p.get("fractional") else 0,
         p.get("entry_ref"), p.get("stop_ref"), p.get("proposal_id"), "pending_fill", ts, ts,
         p.get("account", "margin")),
    )
    conn.commit()
    print(json.dumps({"ok": True, "id": cur.lastrowid, "key": key, "account": p.get("account", "margin")}))


def cmd_cycle_open(args):
    """Step 1 of a cycle: the row every other write this cycle hangs off of."""
    p = json.loads(args.json)
    conn = connect()
    existing = conn.execute("SELECT cycle_id FROM cycles WHERE cycle_id=?", (p["cycle_id"],)).fetchone()
    if existing:
        print(json.dumps({"ok": False, "error": f"cycle_id {p['cycle_id']} already exists — generate a fresh one"}))
        sys.exit(2)
    conn.execute(
        "INSERT INTO cycles(cycle_id,phase,started_at,status) VALUES(?,?,?,?)",
        (p["cycle_id"], p["phase"], now(), "running"),
    )
    conn.commit()
    print(json.dumps({"ok": True, "cycle_id": p["cycle_id"], "phase": p["phase"]}))


def cmd_cycle_close(args):
    """Step 9: close out the row opened at step 1, with the outcome and why."""
    p = json.loads(args.json)
    conn = connect()
    cur = conn.execute(
        "UPDATE cycles SET finished_at=?, status=?, summary=? WHERE cycle_id=?",
        (now(), p["status"], p.get("summary"), p["cycle_id"]),
    )
    conn.commit()
    if cur.rowcount == 0:
        print(json.dumps({"ok": False, "error": f"no cycle row for cycle_id {p['cycle_id']} — was cycle-open called?"}))
        sys.exit(2)
    print(json.dumps({"ok": True, "cycle_id": p["cycle_id"], "status": p["status"]}))


def cmd_source_status(args):
    p = json.loads(args.json)
    conn = connect()
    for s in p["sources"]:
        conn.execute("INSERT INTO source_status(cycle_id,source,status,detail,checked_at) VALUES(?,?,?,?,?)",
                     (p["cycle_id"], s["source"], s["status"], s.get("detail"), now()))
    conn.commit()
    degraded = [s for s in p["sources"] if s["status"] != "ok"]
    print(json.dumps({"ok": True, "recorded": len(p["sources"]), "degraded": degraded}))


def cmd_fingerprint(args):
    """Row counts plus an integrity check — the proof a round trip survived.

    The knowledge base now lives in the cloud for the duration of a cycle and is
    written back at the end. That copy-modify-copy-back shape has a failure mode
    the old single-file setup did not: a cycle that dies after modifying its
    copy, or a commit that silently does not land, leaves the database quietly
    reverted with no error anywhere. So the orchestrator fingerprints before
    and after and compares. Cheap, and it turns "I hope it saved" into "it
    saved, here are the numbers."
    """
    conn = connect()
    counts = {}
    for t in ("positions", "thesis_versions", "events", "levels", "catalysts",
              "alpha_reports", "alpha_calls", "orders", "decisions", "day_trades", "cycles", "stops"):
        counts[t] = conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    stale_cycles = [dict(r) for r in conn.execute(
        "SELECT cycle_id, phase, started_at FROM cycles WHERE finished_at IS NULL ORDER BY started_at")]
    print(json.dumps({
        "ok": integrity == "ok",
        "integrity": integrity,
        "counts": counts,
        "total_rows": sum(counts.values()),
        "unfinished_cycles": stale_cycles,
        "note": ("an unfinished cycle means a previous run died before closing out — "
                 "its writes may not have been committed back"
                 if stale_cycles else "no unfinished cycles"),
    }, indent=2))


def cmd_query(args):
    """Read-only escape hatch. Rejects anything that isn't a SELECT."""
    sql = args.sql.strip()
    if not sql.lower().startswith("select"):
        print(json.dumps({"ok": False, "error": "query accepts SELECT only; writes go through the named commands"}))
        sys.exit(2)
    conn = connect()
    rows = [dict(r) for r in conn.execute(sql)]
    print(json.dumps({"ok": True, "rows": rows, "count": len(rows)}, indent=2, default=str))


def main():
    ap = argparse.ArgumentParser(description="Trading agent knowledge base")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(func=cmd_init)

    for name, fn in [("sync-positions", cmd_sync_positions), ("set-thesis", cmd_set_thesis),
                     ("add-event", cmd_add_event), ("log-decision", cmd_log_decision),
                     ("source-status", cmd_source_status), ("cycle-open", cmd_cycle_open),
                     ("cycle-close", cmd_cycle_close), ("log-order", cmd_log_order),
                     ("resolve-order", cmd_resolve_order), ("stop-open", cmd_stop_open)]:
        p = sub.add_parser(name)
        p.add_argument("--json", required=True)
        p.set_defaults(func=fn)

    sub.add_parser("stale").set_defaults(func=cmd_stale)
    sub.add_parser("fingerprint").set_defaults(func=cmd_fingerprint)

    q = sub.add_parser("query")
    q.add_argument("--sql", required=True)
    q.set_defaults(func=cmd_query)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
