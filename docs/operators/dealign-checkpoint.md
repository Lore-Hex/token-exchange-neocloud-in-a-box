# Separate DeepSeek Checkpoint

The optional `deepseek-v4.1-flash-1017-uncensored-fp8` pack is based on
[dealignai's checkpoint](https://huggingface.co/dealignai/DeepSeek-V4.1-Flash-1017-UNCENSORED-FP8/tree/c19ca5ca4eaeb79d29fdf08aea8327da95b1116a),
frozen at `c19ca5ca4eaeb79d29fdf08aea8327da95b1116a`. It does not replace the
original DeepSeek pack, its served identity, cache or recorded evidence.

## Source Review

Public metadata reports 79 files totaling 510,311,056,155 bytes. The 48
safetensors shards account for 510,296,709,176 bytes. Despite the FP8 name,
the checkpoint retains the base model's mixed native FP8/FP4 format; it is not
a conversion of every expert to FP8. These are remote metadata observations,
not proof of a completed local download.

The committed generator's [metadata run](../../evidence/p0/2026-10-23T045421Z-model-pack-generate/run.json)
passed. Its complete manifest matched a separate public-source audit byte for
byte. This verified small files and published LFS identities, not weight bytes.

The later [October 23 partial seed](../../evidence/p2/2026-10-23-dealign-partial-seed/README.md)
verified and copied 48 matching regular files / 310,737,990,397 bytes from
the original cache. Another 31 files / 199,573,065,758 bytes remain outside
that receipt. It explicitly records `pack_complete=false`, `runtime_ready=false`
and `confidential=false`; it does not prove subsequent full staging or model
execution.

The separate [complete stage receipt](../../evidence/p0/2026-10-23-dealign-stage-complete/README.md)
then verified all 79 files / 510,311,056,155 bytes from 09:28:00 to 11:01:56 UTC
on October 23, reusing 48 files and downloading 199,573,065,758 bytes. Its
terminal proof is distinct from unverified progress counts. This proves local
public-byte staging only, not durable cache upload or backup, runtime readiness,
inference, hardware validation or confidential qualification. The original
partial-seed receipt and its limitations remain unchanged.

The later [complete cache upload receipt](../../evidence/p2/2026-10-23-dealign-cache-upload/README.md)
records all 79 files / 510,311,056,155 bytes copied and verified from
11:04:25.312425 to 12:17:27.349410 UTC on October 23. It reports
`pack_complete=true` with non-atomic `exclusive-directory-copy` publication.
This is a completed cache copy, not power-loss durability, future cache
availability, a GPU restore, runtime execution or model-quality evidence.
Runtime and confidential flags remain false; later use still requires fresh
verification.

Relative to the original pinned `1bae0e1088993b9b82de08f59de5be5a34e42170`:

- The config, tokenizer, tokenizer config, weight index and encoding module
  match the original public file identities.
- 27 weight shards differ. The other 21 have matching published LFS hashes.
- The fork is **not strictly weights-only**: `inference/kernel.py` changes
  `num_stages` to 2, two images are added and several documentation/test-output
  files are removed. The pinned vLLM path does not execute the fork's reference
  inference scripts. Do not enable `--trust-remote-code` or import that code.
- Matching configuration does not prove matching answer quality. The
  publisher's benchmark claims use a different setup and are not acceptance
  evidence for this node.

## Bring-Up Boundary

Use a separate private staging directory and verify the entire manifest,
including non-weight files. Preserve the original pack. Do not use symlinks or
hardlinks to bypass the existing file-identity checks. Public staging uses only
the immutable revision and enforces the same size, hash, deadline and redirect
bounds as the original pack.

The first laboratory run keeps the reviewed vLLM image, GPU-resident Engram
tables, TP4/DP2 topology, 4,096-token context and bounded synthetic workloads.
The full-context pack profile remains a draft, not a tested 1M-context promise.
The selected name, revision, manifest, byte count and response model must agree;
an original-model receipt is not evidence for this variant.

From a clean committed checkout, offline preparation selects the variant
explicitly; omitting `--model-profile` preserves the original checkpoint:

```sh
python3 -I -S -B host/record-stage.py prepare \
  --model-profile uncensored --output "$STAGE_BUNDLE"
python3 -I -S -B host/record-deepseek-lab.py \
  --prepare-local-correctness-b300-dp2 "$LAB_BUNDLE" \
  --model-profile uncensored --tokenizer-parity --tokenizer-materialization \
  --runtime-observation --stream-cohort --stream-profile chat-2k-512-c1-600s
```

These commands prepare source-bound inputs, not a public endpoint. The runtime
uses the identity already frozen into the bundle; a launch-time model override
is rejected. For this first variant bring-up, the older provider-conformance
mode is rejected rather than probing it under the original model's name.

For ordinary eight-B300 experiments, the separately opt-in
[batching, CUDA-graph and DSpark profiles](../tasks/p2-t3-deepseek-tuning.md)
keep this exact checkpoint. They are not measured performance improvements
and cannot be selected for the confidential profile above.

### Host Memory Ceiling

Only this uncensored B300 CC TP4/DP2 lab profile has a **512 GiB total**
container memory ceiling, not 512 GiB per replica. Docker creation and actual
container inspection use the same identity-validating helper. The runtime
observer requires the matching cgroup ceiling. Memory plus swap equals the
same limit, so additional swap remains zero. Original-model DP2 remains
1,024 GiB; original DP1 remains 512 GiB; pre-serve remains 8 GiB. No engine
arguments, GPU selection, 4,096-token context, model identity, contract schema,
deadlines or cleanup policy change. An OOM fails the attempt; there is no
automatic retry or increase of the ceiling.

The operator must freshly gate `MemAvailable >= 640 GiB` after staging and
before launch, with zero swap and no competing workload: 512 GiB maximum
container allocation plus 128 GiB host reserve. This is a capacity preflight,
not a reservation. Both independent weight packs stay in their existing tmpfs
locations; do not copy, link or evict either to manufacture headroom. Private
container cache and SHM tmpfs are charged within the container limit.

The prior original-model [ecdd receipt](../../evidence/p2/2026-10-23-stream-600s-complete/output.log)
reported cgroup `memory.peak = 40,861,405,184` bytes through its after-requests
sample, below its 1 TiB cap. This is not a whole-host peak or evidence that the
new checkpoint fits the lower ceiling. As an **estimate**, the previously
reported 1,630,427,791,360 available bytes minus the new 510,311,056,155-byte
pack leaves 1,120,116,735,205 bytes. Against 512 GiB plus the 128 GiB reserve,
that leaves an estimated 432,921,967,845 bytes; fresh measurements still decide
admission. This change is software-tested resource confinement, not hardware
or production qualification.

A successful functional run is separate from throughput, tau quality,
public-service readiness and confidential qualification. This pack is not
deployable until the applicable phases pass, including immutable model storage
and complete CPU/GPU attestation. Do not label a diagnostic run confidential
merely because its GPUs report CC mode.
