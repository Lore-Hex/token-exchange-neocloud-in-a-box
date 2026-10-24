# P2.T3: Bound CPU Support-Server HTTP Workers

The selected-trial CPU owner built its image but the first Qwen server exited
with SIGABRT before health readiness on the large ordinary GPU host. No model
request or quality score was produced. Its original record does not contain a
native exception message, so it cannot by itself establish the abort's cause.

The pinned llama.cpp revision
[`620d2225cc436ab0b001e3200e590e3b286ef9ab`](https://github.com/ggml-org/llama.cpp/blob/620d2225cc436ab0b001e3200e590e3b286ef9ab/tools/server/server-http.cpp)
defaults its HTTP pool to the greater of `parallel + 4` and the host's reported
hardware concurrency minus one. This is independent of the generation-thread
setting and a Docker CPU quota. A 240-CPU host can therefore request 239 fixed
HTTP threads, exceeding the CPU support container's unchanged 128-task limit.
This is a source-confirmed deployment defect and a candidate explanation for
the observed abort, pending the real rerun.

The shared pilot command now passes `--threads-http 4` in addition to
`--threads 4`, using the upstream
[documented option](https://github.com/ggml-org/llama.cpp/blob/620d2225cc436ab0b001e3200e590e3b286ef9ab/tools/server/README.md).
All pilot, parity and paired-support callers reuse this command. Container
CPU, memory and PID limits, private networking, source/weight verification and
content-log suppression are unchanged. This bounds the fixed HTTP worker pool;
it does not claim to cap every dynamically created upstream thread.

Regression coverage verifies one explicit fixed HTTP-thread option regardless
of the reported host CPU count. Successful startup, tokenizer parity, tau
execution and quality acceptance are separate measured gates.
