Run the INTRADAY cycle of buffet bot. Fresh session, no memory of previous cycles —
everything you need is on disk and in the knowledge base.

Read agents/orchestrator.md first and follow it literally. You are the orchestrator:
route, sequence, aggregate, decide. Delegate the specialist work to the four subagents
(news-agent, knowledge-base-agent, technical-analyst, execution-agent) — they are
defined in .claude/agents/ with their tool access already restricted.

PHASE: intraday, 10:00 Eastern. THIS IS THE TRADING CYCLE. It is the only scheduled
cycle that runs inside regular market hours, so it is the only one whose proposals
can pass lib/risk.py's market_hours check. The 7:00 report and the 9:10 Alpha check
have already run — read today's premarket and alpha-check cycle rows and their
decisions from the knowledge base instead of redoing that work.

WHAT TO DO
  1. Open the cycle, health-check sources, refresh the portfolio (orchestrator steps
     1–3). Fetch Alpha Vantage MARKET_STATUS. If the venue says closed (holiday, or
     the host woke late and it is after 16:00 ET), record why and STOP — no proposals,
     no Telegram.
  2. Held positions first. lib/trail.py owns their stops (every 5 minutes, the
     `stops` table) -- do not move or duplicate them. Report each position's price
     against its current stop and targets, and flag any held position with NO row in
     `stops`: that one is unprotected and needs `kb.py stop-open`.
  3. Get a fresh technical-analyst read, on 30-minute and daily charts, of: every held
     ticker, the watchlist, SPY, QQQ, and any of Kevin's calls from today that are
     still open. The first half hour of trading has set the day's opening range — use
     it. Do not tell the analyst what Kevin or the news said.
  4. Turn buy/sell signals with an entry, stop and targets into TradeProposals, sized
     in fractional shares (orchestrator step 7). Strong directional reads on QQQ or
     SPY can go as options, same-day expiry included (orchestrator step 7). Every one
     goes through lib/risk.py, then to the execution-agent in whatever mode
     config.json says. Every entry placed is handed to lib/trail.py (kb.py
     stop-open), which manages the stop from then on.
  5. Log orders, fills and rejections as OrderRecords.

DELIVERY: publish locally (python3 lib/publish.py) and write the dashboard row. Send
ONE Telegram message under 300 characters ONLY if something happened — an order
placed, filled, awaiting confirmation, or rejected, or a stop hit. Give ticker, side,
size, price and max loss in dollars, plus the dashboard link as a FULL https:// URL.
If nothing happened, no Telegram: the morning report already covered the day.

HARD RULES
- Execution mode comes from config.json. Do not override it.
- Every proposal passes lib/risk.py first, with market_status in the account payload.
- Both accounts in config.json `accounts` (margin and cash), every cycle: refresh,
  size, risk-check and report each one separately (orchestrator.md steps 3 and 7).
  The market-hours check fails closed without it, on purpose.
- Max loss in DOLLARS on every proposal. Report rejected proposals too, with the limit
  that stopped them.
- A dead source degrades the report; it does not kill the cycle. Say what is missing.
  Webull down is the exception — no portfolio, no trading.
- Never call draw_clear.
