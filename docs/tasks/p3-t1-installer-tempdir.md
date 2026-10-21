# P3.T1: Keep mkosi cleanup in private scratch

Native run [39689622860](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39689622860)
passed the earlier stderr cap, configured packages, normalized timestamps, and
reported a 1.3 GB directory root filesystem. It then exited nonzero during
workspace cleanup. The [original result](../../evidence/p3/2026-10-21-native-tempdir/result.json)
remains failed: inventory acceptance, DKMS validation, image boot, and deployment
are not established. Independent cleanup succeeded with zero zombies.

The original `installer.stderr` traceback identifies
[`mkosi.run.vartmpdir`](https://github.com/systemd/mkosi/blob/ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b/mkosi/run.py)
at line 583: its default `/var/tmp` is inside our authenticated read-only tools
root. That function explicitly honors `TMPDIR` before falling back to `/var/tmp`.

The installation environment now sets `TMPDIR=/tmp`, the existing private tmpfs
in the resource-bounded namespace. No extra writable mount, privilege, host
directory, or network access is granted. Tests prove that an inherited host
TMPDIR is discarded, the exact environment chooses the existing private tmpfs,
and the tools-root mount remains read-only. All failure and independent cleanup
gates stay unchanged. Full `make check` and all final-head hosted checks are
required before another main-branch native installation.
