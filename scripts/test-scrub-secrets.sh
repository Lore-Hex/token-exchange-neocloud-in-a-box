#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
set +x
set -eu
umask 077
root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT HUP INT TERM
export NIB_BIN="${NIB_BIN:-$root/build/nib}"

# Synthetic literals are deliberately never printed, including on failure.
token="planted-$(openssl rand -hex 16)"
key='literal.[key]+$'
ip='192.0.2.15'
printf '%s\n' "$token" "$key" "$ip" > "$tmp/deny"
printf 'status %s %s %s\n' "$token" "$key" "$ip" > "$tmp/input"
if ! "$root/scripts/scrub-secrets.sh" --deny-file "$tmp/deny" < "$tmp/input" > "$tmp/output" 2> "$tmp/stderr"; then
    printf '%s\n' 'Evidence scrub positive control failed.' >&2
    exit 1
fi
printf 'status [REDACTED] [REDACTED] [REDACTED]\n' > "$tmp/expected"
if ! cmp -s "$tmp/output" "$tmp/expected" || test -s "$tmp/stderr"; then
    printf '%s\n' 'Evidence scrub output did not match the sanitized expectation.' >&2
    exit 1
fi
if "$root/scripts/scrub-secrets.sh" --deny-file "$tmp/missing" < "$tmp/input" > "$tmp/failed-output" 2> "$tmp/failed-stderr"; then
    printf '%s\n' 'Evidence scrub accepted a missing deny file.' >&2
    exit 1
fi
if test -s "$tmp/failed-output" || grep -F -q -e "$token" -e "$key" -e "$ip" "$tmp/failed-stderr"; then
    printf '%s\n' 'Evidence scrub failure disclosed input.' >&2
    exit 1
fi
printf '%s\n' 'Evidence scrub removed planted token/key/IP literals and failed closed.'
