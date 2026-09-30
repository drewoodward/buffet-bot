#!/usr/bin/env bash
# buffet bot — run one cycle WITH YOU PRESENT, the only context that ever
# grants mcp__webull__* (real order placement via live_direct).
#
#   ./run-attended.sh premarket | intraday | alpha-check | postclose
#
# This is a near-copy of run-cycle.sh with exactly one difference: the
# allowedTools list below. Every scheduled cycle runs through run-cycle.sh,
# which never grants mcp__webull__* — a scheduled cycle therefore CANNOT
# place a live order at the toolset level, regardless of what config.json
# says. This script is the only place that grant exists anywhere in the
# repo, and no systemd unit references it. Running it IS what "attended"
# means — do not wire this into cron, a timer, or anything else that fires
# without you at the keyboard.
#
# execution-agent.md's own require_attended_session / require_confirmation_link
# gates still apply on top of this and are unchanged by running this script —
# see CLAUDE.md's "Execution safety" section. Granting the tool here does not
# by itself skip either gate.
set -euo pipefail

PHASE="${1:?usage: run-attended.sh premarket|intraday|alpha-check|postclose}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

[[ -f "cycles/$PHASE.md" ]] || { echo "no such cycle: $PHASE" >&2; exit 2; }
[[ -f config.json ]] || { echo "config.json missing — cp config.example.json config.json" >&2; exit 2; }
[[ -f .env ]] || { echo ".env missing — cp .env.example .env" >&2; exit 2; }

set -a
source .env
set +a

mkdir -p logs
LOG="logs/$(date -u +%Y-%m-%d)_${PHASE}_attended.jsonl"

claude -p "$(cat "cycles/$PHASE.md")" \
  --mcp-config .mcp.json \
  --permission-mode acceptEdits \
  --permission-prompts none \
  --allowedTools "Bash,Read,Write,Edit,Glob,Grep,WebSearch,WebFetch,Agent,ToolSearch,mcp__tradingview__*,mcp__telegram__*,mcp__schwab__*,mcp__claude_ai_Webull__*,mcp__claude_ai_Google_Drive__*,mcp__claude_ai_Google_Calendar__*,mcp__claude_ai_Alpha_Vantage_MCP_Server__*,mcp__webull__*" \
  --output-format json \
  >> "$LOG" 2>&1

status=$?
if [[ $status -ne 0 ]]; then
  echo "attended cycle $PHASE failed with status $status — see $LOG" >&2
fi
exit $status
