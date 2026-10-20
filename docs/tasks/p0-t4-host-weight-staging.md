# Bounded Host Weight Staging

This host wrapper is software-only preparation, not an engine launcher or
hardware qualification. It uses the existing `nib model stage` StagePlan and
StageProof contracts. There is no second downloader, model-file hash verifier,
cloud API, GPU command or runtime admission path.

## Before Renting Or Downloading

The pinned DeepSeek pack contains 83 files and **510,311,045,295 bytes**. A
500 GB disk is insufficient even before filesystem overhead. This wrapper
requires available bytes on the destination filesystem of at least the full
pack plus `max(10 GiB, ceil(pack bytes / 10))`: **561,342,149,825 free bytes**
for this pack. This is a conservative software allowance, not measured
filesystem overhead, reserved space or capacity for a later immutable image.
Check actual free space, quotas and local filesystem suitability; a provider's
advertised disk size is not a free-space measurement. Resume uses the same full
allowance rather than guessing which existing bytes are reusable.

Separately approve host rental, storage, public weight transfer, duration and
total cost. Set an independent rental stop/delete deadline. The wrapper has
no provider credentials and cannot stop billing, delete a VM or undo a rental.
An inventory pass does not establish TDX, GPU CC, topology or model readiness.

## Prepare On The Trusted Build Host

Use a clean reviewed commit and existing offline Go 1.27.2/module caches. The
committed command below uses the existing pinned image-recorder build helper,
but does not call its Docker or image-diagnostic paths. It builds the ordinary
native CLI for an offline plan and a Linux/amd64 CLI for transfer:

```sh
python3 -I -S host/record-stage.py prepare --output "$NEW_PRIVATE_BUNDLE"
```

`NEW_PRIVATE_BUNDLE` must be an absolute new path under an existing trusted
directory. The output is mode 0700 and contains `record-stage.py`, `nib`,
`pack.yaml`, `files.sha256` and `receipt.json`. Creation never overwrites an
existing bundle. A failed preparation may leave an incomplete private bundle;
it is not accepted without its final receipt.

The preparation reads the wrapper, fixed pack and Go inputs from the exact Git
commit, excluding untracked/ignored files and Git replacement/lazy-fetch inputs.
The loaded wrapper must match that commit. It reuses source-snapshot building,
the selected exact cached toolchain, disabled/private Go telemetry, controlled
flags, CGO disabled and `GOPROXY=off`/`GOSUMDB=off`. Missing prerequisites fail
offline; there is no install/resolver/download fallback. Ambient Go overlays,
workspace and flags are not used. No weight network call occurs while preparing.

Retain the printed source SHA and receipt SHA-256 on the trusted build host.
Transfer the private bundle over the separately approved channel. Supply that
independently retained receipt digest to the host command; **do not derive the
expected digest from an untrusted received receipt**. This is a trusted-builder
receipt binding exact binary, wrapper, pack and manifest bytes to the committed
build-source digest. It is not a signature, reproducible-build proof, compiler
attestation or proof against a malicious host. The source/OS/compiler and
operator's receipt transport remain trusted.

## Preflight And Detached Run

On an approved Linux x86_64 host, select actual operator-owned private paths:

- `BUNDLE`: transferred mode-0700 bundle directory.
- `DESTINATION`: dedicated staging directory under an existing mode-0700
  owned parent on the approved local POSIX storage. It may be absent or a
  resumable mode-0700 directory; no other writers may use it.
- `EVIDENCE`: new private output directory, outside the staging directory.
- `RECEIPT_SHA256`: digest retained on the trusted build host.
- `PRIVATE_LOG`: new private progress log outside both directories.

No provider-specific mount, hostname or cloud flag is assumed. Confirm the
paths on the actual host before using them. First run the no-download preflight:

```sh
python3 -I -S "$BUNDLE/record-stage.py" run \
  --receipt-sha256 "$RECEIPT_SHA256" --destination "$DESTINATION" \
  --evidence "$EVIDENCE" --seconds 10800 --max-bytes 510311045378
```

