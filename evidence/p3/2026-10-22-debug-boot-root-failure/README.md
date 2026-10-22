# CPU-only debug boot root-proof failure

Original JSON-only artifact from the one authorized
[run 39843193309](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39843193309),
attempt 1, producer `c0900f178072a08b5cc8688638993ccfc3061ab7`.
The native export step passed. Debug execution ran from 19:55:00 to 19:56:34 UTC
on October 22, 2026. The overall result is **fail**.

The four JSON files are original bytes. `output.log` is an exact copy of
`validation.json`, not a reconstructed console log. The downloaded original ZIP
SHA-256 matched GitHub's artifact digest:
`df5e83415f57e1938376485c7492b7d3f54a9b4c3eb29d7393c7b037207760b1`.
No image, arbitrary guest output or serial log was uploaded.

Debug UKI assembly and five pinned QEMU runtime checks passed. The fixed INITRD
frame was observed, but no root PASS was verified. QEMU exited zero with empty
stdout/stderr and a cleaned process group. That exit does not establish boot
success. The original receiver did not retain generic FAIL frames or identify
the failed guest check, so these originals cannot establish a specific cause
or prove that the real-root service executed.

The independent wrapper recorded zero OOM kills, verified an empty removed
cgroup, and deleted both private images and the input bundle. The original
worker's `private_work_removed: false` is retained unchanged; final cleanup was
performed and certified by the independent parent, not that failed worker.
Peak cgroup memory was 2,147,483,648 bytes, its configured cap, with no OOM kill.

This proves that this measured debug image reached its initrd staging probe,
not a verified root switch, complete guest service, TDX/GPU CC, GCP
compatibility, reproducibility or release qualification. No retry is included.
