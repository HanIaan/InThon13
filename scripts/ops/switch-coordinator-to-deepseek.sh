#!/bin/sh
# Manually promote Lablup DeepSeek to temporary main coordinator. Run ONLY after the Claude
# coordinator has actually hit its usage limit. Refuses while the Claude coordinator is working.
set -eu
WS=a3b64d3e-4b74-4b10-9ef3-42ac0f12c36d          # coordinator workspace (flash-wizard-a3b64d3e)
CLAUDE_TERM=4bc0ba65-e549-493b-9fe0-8a87a5737c98 # Claude coordinator terminal
WRAPPER=/Users/iaan/.config/lablup/claude-lablup.sh # registered "Lablup DeepSeek" agent (7f8b9b59-…)
PROMPT=/Users/iaan/.superset/worktrees/Inthon/flash-wizard-a3b64d3e/docs/ops/coordinator-deepseek-prompt.md

state=$(superset agents explain --local --workspace "$WS" --terminal "$CLAUDE_TERM" --json 2>/dev/null | sed -n 's/.*"state": *"\([a-z]*\)".*/\1/p' | head -1)
if [ "$state" = "working" ]; then
  echo "Claude coordinator is still working (state=$state). Not switching." >&2
  exit 1
fi
echo "Claude coordinator state: ${state:-unknown}. Starting Lablup DeepSeek coordinator..."
superset terminals create --local --workspace "$WS" \
  --command "$WRAPPER --dangerously-skip-permissions \"\$(cat $PROMPT)\"" --json
