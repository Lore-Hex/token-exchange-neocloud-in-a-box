# Down-Only Debug Network Topology

Status: **software implementation and synthetic tests only**. No guest boot,
namespace/veth creation, packet exchange, networkd/nft execution or cloud run
was performed for this increment. The preceding
[33-package static closure](p3-t1-network-static-closure.md) remains static ELF
evidence, not kernel/runtime evidence. No P3 acceptance criterion is complete.

## Narrow Boundary

The [private-network plan](p3-t1-network-proof-inputs.md) needs a genuine namespace
boundary before its networkd and packet cases can be meaningful. This increment
implements that first boundary, not the whole plan:

- A separately tagged [debug command](../../guest/debugboot/cmd/nib-debug-topology/main_linux.go)
  calls the original full live-root verifier: exact kernel/cmdline/PID1, both
  EROFS mounts, dm-verity table/status, entire mapped-root read and rechecks.
- Only after root success, the [controller](../../guest/debugtopology/run_linux.go)
  requires the initial guest namespace to contain only loopback. It creates two
  owned child processes with `CLONE_NEWNET`, receiving and retaining their
  namespace FDs over the owned socket pairs (`SCM_RIGHTS`, not PID lookups).
  Descriptor-level `NS_GET_NSTYPE`, device/inode comparison, and same-executable
  child/parent checks bind the namespaces. Neither process enters another
  namespace; neither namespace is bind-mounted or given a persistent name.
- One exclusive `RTM_NEWLINK` creates a veth pair directly in those two fresh
  namespaces using explicit FD attributes for **both** ends. The subject
  name/MAC match the existing fixed [fixture](../../guest/nftables/testdata/h100-static-plan.json).
  The peer is fixed `nibpeer0`, `02:00:00:00:00:02`. There are no caller-supplied
  names, addresses, routes, commands or namespace targets.
- Both links stay down. Readback requires exactly loopback plus the expected
  Ethernet veth, exact name/MAC/MTU/down flags, and mutually matching peer indices.
  Both fresh namespaces must have no IPv4/IPv6 addresses or routes. Any early
  link-up, extra link, unexpected object, kernel error or incomplete dump fails.
- The subject deletes only its revalidated owned link. Both namespaces must then
  be empty except down loopback. The initial namespace's identity and semantic
  loopback state must be unchanged. Success additionally requires both owned
  children to exit/reap and all owned namespace/socket descriptors to close.
  Failure kills only owned process handles and closes the owned descriptors;
  cleanup failure cannot pass.

There is intentionally **no link-up, IP assignment, route installation, firewall
change, package execution or connection API**. Thus this slice cannot test or
bypass the strict policy-before-link-up gate. The existing deny-all service,
EROFS configuration checks, renderer and readback implementation are unchanged.

## Source Basis

Protocol facts were checked against upstream Linux v6.8 commit
`e8f897f4afef0031fe618a8e94127a0934896aba`, specifically:

- [if_link.h](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/include/uapi/linux/if_link.h),
  [veth.h](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/include/uapi/linux/veth.h)
  and [rtnetlink.h](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/include/uapi/linux/rtnetlink.h):
  fixed message/attribute layouts, including nested `VETH_INFO_PEER` beginning
  with an `ifinfomsg`, and `IFLA_NET_NS_FD` on each end.
- [rtnetlink.c](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/net/core/rtnetlink.c),
  `rtnl_link_get_net`, and [veth.c](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/net/veth.c),
  `veth_newlink`/`veth_dellink`: FD-directed placement and deletion of both ends.
- [nsfs.h](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/include/uapi/linux/nsfs.h):
  `NS_GET_NSTYPE` validates an opened namespace descriptor, not a path label.

These are upstream protocol facts, **not** verification of Ubuntu's patched
`6.8.0-147-generic` module/configuration availability. Missing veth/netns support
fails closed without guessed module loads, substitutions or fallback commands.
No host `ip`, `nsenter`, `ldd`, package program or vendor binary is used.

## Bounds And Verification

The original root deadline is still 144 seconds with a 145-second hard stop.
Topology work shares that original deadline and has an additional 12-second
ceiling, one second per netlink operation, and one aggregate second for cleanup.
The outer hard stop is never reset. A hard stop or unreaped child cannot produce
a success observation. INT/TERM cancels work and attempts owned cleanup.

Each kernel transaction accepts at most eight datagrams, 64 KiB total, 32 messages
and four data objects. Sender, sequence, port ID, header/attribute lengths,
duplicate attributes, error ACKs, truncated/interrupted dumps and terminal records
are checked. The fixed child protocol uses bounded 32-byte sequenced packets;
child stdout/stderr are discarded, never logged. There are no retries.

The distinct `NIB_DEBUG_TOPOLOGY_V1` record is at most 32 bytes; its failure fields
are a fixed stage number and cleanup boolean. It is **not accepted** by the
existing boot parser and does not increase its 62-byte cap. No raw kernel output,
namespace identifiers, paths, prompts, credentials or operator data are emitted.

Offline checks (a correct pinned Go 1.27.2 and canonical, non-symlink `TMPDIR` are
required on macOS):

```sh
TMPDIR=/private/tmp go test -race -count=1 ./guest/debugtopology ./guest/debugboot
```

The tests exercise fixed FD-targeted/down-only encodings, malformed/bounded kernel
replies, wrong name/MAC/type/flags/peer, lifecycle ordering, failures at every stage,
cancellation and cleanup failure. Cross-build tests verify a static Linux/amd64
stdlib-only binary and exclusion from default, original debug and `nibfake`
commands. Linux-only IPC tests transfer read-only descriptors for the existing
test-process namespace and reject missing/extra/wrong-type descriptors, truncated
packets and deadlines. They do not create namespaces or execute the controller.
The local IPC check used an already cached, capability-free, network-disabled
container; it is not a check of the pinned guest kernel.

## Deliberately Not Enabled

No source profile, unit, initrd composer, host launcher, workflow, proof parser or
release accepted measurement includes the new command. The original debug unit
still restricts address families/capabilities and cannot run this controller.
Do not manually add capabilities or invoke the command on a host. A separately
reviewed source-bound debug profile and bounded result/cleanup consumer are
required before an authorized CPU-only, no-NIC QEMU run.

The next integrated slice must reconcile the actual image's original networkd
configuration/drop-ins/generators and credentials, authenticate its original
executables, and install/read back the unchanged strict nft policy **before**
link-up. The existing plan's networkd address/host-route, startup/drift and packet
negative cases remain unimplemented/unrun here. Static closure does not prove
symbol resolution, dlopen/NSS, kernel support, networkd, packet isolation, vendor
TLS, H100, confidential execution, deployment readiness or phase completion.
