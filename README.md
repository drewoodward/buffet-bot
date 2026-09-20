# Trading agent

Multi-agent system: an orchestrator that owns the workflow, four specialised
subagents, a knowledge base that survives between runs, and a risk engine that
is not allowed to be talked out of anything.

## Setup

A complete walkthrough from a fresh clone to a working scheduled cycle. Follow
it in order — later steps assume earlier ones are done. It's written for a
Linux host with `systemd` (this is what millie runs); if you're on the Mac,
skip the `systemd` step and see "Working from the Mac" below instead.

### 0. Prerequisites

- `git`, `python3` (3.10+), `node` (18+), and a Chromium-family browser
- A Claude Code login. Some of the pieces below are **account-level
  connectors** (Google Drive, Google Calendar, Webull, Alpha Vantage) rather
  than anything this repo installs — they belong to whichever `claude.ai`
  account you're signed into, not to this machine, and they carry over
  automatically to any computer signed into that same account.

### 1. Clone the repo

```bash
git clone https://github.com/drewoodward/buffet-bot.git ~/buffet-bot
cd ~/buffet-bot
```

### 2. Confirm the account-level connectors

Google Drive (the second Alpha Report inbox), Google Calendar (catalyst
dates), Webull (quotes, fundamentals, positions, and `live_instruction`
order confirmations), and Alpha Vantage (macro series, market-hours status)
are all `claude.ai` account connectors. There is nothing to install or copy
between machines for these — no local server, no OAuth flow on this host.
Check what's already connected:

```bash
claude mcp list
```

You want to see, connected:

```
claude.ai Google Drive
claude.ai Google Calendar
claude.ai Webull
claude.ai Alpha Vantage MCP Server
```

If any show as not connected, they're managed from your Claude account
itself (check its Connectors/Integrations settings), not from this repo.
`claude mcp login "<name>"` may also work to (re)authenticate one — see
`claude mcp login --help`.

