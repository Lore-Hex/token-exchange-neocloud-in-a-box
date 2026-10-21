# Guest image

Reserved for the measured UKI and dm-verity guest image in PLAN.md P3/P5.

The [R595 input audit](../docs/tasks/p3-t1-r595-package-inputs.md) pins the
`595.91.07` kernel/driver build dependency graph from authenticated metadata.
Its offline checker proves declared dependency coverage, not installability,
LKCA compilation, a complete guest rootfs, bootability or hardware qualification.
No release or debug image has been built or validated yet.

[Offline package preparation](../docs/tasks/p3-t1-offline-package-preparation.md)
now verifies all pinned archives and control metadata, prepares a private local
repository and re-verifies its complete bytes without installation. The generated
nonbootable mkosi candidate still requires a pinned Linux tools tree and reviewed
installation boundary; it is not an installability or reproducibility result.

The [Linux APT simulation tools](../docs/tasks/p3-t1-linux-tools-simulation.md)
assemble a source-bound, nonbootable image archive without package execution.
Its fixed CPU-only runner rehearses the exact guest package plan without
installation. Container execution awaits independent review and coordinated
validation; the minimal APT root is not yet the complete mkosi tools tree.

The separate [mkosi build-input preparer](../docs/tasks/p3-t1-mkosi-build-inputs.md)
verifies the exact 27.1 source archive and a 203-package tools graph, then
assembles their union with the guest packages without execution. Script review,
expanded-tools APT/runtime checks and a reviewed Linux mount/namespace policy
still block installation; its receipt cannot authorize a build.
Its [local service-denial patch](patches/README.md) binds an exact executable-mode
correction for each APT transaction; only synthetic behavior is tested, not an
actual installation or observed service start.

The [exact tools-tree preparer](../docs/tasks/p3-t1-mkosi-tools-members.md)
handles one archive-bound UTF-8 certificate member, two verified awk aliases,
and the complete pinned inventory of 278 non-host GRUB inputs. It produces a
deterministic tar and receipt without executing packages; generic path and
host-executable architecture checks remain strict. Dynamic loading, ABI and
installation are still unverified.

[P3.T1 prerequisite audit](../docs/tasks/p3-t1-guest-image-feasibility.md)
records the source-backed build inputs and unresolved package/boot requirements.
It is not a build recipe, an artifact lock or evidence of image readiness.

[Ubuntu kernel candidate inspection](../docs/tasks/p3-t1-kernel-package-audit.md)
records authenticated package hashes and actual TDX/configfs/crypto contents.
TDX/TSM require the extra modules package; full closure, R595 pairing and runtime
initialization remain unverified.

The [native namespace probe](../docs/tasks/p3-t1-native-mkosi-probe.md) passed
on an unprivileged Ubuntu 22.04 runner after the Ubuntu 24.04 attempt was denied.
The separate [native pinned-tools probe](../docs/tasks/p3-t1-native-pinned-tools.md)
fetches hash-bound inputs and tests the reconstructed tools inside that boundary.
Neither diagnostic authorizes installation or makes the image bootable.
