# P3.T1: Pinned DKMS Package Configuration

[Native run 39713502643](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39713502643)
used producer `6efd0fbf6021afacb30745f76058f3563dfbe910` on October 22, 2026.
Fetching completed in 61 seconds. All 145 guest packages were configured,
driver compilation reached symbol inspection, and the pre-install guard
rejected an unexpected file under `/etc/dkms`. No `dkms install` ran.

The omitted file was not an injected override. Authenticated DKMS package
`1:3.4.3-1ubuntu1`, pinned in
[`r595-ubuntu2404.json`](../../image/inputs/r595-ubuntu2404.json), ships
`etc/dkms/framework.conf.d/autoinstall_all_kernels.conf`. Its exact 30 bytes are
`autoinstall_all_kernels="yes"` followed by a newline. SHA-256:
`e362342a516c0507da4407b889d041a1df7e187f4cb8d05c8a039b1a307d2d4b`.
The archive's SHA-256 is
`6cb516fc913f1afbe8387078b357e15619e19291b9398e0b3cb6d0beae99e2b4`.

The fix adds this one exact file to the authenticated input set, not a wildcard
for arbitrary configuration. Pinned `/usr/sbin/dkms` checks `no-autoinstall`
at the start of its `autoinstall` function, and the package's
`/usr/lib/dkms/common.postinst` checks it before sourcing framework settings.
That sentinel remains required and unchanged. Our requested operation remains
explicit `install` for one pinned NVIDIA version and kernel. Module-loading
capabilities remain absent; unknown overrides, changed bytes, missing files
and symlinks remain rejected.

Original evidence is retained under
[`2026-10-22-dkms-package-config`](../../evidence/p3/2026-10-22-dkms-package-config/run.json).
`output.log` is the original symbol-table stdout, not a successful install log.
The failed result and independent resource receipt confirm process/payload
cleanup, no OOM kills, and a 4,666,441,728-byte peak within the 8 GiB limit.
No success, hardware, boot, reproducibility or confidential claim is inferred
from that failure. The next native attempt must separately prove installation.
