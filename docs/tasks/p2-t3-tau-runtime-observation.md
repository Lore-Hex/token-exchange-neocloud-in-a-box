# Single-engine runtime observation and tau topology

Status: implemented opt-in producer, tested against synthetic kernel and lifecycle
fixtures only. **No new engine, GPU, container or tau execution. No runtime
measurement receipts have been produced by this change.** The result is not a
`nib.tau-local-runtime/v1` or tokenizer receipt and cannot admit a live tau trial.

## Immediate deployment gap

The legacy v1 [live tau runner](p2-t3-tau-live-runner.md) requires four distinct
live API processes, socket inodes and ports. The current explicit
[selected-target v2 path](p2-t3-tau-selected-target.md) instead requires candidate
OR reference plus simulator and evaluator per trial. Both target configurations
remain frozen; later reference execution needs its own actual measurements and
confirmed cleanup before resource reuse, not relabeled candidate receipts.
Simulator/evaluator still need distinct pinned model identities and file
inventories, different from DeepSeek. Our existing lab has one API process in a
private network namespace; its owner stops it and removes the container after
the request phase. A stopped run, two DP ranks, aliases or repeated hashes cannot
stand in for the three active v2 deployments (or four legacy v1 deployments).

Two independent TP4/DP1 DeepSeek deployments, one on each disjoint GPU quartet,
were a proposed same-host topology for legacy v1, not a prerequisite for the
current sequential-target path. This remains a **proposal, not a capacity claim
or run authorization**. No simulator/evaluator serving configurations are
admitted here. The small Qwen pins in the selected-target plan are proposals,
not qualified support deployments. Both support roles still require complete
weight/tokenizer inventories, source-backed engine support, context limits and
independent tokenizer checks.

Pinned [vLLM data-parallel documentation](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/docs/serving/data_parallel_deployment.md)
describes the single API endpoint and MoE expert group spanning DP times TP.
Consequently, the existing TP4/DP2 result does not prove the memory footprint of
two independent TP4/DP1 deployments. The 510,311,045,295-byte model pack is a
file inventory, not a GPU-memory requirement. All eight GPUs would be assigned
to the two DeepSeek deployments; there is no established spare GPU allocation.

Before co-locating the two additional models, measure per-device resident and
peak memory, KV/cache/workspace requirements, host-memory peak and total process
budget under the exact context and concurrency settings. The pinned
[cache configuration](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/config/cache.py)
defines GPU memory utilization per instance, not as a host-wide reservation.
Do not divide raw capacity or assume two processes automatically coordinate it.
This increment measures **no GPU memory** and does not establish co-residency.

The tau HTTP parent also needs the engines' network namespace, UID and procfs
visibility. The current host-side parent cannot inspect/connect to those private
loopback endpoints as if they were host endpoints. A future bounded orchestration
step must place the trusted HTTP bridge inside that private namespace, retaining
the isolated worker's existing parent-pipe protocol and external lifecycle owner.
Do not publish ports, use host networking, mount a Docker socket into an engine
container or bypass procfs admission to make the current runner connect. A remote
reference would require separate approval and a separately reviewed identity and
transport path; a localhost forwarding process cannot stand in for its runtime.

## Implemented piece

`host/record-deepseek-lab.py --runtime-observation` is preparation-only and valid
only with B300 CC local-correctness, DP1 or DP2. It composes with the existing
tokenizer parity/materialization flags; no default, probe or historical artifact
is changed. An explicitly false/absent flag adds no helper or contract field.
This is opt-in byte compatibility within the new producer, not a claim that the
new source-bound launcher has the same bytes as an earlier commit.

The observer runs immediately before and after the existing request phase. It:

- Uses the exact owned `Popen` PID, requires a live session/group leader, the
  observer's parent as its parent, matching UID and PID/network/cgroup namespaces,
  and stable boot/start/command-line facts before and after each observation.
- Hashes the actual `/proc/PID/exe` bytes and compares size/hash with the existing
  authenticated interpreter contract, including file identity rechecks.
