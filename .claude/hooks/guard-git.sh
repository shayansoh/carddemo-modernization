#!/usr/bin/env bash
# PreToolUse guard for every Claude session in this repo.
# Claude may commit and push only on feature branches, never on or to main.
set -euo pipefail

input=$(cat)
cmd=$(jq -r '.tool_input.command // empty' <<<"$input")
cwd=$(jq -r '.cwd' <<<"$input")
[[ -z "$cmd" ]] && exit 0

block() {
  echo "Blocked by project rules: $1 Work on a feature branch, push it, and open a pull request with gh pr create." >&2
  exit 2
}

# Pushing to main by name, in any form
if grep -Eq 'git[^|;&]*push[^|;&]*([ :/]main\b|HEAD:main)' <<<"$cmd"; then
  block "Claude may not push to main."
fi

# Committing or pushing while the current branch is main
if grep -Eq 'git[^|;&]*(commit|push)\b' <<<"$cmd"; then
  branch=$(git -C "$cwd" branch --show-current 2>/dev/null || true)
  if [[ "$branch" == "main" ]]; then
    block "Claude may not commit or push on the main branch."
  fi
fi

# Merging pull requests is a human decision
if grep -Eq 'gh[^|;&]*pr[^|;&]*merge' <<<"$cmd"; then
  block "Claude may not merge pull requests."
fi

exit 0
