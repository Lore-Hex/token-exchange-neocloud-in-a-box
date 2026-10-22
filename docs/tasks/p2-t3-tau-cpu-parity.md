# CPU Support Tokenizer Parity Producer

Status: implemented and tested with synthetic protocol/process fixtures. No model
execution, tokenizer parity measurement, tau trial, quality result or confidential
execution is claimed by this change. The existing live tau admission remains
vLLM-only. This producer does not create admission receipts or start DeepSeek.

## Exact Independent Path

The producer consumes the two distinct GGUF candidates in
`scripts/tau_cpu_support_models.json`. The 0.6B pin remains the existing pilot;
the 1.7B input is the same official-revision Q8_0 artifact, not a new download.
Both private copies are size/hash checked before image assembly and rehashed
inside the read-only image before use and after inference. Disk sizes are not
runtime-memory measurements.

The independent C++ helper links the public common library from the existing
llama.cpp archive at `620d2225cc436ab0b001e3200e590e3b286ef9ab`. It loads vocabulary
only, with zero GPU layers, and receives the unchanged four probes from
`engine_tokenizer_parity.probes()` over a parent pipe. It independently initializes
the GGUF template, parses complete messages/tools, creates a full chat session
with generation prompt, non-thinking mode and automatic tools, then tokenizes
with both special-token flags true. It never receives HTTP-rendered text.

Primary source at that exact revision:

- [common/chat.h](https://github.com/ggml-org/llama.cpp/blob/620d2225cc436ab0b001e3200e590e3b286ef9ab/common/chat.h): public template, parser and session interfaces.
- [common/chat.cpp](https://github.com/ggml-org/llama.cpp/blob/620d2225cc436ab0b001e3200e590e3b286ef9ab/common/chat.cpp): full session rendering and tool parsing.
- [common/common.h](https://github.com/ggml-org/llama.cpp/blob/620d2225cc436ab0b001e3200e590e3b286ef9ab/common/common.h): the default DeepSeek reasoning parser format remains selected even with thinking disabled.
- [tools/tokenize/tokenize.cpp](https://github.com/ggml-org/llama.cpp/blob/620d2225cc436ab0b001e3200e590e3b286ef9ab/tools/tokenize/tokenize.cpp): vocabulary-only loading and `common_tokenize`.
- [tools/server/server-common.cpp](https://github.com/ggml-org/llama.cpp/blob/620d2225cc436ab0b001e3200e590e3b286ef9ab/tools/server/server-common.cpp): chat/template parsing, non-thinking kwargs and full history.
- [tools/server/server-context.cpp](https://github.com/ggml-org/llama.cpp/blob/620d2225cc436ab0b001e3200e590e3b286ef9ab/tools/server/server-context.cpp): generation tokenizes with `add_special=true, parse_special=true`; `/tokenize` needs the explicit override.

For each model, the existing [CPU transport](p2-t3-tau-cpu-framing.md) sends the
same messages/tools through `/apply-template` and `/tokenize`. The independently
computed ID hash/count must match before a one-output-token chat request is sent.
Its input usage must equal that same count. All four cases must pass for both
models; mismatch or protocol failure never becomes a successful prerequisite.
This is independent invocation of the same pinned implementation, not independent
implementation of tokenization and not proof for arbitrary histories.

## Confinement and Receipts

The recorder snapshots committed source using the existing pinned snapshot
helper. It assembles only already-local pinned pilot compiler/Python images and
the existing hash-locked CMake wheel. No downloader or network fallback is added.
The [Docker controls](https://docs.docker.com/reference/cli/docker/container/run/)
are fixed: Linux/amd64, network none, private cgroup namespace, read-only root,
no capabilities, no-new-privileges, UID/GID 65532, no host mounts, ports or GPU
devices, 128 PIDs, four CPU quota, 8 GiB memory with no swap, a 4 GiB executable
private build tmpfs and a 128 MiB non-executable temporary directory. The worker
checks Linux UID/GID, loopback-only interfaces, zero capability sets, NNP/seccomp
and exact cgroup-v2 limits. There is no macOS identity fallback.

The existing pilot server arguments remain unchanged except removal of the
API-key-file argument in this new isolated synthetic-only namespace. The ordinary
pilot and live tau commands are unchanged. The existing procfs guard checks the
owned PID/start ticks, executable/argv hashes, network namespace, listener inode
and each connected peer socket before sending any HTTP request. Both engine
streams and compiler output go to `/dev/null`; Docker logging is disabled. The
helper redirects native stdout/stderr and returns only bounded IDs through its
dedicated pipe. Only fixed-name numeric observations and hashes survive the
strict parent report validator, never prompts, completions, bodies or exceptions.

Toolchain assembly has a 240-second parent deadline. The owned runtime container,
including native compilation, has an independent 1800-second parent deadline;
native compilation is limited to 900 seconds, each model to 300, readiness to 90,
the independent helper to 60 and each HTTP operation to 30. The parent kills its
Docker client group and always removes/queries the owned container and image.
Unknown cleanup or interrupted daemon-side image build forces failure. Cached
base images and ordinary BuildKit cache are retained, not claimed deleted.
Input staging is byte-bounded local file copying, not a hard-deadline NFS tool.

The output must be a new directory in an owner-only, no-symlink parent. An initial
failure receipt is reserved before container work. Final `run.json` and
`output.log` are each bounded to 64 KiB and contain source snapshot/context,
toolchain image, actual built binaries, exact GGUF and observed process binding.
No raw model or filesystem artifacts are exported. A pass requires cleanup;
missing final output, a pending receipt or failure cannot be interpreted as pass.

## Controlled Run After Review

Use a committed, independently reviewed head and coordinate CPU/memory availability
with the GPU run owner. Both base images and the exact CMake wheel must already
exist locally. No launch was performed while implementing this producer.

```sh
python3 -I -S -B scripts/record-tau-cpu-parity.py \
  --archive /PRIVATE/llama.tar.gz \
  --model-06 /PRIVATE/model.gguf \
  --model-17 /PRIVATE/Qwen3-1.7B-Q8_0.gguf \
  --cmake-wheel /PRIVATE/cmake-3.31.6-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl \
  --output /PRIVATE/new-cpu-parity-receipts
```

```sh
PYTHONPATH=scripts python3 -B -m unittest test_tau_cpu_parity test_tau_local_llama
python3 -B scripts/check-doc-links.py
make check
```

Actual two-model parity, latency/memory feasibility, structured evaluator output
and original tau prompt/context fit remain unproven. Only after those measurements
should an explicitly reviewed CPU runtime kind be admitted and connected to the
selected-target lifecycle. Full benchmark acceptance and distinct simulator and
evaluator requirements are unchanged.
