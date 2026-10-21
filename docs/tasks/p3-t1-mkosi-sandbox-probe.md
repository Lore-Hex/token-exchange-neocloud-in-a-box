# Pinned mkosi namespace probe

This explicitly enabled check follows the [tools startup probe](p3-t1-tools-startup.md).
It tests the actual pinned mkosi sandbox, not a substitute namespace implementation.
It does not install packages or build an image. Hardware, boot and attestation
qualification remain separate gates.

## Run

Use a committed checkout and the authenticated mkosi input directory. New output
directories must not already exist. Both preparation and execution require
`--sandbox`; mixing a startup-only bundle and sandbox-mode runner rejects before
Docker access.

```sh
python3 -I scripts/prepare_mkosi_probe.py --sandbox \
  --inputs /absolute/private/mkosi-inputs --output /absolute/private/sandbox-bundle
python3 -I host/probe-mkosi-tools.py --run --sandbox \
  --inputs /absolute/private/mkosi-inputs --bundle /absolute/private/sandbox-bundle \
  --output /absolute/private/sandbox-result
```

The full source archive is reverified against the committed
[mkosi 27.1 manifest](../../image/inputs/mkosi-27.1.json), including Git blob
correspondence. Only `mkosi/sandbox.py`, its license files and our fixed synthetic
checker are added to the existing two-layer probe image. Runtime reads recheck
both executable-source hashes before any upstream execution. Default startup-only
mode retains its v1 format; explicit sandbox mode uses v2 receipts.

The exact flags are from
[mkosi sandbox.py at ecf7f570](https://github.com/systemd/mkosi/blob/ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b/mkosi/sandbox.py):
read-only binds, private temporary storage, `--unshare-net`, `--unshare-ipc`, and
`--become-root`. That source always requests a mount namespace, and acquires a
user namespace for the unprivileged caller. It may skip network unsharing if
capabilities are unavailable; our child explicitly rejects that outcome.

The child requires four distinct namespace identities (user, mount, network,
IPC), root only inside the new user namespace, and loopback-only networking.
It verifies that its worker directory rejects a synthetic write with `EROFS`,
not merely `EACCES`, and that the new temporary directory permits a synthetic
write/read/remove. Namespace identities and process output are not retained.
An exit-zero response must also match the exact fixed success sentinel.

The outer Docker policy is unchanged: no network, host mounts, devices, ports,
added capabilities or secrets; read-only root, UID 65534, no-new-privileges,
1 GiB memory, 2 CPUs and 64 PIDs. The original deadlines and independent cleanup
apply. **Do not add `--privileged`, disable seccomp, or grant host capabilities
to make this test pass.** Host or runtime namespace restrictions are a failed
prerequisite, not permission to change the policy.

## Interpretation

The result retains the successful prefix of seven startup checks and a final
sandbox check. Failure labels are fixed classes (permission denied, timeout,
process exit, startup error, invalid result), not raw exception text.
`mkosi_executed` records an attempted sandbox invocation, not a completed build.
An unreadable/malformed container result leaves the host execution claim unknown.
`namespaces_validated` is true only after the fixed child succeeds.
`mkosi_build_executed`, package installation, ABI qualification, boot and
deployability remain false even on success.

Before image installation, maintainer hooks still need review and the exact
kernel/compiler bindings still need runtime validation. This probe does not
approve arbitrary installers, foreign UID mappings, writable host mounts or
network access.
