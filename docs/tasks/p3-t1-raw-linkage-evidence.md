# P3.T1: Retained Native Crypto Linkage Evidence

The native build at producer
`e01eb89fee56f36686b27c74dd92713970db93f4` passed on an Ubuntu 22.04
GitHub-hosted runner on 2026-10-22. See
[workflow 39704351859, attempt 2](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39704351859/attempts/2).

The [original receipts](../../evidence/p3/2026-10-22-crypto-symbols-pass/run.json)
record all 145 expected packages configured, five R595.91.07 kernel modules
compiled for 6.8.0-147 and strong undefined references to
`crypto_alloc_aead`, `crypto_alloc_akcipher` and `crypto_alloc_shash`.
[output.log](../../evidence/p3/2026-10-22-crypto-symbols-pass/output.log) is an
exact copy of the exported `dkms-symbols.stdout`, including upstream spacing.
It contains only the native symbol table, not inferred or reconstructed output.
The test reparses that table through the production linkage validator and
compares it with the receipt.

The original [cleanup report](../../evidence/p3/2026-10-22-crypto-symbols-pass/evidence-resources.json)
confirms zero OOM kills, an empty and removed cgroup and a 4,665,389,056-byte
peak below the unchanged 8 GiB limit. Payload and process cleanup also passed.
The five module hashes match the
[preceding compilation](../../evidence/p3/2026-10-22-crypto-linkage-pass/result.json).
This narrow module-byte observation is not full-image reproducibility.

The same workflow's
[first attempt](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39704351859/attempts/1)
failed before package execution. Its original
[fetch diagnostic](../../evidence/p3/2026-10-22-input-fetch-timeout/output.log)
records curl exit 28 for a 77,218-byte input. No worker `run.json` existed at
that stage, so none has been invented. The retry used unchanged pins and
resource limits; the failed attempt remains separate evidence.

No module was installed or loaded in this compilation-only run. Runtime LKCA,
boot, UKI/verity outputs, full guest reproducibility, hardware attestation and
confidential deployment remain unproven. The previous proof's missing raw
table was not backfilled; this is a new independent run after the export fix.
