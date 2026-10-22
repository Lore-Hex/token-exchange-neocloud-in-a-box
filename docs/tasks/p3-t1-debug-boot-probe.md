# P3.T1 CPU-only debug boot probe

## Status

The next separately authorized [39856626007](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39856626007),
producer `6a15eee38c82225bc6649218829532d5ed135d63`, **failed the mapped-root
read deadline**. Its [four original receipts](../../evidence/p3/2026-10-22-debug-boot-read-deadline/README.md)
are byte-preserved. The fixed diagnostic records `deadline`, 171,704,320 bytes
read and 23 whole elapsed read seconds. It identifies the loop deadline, not
the underlying throughput bottleneck. Assembly, all five runtime startup
checks and independent cleanup passed; root proof did not. There was no retry.
The resulting debug-root-only budget adjustment below is implemented for a
reviewed follow-up, not yet demonstrated by an actual boot. The initrd and all
outer/resource/output bounds remain unchanged.

The separately authorized follow-up [39851156135](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39851156135),
producer `28f9fc01d59bc316ffdb206c8c0434cd5ad191e7`, also **failed root proof**.
Its [four original receipts](../../evidence/p3/2026-10-22-debug-boot-root-read-failure/README.md)
are byte-preserved. The fixed failure stage is now `ROOT_READ`: the real-root
probe reached mapped-root reading after the strict mount, device, table and
open checks. It does not identify I/O, EOF/length, deadline or close as the
specific cause. Zero OOM kills and a 2 GiB peak do not prove a deadline issue.
Cleanup passed; that diagnostic producer changed no output cap, read
verification, privilege or deadline.

The first authorized CPU-only run, [39843193309](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39843193309),
at producer `c0900f178072a08b5cc8688638993ccfc3061ab7`, **failed root proof**.
Native export, debug UKI assembly and all five pinned QEMU startup checks passed.
The guest emitted INITRD, but no complete root PASS was verified. QEMU exited
zero; the independent wrapper verified cleanup and zero OOM kills. The
[four original JSON receipts](../../evidence/p3/2026-10-22-debug-boot-root-failure/README.md)
are preserved byte-for-byte. No image or serial log was uploaded.

The original receiver discarded generic FAIL frames. Its metadata cannot
identify a failed guest check or establish whether the real-root probe ran.
The diagnostic repair retains only fixed failure-stage codes and protocol
error categories, without changing the acceptance checks, privileges or
deadlines. It is locally tested, not a demonstrated fix for the unknown boot
failure. Another attempt requires final-head review, green checks and separate
run authorization; the first run's permission is consumed.

This experiment is not a TDX, GPU CC, complete guest-service, release,
reproducibility or post-mount-tamper qualification. The existing installed root
does not contain the complete inference/trcs service composition. A successful
future probe would establish only that this measured debug UKI entered the
initrd, switched to the expected read-only EROFS/dm-verity root, read every mapped
data block successfully, observed a valid unchanged verity table/status, and
powered off within the host deadline. It does not prove GCP GPU compatibility:
Google's [supported configurations](https://docs.cloud.google.com/confidential-computing/confidential-vm/docs/supported-configurations)
(checked 2026-10-22, page updated 2026-10-20) restrict official A3 GPU support to
`ubuntu-2204-lts` and `cos-tdx-113-lts`, explicitly excluding other TDX-tagged
images. The A3 zones remain `us-central1-a`, `us-east5-a`, `europe-west4-c`.
General custom-CVM-image permission does not establish custom GPU-image support.

## Exact Inputs

- Kernel: the existing Ubuntu `6.8.0-147-generic` package and member pins in
  [mkosi_boot.py](../../scripts/mkosi_boot.py). Its authenticated
  `modules.builtin` contains `virtio_pci`, `virtio_blk` and `virtio_console`.
- Stub/systemd/ukify: existing Ubuntu `255.4-1ubuntu8.18` source/package inputs;
  use the pinned stub, GNU cpio and pinned-source PE inspector already used by
  the native build. Do not generate PE or CPIO formats with a new writer.
