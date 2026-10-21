# P3.T1: Explicit bounded package-install stderr

Native run [39686959523](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39686959523)
failed with `output-limit` while unpacking authenticated public packages.
The [original result](../../evidence/p3/2026-10-21-native-stderr-budget/result.json)
reports zero zombies and confirmed cleanup. The preserved `installer.stderr`
is exactly 65536 bytes of normal apt/dpkg progress, not an identified package
installation error. There is no completed installation, DKMS, image, or boot claim.

[mkosi run.py at ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b](https://github.com/systemd/mkosi/blob/ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b/mkosi/run.py)
redirects default child stdout to its own stderr. The installation caller
specified a 2 MiB stdout budget, while the shared process supervisor retained
an unrelated fixed 64 KiB stderr cap. Thus ordinary package progress exhausted
the wrong budget before the 145-package transaction completed.

The supervisor now accepts an explicit stderr budget, defaulting to the same
64 KiB for every existing caller. Only this offline native package installer
requests 2 MiB on each stream, matching the existing artifact reader's bound.
The optional stderr budget must be a positive integer no larger than 2 MiB and
requires bounded capture. Exceeding either stream still fails, retains only its
bounded prefix, and kills/reaps the owned process group. Timeout, namespaces,
resource limits, exact package inventory, and independent cleanup are unchanged.
There is no output truncation treated as success and no suppressed error exit.

Real subprocess tests cover default 64 KiB rejection, explicit success at
65537 bytes and exactly 2 MiB, failure at 2 MiB plus one byte, and group absence.
Invalid budgets fail before spawning. Installer tests require explicit budgets
for both streams and retain nonzero exits as failures. Source-binding hashes
are refreshed for the shared helper; historical evidence is unchanged.
