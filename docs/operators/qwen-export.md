# Local Qwen image export

P3.T8 adds an optional local artifact to the existing committed-source
[Qwen proof](qwen-pilot.md). It does not build a second image or run a second
conformance suite. Export is implemented and covered by synthetic unit tests;
the real Docker proof/export on the combined P3.T8/P3.T9 head is still pending.
No exported artifact or successful Docker reload is claimed by this document.

## Produce a bundle

Use a clean, committed checkout and the previously downloaded, pinned inputs.
Requirements are unchanged: local Docker with BuildKit, Python 3.12+, and the
toolchain from the proof. The exporter supports macOS and Linux filesystems
with atomic exclusive directory rename. It fails closed on unsupported systems.

Create an owner-only parent under ignored build output; the final directory
must not exist. Every parent component must be a real directory, not a symlink.
On macOS, use `/private/tmp`, not the `/tmp` symlink. Example from the checkout:

```sh
mkdir -m 700 -p build/exports
uv run --project tools --frozen python -I scripts/record-qwen-pilot.py \
  --inputs /private/tmp/nib-qwen-inputs \
  --slug qwen-image-export-1 --linux-container \
  --export-image "$(pwd -P)/build/exports/qwen-image-export-1"
```

The parent must be owned by the current user with no group/other permissions.
Do not use a shared destination or change it while the recorder runs. Symlinks,
`..`, existing files, directories and dangling links are rejected, including a
destination that appears during publication. No overwrite option exists.

The ordinary image build can still fetch pinned public base images and locked
build dependencies. **Export and later loading need no registry or network.**
The proof itself retains its network-none, nonroot, read-only runtime with
resource limits, ephemeral test TLS keys and no host configuration mounts.
This task does not create service keys, public certificates, prices or DNS.

## Transaction and contents

1. Snapshot committed source and verify the existing pinned input copies.
2. Build once, capture the immutable image ID, and run the unchanged Linux proof
   by that ID. A failed proof never reaches export.
3. Save that same ID to a new private staging directory. Validate the outer tar
   without extracting it: one untagged Linux/amd64 image, expected nonroot user,
   config digest, ordered uncompressed layer DiffIDs and any OCI index bindings.
   BuildKit's non-runnable provenance descriptors must refer to that sole image;
   their config, payload and subject hashes are checked without rewriting the
   archive or stripping metadata. This is not signature/attestation verification.
4. Load those verified bytes back into the **same local daemon**, inspect the
   exact image ID/platform and recheck the archive. This is not a clean-host or
   cross-Docker-version portability test. No runtime is started by `image load`.
5. Remove the proof container and build tag using the existing cleanup path.
   A bounded pre-build ID inventory distinguishes pre-existing shared images.
   A successful post-cleanup inventory must show the tested ID, config ID and
   OCI manifest IDs absent, or already present before the build. Unexpected retained IDs or an
   unreadable inventory block publication; the exporter never deletes by ID.
   An interrupted load has unknown daemon completion and blocks publication,
   even if the tag currently appears absent.
6. Verify the source snapshot and evidence schema, reserve the exclusive evidence
   directory, recheck staged bytes and licenses, then atomically rename the bundle.
   A later evidence-write error rolls the bundle back. Cleanup failure is reported
   explicitly; a hidden `.qwen-export-*` directory may need operator inspection.

The bundle contains `image.tar`, `manifest.json` and `licenses/`. Directories are
mode 0700; files are 0600. The manifest records archive SHA-256/byte size, tested
image ID and config ID, source Git/archive hashes, Docker context hash, model,
engine and base-image pins, proof results, cleanup results and license hashes.
It explicitly says local-only, not confidential, and no public endpoint.

Save/load each have a 300-second timeout; archive validation has its own
300-second deadline per pass. The archive limit is 2 GiB, expanded layer hashing
4 GiB, JSON 1 MiB, outer members 4,096 and layers 128. CLI diagnostics are capped
at 64 KiB; the shared process-group runner terminates descendants on failure.
Malformed, tagged, multi-image, unsupported-compression or unsupported-layout
archives fail closed. No general-purpose OCI repacker is introduced.