- QEMU: Ubuntu `1:6.2+dfsg-2ubuntu6.31`; OVMF:
  `2022.02-3ubuntu0.22.04.7`. The
  [separate extraction-only lock](../../image/inputs/debug-boot-runtime-ubuntu2204.json)
  records 123 packages, 55,311,182 archive bytes, exact SHA-256 values, four
  compressed indexes and their signed InRelease identities. The Ubuntu archive
  signing fingerprint is `F6ECB3762474EDA9D21B7022871920D1991BC93C`.
  These are public vendor inputs, not our boot measurements.
- Index origin: [Ubuntu snapshot 20261020](https://snapshot.ubuntu.com/ubuntu/20261020T000000Z/),
  `jammy` and `jammy-updates`, `main` and `universe`, amd64. Both InRelease
  signatures were checked against that archive key during lock derivation.
  The preparer rechecks every selected row against exact index bytes and
  reuses the existing Debian dependency/archive/payload parsers. It neither
  installs packages nor executes maintainer hooks. This is not an installed
  Essential-package closure, so the installed-system checker scopes stay
  unchanged.

The preparer's executable/member hashes include the actual QEMU ELF, its
dynamic loader, the unshare/setpriv executables and OVMF CODE/VARS. Successful
offline reconstruction does **not** assert that their shared-library runtime
has started; fixed binary startup checks are still required inside confinement.
The QEMU worker invokes the authenticated Jammy loader with an explicit library
path and `QEMU_MODULE_DIR`, as defined by QEMU v6.2.0
[util/module.c](https://github.com/qemu/qemu/blob/v6.2.0/util/module.c).
It requires the exact version family and the TCG, block-size, virtserialport and
q35 properties in bounded help results before starting the guest. Missing
runtime linkage or options fail closed; no fallback to a host binary occurs.

The static probe uses Go `1.27.2`, stdlib only, `CGO_ENABLED=0`, private caches,
no network proxy/sumdb, no build VCS metadata and only audited source copies.
The host compiler's hash is recorded, not authenticated by this experiment;
`compiler_authenticated` is explicitly false. The exact resulting ELF digest
is bound to the independently inspected debug UKI. Host kernel/Python/toolchain
trust is not converted into a release or attestation claim.

## Debug Image Contract

Keep the original private-export `root.raw` unchanged. Bind its full-copy/hash
verification and the original UKI to the same source identity, receipts and
root hash using the existing private-export checker. A new debug UKI must:

1. Preserve the pinned kernel, stub and original initrd bytes.
2. Append a separately verified GNU-cpio supplement containing only the static
   probe, its initrd staging unit and the switch-root dependency drop-in.
3. Embed exactly the original root parameters followed by
   `systemd.unit=nib-debug-boot.target nib.debug-boot=v1`.
4. Reinspect the complete PE with the existing pinned inspector, including
   exact kernel/initrd/cmdline sections, unsigned state and distinct UKI hash.
5. Never enter accepted release measurements or alter the default image.

Linux v6.8's [initramfs buffer format](https://www.kernel.org/doc/html/v6.8/driver-api/early-userspace/buffer-format.html)
explicitly permits concatenated CPIO archives separated by their trailers.
Systemd v255.4's
[switch-root implementation](https://github.com/systemd/systemd-stable/blob/v255.4/src/shared/switch-root.c)
preserves `/run` across the initrd switch. The debug staging service places
fixed, exclusive files there, never overwrites an existing unit or binary, and
is required by `initrd-switch-root.service`. The real-root probe has no console
or network listener, and emits only the fixed INITRD/PASS/FAIL protocol on
`org.nib.boot-proof`.

Success still requires exactly INITRD then PASS followed by EOF. The receiver
also recognizes the legacy generic FAIL and 19 compile-time failure labels:
`INITRD_STAGE`, `INITRD_MARKER`, `KERNEL`, `PID1`, `CMDLINE`, `PID1_MOUNT`,
`SELF_MOUNT`, `MOUNT_MATCH`, `DM_NAME`, `DM_SUSPENDED`, `DM_READONLY`,
`TABLE_QUERY`, `TABLE_FORMAT`, `ROOT_OPEN`, `ROOT_READ`, `TABLE_RECHECK`,
`STATUS_QUERY`, `STATUS_FORMAT`, `DEADLINE`. Only `INITRD_STAGE` may precede
INITRD; all real-root failures require the INITRD prefix. Every recognized
failure remains a failed run, including with exit zero. No error strings,
paths, query output or arbitrary channel bytes enter the receipt. The finite
wire grammar is bounded by its longest fixed record, less than 128 bytes;
the existing process-output caps remain unchanged. Unknown, out-of-order,
extended, duplicated or truncated frames never pass. Early failure before
opening the proof port can still produce no stage code; absence is not success
and does not justify guessing a cause.

The root-read diagnostic refinement uses one compact failure-only record after
INITRD: `NIB_DEBUG_BOOT_V1 R <reason> <bytes_read> <elapsed_seconds>` and newline.
Reason is one of eight fixed integers: 1=size, 2=loop deadline, 3=short read,
4=I/O error, 5=extra data, 6=missing/error EOF, 7=deadline after EOF, 8=close.
Only canonical decimal values are allowed: bytes from zero through the existing
1,536 MiB root bound, and whole elapsed read seconds from zero through 145.
The extra-byte EOF probe is not included in the mapped-data byte counter.
Partial `io.ReadFull` counts are retained even when that call fails, according
to the pinned Go 1.27.2 standard-library contract; no error string, file data
or query text is emitted. An out-of-range observation falls back to the legacy
fixed ROOT_READ failure. The longest record including INITRD is 62 bytes,
within the unchanged 62-byte proof cap. Numeric diagnostics never satisfy the
unchanged exact INITRD/PASS/EOF success grammar. A complete mapped read, extra
EOF check, unchanged table/status rechecks and all stage-specific deadlines remain
mandatory. This instrumentation is not a diagnosed performance fix.

### Root Read Budget

The original root check had a 24-second deadline, 25-second process hard kill
and 30-second systemd timeout, identical to initrd staging. Run 39856626007
demonstrated the loop deadline after 171,704,320 mapped bytes and 23 whole read
seconds. It did not demonstrate an underlying throughput bottleneck or an
optimization. The QEMU command already selects its existing `max` TCG CPU;
there is no speculative CPU, read-size or verity-feature change.

**Estimate:** the 928,968,704-byte complete GPT is an upper bound on mapped
data. Using 24 seconds for upper rounding of the floored read duration gives
`928968704 / 171704320 * 24 = 129.85` seconds at a constant rate. This is not
an actual full-read duration or a guaranteed future throughput rate.

Only the debug real-root check now receives 144 seconds, a 145-second process
hard kill and a 150-second `TimeoutStartSec`. Initrd staging remains 24/25/30
seconds. Systemd v255.4's
[service contract](https://github.com/systemd/systemd-stable/blob/v255.4/man/systemd.service.xml)
defines `TimeoutStartSec` for the explicit `Type=oneshot` start, and
`TimeoutStopSec=2` for termination; `RuntimeMaxSec` is not a oneshot substitute.
The root check has one second before its process hard kill and another five
before the service timeout. The 150-second service limit plus its unchanged
two-second stop budget leaves 28 seconds inside the unchanged 180-second QEMU
ceiling. Firmware, initrd and poweroff also consume that outer budget: a slow
boot can still exhaust it and fail, and the guest does not extend or reset it.
The wrapper remains 900 seconds and the job remains 35 minutes.

No root-read length, extra EOF, table/status recheck, clean terminal requirement,
source gate, cleanup, privilege, memory/CPU/PID or 62-byte proof cap changes.
Three-digit elapsed counters fit the same cap; values above 145 are rejected.
An incomplete or expired read still cannot pass. These are fixed debug-only
limits, not caller options or release qualification. Another actual run needs
exact-head independent review and passing full CI plus explicit authorization.

The live table check follows Linux v6.8's
[dm-verity table/status contract](https://github.com/torvalds/linux/blob/v6.8/Documentation/admin-guide/device-mapper/verity.rst).
`V` alone only covers blocks read so far; the probe reads the entire bounded
mapping and checks the table and status again. Every optional verity feature
is rejected, including `ignore_corruption` and `check_at_most_once`. The actual
pinned dmsetup executable is rehashed before each fixed read-only query.

## Prewritten Run Plan

Preferred first executor: a fresh GitHub-hosted Ubuntu 22.04 native runner,
preserving the current platform gate. This avoids sharing CPU or namespaces
with the retained Spheron GPU job. A later Spheron execution needs a separate,
reviewed lab-host launcher; do not weaken the native builder's gate or touch
the running GPU container, namespace, devices, ports or cgroup.

The [opt-in workflow](../../.github/workflows/debug-boot.yml) runs only on main,
with `debug_boot=true`. After final-head review/green checks and merge, the
coordinator must record the exact merged SHA and dispatch once:

```sh
gh workflow run debug-boot.yml --ref main -f debug_boot=true
```

Confirm the resulting run's `headSha` equals that reviewed merged SHA before
treating it as the intended run. This document is a run plan, not a dispatch
authorization or evidence that the command has run. The committed workflow
generates the private export, then invokes exactly:

```sh
sudo -- /usr/bin/python3.10 -I -S -B host/run-debug-boot.py \
  --downloads "$PWD/build/native-install/inputs" \
  --archive "$PWD/build/native-install/source.tar.gz" \
  --runtime-inputs "$PWD/build/debug-boot/runtime-inputs" \
  --go "$(readlink -f "$(command -v go)")"
```

Enforced bounds:

- One fresh native build with all five build stages plus private export. The
  previous successful export deleted its image bytes, so its metadata cannot
  substitute for a fresh bundle. Do not regenerate an image from metadata.
- Preparation and fresh native generation: retain existing individual stage,
  stream, package, memory and PID bounds. Native generation has a new outer
  600-second TERM deadline with 30 seconds before forced termination; its
  existing internal limits are unchanged. Debug preparation/assembly/boot
  together have a 900-second independent-wrapper deadline, followed by cgroup
  cleanup and at most 120 seconds for private image deletion/metadata emission.
  The workflow's 35-minute outer ceiling covers public input preparation and
  both stages, instead of the earlier unimplemented 20-minute estimate.
  **Estimate:** normally 10-20 minutes total, based on the prior roughly
  six-minute native generation plus bounded preparation/boot. One attempt only.
- One QEMU attempt, at most 180 seconds, 2 emulated vCPUs, 1 GiB guest RAM,
  at most 2 host CPUs and 2 GiB for the QEMU process group/cgroup, no swap.
  No retry loop or background follow-up. Guest probe runtime is separately
  bounded to 145 seconds with a 150-second systemd root-service bound; initrd
  staging remains 25 seconds with a 30-second service bound. The corresponding
  check deadlines are 144 and 24 seconds, respectively.
- TCG only; no KVM, VFIO, GPU, host device passthrough, NIC, user networking,
  monitor, serial console, SSH, credentials or inherited environment secrets.
  A private UNIX socket carries only fixed guest proof frames. Unknown,
  truncated, duplicated or extra frames fail; process exit 0 alone never passes.
  The fixed Python bootstrap checks namespace/mapping ownership first, then
  re-execs through pinned `setpriv` while it can still drop the bounding set.
  Before parsing boot inputs or starting QEMU, it checks all five capability
  sets are zero, requires no-new-privileges and installs the existing syscall
  filter with its expected capability mask narrowed to zero.
- Original GPT is read-only with 4096-byte logical/physical block size.
  Firmware CODE is read-only; VARS is a fresh private full copy. No ESP is
  required for the direct UKI path used by pinned mkosi
  [ecf7f570 qemu.py](https://github.com/systemd/mkosi/blob/ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b/mkosi/qemu.py).
  QEMU 6.2's
  [fixed option definitions](https://github.com/qemu/qemu/blob/v6.2.0/qemu-options.hx)
  and [seccomp sets](https://github.com/qemu/qemu/blob/v6.2.0/softmmu/qemu-seccomp.c)
  are the source for the launcher, not the local Mac's QEMU 11 executable.
- Require terminal inspection, empty process group/cgroup, zero OOM kills,
  removal of private firmware/images/sockets and exclusive cleanup. A fixed
  failure receipt survives timeout or diagnostic failure; unknown cleanup
  never authorizes artifact upload. Only reviewed JSON/fixed protocol output
  may leave the runner, never binaries or arbitrary console output.

The root wrapper only creates its own fresh cgroup, launches the existing
privilege-drop trampoline and independently kills/reaps that group. It drops
privileges irreversibly before deleting the exact owned private tree and input
bundle. A missing/invalid worker receipt cannot pass. A valid fixed diagnostic
is retained on nonzero exit or timeout, but overall pass additionally requires
terminal success, protocol completion, zero OOM kills and proven cleanup.
Only `validation.json`, `resources.json`, `worker.json`, `run.json` become
uploadable after deletion. If debug never starts, the existing private-export
validator removes the unused bundle and permits only its cleanup JSON. Unknown
cleanup leaves upload unauthorized, including cancellation/forced-kill cases.
Before launching the worker, the independent parent captures the exact audited
producer through a separate bounded unprivileged source-only process. That
audit consumes the same 900-second budget, not an additional worker allowance.
Both successful and killed/missing-worker outcomes emit the standard
`nib.evidence/v1` envelope with phase/task, UTC times and host context. Producer
identity never depends on a surviving worker receipt. Valid worker failure
JSON is preserved byte-for-byte; source mismatch cannot pass.

The original 42-file native source gate is unchanged. Debug helpers, static
probe sources/units, runtime lock and one fixed workflow are checked against
the same HEAD in a separate source closure. The original export is full-copied
and rehashed, and the immutable inputs are rehashed again after QEMU. The debug
assembler reuses the full existing PE inspection and GNU-cpio verification;
the original root, salt, epoch and normal CLI/workflow behavior are unchanged.

Unknown runtime/device-help results, source identities, image hashes or
confinement facts block the boot. After review and a new authorization, the
next actual step is one diagnostic attempt using the workflow above, not an
improvised command on the retained GPU host. No retry is included here.

## Local Checks

From this worktree, with the repository's Go 1.27.2 toolchain and writable
private caches:

```sh
GOCACHE=/private/tmp/nib-go-cache GOPROXY=off GOSUMDB=off GOTOOLCHAIN=local go test -race -count=1 ./guest/debugboot
GOCACHE=/private/tmp/nib-go-cache GOPROXY=off GOSUMDB=off GOTOOLCHAIN=local go vet ./guest/debugboot
python3 -I -S -B -m unittest discover -s scripts -p test_debug_boot_runtime.py -v
UV_CACHE_DIR=/private/tmp/nib-uv-cache uv run --project tools --frozen --offline python -B -m unittest discover -s scripts -p test_debug_boot.py -v
UV_CACHE_DIR=/private/tmp/nib-uv-cache uv run --project tools --frozen --offline python -B -m unittest discover -s scripts -p test_debug_boot_evidence.py -v
```

These are unit/process/fake-input checks and a static cross-build, not a live
guest run. Coverage includes protocol EOF/duplicate/truncation, queued-frame
drain, nonzero exit/timeout diagnostics, OOM and cleanup failures, directory
replacement, source mutation, assembler failures, readonly namespace arguments
and metadata-only workflow paths. Full-suite and independent-review results
belong to the exact PR head, not an assumed boot-success claim.

Failure-diagnostic regressions exercise every fixed failure frame at every
fragment boundary, rejection of arbitrary/extra bytes, missing EOF, and clean
QEMU exit with a guest failure. Original-receipt tests verify the four hashes,
producer/source closure, independent cleanup and failed root-proof outcome.
