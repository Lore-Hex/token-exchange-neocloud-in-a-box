# CPU-only mapped-root read failure

Original four JSON files from the one authorized diagnostic
[run 39851156135](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39851156135),
attempt 1, exact producer `28f9fc01d59bc316ffdb206c8c0434cd5ad191e7`.
The overall result is **fail**. The earlier
[39843193309 failure](../2026-10-22-debug-boot-root-failure/README.md)
remains preserved separately.

Input fetch and fresh native private export passed. The debug worker ran from
20:47:45 to 20:49:17 UTC on October 22, 2026. Debug UKI assembly and all five
pinned QEMU startup checks passed. The fixed channel reports INITRD followed
by `ROOT_READ` failure, with no protocol error. QEMU exited zero with no
stdout/stderr; this is still a failed root proof.

The exact committed probe reached its full mapped-root read stage after the
root mount/device, verity table and device-open checks. At this producer,
`ROOT_READ` does not distinguish size/deadline, short or failed I/O, the extra
EOF read, or close failure. There are no original read counters, and no precise
cause is retrofitted into these receipts. The 2,147,483,648-byte cgroup peak and
zero OOM kills do not establish a read deadline failure.

Independent cleanup verified an empty removed cgroup and deletion of private
images and the input bundle. The failed worker's `private_work_removed: false`
is retained unchanged; final cleanup was performed by the independent parent.

All four JSON files are byte-identical to the uploaded originals. `output.log`
is an exact copy of `validation.json`, not console output. Artifact ID
`12458046047` contains exactly four regular files, 22,419 uncompressed bytes.
Its original 8,850-byte ZIP SHA-256 matches GitHub's artifact digest:
`2c0c9d9eb29b7346fb654b8327d9b956c5467125b3ee2903d9dc5a3090701439`.
No images or serial logs were uploaded or downloaded. This is not a complete
boot proof, TDX/GPU CC, full guest-service, GCP, reproducibility or release
qualification. No automatic retry is included.
