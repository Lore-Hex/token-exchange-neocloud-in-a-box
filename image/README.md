# Guest image

Reserved for the measured UKI and dm-verity guest image in PLAN.md P3/P5.
No release or debug image has been built or validated yet.

[P3.T1 prerequisite audit](../docs/tasks/p3-t1-guest-image-feasibility.md)
records the source-backed build inputs and unresolved package/boot requirements.
It is not a build recipe, an artifact lock or evidence of image readiness.

[Ubuntu kernel candidate inspection](../docs/tasks/p3-t1-kernel-package-audit.md)
records authenticated package hashes and actual TDX/configfs/crypto contents.
TDX/TSM require the extra modules package; full closure, R595 pairing and runtime
initialization remain unverified.
