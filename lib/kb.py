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
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);

-- Snapshot of the real portfolio, refreshed from Webull at the top of every cycle.
CREATE TABLE IF NOT EXISTS positions (
  ticker TEXT PRIMARY KEY,
  quantity REAL NOT NULL,
  cost_basis REAL,
  market_value REAL,
  unrealized_pl REAL,
  asset_type TEXT DEFAULT 'EQUITY',
  opened_at TEXT,
  synced_at TEXT NOT NULL
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
  created_at TEXT NOT NULL
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
  created_at TEXT NOT NULL
);

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
    """Replace the position snapshot wholesale. Payload is the Webull positions array."""
    payload = json.loads(args.json)
    conn = connect()
    ts = now()
    conn.execute("DELETE FROM positions")
    for p in payload:
        conn.execute(
            "INSERT INTO positions(ticker,quantity,cost_basis,market_value,unrealized_pl,asset_type,opened_at,synced_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (p.get("ticker"), float(p.get("quantity", 0)), _f(p.get("cost_basis")), _f(p.get("market_value")),
             _f(p.get("unrealized_pl")), p.get("asset_type", "EQUITY"), p.get("opened_at"), ts),
        )
    conn.commit()
    print(json.dumps({"ok": True, "count": len(payload), "synced_at": ts}))


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
        SELECT p.ticker FROM positions p
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
              "alpha_reports", "alpha_calls", "orders", "decisions", "day_trades", "cycles"):
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
                     ("source-status", cmd_source_status)]:
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
