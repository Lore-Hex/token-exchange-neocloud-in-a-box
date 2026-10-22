# P3.T1 private EROFS and verity increment

Status: implemented and fixture-tested; native run 39784301704 reached EROFS
generation but failed on progress-output overflow. The quiet-output correction
below awaits a full native rerun. This does not complete P3.T1. No successful
image generation, boot or reproducibility result is claimed by the unit fixtures.

## Entry point and order

The existing main-only, manually dispatched
[`mkosi-native-build.yml`](../../.github/workflows/mkosi-native-build.yml) has a
new boolean `verity_root`, default `false`. It requires all three existing
booleans: `guest_runtime`, `compile_driver`, `install_driver`. The resource
launcher and native builder independently enforce the same requirements. The
private worker accepts only `--install-driver --verity-root` for this mode and
requires the exact 212-package runtime scope before installation.

The order is configured-package inventory, driver compile/decompression/install,
[offline startup policy](p3-t1-runtime-startup-policy.md),
[appraisal CLI probe](p3-t1-appraisal-cli.md), then
[`mkosi_verity.py`](../../scripts/mkosi_verity.py). The helper and its embedded
partition definitions are committed-source inputs. The optional mode uses 30
extra source paths (38 unique paths with the eight base inputs), within the
existing limit; no allowance increase or dependency/lock change. Without the
flag, no helper is added to the payload and no image operation is invoked.

No workflow was dispatched for the quiet-output correction. A later authorized
native run must preserve its original bounded diagnostics, worker result,
source/tool receipts and independent resource-cleanup receipt before claiming
actual build success. A fixture supervisor returning zero is not that evidence.

## Native progress failure and correction

[Native run 39784301704](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39784301704)
used `84152f74b436e12626f03450013a2690b96e03ac`. Its unchanged
[run receipt](../../evidence/p3/2026-10-22-verity-progress-failure/run.json),
[worker result](../../evidence/p3/2026-10-22-verity-progress-failure/result.json),
[bounded mkfs stdout](../../evidence/p3/2026-10-22-verity-progress-failure/verity-mkfs.stdout),
and [resource receipt](../../evidence/p3/2026-10-22-verity-progress-failure/evidence-resources.json)
come from artifact `native-mkosi-install-39784301704-1`. All three NVIDIA
appraiser help checks passed without appraisal, GPU or network access. The
worker then failed at `verity-root`: retained stdout is exactly 65536 bytes of
per-file progress, and failure frames reach the supervisor's output-limit
exception. Cleanup passed with no OOM. No verity success was produced.

