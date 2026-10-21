# P3.T1: Preserve nonempty installer output on limit failures

[Native run 39683734488](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39683734488)
at `0fb1cf790d70d778efe26b79dbbc7b46fc2f33e6` confirms the child-reaping repair:
the private process census has no zombies and independent cleanup succeeded.
It also reveals the underlying failure: installer stderr exceeded the existing
64 KiB cap. Package installation and DKMS are not proven.

The worker's output-limit catch wrote both streams even when stdout was empty.
The host correctly rejected that empty artifact before it could collect the
nonempty stderr prefix. This hid the useful diagnostic and classified the outer
run as invalid evidence. The normal nonzero-exit path already skips empty files;
make the output-limit path consistent. Keep the byte, process, time, and resource
limits and the host's strict evidence reader unchanged.

Regression tests exercise stdout-only and stderr-only failures against the real
host reader. Original evidence is in
[the failure bundle](../../evidence/p3/2026-10-21-native-output-limit/run.json).
Run `scripts.test_mkosi_build` and full `make check`, require final-head CI, then
repeat the offline hosted build to inspect its public-source diagnostic prefix.
No bootable, reproducible or confidential image is claimed.
