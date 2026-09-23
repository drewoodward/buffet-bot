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
- **`run-cycle.sh`'s `--allowedTools` has to actually grant Alpha Vantage.**
  Found 21 Sep 2026: the orchestrator's own required `MARKET_STATUS` call
  (see above) was being silently denied under `--permission-prompts none`
  because `mcp__claude_ai_Alpha_Vantage_MCP_Server__*` was never in the
  top-level allowlist — only the other connectors were. Every headless cycle
  since Alpha Vantage was wired in had likely been failing the venue-status
  check closed without anyone noticing, because a closed check just rejects
  proposals quietly rather than erroring loudly. If a future connector gets
  added to `agents/orchestrator.md` or a cycle prompt, it also has to be
  added to `run-cycle.sh`'s `--allowedTools` — granting a connector to the
  account is not the same as granting it to the unattended run.
- **The systemd user manager does not reliably inherit your shell's `PATH`.**
  Found 21 Sep 2026: a timer-triggered cycle failed instantly with `claude:
  command not found` (exit 127) even though `claude` resolves fine
  interactively. Fixed with an explicit `Environment=PATH=...` line in
  `systemd/buffet-bot@.service` — don't remove it as dead-looking
  boilerplate.
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
- **Webull's Open API `account_id` is not the account number shown in the
  app.** `get_account_list()` returns a real `account_id` (a long opaque
  string) alongside the familiar `account_number` (e.g. "5MS75613") — every
  other Webull call wants the former. `WEBULL_ACCOUNT_ID` was set to the
  account_number once, on 23 Sep 2026, and every call failed with `403
  ACCOUNT_ACCESS_DENIED` — a different error than a bad key/secret (401),
  which is the tell if this happens again. Fix: call `get_account_list()`
  once and use its `account_id`, not the number on the account statement.

## Where things are on millie

    ~/buffet-bot/          this repo
    ~/mcp/                 the MCP servers, one dir each
    ~/.tv-profile          chromium profile holding the TradingView login
    /srv/buffet-bot/       the one-way publish directory (see DASHBOARD.md)
    /home/openclaw/        the OpenClaw dashboard, a SEPARATE Linux user

OpenClaw runs as its own user, uid 1001, deliberately. It is an AI agent with
a published history of prompt-injection and credential-exfiltration issues, so
it gets no credentials, no write access to anything here, and cannot traverse
`/home/millie` at all. It reads `/srv/buffet-bot` and nothing else. Do not
"simplify" this by running it as millie or by loosening `/home/millie` from
`0700` — the isolation is the filesystem's job, not a configuration promise.

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
- A full premarket dry run on millie, twice: the first correctly hard-stopped
  at the portfolio refresh (Webull connector not yet wired into
  `--allowedTools`); the second, after that fix, ran the whole way through —
  real subagent fan-out, real chart captures, real Telegram delivery, both
  Alpha Report inboxes checked, `cycles` row opened and closed correctly.
- `execution-agent.md`'s tool line now has `mcp__claude_ai_Webull__*` added,
  for `live_instruction`'s `place_stock_instruction` (creates a confirmation
  link, never executes on its own).
