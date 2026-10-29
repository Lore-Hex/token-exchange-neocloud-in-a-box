# Original staged-view consumer handoff

Status: software draft with synthetic tests, not a hardware result or operation
approval. No mount, Docker operation, model read, image build, inference,
service replacement or live-profile change was performed for this change.

## Scope and prerequisites

This is an explicit opt-in to the existing
[service owner](../../host/record-selected-service.py), not another supervisor.
It pairs [one mount lease](../../scripts/original_view_mount.py) with
[one consumer hold](../../scripts/original_view_consumer.py). Only the closed
`original` model and `original-long1m-graphs` execution profile are accepted.
The existing no-option service path, resource policy, lifetime limits and
receipt assertions remain. No arbitrary model, command, limit override,
embedded checker, fallback, retry or foreign-resource adoption is added.

Prerequisites remain blocking until independently established:

- A successful exact [exclusive NFS staging](p2-t3-original-nfs-staging.md)
  receipt and its immutable local intent, created and completion records.
  Failed or unacknowledged staging is never promoted, even with matching bytes.
- A separately reviewed source commit and newly built prepared service image
  containing this consumer and the matching service-process source. Existing
  image-build evidence does not prove those new bytes. Preparation, build,
  archive/load verification and their exact digests need fresh evidence; there
  are no invented replacement image/version pins in this change.
- An otherwise idle Docker venue, as required by the existing service owner.
  This does **not** implement draining/replacing an existing service, catalog
  changes, external conformance, or rollback. Those require separate review and
  authorization. An old view is usable for rollback only while its own mount
  owner, lifetime and integrity are still valid.
- An independently approved exclusion of all writable aliases and concurrent
  writers to the staged export, and of unregistered mount consumers, until the
  owned mount and consumers are absent. The plan records a digest and this
  scope; software does not discover remote aliases or authenticate the content
  of that operator evidence. Read-only is not immutable or confidential.
- Root Linux x86-64, the existing cgroup-v2 capabilities, local durable record
  storage, exact NFS export and permissions, and a private-propagation empty
  target in the actual Docker daemon mount namespace. Unsupported topology
  rejects before mounting; there is no host-root propagation change, permission
  relaxation, namespace entry or `/proc/<pid>/root` serving shortcut.

## Closed plan and invocation

Add these two arguments to the existing reviewed service-owner invocation:

```sh
--original-view-plan "$PRIVATE_PLAN" \
--original-view-plan-sha256 "$EXACT_PLAN_SHA256"
```

Both or neither must be supplied. `--execution-profile` must be
`original-long1m-graphs`, and `--weights` must be exactly
`<target>/.nib-original-view-<staging-attempt>/model`. Existing explicit lifetime,
hard-stop, image, input, network and pricing arguments retain their meanings;
this change chooses none of their deployment values. Qualification/smoke flags
are not allowed with this opt-in. `--inspect` validates plan/evidence and local
admission only; it cannot assert NFS, daemon mount-namespace or consumer success.
It still performs the existing local Docker/network observations.

The plan is a private `0600` regular JSON file under a private `0700` parent,
with exactly these keys. No public example contains real operator paths or
export addresses.

| Key | Required meaning |
| --- | --- |
| `schema` | `nib.original-view-plan/v1` |
| `attempt_id` | Fresh 32 lowercase hex digits, distinct from the staging attempt |
| `producer` | Independently reviewed exact 40-hex source commit |
| `mount_helper_sha256` | SHA256 of this checkout's `scripts/original_view_mount.py` |
| `staging_receipt`, `staging_receipt_sha256` | Private byte-original successful staging receipt and its SHA256 |
| `staging_producer` | Its actual 40-hex producing commit, not the current commit |
| `staging_ownership` | Private local directory containing its three immutable records |
| `destination_export` | Exact destination export whose digest the staging intent bound |
| `target` | Existing empty, private, unmounted, disjoint absolute directory |
| `verification_seconds` | Integer 61..3600, fitting inside the existing startup deadline |
| `alias_exclusion_evidence_sha256` | Digest of independently reviewed operator evidence, not a software proof |
| `alias_exclusion_scope` | `until-owned-mount-and-consumers-absent` |

The plan's source commit is an operator pin, not guessed from a deployed tree.
Its helper SHA is checked against actual bytes. The source closure must itself
be independently verified and held read-only through use. Preserve producing
Git ancestry through normal merges, as required by
[the evidence workflow](../development.md).

## Ownership and verification

The lease uses the unchanged staging helper with SHA256
`83e7c90b44c74865f20f81ad3f3c2dada7cf22b9547ba6c2949be838a9389c62`,
which transitively pins the existing inspection, cache, view, profile,
publication, capture and resource helpers. It reuses their parser, manifest,
no-replace publication, mount readback, full-file hashes, cgroup lifecycle and
detached-descendant cleanup. No new supervisor, mount parser or copy path exists.

