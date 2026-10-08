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
- **Only the orchestrator talks to Telegram.** No subagent has it. (The
  exception is `lib/trail.py`, which is a plain script, not an agent: it
  messages when it moves a stop or sells. The other is `chat/telegram_chat.py`,
  the two-way chat, which sends Claude's replies itself; see "Telegram chat"
  below.)

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
  account_number twice on 23 Sep 2026 -- once for production, then again
  independently for sandbox once that got its own credentials -- and both
  times every call failed with `403 ACCOUNT_ACCESS_DENIED`, a different
  error than a bad key/secret (401), which is the tell if this happens
  again. This is a mistake worth expecting to make again in any *new*
  environment, not something that's "fixed" once: fix is to call
  `get_account_list()` once per environment and use its `account_id`, not
  the number on the account statement. `get_account_list()` also lists
  every account tied to the app key (Margin, Cash, Futures, Crypto, ...) --
  the Individual Margin one is the one this project trades on.

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
- **`live_direct` — built 23 Sep 2026, sandbox fully working, production
  reads confirmed, production writes still untested.**
  `~/mcp/webull-mcp/webull_mcp.py`, on the official
  `webull-openapi-python-sdk` (not the unofficial scraping package -- that
  distinction matters, see DATA-SOURCES.md). Five tools: `place_order`,
  `cancel_order`, `get_order_status`, `get_account`, `get_positions`.
  `lib/kb.py` gained `log-order`/`resolve-order` to actually write the
  `OrderRecord`s execution-agent.md already said every order becomes.
  - **Sandbox needs its own, separately-issued app key/secret/account_id --
    it does NOT share credentials with production**, despite Webull's own
    getting-started docs implying otherwise by reusing the same placeholder
    names in both examples. Andre generated dedicated sandbox credentials and
    it authenticates cleanly. Both sets live in `.env`; the production ones
    are commented out rather than deleted, so switching back is a one-line
    change plus flipping `WEBULL_ENV`.
  - **`get_account()`, `place_order`, `cancel_order`, `get_order_status` are
    all confirmed working end-to-end against sandbox** (23 Sep 2026): placed
    a real sandbox order, read its status (`SUBMITTED`), cancelled it, and
    confirmed the cancellation landed (`CANCELLED`). `get_account()`'s field
    mapping (buying power lives nested in `account_currency_assets[N]`, not
    top-level) was confirmed against both sandbox ($1M/$4M test balances) and
    production ($271.48 -- the same figure the `day_trades_left` bug story
    below already referenced). `place_order` also surfaced a real broker-side
    check worth knowing about: an order priced too far from market comes back
    `417 OPENAPI_ORDER_RISK_RULE_PRICE_AGGRESSIVE`, which `_call()` (see
    below) turns into a clean `ToolError` rather than a crash.
  - **`get_positions()`'s per-position field mapping is still unverified.**
    The call itself works (empty list, no error, in both sandbox and
    production), but no order actually filled in testing -- sandbox orders at
    two different reasonable prices both sat `SUBMITTED` rather than matching,
    so this remains a real gap. Verify it against a real position the first
    time one exists, the same way `get_account()`'s mapping just got confirmed.
  - **Bug fixed while testing:** `_unwrap` (now `_call`) originally only
    handled the "SDK returns a Response with a bad status code" error path.
    `place_order`'s risk-rule rejection instead raises
    `ServerException`/`ClientException` directly from inside the SDK call --
    uncaught, that's not just a failed tool call on an MCP stdio server, it
    can take the whole server process down. `_call()` now wraps the SDK
    invocation itself, not just its result, and catches both paths into one
    clean `ToolError`.
  - **Never tested against production: `place_order`, `cancel_order`,
    `get_order_status`.** Deliberately not risked once sandbox started
    working properly -- there was no need to. The very first call to
    `place_order` against the *real* funded account should still be Andre,
    attended, one small deliberate order, watching it happen -- not a
    strategy-driven proposal finding out production behaves differently from
    sandbox.
