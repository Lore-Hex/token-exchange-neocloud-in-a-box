# Restricted Network Static Closure

Status: **implemented and tested with synthetic metadata; actual recursive
closure is blocked on exact locked inputs**. This follows the
[direct ELF inventory](p3-t1-network-elf-inputs.md), not a Linux runtime proof.
No P3 acceptance, default/release activation or existing preparation format
changes. The default network configuration remains deny-all.

## Source And Boundary

[prepare_network_runtime.py](../../scripts/prepare_network_runtime.py) consumes
only an explicitly selected Ubuntu subset of the existing
[runtime lock](../../image/inputs/guest-runtime-ubuntu2404.json). Both `systemd`
and `nftables` are mandatory. Each selected DEB must match its original size,
SHA-256 and control fields; every original package row is replayed against the
exact committed index bytes before archive parsing. The trust root is the
previously reviewed signed-index hashes, as described in
[package provenance](p3-t1-r595-package-inputs.md), not a new OpenPGP check.
This extraction-only subset is not an installed APT dependency closure.

The existing virtual package reader, collision checks, merged-/usr handling and
symlink resolver are reused. No extraction follows a host symlink. Each graph
node records its archive/member identity, member size/hash, interpreter,
ordered `DT_NEEDED`, `DT_RPATH`, `DT_RUNPATH` and `DT_SONAME`. A separate complete
virtual inventory records links and source origins and is hash-bound to the
receipt. No package executable or runtime tree is published.

The shared [ELF reader](../../internal/elfmeta/metadata.go) uses Go 1.27.2's
standard `debug/elf` implementation. It bounds headers/tables before parsing,
rejects flagged and legacy `.zdebug` compression or extended section shapes, binds the dynamic/string
sections to file-backed load segments, and checks dynamic termination and
string-offset parity. Alternative filter/audit dependencies and `NODEFLIB`
reject. The original two-executable command retains its exact hash admission,
output format and deadline; only its parser is shared.

## Resolution Contract

This is a deliberately restricted **explicit virtual-path graph**, not a glibc
search-order emulator. Only amd64 little-endian ELF64 `ET_DYN` objects are
accepted. The two root members retain their original exact identities. Their
interpreter must be `/lib64/ld-linux-x86-64.so.2`; library objects may omit it.
The interpreter itself is included and recursively inspected.

- Search each object's own `DT_RUNPATH`, then the explicit candidate directories
  `usr/lib/x86_64-linux-gnu`, `lib/x86_64-linux-gnu`, `usr/lib`, `lib`. The only
  allowed nonempty RUNPATH is the observed networkd directory
  `/usr/lib/x86_64-linux-gnu/systemd`. It is never inherited by child objects.
- Require exactly one canonical candidate after virtual symlink resolution.
  Aliases of the same file are allowed; distinct candidates reject even if
  their bytes match or a real loader might select one by precedence.
- Reject missing libraries/interpreters, slash/token-bearing dependency names,
  `DT_RPATH`, unsupported RUNPATH values, conflicting global name bindings,
  repeated dependency entries, ambiguous member provenance and SONAME mismatch.
- Reject loader cache/preload files and `glibc-hwcaps` directories in the
  supplied tree. Never inspect host library paths, host loader caches or loader
  environment variables. Dependencies terminate by visited canonical identity;
  cycles do not cause repeated inspection.

The candidate directories are this tool's conservative admission contract, not
a claim about every platform's loader defaults. Unknown layouts fail closed.
Even `static_graph_closed: true` proves only name-to-file closure under this
contract. Symbol/version resolution, relocations, IFUNC, constructors, TLS,
glibc search equivalence, `dlopen`, NSS, plugins, services, privileges,
configuration selection, kernel/veth behavior, hardware, confidentiality and
deployability remain unproven. All runtime/activation proof fields remain false.

## Bounds And Use

