# buffet bot

A multi-agent trading analyst. Three cycles a day: it reads the market, reads
Kevin's Alpha Report, forms an independent view, and reports. Execution is
gated — see below.

Andre built this. You are picking up a migration in progress.

## Read these first

- `agents/orchestrator.md` — the cycle procedure. Follow it literally.
- `DATA-SOURCES.md` — which connector serves which data, and why.
- `DASHBOARD.md` — where reports go.
- `README.md` — layout and commands.

## The four boundaries — do not "helpfully" relax these

Each subagent in `.claude/agents/` has a `tools:` allowlist. The restrictions
are the design, not an oversight:

- **technical-analyst** has NO news tools. If it knew the narrative it would
  find chart evidence for it. Keeping it blind is what makes a disagreement
  between it and the News Agent real information rather than an echo.
- **knowledge-base-agent** has NO market data. It keeps records and forms no
  views, which is what makes it safe as the only writer — it has no thesis of
  its own to protect when reconciling a contradiction.
- **execution-agent** has no discretion over risk. It calls `lib/risk.py` and
  reports the verdict.
- **Only the orchestrator talks to Telegram.** No subagent has it.

## Bugs already found and fixed. Do not reintroduce them.

- **`market_status` is mandatory.** `lib/risk.py` fails its market-hours check
  closed without it. The check used to read the local clock, and two machines
  were found 81 minutes apart — at 08:17 ET, market shut, it reported
  "regular session, 09:38 ET" and PASSED. Fetch it from the venue, always.
- **Charts ship as PNG, never SVG.** An SVG in an `<img>` cannot see the host
  page's CSS. The first version referenced classes defined in the dashboard
  stylesheet and every shape fell back to SVG's default fill — solid black
  rectangles, no error. `lib/charts.py` embeds its own styles now.
- **Set the visible range before capturing.** Without it you get 120 sessions
  compressed into an unreadable sliver at the right edge.
- **`ma_overlays` takes PERIODS (50, 200), not prices.** Feeding them into the
  y-axis minimum pinned the floor at 50 and flattened the chart.
- **"close" and "avoid" calls score INVERTED.** Telling Andre to stay out of
  something that then rallied is a miss, not a win. Getting this backwards
  flatters the track record exactly where it matters.
- **Telegram links need `https://`.** A bare `claude.ai/...` arrives as dead
  grey text. Telegram does not linkify bare domains.
- **NEVER call `draw_clear`.** Andre's own hand-drawn levels are on that
  TradingView layout — 715 "THE PIVOT", 702.70 "THE FLOOR", and others. There
  is no undo through this interface. Draw in your own tab, or remove only the
  ids you created.

## Where things are on millie

    ~/buffet-bot/          this repo
    ~/mcp/                 the MCP servers, one dir each
    ~/.tv-profile          chromium profile holding the TradingView login

Chromium runs with `--remote-debugging-port=9222` and the chart open; the
TradingView MCP attaches over Chrome DevTools Protocol. It does not need the
TradingView desktop app — any Chromium serving the same web app works.

## Migration state (as of 20 Sep 2026)

Done:
- TradingView MCP working. `npm run tv -- status` reports connected.
- `.mcp.json`, `.claude/agents/`, `cycles/`, `systemd/` all committed.
- **Telegram MCP** copied from mochi, wired up, verified end-to-end (`getMe`,
  `getChat`, and a real `sendMessage` all confirmed).
- **Google Drive and Google Calendar** — turned out to need no migration at
  all. Both are `claude.ai` account-level connectors, not local MCP servers;
  they work headlessly on millie once `run-cycle.sh`'s `--allowedTools`
  grants them, which it now does. Verified live against the real Drive
  folder and calendar.
- **Webull, for data and for `live_instruction`** — same discovery as Drive
  and Calendar. There is no local `webull-mcp` server to copy from mochi and
  there never was one; Andre was only ever using the `claude.ai` Webull
  connector there too. `run-cycle.sh` and `news-agent.md` now point at
  `mcp__claude_ai_Webull__*` instead of the nonexistent local prefix. See
  DATA-SOURCES.md for the full routing.
- **`config.json`** exists, `execution_mode: paper` (unchanged default),
  `telegram_chat_id` filled in, `options_source: none` (Schwab isn't up).
  `account_id` is still a placeholder — deliberately: it isn't read by any
  code path yet, and Andre's real funded account with Open API access
  arrives Tuesday 22 Sep, at which point it gets filled in alongside the
  API key.
- **`kb.sqlite`** moved from mochi (holds Kevin's scored calls), integrity
  verified with `python3 lib/kb.py fingerprint`.
- A full premarket dry run on millie: health checks, both Alpha Report
  inboxes, and a real Telegram alert all confirmed working. The cycle
  correctly hard-stopped at the portfolio refresh, back when the Webull
  connector wasn't yet wired into `--allowedTools` — worth re-running now
  that it is.

Not done:
- **`live_direct` — the actual Webull Open API server.** This is the one
  genuinely-local, not-yet-built piece: real order placement, distinct from
  the `claude.ai` connector above. Blocked on Andre's account being funded
  (Tuesday 22 Sep) and an Open API key. `execution-agent.md` already has
  `mcp__webull__*` reserved for it in its tool allowlist; don't add that
  prefix to any *standing* allow anywhere else when it lands — CLAUDE.md's
  execution-safety section below explains why.
- **`execution-agent.md`'s tool line needs `mcp__claude_ai_Webull__*` added**
  (for `live_instruction`'s `place_stock_instruction`, which only creates a
  confirmation link, never executes). Attempted and blocked by the
  permission classifier as a sensitive grant — needs Andre to make this one
  edit by hand, or approve it directly.
- **Schwab MCP server** — still not connected. Unlike Webull, this really is
  a local server Andre is standing up; see DATA-SOURCES.md.
- **systemd timers** — see `systemd/INSTALL.md`.
- **The dashboard.** It is a Cowork artifact written with tools Claude Code may
  not have. Unverified. Telegram works regardless; if the artifact write fails,
  that reporting surface needs rethinking.

The old host (mochi, Claude Desktop + cloud scheduled tasks) is still running
and still authoritative. Do not decommission it until a full cycle has run on
millie and the database has been moved deliberately.

## Execution safety

`config.json -> execution_mode` is `paper`. Under `paper` and
`live_instruction` there is no tool that can place an order — the human in the
loop is a property of the toolset.

`live_direct` (the Webull Open API server, not yet built) changes that to a
config value. It carries two gates, both defaulting to on:
`require_confirmation_link` and `require_attended_session`. Turning either off
is a deliberate decision by Andre, never a convenience.

Do not add `mcp__webull__*` to a standing allow in `.claude/settings.json`.
The cycles get it scoped per-run in `run-cycle.sh`; that is where it belongs.

## How Andre wants to be talked to

Plain language, light on acronyms, define terms inline. He wants the mechanism
and the problem, not a summary. He will tell you when you are being too wordy.
