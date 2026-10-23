# P2.T3: Original Agent Prefix Tokenizer Preflight

Status: measured on native Linux/amd64 with the pinned runtime: **4,785 prefix
tokens plus 256 output reserve exceed the unchanged 4,096-token context**.
The [original receipts](../../evidence/p2/2026-10-23-native-prefix-count/result/run.json)
record a successful measurement, not a successful tau trial or full-request fit.
This is a CPU-only lower-bound check before spending time loading the GPU target.
It does not execute a tau trial, generate a user or agent reply, expose an
endpoint, admit a live deployment, or change the full benchmark's requirements.

## Original Input

The existing sealed scorer verifies the complete original source archive and
locked wheel closure at tau revision
`fc0055dc4e0a316c3f83133267fbd6faaa770992`, then constructs retail task `2`,
trial `0`, seed `42`. It invokes the original agent's `get_init_state()` and
converts its one system message using original `to_litellm_messages`; tools use
original `Tool.openai_schema`. It does **not** initialize or step the orchestrator.
Any attempt to generate a completion fails. The original task and policy are not
truncated, replaced or simplified.

Primary source chain:

- [Original orchestrator construction](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/runner/build.py#L360)
  constructs the actual environment and agent.
- [Original agent initial state and next request](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/agent/llm_agent.py#L79)
  keeps policy/instructions in a system message before adding the user message.
- [Original request conversion](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/utils/llm_utils.py#L388)
  uses `to_litellm_messages` and each tool's OpenAI schema.

The body travels through a bounded private parent pipe. Only its hash, one-message
count and tool count enter the render receipt. The CPU input copy is private,
read-only, temporary and removed with its owning workspace; it is never copied
to the output directory. A crash may leave a private temporary workspace, and
an unfinished output marker does not prove cleanup.

## Exact Runtime Count

### Checkpoint Selection

New runs accept `--model-profile original` (the default) or
`--model-profile uncensored`. Both are fixed identities from
[`deepseek_model_profile.py`](../../scripts/deepseek_model_profile.py), not
arbitrary model names, revisions or paths. The source gate authenticates that
helper and both complete committed manifests. Before creating either container,
the recorder verifies the selected manifest's pinned digest and requires its
tokenizer rows to match the exact runtime tokenizer pins below. Wrong manifests,
unknown selections and tokenizer drift fail before output reservation or launch.

The selected repository, revision and manifest identity accompany the pending
marker, final report and tokenizer provenance. They identify the tokenizer
measurement, **not** a verified weight download or a generated model response.
The original tau policy and tools are unchanged; the model label in that sealed
static request is not sent to an inference endpoint. A checkpoint selection does
not change the 4,096-token context, 256-token reserve, image, resource limits,
deadlines, tokenizer controls or fail-closed isolation.

The uncensored pack's
[pinned manifest](../../models/deepseek-v4.1-flash-1017-uncensored-fp8/1/files.sha256)
contains the same two tokenizer hashes and sizes as the original pack. This is
a source-checked input equivalence, not a new measured prefix count or quality
equivalence. The historical receipts below remain unchanged and apply only to
their original producers and checkpoint. The new checkpoint-selection path is
software-tested; a real selected-checkpoint run remains to be recorded.

The counter reuses the existing pinned engine image, five-stage application,
isolated bootstrap and selected vLLM source, revision
`5df63a7203cdb9328d824a27b682e77c7b9928be`. It independently invokes
`DeepseekV41Tokenizer.from_pretrained` with local-only files and no remote code.
It does not use the scorer's different `tokenizers` version as a stand-in.

Both public files must already be staged from model revision
`1bae0e1088993b9b82de08f59de5be5a34e42170`; preparation and runtime rehash them:

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `tokenizer.json` | 6367257 | `c90dfa01249db1be4245780a052ede752e1361c612ac6d08e2bdada7d599476b` |
| `tokenizer_config.json` | 801 | `6ac8c8dc065ed118161d02dd532749ae3f52c243deac27872134fae2f50d8547` |

The four unchanged [tokenizer parity probes](p2-t3-tokenizer-parity.md) execute
locally as controls. Their counts and ID hashes are retained, not their IDs or
rendered text. This run does not independently compare them with an HTTP server.
The original system/tools prefix is rendered in fixed non-thinking mode, without
truncation or a caller-selected maximum length.

[Pinned vLLM rendering](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/tokenizers/deepseek_v41_encoding.py#L309)
adds the structural User marker after the initial system message and tools.
[The wrapper](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/tokenizers/deepseek_v41.py#L66)
encodes that string without adding further special tokens.
[Tokenizers v0.23.3 added-vocabulary extraction](https://github.com/huggingface/tokenizers/blob/v0.23.3/tokenizers/src/tokenizer/added_vocabulary.rs#L397)
isolates matched added tokens before ordinary tokenization. The pinned vocabulary
has an empty normalizer sequence and a non-stripping User token. The producer
checks those settings and verifies that encoding the exact prefix followed by
that structural marker preserves all prefix IDs. No synthetic user content is
introduced by this boundary check.

Only these outcomes are allowed:

- `context-exceeded`: prefix tokens plus the fixed 256-token output reservation
  exceed the unchanged 4096-token target context. Do not launch that profile.
- `full-fit-unproven`: the static prefix does not exclude the profile. The real
  simulator user turn and every later complete request still require an exact
  count before generation. This is **not** a fit or admission pass.

## Execution And Bounds

```sh
python3 -I -S -B scripts/record-tau-agent-prefix.py \
  --model-profile original \
  --source-archive /PRIVATE/tau2-source.tar.gz \
  --wheelhouse /PRIVATE/tau-wheels \
  --tokenizer-directory /PRIVATE/tokenizer-inputs \
  --docker-socket /PRIVATE/docker.sock \
  --output /PRIVATE/new-prefix-evidence
```

The script performs no downloads. The exact images and existing authenticated
inputs must already be present. The default scorer image profile remains
`docker28-classic`, using the exact index/manifest/config chain documented in
[CPU prompt fit](p2-t3-tau-cpu-prompt-fit.md). An explicit
`--scorer-image-profile docker29-descriptor` selects the existing ordinary
[single-trial image checks](p2-t3-tau-single-trial.md): image inspection explicitly
requests `linux/amd64`, requires the pinned repository/index and matching actual
platform/descriptor identity, and binds the created and terminal container to
that same selected manifest. The chosen profile and observed identity are
recorded. Neither profile falls back to the other on failure; this option exists
only for prefix measurement, not for changing other scorer modes or isolation.

The scorer retains UID 65534, private PID/network namespaces, no interfaces
except loopback, no capabilities, read-only root, no-new-privileges, 2 CPUs,
4 GiB without swap, 64 PIDs and no Docker logs. The tokenizer retains the existing
CPU-entry profile: no GPU or network, private namespaces, all capabilities
dropped, 2 CPUs, 8 GiB without swap, 128 PIDs and bounded noexec caches. No shared
PID namespace, new root privilege or container profile relaxation is introduced.
The existing CPU-entry UID 0 is unchanged; the scorer does not become root.

Scorer preparation is bounded to 180 seconds; its owner keeps the existing
300-second operation and 60-second cleanup bounds. CPU application remains
720 seconds, child entry 300 seconds and host attach 1080 seconds. This does not
build llama.cpp, serve a model or load model weights. Engine imports may probe
absent CUDA or compile dependencies under the existing bounded cache policy.

The existing 8 MiB source/code budget is unchanged. The two exact tokenizer data
files have a separate fixed 6,368,058-byte budget and are included in the same
read-only private bind, not a new host mount. A child count report is at most
4096 bytes; the final metadata output is at most 64 KiB. Owners retain exact
container identity/isolation checks and confirmed removal requirements.

An exclusive `pending.json` is reserved before either container is created.
`render/{run.json,output.log}` records the original static rendering; final
`result/{run.json,output.log}` retains the tokenizer/application observations,
source/input hashes and explicit failure stage. No existing evidence is
overwritten. Exit zero means the **measurement completed with confirmed owned
container cleanup**, even when its disposition is `context-exceeded`; it never
authorizes a GPU launch. Signals, missing reports, source drift and unresolved
cleanup cannot produce a successful measurement.

The implementation and its synthetic tests alone do not establish an actual
token count. The native measurement below does not establish a successful
original tau trial, model quality, paired reference result, production readiness
or confidential computing.

## Native Prefix Measurement

Producer `35b5e2caef9e1113b649ae3f744b26c204867cbf` ran once from
2026-10-23 04:32:58 to 04:36:23 UTC on the retained native Linux/amd64 host.
The original [pending marker](../../evidence/p2/2026-10-23-native-prefix-count/pending.json),
[render envelope](../../evidence/p2/2026-10-23-native-prefix-count/render/run.json),
[render report](../../evidence/p2/2026-10-23-native-prefix-count/render/output.log),
[final envelope](../../evidence/p2/2026-10-23-native-prefix-count/result/run.json) and
[final report](../../evidence/p2/2026-10-23-native-prefix-count/result/output.log)
are copied byte for byte. The earlier failures below remain unchanged.

| Measurement | Original Observation |
|---|---|
| Original static input | One system message and 16 tool schemas; same request digest as both earlier attempts |
| Application preflight / application | 6,410 / 165,138 ms; all eight inventories complete |
| Tokenizer entry | 23,045 ms, checkpoint 16, exit 0, no guard mismatch or exception |
| Static prefix | 4,785 tokens; structural boundary verified; four unchanged local tokenizer controls pass |
| Fixed context / output reserve | 4,096 / 256 tokens |
| Disposition | `context-exceeded`: 5,041 exceeds 4,096 by 945, before actual first-user content |
| Cleanup | Scorer and CPU owners report confirmed cleanup |

This is a lower bound for the **original pinned DeepSeek checkpoint and task**,
not a measurement of another checkpoint or a guarantee for a larger context.
In particular it does not measure or qualify the later dealign checkpoint.
It does not compare the count to a live HTTP endpoint, load model weights,
generate a completion, run the original trial, or prove any complete request
fits. No context, request, concurrency, memory, timeout or admission gate changes.
All quality, full-fit, generation and confidential flags remain false.
The original nested CPU candidate `result=fail`/`BLOCKED` fields remain intact;
the outer `pass` means this bounded measurement completed, not deployment approval.
The original pending marker remains `incomplete`/`unproven`; it is not rewritten
to match the final receipts.

The tests verify all five original sizes and hashes, 22 scorer source pins and
the exact 68-file CPU closure. They replay the committed prefix, application,
progress and entry-diagnostic validators, with negative controls. The private
render body and complete private payload are neither retained nor reconstructed;
their original digest joins are checked, not independently remeasured.
The compact original report contains the four existing synthetic-control token
hashes and the source-derived static-prefix token hash. The scanner exception
permits only these five literal values in `tokens_sha256` at this exact report
path. Positive controls retain detection of unknown values, other fields or
paths, and a different secret candidate on the same line.

Before this one runtime attempt, an operator preflight wrapper incorrectly
inspected the scorer index without the committed `--platform linux/amd64`.
The unchanged manifest validator rejected that index. Read-only comparison
confirmed that the existing explicit platform selection passes. Only the
operator wrapper was corrected; the producer, image profile and validators did
not change. A fresh full source/input/image/resource preflight passed before
the one authorized runtime launch. This wrapper failure is not a model or
runtime failure and did not create a container.

## Original Local Attempt

The single CPU-only attempt at producer
`fb41c77ea96ae9a9d76603d5d98b69497bde80a8` ran from
2026-10-23 01:27:09 to 01:39:26 UTC and **failed before tokenizer entry**.
The [final original envelope](../../evidence/p2/2026-10-23-tau-agent-prefix-local-fb41c77/result/run.json)
and [content-free report](../../evidence/p2/2026-10-23-tau-agent-prefix-local-fb41c77/result/output.log)
are preserved byte for byte, together with the original pending marker and
render receipts. No body, transcript, token IDs or raw container logs are added.

| Stage | Original Observation |
|---|---|
| Sealed original scorer | Pass, 01:27:15-01:27:23 UTC; one system message, 16 tool schemas, no generation; cleanup confirmed |
| CPU application preflight | 10,817 ms |
| CPU application | 709,217 ms; fixed `bounded-cpu-entry-rejected` failure |
| Tokenizer entry | Not reached; application receipt, entry and count observation are null |
| CPU container owner | Completed, `entry_pass=false`, cleanup confirmed; overall attempt failed |

The two measured preparation intervals total 720,034 ms, consistent with the
unchanged shared 720-second application deadline. The report does not identify
the internal inventory/application step or explain why it consumed that time.
It is not a tokenizer failure, a measured token count, a `context-exceeded`
result, a full-fit result, or tau execution. The successful renderer is not a
successful end-to-end preflight.

The tests pin all original bytes and both producers, verify the 22 scorer source
hashes against the original Git tree, reconstruct the CPU source/application
contract, and replay the original failure validator with negative controls.
The discarded private request is not reconstructed: its original payload hash
is preserved, not independently remeasured by these tests.

## Native Comparison

A distinct earlier [native CPU envelope](../../evidence/p2/2026-10-22-cpu-native-39795135328-1/run.json)
and [original report](../../evidence/p2/2026-10-22-cpu-native-39795135328-1/output.log)
are also preserved unchanged. GitHub workflow `39795135328`, producer
`0ea53612f5e0b769eb2652af3be89af89678946c`, ran the older
`tokenizer-continuation` diagnostic on a Linux/amd64 software runner. It failed
before entry: 16,982 ms preflight plus 703,019 ms application, cleanup confirmed.
This is not a native prefix count or evidence for the earlier local TypeError.

That native attempt used the same pinned image/config, 2-CPU/8-GiB container
profile, and byte-identical `engine_application.py`, `engine_overlay.py` and
base container policy. Consequently emulation alone is **not an established
cause**, and a native rerun is not a demonstrated remedy.

At the frozen producer, [application inspection](../../scripts/engine_application.py)
and [stage application](../../scripts/engine_overlay.py) perform two complete
admission inventories, a deletion recheck, and five post-stage inventories,
plus metadata/RECORD reads. [CPU entry](../../scripts/engine_cpu_entry.py) gives
all of them the same 720-second deadline. Page-cache pressure, I/O and emulation
cost are hypotheses, not measured explanations; neither original records
substage timings or cgroup I/O/memory counters. No check or deadline is relaxed.

The unchanged producer can be staged for native Linux using the authenticated
Git bundle, original upstream archive, all 74 locked scorer wheels and the two
pinned tokenizer files. Host orchestration uses Python standard-library/POSIX
APIs, Git and Docker, not Go compilation, host upstream imports or model weights.
The host has no explicit Python 3.12 gate; preparation uses standard-library
`tomllib` and parses pinned source syntax. The independently checked
**container** interpreter remains pinned to 3.12.
Host-version compatibility must be checked before launch, not inferred from the
container version. Image profile selection stays explicit and fail-closed.
Staging is not execution approval: a native attempt requires its own coordinated
resource/cleanup readiness and authorization, with the same bounds and no GPU,
network, shared PID namespace, settings changes or automatic retry.
