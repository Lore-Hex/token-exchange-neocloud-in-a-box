# P2.T3: Native CPU Pair Controller

Status: implemented with synthetic pipe tests; **no concurrent model execution
or tau result is claimed**. This is the executable owner for the
[fresh CPU session](p2-t3-tau-cpu-session.md), not a general RPC framework or a
conversion of [historical parity](../../evidence/p2/2026-10-22-cpu-parity-native-pass/README.md).

## Fixed Entry

The main-only manual `tau-cpu-session-native.yml` workflow reuses the existing
hash-pinned input preparation. Its concurrency group is shared with native CPU
parity, so the two routes do not run simultaneously. The existing parity
workflow and all worker resource, argv, process/socket and confinement checks
are unchanged. There is no GPU attachment, external model endpoint, arbitrary
command, lifecycle callback or ambient credential forwarding.

`record-tau-cpu-session.py` runs only on a native Linux/x86_64 GitHub-hosted main
checkout. Before starting its recorder it requires at least four host CPUs,
10 GiB total RAM, 8 GiB currently available RAM and 12 GiB free Docker storage.
These are admission checks, not peak-memory or performance measurements.
The recorder still restricts the model container to four CPUs, 8 GiB/no swap,
128 PIDs, UID 65532, no capabilities, read-only root and network-none. Both
distinct model weights, source archives and immutable base images retain the
existing pins; no input is downloaded by the controller itself.

The controller takes the same four private input paths and a new private output
directory. It runs the committed `--session` producer from a verified private
Git snapshot with private stdin/stdout pipes. Every controller/workflow and
producer dependency hash is retained separately; the producer's own source
closure is not rewritten. Ignored files, replacement Git refs and credentials
are not imported into the snapshot.

## Readiness and Release

Only one canonical `NIB-TAU-CPU-READY` line is accepted. The controller reads the
four already-published mode-0600 originals without following symlinks, verifies
their hashes and reuses the existing session/admission validators. The exact
current producer, readiness-frame digest, immutable built-image ID and fresh
expiry must agree. It does not re-encode or manufacture receipt files, use a
historical producer, or claim container PIDs are host-visible.

In the default mode, only after this validation does the owner send one bound `NIB-TAU-CPU-DONE`
release and close its input pipe. There is no trial request or successful
evaluation acknowledgement. The final original producer report must bind the
same source archive, source files, image, readiness digest and four receipt
hashes, with confirmed child/container/image cleanup. The controller rehashes
the originals again after release. Success means readiness validation and
release completed, not that the stopped pair is still admissible.

Total controller time is 2280 seconds, including source preparation; the
existing producer's 240-second image assembly and 1800-second runtime limits
are unchanged. Output is bounded to a 4096-byte readiness frame plus 1024 bytes
of fixed producer status, never persisted verbatim. On failure the owner closes
stdin, allows up to 60 seconds for producer cleanup, then uses the existing
owned process-group termination and absence check. A killed client is **not**
daemon cleanup proof; missing/invalid final receipts leave producer cleanup
unknown and the run failed. The hosted step is bounded at 40 minutes and job at
60 minutes, with no retry, timeout extension or emulator exception.

Only the controller's two metadata reports and the producer's six original
metadata receipts are uploaded, including failures. No request/response text,
raw token IDs, subprocess stderr, private paths or exception messages are put
in controller reports. Output directories/files are exclusive, private and
no-follow. No pre-existing receipt is overwritten or relabeled.

## Remaining Gates

Independent final-head review and all hosted checks are required before any
separately approved actual run. A first held-pair run would measure functional
concurrent readiness only. Original pinned tau prompt/context fit and evaluator
structured-output checks have a separate explicit
[fixed support-probe mode](p2-t3-tau-cpu-prompt-fit.md), with the existing 4096/256
bounds and no truncation. Its manual input defaults false; no actual probe pass
is implied. No target is launched here; the UID/PID/network
context mismatch with the current UID-0 GPU owner remains explicit. No tau,
quality, live-admission, confidentiality or full-acceptance claim is enabled.
