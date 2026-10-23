# Optional Network Boot: Configuration Read Rejected

The one authorized CPU-only diagnostic
[run 39879042935](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39879042935)
used committed producer `4e6a415dcd813736f6db9434b1ed3ee0ace80f4c`, with
`debug_boot=true` and `guest_network=true`. It failed. Authenticated public
fetch, fresh native private export and debug UKI assembly passed. The wrapper
ran from `2026-10-23T00:42:19Z` to `2026-10-23T00:44:03Z`; the hosted job
completed at `00:44:06Z`.

The original proof contains INITRD, then the fixed failure observation:

```json
{"service_result":"exit-code","exit_code":"exited","exit_status":67,"stage":"config-read"}
```

This proves the network helper reached its guarded configuration read after
the filesystem/interface guards. It failed before any nft command. It does
not distinguish directory traversal, file metadata, read or stability checks.
The source inspection and synthetic umask reproduction described in the
[task note](../../../docs/tasks/p3-t1-guest-network.md#configuration-read-follow-up)
are not a retrospective measurement of the deleted guest directories.

Root/complete/boot/confidentiality remain false. The protocol was valid;
QEMU exit zero does not promote failure. Its stdout/stderr byte counts are
zero. The observer's fixed code is not serial or journal content.

Independent parent cleanup confirms private images removed, limit readback,
empty cgroup and group removal; OOM kills zero, peak memory 2,147,483,648 bytes.
The child's original `private_work_removed=false` is retained, not rewritten.
No public binary, network-service success, routing, reproducibility, full
guest readiness, GPU or confidential qualification is claimed. No retry ran.

## Original Artifact

Artifact `12470007412`, `native-debug-boot-39879042935-1`, contains exactly
four regular mode-0600 JSON files, 29,196 expanded bytes. Downloaded ZIP size
11,418 bytes; SHA-256 matches the GitHub artifact digest:
`483fef3fe6bfd3925cd93a4c82979a17d977cecedfb7d0a4a0905ffe049ed902`.

The four originals are byte-identical. `output.log` is an exact duplicate of
`validation.json`, not reconstructed terminal output. No raw serial, journal,
nft output, error strings or binary images were downloaded.

| File | SHA-256 |
| --- | --- |
| validation.json | `afa21d099f290f5aeef626cf1993efe47ee34602f24ada4ff7dd3d5d07442426` |
| resources.json | `bd39ffd3876f4076cc0eb18252cbc3316863a0313390e57f846d4b187cd05c9b` |
| worker.json | `2b4d1cb6094c884ed180cb7e0e3a7c45fce4d355e49031c25300fecaa74ce599` |
| run.json | `4a803517353b79f9c177fe327004593204f479050a7ca220129c3204bcc47eab` |

`scripts/test_debug_network_config_read_evidence.py` verifies all original
hashes, the standard failed-run envelope, both exact committed source closures,
the failed proof and independent cleanup. The earlier
[undiagnosed failure](../2026-10-22-debug-network-boot-failure/README.md) remains
unchanged; this later observation does not retroactively classify that run.