- Requires the single exact IPv4 loopback port 8000 listener to belong to that
  process. Wildcard, IPv6, duplicate or foreign listeners reject. This is a
  listener ownership snapshot, **not** the tau client's connected-peer check.
- Checks stdout/stderr are `/dev/null`. This does not prove request, access or
  file logging is disabled, nor identify imported engine/model bytes in memory.
- Reads the matching private cgroup-v2 root's `memory.current`, `memory.peak`,
  `memory.max`, `memory.swap.max`, `pids.current` and `pids.max`. The configured
  memory, no-swap and 1024-PID bounds must match the lab contract. Missing or
  unsupported kernel observations reject; no capability or mount fallback exists.

Kernel interface sources: Linux v6.17
[procfs, process fields and file descriptors](https://github.com/torvalds/linux/blob/v6.17/Documentation/filesystems/proc.rst)
and [cgroup-v2 memory/PID controllers](https://github.com/torvalds/linux/blob/v6.17/Documentation/admin-guide/cgroup-v2.rst).
`memory.peak` covers the cgroup and descendants since creation, including earlier
application work, scratch/cache and observer overhead. It is not API-only RSS or
GPU memory. Counters are sequential kernel reads, not an atomic snapshot, and
`pids.current` counts tasks/threads, not only API processes. This deliberately
rejects over-limit samples, including possible transient memory-limit overshoot;
it does not rewrite an unavailable or rejected observation as zero usage.

The pinned [serve dispatcher](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/entrypoints/cli/serve.py)
runs the single Python API server in the calling process for the lab's explicit
`--api-server-count 1`, non-Rust, non-multi-port profile. Runtime listener checks
must still confirm that expectation. No child PID is guessed from process names.

Only hashes, fixed event names, booleans and counters leave the observer. Raw
PIDs, argv, socket addresses, descriptors, paths, process names, boot IDs, requests
and responses are not exported. The report binds the exact prepared contract.
Two samples must identify the same process/executable/argv/listener; replacement
or drift prevents pass. Missing observations retain zero/one completed samples
and follow the existing failure/cleanup path, never fabricated equality rows.

The existing source-bound `execute_process_group` supervisor provides the
independent 15-second observation timeout, bounded pipes and owned-group cleanup
(up to 25 additional seconds). The child alarm is secondary. Each observation
reserves that whole allowance before the unchanged 90-second teardown reserve.
The outer 80-minute deadline, watchdog, resource bounds and container cleanup
remain authoritative. No engine is kept alive after completion; unproven
subprocess or container cleanup cannot become success.

## Preparation and verification

On an approved future run, prepare a fresh committed bundle, for example:

```sh
python3 -I -S host/record-deepseek-lab.py \
  --prepare-local-correctness-b300 "$PRIVATE_BUNDLE" \
  --tokenizer-parity --tokenizer-materialization --runtime-observation
```

The existing `--run` command consumes the resulting receipt digest; it accepts
no observation override. Use the explicit `-dp2` preparation selector only when
that topology is approved. No execution is authorized by preparation.

```sh
python3 -B -m unittest discover -s scripts -p 'test_engine_runtime_observation.py'
python3 -B -m unittest discover -s scripts -p 'test_deepseek_lab.py'
```

Tests exercise synthetic kernel observations, source/result binding, malformed
and overflowing reads, identity/listener/executable drift, missing counters,
deadlines, partial results and cleanup after either observation fails. They do
not manufacture runtime evidence or establish native procfs compatibility.

Remaining gates are actual observation on the selected runtime, measured resource
headroom, exact simulator/evaluator pins, selected-target plus support-role
lifecycle/namespace integration (four concurrent roles only for legacy v1),
measured complete runtime/weight/logging provenance and independent full-framing
tokenizer parity for every active role. Sequential reference execution still
requires its own qualified configuration and measurements. Even after these,
the operational trial remains `quality_eligible=false`; it is not the full frozen
quality benchmark, production readiness or confidential verification.