Limits: 32 selected Ubuntu packages, 64 MiB compressed archives total, existing
50,000-member/512 MiB virtual-tree bounds, 128 visited ELF objects, 16 MiB per
ELF, 32 entries per string-valued dynamic tag, 256 headers/sections, 64 KiB
dynamic table, 1 MiB string table, 64 KiB helper output. Shared archive/index
decompression limits are unchanged. Committed-source auditing uses the existing
bounded Git reader. Local helper compilation has a 120-second aggregate budget;
archive/graph processing has its own fixed 120-second cutoff and does not renew
it per object. Each metadata child has at most ten seconds, including blocked
stdin; bounded process-group cleanup joins/reaps owned children. The Go compiler
is observed and recorded, not cryptographically authenticated. Only the locally
compiled stdlib helper runs; vendor binaries, host `ldd` and loaders never run.

The optional [subset fetcher](../../scripts/fetch_network_runtime.py) reuses the
reviewed fixed URL plan, four-transfer/600-second aggregate limit, exact hash
checks, fixed HTTPS curl arguments and bounded transport-only retry policy.
It adds no URLs, fallback flags or version discovery. Failed transfers and
post-fetch metadata/staging validation failures produce a redacted private failed
receipt, never verified input artifacts. Output directories
must be new; publication is exclusive, owner-only and fsynced. Failed closure
receipts and any already-authenticated inventory are retained as failures.

Example initial input selection, **not a claim that these six packages close
the graph**:

```sh
python3 -I -S -B scripts/fetch_network_runtime.py \
  --package systemd --package nftables --package libc6 \
  --package libsystemd-shared --package libnftables1 --package libedit2 \
  --output "$NEW_INPUTS"

python3 -I -S -B scripts/prepare_network_runtime.py \
  --metadata "$NEW_INPUTS/metadata" --archives "$NEW_INPUTS/archives" \
  --package systemd --package nftables --package libc6 \
  --package libsystemd-shared --package libnftables1 --package libedit2 \
  --go "$ABSOLUTE_GO_1_27_2" --output "$NEW_STATIC_REPORT"
```

Run only from an exact committed checkout. The CLI audits its source closure;
uncommitted changes reject. Success emits `receipt.json` and `inventory.json`,
not an image, filesystem installation, service or proof of runtime linkage.

## Current Blocker And Next Step

As of 2026-10-27, actual closure has **not** been established. The existing
two pinned executable members are retained, but no verified complete recursive
library set or exact replayable metadata set is available for this increment.
One required locked index is `noble-updates-main.xz`, 1397104 bytes, SHA-256
`a398e4efccd241c15ea24a92e1aedfbb853f9e9100e73de9f6bc6f1d5b2581fa`.
Its primary by-hash source is
[Ubuntu's locked index](https://archive.ubuntu.com/ubuntu/dists/noble-updates/main/binary-amd64/by-hash/SHA256/a398e4efccd241c15ea24a92e1aedfbb853f9e9100e73de9f6bc6f1d5b2581fa).
The bounded recovery attempt failed with curl HTTP error 22, without accepted
index bytes. A separate dated-snapshot recovery check also yielded no valid
bytes. These failures do not justify newer indexes or package substitutions.
The existing snapshot fetch support covers pool DEBs, not metadata fallback;
this increment does not silently expand that policy.

Next executable offline step: recover the exact locked indexes and Ubuntu DEBs
into a new owned directory, replay every selected package row, then run the
commands above. Use the resulting missing-library report and actual authenticated
archive contents to extend only the explicit locked package selection. Do not
infer recursive ELF linkage from `Depends` alone. Unknown/unsupported shapes
remain blockers. Once a complete graph is reviewed, separately prepare an
isolated CPU debug guest with the original pinned networkd/nft and synthetic
veth cases. Preserve the strict firewall and EROFS guards; do not connect that
guest to a vendor endpoint or turn static preparation into an H100/runtime claim.

Tests cover recursion, cycles, aliases, missing/ambiguous members, noninherited
RUNPATH, source identities, hash-before-parser checks, malformed ELF, bounds,
fixed compiler/child environments, immutable failure receipts and owned cleanup.
Synthetic tests and direct member metadata are not evidence of Linux execution.
