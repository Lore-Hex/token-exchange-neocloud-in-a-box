# P3.T1: Offline H100 Diagnostic Prerequisites

Status: private static-network **preparation only**, not a guest launcher. No
interface, route, firewall, device, SDK, systemd unit, image or cloud resource is
activated. The existing default and collector-only composition are unchanged.
This does not qualify NRAS connectivity, H100 access, TDX, boot or readiness.

## Implemented Boundary

[prepare_h100_network.py](../../scripts/prepare_h100_network.py) accepts one
external `nib.h100-static-ipv4/v1` manifest, at most 4096 bytes. Its exact fields
are `schema`, `interface`, `mac`, `address` (canonical IPv4/prefix), `gateway`,
`gateway_on_link` (boolean) and `nras_ipv4`. All addresses and the interface/MAC
come from independently reviewed operator inputs, never discovery or defaults.
The [shared fixture](../../guest/nftables/testdata/h100-static-plan.json) uses
documentation addresses only; it is not a deployment template.

The preparer rejects extra/missing/duplicate fields, alternate hostnames/ports,
wildcards, IPv6, noncanonical literals, multicast/loopback/link-local/reserved
addresses, local network/broadcast addresses and overlapping NRAS/local tuples.
An off-subnet gateway requires the explicit `gateway_on_link=true` assertion;
that assertion is not proof of reachability. There is no default route: the
candidate specifies only a host route to the one supplied NRAS IPv4 address.

The private output contains the unchanged original manifest and three inert
candidate files: the existing `nib.guest-network/v1` configuration, one networkd
file and an `/etc/hosts` candidate with only `nras.attestation.nvidia.com`.
The existing Go [Config/Render](../../guest/nftables/policy.go) and strict kernel
readback remain the only policy implementation. No DNS/DHCP/general-egress rule
is added. Its existing loopback/established traffic and inbound TCP8443 rule
remain; this task does not create a listener or close that existing ingress.
ARP required for IPv4 neighbor discovery is outside this inet policy, not proof
of an L2 isolation boundary.

Preparation audits committed source bytes, the locked systemd package, and five
explicit primary-source files. It hashes each output and the original input in
a private receipt. Existing nofollow/single-link/stamp checks and exclusive
0700-directory/0600-file publication are reused; the success receipt is last.
Incomplete output is retained and cannot be overwritten. `--verify` rebuilds
the expected bytes from the same source and external inputs, then checks the
exact private tree, including modes and extra-file rejection. Terminal output
contains no addresses, paths, manifest or exception strings.

```sh
python3 -I scripts/prepare_h100_network.py \
  --manifest "$PRIVATE_MANIFEST" --primary-sources "$PRIMARY_SOURCES" \
  --output "$NEW_PRIVATE_PLAN"
python3 -I scripts/prepare_h100_network.py \
  --manifest "$PRIVATE_MANIFEST" --primary-sources "$PRIMARY_SOURCES" \
  --verify "$NEW_PRIVATE_PLAN"
```

Inputs must be absolute paths without symlink ancestors; the manifest is an
owned, single-link 0600 file. Output must be new and outside the source tree and
inputs. These commands are offline; they do not download missing inputs. Keep
the plan and manifest private. File hashes are input bindings, not signed
operator authorization or proof that an address belongs to NVIDIA.

## Pinned Source Findings

The preparer's `PRIMARY` table fixes exact sizes and hashes for the following
local filenames. Obtain these public inputs separately; never resolve NRAS
inside preparation or silently substitute a newer package/source.