- **Stops, trailing stops, options, fractional shares — built 29 Sep 2026,
  sandbox-read-tested only.** The broker code moved into
  `~/mcp/webull-mcp/webull_broker.py`, shared by the MCP server (tools now:
  `buy_stock`, `sell_stock`, `buy_option`, `sell_option`,
  `find_option_contracts`, `get_option_quote`, `cancel_order`,
  `get_order_status`, `get_open_orders`, `get_account`, `get_positions`) and
  by `lib/trail.py`, the stop watcher (`systemd/buffet-bot-watch.*`, every 5
  min in market hours; NOT yet installed — Andre enables it). Webull rules
  that shaped it, from developer.webull.com: fractional = MARKET only and
  under 1 share, so no broker-side stop is possible on a fractional position
  and trail.py sells those itself; options = LIMIT/stop orders only, sell
  side DAY only, no trailing type, so trail.py re-places option stops daily
  and trails by cancel-and-replace; stock trailing type is DAY only, so
  trail.py trails a GTC stop with `replace_order` instead. Entries attach the
  stop as a MASTER + STOP_LOSS combo. trail.py only ever sells, and only acts
  in `live_direct`. Its logic was tested against a fake broker (fills, trail
  to breakeven, lapsed stop re-placed, fractional stop-out, option trail,
  expiry-day close, unfilled-exit re-price); **no order through the new code
  has reached even sandbox yet** — sandbox refuses orders outside market
  hours (`OPENAPI_CAN_NOT_TRADING_FOR_FIXGW_NOT_READY`). Test there first,
  in market hours. Production market data (stock AND option quotes) returns
  `403 MARKET_DATA_NOT_SUBSCRIBED` — option contract lists work, prices
  don't, until Andre subscribes. Held positions' `last_price` comes from the
  positions endpoint, which needs no subscription (field name from Webull's
  own MCP server; unconfirmed on this account).
  - **Sandbox order test, 30 Sep 2026 (GO-LIVE-PLAN step A), in market
    hours: passed.** Run late (15:18–15:30 ET, not 09:35) and after the 10:00
    cycle, as the plan allows.
    - Whole-share buy + attached stop (`buy_stock('QQQ', 1, 743.55,
      stop_price=728.63)`): Webull accepted the combo. The buy filled at once,
      and the GTC STOP_LOSS leg showed SUBMITTED. `open_orders()` entries do
      carry `client_order_id` at the top level. The list is combos, each with
      a nested `orders[]`, and a **filled MASTER stays in the list while its
      stop leg is still working**. trail.py only checks membership, so that's
      harmless.
    - `move_stock_stop` after the fill worked (728.63 → 730.00) via
      `replace_order`. It was not tried before the fill, because the buy
      filled instantly.
    - `positions()` field names confirmed for stock (`symbol`, `quantity`,
      `last_price`, `cost_price`, `instrument_type`) **and for options** (a
      single `legs[]` entry with `option_type`, `option_expire_date`,
      `option_exercise_price`; no `strike_price`, so the fallback is what
      gets used). No code change needed.
    - Fractional: 0.02 QQQ market buy filled (~$14.87), market sell filled.
    - Option: `option_contracts` + `option_quote` work on sandbox, but
      **sandbox option quotes are 15 minutes delayed** (`delay_minutes: 15`).
      A limit at that stale ask sat unfilled. Cancelling the MASTER
      also cancelled its STOP_LOSS leg. Re-sent at 1.80 limit, it filled at
      1.30, and its DAY stop leg went live.
    - trail.py `--dry-run --force-hours` against a copy of kb.sqlite with a
      `stop-open` row for that option: flipped it to active, and with
      `high_water` set to 1.95 it planned "cancel stop, trail 0.9 → 1.46".
    - **Bug found and fixed:** a sell placed straight after cancelling the
      stop covering the same shares is refused with
      `OPENAPI_ORDER_NOT_SUPPORT_REVERSE_OPTION` ("would reverse an existing
      position"), because the cancel hasn't settled yet. That hit trail.py's
      `exit_now` (expiry-day close) and its option trail (cancel → re-place,
      which would have left the option with no stop until the next run).
      `Broker.cancel()` now waits (up to 10 s) until Webull reports the order
      CANCELLED/FILLED/etc. before it returns. Re-tested on sandbox: cancel
      settled in about 1 s, then re-placing the option stop and the exit sell
      both went through straight away.
    - Ended flat: no positions, no open orders.
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

**Current state (30 Sep 2026): fully autonomous live trading.** Andre chose
this on 29 Sep 2026 (GO-LIVE-PLAN.md) and it was applied on 30 Sep, after the
sandbox test and a production order that didn't fill (1 F @ 11.65 limit,
placed then cancelled) both passed. `execution_mode` is `live_direct`, and
`require_confirmation_link` and `require_attended_session` are both `false`.
`run-cycle.sh` grants `mcp__webull__*`, so every scheduled cycle can place
real orders with no human confirmation. The stop watcher
(`buffet-bot-watch.timer`) is installed and running. What stands between a
cycle and a real order now is `lib/risk.py` (per-trade/per-ticker caps, daily
loss halt, PDT) and the stop on every entry. Keep both intact. To go back to
gated trading, reverse those four changes. Production quirk: Webull answered
0.5 s status polling with 429 TOO_MANY_REQUESTS, so keep API calls spaced out.

