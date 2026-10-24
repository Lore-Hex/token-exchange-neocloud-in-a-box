# Selected Operational Trial Controller

`scripts/record-tau-selected.py` joins the existing isolated GPU lab, CPU
support-pair owner and sealed scorer. It is an explicit hardware operation on
an approved ordinary Linux/amd64 eight-B300 host, not a public serving command.

Prepare the GPU bundle from the **same clean commit** with the selected
`uncensored` checkpoint, `tau8k-eager` profile, tokenizer parity,
materialization, runtime observation and `--tau-owner`. Supply the exact
bundle receipt hash and the independently restored/hash-verified weight tree.
The CPU inputs are the four fixed files in
`scripts/prepare-tau-cpu-parity-native.py`'s `plan`; the scorer archive and
wheels are pinned by the existing tau runtime lock. Nothing downloads during
inference. Both pinned Go/Python images and the GPU image must already exist.
Before preparing the scorer payload or starting either owner, the controller
performs bounded read-only image checks: the existing selected-scorer descriptor
validator on its fixed local socket, and the CPU recorder's existing base-image
validator on the CPU owner's local Docker context. Missing images, unsupported
image-inspection commands and incompatible metadata fail before GPU startup.
This does not add a classic-store fallback, pull images or replace the original
checks repeated by each consumer at use time.
The same preflight also calls the CPU recorder's pinned `require_buildkit`
helper in its private Docker configuration and resolved CPU context. A missing
Docker/Buildx executable, failed version check, timeout or exhausted preflight
deadline prevents either owner from starting. This check installs nothing,
builds no images and does not replace the CPU owner's repeated Buildx check.

Stage transferred bundles as root-owned private files. When packing on macOS,
exclude AppleDouble and extended attributes with `COPYFILE_DISABLE=1` and tar's
`--disable-copyfile --no-xattrs`; on the approved Linux host, extract **as root**
with GNU tar `--no-same-owner` into a fresh private directory. Preserve file
modes and the original receipt hash. The controller reuses the GPU recorder's
[payload ownership validator](p2-t3-payload-ownership.md) immediately after bundle
authentication, before image checks or owner startup. It does not repair
ownership, widen permissions, relax receipt checks or add container capabilities.

```sh
python3 -I -S -B scripts/record-tau-selected.py \
  --gpu-bundle /private/gpu-bundle --gpu-receipt-sha256 EXACT_HASH \
  --weights /private/verified-weights --cpu-inputs /private/cpu-inputs \
  --source-archive /private/tau-source.tar.gz --wheelhouse /private/wheels \
  --output /private/new-trial --deadline YYYY-MM-DDTHH:MM:SSZ
```

The explicit cutoff must be more than 22 minutes and no more than 70 minutes
away. Six minutes are reserved for owner cleanup. Original worker expirations
are never extended; a slower startup reduces the available trial time and may
fail closed. The fixed task is retail task 2, trial 0, seed 42, at concurrency
one with no retries. Selected policy limits remain unchanged.

The controller starts both owners, validates the original private readiness
packets and their pinned source identities, then passes borrowed descriptors
to the existing scorer lifecycle. It closes and reaps both children even on
failure. A pass also requires exact joins to the final CPU/GPU reports, both
finish acknowledgements, actual usage, and independently confirmed owner and
scorer container cleanup. The GPU lab's original outer `FAIL/BLOCKED` result
is preserved: an operational trial is not full model qualification.

A completed, acknowledged failed trial can independently confirm cleanup when
the same original source/readiness/receipt joins and all owner cleanup checks
hold. Its scorer, CPU runtime, owner statuses and exit codes must agree on
failure; the GPU outer qualification remains `FAIL/BLOCKED`. The controller
preserves that failure and the original report hashes, never a reward or pass.
Contradictory outcomes, missing evidence, an unstopped engine or unconfirmed
cleanup still reject the join. Cleanup before readiness is not inferred.

For a successful final join, the envelope's driver and CUDA facts come from
the original GPU observation, revalidated against the selected eight-B300
nonconfidential profile and matched to the GPU evidence envelope. CUDA means
the driver-supported API version, not a measured runtime version. These facts
do not grant hardware qualification, attestation or confidential status. When
the join cannot establish them, the failed envelope retains null versions.

On abort, including a peer failure before either READY packet, the controller
sends SIGTERM to its still-running GPU outer recorder. That recorder's existing
cancellation handler unwinds startup into its owned-container cleanup and final
report path; EOF alone cannot interrupt startup before the stdin relay begins.
The CPU owner still receives SIGINT for its existing exception-based cleanup.
These initial signals target only the controller's direct children, not the
GPU attach or watchdog process groups. Final stdout is drained without logging
it for at most 180 seconds, capped by the original controller cutoff, before
the existing owned-process-group reaper runs. Neither a signal nor a child exit
proves daemon-side cleanup. Failed pre-READY runs remain failed with joined
cleanup unconfirmed, even if an individual owner's original report confirms
its own cleanup; the final readiness/scorer/report joins are not relaxed.
GPU-free cancellation tests exercise the committed outer handler and cleanup
with a blocked fake attach, including ignored SIGINT and failed absence checks.
They do not establish the cause of an earlier hardware startup failure.

The output contains separate original GPU, CPU and scorer evidence plus the
joined `evidence/run.json` and `evidence/output.log`. No model text or exception
messages are logged. Support-model quality limits comparison: even an evaluated
task is **not** a full tau score, a frontier-judge evaluation, a production
listing, or confidential-compute proof. GPU-free tests establish controller
behavior only; actual results require separate original hardware evidence.
