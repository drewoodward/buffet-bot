#!/usr/bin/env python3
"""
Alpha Report intake and scoring.

Two entry paths, because Andre asked for both: a file dropped in inbox/, and
text pasted into the chat. Both land in the same place through the same dedupe,
so dropping the file AND pasting it does not double-count Kevin's calls.

Parsing the report into structured alerts is the orchestrator's job -- that is
judgement over prose and it changes with Kevin's writing style. This module owns
storage, deduplication, archiving and outcome scoring, which are mechanical.
"""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DB_PATH = os.environ.get("TA_DB", os.path.join(ROOT, "kb.sqlite"))
INBOX = os.path.join(ROOT, "inbox")
ARCHIVE = os.path.join(ROOT, "archive", "alpha")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=TRUNCATE")
    return conn


def cmd_scan(args):
    """List unprocessed files in the inbox. Does not parse them."""
    if not os.path.isdir(INBOX):
        print(json.dumps({"ok": True, "files": []}))
        return
    conn = connect()
    seen = {r["sha256"] for r in conn.execute("SELECT sha256 FROM alpha_reports WHERE sha256 IS NOT NULL")}
    out = []
    for name in sorted(os.listdir(INBOX)):
        path = os.path.join(INBOX, name)
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        out.append({"file": name, "path": path, "sha256": digest,
                    "already_ingested": digest in seen,
                    "bytes": os.path.getsize(path)})
    print(json.dumps({"ok": True, "files": out}, indent=2))


def cmd_ingest(args):
    """Store a report. Accepts --file or --text. Idempotent on content hash."""
    if args.file:
        with open(args.file, "rb") as f:
            raw = f.read()
        text = raw.decode("utf-8", errors="replace")
        source_path = args.file
    elif args.text:
        text = args.text
        raw = text.encode()
        source_path = "(pasted in chat)"
    else:
        print(json.dumps({"ok": False, "error": "need --file or --text"}))
        sys.exit(2)

    digest = hashlib.sha256(raw).hexdigest()
    conn = connect()
    existing = conn.execute("SELECT id, report_date, received_at FROM alpha_reports WHERE sha256=?",
                            (digest,)).fetchone()
    if existing:
        print(json.dumps({"ok": True, "duplicate": True, "report_id": existing["id"],
                          "received_at": existing["received_at"],
                          "note": "identical report already ingested; calls not re-counted"}))
        return

    report_date = args.date or datetime.now(timezone.utc).date().isoformat()
    cur = conn.execute(
        "INSERT INTO alpha_reports(report_date,received_at,source_path,raw_text,market_commentary,sha256)"
        " VALUES(?,?,?,?,?,?)",
        (report_date, now(), source_path, text, args.commentary, digest))
    conn.commit()
    report_id = cur.lastrowid

    archived = None
    if args.file and os.path.isdir(ARCHIVE):
        base = f"{report_date}_{report_id}_{os.path.basename(args.file)}"
        archived = os.path.join(ARCHIVE, base)
        shutil.copy2(args.file, archived)

    print(json.dumps({"ok": True, "duplicate": False, "report_id": report_id,
                      "report_date": report_date, "archived_to": archived,
                      "chars": len(text)}, indent=2))


def cmd_add_calls(args):
    """Attach parsed AlphaAlert objects to a report."""
    payload = json.loads(args.json)
    report_id = payload["report_id"]
    conn = connect()
    if not conn.execute("SELECT 1 FROM alpha_reports WHERE id=?", (report_id,)).fetchone():
        print(json.dumps({"ok": False, "error": f"no report with id {report_id}"}))
        sys.exit(2)
    ids = []
    for c in payload["calls"]:
        cur = conn.execute(
            "INSERT INTO alpha_calls(report_id,ticker,direction,instrument,entry_hint,stop_hint,target_hint,"
            "horizon,raw_excerpt,our_verdict,our_reasoning,price_at_call,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (report_id, c["ticker"], c.get("direction"), c.get("instrument"), c.get("entry_hint"),
             c.get("stop_hint"), c.get("target_hint"), c.get("horizon"), c.get("raw_excerpt"),
             c.get("our_verdict"), c.get("our_reasoning"), c.get("price_at_call"), now()))
        ids.append(cur.lastrowid)
    conn.commit()
    print(json.dumps({"ok": True, "report_id": report_id, "call_ids": ids, "count": len(ids)}))


