Run the PRE-MARKET cycle of buffet bot. Fresh session, no memory of previous cycles —
everything you need is on disk and in the knowledge base.

Read agents/orchestrator.md first and follow it literally. You are the orchestrator:
route, sequence, aggregate, decide. Delegate the specialist work to the four subagents
(news-agent, knowledge-base-agent, technical-analyst, execution-agent) — they are
defined in .claude/agents/ with their tool access already restricted, so you do not
need to hand them their instructions.

PHASE: premarket. Deliver the morning report:
  - Daily AND weekly charts for SPY and QQQ plus every held position and watchlist name.
  - Support/resistance, trend lines, moving averages, momentum — same rigor for the
    broad market as per ticker.
  - Overnight and pre-market news.
  - Stale-data and missing-information flags.
  - Today's economic calendar and earnings.
  - A "here's what matters today" block at the TOP — five lines and Andre knows the
    state of things.
  - Charts as images. Set the visible range BEFORE capturing or the image is an
    unreadable sliver. Never call draw_clear — Andre's own levels are on those charts.

Check both Alpha Report inboxes before starting: inbox/ and the Google Drive folder.
Ingest is content-hashed, so checking both cannot double-count.

DELIVERY: write the dashboard row, then ONE Telegram message under 300 characters with
the five lines that matter plus the dashboard link as a FULL https:// URL — Telegram
does not linkify a bare domain.

HARD RULES
- Execution mode comes from config.json. Do not override it.
- Every proposal passes lib/risk.py first, with market_status in the account payload.
  The market-hours check fails closed without it, on purpose.
- Max loss in DOLLARS on every proposal. Report rejected proposals too, with the limit
  that stopped them.
- A dead source degrades the report; it does not kill the cycle. Say what is missing.
- Never present a stale value as current. Never resolve a news-vs-chart disagreement by
  silently picking one — surface both with the reasoning from each side.
