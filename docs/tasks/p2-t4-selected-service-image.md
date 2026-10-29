# Selected service image preparation

This software-only follow-up to the [selected service owner](p2-t4-selected-service-lifecycle.md)
adds concrete offline preparation and local image-build commands in
[selected_service_image.py](../../scripts/selected_service_image.py). It has
not built an image or run a container. Public serving, native cache behavior,
nonroot engine execution and hardware validation remain unproven.

## Local provenance identity

The [failed original archive verification](../../evidence/p2/2026-10-29-original-image-archive-failure/README.md)
exposed an empty in-toto subject in a build made without an image name. Builds
now supply one fresh UUID-based local reference under
`nib-build.invalid/selected-service`, then require exact reference readback.
BuildKit derives default attestation subjects from image names in
[`writer.go` at c19b15f](https://github.com/moby/buildkit/blob/c19b15f5775bd222f93b9741ade08fe77c3d3de7/exporter/containerimage/writer.go),
checked October 29, 2026. The reserved name is local metadata, not a registry
destination: no push, login or additional network access is introduced.

Provenance remains enabled, and the archive verifier still rejects empty or
incorrect subjects. Consumers and exports use the exact returned image ID,
never the mutable local tag; the exported archive must remain untagged. Local
image references are retained, not automatically removed or broadly pruned.
Synthetic tests verify the build command and reject wrong reference readback;
a separately reviewed real rebuild and complete archive verification are still
required. BuildKit provenance is not confidential-computing attestation.

## Implemented packaging path

Preparation accepts an existing exact-receipt-validated **non-owner** selected
lab bundle: B300, ordinary nonconfidential operation, DP2/TP4, `tau8k-eager`
by default or the explicit [graph profile](p2-t4-selected-service-graphs.md).
The shared lab validator checks receipt filenames, bytes, hashes and raw OCI
metadata before the service adapter derives a new payload. The original lab
bundle is never modified. Tau-owner and provider roles are not accepted.
Eager rejects stream cohorts; graphs require and preserve the exact sealed
C8 cohort metadata but launch only the engine, never the cohort requests.

The lab bundle must use the current reviewed host recorder and original
launcher bytes. A historical bundle can have a valid original receipt yet be
incompatible with this adapter. Prepare a fresh non-owner bundle with the
existing committed lab preparer from this exact source checkout; do not edit
an old receipt or bypass either source-identity check.

The source checkout must be clean and committed. The complete Python source
tree and e2e pins come from verified Git blobs, not an untracked directory
copy. The guard is compiled for Linux/amd64 with the locally installed pinned
Go toolchain, CGO disabled, proxy and checksum network access disabled, and
the existing bounded process-group helper. The supplied trcs executable must
match its existing exact binary pin. No operator input belongs in the context.

The generated Dockerfile applies the existing reviewed engine overlay with
its original source-bound application entrypoint. A separate image-build
permission step verifies every approved service/payload hash before making
public directories and executables `0555`, and other public files `0444`.
It does not change the public weight cache or any API/TLS input. The final
image entrypoint is the GPU runtime's CPython 3.12.3 path with `-I -S -B` and
the closed service owner; image user is `65532:65532`.

The build command snapshots the exact approved context before Docker sees
it. It checks the cached base image with the existing OCI/platform/DiffID
validator, checks local BuildKit availability, and uses a private Docker
configuration and explicit local Unix daemon. There are no build secrets,
SSH forwarding, runtime devices or image publication operations. Missing or
incompatible base images fail before the build starts. A stopped build client
does not prove daemon completion: failure records retain unknown cleanup.

Docker documents `RUN --network=none` as disabling networking for that build
step, and `--pull` as always attempting image pulls. The command disables
forced pulls and requires the exact base already cached. This is **not** a
claim that a trusted Docker daemon cannot contact a registry for metadata;
the build-step network restriction and a runtime egress policy are separate
boundaries. [Dockerfile RUN networking](https://docs.docker.com/reference/dockerfile/#run---network),
[Docker build options](https://docs.docker.com/reference/cli/docker/buildx/build/),
accessed 2026-10-24.

## Operator commands

These commands prepare software or build locally only after the corresponding
operator approval. They do not authorize a GPU run or publication. Inputs are
external local paths; no operator values are compiled into the image.

```sh
python3 -I -S -B scripts/selected_service_image.py prepare \
  --lab-bundle "$APPROVED_NONOWNER_LAB_BUNDLE" \
  --lab-receipt-sha256 "$APPROVED_LAB_RECEIPT_SHA256" \
  --trcs "$PINNED_TRCS_BINARY" --output "$NEW_PREPARATION"

python3 -I -S -B scripts/selected_service_image.py build \
  --preparation "$NEW_PREPARATION" \
  --receipt-sha256 "$APPROVED_PREPARATION_RECEIPT_SHA256" \
  --docker-host "$LOCAL_UNIX_DOCKER_HOST" --output "$NEW_BUILD_RECORD"
```

The preparation receipt and final image build record are distinct. A
successful build record is not permission to serve: `runtime_started`,
`hardware_validated`, `deployable` and `published` remain false.

## Frozen Host-Owner Interface

The separate host owner can reuse these read-only helpers from
`scripts/selected_service_image.py`. Both expected hashes must come from the
operator's approved receipts, not from recomputing an unapproved input:

```python
receipt, files, contract = image.validated_files(preparation_path, preparation_sha)
record = image.validate_build_record(
    image.read(str(build_record_path), 16384), build_record_sha,
    receipt, preparation_sha, contract,
)
```

`preparation_path` is a `Path` to the whole preparation directory;
`build_record_path` names the resulting `build.json`. The first helper verifies
the exact preparation inventory, bytes and hashes and returns its immutable
file snapshots. The second verifies the exact build-record bytes, strict
fields, preparation lineage and base binding. Neither helper contacts Docker
or requires the later host owner's Git HEAD to equal the builder's HEAD.
The host owner must separately inspect and bind the current daemon's image
and container to `record["image_id"]`; never launch by a mutable tag.

The preparation receipt schema is `nib.selected-service-image-preparation/v1`:
`git_sha`, `lab_receipt_sha256`, `prepared_sha256`, `guard_source_sha256`,
`files` (exact relative filename to `{bytes, sha256}`), and the false flags
`image_built`, `hardware_validated`, `deployable`. Files are rooted at
`context/service/`, `context/payload/`, `context/Dockerfile` and `metadata/`.
The exact `context/service/prepared.json` hash is `prepared_sha256`.

A successful `nib.selected-service-image-build/v1` record has exactly
`result="pass"`, `preparation_sha256`, `prepared_sha256`, `base_binding`,
`image_id`, true `image_built`/`build_completed`, and false `runtime_started`,
`hardware_validated`, `deployable`, `published`. `base_binding` is the unchanged
shared OCI validator's manifest/config hashes, daemon-reported base ID,
`linux/amd64` platform and explicit trusted-daemon/not-rehashed limitations.
Failure records are not accepted by the validator and cannot authorize a run.

The image is `linux/amd64`, `USER 65532:65532`, `WORKDIR /`, with no CMD or
inherited volume declarations and healthchecks disabled. Its exact ENTRYPOINT
is the following argument vector; the hash is generated at preparation:

```text
/usr/bin/python3.12 -I -S -B /opt/nib-service/scripts/selected_service_process.py
  --prepared-sha256 <exact-prepared.json-sha256>
```

Append only the existing closed owner's `--provider-config`, `--api-key-file`,
`--cert`, `--key`, `--hostname`, `--input-price`, `--output-price`, and
`--hard-stop-at` arguments. The last is the original absolute UTC cutoff;
startup cannot reset it. There is no model, upstream URL, engine flag or port
override. `/opt/nib-service` contains scripts, pins, native guard/trcs binaries
and the receipt; `/opt/nib` contains the derived lab payload. The owner must be
PID 1, without an init-wrapper process. It retains one engine at
`127.0.0.1:8000`, the guard at `127.0.0.1:8001`, and trcs TLS at `:8443`.

## Remaining Runtime Work

### Image Application Budget

The standalone image-build application now uses the same fixed 720-second
layout-plus-application budget as the CPU diagnostic. Previously it inherited
the generic tree's 300-second deadline, although the recorded
[CPU entry run](../../evidence/p2/2026-10-20-cpu-entry-125036c/output.log)
took 496,309 ms for application plus 8,562 ms of preflight, and the
[native run](../../evidence/p2/2026-10-20-cpu-native-39467970211-1/output.log)
took 671,086 ms for application. These are historical software observations,
not a successful new image build or hardware acceptance.

One deadline starts before layout verification and covers inspection, all
eight full inventory passes and the final completion check. It is not renewed
per pass and is not configurable through an environment variable or argument.
The generic tree and layout scanner retain their existing bounds; hash,
mutation, link, size, ABI and packaging checks are unchanged. Expiry at any
point rejects the application and requires discarding the modified image.
Regression tests use synthetic time and files; a fresh exact-head build is
still required before this correction can establish a usable image.

The private-container runner and external lifetime watchdog are a **separate
host-owner task**, not implemented here. They must retain the original absolute cutoff,
exact image identity, UID 65532, read-only root, 512 GiB/no swap, bounded
private scratch/cache and ordered owner drain. The public view helper's
`destination/model`, not its private parent wrapper, is the weight bind source.
Private operator inputs need separate UID-owned `0700/0600` staging, never
permission relaxation on their originals. These runner requirements are not
implemented by the build command above.

The image does not contain weights or private inputs. Bind the validated
public view at `/model` read-only. Keep `/cache` on bounded runtime-UID-owned
tmpfs (`rw,nosuid,nodev,exec,mode=0700`) and `/tmp` on separate bounded tmpfs
(`rw,nosuid,nodev,noexec,mode=1777`). The retained lab limits are 16 GiB cache,
256 MiB temporary storage, private IPC with 2 GiB shared memory, 1024 PIDs and
64 CPUs. Their suitability for nonroot/read-only native compilation remains
unproven until a real prepared-image test. Docker documents tmpfs size, mode,
UID/GID and execution options in its
[tmpfs mount reference](https://docs.docker.com/engine/storage/tmpfs/), accessed
2026-10-24. Do not relax original public-cache or operator-key permissions.

The first runtime profile must use Docker `--network none`, with no published
ports: only the private loopback device is provided. Checks against the same
retained engine and TLS listener then run inside that owned container. A
Docker `--internal` bridge is insufficient for a strict no-egress claim because
host gateway services remain reachable. Public TLS ingress therefore remains
a separate reviewed networking gate, never a host-network shortcut.
[Docker none networking](https://docs.docker.com/engine/network/drivers/none/),
[Docker internal networks](https://docs.docker.com/reference/cli/docker/network/create/#network-internal-mode---internal),
accessed 2026-10-24.

## Focused Verification

```sh
cd scripts
python -B -W error::ResourceWarning -m unittest -v test_selected_service_image
```

Tests use synthetic binaries and mocked compiler/Docker calls. They cover
original payload preservation, exact context closure, pinned trcs rejection,
offline guard compilation parameters, public versus private file permissions,
base-image preflight, build completion ambiguity, and final image user,
entrypoint and base layers. They neither build an image nor demonstrate
nonroot/read-only native execution. A real prepared-image proof and independent
final-head review remain required before any bounded hardware trial.
