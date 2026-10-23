# CPU application progress diagnostic

Scope: metadata-only localization of the existing CPU application deadline.
This is implemented and tested with synthetic files/kernel responses, not a
native execution result, tokenizer count, context-fit proof or tau admission.
The original local prefix and earlier native failures remain unchanged in
[the prefix evidence](p2-t3-tau-agent-prefix.md#original-local-attempt).
Neither failure identifies an inventory pass or proves a memory/I/O cause.

## Unchanged integrity and bounds

The CPU owner includes a new exact-Git-source helper and its test in the source
closure. The helper digest is bound into the input, verified before loading in
the container, and required by the host's closed receipt validator. Missing,
foreign-source or malformed progress cannot grant entry success. Historical
receipts still use their original producer and validator; they are not rewritten.

`engine_application.py` and `engine_overlay.py` remain byte-identical. A CPU-only
subclass delegates the original selected-tree reads, inventories, writes and
unlinks without replacing any validation. The existing eight complete inventory
passes and all native/header/hash/RECORD/link/depth/size/time checks remain:

1. Initial admission.
2. Pre-mutation recheck.
3. Post-removal verification.
4. Five post-patch-stage verifications.

The same 720-second shared layout/application deadline includes diagnostic work.
Entry remains 300 seconds; host attachment remains 1,080 seconds. The owner still
requires two CPUs, 8 GiB memory with no swap, 128 PIDs, private cgroup/PID/IPC
namespaces, network none, no devices/GPU, all capabilities dropped, no new
privileges, read-only payload bind and the existing private writable container
layer. No mount, environment, engine command, workflow flag or cleanup change.

## Closed metadata

Only the fixed `layout`, `inspection`, `application`, `complete` phases and
`idle`, `inventory`, `read`, `write`, `unlink` operation categories are emitted.
Inventory started/completed ordinals locate a partial pass. Cumulative counts
include successful selected-tree reads and their logical bytes, writes and
unlinks. Failed/in-flight reads contribute no bytes; retained metadata reads and
repeated reads count again. These counters are not physical I/O or hash-only
timing. Elapsed wall time and current-process CPU time include observer overhead;
the observer does not inspect arbitrary processes or arguments.

Samples occur at existing phase/inventory boundaries and at most once per minute
after a completed read. At most 16 samples are retained, reserving the last for
terminal counts/active operation; omitted samples are counted. The progress
object is limited to 32 KiB serialized JSON; the owner's existing 64 KiB output
cap is unchanged. No raw path, device identifier, exception, prompt, token ID,
response, request or free-form label is emitted. No new thread, child, listener,
background sampler, diagnostic disk file or host cgroup discovery is introduced.

The collector requires the owned container's private cgroup-v2 root membership,
read-only root cgroup mount and exact memory/swap limits. Reads are fixed-name,
bounded and deadline-checked. It records memory current/peak, selected anonymous
and file-memory/refault/reclaim counters, selected memory events, and sums of
`rbytes`, `wbytes`, `rios`, `wios` across at most 128 `io.stat` rows. Device labels
are discarded. The definitions follow
[Linux v6.17 cgroup-v2 interfaces and namespaces](https://github.com/torvalds/linux/blob/v6.17/Documentation/admin-guide/cgroup-v2.rst).
These are kernel-accounted aggregates, not a proof of unique physical-device
traffic, total image reads, cache thrashing, or causality. Separate counter reads
are not an atomic sample. Shared page-cache charging and stacked devices limit
interpretation. No cgroup value is written or reset.

Unsupported, missing or malformed telemetry is `unavailable` with null counters,
never fabricated zeros, and stops further application. Once the shared deadline
has expired, the terminal sample is `deadline` with null counters, retaining the
last earlier observed sample and completed operations. Finalization does not
replace the original fixed failure classification with exception text. Crossing
the deadline during terminal capture or the final phase-timing check remains
an application failure; it cannot start entry or claim completed progress. A
blocked call or killed process can still prevent any terminal receipt; the
unchanged independent host deadline/cleanup remains authoritative. In-memory
samples are not a promise of progress delivery after an uninterruptible stall.

## Verification and next gate

Focused synthetic tests exercise original-tree delegation and identical final
application receipts with eight passes, successful-only counters, unsafe-file
rejection, missing/malformed/non-private cgroups, aggregate bounds, throttling,
final-slot reservation, source identity and contradictory/extra fields. Existing
CPU owner, tokenizer and prefix tests continue to cover isolation, cleanup and
source-bound result validation.

Native repetition remains on hold until independent final-head review and full
local/hosted gates, followed by explicit run authorization. No deadline increase,
inventory removal, resource expansion, tau execution, quality result or
confidentiality claim follows from this implementation.
