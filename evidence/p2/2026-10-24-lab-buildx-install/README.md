# Buildx Installation and Idempotent Recheck

Original reports from the approved ordinary Linux/amd64 lab installation and
its idempotent recheck, supplied for producer
`4041d6059756c2fba5a0a6aea5bbc8a8ae708c64`:

- [Installation](buildx-install-4041d60.json): `result: pass`, `installed: true`.
- [Recheck](buildx-idempotent-4041d60.json): `result: pass`, `installed: false`.
- Both report Buildx `v0.38.1`, 68,366,498 bytes, binary SHA-256
  `e25d5cd675092bb5d1d03632fdc46cdf3fede7b514dc5f023a105e00d3ce5ad4`.
- Both bind the installer source SHA-256
  `fcf18b2714f5ecef13f365a8d764f53de9f07ee920ee1e1eb130da1dc7a1c287`.

These two files are byte-exact originals, not fabricated evidence envelopes.
They contain no Git revision or timestamps; the supplied producer attribution
is checked against the retained commit's exact script bytes. No missing fields
are invented. The original `hardware_validated` and `confidential_qualified`
flags remain false. This establishes only the installer's reported prerequisite
success, not inference, model quality, full tau, or confidential-compute success.
The committed installer does not restart the Docker daemon.

[Regressions](../../../scripts/test_lab_buildx_evidence.py) pin both original
file hashes, require exact producer ancestry, and compare the closed report
shape and version/binary/source hashes to the original source without running
the installer. See the [installer contract](../../../docs/tasks/p2-t4-lab-buildx.md)
for its trusted-root boundary and pinned official release references.