**Do not confuse this with a local MCP server.** `mcp__claude_ai_Webull__*`
(this connector) and `mcp__webull__*` (a genuinely local server, not yet
built — see step 7 and CLAUDE.md's execution-safety section) are two
different things with similarly-named tools. Mixing them up is exactly the
bug this project shipped once already.

### 3. TradingView MCP server (local, per machine)

This is a real local server — clone and build it:

```bash
git clone https://github.com/tradesdontlie/tradingview-mcp.git ~/mcp/tradingview-mcp
cd ~/mcp/tradingview-mcp
npm install
```

`.mcp.json` in this repo already points at
`~/mcp/tradingview-mcp/src/server.js`. If you installed somewhere else, edit
that path.

TradingView needs to be reachable over Chrome DevTools Protocol on port
9222. This project uses a plain Chromium browser pointed at the TradingView
**web app**, not the TradingView desktop app — that's what lets it run
headless on a Linux server. Log in once, interactively (this step can't be
scripted):

```bash
mkdir -p ~/.tv-profile
which chromium chromium-browser   # confirm the binary name on your distro
chromium --remote-debugging-port=9222 --remote-allow-origins=* \
  --user-data-dir=~/.tv-profile --no-first-run --no-default-browser-check \
  https://www.tradingview.com/chart/
```

Log into TradingView in that window so the chart loads your saved layout and
any levels you've drawn. Once logged in, close this manual instance —
`systemd/tradingview-cdp.service` (step 8) reuses the same profile
(`~/.tv-profile`) and stays logged in from here on.

Verify: `cd ~/mcp/tradingview-mcp && npm run tv -- status` should report
`cdp_connected: true`.

### 4. Telegram MCP server (local, per machine)

Create a bot via [@BotFather](https://t.me/BotFather) in Telegram (`/newbot`,
follow the prompts) and note the token it gives you — or, if you're moving
this project to a new machine and already have a bot, reuse that same
token instead of creating a new one.

Get your chat id: message [@userinfobot](https://t.me/userinfobot) and it
replies with your numeric id, or message your own new bot once and read the
`chat.id` field from `https://api.telegram.org/bot<TOKEN>/getUpdates`.

Materialize the server — this is a small, self-contained script, reproduced
here in full so a fresh machine never needs to copy it from anywhere else:

```bash
mkdir -p ~/mcp/telegram-mcp
cat > ~/mcp/telegram-mcp/telegram_mcp.py <<'PYEOF'
"""
telegram_mcp.py — a tiny local MCP server that lets Claude send you Telegram messages.

Works with both mcp 1.x and mcp 2.x (the class was renamed FastMCP -> MCPServer in 2.0).

Environment variables:
  TELEGRAM_BOT_TOKEN   (required)  the token BotFather gave you
  TELEGRAM_CHAT_ID     (required)  the default chat to send to (your own user id)
  TELEGRAM_API_BASE    (optional)  override the API host; only used for testing

Run it by hand to check it starts:   python telegram_mcp.py
It will sit there silently waiting for JSON-RPC on stdin. That is correct. Ctrl+C to quit.
"""

import os
import sys

import httpx

try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError
except ModuleNotFoundError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server
    try:
        from mcp.server.fastmcp.exceptions import ToolError
    except ModuleNotFoundError:  # very old 1.x
        class ToolError(RuntimeError):
            pass

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
DEFAULT_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
API_BASE = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")

if not TOKEN:
    print("TELEGRAM_BOT_TOKEN is not set", file=sys.stderr)
    sys.exit(1)

server = _Server(
    "telegram",
    instructions=(
        "Sends push notifications to the user's phone via a private Telegram bot. "
        "Use send_telegram_message when the user has walked away and something is "
        "worth interrupting them for: a long job finished, a price alert fired, a "
        "decision is needed. Keep messages short and lead with the actionable part."
    ),
)


def _call(method: str, payload: dict) -> dict:
    """POST to the Telegram Bot API and return the parsed JSON body."""
    url = f"{API_BASE}/bot{TOKEN}/{method}"
    try:
        r = httpx.post(url, json=payload, timeout=20.0)
    except httpx.HTTPError as e:
        raise ToolError(f"could not reach Telegram: {e}") from e
    try:
        body = r.json()
    except ValueError:
        raise ToolError(f"Telegram returned non-JSON (HTTP {r.status_code}): {r.text[:200]}")
    if not body.get("ok"):
        raise ToolError(
            f"Telegram rejected the request (HTTP {r.status_code}): "
            f"{body.get('description', 'no description')}"
        )
    return body["result"]


@server.tool()
def send_telegram_message(message: str, chat_id: str | None = None) -> str:
    """Send a plain-text notification to the user's Telegram.

    Args:
        message: The text to send. Keep it under ~300 characters; phones truncate.
        chat_id: Optional override. Leave empty to use the configured default chat.
    """
    target = (chat_id or DEFAULT_CHAT_ID).strip()
    if not target:
        raise ToolError("no chat_id given and TELEGRAM_CHAT_ID is not set")
    if not message.strip():
        raise ToolError("message is empty")

    result = _call(
        "sendMessage",
        {
            "chat_id": target,
            "text": message,
            "disable_web_page_preview": True,
        },
    )
    return f"sent (message_id {result.get('message_id')}) to chat {target}"


@server.tool()
def telegram_check() -> str:
    """Verify the bot token works and report the bot's name and the configured chat.

    Call this once after setup, or when sending starts failing, to tell a bad token
    apart from a bad chat id.
    """
    me = _call("getMe", {})
    lines = [
        f"bot: @{me.get('username')} ({me.get('first_name')})",
        f"default chat id: {DEFAULT_CHAT_ID or '(not set)'}",
    ]
    if DEFAULT_CHAT_ID:
        try:
            chat = _call("getChat", {"chat_id": DEFAULT_CHAT_ID})
            who = chat.get("username") or chat.get("title") or chat.get("first_name")
            lines.append(f"default chat resolves to: {who} (type: {chat.get('type')})")
        except ToolError as e:
            lines.append(f"default chat could NOT be resolved: {e}")
    return "\n".join(lines)


if __name__ == "__main__":
    server.run(transport="stdio")
PYEOF
```

Install its dependencies:

```bash
python3 -c "import httpx" 2>/dev/null || echo "need httpx"   # most distros already have it
python3 -c "import mcp" 2>/dev/null || echo "need mcp"
```

If either is missing: on Fedora, `sudo dnf install python3-mcp` (and
`python3-httpx` if needed) — packaged, no `pip` required. On a distro
without that package, `pip install mcp httpx` (installing `pip` itself
first via your package manager, if `python3 -m pip --version` fails).

### 5. Fill in secrets: `.env` and `config.json`

```bash
cp .env.example .env
cp config.example.json config.json
```

Edit `.env`:

```
TELEGRAM_BOT_TOKEN="123456789:AA...rest-of-the-token-no-spaces-anywhere"
TELEGRAM_CHAT_ID="6801131217"
```

**Quote both values, and make sure the token has no spaces in it anywhere**
— copy it as one unbroken string. An unquoted value with a stray space
silently breaks: bash treats everything after the space as a separate
command instead of part of the value, the variable ends up empty, and
nothing tells you why. Quoting turns that failure mode into an obviously
wrong-looking token instead of a silent one.

Edit `config.json`:

- `telegram_chat_id` — same value as `.env`
- `account_id` — your Webull account id, if you have one to hand. Nothing
  in `lib/` reads this yet, so it's safe to leave the placeholder until you
  do.
- `execution_mode` — leave as `"paper"`. This is the safe default: under
  `paper`, no tool exists that can place a real order, at all. See
  "Execution safety" in CLAUDE.md before ever changing it.
- `options_source` — leave as `"none"` until Schwab's local MCP server
  exists (see DATA-SOURCES.md — it's genuinely not built yet, unlike
  Webull above).

### 6. The knowledge base

```bash
python3 lib/kb.py init
```

This creates an empty `kb.sqlite`. If you're migrating from a machine that
already has one — with Kevin's scored Alpha Report history, existing
theses, positions — copy that file here instead of running `init` (see
"Moving files from another machine" below).

Verify either way: `python3 lib/kb.py fingerprint` should report
`"integrity": "ok"`.

### 7. Make `run-cycle.sh` executable

```bash
chmod +x run-cycle.sh
```

Git preserves the executable bit on a normal clone, but a zip download or
some Windows checkouts can lose it. If `./run-cycle.sh premarket` fails with
"Permission denied," this is why.

### 8. Try a cycle by hand

```bash
./run-cycle.sh premarket
```

Watch it in `logs/$(date -u +%Y-%m-%d)_premarket.jsonl`. On a first run,
expect Schwab (not built) and Webull's `live_direct` order execution (not
built — see below) to show up as gaps in the report, not failures of
anything you just set up. Telegram, TradingView, and the Google/Alpha
Vantage connectors should all report `ok`.

### 9. Put it on a schedule

Full detail in `systemd/INSTALL.md`. Short version:

```bash
mkdir -p ~/.config/systemd/user
cp systemd/* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now tradingview-cdp.service
systemctl --user enable --now buffet-bot-premarket.timer
systemctl --user enable --now buffet-bot-alpha.timer
systemctl --user enable --now buffet-bot-postclose.timer
loginctl enable-linger $USER   # user services otherwise die at logout
```

### 10. Moving files from another machine

If you're migrating `kb.sqlite`, a working `telegram_mcp.py`, or anything
else from a machine that already has it, and both machines are on the same
Tailscale network, Taildrop is the simplest path:

```bash
# on the OLD machine
tailscale file cp kb.sqlite <new-machine-name>:

# on the NEW machine, once
sudo tailscale set --operator=$USER

# on the NEW machine, each time you're expecting a file
tailscale file get --wait ~/buffet-bot/
```

### 11. Pushing changes back to GitHub

GitHub no longer accepts a password for `git push`. Create a personal
access token first (github.com/settings/tokens, `repo` scope) and use it as
the password when git prompts for one — or set up SSH keys instead, if
you'd rather not manage a token. A 403 ("Permission ... denied") after
authenticating successfully usually means the token itself is missing the
`repo` scope or (for a fine-grained token) wasn't granted access to this
specific repository — it's not a sign the password/token is wrong.

### What's still manual, on purpose

- **Schwab's OAuth re-login, roughly weekly.** Claude Code will never enter
  credentials into a login form — there is no version of this that
  auto-renews. See DATA-SOURCES.md.
- **Flipping `execution_mode` out of `paper`.** A deliberate decision by
  Andre, not a setup step — see "Execution safety" in CLAUDE.md.
- **The dashboard artifact.** Unverified as of this writing — it may need
  tools Claude Code doesn't have. Telegram is the reporting path that's
  proven to work regardless of whether the dashboard does.
- **`live_direct` (real order placement via Webull's Open API).** Genuinely
  not built. Needs a funded account and an API key, and even once it
  exists, `mcp__webull__*` must never go into a *standing* tool allow
  anywhere (`.claude/settings.json`, `run-cycle.sh`'s default flags) — it
  gets scoped into a single run deliberately, per CLAUDE.md.

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

```bash
./run-cycle.sh premarket    # or alpha-check, or postclose
```

or, in an interactive Claude Code session in this folder: "Run the pre-market
cycle." The orchestrator procedure is identical to the scheduled path -- the
schedule is just a clock.

## Running on millie (Claude Code, headless)

**millie is the only host.** The old host (mochi, Claude Desktop + cloud
scheduled tasks) has been decommissioned -- see CLAUDE.md's migration state
for when and why. Every cycle, scheduled or by hand, runs through Claude Code
in headless mode on millie, driven by systemd timers:

    cycles/*.md          the prompt for each cycle
    run-cycle.sh         wraps `claude -p` with the right flags
    systemd/             three timers + the chromium CDP service
    .mcp.json            the local MCP servers (TradingView, Telegram, Schwab)
    .claude/agents/      the four subagents, with tool access enforced

Setup: see "Setup" above, or `systemd/INSTALL.md` for just the timers.

Two things this host does that the old one couldn't:

**Tool restrictions are real, not prose.** On the old Desktop host the
subagent boundaries were just instructions -- the Technical Analyst was
*told* not to read news. Here the `tools:` line in each `.claude/agents/*.md`
is an allowlist the runtime enforces, so it has no news tool to reach for.
Same for the Knowledge Base Agent, which has no market data tools at all.

**Daylight saving stops being a problem.** The old host's scheduler ran on
UTC cron, so the cycles drifted an hour every March and November and had to
be moved by hand. `OnCalendar=Mon-Fri 07:00:00 America/New_York` tracks the
zone itself.

## Where things run

Every cycle runs directly on millie: `run-cycle.sh` invokes `claude -p` with
the cycle's prompt, in this folder, over the local filesystem. There is no
cloud container, no staging step, and no second machine in the loop --
`kb.sqlite` is read and written right here and committed with a plain `git
commit`. TradingView, Telegram, and (once it exists) Schwab run as local MCP
servers on millie; Google Drive, Google Calendar, Webull, and Alpha Vantage
are `claude.ai` account-level connectors that need no local server at all
(see DATA-SOURCES.md). One machine, one path, nothing to keep in sync.

## Scheduled

| Cycle | Time (America/New_York) | What it does |
|---|---|---|
| `premarket` | 7:00am, weekdays | Full report: charts, news, positions, catalysts |
| `alpha-check` | 9:10am, weekdays | Checks for Kevin's Alpha Report and ingests/scores it if one has arrived |
| `postclose` | 4:30pm, weekdays | End-of-day recap |

All three run on millie via `systemctl --user list-timers 'buffet-bot*'`.
`alpha-check` is 10 minutes after 9:00am deliberately -- Kevin's report
usually lands by 9:00, so checking exactly on the hour risks catching it a
minute early on a slow day; the buffer trades a few minutes of latency for
not missing it. Adjust `systemd/buffet-bot-alpha.timer`'s `OnCalendar` if you
want it tighter.

**Daylight saving:** the scheduler works in UTC underneath, so these shift by
an hour when US clocks change on 1 November 2026 -- `systemd`'s
`America/New_York` `OnCalendar` handles this automatically; nothing to move
by hand, unlike the old host's UTC cron.

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
