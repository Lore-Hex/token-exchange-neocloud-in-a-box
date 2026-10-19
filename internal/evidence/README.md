# Evidence tooling

This package is operator-only and uses the Go standard library. The CLI owner
wires `evidence.Run(argsAfterEvidence, stdin, stdout, stderr) int`. It returns
0 for success, 1 for operational errors, and 2 for invalid usage.

## Scaffold

`nib evidence new P0 unit-test` discovers the current Git worktree root and
creates `evidence/p0/YYYY-MM-DD-unit-test/run.json` and an empty `output.log`.
The UTC date is the creation date, not a fabricated execution timestamp.
`New(root, phase, slug) (relativeDirectory, error)` provides the same API for
callers with a known repository root. P0 through P9 are accepted, ignoring
phase case. Slugs use lowercase ASCII letters/digits and internal single
hyphens, up to 64 characters. Never put sensitive values in either identifier.

The scaffold records HEAD, `result: not_run`, `execution: not_run`,
`hardware_validated: false`, null task/host/start/end, and a fixed command
placeholder. It does not run commands, read shell history, capture arguments,
discover hardware/host facts, or collect environment variables as evidence.
An operator must fill in reviewed, scrubbed facts from an actual run before
claiming pass/fail. There is no automatic hardware-validation path.

Created directories are 0700 and files 0600 (subject to a stricter umask).
Existing real evidence/phase directories are allowed unless group/world
writable. Symlinks are rejected, path operations are confined with `os.Root`,
and existing runs/files are never overwritten. On I/O failure an incomplete
private directory may remain; inspect it instead of blindly retrying/deleting.

## Scrub

`nib evidence scrub --deny-file /private/path/deny.txt` reads stdin and writes
only scrubbed output to stdout. `scripts/scrub-secrets.sh` forwards the same
arguments to `nib` on PATH, or to the executable specified by `NIB_BIN`.
It does not build/download tools or write temporary/raw log files.

Create the local deny file outside version control with permissions 0600.
It must be a regular, non-symlink file. Each LF/CRLF-delimited nonempty line is
a case-sensitive literal: tokens, key material lines, IP addresses, hostnames,
and regex metacharacters are treated identically. Spaces are significant and
`#` does not start a comment. Blank lines are ignored; an empty deny list is
rejected. NUL and embedded CR are rejected. For multiline key material, list
each nonempty line. Listing only a key header does not redact its body.

`Scrub(deny, input io.Reader, output io.Writer) error` buffers at most 1 MiB of
deny data and 64 MiB of input; up to 1024 unique literals of 64 KiB each are
accepted. All overlapping matches are covered by `[REDACTED]`. Read/validation
errors write no output. A marker/boundary collision with a denied literal also
fails closed without output. Write failures may leave a sanitized prefix;
check the exit status. Diagnostics never contain raw input, secrets, paths,
or underlying reader/writer errors.

This is a literal scrubber, not a secret detector or inference-data anonymizer.
Never feed it prompts, completions, bodies, headers, or query strings. Unlisted
or encoded values are not detected. Review sanitized operational artifacts
before committing them. The wrapper's umask cannot secure a destination opened
by the calling shell: set `umask 077` and use shell noclobber (`set -C`) before
redirecting to a new private file. Do not redirect over an existing run log.

Tests use only temporary Git repositories and synthetic values. They are not
hardware evidence, and no scaffold is committed as a measured run.
