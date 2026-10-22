# P2.T3: fresh held CPU support pair

Status: the first native lifecycle attempt **failed before pair readiness**;
whether either model completed parity is unknown. This is an opt-in executable continuation of
[CPU parity](p2-t3-tau-cpu-parity.md) and [CPU admission](p2-t3-tau-cpu-admission.md),
not a tau trial or an acceptance result. Historical sequential parity receipts
are not converted into live receipts.

## Entry point

The existing committed recorder gains only an explicit `--session` mode:

```sh
python3 -I -S -B scripts/record-tau-cpu-parity.py \
  --archive PRIVATE --model-06 PRIVATE --model-17 PRIVATE \
  --cmake-wheel PRIVATE --output PRIVATE --session
```

The command requires private FIFO stdin and stdout connected to an owning
controller. It rejects terminal/file descriptors and the hosted-workflow mode;
the existing native parity workflow and default sequential behavior are
unchanged. This is not authorization to execute models or rent resources.

The host retains the existing clean committed snapshot, exact public input
copies, offline image assembly, local immutable base preflight, Docker authority
and final owned container/image absence checks. The worker imports only the
explicit hash-checked source closure. The image config ID returned by the local
build is the exact `docker run` target and the receipt's `image_sha256`; it is
not a registry-manifest digest or a reproducibility claim.

Inside the unchanged network-none namespace the worker:

1. Enforces UID/GID 65532, no capabilities, no-new-privileges, seccomp, loopback
   only, 4 CPUs, 8 GiB with no swap, 128 PIDs and no GPU devices.
2. Rehashes both pinned GGUFs and the pinned llama archive, builds the same
   server/independent native tokenizer, and measures both binaries.
3. Starts the two distinct support models using the existing fixed arguments,
   context 4096, output cap 256 and null logs. The first server remains alive
   while the second is prepared. Each runs the unchanged four independent
   tokenizer comparisons, endpoint token-ID comparisons and generated-usage
   checks; raw token IDs and text are never retained.
4. Rechecks GGUF/helper hashes and live executable, raw argv, PID start/boot,
   namespace, listener ownership and reverse connected socket identity using
   the existing guards. Both processes must still be alive with distinct
   PIDs, ports and listener inodes.
5. Creates four exclusive mode-0600 originals in `/work/receipts` (mode 0700):
   `simulator-runtime.json`, `simulator-tokenizer.json`,
   `evaluator-runtime.json`, `evaluator-tokenizer.json`.

The schemas and exact hash recipes are the existing admission contracts.
Each tokenizer receipt binds the exact runtime bytes, all four fixed probe
hashes and the independently measured equal token hashes/counts. Each receipt
binds the committed recorder source. Native helper identity measures the built
helper executable; framing also binds its pinned source recipe. This is a
measurement by the approved producer, not cryptographic hardware attestation.

## Private protocol and lifetime

The worker sends one bounded private readiness frame containing the four
original JSON byte strings. The host validates source provenance and the
admission schemas, checks the exact producer/image and pair distinctness, and
writes those bytes unchanged to the private output directory. It does not
pretend the container PIDs are host-visible or skip any in-namespace guards.

The controller then receives one line prefixed `NIB-TAU-CPU-READY ` with a JSON
object of schema `nib.tau-cpu-pair-held/v1`: `ready_sha256`, `expires_unix`, and
four receipt SHA-256s. There are no model bodies, ports, PIDs or paths in this
controller notification. The controller may use the private receipts only
within the still-owned namespace and approved lifetime. To release it sends:

```text
NIB-TAU-CPU-DONE {"ready_sha256":"<exact ready_sha256>","released":true}
```

This acknowledgement means **release**, not successful tau execution. It is
bound to the full worker readiness frame, including the original receipts.
Missing/wrong/oversized acknowledgements, controller EOF, child exit, identity
failure or deadline fail closed. Both owned children are stopped even if one
cleanup fails; the host independently removes the owned container and image
and requires confirmed absence. Original receipts can remain on failure, but
the final envelope is failed and never proves they are still usable.

