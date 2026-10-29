# Original cache inspection failure

[receipt.json](receipt.json) is the byte-exact content-free receipt from the
operator's approved read-only inspection using committed source
`aa87e9ef0e9a9164e41aebe10681cfa6934bd947`, from
2026-10-29T02:08:50.166342+00:00 to 02:09:01.147751+00:00.
It is 1,834 bytes with SHA-256
`c87747674f82d223fcfe0d47d373db074c00db60edcbb6f08d2fbf908a0916c7`.
[output.log](output.log) is the actual empty log (zero bytes), SHA-256
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
Neither contains private hostnames, addresses, paths or exception text;
neither has been rewritten to add diagnostics or a success claim.
[run.json](run.json) records the inspector invocation with private values
redacted, not the private launch wrapper or an executable retry command.

The result is **fail / worker-failed**, with zero claimed verified files or
bytes. Zero counters do not prove no reads or mounts occurred. The receipt
records cgroup limit readback, empty membership, group removal and zero OOM
kills, but explicitly leaves overall cleanup **unproven**, worker exit and
process-group absence unverified, and private namespace/mount cleanup false.
These flags remain unchanged. The operator separately reported exact owned
processes and cgroup absent in a private monitoring journal; that journal is
not included here and is not retroactive worker acknowledgment.

The old worker let exceptions escape. Its pinned capture helper discards
stderr and rejects nonzero exit before returning stdout, reducing failures to
`worker-failed`. The failure's stage and cause are **unknown**. The operator
reported a later TCP connection to NFS port 2049 and read-only `findmnt --id`
support check at 02:12:18 UTC. Those later checks are not included measurements,
do not reproduce the failed invocation and establish neither cause nor cache
validity. No NFS protocol, permission, layout, mount, hash or cleanup cause is
inferred from them.

The [diagnostic update](../../../docs/tasks/p2-t3-cache-inspection.md#failure-diagnostics)
is tested against synthetic failures only. It is not a successful retry or
approval to operate. Source/ownership/resource limits, independent exact-head
review, CI and separate operator run gates still apply. No inference, serving,
immutable snapshot, runtime readiness or confidentiality is proven.