The existing watchdog records the plan/staging digests, exact export/target,
boot ID and actual mount namespace. It opens a pidfd for the discovered daemon,
then revalidates its existing process identity and actual mount-namespace
device/inode, retaining the namespace descriptor. Sharing network or process
namespaces alone is insufficient.

Before any mount, immutable local `mount-intent.json` is published. A new
read-only NFS mount is then read back by exact source, target, filesystem,
options, descriptor mount ID and device. `mount-created.json` records that
identity. Only the inner `model` path is offered to Docker; the staging wrapper
stays root-owned `0700`, the inner directory `0555`, and files `0444`.

Reopening checks both the **staging-created wrapper and model inodes**, not
just content or existence. Historic staging devices are preserved in evidence
but are not treated as durable NFS export identities: a remount can have a new
client `st_dev`. Both reopened directories must belong to the fresh exact
mount/device and retain their original inodes. A server with unstable file IDs
fails closed. This does not establish inode generations or exclude malicious
inode reuse; the independently maintained no-writer/alias gate is essential.

The attach worker performs a fresh full 83-file, 510,311,045,295-byte manifest
rehash, inventories and mode checks, and rechecks mount and directory identity.
It runs in the pinned helper's exclusive cgroup: two CPU cores, 8 GiB memory,
zero swap and 256 tasks. Worker budget is `verification_seconds - 60`; the
reserve is not a guarantee that hard-NFS tasks terminate. Exact limit readback,
zero OOM kills, empty/removed cgroup and a closed success acknowledgment are
required before admission. The host parent does not repeat NFS reads outside
this bounded worker. No third RAM copy or persistent data mutation is added.

Admission uses the existing local-record publication primitives and an
exclusive nonblocking lock. Registration is durably published **before Docker
create**, and the lock spans create plus exact ID publication. Closure cannot
unmount during that interval. An unresolved create remains unknown, not an
invitation to recreate or adopt a container.

The registered container starts only the existing non-root service owner under
network quarantine. Its actual PID 1, UID/GID 65532, private mount namespace,
read-only inner-model device/inode, prepared receipt and absolute hard stop
are joined through a single Unix seqpacket connection in the existing private
input mount. The host binds peer credentials to the exact Docker PID using a
pidfd, retains the actual consumer namespace, and binds its cgroup device/inode.
The namespace must differ from the host/daemon namespace. No root-only traversal
probe substitutes for this actual non-root consumer.

The consumer runs the existing full `nib model check-local` before any engine
launch. A closed `verified` acknowledgment binds both contract digests, the
prepared receipt, original manifest, model identity, namespace and numeric
83-file/510,311,045,295-byte result. The host checks it against registration,
admission, host verification, joined peer and fresh Docker identity. A container
ID or a merely plausible acknowledgment alone is insufficient. Only then does
the existing owner release quarantine and send the exact hold release. EOF,
wrong/extra packets, expiry or cancellation reject; there is no reconnect.
Existing GPU-service limits remain unchanged; the 2-CPU/8-GiB limit applies to
host mount workers, not to the existing service/consumer container.

The time budget must include both the host and actual-consumer full rehashes,
image admission and engine startup inside the existing startup cutoff. The
mount worker's nominal upper bound does not extend that cutoff or renew the
original wall/monotonic service lifetime. CPU caps do not cap NFS traffic;
separately approved pressure monitoring remains necessary.

The private consumer contract also carries the host's original absolute
`startup_deadline`, boot ID and actual time-namespace device/inode. Before
preparation, the non-root consumer verifies the same boot and
`/proc/self/ns/time` identity, establishing the shared monotonic clock, then
clamps its `Contract.startup_deadline` to the smaller existing cutoff. It
rechecks that binding before joining the hold. Unknown or different time
namespaces reject; there is no assumed wall-clock conversion or fresh startup
period at handoff. Preparation, full consumer hashing, release, engine startup
and the existing supervisor all receive the clamped cutoff. No hard-stop or
overall lifetime value is extended.

Startup checks run again after intervening durable IO/readback and immediately
before mount, Docker create/start, quarantine release and hold release. The
unchanged mount/create/policy helpers run under the existing main-thread
deadline alarm, so their own intervening IO cannot re-arm the startup window.
An expired intent/verification record remains evidence, not mutation authority.
Cleanup and observation are deliberately still permitted after startup expiry.

**Deployment budget is still blocked.** This branch retains the existing
1800-second startup budget. Both full hashes plus engine startup must fit it;
this change makes no throughput claim that they can. A separately reviewed,
explicit original-only startup budget with remaining-hard-stop checks is
required before the proposed deployment. No pending budget branch is integrated
here, and a budget change must still preserve the one host-to-consumer cutoff.

## Closure and failure retention

After release, the host observes both the bound pidfd and the hold socket.
Active-state EOF or unsolicited data fails closed, including simultaneous PID
exit without a terminal acknowledgment. The consumer sends one fixed
`nib.original-view-terminal/v1` packet bound to the exact consumer-contract
digest on its original authenticated connection. It sends only after the
supervisor reports ready, retired and confirmed cleanup with every expected
role drained/reaped, the input snapshot closes successfully, and the lifecycle
receipt is published. Cancellation, incomplete cleanup or expiry cannot send
this acknowledgment. It grants no new serving authority.

