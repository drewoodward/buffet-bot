Run the POST-CLOSE cycle of buffet bot. Reconciliation and bookkeeping, not fresh ideas.

Read agents/orchestrator.md and follow it. Delegate to the subagents in .claude/agents/.

  1. Refresh the portfolio from Webull and sync it to the knowledge base.
  2. Reconcile the day: fills, rejections, expired orders -> OrderRecords. A fill that
     closed a same-day entry writes a day_trades row — that ledger is what keeps the
     pattern day trader count honest, since Webull's own day_trades_left field reported
     UNLIMITED on a $271 margin account and is not trusted.
  3. Run lib/kb.py stale. Report every bucket. Positions held with no written thesis are
     the most important finding — surface them, never invent a thesis to close the gap.
  4. Score any of Kevin's calls that reached their horizon, then lib/alpha.py record.
     A "close" or "avoid" call scores INVERTED: telling Andre to stay out of something
     that rallied is a miss, not a win.
  5. Sweep both Alpha Report inboxes for anything that arrived late.
  6. Re-derive key levels off today's close for held positions, so tomorrow's pre-market
     starts fresh. Set the visible range before any capture; never call draw_clear.
  7. Prune dashboard reports older than 90 days, keeping each month's last one.
  8. Log the day's decisions with their inputs.

DELIVERY: dashboard row, then Telegram under 300 characters — day P/L, anything filled,
staleness flags needing input, Kevin's running record if it changed. Full https:// URL.

Run lib/kb.py fingerprint at the start and again at the end; the counts must reflect
what you wrote. A cycle that reported results it did not save is worse than one that
failed.
