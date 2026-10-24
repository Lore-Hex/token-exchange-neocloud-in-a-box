# P2.T4: Explicit Classic-Store Provider Checker

Status: implemented and tested against synthetic fixtures, not a completed
hardware conformance run. This does not change the inference engine, image,
weights, checker version, checks, time limits or confidentiality classification.

## Root Cause and Source

The checker owner assumed descriptor-aware Docker image inspection. Docker
28's [image-inspect CLI](https://github.com/docker/cli/blob/v28.0.4/cli/command/image/inspect.go)
has no `--platform` option. Its
[classic image store](https://github.com/moby/moby/blob/v28.0.4/daemon/images/image_inspect.go)
reports the image config digest, not an OCI manifest descriptor. Successfully
caching that image does not prove the launcher accepts that representation.

The explicit preparation-only `--provider-image-profile docker28-classic`
reuses the already verified immutable Python image chain documented in
[the CPU scorer profile](p2-t3-tau-cpu-prompt-fit.md):

- Index: `4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2`.
- Linux/amd64 manifest: `6e13e65c55e33adf203d77ee371cf8bf5d81bd4902ef07565721f46bf44917af`.
- Config: `64d91f7b885eed272bba87909446b12ff408d4aaa5f1a0e9ca787bbea1a020b9`.

The contract binds the choice before execution. It cannot be enabled at run
time. There is no automatic fallback: absent the option, descriptor verification
and existing report shapes are unchanged. An unexpected store representation,
platform, config, index reference, volume or manifest fails closed.

## Verification and Lifecycle

The host verifies the image before starting the expensive engine, again before
checker creation, and binds every checker-container inspection to its exact
config ID. Checks for the owning engine, same network namespace, separate PID
and cgroup namespaces, no GPUs, read-only filesystem, restricted mounts, limits,
watchdog and cleanup remain mandatory. The private report records the profile;
the host joins that exact profile and manifest to the source-bound contract and
guest acknowledgement. All 17 mandatory conformance checks still must pass.

Tests cover both store representations, explicit-only selection, malformed
profiles, wrong image/config/platform, confinement drift, post-start image
drift, the full owner handshake and cleanup, CLI plumbing, and unchanged default
contracts. Real node validation requires a separately recorded hardware run.