The exact terminal packet starts a wait of at most 60 seconds, capped by the
original owner deadline. Only then may socket EOF precede PID exit, including
the consumer's explicit `finally` close or interpreter teardown. Wrong-contract,
duplicate or other data is rejected even with simultaneous PID exit. EOF does
not prove PID or descendant absence, and neither EOF nor PID exit renews the
terminal wait. The consumer also checks its unchanged hard stop before and
after the bounded terminal send.

On normal consumer exit, the watchdog retains the container/network for final
parent readback. The parent sends fixed `done` over its exclusively inherited
life pipe only after validating the lifecycle receipt and fresh exited/zero
Docker state. The watchdog independently verifies that state and PID exit
before accepting it. Parent readback must finish within the same terminal wait
and original owner deadline; neither is renewed. Parent EOF,
expiry or failed readback cleans up independently and cannot report success.
This is an authenticated parent acknowledgment, not a reused container name or
an unauthenticated terminal snapshot. There is no waiting for a dead parent.

The watchdog closes admission and the hold connection before teardown, then
uses the existing owned-container quarantine/cleanup. Before one ordinary
unmount it requires exact daemon/namespace continuity, resolved registration,
fresh container absence, an empty Docker inventory, and observed consumer PID
exit plus empty/absent bound cgroup including descendants. A start intent
without an observed peer remains unknown even if Docker later reports absence.
The retained consumer namespace descriptor is released only after these gates.
The unmount worker rechecks the original mount identity; no forced/lazy detach,
foreign mount adoption, or repeated unmount is permitted.

Record-write/fsync failure closes admission in memory and does not stop
independent process/cgroup observations. A failed closure record cannot
authorize unmount. If any capture, process, journal or mount state is uncertain,
the existing watchdog remains alive and continues observations after parent
exit; a parent timeout does not kill it or start a fallback owner. Its private
`watchdog.json` and worker ownership records bind the exact attempt/source.
Failures and original flags are retained, never relabelled as successful
cleanup because a later observer saw absence.

No crash-recovery/adoption command is provided. Missing ownership, partial
publication, killed watchdog, changed daemon, blocked NFS or lost consumer
identity requires explicit operator monitoring and unresolved-state handoff.
There is no automatic retry, recovered serving lease or cleanup permission
based on existence. An externally acknowledged handoff is an operational gate,
not a protocol implemented here. Ordinary unmount never removes persistent
partial or completed NFS wrappers.

## Static and synthetic verification

[Focused regressions](../../scripts/test_original_view_handoff.py) cover exact
staging evidence, resource failures, substituted wrapper/model identity,
remount-device semantics, atomic exclusive records, registration, consumer
ack/release joins, prepared-source mismatch, hash-before-engine ordering,
ordinary unmount binding and retention despite failed records/observations.
They also cover expiry during intent/fsync/readback, the actual consumer-entry
cutoff propagation before preparation and into the supervisor, boot/time
namespace mismatch, exact consumer bytes in original image preparation, the
end-to-end synthetic watchdog/parent acknowledgment protocol, normal-exit
readback races, terminal-before-PID EOF, wrong/duplicate/late terminal packets,
and premature post-release socket EOF/data. No real image is built by
these tests; preparation proves packaged source bytes, not runnable-image or
hardware success.
The image-archive verifier's authenticated `selected_service_process` pin is
updated to the measured source bytes; its existing pre-import tamper regression
also covers that module. Integration must remeasure any changed transitive
preparation sources without relaxing authentication.
All privileged/system observations are fakes; local temporary files and record
locking exercise existing helpers. They are not NFS, namespace, GPU or Docker
validation. Legacy suites must remain green without weakened assertions.

The author has not executed tests for this draft. The parent owns the test
window and approved offline, resource-bounded Docker invocation, with read-only
source, private temporary storage, no network, Docker socket or GPU. Inside that
container, from the repository's `scripts` directory:

```sh
timeout 180 python3 -B -m unittest -v test_original_view_handoff
timeout 300 python3 -B -m unittest -v \
  test_selected_service_owner test_selected_service_network \
  test_selected_service_process test_selected_service_image \
  test_selected_service_smoke test_selected_service_graphs \
  test_selected_service_lifetime test_selected_service_weekly \
  test_original_service_runtime test_original_weight_staging \
  test_selected_image_archive
```

The parent reported 82 focused/archive tests passing in 14.094 seconds at
`d2ab5104e71d4471407724933524b14b900951f0`. That result predates the terminal
handshake fix and is not a passing-test claim for the corrected head.

Use the already approved cached runtime and record its actual identity; this
document supplies no guessed image pin. Independent final-head review and all
mandatory CI remain required before merge. Passing synthetic tests cannot
authorize a mount, new image build/publication, live inference or cutover.
