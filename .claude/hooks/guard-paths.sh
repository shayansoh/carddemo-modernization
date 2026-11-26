#!/usr/bin/env bash
# PreToolUse guard for subagents.
# Usage: guard-paths.sh <write|all> <path-prefix>...
#   write: block edits to the listed paths (file tools and common shell writes)
#   all:   block any read or edit of the listed paths
# Prefixes are relative to the repository root, for example "tests/".
set -euo pipefail

mode="$1"; shift
input=$(cat)
tool=$(jq -r '.tool_name' <<<"$input")
cwd=$(jq -r '.cwd' <<<"$input")

block() {
  echo "Blocked by project rules: this agent may not ${1} '${2}'. If you believe this is needed, stop and raise it as an open question or issue." >&2
  exit 2
}

case "$tool" in
  Edit|Write|NotebookEdit)
    target=$(jq -r '.tool_input.file_path // .tool_input.notebook_path // empty' <<<"$input"); action=edit ;;
  Read)
    [[ "$mode" == all ]] || exit 0
    target=$(jq -r '.tool_input.file_path // empty' <<<"$input"); action=read ;;
  Grep|Glob)
    [[ "$mode" == all ]] || exit 0
    target=$(jq -r '.tool_input.path // empty' <<<"$input"); action=read ;;
  Bash)
    target=$(jq -r '.tool_input.command // empty' <<<"$input"); action=shell ;;
  *) exit 0 ;;
esac
[[ -z "$target" ]] && exit 0

for prefix in "$@"; do
  if [[ "$action" == shell ]]; then
    if [[ "$mode" == all ]]; then
      grep -qF -- "$prefix" <<<"$target" && block "access" "$prefix"
    else
      grep -Eq -- "(>|tee |sed -i|rm |mv |cp |git checkout |git restore )[^|;&]*${prefix}" <<<"$target" && block "change" "$prefix"
    fi
  else
    rel="${target#"$cwd"/}"
    rel="${rel#./}"
    [[ "$rel/" == "$prefix"* ]] && block "$action" "$prefix"
  fi
done
exit 0
