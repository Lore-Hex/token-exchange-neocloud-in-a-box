# Optional Guest Network Boot Failure

Original metadata from the one authorized CPU-only
[run 39873097231](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39873097231),
attempt 1, producer `4fe0bd7df8f6ce3293e6dca609106b37dd9676a3`.
The debug wrapper ran 2026-10-22 23:45:24Z through 23:47:02Z.
Both explicit workflow inputs, `debug_boot` and `guest_network`, were true.

The authenticated public-input fetch and optional native export steps passed.
The private debug UKI assembled, and the guest emitted the initrd marker.
The boot failed: root proof was absent, the protocol was incomplete, and no
guest failure stage was received. QEMU exited zero without stdout/stderr.
That does not prove the network executable started or identify a firewall,
configuration, capability, namespace or readback failure. No cause is inferred
from the missing root marker. The required network unit could suppress the
root probe on failure.

`resources.json` reports the existing 2 GiB/2 CPU/128 PID/swap-zero limits,
2 GiB peak accounting, zero OOM kills, an empty cgroup and verified removal.
The worker's `private_work_removed=false` is preserved: the failed child did
not complete its cleanup. The independent parent then removed the private
images and verified cleanup before publishing `validation.json` and the
other three JSON files. No raw serial, journal, firewall output, GPT or UKI
was uploaded or retained here.

The downloaded ZIP's SHA-256 matches the GitHub artifact digest:
`8ca07562370a4e213de0f33a96ede7212a0e8fb9068d6efb94e72b1313cf3038`.
It contained exactly the four regular JSON files, copied byte-for-byte:

| Original | SHA-256 |
|---|---|
| `validation.json` | `afa21d099f290f5aeef626cf1993efe47ee34602f24ada4ff7dd3d5d07442426` |
| `resources.json` | `36f2d94be8e9135eef4b365259211f93487e097af8d602276b0000dbc14c2799` |
| `worker.json` | `3671b5ac0ea8229715ae689a93fc51d0a6fa73e69e36e5b40710d6d14ed51f92` |
| `run.json` | `945429f2ddc6b8419c6c572206b3cc65da8c4bc312ffa30d195273b9bcc9c942` |

`output.log` is an exact duplicate of original `validation.json`, not invented
console output. Regression tests rehash every original, compare each source
digest to its committed producer blob, verify cleanup and retain failure.

This is not a network-service pass, successful optional-profile boot,
connectivity test, reproducibility result, TDX/GPU proof or production-service
qualification. The next diagnostic implementation is separate from these
unchanged observations and requires review and run authorization.
