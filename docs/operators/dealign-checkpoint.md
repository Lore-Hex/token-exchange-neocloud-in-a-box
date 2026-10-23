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

A successful functional run is separate from throughput, tau quality,
public-service readiness and confidential qualification. This pack is not
deployable until the applicable phases pass, including immutable model storage
and complete CPU/GPU attestation. Do not label a diagnostic run confidential
merely because its GPUs report CC mode.
