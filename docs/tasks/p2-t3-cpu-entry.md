# CPU-Only Engine Entry Diagnostic

Software-only diagnostic, not serving or qualification. The candidate stays
**BLOCKED**, `deployable=false`, `confidential=false`, and every evidence envelope
has `result=fail`, even if `entry_pass=true`. This does not satisfy P2 model,
provider, quality or tau acceptance. The prior paid VM is gone; this task does
not authorize a replacement rental.

The lab-only [pattern-matcher adapter](p2-t3-pattern-matcher-compat.md) addresses
one exact upstream registration conflict without permitting arbitrary finder
changes. Its source-backed synthetic reproduction is not proof of the finder
identity in an earlier diagnostic.

## Boundary

The committed recorder is
[`record-engine-cpu-entry.py`](../../scripts/record-engine-cpu-entry.py).
It reuses the exact-image application builder, immutable image/config/DiffID
verification, selected wheel/source/native integrity checks, bytecode removal
accounting and all five patch stages. No generic admission policy is relaxed.
The read-only payload is bound to committed source bytes; preparation fails
on a dirty source mismatch, missing offline Git object or substituted helper.

After application, a fresh `-I -S -B` child uses the existing lab bootstrap and
the shared `prepare_engine` sequence: imports, parser construction, exact
existing lab arguments, argument validation, then the lab state
check. It returns before `serve.cmd`. The CPU launcher has no serving, hardware
probe, weight verification, model loading, HTTP request or worker-dispatch
route. There is no `/model` mount, model binary or model manifest in its payload.
The existing `/model` argument is only parsed, not replaced with a guessed
upstream CPU flag.

Source basis at vLLM `5df63a7203cdb9328d824a27b682e77c7b9928be`:
[ServeSubcommand](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/entrypoints/cli/serve.py)
separates parser/validation from `cmd` server dispatch;
[validate_parsed_serve_args](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/entrypoints/launchers/cli_args.py)
performs pre-loading argument checks. Existing fixed arguments, source pins
and compatibility constraints remain documented in the
[lab contract](p2-t3-deepseek-lab.md).

## Observations And Limits

Checkpoint IDs 1-16 retain their existing meaning. ID 16 means the state check
was entered; exit zero and a valid final observation are also required to pass
this diagnostic. ID 17 is never emitted: the serving boundary is not entered.
Each endpoint is an explicitly passed write-only nonblocking FIFO, made
non-inheritable and closed on every owned exit path. No FD number is assumed.
The parent retains only a fixed status and last checkpoint.

A second dedicated pipe carries exactly two bytes: ten fixed mismatch bits,
or the unavailable sentinel. It reports only the existing comparisons for
paths, executable, spawn executable, start method, cwd, pycache prefix, plugin
setting, worker method, NUMA environment presence and finders. No values,
module/finder names, exception text, stack, argv or logs are retained.
Unavailable or malformed observations cannot pass. These are observations at
child termination, not proof of which mutation first caused a rejection.
They do not encode every loader or source-verification failure. In particular,
the pattern adapter's sticky load/identity rejection can exit nonzero while
all ten original process-state comparisons remain false; that is still failure.

Imports can execute pinned dependency initialization and CPU/compiler probes.
This is not the P0 deny-all-process import guard or an adversarial Python
sandbox. No GPU devices/toolkit runtime are supplied. Missing CUDA, absent
model files, the noexec cache, denied Python worker/VecISA reentry or platform
selection can produce CPU-only failures that do not reproduce the prior GPU
startup failure. A finder mismatch proves only the fixed comparison failed,
not which finder changed or that the same failure occurred on that VM.

## Container And Evidence

The existing P0 CPU container policy is retained: cached immutable
`linux/amd64` image, `--pull never`, `runc`, network none, no devices, exposed
ports or credentials, all capabilities dropped, no-new-privileges, private
IPC/cgroup namespaces, 128 PIDs, two CPUs, 8 GiB RAM/swap cap, 16 MiB shared
memory, no restart or Docker log capture. Only the private payload is mounted,
read-only. `/tmp` and `/cache` are each 512 MiB noexec tmpfs mounts.
The ephemeral container layer is writable for the exact source application;
no image is built, committed or published.

One shared 720-second application deadline covers all inventory passes;
the child has 300 seconds and Docker attach has 1,080 seconds. The container
also exits on its own 1,065-second alarm. Parent output is capped at 64 KiB.
Whole-container removal and absence, not child-leader exit, establish cleanup.
Lost daemon completion or failed ownership/cleanup checks stay unknown and
cannot pass. Docker, the OS and interpreter are trusted components.

Offline preparation:

```sh
python3 -I -S scripts/record-engine-cpu-entry.py --prepare /private/tmp/nib-cpu-entry-payload
```

Only after final-head independent review and green CI, the parent may explicitly
record one local run with `--slug <approved-slug>`. It needs the existing pinned
raw OCI inputs in ignored `build/engine-image-metadata`, the cached exact image,
and the offline pinned Go validator. Evidence is published without overwriting
under `evidence/p2/<date>-<slug>/`, through the authoritative evidence schema.
The implementation alone claims no image execution or actual diagnostic result.
Recorded attempts are listed separately below.

## Recorded Local Attempt

The [2026-10-20 attempt](../../evidence/p2/2026-10-20-cpu-entry-125036c/README.md)
ran reviewed source `125036cb61a9d15d624f5d1c8a0c65d69076574c` after green CI.
Image preflight took 8,562 ms, all five application stages 496,309 ms, and the
entry child 9,148 ms. The child exited 1 after checkpoint 11, in the interval
containing the `ServeSubcommand` import. The final observation reports only
`finders=true`; all other mismatch bits are false. Cleanup is confirmed.
The responsible dependency and exact first exception remain unproven by this
observation. This is a local software failure, not a new GPU attempt, and it
does not establish the cause of the previous hardware startup failure.
