# Native Root Finalization Repeat Build

**The two build receipts match.** Both attempts of
[workflow 39845038154](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39845038154)
built exact producer `e0785af803f9e5752ca76bd3f6bf548203d7ba3f` with all six
native build/export flags enabled. Attempt 2 reran the original workflow commit,
not moving main. Each job installed the pinned packages and compiled the driver
in a fresh bounded offline namespace.

- [Attempt 1](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39845038154/attempts/1):
  build 2026-10-22 20:07:46-20:10:39 UTC; job `120055989285`.
- [Attempt 2](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39845038154/attempts/2):
  build 2026-10-22 20:14:08-20:16:38 UTC; job `120061515956`.

The five metadata JSON files in each directory are original downloaded bytes.
`output.log` is an exact copy of that attempt's final `validation.json`, not a
reconstructed console log. Each job independently rehashed its private exported
images and removed them before uploading metadata. No images, raw filesystem
contents or native logs were uploaded. Both resource receipts confirm zero OOM
kills and an empty, removed cgroup.

The committed producer's `scripts/compare_native_exports.py` generated
[comparison.json](comparison.json) with exit 0:

| Output | Matching SHA-256 / root hash |
|---|---|
| Root GPT, 928,968,704 bytes | `1db9e647c887f1d7abdee8b8155f0f73dbbc475dfb08464d04872a15e3e33a08` |
| Unsigned UKI, 97,898,496 bytes | `20b151d0c44719e6005e40dad1a329865ccc47bcd52a33370336518357d11609` |
| Verity root | `6b64b8e318419c17a7414e27978de9e51874489332ab947b42880ad8bca2e4af` |

All eight post-finalization filesystem buckets and selected retained files match.
All three discarded build-only artifacts have different content hashes between
the attempts. This supports the narrow finalization fix for this observed pair;
it does not isolate the necessity of each removal through separate experiments.
The earlier [root-tree mismatch](../2026-10-22-native-root-tree-mismatch/README.md)
and [first mismatch](../2026-10-22-native-repro-mismatch/README.md) remain unchanged.

This is a same-day, same-source, trusted-builder receipt comparison. It does not
prove cross-day or cross-platform reproducibility, independent builder trust,
boot, signing, hardware attestation, production service or confidentiality. The
original qualification booleans remain false. Automatic repeat-build workflow
enforcement and the remaining guest/runtime validation are separate work.
