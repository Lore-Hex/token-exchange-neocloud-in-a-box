# Offline tools startup probe

This runs the seven fixed `--version` commands from the committed tools probe,
not mkosi, package installation, a boot test, or a confidential workload.
It is the next dynamic-linking check after the
[203-package tools-tree preparation](p3-t1-mkosi-tools-members.md).

From the same committed source revision, prepare the image as data and then
explicitly execute it using a local Docker daemon:

```sh
python3 -I scripts/prepare_mkosi_probe.py \
  --inputs /absolute/private/mkosi-inputs --output /absolute/private/probe-bundle
python3 -I host/probe-mkosi-tools.py --run \
  --inputs /absolute/private/mkosi-inputs --bundle /absolute/private/probe-bundle \
  --output /absolute/private/new-probe-result
```

Both commands reconstruct the exact same source-bound image. The runner checks
the signed package inputs and every archive again, then compares all bundle bytes
before Docker access. No received receipt alone authorizes an image. New output
is owner-only and never overwrites an earlier result.
The committed runner writes `run.json` and `output.log` with its exact source
revision, UTC interval, normalized fixed command and content-free observations.

The APT simulation and tools probe share one container lifecycle: no network,
ports, host mounts, devices, added capabilities or client credentials; read-only
root, UID 65534, private IPC, 1 GiB RAM, 2 CPUs, 64 PIDs, and a 128 MiB noexec
temporary work directory. The host validates actual container settings before
starting it, binds both layers to their expected digests, and never pulls an
image. Existing image tags reject without deletion. Only the runner's own image
and container are removed, with independent absence checks.

The host deadline is 360 seconds plus bounded cleanup; the worker has a shared
60-second budget and 10 seconds per command. Timeout, output overflow, malformed
results, terminal-state mismatch, sandbox drift and unconfirmed cleanup fail.
The report retains command classes, byte counts and output hashes, not raw
stdout/stderr. A partial failure keeps the completed prefix and attempted count.

Even a passing result leaves `mkosi_executed`, `namespaces_validated`,
`abi_validated`, `installable_verified`, `bootable` and `deployable` false.
Successful startup of these binaries is not evidence of all library interfaces,
namespace creation, package configuration, image reproducibility or attestation.

## Recorded execution

The [October 21 run](../../evidence/p3/2026-10-21-tools-startup-pass/run.json)
at producer `f309d8a814fdf1180e30a99df6e8466b03b5ab26` passed all seven
checks on a local Linux/amd64 Docker container and confirmed cleanup.
Its [content-free output](../../evidence/p3/2026-10-21-tools-startup-pass/output.log)
binds the image, package receipt, completed checks and their output hashes.
Both files are unchanged committed-runner output. This is startup evidence
only; all broader qualification flags remain false.

The next explicit check is the [pinned mkosi namespace probe](p3-t1-mkosi-sandbox-probe.md).
