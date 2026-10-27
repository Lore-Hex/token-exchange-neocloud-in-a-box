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
are a fixed stage number and cleanup boolean. It is **not accepted** in the
original boot-parser mode. The explicit topology mode described below accepts
it after the original INITRD marker without increasing the 62-byte cap. No raw kernel output,
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

## Explicit Debug Integration

The command remains excluded from default, original debug and `nibfake` builds.
The opt-in `--guest-network --debug-topology` pair on
[host/run-debug-boot.py](../../host/run-debug-boot.py) now connects the existing
[private debug-boot pipeline](p3-t1-debug-boot-probe.md) to the down-only controller.
This is software integration, **not a recorded guest run**. The separate manual
workflow selection below is default-off; no release accepted measurement changes
and no workflow was dispatched for the software tests.
Do not invoke the command on a host or add capabilities to an existing unit.

- `--debug-topology` without `--guest-network` is rejected before launching a
  worker. The original hosted Ubuntu 22.04 identity gate is unchanged. The
  authenticated native image must use existing `guest-network/v1` (68 source
  files), not a root-only export. Its original EROFS/dm-verity root is unchanged.
- The independent unprivileged producer audit binds `debug-topology/v1` and
  exactly 37 debug source files (original 30 plus seven topology sources). The
  private compiler copy contains exactly 20 source/module files, uses Go 1.27.2,
  `nibdebugtopology`, CGO off and an exact three-package stdlib-only import
  closure. Compiler hash is an observation, not vendor authentication. No
  ambient checkout files, runtime flags or command selection enter that copy.
- The distinct unsigned UKI appends the existing bounded three-file initrd
  supplement, using `--stage-initrd-topology`. The shared initrd staging code
  retains tmpfs/identity/ownership checks, exclusive no-overwrite writes and
  the unchanged 8 MiB executable limit. Only this explicit profile stages the
  [topology unit](../../guest/debugboot/nib-debug-topology.service) in `/run`.
- That unit requires and binds to the original `nib-network.service` deny-all
  gate. The original fixed network-failure observer still shuts down on failure.
  The topology unit allows only `AF_UNIX AF_NETLINK` and
  `CAP_SYS_ADMIN CAP_NET_ADMIN`; the original unit remains unchanged. No IP
  socket family, raw-packet capability or module-loading command is added.
- Versioned `nib.debug-{uki,qemu}-input/v2` configs bind the explicit profile.
  Root-only PASS cannot satisfy topology mode; topology PASS cannot satisfy the
  original mode. Exact INITRD/PASS order, EOF, clean QEMU exit, empty stdout/stderr
  and owned process-group cleanup are all required. Fixed failure enums remain
  content-free, including the original network-service failure records.
- New `nib.debug-topology-{uki,qemu,boot,validation}/v1` receipts keep old schemas
  and historical evidence unchanged. The parent binds compiled probe to assembly,
  original root/image to assembly, and assembled image to QEMU's checked image.
  It requires both profile identities, exact protocol/terminal records and
  independent cgroup cleanup before deleting only owned private images and
  publishing metadata. Failed worker bytes are retained; unknown cleanup cannot
  authorize publication. `run.json` remains standard `nib.evidence/v1`.

Limits remain 24/25 seconds for initrd, 144/145 for root plus topology, 150/2 for
the root service, 180 for QEMU and 900 for the outer owner. The topology's 12-second
ceiling consumes the original root budget; it does not extend it. Guest resources
remain 2 CPUs/1 GiB, root service 64 MiB/32 tasks, and QEMU has no NIC/KVM/GPU.
The original namespace/capability/cgroup and irreversible privilege-drop guards
are reused. No retry, timeout widening or additional paid resources are introduced.

Before a separately reviewed, authorized run, retain the matching authenticated
private image and exact locked tools/runtime inputs required by the existing
runbook. Use a canonical non-symlink `TMPDIR` (for example `/private/tmp` on macOS
for offline checks); do not weaken `O_NOFOLLOW` for `/var` aliases. Offline checks:

```sh
TMPDIR=/private/tmp go test -race ./guest/debugboot ./guest/debugtopology
TMPDIR=/private/tmp PYTHONPATH=scripts uv run --project tools --frozen python -m unittest test_debug_topology test_debug_boot test_mkosi_guest_network
```

The ordinary Go test suite also invokes the tagged staging tests. Tests use
synthetic initrd/UKI bytes, temporary owned directories, mock workers and local
Unix test sockets; none runs the topology controller or boots a guest. Later
`topology_down_only_verified` success would prove only this narrow down-only
CPU debug case. Packet isolation, networkd, confidentiality, reproducibility,
production service and signed-image claims remain false.

## Remaining Runtime Proof

### Manual CPU Selection

The [existing CPU debug workflow](../../.github/workflows/debug-boot.yml) exposes
three typed boolean inputs, all defaulting to false: `debug_boot`,
`guest_network` and `debug_topology`. It remains manual, main-only, hosted Ubuntu
22.04, read-only token permissions and the original 35-minute serial job.
When `debug_boot` enables the job, `debug_topology=true` requires
`guest_network=true`; a first step rejects an
incompatible selection **before checkout, tool setup or input fetching**. The
debug step checks the same finite selection again before resolving the compiler
or invoking the owner. No selection silently changes a native image profile.

Original false/false and true/false paths retain their exact launcher arguments.
The true/true path adds only `--debug-topology` to `--guest-network`. Native image
build and unused-export cleanup arguments are unchanged. The unused-export helper
is reached only after profile validation succeeds (invalid inputs stop before
checkout or image creation). The existing metadata-only upload
and independent cleanup gates still preserve a verified failed receipt and refuse
upload readiness for a missing/killed owner or unknown result. There are no
automatic triggers, retries, caller-supplied commands or arbitrary flags, wider
timeouts, cloud provisioning or production profile changes.

After final-head independent review, normal full/E2E/hosted gates, merge and the
applicable run authorization, one bounded original-image-profile proof may use:

```sh
gh workflow run debug-boot.yml --ref main -f debug_boot=true -f guest_network=true -f debug_topology=true
```

Before dispatch, require no queued, waiting or active owner in the existing
`bounded-cpu-debug-boot` group. Wait for existing owners; never cancel/recover them
or displace a pending run with another dispatch. Record the exact producer SHA
and run URL. Do not dispatch before review or retry a failed proof automatically.
The run fetches only locked public inputs, builds a fresh matching private
`guest-network/v1` export, and uses the original CPU-only TCG/no-NIC/no-GPU limits.
It must retain failure metadata and confirm owned cleanup. Missing pinned inputs
or kernel support is a failure to investigate, not permission to substitute pins,
load guessed modules, widen bounds or claim success. No such runtime result is
claimed by the workflow selection tests.

Offline workflow tests execute the actual selection shell against local stubs;
they never invoke sudo, compile a guest, boot QEMU or dispatch a workflow:

```sh
TMPDIR=/private/tmp PYTHONPATH=scripts uv run --project tools --frozen python -m unittest test_debug_topology_workflow test_debug_topology test_debug_boot test_mkosi_guest_network
```

### Beyond Down-Only Topology

The next slice must reconcile the actual image's original networkd
configuration/drop-ins/generators and credentials, authenticate its original
executables, and install/read back the unchanged strict nft policy **before**
link-up. The existing plan's networkd address/host-route, startup/drift and packet
negative cases remain unimplemented/unrun here. Static closure does not prove
symbol resolution, dlopen/NSS, kernel support, networkd, packet isolation, vendor
TLS, H100, confidential execution, deployment readiness or phase completion.
