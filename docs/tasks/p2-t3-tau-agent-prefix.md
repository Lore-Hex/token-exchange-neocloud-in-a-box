# P2.T3: Original Agent Prefix Tokenizer Preflight

Status: implemented and tested against synthetic fixtures, **not yet measured**.
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
  --source-archive /PRIVATE/tau2-source.tar.gz \
  --wheelhouse /PRIVATE/tau-wheels \
  --tokenizer-directory /PRIVATE/tokenizer-inputs \
  --docker-socket /PRIVATE/docker.sock \
  --output /PRIVATE/new-prefix-evidence
```

The script performs no downloads. The exact images and existing authenticated
inputs must already be present. It uses the existing explicit Docker 28 scorer
image identity profile, not a fallback accepting an arbitrary image.

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

No actual token count, successful original tau trial, model quality, paired
reference result, production readiness or confidential-computing claim is made
by this implementation or its synthetic tests.
