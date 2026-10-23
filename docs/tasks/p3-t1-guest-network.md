# P3.T1 Real Guest Network Gate

## Status

The first real network backend is implemented and covered by offline unit and
synthetic subprocess tests. The optional native image built in the first
[CPU-only run](../../evidence/p3/2026-10-22-debug-network-boot-failure/README.md),
but its boot failed: initrd proof arrived, root proof did not, and no guest
failure code was received. This does not prove the network executable started
or that a kernel ruleset was installed. No development-host firewall was touched. GPU,
weights and engine release backends still deny startup; a successful network
oneshot cannot start inference or establish confidentiality.

An explicit optional native composition/export/debug-consumer path is now
implemented and tested with synthetic processes and temporary image trees.
The original failure and cleanup receipts are preserved separately from the
new diagnostic implementation; no corrected native run is claimed.

This slice applies and reads back the existing firewall candidate. It is not
interface/address configuration, connectivity qualification or a deployable
dual-stack network profile. In particular, the existing policy has no ICMP or
IPv6 neighbor-discovery allowance. Do not infer that IPv6 routing works merely
because the literal-address renderer and readback support IPv6 set elements.

## Fixed Runtime Contract

`nib-network --config /etc/nib/network.json` is the only accepted real path.
The file is a regular root:root-owned, mode-0644, single-link file, bounded
to 4096 bytes, with the exact schema:

```json
{"schema":"nib.guest-network/v1","egress":[]}
```

An explicit empty list is valid. At most 32 unique literal unicast addresses
are allowed, using the existing renderer's validation. No DNS, CIDRs, zones,
loopback/link-local targets, arbitrary ports, commands, paths or environment
overrides. Duplicate/unknown/case-aliased keys, nulls, trailing values, invalid
UTF-8, excessive nesting or oversized JSON reject. Fake schemas never pass.

Before any command, the helper requires a read-only EROFS root and no up
non-loopback interface. This is an accidental-host-execution guard, **not**
verification of a verity chain or attestation. The image/boot chain must bind
the complete executable/configuration/library closure. Descriptor-relative
fixed-path reads reject symlinks, writable/non-root-owned parents, unexpected
modes, hardlinks, size changes and non-read-only-EROFS input files.

The only child executable is the rehashed `/usr/sbin/nft`. Four operations
share one eight-second deadline, with two seconds per child and bounded
process-group cleanup:

1. `--json list ruleset`: require the full ruleset to be empty.
2. `--check --file -`: check the existing rendered policy, with only its table
   declaration changed to `create table inet nib`.
3. `--file -`: commit the exact same create-only policy bytes in one batch.
4. `--json list ruleset`: compare the entire result to the intended table,
   two sets, three drop-policy chains and nine ordered rules.

Allocated handles and unordered table/chain/set enumeration are normalized;
all policy fields, expressions and rule ordering remain exact. Unknown or
extra objects fail. Both child streams are capped at 64 KiB; stderr, unexpected
check/create stdout, nonzero exit, surviving descendants and timeout deny.
Raw tool output is never logged. The writer intentionally does not embed
`bytes.Buffer`: its promoted `ReadFrom` would bypass bounded `Write` through
`io.Copy`. A real subprocess overflow regression protects this constraint.
There is no flush, replacement, rollback deletion or automatic retry, including
after an uncertain commit. Failure triggers the existing failclosed target.

## Pins And Sources

Source inspected 2026-10-22:

