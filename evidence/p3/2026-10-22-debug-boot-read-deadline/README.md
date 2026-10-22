# CPU-only mapped-root read deadline

Original four JSON files from the one authorized diagnostic
[39856626007](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39856626007),
attempt 1, exact producer `6a15eee38c82225bc6649218829532d5ed135d63`.
The overall result is **fail**. Earlier unclassified and ROOT_READ failures
remain preserved in their separate evidence directories.

Authenticated inputs and fresh native export passed. The debug worker ran
from 21:29:41 through 21:31:43 UTC on October 22, 2026. Debug UKI assembly and
all five pinned runtime startup checks passed. The fixed channel reports:

- INITRD observed, root proof incomplete, `guest_failure: ROOT_READ`.
- `root_read.reason: deadline`, `bytes_read: 171704320`, `elapsed_seconds: 23`.
- No protocol error. QEMU exit 0, no stdout/stderr, process group cleaned.

The committed probe reached its loop-deadline check after reading this many
mapped bytes. Elapsed seconds are floored; the receipt does not measure exact
fractional duration or identify an I/O, hashing, emulation or memory-pressure
bottleneck. The 24-second deadline covers other checks as well as reading.
It is not an EOF failure, successful full read, or complete root proof.

**Estimate, not a measurement:** using the upper rounded read duration of
24 seconds and the original GPT's 928,968,704 bytes as an upper bound on mapped
data, linear extrapolation is `928968704 / 171704320 * 24 = 129.85` seconds.
Actual mapped data is smaller than the complete GPT; sustained throughput is
not proven constant. This estimate cannot guarantee a successful later boot.

The independent wrapper verified zero OOM kills, an empty removed cgroup,
unchanged resource limits and deletion of the private images/input bundle.
The worker's `private_work_removed: false` is retained unchanged; the parent
performed final cleanup. Overall failure is never promoted from clean exit.

The four JSON files are byte-identical to the originals. `output.log` exactly
duplicates `validation.json`, not console output. Artifact `12461255214`
contains four regular mode-0600 files totaling 22,542 uncompressed bytes.
Its 8,892-byte ZIP SHA-256 matches GitHub's digest:
`bcb0286d12f3728d80309b81ba301a36c4027cc78c6ea2a6c3b0e98395477247`.
Both source closures match the producer's 42 native and 28 debug source files.
No images or serial logs were retrieved. No retry, TDX/GPU CC, complete guest
service, GCP compatibility, release or reproducibility qualification is claimed.
