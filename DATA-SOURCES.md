# Where each piece of data comes from, and why

This routing is not arbitrary. It was rebuilt after testing every connector on
2026-09-18 and finding that two of them are on free tiers with hard caps.

## What the testing found

| Source | State | Consequence |
|---|---|---|
| Webull | `claude.ai` account connector, full access, no local setup | Primary for quotes, fundamentals, analyst data, filings, earnings, positions |
| TradingView | Live on mochi, CDP connected | Primary for all charting, indicators, levels |
| Alpha Vantage | **Free tier: 25 requests/day**, 1 req/sec | Demoted to macro-only, cached hard |
| Alpha Vantage options | **Premium endpoint — returns fake sample data** | No options chain source exists (see below) |
| Firecrawl | **Free MCP tier, already rate limited** | Not used |
| WebSearch (built-in) | Working | Primary news source |
| Telegram | Bot @finbud_buffet_bot resolves to "Woody" | Alert channel, text only, ~300 char |
| Google Drive | claude.ai account connector, no local setup needed | Second Alpha Report inbox, orchestrator-only |
| Google Calendar | claude.ai account connector, no local setup needed | Catalyst dates, orchestrator-only |

## Routing

**Price, bars, indicators, levels** -> TradingView MCP on mochi.
`data_get_ohlcv`, `data_get_indicator`, `chart_manage_indicator`, `capture_screenshot`.
Free, local, no quota. This is why the Technical Analyst never touches Alpha Vantage.

**Quotes and snapshots, positions, account balance** -> Webull, via
`mcp__claude_ai_Webull__*`: `get_stock_snapshot` (supports up to 100 symbols in
one call, and extended-hours flags for the pre-market cycle), `get_account_list`,
`get_account_positions`, `get_account_balance`. This is the same kind of
account-level `claude.ai` connector as Drive and Calendar -- there is no local
`webull-mcp` server to set up or copy from mochi for any of this, and there
never was; the read side of "Webull" has always been this connector, on
mochi and on millie alike.

**Fundamentals, analyst actions, filings, earnings** -> Webull, same connector:
`get_analyst_rating`, `get_analyst_target_price`, `get_stock_filings`,
`get_stock_earnings_calendar`, `get_financial_indicators`, `get_income_statement`.

**Order execution is a separate thing from all of the above**, and only
matters once `config.json -> execution_mode` leaves `paper`. See
`.claude/agents/execution-agent.md` for the three broker implementations:
`paper` calls no broker at all; `live_instruction` uses this same
`mcp__claude_ai_Webull__*` connector's `place_stock_instruction` (creates a
confirmation link, does not execute); `live_direct` is a genuinely separate,
not-yet-built local MCP server on Webull's Open API, gated on a funded account
and an API key. Don't conflate the two -- a working connector for reads and
`live_instruction` says nothing about whether `live_direct` exists yet.

**Second Alpha Report inbox** -> Google Drive, folder "Alpha Reports — trading
agent inbox" (id `1e0hVEcc4TUv95SXd2uqldLbNjELbnqWW`), via
`mcp__claude_ai_Google_Drive__search_files` / `download_file_content`. This is
a `claude.ai`-account-level connector, not a local MCP server like TradingView
or Telegram -- it needs no OAuth setup on millie and travels with the account
to any machine. The orchestrator checks it directly in step 2b; no subagent
touches Drive. See `agents/orchestrator.md`.

**Catalyst dates** -> Google Calendar, primary calendar `drewoodward@gmail.com`
(America/New_York), via `mcp__claude_ai_Google_Calendar__*`. Same
account-level connector as Drive, same reason it needs nothing local. Written
by the orchestrator ONLY, in step 8b, after the Knowledge Base Agent has
recorded the catalyst -- never by the News Agent directly, even though News is
what spots the date. That keeps the one-writer boundary intact: subagents stay
narrowly scoped, and only the orchestrator touches a surface that's visible to
Andre outside the dashboard and Telegram.

**News** -> WebSearch, one query per held ticker plus one macro sweep.
Not Alpha Vantage NEWS_SENTIMENT: at 25 requests/day, a five-ticker morning
sweep would spend a fifth of the daily budget on news alone.

**Macro series** (CPI, fed funds, treasury yields, unemployment, payrolls)
-> Alpha Vantage, and ONLY these. They update monthly, so the Knowledge Base
Agent caches each one with a 25-day staleness horizon and the system makes
roughly five Alpha Vantage calls a month instead of five a day.

**Options chains** -> nothing. No working source. See below.

## The options gap

Section 2 of the spec asks the Technical Analyst to pull the chain from Alpha
Vantage and factor in implied volatility, open interest and expiry positioning.
That is not currently possible:

- Alpha Vantage `REALTIME_OPTIONS` and `HISTORICAL_OPTIONS` are premium-only and
  return an artificial sample schema on this key.
- Webull's MCP can PLACE option orders (single-leg and 13 multi-leg strategies)
  but exposes no endpoint to READ a chain, greeks, IV or open interest.
- TradingView can chart an individual contract but serves no chain data here.
- yfinance was tested as a free fallback on both the cloud container and the
  device VM. Yahoo's host is blocked by the egress proxy on both. Not viable.

The system is therefore built equity-first, with the options path defined but
disabled. `config.json -> options_source` switches it on when a source exists.

## Schwab, once the local MCP server is up

Andre is standing the Schwab Trader API back up as the options chain source.
When `get_device_info` lists a `schwab` server under `localMcpServers`, set
`config.json -> options_source` to `"schwab"` and the Technical Analyst's
options section turns on.

**The authentication reality, stated plainly.** Schwab's refresh token expires
about every seven days, and renewing it is a browser login with Andre's
credentials plus a consent screen. A scheduled task CANNOT do that:

- Claude does not enter credentials into login forms. That is a hard rule, not
  a configuration choice, and no scheduled task changes it.
- So there is no version of this where the weekly re-auth happens unattended.
  Andre does the login; the system's job is to make sure he never discovers a
  dead token at 7am with a report half-built.

**What automation can do, and does:**

1. **Health check every cycle.** Schwab joins the source list the orchestrator
   probes at step 2. A dead or expiring token shows as `degraded`/`down` in the
   dashboard's source strip and in the report's flags, exactly like any other
   source. No special case.
2. **Warn before it lapses, not after.** When the token has less than 48 hours
   left, the cycle raises a high-severity flag and the Telegram ping leads with
   it. That turns a Monday-morning outage into a Saturday-afternoon errand.
3. **Never silently fall back.** If Schwab is down, options analysis is skipped
   and said so. It is never replaced with an estimate inferred from price action.

A separate weekly reminder task can be scheduled for Sunday evening, before the
week's first cycle. It reminds; it does not authenticate.