The existing 1800-second host runtime bound is unchanged. The worker reserves
60 seconds for cleanup and has an all-inclusive 1740-second runtime deadline;
build/readiness/probe limits remain 900/90/300 seconds as before and fit within
that outer deadline. Readiness pipe writes have a five-second bound. Total
captured child output remains 64 KiB; controller acknowledgement is at most
512 bytes. Image assembly remains separately bounded at 240 seconds. No
automatic release, keepalive, retry, external network or timeout extension is
added. The output envelope always leaves `live_admission`, `quality_eligible`,
`confidential` and `hardware_validated` false.

## Failed native attempt and diagnostics

[Native workflow 39865822820](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39865822820)
ran producer `ea1de780008a7f81b1e54d2df3012c437f109830` on native
Linux/amd64. The controller recorded 2026-10-22 22:36:07-22:41:29 UTC.
The [four original files](../../evidence/p2/2026-10-22-cpu-session-parity-failure/)
are preserved byte-for-byte, including their original source hashes and failed
results. No runtime/tokenizer originals were published. The inner report says
stage `parity`, with child cleanup confirmed; container/image/build cleanup is
also confirmed. The outer report conservatively leaves producer cleanup
`unknown` because it did not admit a successful producer. These reports are
not rewritten to reconcile that distinction.

The old `parity` stage covered the native helper, launch, ownership, health,
comparisons, rehashing and receipt construction. It retained no model index,
comparison rows or exception classification. It therefore proves neither a
tokenizer mismatch nor a timeout, resource failure or receipt-construction
failure. Source comparison has not established the cause. The earlier
sequential pass does not establish concurrent pair readiness.

Session report v2 fixes that observability gap, not a proven runtime cause.
It retains a fixed substage, model index and the existing closed CPU parity
metadata: completed per-case token hashes/counts, usage, measured binary and
process identity. A later failure cannot erase earlier completed comparisons.
Failure metadata is limited to fixed enum, errno, pre-cleanup server exit and
at most eight fixed-source SHA-256/line frames. No exception messages, raw
paths, arguments, locals, prompts, tokens or response bodies are retained.
The existing CPU validator checks these observations; partial comparisons are
not runtime/tokenizer receipts and cannot produce READY or successful release.
No original v1 artifact is promoted to v2.

The standalone parity worker, probes, pilot, kernel/socket guards, Dockerfile,
recorder/controller and workflow remain byte-identical. Source closure remains
23 worker files / 27 controller files; diagnostic sources are already in it.
The v2 diagnostic path has only synthetic regression coverage until a separately
reviewed, green-CI, explicitly approved native attempt. No retry was performed.

## Remaining executable prerequisites

This producer supplies only the support pair's four originals. A selected
target needs its own two **fresh** originals and all three live roles must be
visible to the guarded parent in the same approved PID/network/UID context.
The current GPU owner runs UID 0, while this CPU profile remains UID 65532;
joining namespaces alone does not satisfy the unchanged same-UID guard. An
explicitly reviewed integrated ownership/profile or qualified non-root engine
is still needed. There is no implicit root mode. Docker authority stays on the
host, and the scorer must remain isolated as in the reviewed tau runner.

Before another GPU startup, qualify actual pinned tau prompt/context fit and
evaluator structured output against a fresh support pair. The current 4096
context and 256 output bounds must not be silently enlarged or truncated.
No real tau driver is invoked here. A first operational smoke is not a quality
comparison; an independently qualified same-revision reference remains required.

## Verification

`scripts/test_tau_cpu_session.py` checks byte-identical private publication,
fresh receipt/admission bindings, malformed/replayed release rejection,
child/identity/timeout/EOF failures, both-child cleanup, real subprocess pipe
relay, bounded output, immutable image invocation and final daemon cleanup.
The existing CPU parity, live admission and transport tests retain their
original kernel/peer guards. No actual model, Docker or GPU run is claimed by
these tests.
