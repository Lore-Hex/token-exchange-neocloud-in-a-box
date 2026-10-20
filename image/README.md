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

[P3.T1 prerequisite audit](../docs/tasks/p3-t1-guest-image-feasibility.md)
records the source-backed build inputs and unresolved package/boot requirements.
It is not a build recipe, an artifact lock or evidence of image readiness.

[Ubuntu kernel candidate inspection](../docs/tasks/p3-t1-kernel-package-audit.md)
records authenticated package hashes and actual TDX/configfs/crypto contents.
TDX/TSM require the extra modules package; full closure, R595 pairing and runtime
initialization remain unverified.