The old `-d 0` sets only the debug level. In pinned EROFS v1.7.1,
`mkfs/main.c:mkfs_parse_options_cfg` handles `--quiet` by setting the same
`EROFS_ERR` level and clearing `c_showprogress`. The independent progress path
in [lib/config.c](https://github.com/erofs/erofs-utils/blob/v1.7.1/lib/config.c)
otherwise writes a line for each file even with redirected stdout; SHA-256
`fa0c27a59563766391d248f5410b639619077631e050cefe6ac167bd9de2862d`.
[include/erofs/print.h](https://github.com/erofs/erofs-utils/blob/v1.7.1/include/erofs/print.h)
defines `EROFS_ERR = 0` and retains error reporting at that level; SHA-256
`4b0e2fd1ba87b34eea1b7100d6452bf70a7cbfcb7203faace75c452c8e40b97b`.
The exact [mkfs source](https://github.com/erofs/erofs-utils/blob/v1.7.1/mkfs/main.c)
hash is pinned below. Replace `-d 0` with `--quiet`; no image-setting, source,
tool, hash, output cap, timeout, verification or cleanup gate changes.

The hash-matched 163496-byte `mkfs.erofs` from the locked Ubuntu
`erofs-utils_1.7.1-1build2_amd64.deb` was also run with `--help` in a disposable
local container on 2026-10-22. It exited 0 with empty stdout and 2345 stderr
bytes, explicitly listing `--quiet`. The loader and libraries came from the
existing tools archive, including `libdeflate.so.0`; an initial incomplete
library extraction failed to load and was not treated as a successful check.
This was a help-only check, not image generation or native build qualification.
The container used an existing local image (no pull), network none, read-only
root and tool mount, UID/GID 65534, all capabilities dropped, no-new-privileges,
one CPU, 128 MiB and 32 PIDs. The shared process supervisor retained both
65536-byte output caps with a 20-second check timeout; the container was removed.

Regression tests pin the exact corrected argv and unchanged image parameters,
authenticate the four original failure artifacts, and require either stream's
overflow to retain at most its 64 KiB prefix, remove the partial image workspace,
and stop before repart. Positive-tool nonzero exits still fail. The focused
command below passed 122 tests; independent final-head review and the next
guarded native build remain pending.

## Fixed operations

1. Make EROFS directly from the read-only installed root using the existing
   tools-tree `mkfs.erofs --quiet`: block size 4096, LZ4, label `nib-root`, timestamp
   `1792454400`, UUID `1ea2d72f-6bd0-480e-89a3-132f03a4c7e1`. No UID/GID override,
   file exclusion or xattr-disabling option is used. `SOURCE_DATE_EPOCH` is
   omitted from the helper environment: v1.7.1 would use it to replace `-T`'s
   fixed timestamps with timestamp clamping.
2. Feed that regular file to `systemd-repart`, with `CopyBlocks=`, an exact
   data-partition size and `SplitName=-`. A paired hash partition has
   `Verity=hash`, `VerityMatchKey=root`, both block sizes 4096, `Minimize=best`,
   `ReadOnly=yes`, `SplitName=verity`. The data partition has `Verity=data`
   and the same match key. `CopyBlocks=` cannot be combined with `Format=` or
   `CopyFiles=`. Direct EROFS generation avoids repart's extra installed-root
   directory copy for the latter path.
3. Repart receives explicit `--offline=yes --empty=create --size=auto
   --dry-run=no --discard=no --split=yes --sector-size=4096 --json=short` and
   only the fresh private definitions directory. Its explicit seed is
   `1ea2d72f-6bd0-480e-89a3-132f03a4c7e1`, never machine-id or random fallback.
4. Check the bounded JSON has exactly the two expected partitions, matching
   root hashes, integer offsets and exact sizes. Stream-hash the GPT partition
   ranges and require byte identity with the EROFS and split hash files. Check
   the verity header's format 1, SHA-256, 4096-byte blocks and **full EROFS file
   coverage**, plus the pinned seed-derived salt and UUID. This header parser
   is not a cryptographic verifier.
5. Run tools-tree `fsck.erofs --extract IMAGE`. Bare `--extract` decodes/checks
   compressed data without writing an extracted tree. Chroot into the read-only
   guest for its existing `veritysetup verify DATA HASH ROOT_HASH`. Require
   success, then rejection of a one-bit-changed root hash, then success again.
   The negative control requires exit 1 and the exact C-locale root-hash
   mismatch diagnostic, not just any nonzero exit. No `open`, device-mapper
   activation, FEC or corruption-ignore option exists in the helper.
6. Require unchanged data/hash/tool identities after checking. Delete the
   intermediate GPT image before verification and the remaining private
   workspace before returning success. A cleanup error is failure.

The fixed salt is
`89609461d8139622a4a5e5b22ac0a928626ceac08652e36004266d6c7acc3c16`;
the verity header UUID is `58468b84-9dc4-4d41-b651-ceece5ff3767`.
These parameter pins follow upstream `derive_salt`/`derive_uuid`; the actual
hash tree is generated by repart/libcryptsetup and checked by cryptsetup,
not by a new Python cryptographic implementation.

## Existing authenticated tools

The local archive inspection checked the complete `.deb` byte size and SHA-256
against the existing locks, then read members in memory without executing them.
Executable member size/hash pins below are also checked inside the build before
execution. The authenticated package closure still supplies their loaders and
shared libraries. No host executable is substituted for these tools.

| Existing package | Archive SHA-256 | Executable member: bytes, SHA-256 |
| --- | --- | --- |
| tools `erofs-utils` `1.7.1-1build2` | `ce1a7283a3ffc47bc832dfd77c4fba6a327f3928f1c90c64fe14e251251cfc3a` | `usr/bin/mkfs.erofs`: 163496, `9ad2004dd18dba23f1297a1e360cb79280c174267a99a99e53d926cb776d5180` |
| same package | same archive | `usr/bin/fsck.erofs`: 68216, `9df25cc289bbd04f1995383aa6a5a235757ef895a6085b26d0b63eb45718a448` |
| tools `systemd` `255.4-1ubuntu8.18` | `ba37e7dc1e33690d94a1063543d27f9b17c013a20791c3315b2423fbf9e7ae7b` | `usr/bin/systemd-repart`: 199912, `5e731ad1322a7badc6a57001622ddb8ff2b5a665f293a19781f91a86c6db219d` |
| guest `cryptsetup-bin` `2:2.7.0-1ubuntu4.2` | `63fc1b21893ddcbc85968cf06d6c321d77ad062fa6210c029b0554aaade04eab` | archive `sbin/veritysetup`, merged-usr `usr/sbin/veritysetup`: 44992, `95659d771286250ebdb04c831c198711bb8f6b5e569a39e88d35b3f619cd4faf` |

`cryptsetup-bin`/`libpopt0` are in the guest lock, not the tools lock; EROFS
tools are in the tools lock, not the guest lock. This is why generation and
verification use different existing roots. Both contain the pinned
`libcryptsetup12` `2:2.7.0-1ubuntu4.2`. Package-member inspection is source/pin
evidence only, not a native loader or image-build result.

## Sources for interfaces

- [systemd-stable v255.4 repart.d](https://github.com/systemd/systemd-stable/blob/v255.4/man/repart.d.xml),
  `CopyBlocks`, `Verity`, `VerityMatchKey`, block sizes, `Minimize`, `SplitName`;
  SHA-256 `b196b09e3e93806288fb9f14458badd79d7893b84511f7a55bfde41a180c6527`.
- [systemd-stable v255.4 repart CLI](https://github.com/systemd/systemd-stable/blob/v255.4/man/systemd-repart.xml),
  offline regular-file construction, explicit seed, JSON, sizes, definitions;
  SHA-256 `cebe1df3499556121588d2d98099fd57e65ac903b2bd183109b58253588c9a1e`.
- [systemd-stable v255.4 repart.c](https://github.com/systemd/systemd-stable/blob/v255.4/src/repart/repart.c),
  `context_minimize`, `partition_format_verity_hash`, `derive_salt`, `derive_uuid`,
  seed-derived verity parameters at line 5159, `context_dump_partitions` and
  `context_split`; SHA-256
  `8dc5fb302b36af407fe3636f03f14a7e78d2687a78369289007b2ed4950f036b`.
- [EROFS v1.7.1 mkfs manual](https://github.com/erofs/erofs-utils/blob/v1.7.1/man/mkfs.erofs.1)
  and [mkfs source](https://github.com/erofs/erofs-utils/blob/v1.7.1/mkfs/main.c)
  (`mkfs_parse_options_cfg`, `parse_source_date_epoch`). Source SHA-256
  `577bfa7b7eca4104d59ebee13faf54066e7a4d231214493417398a268fa659f5`.
  The authenticated package's `man1/fsck.erofs.1.gz`, SHA-256
  `ffff114bc47c1c41c42fe4b9d2c83244361a43dc861505cb84d26462c117f033`,
  documents bare `--extract` as a decoding check.
- [cryptsetup v2.7.0 manual](https://github.com/mbroz/cryptsetup/blob/v2.7.0/man/veritysetup.8.adoc),
  `VERIFY`: userspace verification without a kernel device;
  SHA-256 `764be828aa29efadb990a2a10af5020d8cfbcbab200f96dbcd43be1f091ae39c`.
  [Header/verification parameters](https://github.com/mbroz/cryptsetup/blob/v2.7.0/lib/verity/verity.c)
  SHA-256 `28010d6cd93da63f1ac275d412b4456035a46e75bd486bd7ac0c03612a78c504`;
  [root-hash mismatch](https://github.com/mbroz/cryptsetup/blob/v2.7.0/lib/verity/verity_hash.c)
  returns `-EFAULT`, translated to exit 1 by
  [`translate_errno`](https://github.com/mbroz/cryptsetup/blob/v2.7.0/src/utils_tools.c).

## Confinement, bounds and output

All operations remain under the existing fresh mapped user/mount/network/IPC/PID
namespaces. The guest and tools are read-only, `/dev` is synthetic, the network
has only `lo`, and only the new private workspace is writable for generation.
Verification sees its image files read-only under the guest's `/tmp` after
chroot. Each child applies the existing eight-capability set, NNP and seccomp
checks before exec. No `CAP_SYS_ADMIN`, `CAP_SETPCAP`, real devices, host mounts,
network access or package additions are introduced. The trusted image tools
retain the existing eight-capability ceiling; this is not the vendor CLI's
separate UID-65534 all-effective-capabilities-dropped probe.

The cgroup remains 8 GiB memory, no swap, 256 PIDs and two CPUs. Native-supervisor
and root-launcher deadlines remain 1900 and 2500 seconds. Subcommands are limited
to 240 seconds for EROFS and 120 seconds each for repart/fsck/verification. These
do not extend the outer deadline: a slow install or compression may leave too
little time, and must fail rather than relax limits. Peak memory and practical
fit within these bounds have **not** been measured for this increment.

The EROFS limit is 1536 MiB; hash partition 32 MiB; GPT file 1568 MiB. Child hard
`RLIMIT_FSIZE` enforces the corresponding generation ceiling and is zero for
check-only commands; core dumps are disabled. The shared cgroup bounds scratch
and aggregate memory. Streaming identity reads are at most 1 MiB. Fresh workspace
and definitions are owner-only; collisions are errors. File readers use no-follow,
regular-file, single-link, bounded-size and whole-block checks. Directory walks
are descriptor-anchored. Diagnostic files are exclusive/no-follow, at most 64 KiB
per stream per operation (twelve allowlisted names); the existing 64 KiB worker
receipt and other artifact limits remain unchanged. No image artifact is copied
to `/out` or uploaded.

On a successful **actual** run, `worker.verity_root` records actual sizes/SHA-256
for all three private image files, the root hash, seed, parameters, tool pins and
verification/cleanup booleans. The GPT file is identified but not boot-validated.
Only receipts and bounded diagnostics survive; image export needs separate
review and authorization. A failed child, changed identity, malformed metadata,
wrong negative-control outcome, output overflow or cleanup failure cannot return
a successful helper result. Failure logs and the existing resource cleanup path
remain available; a timeout/OOM is not proof of image generation.

## Remaining gates

There is no UKI, authenticated boot root-hash selection, mounted dm-verity
enforcement, post-mount tamper test, release/debug measurement separation, full
release hardening, LKCA runtime proof, appraiser attestation, GPU or confidential
result, production deployment, or two-clean-build reproducibility proof here.
Fixed filesystem timestamps/UUID/seed do not normalize package-generated file
contents, driver build products or every xattr. The images still contain the
current installed-root state. Repeated clean native builds and content analysis
are required before any reproducibility claim; kernel/UKI/boot are subsequent
increments. Do not accept this newly generated root hash as an authenticated
release measurement merely because userspace verification succeeded.

The subsequent optional [private initrd/UKI stage](p3-t1-initrd-uki.md) now
implements unsigned assembly and inspection after this stage, still without
native execution, export or boot proof. Its separate opt-in also checks the
root-hash-derived partition UUIDs needed by systemd's initrd discovery.

Focused tests, from `scripts/`, using the existing Python 3.12 check environment:

```sh
python -B -m unittest test_mkosi_verity test_mkosi_build \
  test_mkosi_runtime_policy test_mkosi_appraisal_probe test_mkosi_dkms \
  test_mkosi_confinement test_mkosi_ownership test_build_resources
```

These cover the control flow and rejection boundaries with explicit fixtures,
not real EROFS or dm-verity generation/verification. The native main-only gate,
independent final-head review and CI remain outstanding.
