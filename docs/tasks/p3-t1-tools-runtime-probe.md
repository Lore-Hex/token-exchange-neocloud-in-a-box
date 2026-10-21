# P3.T1 tools startup-probe preparation

Status: **implemented with synthetic tests; runtime not executed**.
This extends the [exact tools preparation](p3-t1-mkosi-tools-members.md).
It is not an installation runner, namespace approval or a confidential image.

The existing package preparer revalidates all 203 selected archives. The new
preparer uses its exact tools tar as the first image layer, without extraction
or mutation. A second layer adds only a fixed probe, the existing bounded
process-group helper and an input manifest binding the producer and helper.
Package paths colliding with `/nib` reject. The shared Docker archive writer
preserves the original APT image format byte-for-byte for identical inputs;
the regression test compares the complete original single-layer encoding.

```sh
python3 -I scripts/prepare_mkosi_probe.py \
  --inputs /absolute/private/mkosi-inputs \
  --output /absolute/private/new-probe-bundle
python3 -I scripts/prepare_mkosi_probe.py \
  --inputs /absolute/private/mkosi-inputs \
  --verify /absolute/private/new-probe-bundle
```

This reads only authenticated package inputs and committed repository sources.
It does not call Docker, install a package, run mkosi or execute any packaged
program. Output remains owner-only and no-overwrite. Verification reconstructs
every byte. The existing archive bounds and 512 MiB aggregate-output limit
remain. The image, original tools receipt, root inventory and new receipt are
all preserved. No container/image is published or loaded.

## Fixed Future Probe

The worker runs only `--version` for Python, APT, dpkg, bubblewrap, bsdtar,
systemd-repart and the pinned awk alias, in that order. The primary references
are the exact package man pages in the unchanged
[tools lock](../../image/inputs/mkosi-tools-ubuntu2404.json):
`usr/share/man/man1/{python3.12,dpkg,bwrap,bsdtar,gawk}.1.gz`,
`usr/share/man/man8/{apt-get,systemd-repart}.8.gz`.
The worker does not accept arguments, environment settings, program paths or
commands from the input manifest. It requires Linux, unprivileged UID 65534,
isolated Python, no site loading and no bytecode writes.

There is one 60-second deadline, at most 10 seconds per command and 64 KiB of
combined output per subprocess. The existing helper terminates and reaps the
whole process group. Nonzero exits, missing/empty output, any stderr, output
overflow or timeout stop the sequence and fail. Receipts contain only fixed
program names, output sizes and hashes; no raw process output or exception
text is retained. An attempted call is not labelled a successful process start.

Even a future successful probe establishes only those seven startup commands
on the tested runtime. It cannot prove every dynamic dependency, ABI, package
configuration, namespace/mount support, mkosi builds, R595/LKCA, boot or
attestation. All corresponding claims remain false in these receipts.

## Next Gate

No host execution entrypoint is provided in this increment. A reviewed runner
must first inspect the loaded digest and enforce an external, network-disabled,
read-only, unprivileged sandbox with no host mounts, devices, ports or secrets,
bounded memory/CPU/PIDs and independent cleanup. Image metadata alone does not
enforce those constraints. It must validate worker receipts against its own
inputs and refuse incomplete execution or cleanup. Do not run this image with
`--privileged` or add capabilities to make a later namespace probe pass.

The [installation blockers](p3-t1-mkosi-build-inputs.md#exact-blockers-before-installation)
remain: expanded APT resolution, maintainer-hook audit, target-kernel/compiler
binding, nested namespace policy and two fresh reproducible builds. These
preparation and fake-test results do not satisfy any hardware acceptance gate.
