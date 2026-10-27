# Isolated Network Proof Inputs

Status: **offline preparation only**. No Linux network proof, packet exchange,
guest launch, image modification or service activation has occurred. This is
the next bounded prerequisite after the [H100 network plan](p3-t1-h100-network-prerequisites.md),
not a new hardware launcher or a completed P3 phase.

## Narrow Increment

[prepare_network_proof.py](../../scripts/prepare_network_proof.py) authenticates
the actual retained systemd and nftables packages against the existing guest
lock, hashes their networkd/nft ELF members, and inventories the systemd package's
network configuration files. It does not execute or install either binary.
The existing bounded package reader/decompressor and exclusive private publisher
are reused. The host zstd decompressor may run; no package program runs.

The candidate bytes are exactly the existing Go-shared documentation-address
fixture and static-network renderer. No new firewall renderer, operator input,
vendor address, alternate hostname, general-egress rule or device permission is
introduced. The private output contains those three inert candidates, a fixed
synthetic topology/required-case plan, and a source/package/hash-bound receipt.
Every case is `not-run`; preparation success cannot be promoted to proof success.

The preparer requires a committed source checkout. Supply these already retained
public inputs; it never fetches missing files:

```sh
python3 -I -S -B scripts/prepare_network_proof.py \
  --archives "$PACKAGE_DIRECTORY" \
  --systemd-source "$SYSTEMD_SOURCE_ARCHIVE" --output "$NEW_PRIVATE_DIRECTORY"
python3 -I -S -B scripts/prepare_network_proof.py \
  --archives "$PACKAGE_DIRECTORY" \
  --systemd-source "$SYSTEMD_SOURCE_ARCHIVE" --verify "$NEW_PRIVATE_DIRECTORY"
```

The archive directory must contain `systemd_255.4-1ubuntu8.18_amd64.deb` and
`nftables_1.0.9-1ubuntu0.1_amd64.deb`. Paths must be absolute with no symlink
ancestors. Output must be new and disjoint from the source tree and inputs.
Files are mode 0600, directories 0700, with exclusive writes and fsync. The
receipt is last; failure retains incomplete output and never overwrites it.
Verification reconstructs every byte and rejects missing, extra or changed files.
No executable, unit, workflow flag or network namespace is emitted or enabled.

## Source Findings

The original [systemd 255.4 source archive](https://snapshot.ubuntu.com/ubuntu/20261020T000000Z/pool/main/s/systemd/systemd_255.4.orig.tar.gz)
is 14,952,427 bytes, SHA-256
`96e75bd08c57ad401677456fb88ef54a9f05bb1693013bc6ecce839640fd5`.
Its two authenticated manual members are inspected as data only. The only
accepted PAX metadata is the original exact revision comment `387a14a7b67b8b76adaed4175e14bb7e39b2f738`:

- [systemd.network](https://github.com/systemd/systemd-stable/blob/387a14a7b67b8b76adaed4175e14bb7e39b2f738/man/systemd.network.xml):
  four configuration search directories, lexicographic first matching file,
  filename replacement and merged drop-ins. A correct candidate file alone
  does not establish exclusive effective configuration.
- [systemd.netdev](https://github.com/systemd/systemd-stable/blob/387a14a7b67b8b76adaed4175e14bb7e39b2f738/man/systemd.netdev.xml):
  veth pairs and `[Peer]` naming. This source does not prove namespace placement
  or runtime isolation. The package contains built-in `.network` files; the
  future consumer must reconcile their exact recorded bytes, not assume an
  empty image or delete arbitrary files.

The [guest runtime lock](../../image/inputs/guest-runtime-ubuntu2404.json) binds
the packages. The authenticated networkd member is 1,669,864 bytes, SHA-256
`e4ab77a36ee958f052b0100b60583716904a393159227e1e895469199e1fd8f9`.
The nft member retains the existing 26,856-byte hash
`3f1c21553e62716ef1abfcf31f51ff94eb73ff4234a6653bac754a42f936d1c7`.
These member identities do not establish the complete dynamic loader/library
closure, installed configuration, kernel support, execution or network behavior.

## Reuse Plan And Remaining Gates

Use a separately reviewed opt-in extension of the existing
[CPU debug boot](p3-t1-debug-boot-probe.md): TCG, no virtual NIC, no KVM/GPU,
unchanged private export/verity/EROFS guards, original finite deadlines,
bounded observations and independent cleanup. Do not relax the release helper's
read-only EROFS or all-non-loopback-links-down prerequisite to run it on a host.
The existing [strict nft gate](p3-t1-guest-network.md) must install and read back
the unchanged policy before any synthetic link can be brought up.

The planned topology is one synthetic veth pair entirely inside that no-NIC
guest. The subject uses the fixed fixture name/MAC/address and the peer belongs
to a separate network namespace inside the guest. A subject and peer in the
same namespace would permit local routing and would not prove the egress gate.
The peer owns the fixture gateway and synthetic destination; no vendor endpoint
is contacted. Allowed TCP443 traffic is transport evidence only, not SDK/TLS,
NRAS, H100 or confidential qualification. Existing TCP8443 ingress and ARP remain
outside any claim of deny-all ingress or L2 isolation.

Implementation of that executor remains blocked on an independently reviewed
guest-only veth/namespace creation and teardown boundary, actual kernel support,
complete runtime loader closure, exclusive configuration including generated
files/drop-ins/competing managers, startup ordering, and a drift monitor that
removes network access on policy or configuration failure. `BindsTo` and a
successful network oneshot alone do not establish ongoing drift detection.
The current source package inventory is not a scan of a built guest image.

The plan lists positive and negative cases for exact link/address/host-route,
wrong name/MAC, denied DNS/DHCP/IPv6/ports/destinations, startup traffic, competing
configuration and managers, policy/configuration drift, and owned cleanup.
An eventual consumer must retain bounded private kernel/packet observations,
including failures; missing coverage cannot pass. No raw observation is produced
by this preparer. Root proof and network proof need separate explicit results;
do not reinterpret the existing INITRD/PASS/EOF boot grammar as a network result
or silently widen its proof cap, runtime ceiling or source profile.

No workflow dispatch, H100 launch, vendor request, host-network mutation,
default/release activation or phase completion is authorized here. The next
executor increment requires exact-head independent review before merge and a
separate run decision afterward.