The text below is the gated design as it stood before 30 Sep, kept as
history.

`config.json -> execution_mode` was `paper`. Under `paper` and
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

## Telegram chat (6 Oct 2026)

Andre can text the bot. `chat/telegram_chat.py` (`buffet-bot-chat.service`,
always on) long-polls the same Telegram bot. It drops any message that isn't
from `TELEGRAM_CHAT_ID`, and runs `claude -p` here for each message, with
`run-cycle.sh`'s tool grants (minus `mcp__telegram__*`) and `chat/CHAT.md`
appended as system prompt. The conversation carries over via `--resume`; it
resets on `/new` or after 6 h idle. Edits to CHAT.md only reach a fresh
conversation. Transcripts are in `logs/chat/`.

Orders, chosen by Andre on 6 Oct 2026. If he texts an order, it is placed with
no confirmation. If the bot proposes an order, it goes to `chat/pending.json`
and is placed only on his "yes" within 15 min. Both still go through
`lib/risk.py`. An order message more than 10 min old is not executed. The
listener advances the Telegram offset before handling a message, so a crash
can't replay an order. Nothing else may call `getUpdates` on this bot token.

## Two accounts (7 Oct 2026)

Andre added an Individual Cash account (CVY4GAV6) and wants every cycle to
trade it alongside Individual Margin (5MS75613), at riskier limits, with
options once its funds settle. `config.json -> accounts` lists both. Each
has its own `trading_enabled` flag and a `risk` block that overrides the
shared one. The Open API ids are in `.env`: `WEBULL_ACCOUNT_ID` (margin) and
`WEBULL_ACCOUNT_ID_CASH`.

- Every `mcp__webull__*` tool takes `account` (default `margin`).
  `webull_broker.Broker(name)` reads `WEBULL_ACCOUNT_ID_<NAME>`. The
  claude.ai Webull connector only sees margin.
- `orders`, `stops`, `day_trades` and `positions` have an `account` column.
  `kb.py` migrated the old rows to `margin` on first connect.
  `sync-positions` takes `{"account", "positions"}` and replaces only that
  account's rows.
- risk.py: the pattern day trader rule is margin-only. A cash account's entries are
  capped at settled cash, to avoid good-faith violations. Entries on an
  account with `trading_enabled: false` are rejected; exits are allowed.
- trail.py runs one pass per account. One account failing doesn't stop the
  other.
- A cash account's balance has `buying_power` + `settled_cash`, not
  `day_buying_power` (confirmed 7 Oct 2026). Fractional orders, stops and
  option orders have not been tried on the cash account yet.

## How Andre wants to be talked to

Plain language, light on acronyms, define terms inline. He wants the mechanism
and the problem, not a summary. He will tell you when you are being too wordy.
