#!/usr/bin/env bash
# SubagentStop hook for the implementer: the agent may finish only when
# `dotnet test` passes, or when it reports that it is blocked.
set -uo pipefail

input=$(cat)
cwd=$(jq -r '.cwd' <<<"$input")
last=$(jq -r '.last_assistant_message // empty' <<<"$input")
cd "$cwd" || exit 0

# An agent that cannot proceed opens an issue and says "BLOCKED:" in its final message.
if grep -q '^BLOCKED:' <<<"$last"; then
  exit 0
fi

# Nothing to test yet (no solution or project files).
if ! ls ./*.sln ./*.slnx >/dev/null 2>&1 && [[ -z "$(find . -name '*.csproj' -not -path './.git/*' -print -quit)" ]]; then
  exit 0
fi

if output=$(dotnet test --nologo 2>&1); then
  exit 0
fi

{
  echo "You cannot finish yet: dotnet test is failing. Fix the code under src/ until all tests pass."
  echo "If a test or spec is wrong, open an issue with gh issue create and end your reply with a line starting 'BLOCKED:'."
  echo "Last lines of the test output:"
  tail -n 40 <<<"$output"
} >&2
exit 2
