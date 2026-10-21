# P3.T1: Identify rejected public build inputs

[Native run 39691976863 attempt 1](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39691976863/attempts/1)
authenticated the index archive, then rejected an input fetch before any package
installation. The original log does not identify the failing archive or cause;
do not label it a timeout, HTTP failure or hash mismatch without evidence.

The fetcher now emits only the already-validated public input's expected digest,
size and kind, a fixed failure category, and a bounded numeric process exit code
when available. It never emits URLs, paths, response bytes, curl stderr, command
arguments or exception messages. The native workflow preserves this diagnostic
even if the installation worker never starts.

All validation, byte limits, per-input and total deadlines, network destinations,
atomic publication and no-retry behavior are unchanged. Tests separately cover
hash/size/stderr/exit failures, timeout/output-limit/OS exceptions, no-network
budget expiry and private-string non-disclosure. Failed fetches still abort and
cannot publish partial inputs or run packages. Full local and hosted checks gate
merge. This improves diagnosis; it does not claim the unknown upstream failure
has been repaired or the image has been built.