- **systemd timers, installed and enabled on millie**: `premarket` (7:00am),
  `alpha-check` (9:10am — this is the Kevin's-Alpha-Report ingestion cycle;
  it already existed, it just wasn't turned on), `postclose` (4:30pm), all
  America/New_York, weekdays. `tradingview-cdp.service` enabled too, and
  `loginctl enable-linger` set so they survive logout. See `systemd/INSTALL.md`.
- **millie is now the primary host, as of 20 Sep 2026.** A full cycle has run
  clean on millie and `kb.sqlite` has been moved over deliberately — the two
  conditions this file used to say to wait for before touching mochi.
  README.md's "Where things run" and "Scheduled" sections describe the
  current, millie-only architecture; ignore anything elsewhere that still
  describes a cloud-container staging step or a Mac/OneDrive multi-host
  setup, both are pre-migration history now.

Not done:
- **Decommissioning mochi's scheduled tasks.** Andre asked for this on 20 Sep
  2026. Nothing in this toolset can reach it: `CronList`/`CronDelete` here
  only manage jobs created by *this* Claude Code session, not mochi's Claude
  Desktop scheduled tasks, and there's no SSH/API path into that machine's
  Desktop app from millie. This has to be done by Andre, directly on mochi,
  in Claude Desktop's own scheduled-tasks UI. Don't mark this done until
  confirmed, and don't assume it's done just because millie is running --
  the two hosts could otherwise both be running cycles unnoticed.
- **`live_direct` — built 23 Sep 2026. Reads confirmed against the real
  account; nothing that can place a trade has been tested at all.**
  `~/mcp/webull-mcp/webull_mcp.py`, on the official
  `webull-openapi-python-sdk` (not the unofficial scraping package -- that
  distinction matters, see DATA-SOURCES.md). Five tools: `place_order`,
  `cancel_order`, `get_order_status`, `get_account`, `get_positions`.
  `lib/kb.py` gained `log-order`/`resolve-order` to actually write the
  `OrderRecord`s execution-agent.md already said every order becomes.
  - **Sandbox does not authenticate with Andre's real app key/secret** --
    `api.sandbox.webull.com` returns 401 with the same credentials that work
    fine against `api.webull.com`, despite Webull's own getting-started docs
    showing the identical credentials used for both. Root cause not found;
    the plan of testing everything against sandbox before production
    couldn't happen as designed. If sandbox ever needs revisiting, this is
    where to pick it back up -- don't assume it's a credentials typo, that
    was already ruled out.
  - **`get_account()` and `get_positions()` were instead verified read-only
    against the real funded account** (23 Sep 2026): `get_account()`'s field
    mapping is now confirmed correct (net liq $271.48, matches the exact
    figure CLAUDE.md's `day_trades_left` bug story already referenced --
    this is the same account). `get_positions()` correctly returned `[]` --
    call confirmed working, but its per-position field mapping is still
    unverified since there were no real positions to check it against. Verify
    that mapping against a real position the first time one exists.
  - **`place_order`, `cancel_order`, `get_order_status` are completely
    unexercised** -- never called against sandbox (couldn't) or production
    (deliberately not risked). The very first call to `place_order` should
    be Andre, attended, one small deliberate order, watching it happen --
    not a strategy-driven proposal finding out these are untested.
- **Schwab MCP server** — still not connected. Unlike Webull, this really is
  a local server Andre is standing up; see DATA-SOURCES.md.
- **The dashboard.** Built and installed 22 Sep 2026 as an OpenClaw feature
  plugin (`/home/openclaw/plugins/buffet-dashboard/`) — a native Control UI
  page, not a dashboard widget (the widget sandbox's CSP blocks remote images
  and caps payloads too small for a growing report history). Reads
  `/srv/buffet-bot` directly; no tool: bindings, so it's UI-only and never
  reachable by the OpenClaw assistant itself. Reachable from Andre's phone
  over Tailscale (`https://millie.barreleye-city.ts.net/`), and the
  Cowork artifact still updates every cycle as a no-tailnet-required
  fallback. The Cowork artifact itself remains unverified as its own
  surface, independent of this.

## Execution safety

`config.json -> execution_mode` is `paper`. Under `paper` and
`live_instruction` there is no tool that can place an order — the human in the
loop is a property of the toolset.

`live_direct` (the local Webull Open API server, built 23 Sep 2026) changes
that to a config value. It carries two gates, both defaulting to on:
`require_confirmation_link` and `require_attended_session`. Turning either off
is a deliberate decision by Andre, never a convenience.

`mcp__webull__*` is never granted in `run-cycle.sh` — the three scheduled
cycles literally have no tool that can place a live order, matching how
`paper`/`live_instruction` already can't. It is granted in exactly one place,
`run-attended.sh`, which no systemd unit references; running it by hand *is*
what "attended" means. Do not add `mcp__webull__*` anywhere else, standing
allow or otherwise — that would turn "can't" back into "won't," which is the
one thing this whole design exists to avoid.

## How Andre wants to be talked to

Plain language, light on acronyms, define terms inline. He wants the mechanism
and the problem, not a summary. He will tell you when you are being too wordy.
