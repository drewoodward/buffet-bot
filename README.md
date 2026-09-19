# Trading agent

Multi-agent system: an orchestrator that owns the workflow, four specialised
subagents, a knowledge base that survives between runs, and a risk engine that
is not allowed to be talked out of anything.

## Layout

```
agents/          role definitions -- read at runtime and handed to each subagent
  orchestrator.md        the procedure; start here
  news-agent.md          scanning and the materiality rubric
  knowledge-base-agent.md  memory integrity, staleness, reconciliation
  technical-analyst.md   TradingView charting and signal structure
  execution-agent.md     order preparation and the approval gate
lib/
  kb.py            knowledge base: schema + the only writer
  risk.py          risk engine: deterministic pre-trade checks
  schemas.py       inter-agent handoff schemas + validator
  alpha.py         Alpha Report intake, archiving, call scoring
inbox/           drop Kevin's Alpha Report here
archive/alpha/   ingested reports, kept
charts/          TradingView captures
exports/         markdown mirror of the knowledge base
kb.sqlite        the durable state. Everything else is replaceable.
config.json      risk limits, execution mode, account id
DATA-SOURCES.md  which connector serves which data, and why
DASHBOARD.md     where reports are published and in what shape
```

## Running a cycle by hand

Ask Claude, in a conversation linked to a computer that has this folder:

> Run the pre-market cycle from the trading-agent folder.

or `postclose`, or `alpha` after dropping a report in an inbox. The orchestrator
procedure is identical to the scheduled path -- the schedule is just a clock.

## Where things run

Every cycle runs in the **cloud container**: the workspace is staged up, the work
happens there, and `kb.sqlite` is committed back and verified. There is no
second path and no dependency on the desktop's Linux shell, which is unreliable
on Windows (a known, open bug). One path that always runs beats a fast path plus
a fallback nobody exercises.

What still has to be local, and therefore still ties the scheduled cycles to
`mochi`: **TradingView, Telegram and Schwab run as MCP servers on that machine.**
Charting, alerting and options chains come from there. That is why the scheduled
tasks are bound to mochi, and it is the right place for them -- mochi is always on.

## Working from the Mac

The folder is in OneDrive, so it syncs. Everything in `lib/`, `agents/` and the
docs can be read and edited from any machine, and a cycle run from a Mac session
follows the identical procedure.

Two caveats:

1. **The three local MCP servers do not travel.** A Mac session has no
   TradingView, Telegram or Schwab until those are set up there too.
2. **Only mochi writes `kb.sqlite`.** OneDrive resolves a two-machine write by
   creating a "conflicted copy", which silently drops one side's work. Read it
   from anywhere; let the scheduled cycles on mochi own the writes. To change
   that, move the database off the synced folder first.

## Scheduled

- Pre-market, 7:00am Eastern, weekdays
- Post-close, 4:30pm Eastern, weekdays

Both are bound to `mochi`, because TradingView, Telegram and Schwab run there.

**Daylight saving:** the scheduler works in UTC, so these shift by an hour when
US clocks change on 1 November 2026. They become 6:00am and 3:30pm Eastern until
the crons are moved back an hour.

## Useful commands

```bash
python3 lib/kb.py stale                     # what is out of date
python3 lib/kb.py query --sql "SELECT ..."  # read-only; rejects writes
python3 lib/alpha.py scan                   # unprocessed reports in inbox/
python3 lib/alpha.py record                 # Kevin's running hit rate
python3 lib/risk.py '<proposal>' '<account>'  # exit 0 = approved
```

## Current state

Execution is **paper**: proposals are logged and scored against real prices,
nothing reaches Webull. Flip `config.json -> execution_mode` to `live` to switch
to Webull instructions, which still require confirmation in the Webull app.

Options analysis is **available but not yet switched on**: Schwab's local MCP
server now serves chains with greeks, implied volatility and open interest. Set
`config.json -> options_source` to `"schwab"` to enable it. The token needs
re-authenticating roughly weekly, by hand -- see DATA-SOURCES.md.
