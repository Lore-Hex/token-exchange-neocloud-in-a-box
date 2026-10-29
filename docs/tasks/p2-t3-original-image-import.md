# Original image import boundary

Software candidate only. No import, transfer, Docker invocation or runtime is
evidence for this change. Existing archive-verification PASS evidence does not
authorize importing that image. Consumer/mount integration is a separate task:
consumer changes require a fresh preparation, build, archive and verification,
plus an independently reviewed verifier/helper repin when their sources change.
Never substitute an older verified image to bypass that requirement.

## Closed reuse

[import-original-image.py](../../host/import-original-image.py) pins the existing
[archive verifier](../../host/verify-selected-image-archive.py) and therefore its
fresh-instance [Qwen archive parser](../../scripts/pilot/export.py) and complete
preparation-helper graph. Qwen's save/load/cleanup orchestration is not reused:
its same-daemon proof and build-tag cleanup are not this import operation.
Neither Qwen nor the builder/verifier is changed. No new OCI parser is added.

The importer requires the exact private verification receipt hash and its
original-only PASS, worker-exit, profile, producer, helper and limits fields.
It independently rejoins the preparation/build via the pinned verifier and
rehashes/revalidates the entire archive, blobs, provenance and ordered DiffIDs.
Expanded bytes must equal the receipt, including repeated layers. The existing
parser's JSON callback provides the authenticated image config for readback;
no second graph interpretation or generic parser override is exposed.

Validation has the verifier's 10GiB archive, 64GiB expanded, 1800-second wall/CPU
and 2GiB address-space bounds. This is substantial read/CPU work on the eventual
target and must be admitted separately, with no overlapping scanner/build/load.
Only software/fake tests may run before that operational review.

## Target and capacity

The closed target is native Linux, root, a local `/run/docker.sock`, Docker
29.1.3 with the containerd snapshotter, and `/usr/bin/docker`/`/usr/bin/dockerd`.
An independently reviewed private request pins boot ID, daemon PID/start ticks,
cmdline hash, both executable hashes, daemon ID and socket device/inode. The
daemon must share the import owner's PID/network/mount/user namespaces. Both
daemon and socket identities are rechecked; no endpoint/context fallback,
remote Docker transport, package installation or credential inheritance exists.

The request names both Docker and containerd data roots. The operator must first
establish that they are the actual daemon stores, including split containerd
storage. The software checks real, root-owned non-writable-by-others directories;
Docker's reported root must match. It does not infer the containerd store from
free space on `/` or promise that an arbitrary directory is the real store.
Unknown storage mapping blocks operational approval.

Each declared store must have at least:

`archive bytes + 2 * expanded ordered-layer bytes + 10 GiB reserve`

and 262144 free inodes. No credit is taken for shared layers or existing images,
even when stores share a filesystem. These are conservative admission estimates,
not a bound enforced on dockerd and not an import-space guarantee. The actual
archive is already present; this tool performs no transfer. Concurrent disk
consumption and daemon CPU/memory/I/O pressure require an independently approved
exclusive operation window and external monitoring. This tool cannot impose
cgroup limits on the shared daemon without affecting unrelated workloads.
Final readback also requires the same storage directory identities and the
10GiB/free-inode reserve still available. It never frees space by deleting data.

The old image must already exist by its exact approved ID. The incoming archive
IDs must not appear in the bounded image inventory. An existing incoming image
is rejected, never adopted as proof that an uncertain earlier load completed.
Only `info`, `image ls`, exact `image inspect`, and one untagged `image load`
are used. No tag, pull, build, container start, image deletion or prune exists.

## Durable ownership

The host-wide `/var/lib/nib-original-image-import` must already be a real,
root-owned 0700 directory and empty for `start`. The importer creates immutable
0600 request/claim/ownership records, fsyncing files and directory before work.
A new detached owner is bound by boot/PID/start ticks/session/argv hash before
the pipe permit. A boot-bound 30-second launch expiry prevents delayed dispatch.
No caller/SSH lifetime controls the import owner. A durable load-intent precedes
the only mutating Docker command. The exact load client identity is recorded;
an interruption in publication leaves the claim unresolved, not replayable.

The claim is never cleared automatically, including after success. A second
start cannot run in the same host-wide state directory. Missing records,
owner loss, disk-full errors or unknown daemon completion require operator
investigation; changing directories or deleting a claim is not a retry policy.
Only explicitly reviewed terminal-state handling could authorize another attempt.

