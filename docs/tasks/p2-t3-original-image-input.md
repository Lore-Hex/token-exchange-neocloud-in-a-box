# Original image input component receiver

Software-only library: [original_image_input.py](../../scripts/original_image_input.py).
This is not an SSH uploader, host controller, archive extractor or deployment CLI.
It does not call Docker, import an image, mount weights or execute input content.
The [existing importer](p2-t3-original-image-import.md) and its pinned image/helper
sources remain unchanged. No live transfer or hardware result is claimed.

## Caller contract

`receive(input_fd, destination, raw_request, request_sha256, *, deadline)` accepts
one opaque component from a POSIX read-only pipe. A piped stdin descriptor is
supported; regular-file stdin, sockets and write/read-write descriptors reject.
The caller must exclusively own the read side, including every duplicated
descriptor: this library temporarily sets its shared open-file description to
nonblocking and restores the prior mode. It duplicates, but never closes, the
caller's descriptor. It does not close the producer's write end.

Authenticate `request_sha256` through a separate trusted channel before calling.
A hash calculated from an untrusted request beside its payload is not authority.
The canonical request is sorted compact JSON plus newline, with exactly:

- `schema`: `nib.original-image-input-request/v1`.
- `attempt_id`: fresh 32-character lowercase hexadecimal identity.
- `producer`, `image_producer`: exact 40-character lowercase commit identities.
  These are caller assertions, not Git ancestry verification or model validation.
- `receiver_sha256`: independently pinned hash of the receiver source; it must
  match the file in the authenticated checkout used by the caller.
- `bytes`: exact nonnegative integer payload length; booleans are not integers.
- `sha256`: exact lowercase 64-character digest. Zero bytes require SHA256(empty)
  and observed EOF; this supports authenticated empty preparation marker files
  without claiming to validate the surrounding preparation inventory.
- `seconds`: explicit positive integer maximum budget, with no default.

`deadline` is a finite absolute deadline on the **same process's monotonic clock**,
not a remote monotonic timestamp or a new duration at each phase. At entry it must
be in the future and no later than entry time plus request `seconds`. All pipe
waits, writes, rehashes and publication checks use that unchanged deadline.

The existing pinned [archive verifier](../../host/verify-selected-image-archive.py)
provides strict JSON, private directory/file readers, identity checks and durable
no-replace JSON publication. Its 10 GiB input ceiling, 1800-second ceiling and
65536-byte metadata ceiling are reused as implementation maxima. The request's
exact byte count and caller's remaining budget may be smaller; these ceilings
are **not** an approved upload rate, duration, capacity reservation or resource
allocation. Data is streamed in chunks of at most 1 MiB. No large input is buffered
in memory, parsed as an image, or extracted.

## Local ownership and failure

The destination must not exist, even as an empty directory or dangling symlink.
Its parent must be an existing real current-user-owned 0700 directory. Path
walking rejects symlinks. A fresh 0700 directory reserves the attempt; all later
attempts at that path reject. The caller must separately prevent reusing the same
attempt identity at another path or in another invocation/controller.

The parent directory and an exclusive 0600 `intent.json` are fsynced **before**
creating/writing `payload`. The intent binds the exact request/hash, helper pin,
deadline and directory device/inode/ownership/mode. The payload is created via
`O_EXCL | O_NOFOLLOW` relative to the held directory descriptor. Directory identity
is rechecked after durable I/O; no existing file, partial directory or receipt is
adopted, overwritten, resumed or deleted. There is no automatic retry.

Pipe reads use readiness waits and nonblocking reads, with deadline checks on
both sides. An idle producer, delayed EOF after the exact byte count, early EOF,
extra bytes, or a wrong digest cannot succeed. Successful copying requires EOF,
file/directory fsync and a reopened full hash of the exact original output inode.
Short writes are handled explicitly. Final receipt and intent bytes are read back.

Failures raise only `original-image-input-rejected`, without printing input data,
paths or raw exception text. Interruptions also preserve everything already
created. A failure before payload creation may leave only the reserved directory
or intent; a later failure leaves a partial or even fully written payload. Do
not interpret absence of a receipt as permission to retry. Failed fsync cannot
promise crash durability, and partial payload bytes are not certified durable.

`receipt.json` is a **component observation**, not a PASS/admission authority.
It records local EOF and reopened byte/hash verification. A late publication,
readback, deadline or fsync failure can leave a receipt even though the call
raised; receipt existence alone is insufficient. Require successful return and
independent controller reconciliation before using any component. All receipts
keep `remote_transfer_complete`, `admitted`, `imported`, `image_loaded`,
`runtime_started`, `deployable`, `confidential` and `cleanup_confirmed` false.
No process/daemon absence, peer acknowledgment or host ownership is proved.

## Deliberately missing

This component does not join multiple source/preparation files, validate a Git
tree or image model/profile, authenticate a peer, bootstrap remote code, manage
SSH keys or process lifetimes, allocate cgroups, rate-limit a network, reserve
disk, monitor service health, or obtain spending/action approval. The parent
controller must authenticate code and inputs, enforce exclusive descriptor and
attempt ownership, and preserve independent failure/unknown-state evidence.
Regular-filesystem writes/reads/fsync can block in the kernel; checks reject late
returns but are not preemptive termination. Only stalled **pipe input** is bounded
here. External lifecycle/resource monitoring and uncertain-state handoff remain
required. No existing serving process or shared Docker daemon may be moved into
an assumed receiver resource group.

## Focused checks

[Tests](../../scripts/test_original_image_input.py) use synthetic bytes, local
temporary private directories and pipes only. They cover zero/nonzero inputs,
strict authenticated requests, stalled streams and withheld EOF, shared deadline
rechecks, exclusive paths, descriptor identity changes, short writes, durability
failures, preserved partials and non-admission receipts. The author did not run
tests, Docker, remote commands or live scans for this change.

Parent-only command inside the separately authorized isolated offline test
environment, with a writable bounded temporary directory and read-only source:

```sh
python3 -B -m unittest discover -s scripts -p test_original_image_input.py -v
```
