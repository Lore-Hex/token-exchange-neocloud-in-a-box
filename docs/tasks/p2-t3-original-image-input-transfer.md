# P2.T3 original image input-set transfer adapter

Software only. No transfer, target admission, import, runtime, deployment or
confidential-computing result is established here. This does not complete P2.
The existing [single-component receiver](p2-t3-original-image-input.md),
[importer](p2-t3-original-image-import.md), image, preparation, verifier and
materializer are unchanged. Independent exact-head review and parent-run tests
are required before any operational consideration.

## Closed interface and trust boundary

`scripts/original_image_transfer.py:send` accepts original manifest bytes and
their independently authenticated SHA256, original transport specification
bytes and their independently authenticated SHA256, seven local component
paths, a fresh durable local attempt directory, and an absolute caller-clock
deadline. It has no CLI, default transport, endpoint, credential discovery,
approval writer, retry, resume, deletion or importer invocation. It never
creates an operational manifest, packages actual inputs or discovers pins.

The local caller must already trust the exact sender source before importing
it. A pin read from the same untrusted manifest/sidecar is not authentication.
`authenticated` authenticates the manifest against the caller's independent
pin, then the complete six-file code graph before importing the new remote
entrypoint or runner. Frozen helper hashes are also compiled into that
entrypoint. Code producer and d18 image producer are distinct fields.

The explicit `nib.pinned-posix-command-transport/v1` specification contains only
`schema`, `argv`, `executable_sha256` and `remote_python`. Its absolute executable
is rehashed before each invocation; the last argv element must be the literal
`{command}`. That element is replaced with one POSIX-shell-quoted invocation of
the explicitly supplied Python path, using `-I -S -B -c`. Nothing selects SSH
or supplies its options. Only a fixed minimal environment is inherited; no
agent socket, user HOME/configuration, proxy or credential environment is used.

**Blocking transport contract:** independent operator review must establish
strict authenticated destination/host-key/configuration binding, exact remote
command execution, pipe stdin without a PTY, transparent bounded stdout/stderr,
and propagation of the remote process's actual exit status. An asynchronous
wrapper returning success after launch is unsupported. The target's OS,
interpreter, standard library and POSIX shell must already be trusted. This
adapter cannot establish those facts from arbitrary argv or a receipt, and
contains no guessed transport command or ambient SSH fallback. Mutable
transport configuration/executable aliases and concurrent same-UID writers
must be excluded externally.

The initial launcher is a literal program sent through that trusted command
channel, not an uploaded executable selected by the peer. Its compressed inline
capsule contains the independently authenticated code bytes and pins; all bytes
are authenticated before any adapter import/exec. The payload pipe never carries
executable bootstrap code. The fixed command ceiling is 64 KiB and capsule
expansion ceiling 1 MiB. Exceeding either fails closed, without another upload
mechanism. These are conservative implementation ceilings, not approval budgets.

## Manifest and input joins

Canonical compact JSON plus newline, at most 65536 bytes, has exactly:
`schema`, `attempt_id`, `producer`, `image_producer`, `seconds`, `destination`,
`code`, `components`, `preparation_sha256`. Schema is
`nib.original-image-input-set/v1`. `attempt_id` is a caller-supplied 32-hex value;
both fresh directory basenames equal it. Image producer is exactly
`d18aee4ce01062134d0e893c72555ef104a9ed84`; no rebuild or source substitution.
`code` contains only the sender, remote entrypoint, receiver, verifier,
materializer and snapshot runner, each with an exact independently reviewed SHA.

The seven ordered component roles are `source-tree`, `source`,
`preparation-tree`, `preparation`, `build`, `verification`, `image`. Each descriptor
has only positive integer `bytes` and lowercase SHA256. Inventory caps are
1 MiB each, source/preparation tar caps 80 MiB each, build 16384 bytes,
verification 65536 bytes and opaque image 10 GiB. Every component uses the
existing receiver, requires exact EOF, and gets its own exclusive directory.
The image is streamed and rehashed, never extracted or interpreted here.

`source-tree` is the exact NUL-delimited output of
`git ls-tree -rlz --full-tree d18aee4ce01062134d0e893c72555ef104a9ed84`.
Its measured SHA256 is
`78e561ec5fbf998ab211942de3c7f8e7ae6d4ed37b83f86b73461ba5204b0322`.
A focused Git-metadata test checks that real commit relationship. The uncompressed
source tar must contain exactly that inventory with matching Git blob identities.
No Git checkout wrapper is called; its independent deadline would be incorrect.