An uncatchable host crash can leave private staging or a complete renamed bundle
without final evidence. Only accept a bundle alongside the matching terminal
PASS evidence; never infer success from a directory's presence. Cleanup checks
the task's container, image tag and exported image IDs, not unrelated images or
BuildKit caches. Any preserved pre-existing image is identified explicitly.

## Offline handoff

Keep the bundle private and transfer it as data only after separate approval.
On the eventual destination, first verify the archive size/hash against the
manifest **and independently retained source-bound evidence**. An unsigned
manifest is integrity metadata, not authentication or attestation. Check the
license file hashes as well; do not extract the image tar into the host root.

After those checks, Docker's offline operations are:

```sh
docker image load --input /absolute/private/bundle/image.tar
docker image inspect --format '{{.Id}} {{.Os}}/{{.Architecture}}' "$TESTED_IMAGE_ID"
```

`TESTED_IMAGE_ID` must be the reviewed `manifest.json` archive image ID, not a
tag or value copied from untrusted output. Stop if identity/platform differs;
Docker storage backends can expose different image IDs. Validate the final
target Docker version before approving deployment. Never substitute a rebuild
or a pull and call it the same tested artifact.

Loading does not serve traffic. The default image entrypoint still runs the
proof; persistent mode and its public retirement cutoff belong to
[the service contract](qwen-service.md). Public hosting, nonroot/read-only runtime
flags, TLS certificates, inference keys, absolute VM lifetime and teardown need
separate review and approval in `hixtube-neocloud`. No deploy driver is included.
This artifact provides no ZDR, E2EE, GPU or confidential-compute guarantee.

## License provenance

- This repository: committed Apache-2.0 `LICENSE` and `NOTICE`.
- `trcs`: original Apache-2.0 `LICENSE` and `NOTICE` from the hash-verified
  [pinned source](https://github.com/Lore-Hex/trustedrouter-confidential-stack/tree/8a87d9765ceabe76bbfa5e56a72c91da6d8290d5).
- llama.cpp: original MIT `LICENSE` and nested license/notice files from the
  hash-verified [pinned source](https://github.com/ggml-org/llama.cpp/tree/620d2225cc436ab0b001e3200e590e3b286ef9ab).
  Nested notices are conservatively included, not relicensed as MIT.
- Model: original Apache-2.0 file at the exact pinned Hugging Face revision;
  [vendored provenance](../../third_party/qwen3-0.6b/README.md) records its hash.
  A revision change requires reverification. Export does not fetch license files.

The unchanged base image and installed Python distributions retain their bundled
notices. This is not a complete redistribution audit of the base OS, statically
linked compiler runtimes or every transitive dependency. Public image publishing
remains gated and needs that audit; this task exports privately only.

## Format sources

- Docker [image save](https://docs.docker.com/reference/cli/docker/image/save/)
  and [image load](https://docs.docker.com/reference/cli/docker/image/load/).
- [Docker image specification](https://github.com/moby/docker-image-spec/blob/main/spec.md):
  ImageID, DiffID and combined archive format.
- [containerd importer](https://github.com/containerd/containerd/blob/main/core/images/archive/importer.go):
  OCI layout preference and Docker manifest handling. Both entry points must
  describe the same single image.
- Docker's [BuildKit attestation storage](https://docs.docker.com/build/metadata/attestations/attestation-storage/):
  unknown/unknown metadata descriptors, target manifest bindings and in-toto payloads.
- [Linux rename](https://man7.org/linux/man-pages/man2/rename.2.html):
  `renameat2(RENAME_NOREPLACE)`. macOS uses local `renameatx_np(2)` with
  `RENAME_EXCL` from the SDK's `sys/stdio.h`; there is no replacing-rename fallback.
