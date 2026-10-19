#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Only operational output is allowed on stdin, never inference contents.
set +x
set -eu
umask 077

if ! command -v "${NIB_BIN:-nib}" >/dev/null 2>&1; then
    printf '%s\n' 'scrub-secrets: build nib and put it on PATH or set NIB_BIN' >&2
    exit 1
fi

exec "${NIB_BIN:-nib}" evidence scrub "$@"
