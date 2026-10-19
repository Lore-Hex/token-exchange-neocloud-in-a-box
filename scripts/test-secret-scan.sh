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