`cancel` persists an abort request, serialized with dispatch. Before dispatch it
prevents the load. Once dispatch has won that lock, cancellation does not kill
the load client or claim to cancel dockerd. A busy dispatch lock fails closed;
the operator can retry the cancellation request, never the import. The owner
continues observing its child. A 1800-second load deadline is persisted as a
failure boundary, not represented as a daemon kill or completion. A late exit
can establish completion, but cannot turn the exceeded-budget attempt into PASS.
Status is read-only and never signals, polls Docker or adopts a process/image
by name. An exited nonzero client leaves daemon completion unknown even when
its process is gone. A successful client response is followed by bounded exact
target/readback checks; an owner crash without that result stays unknown.

PASS requires exact index/image ID, Linux/amd64, the full authenticated Config
including labels, complete ordered RootFS DiffIDs, no image tags, and unchanged
old-image identity/config/tags/digests. The archive's raw config digest is kept
as `archive_config_id`; Docker inspect does not rehash raw config bytes, so
`daemon_config_bytes_rehashed` remains false. All runtime, deployable, hardware,
published and confidential flags remain false. No cutover/rollback is attempted.
Config comparison uses a closed, type-checked default table from locally cached
Moby v28.1.1 commit `01f442b84d6a669c1e335b800d4670997cd5aa93`:
[`container.Config` field types](https://github.com/moby/moby/blob/01f442b84d6a669c1e335b800d4670997cd5aa93/api/types/container/config.go)
and [OCI-to-Config conversion](https://github.com/moby/moby/blob/01f442b84d6a669c1e335b800d4670997cd5aa93/daemon/containerd/imagespec.go).
Only these named defaults are equivalent to an absent field:

- Empty strings: `Hostname`, `Domainname`, `User`, `Image`, `WorkingDir`,
  `MacAddress`, `StopSignal`.
- Boolean false: `AttachStdin`, `AttachStdout`, `AttachStderr`, `Tty`,
  `OpenStdin`, `StdinOnce`, `ArgsEscaped`, `NetworkDisabled`.
- Null or empty string lists: `Env`, `Cmd`, `Entrypoint`, `OnBuild`, `Shell`.
- Null or empty maps: `ExposedPorts`, `Volumes`, `Labels`. Nonempty port/volume
  maps require empty-object values; label values must be strings.
- Null pointers: `Healthcheck` and `StopTimeout`. An empty healthcheck object
  or zero timeout is an explicit value, not an absent-field default.

This is a conservative legacy serialization compatibility table, not a claim
that the pinned Docker 29.1.3 target emits every legacy field. Unlisted fields,
including unknown false/null/empty values, remain in canonical JSON comparison;
new or missing fields fail closed. Remaining values, including nested values,
compare by canonical JSON, so booleans and integers are never interchangeable.
No label or nondefault field is excluded. No other readback checks are relaxed.

## Private request and invocation

Requests are canonical sorted compact JSON with a trailing newline, bounded to
64KiB, in a private 0600 file under a real 0700 parent. Closed fields:

- `schema`: `nib.original-image-import-request/v1`.
- `operation`: fresh 32-character lowercase hexadecimal UUID.
- `producer`, `importer_sha256`: reviewed importer commit and exact file hash.
- `image_producer`: exact preparation/verifier producer commit.
- `verification`, `preparation`, `build`: objects with absolute `path` and
  exact `sha256`. `archive` adds the exact integer `bytes`.
- `preserve_image_id`: exact existing image ID, never a tag.
- `target`: `boot_id`, `daemon_pid`, `daemon_start_ticks`,
  `daemon_cmdline_sha256`, `daemon_exe_sha256`, `docker_cli_sha256`, `daemon_id`,
  `docker_root`, `containerd_root`, `socket_device`, `socket_inode`.

After independent source/CI review and separate operation authorization only:

```sh
python3 -I -S -B host/import-original-image.py start \
  --request /absolute/private/request.json --request-sha256 "$REQUEST_SHA256"
python3 -I -S -B host/import-original-image.py status --request-sha256 "$REQUEST_SHA256"
python3 -I -S -B host/import-original-image.py cancel --request-sha256 "$REQUEST_SHA256"
```

These examples supply no real target, artifact, identity or authorization.
Private state is not public acceptance evidence. Raw paths, labels, Docker
diagnostics, socket payloads and exception text are not copied to public logs.

## Verification

Focused tests are fake-only; an external process call is a test failure. The
parent runs them in an isolated network-disabled CPU-only test container, never
against Docker or a production proc/socket view:

```sh
python3 -B -m unittest discover -s scripts -p test_original_image_import.py -v
```

Tests cover exact request/receipt joins, existing parser observation, repeated
layer capacity accounting, inode/byte admission, identity/config/label/old-image
mismatches, closed Config defaults, unknown empty keys, boolean/integer type
substitution, failure before dispatch, cancellation after dispatch, timeout,
client-journal failures, unknown completion, PID/boot reuse and no adoption.
No tests or live operations were run by the implementing agent.
