# token-exchange-neocloud-in-a-box

Open-source software for running GPU inference and selling it through
[TrustedRouter](https://trustedrouter.com/providers/marketplace), without
building a separate customer billing system.

**Real DeepSeek inference works on eight B300 GPUs.** The ordinary-TLS serving
path has passed authenticated API, streaming, tool-calling, long-context, cache
and selected benchmark checks. See the [measured results](#status) below.

The full project targets **confidential GPU inference**: a model inside an Intel
TDX VM, GPUs in NVIDIA confidential-computing mode, and an endpoint whose TLS
key is bound to independently verified hardware attestation. That complete
confidential path is still being built; ordinary TLS is not confidential compute.
The planned confidential nodes target
[DeepSeek-V4.1-Flash-1017](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017)
and [GLM-5.3](https://huggingface.co/zai-org/GLM-5.3) on HGX 8×B200 servers.

**Read [PLAN.md](PLAN.md)** for sizing, the order of work and the phases.
Agents working in this repo read [AGENTS.md](AGENTS.md) first.

## Serve Models, Not a Billing System

This is a provider-neutral toolkit. Bring your own hardware, model and endpoint;
sell inference through TrustedRouter. Keep your operator identity, credentials
and deployment configuration outside this repository.

| This toolkit / your node | TrustedRouter |
|---|---|
| Serve inference behind an authenticated API | Authenticate customers and enforce their credit limits |
| Publish models, capabilities and agreed prices | Discover routes and apply customer pricing |
| Return actual engine token usage, including the final streaming usage chunk | Keep the accounting ledger and bill customers |
| Maintain health, capacity and verified attestation | Handle customer payments and provider payouts under the onboarding agreement |

No local customer accounts, balances, invoices, payment processor or settlement
database. Basic health and content-free lifecycle logs are enough to operate the
pilot; usage travels in API responses, not a second billing ledger.

Start with the [TrustedRouter operator guide](docs/operators/trustedrouter.md).
The [small Qwen pilot](docs/operators/qwen-pilot.md) exercises real inference
locally, and the [service runbook](docs/operators/qwen-service.md) describes its
endpoint configuration. Marketplace activation and payout terms are separate
onboarding steps; running the toolkit does not automatically list a provider.

## Status

**Working real-model inference; confidential production deployment is unfinished.**
Status through October 27, 2026. The results below use the selected
`dealignai/DeepSeek-V4.1-Flash-1017-UNCENSORED-FP8` abliterated checkpoint on
ordinary eight-B300 hardware, with GPU confidential mode disabled. These are
recorded hardware results, not mocks or a live uptime guarantee.

### Verified Functionality

| Capability | Measured result and scope |
|---|---|
| Authenticated OpenAI-compatible API | [17 required provider checks passed](evidence/p2/2026-10-25-selected-public-conformance/README.md), including streaming, usage and rejected-key checks over ordinary public TLS. |
| Native tools | [Tool call and continuation passed](evidence/p2/2026-10-25-selected-tool-pass/README.md); the later tau2 task below also completed with zero tool errors. |
| 1M combined context / 384K output | [655,360 input + 393,216 actual output tokens](evidence/p2/2026-10-26-selected-million-pass/README.md), filling 1,048,576 combined tokens in one completed request. |
| Prefix / KV caching | [All nine retrieval/cache checks passed](evidence/p2/2026-10-26-selected-million-pass/README.md); the warm 1M request reused 1,048,192 tokens, with first content in 1.66 seconds versus 23.64 seconds cold. |
| Long-output throughput | The [long-output request](evidence/p2/2026-10-26-selected-million-pass/README.md) averaged 143.44 output tokens/second across the whole request, including prefill. |
| Concurrent throughput | A [separate bounded-wave experiment](evidence/p2/2026-10-26-selected-batch32/README.md) reached 4,432.11 aggregate output tokens/second at C64. This is not the 1M profile: the combined long-context/high-concurrency configuration remains unimplemented. |

### Quality Calibration

| Benchmark | Result | Coverage |
|---|---|---|
| [tau2](evidence/p2/2026-10-26-tau-external-retail-pass/README.md) | Reward **1.0**, zero tool errors | One original retail task; 14 agent, five simulator and one evaluator calls |
| [GSM8K](evidence/p2/2026-10-26-quality-gsm8k-50/README.md) | **50/50** | First 50 cases |
| [MMLU-Pro](evidence/p2/2026-10-27-quality-mmlu-pro-200/README.md) | **171/200 (85.5%)** | 200 proportionally selected cases; one output-cap truncation counted wrong |
| [IFEval](evidence/p2/2026-10-27-quality-ifeval-200/README.md) | **87.0% strict / 90.5% loose** | First 200 prompts; all requests completed |

These are selected-case calibrations of the abliterated checkpoint, not official
full-suite scores or a comparison against the original DeepSeek weights. See the
[reproduction procedure](docs/tasks/p2-t3-retained-quality.md).

### Still To Finish

- **Confidential operation (P3-P5):** end-to-end hardware attestation, the generic
  TrustedRouter verifier, and the fully verified confidential DeepSeek deployment.
  Earlier CC lab results do not establish that complete path.
- **Production onboarding:** the 1M candidate's catalog is deliberately empty
  because its 59-minute request deadline exceeds the pinned marketplace contract's
  15-minute ceiling. Contract, checker and router support must agree before listing;
  [the finite 1M result](evidence/p2/2026-10-26-selected-million-pass/README.md)
  does not prove marketplace billing or settlement.
- **Broader reliability and quality:** full multi-domain tau2, repeated trials,
  original-versus-abliterated comparisons, a completed 24-hour soak and sustained
  mixed-workload testing. Bounded throughput tests are not production SLOs.
- **Remaining plan scope:** the combined 1M/high-concurrency profile, GLM deployment,
  signed confidential releases and the turnkey operator/fleet workflow. See
  [PLAN.md](PLAN.md) for the unchanged phase exit criteria.

For reproduction, start with the [operator guide](docs/operators/trustedrouter.md),
[1M profile runbook](docs/tasks/p2-t3-million-context.md) and
[quality runner](docs/tasks/p2-t3-retained-quality.md). These are reviewed runbooks,
not a one-command confidential installer.

### Detailed Evidence History

<details>
<summary>Earlier experiments, preserved failures and component-level progress</summary>

The records below retain the scope of each individual run. An earlier failed or
blocked experiment is not the current status of every serving path; later passes
do not retroactively change its result. The status summary above describes the
latest verified capabilities.

The [1M-context / 384K-output candidate](docs/tasks/p2-t3-million-context.md)
**passed its finite C1 trial on ordinary 8xB300**
([original results](evidence/p2/2026-10-26-selected-million-pass/README.md)):
655,360 input plus 393,216 actual output tokens in 45.69 minutes, with zero
retries or unknown usage and confirmed cleanup. All nine retrieval/cache checks
passed through 1,048,320 input tokens. The warm 1M request reused 1,048,192 tokens
and reached first content in 1.66 seconds, versus 23.64 seconds cold.
This is not sustained capacity, quality or confidential qualification. The
candidate still returns an empty model catalog: its 59-minute deadline exceeds
the pinned marketplace contract's 15-minute ceiling. Listing requires coordinated
contract, checker and router support. Existing profiles remain unchanged.

A separate [external restart smoke](evidence/p2/2026-10-26-selected-million-external/README.md)
passed pinned TLS, missing/wrong-key rejection and one authenticated stream.
It records point-in-time reachability, not continuous availability or public
listing, and does not replace the earlier terminal cleanup evidence.

The [October 26 preparation checkpoint](evidence/p2/2026-10-26-million-prerequisites/README.md)
records verified staging of all 510.3 GB, a separate verified read-only copy,
passing host isolation controls, and the exact image/build preflight for the
1M candidate. It preserves the earlier rejected host-library pin as well.
These prerequisites are not a completed inference or capacity result.

The opt-in [128K-context / 16K-output profiles](docs/tasks/p2-t3-long-context-cache.md)
provide bounded chunked prefill, cache namespaces and actual cached-token usage
reporting. The [October 26 graph-profile trial](evidence/p2/2026-10-26-selected-long-graphs/README.md)
**passed on ordinary 8xB300**: 114,688 input tokens plus 16,384 output tokens in
114.83 seconds, with 2.46-second TTFT. All nine retrieval/cache checks passed
through 130,816 input tokens; warm reads reused 32,384 / 65,152 / 130,688 tokens,
while cold and different-namespace controls reported zero hits. Context includes
input and output. The [earlier eager trial](evidence/p2/2026-10-26-selected-long-qualification/README.md)
still records its long-output timeout and unknown final usage. This finite C1
pass does not establish sustained capacity, general quality, a production cache
discount, broad cache isolation or confidential inference; defaults are unchanged.

**Earlier confidential-lab work, separate from the ordinary-TLS service.** Real
8xB200 and 8xB300 lab checks and complete DeepSeek weight downloads have run. Fresh
eight-GPU appraisal and readiness readback have passed. DeepSeek generated real
responses in two earlier smoke runs, passing seven of eight cases. After a
case-only mismatch was diagnosed, an explicit-format fixture passed all eight
cases and ten requests ([evidence and limits](docs/tasks/p2-t3-functional-pass.md)).
A second pass used the [512 GiB lab profile](docs/tasks/p2-t3-bounded-memory-pass.md)
with the same eight cases and ten requests, without OOM and with confirmed cleanup.
The overall qualification and quality gates remain blocked/unproven. The lab
uses AMD SEV-SNP, not the plan's Intel TDX deployment.

The separately selected **abliterated checkpoint** passes all eight fixed
correctness cases and ten requests on an ordinary eight-B300 node
([original correctness evidence](evidence/p2/2026-10-24-ordinary-selected-correctness/README.md)),
plus [17 required provider checks](docs/tasks/p2-t3-selected-provider-evidence.md).
A separate [read-only serving copy](evidence/p2/2026-10-24-selected-weight-view-pass/README.md)
now verifies all 79 files and 510.3 GB while preserving the original checkpoint.
File preparation alone does not establish a running service.
A small graph-enabled stream cohort measured **143.36 output tokens/second**
overall and **98.61 ms median TTFT** across eight completed requests at concurrency
one, excluding warmup ([original stream evidence and limits](evidence/p2/2026-10-24-selected-graphs-stream/README.md)).
A later [eight-concurrent-request burst](evidence/p2/2026-10-24-selected-c8-stream/README.md)
completed all eight requests at **645.28 output tokens/second overall** and
**1,835.91 ms median TTFT**, excluding warmup. This improves aggregate throughput
in the small synthetic test while increasing first-token latency; it is not
a sustained-load guarantee or quality qualification.
A [32-wave C8 attempt](evidence/p2/2026-10-26-selected-sustained-c8/README.md)
completed 256 measured requests without errors at **896.05 output tokens/second**
and **205.68 ms median TTFT**. Its 114.279-second measurement fell short of the
predeclared two-minute minimum, so sustained qualification remains failed.
Repeated-prefix cache state and replica saturation are unqualified.
A [fresh-salt C16/C32/C64 experiment](evidence/p2/2026-10-26-selected-batch32/README.md)
passed its finite functional and cleanup gates: **1,584.36 / 2,859.36 / 4,432.11
output tokens/second**, each over at least 150 seconds, with 3,360 completed
measured requests and zero errors, unknown usage or reported cache hits.
This is bounded-wave throughput, not continuous saturation, quality or
confidential qualification; the outer report remains FAIL/BLOCKED. It does
not change the public C1 service, old evidence or customer prices.
A [combined long-context / batch32 planning helper](docs/tasks/p2-t3-combined-qualification-plan.md)
composes the existing controls and checks fixed deadline budgets offline. It is
**not runnable**: bounded owner sequencing and a separate receipt path remain
unimplemented; public C1 and all existing profiles stay unchanged.
The latest nonroot/read-only selected-service image passed one bounded
[private TLS inference smoke](evidence/p2/2026-10-24-selected-service-inference/README.md)
on the ordinary node: engine/guard/TLS ready, missing/wrong keys rejected,
36 prompt and 13 completion tokens, and cleanup confirmed. It remains unpublished;
this is not public serving, full provider/quality qualification or confidential
inference. The earlier [build-only receipt](evidence/p2/2026-10-24-selected-service-image/README.md)
retains its original scope.
A later [private protocol and active-drain run](evidence/p2/2026-10-25-selected-protocol-pass/README.md)
passed all 17 provider checks and completed an active streamed response through
retirement, with all three roles reaped and host cleanup confirmed. This remains
a bounded private test, not tool/quality qualification or public/confidential
serving; the earlier failed drain observation is preserved.
A subsequent [private native tool smoke](evidence/p2/2026-10-25-selected-tool-pass/README.md)
passed one fixed tool call and its continuation through owned TLS, with actual
usage and confirmed cleanup. No retries were used; this is not general tool
reliability, full tau evaluation, public serving or a capability/privacy claim.
A separate [ordinary day-profile startup](evidence/p2/2026-10-25-selected-day-startup/README.md)
passed external pinned-TLS health and one authenticated stream after a preserved
pre-container failure. Its later [cancelled-owner terminal record](evidence/p2/2026-10-25-selected-owner-cancelled/README.md)
remains FAIL with reported cleanup confirmed and unknown process exit status;
this is not a completed 24-hour soak, public deployment or confidential result.

A completed [finite externally reachable service run](evidence/p2/2026-10-25-selected-finite-owner-pass/README.md)
reached readiness, retired naturally, drained all three roles, and confirmed
watchdog resource cleanup and private-input removal. The separate external
client passed health, authentication rejection and one stream. This is a bounded
ordinary test, not continuous availability, marketplace or confidential service.
An [external pinned-TLS smoke](evidence/p2/2026-10-25-selected-external-smoke/README.md)
subsequently passed health, missing/wrong API-key rejection and one authenticated
stream with 36 input and 13 output tokens. It proves a bounded external API path,
not sustained availability, the owner's terminal cleanup or confidential inference.
A later [trusted-public-TLS conformance check](evidence/p2/2026-10-25-selected-public-conformance/README.md)
passed all 17 required provider assertions, exact catalog matching and rejected-key
checks with zero inference retries. Nine synthetic generations reported 280 input
and 246 output tokens. This is ordinary TLS evidence, not sustained availability,
model-quality qualification, marketplace settlement or confidential attestation.
A separate [closed-NAT CPU experiment](evidence/p2/2026-10-25-selected-nat-pass/README.md)
passed all 15 baseline controls, allowed only the two TLS paths after protection,
blocked 13 other paths including DNS relays, and confirmed cleanup. The same
[isolated experiment on the ordinary GPU host](evidence/p2/2026-10-25-selected-nat-ubuntu26-pass/README.md)
also passed with its separately pinned Ubuntu 26.04 venue and no GPU use.
Neither experiment qualifies persistent public serving or confidential inference.
A later [isolated quarantine proof with private host baselines](evidence/p2/2026-10-25-selected-quarantine-baseline-pass/README.md)
passed: verified quarantine blocked all 15 paths after serving-policy withdrawal,
cleanup was confirmed, and the wrapper reported equal pre/post firewall/DNS
snapshots. Raw host captures remain private; no real-service/public qualification
or continuous host-integrity claim follows.
The [8K-profile parity prerequisite](evidence/p2/2026-10-24-selected-tau8k-parity/README.md)
also passed four fixed cases and eight requests; this does not establish full
8K request fit. Both [DSpark attempts](evidence/p2/2026-10-24-selected-dspark-startup-failure/README.md)
failed before inference. The [bounded diagnostic chain](evidence/p2/2026-10-24-selected-dspark-cause-diagnostics/README.md)
locates a runtime exception in parallel attention projection followed by an
accelerator exception during cleanup; the precise root cause remains unknown.
Cleanup was confirmed. GPU CC was disabled; outer FAIL/BLOCKED records remain
unchanged. These are not a capacity sweep, full tau result, quality acceptance
or confidential qualification.

The October 25 [natural customer-resolution diagnostic](evidence/p2/2026-10-25-selected-customer-resolution-1117/README.md)
used the pinned, unqualified Qwen3-1.7B simulator and stopped at its first email
lookup with `lookup_error` and unknown customer match. Ten measured requests
completed; reward remains null and cleanup was confirmed. No exception-origin
or conversation evidence identifies the underlying cause or assigns model blame.
It is not a tau quality score or simulator qualification.

Software preparation now includes an explicit
[full original retail-task-2 trial with external support](docs/tasks/p2-t4-tau-external-full-task.md),
reusing the pinned scorer and preserving actual task reward. The separate
[first-lookup mode](docs/tasks/p2-t4-tau-external-simulator.md) remains unchanged.
The [full-task path now has a passing live trial](evidence/p2/2026-10-26-tau-external-retail-pass/README.md)
on the retained node with the 32K/2048 request budget and an external simulator.
The first-lookup mode remains preparation only. One successful retail task is
not the full benchmark or matched-reference selected-checkpoint qualification.

The earlier [selected tau diagnostic](evidence/p2/2026-10-25-selected-tau-email-relation-0429/README.md)
failed at the retail customer-email lookup. A bounded boolean records that the
submitted argument occurred case-insensitively in earlier simulator-user text;
it does not establish the correct email, full identity or model blame. No
conversation or arguments were retained. The strict smoke failed with no quality
score and confirmed cleanup; startup and four tokenizer-parity cases passed.
Scoring and retry limits were unchanged. The [preceding email-lookup failure](evidence/p2/2026-10-25-selected-tau-email-rejection-0333/README.md)
did not record that relation. The earlier
[known-tool diagnostic](evidence/p2/2026-10-25-selected-tau-tool-rejection-0245/README.md)
did not record an exact rejection site and remains uninterpreted at that level.

A separate [three-turn CPU identity diagnostic](evidence/p2/2026-10-25-tau-cpu-identity-followup/README.md)
completed transport and cleanup, but detected none of the expected name/ZIP
literals, even after the direct question. No email-like literal was detected.
This does not establish semantic refusal or explain the earlier failure;
the simulator remains unqualified.

The separate [1.7B CPU identity diagnostic](evidence/p2/2026-10-25-tau-cpu-identity-17b/README.md)
also completed three simulator calls and cleanup (1,243 prompt / 53 completion
tokens). Only the third, direct name/ZIP reply contained those expected literals;
no email-like literal was detected. This is transport and literal evidence, not
semantic correctness, simulator qualification or a full tau result.

A later [selected tau controller attempt](evidence/p2/2026-10-24-selected-tau-readiness-failure-1736/README.md)
failed during readiness: both owners exited 1, no READY hashes or scorer result
were recorded, and cleanup remained **unconfirmed**. No inference or quality
score was established; the original report does not identify the root cause.
The [17:41 retry](evidence/p2/2026-10-24-selected-tau-readiness-failure-1741/README.md)
also failed before readiness admission. Its CPU owner reports an inputs-stage
failure with its own cleanup confirmed; controller cleanup remains unconfirmed,
with no owner exits recorded. This is not inference or a quality result.

| Component | State |
|---|---|
| Plan, sizing, phase definitions | written ([PLAN.md](PLAN.md)) |
| CLI skeleton and repository checks | implemented; the CLI is not yet a turnkey serving installer ([development](docs/development.md)) |
| Model packs (pinned revisions and checksums) | source-audited drafts; not deployable confidential profiles ([model packs](models/README.md)) |
| Pinned weight staging | real DeepSeek download completed: 83 files, 510,311,045,295 bytes, exact-set/hash verification PASS and cleanup confirmed; no model execution implied ([evidence](evidence/p2/2026-10-20-weight-stage-verified/run.json), [checkpoint](docs/tasks/p2-t3-verified-weights-checkpoint.md)) |
| Engine candidate overlay preparation | offline committed-source context and installed-wheel checks tested with fake files; BLOCKED, no image build, ABI or hardware validation ([scope and limits](docs/tasks/p0-t4-engine-overlay.md)) |
| Experimental packaging contract | standalone contract tested with synthetic files; exact entry-point map and sealed bytecode removal subsequently exercised by the bounded smoke application below; default admission unchanged ([scope](docs/tasks/p0-t4-engine-packaging-contract.md)) |
| Python layout descriptor | read-only collection complete: exact interpreter chain, packaging and 14 source pins verified; stdlib child canaries pass, overall qualification remains failed, no engine/GPU admission ([evidence](docs/tasks/p0-t4-engine-python-chain.md#recorded-outcome)) |
| Exact-image application contract | standalone preparer and sealed import-path contract tested with synthetic files; later smoke applied all five stages to the exact image, but no real engine launcher or generic admission is granted ([scope](docs/tasks/p0-t4-engine-application-contract.md)) |
| Bounded engine import smoke | latest actual run verified all 12 source references and completed five-stage application; first import rejected the exact `ldconfig -p` query through `ctypes` during Torch extension initialization; remaining imports not run, cleanup confirmed, no model/GPU/ABI or production qualification ([latest evidence](evidence/p0/2026-10-20-official-vllm-distro-callsite-smoke/run.json), [prior coverage gaps](evidence/p0/2026-10-20-official-vllm-callsite-smoke/run.json), [scope](docs/tasks/p0-t4-engine-import-smoke.md)) |
| Pinned engine image diagnostic | exact-image bounded inventory and detailed collection complete; RECORD/wheel and root/selected-root admission rejected; 13 source expectations match, cleanup confirmed; overall FAIL, still BLOCKED ([evidence](evidence/p0/2026-10-19-official-vllm-record-diagnosis/run.json), [scope and limits](docs/tasks/p0-t4-engine-image-diagnostic.md)) |
| Published AOT stub classification | all four installed assets match exact published hashes, sizes and headers in the read-only diagnostic; generic admission unchanged, no native ABI/runtime qualification ([measured outcome and pins](docs/tasks/p0-t4-cutlass-stubs.md#recorded-outcome)) |
| RECORD/wheel cause diagnostics | read-only image run observed two strict-policy mismatches: extra entry-point group and 2,879 unlisted bytecode files with recorded source-name counterparts; selected dependency pins match; no bytecode trust or admission change ([measured outcome](docs/tasks/p0-t4-engine-record-diagnostic.md#recorded-outcome)) |
| Engine metadata census | selected root counted: 204,969 entries and 19,176,556,448 logical regular-file bytes; collection complete, cleanup confirmed, qualification intentionally failed; admission limits unchanged ([evidence](evidence/p0/2026-10-19-official-vllm-metadata-census/run.json), [scope](docs/tasks/p0-t4-engine-image-diagnostic.md#metadata-only-census)) |
| Guest services | implemented with release-deny behavior and tagged fake tests ([guest services](guest/README.md)) |
| Guest package preparation | executable offline archive/control verification and deterministic private repository preparation; nonbootable mkosi candidate only, no installation, guest build or reproducibility claim ([scope](docs/tasks/p3-t1-offline-package-preparation.md)) |
| Linux package-plan rehearsal | offline rehearsal passes after explicit private empty preferences: 145 packages planned, zero removals, cleanup confirmed. No installation or guest-build qualification ([passing rehearsal](evidence/p3/2026-10-20-apt-private-preferences-pass/run.json), [original failure](evidence/p3/2026-10-20-apt-resolve-failure/run.json), [diagnostic failure](evidence/p3/2026-10-20-apt-missing-preferences/run.json), [scope](docs/tasks/p3-t1-apt-diagnostics.md)) |
| mkosi build inputs | exact 27.1 source and 203-package tools graph; nonexecuting private bundle preparation, not installation or a guest build ([scope and blockers](docs/tasks/p3-t1-mkosi-build-inputs.md)) |
| mkosi tools-tree members | exact pinned certificate data, awk preparation aliases and non-host GRUB classification; data-only tar preparation, no ABI or installability claim ([scope](docs/tasks/p3-t1-mkosi-tools-members.md)) |
| mkosi tools startup | seven fixed offline Linux/amd64 binary startup checks pass; sandbox cleanup confirmed. No package installation, mkosi execution, boot or attestation ([evidence](evidence/p3/2026-10-21-tools-startup-pass/run.json), [scope](docs/tasks/p3-t1-tools-startup.md)) |
| mkosi tools dependency plan | all 203 pinned packages resolve offline with zero removals and confirmed cleanup; no package installation or image build ([evidence](evidence/p3/2026-10-21-tools-apt-plan-pass/run.json), [scope](docs/tasks/p3-t1-tools-apt-plan.md)) |
| Native guest package installation | all 145 exact guest packages installed and configured in a bounded offline Linux namespace; cleanup verified ([evidence](evidence/p3/2026-10-21-native-install-pass/run.json), [scope](docs/tasks/p3-t1-native-install-pass.md)) |
| Native NVIDIA driver compilation | five R595.91.07 modules compiled for the pinned 6.8.0-147 kernel; exact compiler, module hashes and independent cleanup recorded. No installation, loading, LKCA, boot or reproducibility claim ([evidence](evidence/p3/2026-10-22-native-driver-pass/run.json), [limits](docs/tasks/p3-t1-native-driver-pass.md)) |
| Native NVIDIA driver installation | all five installed modules match their compiled objects; dependency indexes, crypto references and cleanup verified. No module loading, boot or confidential-service claim ([evidence and limits](docs/tasks/p3-t1-driver-install-pass.md)) |
| Native unsigned boot-image assembly | all 212 runtime packages, installed driver modules, userspace verity check, initrd and unsigned UKI assembly pass with cleanup confirmed. Images were deleted; no boot, export, signing or reproducibility claim ([original evidence and limits](evidence/p3/2026-10-22-native-boot-assembly-pass/README.md)) |
| Native private boot-image export | actual GPT/unsigned UKI export and independent file rehashing pass; images deleted before metadata-only upload, no OOM, cleanup confirmed. No boot, signing, reproducibility or confidentiality claim ([original evidence](evidence/p3/2026-10-22-native-private-export-pass/README.md)) |
| CPU-only guest network boot | native export, initrd, strict firewall readback, root and completion proofs pass under TCG; independent image and resource cleanup confirmed. No NIC/GPU, connectivity, production, signed-release or confidential qualification ([original evidence and limits](evidence/p3/2026-10-25-debug-network-pass/README.md)) |
| Native repeat-build comparison | two fresh same-commit builds produce identical root GPT, verity root and unsigned UKI hashes; the reusable two-build/comparison workflow also passes end to end. Original failures preserved. Same-day trusted-builder comparison only, not boot, signing, cross-platform or confidential qualification ([workflow evidence](evidence/p3/2026-10-22-native-repeat-workflow/README.md), [finalization evidence](evidence/p3/2026-10-22-native-root-finalization/README.md)) |
| Build isolation prerequisites | Linux namespace/PID/ownership/capability/syscall probe and separate cgroup resource-limit probe pass; not yet combined into an installer, no image build ([isolation evidence](evidence/p3/2026-10-21-final-confinement/run.json), [resource evidence](evidence/p3/2026-10-21-build-resource-limits/run.json)) |
| GPU-free end-to-end node | tested against fakes locally and in CI; independently reviewed and merged in [PR #10](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/pull/10) ([evidence](evidence/p1/2026-10-19-combined-node-v4/run.json), [results](evidence/p1/2026-10-19-combined-node-v4/result.json)) |
| Local real-model provider pilot | Qwen3-0.6B CPU inference through standard TLS: 17 conformance checks, auth, usage, upstream failure and cleanup pass on native macOS and an offline Linux/amd64 container; not public or confidential ([native](evidence/p3/2026-10-19-qwen-native-regression-1/run.json), [Linux](evidence/p3/2026-10-19-qwen-linux-4/run.json), [runbook](docs/operators/qwen-pilot.md)) |
| Provider-configurable Qwen service | Real Linux model: operator metadata, all 18 provider checks, Catalog v2 and declared retirement pass; active streaming response finishes through shutdown; no public cloud or billing claim ([evidence](evidence/p3/2026-10-19-provider-neutral-linux/run.json), [results](evidence/p3/2026-10-19-provider-neutral-linux/output.log), [runbook](docs/operators/qwen-service.md)) |
| Earlier model bring-up and baseline | eight fixed functional cases and ten requests pass. The October 23 streaming baseline completes all eight measured requests at concurrency one: 10.628 output tokens/second, median TTFT 286.824 ms; that run does not establish load, quality/tau or confidential qualification. Later selected-checkpoint results are in the status summary above ([functional evidence](docs/tasks/p2-t3-functional-pass.md), [original stream evidence and limits](evidence/p2/2026-10-23-stream-600s-complete/README.md)) |
| Private provider checker | real DeepSeek private API: all 17 mandatory upstream 0.1.8 checks pass across tiers 1-4; declared catalog deliberately skipped, owned identity and cleanup confirmed. API conformance only; overall FAIL/BLOCKED, no tau/quality, public-service or confidential qualification ([original evidence and limits](evidence/p2/2026-10-22-provider-conformance-pass/README.md)) |
| Tau evaluation preparation | actual scripted upstream trial passed with confirmed cleanup. [Original evidence and limits](docs/tasks/p2-t3-tau-integration-evidence.md); synthetic replies and usage, not live-model quality or tau acceptance |
| CPU tau prompt/parser compatibility | original fixed task 2 probe passes with real Qwen support models: simulator 382 prompt / 24 output tokens, evaluator 281 / 88; bound release and cleanup confirmed. Target is scripted: no DeepSeek, full tau, quality or confidential claim ([original evidence and limits](evidence/p2/2026-10-22-tau-cpu-prompt-fit-pass/README.md)) |
| Original DeepSeek tau prefix | native CPU measurement: 4,785 static policy/tool tokens + 256 output reserve = 5,041, exceeding the unchanged 4,096 context. Cleanup confirmed; not full-request fit, another checkpoint's count, tau execution or quality ([original evidence and limits](docs/tasks/p2-t3-tau-agent-prefix.md#native-prefix-measurement)) |
| Runtime tokenizer parity | real eight-B300 rerun passes all four unchanged cases: text, system, tools and tool continuation match token-for-token and generated input usage; eight requests, no OOM, cleanup confirmed. Earlier failure preserved; no general tokenizer equivalence, quality/tau or attestation acceptance ([fixed-run evidence and limits](docs/tasks/p2-t3-fixed-parity-evidence.md)) |
| Owned API runtime observations | real eight-B300 run confirms stable process/executable/loopback-listener identity before and after eight passing parity requests; peak cgroup memory 38.4 GiB, no swap/OOM, cleanup confirmed. Not GPU/whole-node memory, tau admission or confidential qualification ([original evidence and limits](evidence/p2/2026-10-22-tokenizer-parity-runtime-pass/README.md)) |
| CPU-only startup diagnosis | exact-image application passes all five stages; serving imports now reach checkpoint 13 with clean guard state, then device autodetection rejects the GPU-free environment. Overall pre-serve run remains FAIL; cleanup confirmed; no inference/GPU/confidentiality acceptance ([latest run](evidence/p2/2026-10-20-cpu-opencv-runtime-9115b95/run.json), [root cause and limits](docs/tasks/p2-t3-opencv-native-loader.md#exact-image-result), [earlier Botocore observation](evidence/p2/2026-10-20-cpu-native-39467970211-1/README.md)) |
| GPU appraisal and gated readiness | fresh eight-device appraisal PASS; nonce, measurements, certificates/revocation, secure boot, debug-disabled and local identity checks passed; readiness 0 to 1 readback and NVML shutdown verified. Not full hardware/runtime/confidential qualification ([success](evidence/p2/2026-10-20-gpu-appraisal-121447763215/run.json), [prior failure](evidence/p2/2026-10-20-gpu-appraisal-120201396352/run.json), [checkpoint](docs/tasks/p2-t3-appraisal-checkpoint.md)) |
| Bounded DeepSeek CC functional lab | TP4 on four B300s and TP4 x DP2 on eight B300s passed all eight fixed cases and ten requests, including two-turn tools; cleanup confirmed. Overall candidate remains blocked, without confidential-service or quality qualification ([eight-GPU evidence](evidence/p2/2026-10-22-b300-dp2-functional-pass/run.json), [limits](docs/tasks/p2-t3-dp2-functional-pass.md)) |
| Confidential pipeline on a 1-GPU TDX VM | not validated end to end (P3); preparatory image/build/CPU-boot work is recorded above |
| TrustedRouter generic verifier | not started (P4) |
| Bare-metal 8×B200 confidential node | not started (P5) |

This table only says "validated" for things with committed evidence from real
hardware.

The P1 container run passes provider-check Tier 4, tagged fake verification,
fourteen rejecting mutation checks, admission, privacy and weight-gate tests.
The release verifier rejects the fake evidence. Runtime has no external
network, API secrets or GPU access; container and image removal are verified.
This is software integration evidence, not confidential inference or hardware
validation. See the [P1.T3 task record](docs/tasks/p1-t3-container-e2e.md).

</details>

## Start contributing

```sh
make check
make build
./build/nib --help
```

See [development instructions](docs/development.md) for prerequisites. Real-model
inference is covered by the runbooks and evidence above. Deployment of a
confidential route additionally requires the remaining hardware validation,
independent attestation and onboarding gates before listing on
[NY Token Exchange](https://nytokenexchange.com).

## Related projects

- [trustedrouter-confidential-stack](https://github.com/Lore-Hex/trustedrouter-confidential-stack):
  the `trcs` shim and the `tr-attestation/v0` protocol this project builds on.
- [trustedrouter-provider-check](https://github.com/Lore-Hex/trustedrouter-provider-check):
  the conformance checker every node must pass.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). Report security issues
privately as described in [SECURITY.md](SECURITY.md).