`preparation-tree` uses the same bounded regular-file inventory encoding solely
as input to the existing materializer. These blob rows are **not Git provenance**.
It covers the entire original preparation including its unchanged `receipt.json`.
After materialization the frozen d18 `source_inputs` authenticates every
preparation SHA256/size, original model/profile, prepared source, build record
and manifest join. Permitted empty wheel markers remain zero bytes and require
the original validator's empty-file/prepared-source checks. A descriptor cannot
substitute a nonempty-file rule for those files.

Separate envelopes preserve the materializer's 4096-file, 16-MiB member and
64-MiB expanded-tree limits. Its path/type/duplicate/case-collision checks are
reused unchanged. New roots are exclusive; files are fsynced/reopened by the
materializer, and directories are fsynced by the adapter. No general extractor
or limit increase is introduced. Actual package sizes/hashes are still operator
inputs, not inferred from declared preparation totals.
Bootstrap and materialization explicitly create every intermediate directory
with mode 0700 using descriptor-relative, no-follow traversal. Each existing
intermediate must also pass the exact owner/mode check; nothing is chmodded or
adopted to repair broader permissions. The authenticated inventory's parents
exist before the pinned materializer runs, so its `Path.mkdir(parents=True)`
cannot create intermediate 0755 directories under umask 022. The process umask
is never changed. Directory enumeration errors at the root or any nested level
are fatal: incomplete recursive fsync cannot produce a terminal acknowledgment.

The final join compares the original verification record's producer, helper
pins, limits, profile, success/non-runtime flags, source and expected inputs.
The sender also joins its terminal acknowledgment to the exact local original
verification/build records. This is narrower than importer admission: no OCI
parse or daemon operation occurs. Later admission must use the unchanged
importer and revalidate the full image. Its archive input may point directly
to `image/payload`; no second image copy is required.

## Ownership, deadline and failure semantics

Local exclusive intent is durable before even the clock probe. Remote exclusive
set intent is durable before persistent code or input writes. Every phase has
an exclusive intent before work and an immutable acknowledgment afterward.
Each invocation carries the previous acknowledgment hash over the authenticated
channel; the sender advances only after both the exact acknowledgment and
observed successful remote exit. Local phase records explicitly record that
exit-zero observation. A remote record alone is never permission to advance.
The bootstrap acknowledgment also binds the immutable intent hash, including
its directory identity. Subsequent commands carry that same hash, preventing
adoption of a substituted directory or a rewritten deadline/ownership record.

Linux boot ID and time-namespace identity bind receiver-clock values. One clock
probe precedes bootstrap; the receiver deadline is its monotonic sample plus
the sender's remaining budget *after* the round trip, conservatively discarding
the round-trip time. The unchanged deadline and clock binding accompany every
command and durable record; no per-file budget is rearmed. The overall supplied
budget must fit the existing receiver's 1800-second ceiling. That ceiling is
not an operationally approved duration. Sender timeout and final close checks,
receiver close checks, and bounded acknowledgment writes all reject late success.

The source/preparation materializer and validators perform synchronous filesystem
calls. Python cannot preempt a filesystem stall. Local transport process-group
cleanup does not establish remote process absence. No cgroup/resource admission,
global host ownership, remote watchdog or uncertain-state monitor is fabricated.
Those remain blocking external controller prerequisites. A failed/disconnected
call leaves durable claims, received/partial files, materialized trees and any
already-written records untouched. No automatic replay, adoption, removal or
fresh-directory reset is available. Keep the configured durable attempt roots;
changing parents or IDs to escape uncertainty is prohibited operationally.

All receipts retain false `remote_transfer_complete`, `admitted`, `imported`,
`image_loaded`, `runtime_started`, `deployable`, `confidential` and
`cleanup_confirmed`. `inputs_joined` describes only the bounded input checks.
Original image/preparation/source evidence is never relabeled or rewritten.

## Verification and remaining gates

Author has not run tests, Docker, transport, packaging or live operations.
Preserved parent baseline: frozen `5f9eb4f` ran 31 tests in an isolated offline
CPU container, **30 passed / 1 error in 0.566 s**. The nested empty-marker test
failed the private-directory check because intermediate directories were 0755
under umask 022. Independent exact-`5f9eb4f` review also reported two P2 findings:
the same permissions defect during canonical-order bootstrap installation, and
`os.walk` silently suppressing directory-enumeration errors during fsync. This
candidate adds explicit private-parent creation and fatal traversal errors, with
canonical-order/default-umask, failure-preservation and root/nested enumeration
regressions. The baseline failure and blocked review are not relabeled as passes.
The parent then tested exact `fb7901122737b0ec7f431d2ff496e922b4ce9551`
in its pinned offline, read-only-source container: **41 tests, 38 passed /
3 errors in 0.915 s**. The mode/enumeration regressions passed; all three real
local-transport cases rejected before their expected terminal artifacts existed.
Independent exact-head review remained **BLOCKED** solely on the temporary
transport script being executed directly from confirmed `noexec` scratch;
no additional production defect was reported. Early `EACCES` is consistent
with that prerequisite mismatch, but the original run did not retain the
underlying exception, so that exact error remains an explanation pending
diagnostic confirmation, not a retrospectively observed errno.

