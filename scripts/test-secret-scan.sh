#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# Generate a nonfunctional GitHub-shaped token in temporary storage; never
# print the planted value or relax the scanner configuration to admit it.
printf 'access_token = "ghp_%s"\n' "$(openssl rand -hex 18)" > "$tmp/planted.txt"
status=0
"$root/build/gitleaks" dir --redact --no-banner --exit-code 42 "$tmp" > "$tmp/scan.log" 2>&1 || status=$?
if [[ "$status" != 42 ]]; then
  printf 'Secret scanner positive control failed (exit %s, expected 42).\n' "$status" >&2
  exit 1
fi
rm "$tmp/planted.txt" "$tmp/scan.log"
"$root/build/gitleaks" dir --redact --no-banner --exit-code 42 "$tmp"
printf 'Secret scanner rejected the planted token and accepted the cleaned directory.\n'

# A deleted key still leaks through Git history. Prove that mode independently.
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
git -c init.defaultBranch=main init --quiet "$tmp/history"
git -C "$tmp/history" config user.email scanner@example.invalid
git -C "$tmp/history" config user.name 'Secret scanner positive control'
printf 'access_token = "ghp_%s"\n' "$(openssl rand -hex 18)" > "$tmp/history/planted.txt"
git -C "$tmp/history" add planted.txt
git -C "$tmp/history" -c commit.gpgsign=false commit --quiet -m 'Add nonfunctional positive control'
git -C "$tmp/history" rm --quiet planted.txt
git -C "$tmp/history" -c commit.gpgsign=false commit --quiet -m 'Remove positive control'
status=0
"$root/build/gitleaks" git --redact --no-banner --exit-code 42 --log-opts=--all "$tmp/history" > "$tmp/history-scan.log" 2>&1 || status=$?
if [[ "$status" != 42 ]]; then
  printf 'Git-history positive control failed (exit %s, expected 42).\n' "$status" >&2
  exit 1
fi
printf 'Secret scanner rejected a token retained only in Git history.\n'
