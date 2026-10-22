# CPU-only debug root boot pass

The one separately authorized corrected run
[39863142055](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39863142055),
attempt 1, **passed** at exact producer
`eed4b10ce0ffff750df345652a8db05d95297a02`. It was dispatched once at
22:11:53 UTC on October 22, 2026, after final-head independent review and green
full CI. The wrapper ran from 22:18:26 through 22:20:10 UTC. The preceding
unclassified, ROOT_READ and loop-deadline failures remain preserved separately;
this result neither edits them nor retrospectively assigns them new causes.

## Observed Result

Debug UKI assembly and all five pinned runtime startup checks passed. The
fixed proof channel accepted exactly INITRD, PASS and EOF: `initrd`, `root`
and `complete` are true; guest failure and protocol error are null. QEMU
exited zero, emitted no stdout/stderr and its process group was cleaned.

The source-bound
[real-root check](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/blob/eed4b10ce0ffff750df345652a8db05d95297a02/guest/debugboot/run_linux.go)
requires the expected kernel/PID1/cmdline, matching live read-only EROFS root,
strict verity mapping, full mapped-root read, mandatory extra-byte EOF,
unchanged table, valid status and live-root recheck before PASS. Every check
must finish before its fixed deadline. A successful receipt contains no
root-read elapsed/count telemetry: the wrapper duration is not a read-speed
measurement. This is software boot proof for this debug image, not proof of
post-mount tamper rejection.

The input GPT was 928,968,704 bytes, original UKI 97,898,496 bytes, compiled
probe 3,486,864 bytes and distinct debug UKI 101,387,776 bytes. Their exact
digests are in `worker.json`. Assembly and worker bind the same root hash;
the assembled original UKI and probe match the input/compiler receipts.
All 42 native and 28 debug source hashes match the producer's Git blobs, and
all 11 compiler-source entries match the debug source closure. The observed
compiler was Go 1.27.2; `compiler_authenticated` remains false.

## Independent Cleanup

The parent verified child exit zero, zero OOM kills, unchanged read-back
limits, an empty removed cgroup, zero populated processes before cleanup,
and deletion of private images/input bundle. Peak cgroup memory was
2,147,483,648 bytes. The exact limits were 2 host CPUs, 2 GiB memory, no swap,
128 PIDs and group OOM handling. The unchanged execution bounds were one
180-second QEMU TCG attempt, 2 emulated vCPUs/1 GiB guest, a 900-second wrapper
and a 35-minute job, with no GPU, KVM, NIC, serial or monitor.

`worker.private_work_removed: false` is intentionally preserved. It describes
the subordinate worker scope, not the final parent cleanup result. The
independent parent resource receipt proves process/cgroup cleanup, and
`validation.private_images_removed: true` records final private-image/input
deletion. Neither field is rewritten to hide the distinction.

## Original Artifacts

Artifact `12463291480`, `native-debug-boot-39863142055-1`, contains exactly four
regular mode-0600 JSON files, totaling 22,415 bytes. Its 8,822-byte ZIP SHA-256
matches GitHub's artifact digest:
`67595aa5b4e26f2ca5d5589be70ecc56b9074f866b5f5159674fba99cd542def`.
The ZIP and extracted originals are retained privately; the four committed
JSON files are byte-identical. `output.log` is an exact copy of
`validation.json`, not console output. No images or raw serial were retrieved
or published.

| Original | Bytes | SHA-256 |
| --- | ---: | --- |
| `resources.json` | 8467 | `d3f4ad669781f0364e8c2bd13f8ebe0a797fa362cc5e6dc3d7ad94b4db782e37` |
| `run.json` | 672 | `5decd9419c40f3038de3df340225d1907d974ac1bbf73ae82837b1dda1a106cf` |
| `validation.json` | 284 | `523524d207a543ae290d44858325065a7f5022f921752fcd86cfae5165738051` |
| `worker.json` | 12992 | `b4653f26fd08ed8347ff4998d3f434f15c4b110ddba2df02c7eaa6833347b4cd` |

## Qualification Limits

Confidentiality, production-service verification, reproducibility and signing
flags remain false. This run does not qualify TDX, GPU CC, complete guest
services, GCP custom-GPU-image compatibility, release measurements, authenticated
compiler provenance or post-mount tamper rejection. The one-run authorization
is consumed; this evidence does not authorize another attempt.