def cmd_score(args):
    """Record the outcome of one call at horizon."""
    p = json.loads(args.json)
    conn = connect()
    row = conn.execute("SELECT price_at_call, direction FROM alpha_calls WHERE id=?", (p["call_id"],)).fetchone()
    if not row:
        print(json.dumps({"ok": False, "error": "no such call"}))
        sys.exit(2)
    start, end = row["price_at_call"], float(p["price_at_horizon"])
    direction = row["direction"]
    pct = None
    outcome = p.get("outcome")
    if start:
        move = (end - start) / start * 100

        # The sign depends on what the call actually asked you to DO, which is
        # not the same as which way the price went. A "long" is rewarded by a
        # rise. A "short" is rewarded by a fall. So is a "close" or "avoid":
        # telling you to stay out of something that then rallied is a miss, not
        # a win, and scoring it as a win would flatter the track record exactly
        # where it matters most.
        if direction in ("short", "close"):
            pct = round(-move, 2)
        elif direction == "long":
            pct = round(move, 2)
        else:
            # "hold" and anything unrecognised have no directional claim to score.
            pct = round(move, 2)
            if outcome is None:
                outcome = "unresolved"

        if outcome is None:
            outcome = "win" if pct > 1 else ("loss" if pct < -1 else "flat")
    conn.execute("UPDATE alpha_calls SET price_at_horizon=?, outcome=?, outcome_pct=?, scored_at=? WHERE id=?",
                 (end, outcome, pct, now(), p["call_id"]))
    conn.commit()
    print(json.dumps({"ok": True, "call_id": p["call_id"], "outcome": outcome, "outcome_pct": pct}))


def cmd_record(args):
    """Kevin's scored track record. The reason any of this is worth storing."""
    conn = connect()
    rows = list(conn.execute("""
        SELECT outcome, COUNT(*) n, ROUND(AVG(outcome_pct),2) avg_pct
        FROM alpha_calls WHERE outcome IS NOT NULL GROUP BY outcome
    """))
    total = sum(r["n"] for r in rows)
    wins = sum(r["n"] for r in rows if r["outcome"] == "win")
    unscored = conn.execute("SELECT COUNT(*) c FROM alpha_calls WHERE outcome IS NULL").fetchone()["c"]
    by_ticker = [dict(r) for r in conn.execute("""
        SELECT ticker, COUNT(*) n, ROUND(AVG(outcome_pct),2) avg_pct
        FROM alpha_calls WHERE outcome IS NOT NULL GROUP BY ticker ORDER BY n DESC LIMIT 10
    """)]
    agreement = [dict(r) for r in conn.execute("""
        SELECT our_verdict, outcome, COUNT(*) n FROM alpha_calls
        WHERE outcome IS NOT NULL AND our_verdict IS NOT NULL
        GROUP BY our_verdict, outcome
    """)]
    print(json.dumps({
        "ok": True,
        "scored_calls": total,
        "hit_rate_pct": round(wins / total * 100, 1) if total else None,
        "by_outcome": [dict(r) for r in rows],
        "by_ticker": by_ticker,
        "our_verdict_vs_outcome": agreement,
        "unscored": unscored,
        "note": "hit_rate needs a few dozen calls before it means anything",
    }, indent=2))


def main():
    ap = argparse.ArgumentParser(description="Alpha Report intake and scoring")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan").set_defaults(func=cmd_scan)

    i = sub.add_parser("ingest")
    i.add_argument("--file")
    i.add_argument("--text")
    i.add_argument("--date")
    i.add_argument("--commentary")
    i.set_defaults(func=cmd_ingest)

    for name, fn in [("add-calls", cmd_add_calls), ("score", cmd_score)]:
        p = sub.add_parser(name)
        p.add_argument("--json", required=True)
        p.set_defaults(func=fn)

    sub.add_parser("record").set_defaults(func=cmd_record)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