| Local name | Source |
| --- | --- |
| `systemd.network.xml` | [v255.4 commit 387a14a, network settings](https://github.com/systemd/systemd-stable/blob/387a14a7b67b8b76adaed4175e14bb7e39b2f738/man/systemd.network.xml) |
| `systemd.resource-control.xml` | [same commit, memory/device controls](https://github.com/systemd/systemd-stable/blob/387a14a7b67b8b76adaed4175e14bb7e39b2f738/man/systemd.resource-control.xml) |
| `systemd.deb` | [Ubuntu systemd 255.4-1ubuntu8.18 amd64](https://security.ubuntu.com/ubuntu/pool/main/s/systemd/systemd_255.4-1ubuntu8.18_amd64.deb), exact existing [guest lock](../../image/inputs/guest-runtime-ubuntu2404.json) |
| `nv_http.cpp` | [SDK 11b2bfeb, HTTP client](https://github.com/NVIDIA/attestation-sdk/blob/11b2bfeb42a1158aa08744603ecb239ccb3d2265/nv-attestation-sdk-cpp/src/nv_http.cpp) |
| `nv_jwt.cpp` | [same SDK, issuer/JWKS](https://github.com/NVIDIA/attestation-sdk/blob/11b2bfeb42a1158aa08744603ecb239ccb3d2265/nv-attestation-sdk-cpp/src/nv_jwt.cpp) |
| `nvml_client.cpp` | [same SDK, NVML collection](https://github.com/NVIDIA/attestation-sdk/blob/11b2bfeb42a1158aa08744603ecb239ccb3d2265/nv-attestation-sdk-cpp/src/gpu/nvml_client.cpp) |

The authenticated `.deb` was read as an archive, not installed. Its packaged
`systemd.network(5)` documents the emitted settings. Networkd supports static
addresses, exact name/MAC matching and `GatewayOnLink`; DHCP, IPv6 RA/link-local,
LLMNR and mDNS are explicitly off. A route assertion does not establish the
vendor's routing topology. Networkd's first matching file and merged drop-ins
can override intent: a future consumer must reject competing network files,
generator output, inherited addresses/routes and other network managers.

The collector retains the fixed HTTPS hostname, not an IP URL. SDK HTTP options
do not disable certificate/name verification or enable redirects; JWKS is
derived from the configured issuer plus `/.well-known/jwks.json`, with the
expected issuer checked. The SDK's [build source](https://github.com/NVIDIA/attestation-sdk/blob/11b2bfeb42a1158aa08744603ecb239ccb3d2265/nv-attestation-sdk-cpp/CMakeLists.txt)
defaults to bundled curl 8.22.0; [curl's default initialization](https://github.com/curl/curl/blob/curl-8_22_0/lib/url.c)
enables peer/hostname verification. These are source findings, not proof of the
installed binary's CA/NSS behavior. The locked CLI/library remain authenticated
by the [collector admission](p3-t1-h100-appraisal-collector.md). The future image
must additionally bind its full loader/NVML/CA/NSS closure and prove wrong-name,
untrusted-CA, redirect and unexpected-destination failures with the actual SDK.
Do not substitute an IP URL, custom trust bypass, proxy, arbitrary JWKS URL or
service key. A stale operator IP must fail, not trigger DNS fallback/discovery.

The hosts candidate is **not yet immutable**: it becomes so only in the future
verified read-only image. That consumer must establish hosts-only lookup for
this process with no NSS DNS/resolved/mDNS fallback. No `nsswitch.conf` is
replaced here. A hosts file alone does not establish those properties, and
`DNSDefaultRoute=no` alone is not a global DNS disable switch.

## Device, Identity and Memory Gates

The SDK dynamically loads `libnvidia-ml.so.1`, checks CC/PPCIe state and queries
every NVML-visible GPU; it does not document a complete nonroot device-node
allowlist. The [pinned 595.91.07 driver header](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/9f087a6d4e86d85acc0ce1d354d6276fe5047b29/kernel-open/common/inc/nv-chardev-numbers.h)
defines major 195, GPU minors 0..247, modeset 254 and control 255. This does not
prove that `/dev/nvidia0` and `/dev/nvidiactl` alone suffice, nor authorize their
DAC permissions or any broad major/minor grant. NVML report documentation is
not proof of unprivileged access. No device chmod, mknod, readiness setter,
driver option or guessed `DeviceAllow` is emitted.

An empty `SupplementaryGroups=` does not guarantee the collector's required
empty group list: pinned [systemd credential setup](https://github.com/systemd/systemd-stable/blob/387a14a7b67b8b76adaed4175e14bb7e39b2f738/src/core/exec-invoke.c)
uses `initgroups`. The future narrow privilege-drop boundary must explicitly
clear groups, drop all real/effective/saved/filesystem IDs and capabilities,
establish NNP/seccomp and recheck `/proc` before the unchanged collector. It
must not relax the collector's current checks to accommodate a service unit.

The SDK HTTP callback appends into an unbounded string. The collector's 64KiB
stdout/stderr caps do **not** bound that memory. Proposed launcher policy is a
512MiB whole-unit `MemoryMax` and `MemorySwapMax=0`, including all children and
tmpfs accounting, checked through cgroup-v2 readback. This is a conservative
test ceiling, not measured sufficient memory. OOM, unsupported controller or
missing readback must fail, preserve a bounded failure receipt and never retry
with a larger limit. `MemoryMax` cannot promise zero transient overshoot or
bound network bandwidth; the existing monotonic deadline is still required.
No unit or memory control is installed by this task.

## Acceptance Before Activation

1. **Offline, implemented:** strict manifest negatives; deterministic exact
   file bytes; source/package/hash mutation; private publication, interruption,
   symlink/hardlink/overlap/no-overwrite tests; shared fixture accepted by the
   unchanged real Go policy parser/renderer with only its IPv4 set changed.
2. **Linux isolated network proof, not implemented:** actual pinned networkd
   and policy on synthetic veths, no vendor requests. Require all non-loopback
   links down until exclusive policy install and strict readback; exact single
   link/address/host-route afterward. Prove wrong name/MAC fails, no DHCP/DNS/
   IPv6/general egress, only allowed TCP443 replies, no startup race, and network
   removal/failure on policy or configuration drift. Reject alternate files,
   drop-ins, generated config and competing managers. Preserve raw private
   observations; never mutate the host network.
3. **Linux identity/resource proof, not implemented:** actual systemd/cgroup-v2
   and privilege-drop helper, account deliberately in an extra group, inherited
   capabilities, absent seccomp/controller, response-memory exhaustion and
   blocked descendants. Verify empty groups/IDs/caps, enforced memory/swap,
   fixed original deadline, complete reap and bounded content-free failure.
   A `Type=oneshot` unit cannot rely on `RuntimeMaxSec`; use an explicit start
   timeout or an exec service with the existing collector deadline. See pinned
   [service timeout semantics](https://github.com/systemd/systemd-stable/blob/387a14a7b67b8b76adaed4175e14bb7e39b2f738/man/systemd.service.xml).
4. **Explicit hardware gate:** authorized H100 TDX venue and assigned inventory,
   exact locked NVML/driver/loader closure, proven minimal device permissions,
   CC/debug/readiness state and measured bounded memory. Validate SDK TLS and
   remote appraisal only after those gates. Ordinary B300 evidence is not this
   proof. No launcher should be wired before these prerequisites are specified
   and independently reviewed; no confidential or service-ready claim follows
   from a successful offline plan.
