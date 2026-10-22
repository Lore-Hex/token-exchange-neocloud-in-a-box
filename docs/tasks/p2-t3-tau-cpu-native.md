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