The `72a3fce` test-only follow-up pinned and invoked the existing Python
interpreter with the temporary fixture as a non-executable input file. Scratch remains `noexec`;
production transport validation and helpers are unchanged. Synthetic diagnostics
retain exception classes/errno and source frame locations (not messages, argv,
payloads or full paths), local phase progress, and actual transport start/exit
observations. Negative cases must reach their intended fault boundary before
checking terminal artifacts, rather than accepting any early rejection. A
separate injected pre-child error covers diagnostics without pretending that
synthetic errno is the original run's observed cause. Both failed parent runs
remain failures.

Parent verification of exact `72a3fcef897419babb3d2f68554b5cafa5bd32e4`
passed **43 focused tests in 2.510 s**, including the real roundtrip. A separate
seven-suite run retained **209 tests, 208 passed / 1 failure in 18.470 s**:
the unchanged history-maintenance assertion expected `--no-detach`; investigation
of the bundled Git environment was pending, without weakening that assertion.
PR 498 Check runs `40883783957` and `40883723463` both subsequently **FAILED**.
The inspected failed log reports **4130 tests in 424.081 s, 4 failures and
43 skips**. All four local roundtrip cases failed the sender's transport
executable metadata predicate before any transport event. The failing executable
was the hosted managed Python, but its exact owner/mode/execute/size fields were
not captured. No particular rejected field is inferred from this observation.

The current test-only follow-up instead resolves the existing `/bin/sh`, hashes
that exact executable, and uses fixed `exec "$@"` with an explicit shell `$0`
and individually passed Python/driver/transport arguments. There is no dynamic
shell interpolation, eval, background launch, status masking or transport
fallback. The driver remains 0600 on `noexec` scratch. Both the production
owner/mode/executable/16-MiB checks and all deadlines, cleanup and error behavior
are unchanged; the existing Python/OS remain trusted test-runtime prerequisites,
not an operational SSH configuration. Diagnostic snapshots before each send
include transport and Python uid, caller uid, mode, size, link count and file
type, without paths. Real-file negative tests cover writable/non-executable,
empty/oversized and wrong-hash transport rejection before execution. All four
integration cases retain real subprocess/pipe composition and no-replay checks.
No prior result is relabeled. Fresh parent tests, independent exact-head review
and green CI are required; the author has not executed this follow-up.
Parent-run focused command in an isolated offline, read-only-source,
resource-bounded, Git-capable environment:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts -p 'test_original_image_transfer.py'
```

Also run the existing `test_original_image_input.py`,
`test_selected_image_archive.py`, `test_original_image_import.py` and
`test_evidence_history.py` suites, documentation checks and normal full CI.
Unit tests use synthetic pipes/files, fake transport/exit observations and
generated preparation fixtures. The Linux-only `LocalTransportRoundtripTests`
also run real local subprocesses and pipes through a temporary POSIX command
transport: generated launcher, installed pinned helpers, component receiver,
full materialization/joins and emitted acknowledgments are not mocked.
They cover success, a nonzero transport exit after the complete final ack, and
replacement of the original attempt directory with a same-content copy after
bootstrap, plus a synthetic pre-child transport error. Failures must preserve
the original claim/artifacts and reject replay.

Those integration fixtures construct their source tar exclusively from public
d18 Git objects, and generate tiny OCI/preparation inputs with the existing test
fixtures. All files live in temporary software-test directories; no actual
operator image archive or private preparation is accessed, no operational inputs
are packaged, and nothing is published as official transfer evidence. Allow
writable scratch for the full public source fixture and received/materialized
copies, plus subprocesses, in the parent's bounded offline environment.
No SSH, network peer, Docker or import is invoked. These tests do not qualify an
operational transport, target, throughput or resource policy.

Remaining human/capacity gates include actual rolling spending reconciliation,
operation approval, exact target and transport trust, bandwidth/resource policy,
landing/store/quota/inode capacity, daemon/writer exclusion, no-overlap window,
health/pressure monitoring and acknowledged uncertain-state handoff. No change
to the old serving owner, profiles, image artifacts or runtime is authorized.
