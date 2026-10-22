# Native CPU Parity Route

The [actual diagnostic failure](../../evidence/p2/2026-10-22-cpu-parity-argv-failure/README.md)
identified an exact argv hash mismatch while the first server was alive. It did
not establish emulator rewriting. No prefix stripping, argument normalization,
retry-after-mismatch or alternative executable hash is accepted.

At llama.cpp `620d2225cc436ab0b001e3200e590e3b286ef9ab`,
[common/arg.cpp](https://github.com/ggml-org/llama.cpp/blob/620d2225cc436ab0b001e3200e590e3b286ef9ab/common/arg.cpp)
copies CLI arguments into `std::string` when parsing them; the audited
[server entry](https://github.com/ggml-org/llama.cpp/blob/620d2225cc436ab0b001e3200e590e3b286ef9ab/tools/server/server.cpp)
passes its original argv to that parser. This source inspection does not explain
the observed kernel bytes. The trailing-NUL expected encoding is unchanged;
tests explicitly reject missing/extra NULs and an interpreter prefix.

The new `Native CPU support tokenizer parity` workflow is manual, main-only and
GitHub-hosted `ubuntu-24.04`, with a host `x86_64` check before any preparation.
It runs the existing producer with explicit `--host-kind github-actions` and
the same Linux/amd64 image, compiler, CMake wheel, source archive, two model
pins and four probes. The entire worker, pilot command and procfs/endpoint
checks remain byte-identical to the diagnostic producer. Native success is
not assumed: any mismatch still fails before requests or admission.

Preparation reads a committed private source snapshot, uses empty credential
configuration, fetches only the four existing public hash/size-bound inputs and
pulls only the two existing digest-pinned base images. The
[CMake 3.31.6 metadata](https://pypi.org/pypi/cmake/3.31.6/json) binds the wheel's
fixed URL, 27,800,904-byte size and existing SHA-256. No new package or model is
introduced. Downloads stream through the existing independent process-group
supervisor with exact byte caps, HTTPS-only redirects and a shared 900-second
preparation budget. Partial private inputs and base/BuildKit caches may remain
on the disposable runner; they are not uploaded or claimed deleted. Docker pull
timeout stops the client but is not proof of daemon-side pull cleanup.

After preparation, image assembly is offline (240 seconds); runtime remains
network-none, no GPU, four CPUs, 8 GiB/no swap, 128 PIDs, read-only root, zero
capabilities and no host mounts. The existing 1800-second runtime, 300-second
model and 90-second readiness limits and owned cleanup remain unchanged.
Workflow limits are 17 minutes for preparation, 40 for recording and 60 for the
job, leaving room for normal cleanup and upload. Cancellation or a missing final
receipt is failure, not cleanup proof. Only the two bounded original metadata
files are uploaded, including failures; no model, compiler log or input directory
is selected. A successful upload does not make a failed producer successful.

No dispatch occurred while implementing this route. Exact-head independent
review, full checks and CI must pass before the one separately authorized native
CPU trial. No changes to Spheron, current GPU jobs, live CPU admission, tau
acceptance, quality or confidential qualification are included.

## Native Base Inspection Failure

The one approved [native run 39853952934](../../evidence/p2/2026-10-22-cpu-parity-base-inspect-failure/README.md)
failed before image assembly or any model execution. Input preparation passed;
the original record retains stage `inputs`, context/source/base-image pins,
`image_id: null`, `runtime: null`, and confirmed owned cleanup. It does not
retain inspect stderr, so no runtime exception text is claimed.

The job identifies runner image `ubuntu24/20261012.334`; its
[immutable inventory](https://github.com/actions/runner-images/blob/7e7dc1bcc0ef464e5e315a2b4c33609590c6cffe/images/ubuntu/Ubuntu2404-Readme.md#tools)
lists Docker client/server **28.0.4**. That version's
[inspect command registration](https://github.com/docker/cli/blob/b8034c0ed70494a90c133461d145cd072d920d7c/cli/command/image/inspect.go#L37-L39)
defines `--format`, not `--platform`. The producer used the unsupported flag
at precisely this pre-build boundary. This establishes a source-backed CLI
compatibility defect, not an observed native tokenizer failure.

The correction follows the existing `scripts/pilot/export.py` inspection
pattern: query the exact immutable base reference with `--format`, then require
a SHA-256 image ID and the returned exact `linux/amd64` platform. Docker's
[28.0.4 image inspection](https://github.com/moby/moby/blob/6430e49a55babd9b8f4d08e70ecb2b68900770fe/daemon/images/image_inspect.go#L51-L66)
returns that actual image metadata. No tag or repository normalization is
involved: this path does not read `RepoDigests`. The digest pins, explicit
pull/build/run platforms, worker, confinement, process/socket/argv identity
and all runtime budgets are unchanged. Inspection remains 15 seconds per
base; stdout and stderr are each capped at 1024 bytes, stderr must be empty,
and future inspection failures retain fixed stage `base-images`.

Tests reject nonzero exit, wrong OS/architecture, malformed/multiple output,
stderr, timeout and output-limit failure for either base before building or
running anything. They also bind every original source hash and receipt byte.
The correction was gated on final-head independent review, full checks, CI and
one explicit corrected-run approval; no blind retry was performed.

## Actual Native Parity

The [original corrected-run receipts](../../evidence/p2/2026-10-22-cpu-parity-native-pass/README.md)
from workflow **39857959628** at producer
`0a89d7f5193fab48f1b1e3cfa8e88fd772436f4f` passed. Both pinned Qwen3-0.6B and
Qwen3-1.7B models matched independent token-ID hashes and generated prompt usage
for all four unchanged probes: **20, 28, 160, 198** tokens. Strict ownership
checks passed and owned container/image cleanup was confirmed. The original
21-source closure is preserved, not upgraded to a later producer.

This measured sequential CPU functional parity only. It did not produce fresh
live-pair admission, a tau trial, a quality result or confidential proof. No
claim is made about concurrent memory fit, arbitrary histories, peak memory,
binary reproducibility or the cause of the earlier OrbStack argv mismatch.
