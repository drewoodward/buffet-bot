#!/usr/bin/env bash
# buffet bot — run one cycle headlessly.
#   ./run-cycle.sh premarket | alpha-check | postclose
set -euo pipefail

PHASE="${1:?usage: run-cycle.sh premarket|alpha-check|postclose}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

[[ -f "cycles/$PHASE.md" ]] || { echo "no such cycle: $PHASE" >&2; exit 2; }
[[ -f config.json ]] || { echo "config.json missing — cp config.example.json config.json" >&2; exit 2; }
[[ -f .env ]] || { echo ".env missing — cp .env.example .env" >&2; exit 2; }

set -a
source .env
set +a

mkdir -p logs
LOG="logs/$(date -u +%Y-%m-%d)_${PHASE}.jsonl"

# --permission-prompts none: nobody is awake to answer. Anything that would
# prompt is denied and the run continues rather than hanging until timeout.
# Requires Claude Code 2.1.259+.
claude -p "$(cat "cycles/$PHASE.md")" \
  --mcp-config .mcp.json \
  --permission-mode acceptEdits \
  --permission-prompts none \
  --allowedTools "Bash,Read,Write,Edit,Glob,Grep,WebSearch,WebFetch,Agent,ToolSearch,mcp__tradingview__*,mcp__telegram__*,mcp__schwab__*,mcp__claude_ai_Webull__*,mcp__claude_ai_Google_Drive__*,mcp__claude_ai_Google_Calendar__*" \
  --output-format json \
  >> "$LOG" 2>&1

status=$?
if [[ $status -ne 0 ]]; then
  echo "cycle $PHASE failed with status $status — see $LOG" >&2
fi
exit $status