- [nftables 1.0.9 release archive](https://netfilter.org/projects/nftables/files/nftables-1.0.9.tar.xz),
  SHA-256 `a3c304cd9ba061239ee0474f9afb938a9bb99d89b960246f66f0c3a0a85e14cd`.
  `doc/nft.txt` defines stdin file input, check and create semantics;
  `src/parser_bison.y:1268-1277` accepts a create-table block;
  `src/libnftables.c:21-57` uses one netlink batch and omits the batch end for
  check-only mode. Detailed JSON/list-vs-set citations are in the
  [synthetic fixture note](../../guest/nftables/testdata/README.md).
- The existing [guest runtime lock](../../image/inputs/guest-runtime-ubuntu2404.json)
  pins `nftables 1.0.9-1ubuntu0.1`, 69,842 archive bytes, SHA-256
  `d648859fccde3c17b8b9b89993427564f91f394a39306db1f67bd5ea3ac42c6e`.
  The exact package was downloaded from the official
  [pinned Ubuntu snapshot](https://snapshot.ubuntu.com/ubuntu/20261020T000000Z/pool/main/n/nftables/nftables_1.0.9-1ubuntu0.1_amd64.deb),
  rehashed, and its regular `usr/sbin/nft` member inspected without execution:
  26,856 bytes, mode 0755, SHA-256
  `3f1c21553e62716ef1abfcf31f51ff94eb73ff4234a6653bac754a42f936d1c7`.
  Shared libraries remain bound by the existing package/root-image closure;
  checking this ELF alone is not authentication of a mutable host runtime.
- [systemd v255.4 execution contract](https://github.com/systemd/systemd-stable/blob/v255.4/man/systemd.exec.xml),
  `CapabilityBoundingSet`, `AmbientCapabilities` and `RestrictAddressFamilies`:
  the unit keeps user/group `nib`, grants only guest `CAP_NET_ADMIN`, and permits
  only netlink/Unix sockets. Other hardening and the ten-second start bound
  remain. Group kill and a two-second stop bound are explicit.
- [systemd v255.4 special targets](https://github.com/systemd/systemd-stable/blob/v255.4/man/systemd.special.xml),
  `network-pre.target`: the unit pulls in and orders before this passive
  firewall synchronization target and before all existing nib gates. It uses
  explicit early-boot/shutdown ordering, with no automatic unit enablement.

The composition uses the existing tools/runtime `systemd 255.4-1ubuntu8.18`
package, archive SHA-256
`ba37e7dc1e33690d94a1063543d27f9b17c013a20791c3315b2423fbf9e7ae7b`.
Its regular `usr/bin/systemd-sysusers` member was inspected without execution:
68,224 bytes, mode 0755, SHA-256
`f2f07333c751d6a05206e6c5b65df05f7d50d98293d6356d4a6b3c8b4f91301a`.
The pinned Ubuntu [source descriptor](https://snapshot.ubuntu.com/ubuntu/20261020T000000Z/pool/main/s/systemd/systemd_255.4-1ubuntu8.18.dsc)
names the inspected original archive (`systemd_255.4.orig.tar.gz`, 14,952,427
bytes, SHA-256 `96e75bd08c57ad401677456fb88ef54a9f05bb1695693013bc6ecce839640fd5`)
and Debian patch archive (273,272 bytes, SHA-256
`ec3ce620c3e072d34bbeaf9a4155cd91b5bee94eb412071bf81182f230ae2657`).
Those hashes were checked; no patch targets either source file below.

- [sysusers.c at v255.4](https://github.com/systemd/systemd-stable/blob/v255.4/src/sysusers/sysusers.c):
  source-archive lines 581-620 read `SOURCE_DATE_EPOCH` and derive shadow
  last-change days; line 668 assigns the new entry. Lines 1994 and 2322-2329
  select the explicit positional configuration. Member SHA-256
  `0d9e3c6bff03fc3faa849667022f5cdd9f1c2f986685ac06d83e9174b375393b`.
- [fileio.c at v255.4](https://github.com/systemd/systemd-stable/blob/v255.4/src/basic/fileio.c):
  source-archive lines 1081-1132, `search_and_open_internal`, opens absolute
  configuration paths unchanged. `--root` does **not** prefix those arguments.
  Member SHA-256 `3c32bd995c14f648f445d872ec9a8b4a7f59f36debdc4743762855a6a65f879e`.
  The initial WIP assumption that `/etc/sysusers.d/nib.conf` was root-relative
  was incorrect. The corrected fixed argument is
  `/work/output/rootfs/etc/sysusers.d/nib.conf`, with
  `--root=/work/output/rootfs` for the account databases.
- [sysusers.d at v255.4](https://github.com/systemd/systemd-stable/blob/v255.4/man/sysusers.d.xml):
  `u` creates a disabled user and matching group. The fixed declaration uses
  automatic unused IDs, `/nonexistent` and `/usr/sbin/nologin`. Post-command
  validation requires those values, locked shadow entries, the fixed epoch
  and unchanged existing accounts. The command receives no credentials or
  inherited environment. Only output byte counts are retained.

These public source/package identities are not claims that a guest ran.

## Optional Image Path

`host/run-mkosi-build.py --guest-network --go ABSOLUTE_COMPILER` requires all
existing runtime/driver/verity/UKI/private-export flags. The unprivileged child
compiles all four release helpers with observed Go 1.27.2, Linux amd64 v1,
CGO off, no module proxy/sumdb/workspace, a fresh private cache and a shared
120-second deadline. Compiler bytes are hashed before/after; dependency
enumeration permits only the standard library and the exact local guest
packages. Compiler identity is **observed, not authenticated**.

Default preparation keeps the existing 42-file profile and command unchanged.
The opt-in `guest-network/v1` profile requires that exact set plus 26 enumerated
sources, including every imported guest package, embedded policy, unit and
configuration byte. Two existing bounded audits must agree on commit and
overlap. Missing, extra, duplicate, mixed-profile and changed producer inputs
reject. No generic source-count or output limit is increased.

The worker copies and rehashes four helpers and ten fixed data files, creates
the `nib` account with the pinned tool, and enables only the network unit.
All destinations are fixed; collisions, symlink parents, hardlinks, writable
parents, changed files and malformed account databases reject. Composition
precedes verity: UKI and private export retain the same verified root bytes,
root hash and complete source profile. Failure never reaches image staging.
Whole-workspace cleanup and independent resource checks remain mandatory.

The optional root-probe and networkd drop-ins use `Requires`, `After` and
`BindsTo`, not ordering alone. Other request-serving gates retain their own
dependencies and fail-closed backends; no engine/node activation is added.
An empty egress list is deny-all preparation, not attestation readiness.

## Next Real Run

After final independent review, green checks, merge and separate run approval,
the existing `debug-boot.yml` accepts `debug_boot=true, guest_network=true`.
Both default false. The selected profile propagates through build, independent
producer capture, debug consumer and unused-export cleanup. A default consumer
rejects a network-profile bundle. The root probe cannot emit success until the
required network oneshot succeeds; merely installing its unit is insufficient.

The existing hosted Ubuntu 22.04, CPU-only/no-NIC/no-GPU execution and bounds
remain: 35-minute job, 600-second native generation, 900-second debug wrapper,
180-second QEMU, two CPUs, 2 GiB debug memory, swap zero and 128 PIDs. Only the
existing fixed JSON files can upload after independent cleanup. No serial,
firewall output or unsigned image is published. The first run failed as
recorded above; no additional run is authorized by these instructions.

Real NET_ADMIN delivery, kernel module loading, atomic installation and nft
JSON readback remain unproven. Routing, conntrack initialization and IPv6
neighbor discovery remain qualification gaps. Only an attested front door may
eventually listen off loopback; engine/GPU/weights readiness remains unavailable.

## Failure Diagnostics

The release network helper now maps failures to fixed exit statuses 64-77:
path, filesystem, interfaces, configuration read, configuration parse, policy
render, executable validation, initial command, initial ruleset, check,
create, readback command, readback comparison and deadline. Every status is
nonzero; acceptance, commands, privileges, eight-second aggregate deadline,
two-second child deadline and output bounds are unchanged. Generic errors
and stderr remain fixed and content-free.

Only the explicit network debug path selects `--stage-initrd-network`. Its
source-bound initrd stages an additional observer and an in-memory network
`OnFailure` drop-in under `/run`. The original release unit is unchanged.
On failure, the observer reads only the fixed systemd `MONITOR_UNIT`,
`MONITOR_SERVICE_RESULT`, `MONITOR_EXIT_CODE` and `MONITOR_EXIT_STATUS` fields.
An unknown unit or unknown values collapse to fixed unknown enums; only a
canonical eight-bit exit status may be forwarded. No environment strings,
unit names, errors, journal or nft output cross the proof channel.

The observer has an empty capability bounding set, no new privileges, null
standard streams, a 1.5-second hard process bound and a two-second unit bound.
Both success and failure of the observer enter the existing failclosed
shutdown target. It has no startup dependencies that could prevent its
failure action; an unavailable proof port fails within its own bound.
The root probe still requires network success. The host
recognizes only a bounded failure frame within the existing 62-byte cap and
continues to require exact INITRD/PASS/EOF for successful boot. A zero process
status reported by the observer is never successful boot evidence.

Pinned primary sources:

- [systemd v255.4 execution environment](https://github.com/systemd/systemd-stable/blob/v255.4/man/systemd.exec.xml),
  source-archive lines 4021-4045: `MONITOR_*` is passed to an `OnFailure` or
  `OnSuccess` handler; multiple triggers may omit it. Missing data is unknown,
  never invented. The observer is dedicated to `nib-network.service`.
- [systemd v255.4 unit dependencies](https://github.com/systemd/systemd-stable/blob/v255.4/man/systemd.unit.xml),
  `OnFailure` and `OnSuccess`: the debug observer terminates in the same
  shutdown target; it is not a restart or a fallback network policy.
- [systemd v255.4 namespace implementation](https://github.com/systemd/systemd-stable/blob/v255.4/src/core/namespace.c),
  source-archive lines 2799-2888: existing read-only private-temp directories
  can fall back to an empty read-only directory. Read-only EROFS alone does
  not establish `PrivateTmp` as the cause. That hardening is unchanged.

These files were read from the already hash-verified pinned Ubuntu source
archive above. Synthetic tests cover fixed statuses, all network command
failure positions, explicit staging, destination collisions/symlinks, fragmented
frames, malformed fields, missing EOF, zero QEMU exit and cleanup. They do not
prove the new observer has run in the actual guest. No diagnostic-only native
retry should occur without an exact reviewed proposal.

The proposed next run is one `debug-boot.yml` dispatch on the exact reviewed,
green merged producer, with both `debug_boot=true` and `guest_network=true`.
Record that producer and the run URL before execution. Expected duration is
an **estimate** of 8-10 minutes based on the original 8m45s job; the existing
35-minute job ceiling and all component/resource bounds above remain fixed.
It will build fresh private inputs, boot once without NIC/GPU/KVM, remove all
images, and preserve only the same four cleanup-gated JSON files. Stop after
either outcome and preserve the originals. Missing or unknown diagnostics
remain a failure, not permission to retry, weaken a check or claim readiness.