This verifies the bundle, reports its pinned plan and disk capacity, and does
not execute a transfer or create evidence. The parent must have enough free
space for a new destination; an existing destination is checked on its own
filesystem. The free-space check is not a reservation; concurrent consumption,
quotas and disk failures can still stop the real run.

After explicit transfer/run approval, use the same arguments with `--download`:

```sh
umask 077
set -C
nohup python3 -I -S "$BUNDLE/record-stage.py" run \
  --receipt-sha256 "$RECEIPT_SHA256" --destination "$DESTINATION" \
  --evidence "$EVIDENCE" --seconds 10800 --max-bytes 510311045378 \
  --download >"$PRIVATE_LOG" 2>&1 </dev/null &
STAGING_WRAPPER_PID=$!
```

Retain the wrapper PID privately for cancellation and verify the final evidence
and exit status; background command submission is not success. The body-byte
budget must equal the pinned plan's minimum budget, including one overflow
sentinel per file. Runtime is capped at three hours; the underlying CLI supports
longer bounds, but this wrapper deliberately does not. Shorter approved runs
can use fewer seconds and retain completed files for a later approved retry.

The wrapper copies verified binary/pack inputs to a private temporary directory,
rechecks StagePlan using that exact CLI, then invokes only `model stage` with
the explicit bounds. The child receives a minimal environment, no inherited
API credentials, proxy, tokens or certificate overrides. No model/engine import
or GPU access occurs.

## Progress, Exit And Cleanup

Every 30 seconds the progress log reports only elapsed seconds and bounded
aggregate filesystem counts/bytes, with `verified:false`. No model contents,
filenames, hostnames, IPs, environment, command diagnostics or URLs are logged.
These observations are not HTTP-byte counters or verified-file counts. A null
count means the bounded metadata observation was unavailable; it is not zero.
Long hashing phases can show unchanged counts. There is no heuristic idle kill
that mistakes final verification for a stalled transfer.

SIGINT, SIGTERM, timeout, output-limit and parser failures trigger owned process
cleanup. A supervising process keeps the group identity alive and detects a
closed parent liveness pipe. The downloader gets up to 30 seconds to handle
TERM and remove its own temporary file; then the owned group is killed, reaped
and checked for absence within bounded waits. Plan verification has a separate
30-second limit; transfer deadline, termination grace and cleanup waits are
not a rental-expiry mechanism. Kernel-uninterruptible I/O can prevent cleanup;
that remains failure/unproven, not permission to launch an engine.

The wrapper never removes model files. Existing complete files remain; the Go
stager rehashes them on an approved retry. A forced kill/crash may leave a
partial file and the next run will reject it. Follow the
[existing recovery procedure](../../models/README.md#restart-and-recovery),
never wildcard-delete a live directory. `cleanup:confirmed` in wrapper evidence
means local process-group absence, **not** model-directory deletion, independent
partial-file verification or VM termination.

Private mode-0600 `output.log` and `run.json` are written without overwrite in
the new mode-0700 evidence directory. Download/plan/proof failure records a fixed
failure code and returns 1; success requires matching StageProof and confirmed
process cleanup. Raw child stdout/stderr never enter the log or evidence.
Pre-bundle/bootstrap failures cannot claim source-bound evidence. A crash or
SIGKILL can leave incomplete evidence; a directory or progress log alone is
not proof. `pass` means pinned bytes were staged and checked by the CLI, not
immutable storage, hardware qualification, TEE admission or runtime readiness.

## Software Verification

```sh
uv run --project tools --frozen python -I -m unittest discover -s scripts -p 'test_weight_stage_recorder.py'
```

Tests use synthetic bundles/files, a fake stage CLI result, local stdlib child
processes and the real offline evidence-schema validator. They cover receipt
and source tamper, FIFO/link/size failures, correct destination-filesystem disk
checks, plan-only/insufficient-space no-execution, malformed/failed proofs,
private no-overwrite publication, bounded metadata-only progress, process-tree
timeout/cancellation and pinned-builder environment reuse. No actual weight
download, remote host, GPU run or rental is performed by these tests.
