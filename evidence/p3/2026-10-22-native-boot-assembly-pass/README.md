# Native initrd and unsigned UKI assembly passed

Original receipts from [workflow 39819992457](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39819992457),
attempt 1, producer `eee2388421e09568ae28951d0d747c84267f1fea`.
The bounded native operation ran on GitHub-hosted Ubuntu 22.04 x86-64 from
2026-10-22 17:09:14 to 17:13:30 UTC. The workflow completed successfully.

`run.json`, `result.json`, resource receipt and selected original diagnostics
are copied byte-for-byte from its artifact. `output.log` is an exact duplicate
of the producer's `result.json`, not a reconstructed report. No image is included.

Verified by the committed producer:

- All 212 packages configured; five driver modules compiled and installed.
- Runtime startup policy and all three appraiser CLI help probes passed.
- EROFS, GPT partition identities and userspace verity passed; a wrong root
  hash was rejected.
- Corrected installed-file inventory matched, including the 445-byte
  `usr/lib/os-release`. The boot-input diagnostic is empty after success.
- Initrd membership/content verification and exact UKI PE-section inspection
  passed. Initrd: 82,744,320 bytes and 2,359 members. UKI: 97,898,496 bytes.
- Temporary images and payload were removed; processes exited and the cgroup
  was verified empty and removed. Peak memory: 7,106,478,080 bytes, zero OOM kills.

This is a **native assembly/inspection result**, not a successful boot. The
producer explicitly reports `bootable=false`, `deployable=false`, no signing,
no export, no reproducibility proof, no kernel verity proof and no confidentiality
proof. No GPU was accessed or model served. The prior failed run remains unchanged
in [boot-member-rejected](../2026-10-22-boot-member-rejected/README.md).

The fixed inventory and `.osrel` hash now pass native execution; the prior
failure did not name its member, so this does not retroactively identify it.
Next steps are private verified artifact export, boot/root-switch tests,
tamper-failure tests, reproducibility, then actual TDX/GPU attestation.
