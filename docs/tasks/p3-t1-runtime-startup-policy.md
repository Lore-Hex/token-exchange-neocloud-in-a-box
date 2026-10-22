# Runtime Startup Policy

This is an offline build transformation for the 212-package runtime candidate,
not a boot test or a complete release-hardening policy. It never runs systemctl,
starts a service, deletes logs or changes the host. It is applied after package
configuration and any requested driver compilation/installation in the existing
private build namespace. The original 145-package target is unchanged.

## Fixed Changes

- Mask login and emergency/rescue shells, user managers, persistent-store
  archival and journal namespaces in the system scope.
- Mask Podman's API, automatic restart, auto-update and Kubernetes launcher in
  both system and user scopes. Mask automatic NVIDIA CDI refresh; a future
  verified engine launcher must generate and validate its device specification.
- Mask user GnuPG services and sockets inherited from build dependencies.
- Install default-deny system/user presets. Presets do not disable existing
  activation links; the explicit masks are what block the listed services.
- Configure journald with no storage and no forwarding to syslog, kernel,
  console or wall. Set the manager's default core limit to zero.

The [implementation](../../scripts/mkosi_runtime_policy.py) uses fixed paths
under `/work/output/rootfs`, with directory-descriptor-relative traversal and
no symlink parents. All existing output leaves must already match this exact
policy. Unexpected files, directories, links, hardlinks, FIFOs, permissions or
bytes fail before any policy files are written. Directory creation can precede
failure; the caller must discard the failed build as it already does.
Writes use exclusive creation, so a newly appearing leaf cannot be overwritten.
Every resulting file/link is checked again and included in the build receipt.
The operation is deterministic and idempotent in the quiescent private root.
It is not a general adversarial live-filesystem editor or a host configuration tool.

## Source Review

Reviewed October 22, 2026 against the actual authenticated package bytes in the
[runtime lock](../../image/inputs/guest-runtime-ubuntu2404.json), not unpinned
online defaults. No upstream implementation or manual text is copied here.

| Package | Source and relevant behavior |
| --- | --- |
| systemd 255.4-1ubuntu8.18 | Packaged `systemd.unit(5)` defines `/etc` unit precedence and `/dev/null` masks; `systemd.preset(5)` defines ordered presets and `disable *`. |
| systemd 255.4-1ubuntu8.18 | Packaged `journald.conf(5)`: `Storage=none` and all four `ForwardTo*` settings; journal namespaces otherwise create persistent log directories. |
| systemd 255.4-1ubuntu8.18 | Packaged `systemd-system.conf(5)`: `DefaultLimitCORE=0` is only a child-unit default and can be overridden; it does not constrain PID 1. |
| systemd 255.4-1ubuntu8.18 | Installed units and aliases include getty variants, rescue/emergency shells, user managers, pstore archival and journal namespaces. |
| Podman 4.9.3+ds1-1ubuntu0.2 | Actual system/user unit files start the API socket, all restart-policy containers, auto-update and kube workloads without our future appraisal gate. |
| NVIDIA container toolkit 1.20.1-1 | CDI refresh path/service automatically enumerates devices. Installing the package is not authorization to expose those devices. |
| GnuPG packages | User-scope agent/keybox/certificate-manager sockets are not needed by the inference service. |

For independent navigation, the upstream
[systemd v255 manuals](https://github.com/systemd/systemd/tree/v255/man) cover
the same named directives. The lock records the actual Ubuntu archive hashes;
upstream v255 is context, not a replacement for the reviewed Ubuntu inputs.

## Verification And Limits

Focused tests cover repeatability, fixed-root rejection, parent/leaf symlinks,
hardlinks, FIFOs, conflicting files, permission changes, exclusive creation
under a leaf race and system/user policy coverage. Tests use disposable
fixtures only. The native workflow must still prove application to the real
installed package graph; no such result is claimed by this document.

This does **not** prove no content can ever be logged: unit-level overrides,
direct writes, kernel dumps and boot-command-line overrides need the later
immutable-image policy. Existing package logs and generated identities are
not removed. Build tools remain installed. There is no complete unit allowlist,
root/weight dm-verity, UKI, real guest service implementation, hardware boot,
attestation or production qualification. Those gates stay fail-closed.
