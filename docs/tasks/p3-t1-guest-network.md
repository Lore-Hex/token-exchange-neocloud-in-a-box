# P3.T1 Real Guest Network Gate

## Status

The first real network backend is implemented and covered by offline unit and
synthetic subprocess tests. It is not yet installed in a native image or run
against a kernel firewall. No development-host firewall was touched. GPU,
weights and engine release backends still deny startup; a successful network
oneshot cannot start inference or establish confidentiality.

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

These public source/package identities are not claims that a guest ran.

## Next Integration

Compose the source-bound static helper, protected explicit config, `nib`
account and units into the private root **before** verity generation. Keep
the same root-hash/source binding through UKI verification and export; do not
regenerate or patch a verified root afterward. Preserve default native builds
without this opt-in composition. Only a separately reviewed, bounded guest
execution can prove the NET_ADMIN capability, kernel module closure, atomic
installation and actual readback. Routing, conntrack initialization and IPv6
neighbor discovery remain explicit qualification gaps. Only an attested front
door may eventually listen off loopback; engine/gpu/weights readiness remains
unavailable, not simulated into success.
