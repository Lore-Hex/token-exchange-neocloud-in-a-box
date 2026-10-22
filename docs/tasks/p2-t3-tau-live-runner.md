# Explicit local live tau runner

Status: implemented and tested with synthetic receipts, fake kernel observations,
real subprocess pipes and fake loopback HTTP. **No model, GPU, Docker, external
endpoint or benchmark run was performed for this implementation.** No quality,
hardware or confidential qualification is claimed.

This builds on the [local vLLM transport](p2-t3-tau-local-vllm.md) and
[isolated parent-pipe runner](p2-t3-tau-container-pipes.md). The separate
`scripts/tau_live_trial.py` CLI selects live transport explicitly. The default
`scripts/tau_trial.py` CLI and its scripted outputs remain unchanged.

## Execution boundary

- The unchanged pinned worker executes the original upstream task, tools and
  `ALL_WITH_NL_ASSERTIONS` scorer. It still has no network, no credentials, a
  read-only payload, bounded scratch space and Docker logging disabled.
- The shared `tau_pipe_driver.run_process` owns attach, framing, deadlines and
  child cleanup. Its admitted live session is `LocalVllmSession`, using the
  existing single Bridge/Budget for agent, simulator and evaluator. There is no
  second ledger, scorer implementation, generation retry or budget reset.
- Source preparation, complete payload hashing, source-only host imports,
  upstream/archive/wheel pins and worker runtime gates are retained. Live
  preparation adds the live source closure and `live-binding.json` to that
  verified payload; private endpoint/process data is not copied into it.
- Original worker aliases containing `scripted` and the plan's
  `scripted-parent-pipe` value describe the existing pipe protocol. Neither lets
  the worker select live HTTP. A live-bound payload is rejected by the scripted
  runner; the explicit live runner must additionally authenticate its deployment.
- Trial work remains capped at 180 seconds, inside the existing 300-second
  runner operation ceiling and separate 60-second cleanup reserve. Requests
  also obey the frozen manifest's tighter token, time, request and cost limits.
- Diagnostics retain settled usage or unknown-usage reservations even if attach,
  terminal inspection, container exit, identity recheck or cleanup fails. Overall
  pass requires an evaluated trial, clean terminal state and confirmed cleanup.

## Admission trust

The deployment SHA-256 supplied by the operator is an **authenticated input
approval**, not a value to copy from an untrusted endpoint. It commits to the
private manifest and eight original measurement receipts. Before approving it,
the operator must review the receipt producers and their original runtime and
independent tokenizer measurements. A source hash proves which committed script
is named; it does not prove that a measurement was actually performed. This
consumer never manufactures those receipts, executes supplied verifier code,
accepts `verified=true` as proof, or promotes them to attestation.

The base repository has no producer for these live receipts. Obtaining reviewed
original receipts for all four actual deployments is an explicit remaining
prerequisite, owned by the deployment work. Do not translate a stopped historical
lab run, a fixture, `/v1/models`, an engine version string or localhost reachability
into a receipt. Unknown producer commits, missing receipts or binding mismatches
are rejected. No live run is authorized by the commands below alone.

At execution, Linux procfs must expose each admitted API process in the parent's
network namespace and UID. Four distinct PIDs, listening socket inodes and IPv4
ports are required. Simulator and evaluator model identifiers must differ from
each other and from the model under test; their model-file inventories must also
be distinct, so aliases cannot hide shared weights. Revisions must be exact 40-hex commits;
candidate/reference retain the existing pinned DeepSeek revision and tokenizer.

Before HTTP, the parent checks boot identity, PID start ticks, network namespace,
exact command-line hash, executable hash/metadata and `/dev/null` stdout/stderr.
Only a single exact `127.0.0.1:port` listener owned by that PID is accepted;
wildcard and IPv6 listeners on the selected port are rejected. After connecting,
the established reverse socket must belong to that same PID **before sending a
request body**. Process identity is checked again. These checks run under the
request deadline, and the full deployment is rechecked at trial boundaries.
No DNS, proxy, arbitrary URL, credential, redirect or remote Docker context is
supported. Missing procfs visibility fails closed; there is no macOS bypass.

This trusts the local OS, Git checkout and operator-approved measurement
provenance, not the endpoint's assertions. It is not a defense against a malicious
kernel, privileged runtime mutation or a dishonest receipt issuer. Immutable
runtime/weight provenance and logging policy must already have been measured by
the approved producer. Token usage remains **engine-reported**.

## Private input contract

All nine JSON files must be regular single-link, current-user-owned mode `0600`
files in a mode `0700` directory. Reads are bounded to 1 MiB per file, reject
symlinks throughout the path, check identity before/after reading and run under a
whole admission deadline. JSON rejects duplicate keys and nonfinite numbers.
Unknown fields are rejected. No default endpoint, identity or receipt is filled in.

`deployment.json` has exactly:

- `schema`: `nib.tau-local-deployment/v1`.
- `expires_unix`: integer expiry, in the next hour, checked before each connection
  and used to cap the session's monotonic trial deadline.
- `manifest`: the complete existing `nib.tau-transport-smoke/v1` manifest, including
  explicit non-thinking framing, `quality_eligible=false`, the original benchmark
  manifest digest and exactly matching settings.
- `roles`: exactly `candidate`, `reference`, `simulator`, `evaluator`. Each has
  `host` equal to `127.0.0.1`, integer `port` in 1024..65535, and `runtime_sha256`
  and `tokenizer_sha256` of the corresponding receipt files below.

