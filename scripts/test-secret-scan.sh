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

# A known synthetic hash is exempt only at its exact evidence field and path.
# An unrelated value in the same field must still be detected.
evidence_path=evidence/p2/2026-10-22-tokenizer-parity-tool-protocol-fail/output.log
mkdir -p "$tmp/receipt/$(dirname "$evidence_path")"
printf '            "tokens_sha256": "%s"\n' \
  9d6bde6f28006680e5d37e06d816356d782004f1e8dbabf46d42e81b088afc16 \
  > "$tmp/receipt/$evidence_path"
"$root/build/gitleaks" dir --config "$root/.gitleaks.toml" --redact --no-banner \
  --exit-code 42 "$tmp/receipt" > "$tmp/receipt-scan.log" 2>&1
printf '            "tokens_sha256": "%s"\n' "$(openssl rand -hex 32)" \
  > "$tmp/receipt/$evidence_path"
status=0
"$root/build/gitleaks" dir --config "$root/.gitleaks.toml" --redact --no-banner \
  --exit-code 42 "$tmp/receipt" > "$tmp/receipt-scan.log" 2>&1 || status=$?
if [[ "$status" != 42 ]]; then
  printf 'Synthetic-hash exception escaped its exact value boundary.\n' >&2
  exit 1
fi
printf 'Secret scanner still detects unknown values in the hash receipt field.\n'
for boundary in path field; do
  target="$tmp/receipt/$evidence_path"
  name=tokens_sha256
  if [[ "$boundary" == path ]]; then target="$tmp/receipt/other.log"; fi
  if [[ "$boundary" == field ]]; then name=api_key; fi
  printf '            "%s": "%s"\n' "$name" \
    9d6bde6f28006680e5d37e06d816356d782004f1e8dbabf46d42e81b088afc16 > "$target"
  status=0
  "$root/build/gitleaks" dir --config "$root/.gitleaks.toml" --redact --no-banner \
    --exit-code 42 "$target" > "$tmp/receipt-scan.log" 2>&1 || status=$?
  if [[ "$status" != 42 ]]; then
    printf 'Synthetic-hash exception escaped its %s boundary.\n' "$boundary" >&2
    exit 1
  fi
done
printf 'Synthetic-hash exception requires the exact field and path.\n'