For each role, `<role>-runtime.json` has exactly:

- `schema`: `nib.tau-local-runtime/v1`; `role`, `model`, `revision` matching the
  manifest; `engine_revision` equal to the already reviewed vLLM source revision
  in `tau_local_vllm.REVISION`.
- `producer`: `git_sha`, `script`, `source_sha256`. The producer script must be
  a `host/` or `scripts/` Python file at that locally available repository commit,
  with matching bytes. Git access is offline, without hooks, replacement objects,
  lazy fetching or ambient configuration. This is provenance validation only.
- `endpoint`: exactly `host` and `port`, matching the deployment.
- `process`: `pid`, `start_ticks`, `boot_id_sha256`, `netns_inode`, `exe_sha256`,
  `argv_sha256`, `listener_inode`. Hash the raw kernel boot-ID and command-line
  bytes; hash the executable's full bytes. Positive numeric identities are required.
- `serving`: the existing seven serving digests (`image_sha256`, `overlay_sha256`,
  `arguments_sha256`, `tokenizer_sha256`, `tokenizer_config_sha256`,
  `chat_template_sha256`, `precision_sha256`). Candidate/reference must match
  the frozen benchmark exactly. For this live contract, `arguments_sha256` is
  the raw command-line digest, equal to `process.argv_sha256`.
- `model_files_sha256`: digest of the measured complete model file inventory;
  candidate/reference require the committed pack's `files.sha256` digest.
- `measurements`: exactly those seven serving keys plus `model_files_sha256` and
  `framing_sha256`. Each has `expected_sha256` and `observed_sha256`; both must
  match the corresponding frozen identity. These are original producer
  observations, not permission to fabricate equality rows.
- `logging`: exactly `stdout: "null"`, `stderr: "null"`, `request_logging: false`,
  `access_logging: false`. The producer must verify this policy, including file
  logging; procfs additionally checks the current standard-output descriptors.

For each role, `<role>-tokenizer.json` has exactly:

- `schema`: `nib.tau-local-tokenizer/v1`; `producer` as above;
  `runtime_receipt_sha256` binding the exact runtime receipt.
- `framing_sha256`, `tokenizer_sha256`, `tokenizer_config_sha256`, `context_tokens`,
  matching that runtime and the transport framing.
- `cases`: exactly `text` and `tool-history`. Each contains `case_sha256`,
  `independent_tokens_sha256`, `endpoint_tokens_sha256`, `independent_count`,
  `endpoint_count`. Counts must be positive, identical and within context; token
  digests must match. The independent producer must use the measured tokenizer,
  full framing and tools, and retain the source-bound case and token-ID hashing
  recipe privately. Engine counts compared with themselves are not parity proof.

These inputs contain no model responses, benchmark trajectories, credentials or
external addresses. The evidence retains only their digests, source hashes,
selected task/seed, fixed statuses and numeric accounting. Paths, ports, PIDs,
raw requests, response bodies, headers and exception text are not exported.

## Commands

Run only from the committed implementation head. First freeze the original
[benchmark manifest](p2-t3-tau-preflight.md), review actual deployment receipts,
and obtain separate run approval. Keep the manifest in a private `0700` directory
as a `0600` file. Preparation is offline and does not contact engines:

```sh
python3 -I -S -B scripts/tau_live_trial.py prepare \
  --source-archive PRIVATE_ARCHIVE --wheelhouse PRIVATE_WHEELHOUSE \
  --manifest PRIVATE_MANIFEST --manifest-sha256 MANIFEST_DIGEST \
  --domain DOMAIN --task-id TASK --trial 0 --role candidate \
  --deployment PRIVATE_DEPLOYMENT --deployment-sha256 APPROVED_DEPLOYMENT_DIGEST \
  --output NEW_PRIVATE_PAYLOAD_PARENT
```

On the approved Linux host, with the four independently verified engines already
running and the pinned worker image already local:

```sh
python3 -I -S -B scripts/tau_live_trial.py run \
  --payload PRIVATE_PAYLOAD_PARENT/payload --plan-sha256 PLAN_DIGEST \
  --deployment PRIVATE_DEPLOYMENT --deployment-sha256 APPROVED_DEPLOYMENT_DIGEST \
  --docker-socket PRIVATE_LOCAL_SOCKET --output NEW_PRIVATE_EVIDENCE
```

The runner does not start, stop or reconfigure engines or provision hardware. It
owns only its isolated scorer container. `run.json` records the explicit live
command with digests and redacted paths. `output.log` is a bounded content-free
diagnostic, not captured engine or attach output. Live reports use
`declared-unverified` and `engine-reported`, never `synthetic-not-tokenized` or
quality acceptance. Operational pass means this one task was evaluated, not that
the task reward was successful or the full benchmark passed.

## Verification

```sh
UV_CACHE_DIR=/private/tmp/nib-uv-cache python3 -B -m unittest discover -s scripts -p 'test_tau_live_trial.py'
UV_CACHE_DIR=/private/tmp/nib-uv-cache python3 -B -m unittest discover -s scripts -p 'test_tau*.py'
```

Tests replace measurement provenance and kernel observations explicitly; those
substitutions exist only in tests. Fake HTTP exercises every active role through
the real pipe driver and accounting, including malformed usage, truncation,
worker death, terminal exit/inspection/identity failure and cleanup failure.
Existing HTTP tests cover deadlines, oversized/slow responses and protocol bounds.
The original worker/scorer code and pinned runtime were not changed.
