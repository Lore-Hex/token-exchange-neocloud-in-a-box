# Selected closed-NAT proof candidate

Status: the [corrected CPU experiment passed](../../evidence/p2/2026-10-25-selected-nat-pass/README.md):
all 15 baseline controls, only two protected TLS paths allowed, 13 negative
paths blocked, unchanged DNS relay counters and confirmed cleanup. The
[two earlier failures](../../evidence/p2/2026-10-25-selected-nat-policy-failure/README.md)
remain failures. The separate
[ordinary-host venue profile](p2-t4-selected-nat-venue.md) subsequently passed
the same isolated experiment with its own
[original evidence](../../evidence/p2/2026-10-25-selected-nat-ubuntu26-pass/README.md).
Neither result qualifies a persistent public service or confidential inference.
This is a
GPU-free network experiment, not a public service owner or permission to change
a host firewall. The earlier [isolated-bridge observation](p2-t4-selected-network-probe.md)
does not qualify this alternative. No inference, credentials, model weights,
external endpoints, DNS publication, customer traffic or confidentiality claim
are involved.

## Boundary and sources

The reusable [policy renderer](../../scripts/selected_network_policy.py) produces
create-only, uniquely named `inet` and `bridge` nftables tables. It never executes
commands, flushes a ruleset, modifies Docker-owned tables or changes the daemon's
firewall backend. All unrelated traffic keeps its existing policy.

- `inet input`: deny traffic from the owned bridge except established replies
  from the candidate's TLS port. Match original/reply conntrack tuples so reverse
  NAT does not confuse the published port with the container port.
- `inet forward`: allow only the exact published IPv4 TCP destination, approved
  ingress interface, DNAT tuple and candidate port 8443, plus its established
  replies. Deny all other traffic entering or leaving the owned bridge.
- `inet output`: permit host-originated candidate TLS only, denying other host
  access to that bridge, including ports 8000/8001 and IPv6.
- Native `bridge forward`: deny L2 peer forwarding in either direction. This
  does not rely on `bridge-nf-call-iptables`. ARP needed for routed traffic is
  not an authorization for raw sockets; fixtures drop every capability.

[Docker's firewall documentation](https://docs.docker.com/engine/network/firewall-nftables/)
requires separate operator tables, explains base-chain priority and that an
accept verdict does not bypass a later drop. This proof leaves Docker 29.1.3's
default iptables backend enabled; its additive nft rules run before the normal
filter priority. It does not switch to Docker's experimental nft backend.
[Netfilter hooks](https://wiki.nftables.org/wiki-nftables/index.php/Netfilter_hooks)
and [bridge filtering](https://wiki.nftables.org/wiki-nftables/index.php/Bridge_filtering)
describe the independent routed and L2 paths. Exact owned-table readback rejects
drift and any flowtable, which could bypass the forwarding path.

The JSON format was checked against the
[nftables 1.0.9 source archive](https://netfilter.org/projects/nftables/files/nftables-1.0.9.tar.xz),
SHA-256 `a3c304cd9ba061239ee0474f9afb938a9bb99d89b960246f66f0c3a0a85e14cd`:
`configure.ac`, `src/ct.c`, `src/meta.c`, `src/parser_json.c` and `src/json.c`.
The exact release label is `Old Doc Yak #3`. In this revision,
conntrack IPv4 keys are `ip daddr`/`ip saddr` with `dir`; bridge-name keys are
`ibrname`/`obrname`. Readback ignores only object handles and independent
table/chain enumeration, preserving rule order and comparing every expression
against the exact expected canonical form described below.

### Pinned canonical readback

The input batch remains byte-identical. nftables 1.0.9 removes a stored
`meta nfproto ipv4` dependency when a later IPv4 address payload implies it,
and a stored `meta l4proto tcp` dependency when a later TCP port payload implies
it. See `src/netlink_delinearize.c:2030-2077` and
`src/payload.c:870-940` in the same hash-pinned archive. Upstream
`tests/py/inet/meta.t.json` and `.json.output` demonstrate the IPv4 elision and
that IPv4 is retained when the only payload is a TCP port (TCP alone also
matches IPv6).

The [source-derived fixture](../../scripts/fixtures/nftables-1.0.9-canonical.json)
includes those upstream cases and an explicitly labelled derivation for the
policy's input guard. It is not captured output from our failed run. The six
rules with both IPv4 address and TCP port payloads have an exact expected form
without the two implied meta expressions. The two conntrack-only input reply
rules retain both explicit protocol matches (`src/ct.c:450-470` gives the
relevant conntrack expression bases). Actual readback is not normalized and
arbitrary missing or added expressions are not accepted. Predicate removals,
wrong family/address/port, changed verdicts, extra rules/fields and rule-order
changes fail. Full input-batch hash and existing packet-condition tests protect
the unchanged installed policy. The diagnostic observation failed at readback, after check/apply completed,
but captured no rules or exception details. Canonicalization remains a candidate
cause, not a captured mismatch; fetching and validation share that step. The
subsequent corrected experiment passed without changing the installed batch;
it does not recover the missing historical rules or exception details.

### Pure-policy 1.1.6 compatibility

`Policy.nft_version` is a final, optional field defaulting to `1.0.9`; only
exact `1.0.9` and `1.1.6` are accepted. It selects the expected readback, not a
different installed policy. Installation and removal bytes are identical for
both versions, including the existing installation SHA-256 golden.
`readback_args()` returns these fixed argv lists without executing them:

```text
1.0.9: nft --json list ruleset
1.1.6: nft --json --numeric-protocol list ruleset
```

The [1.1.6 source-derived fixture](../../scripts/fixtures/nftables-1.1.6-canonical.json)
is based on the official
[nftables 1.1.6 archive](https://www.netfilter.org/projects/nftables/files/nftables-1.1.6.tar.xz),
SHA-256 `372931bda8556b310636a2f9020adc710f9bab66f47efe0ce90bff800ac2530c`.
It includes upstream JSON cases and separately labelled policy derivations,
**not captured kernel output**. `src/ct.c:464-475` assigns typed IPv4 conntrack
addresses a network-header base. `src/netlink_delinearize.c:2274-2402` recognizes
those addresses as implying IPv4, removing the explicit `nfproto` dependency;
conntrack ports do not remove the transport dependency. Consequently, only the
two conntrack-only input reply rules differ from the 1.0.9 expected form: they
omit `meta nfproto ipv4` but retain `meta l4proto` as integer `6`. All remaining
predicates and their order stay exact. The six payload rules retain the existing
canonical form (`src/payload.c:856-940`).

The numeric flag is defined in `src/main.c:134,495-497` and suppresses protocol
name lookup in `src/json.c:1242-1252`. Upstream's JSON test runner explicitly
enables numeric protocols (`tests/py/nft-test.py:935-939`); its
`tests/py/inet/meta.t.json.output` demonstrates TCP as integer `6`. This is a
fixed rendering request, not a rule rewrite or an attempt to accept arbitrary
host protocol names. The version, release label (`Commodore Bullmoose #7` for
1.1.6, `Old Doc Yak #3` for 1.0.9) and integer schema version `1` must match
exactly; unknown metadata fields fail. Owned-object comparison also preserves
JSON scalar types, so a floating-point `6.0` is not accepted as protocol `6`.
No actual readback expressions are normalized or repaired.

The original tests, fixture and golden remain intact. Additional pure tests
cover both versions, exact argv/metadata, unchanged installation bytes,
cross-version rejection, every object/predicate/verdict removal, unexpected
fields/expressions, family/tuple changes and rule/predicate ordering. These are
source-derived software checks, not a new kernel, daemon or host result.
The policy-only change left `prove-selected-nat.py` unchanged. Its subsequent
[venue integration](p2-t4-selected-nat-venue.md) retains the default 1.0.9
contract and adds one exact opt-in profile. Neither change authorizes execution on an
existing GPU host or choose a host-profile schema. Actual host/tool identity
pins, separately reviewed proof-runner integration and an approved isolated
synthetic-network observation remain prerequisites for that compatibility claim.

### Software-only drop quarantine

`Quarantine(owned_policy)` is an additive renderer and strict validator, tested
against synthetic rulesets for both pinned versions. It is **not integrated
into the service owner, executed against a kernel, or covered by the earlier
CPU PASS**. It performs no command, host discovery, mount or daemon operation.
The selected `--network none` smoke owner and its argv/receipt contracts are
unchanged. Serving installation, canonical expectations, removal and default
version behavior remain unchanged; shared object comparison and batch helpers
avoid a second, weaker validator.

Quarantine uses separate `nib_quarantine_<owner-token>` tables in `inet` and
`bridge`. It never requires the serving tables to exist or pass validation,
and never replaces, flushes or deletes them. Both tables use `create`, not an
idempotent adoption of an existing name. A collision or failed/uncertain apply
is failure, not proof of protection. The `CREATE` section of
`doc/libnftables-json.adoc` in the hash-pinned
[1.0.9](https://netfilter.org/projects/nftables/files/nftables-1.0.9.tar.xz) and
[1.1.6](https://www.netfilter.org/projects/nftables/files/nftables-1.1.6.tar.xz)
archives above specifies the existing-object error.

Every rule verdict is `drop`; base-chain policy remains `accept` so unmatched
host traffic remains subject to its existing rules. The same hook families and
priorities as the serving policy are used:

- `inet input`, `forward` and `output` drop IP traffic entering/leaving the
  owned bridge, including IPv6, UDP, DNS and existing TLS replies. No conntrack
  state or direction creates an exception.
- Input/output also drop the exact published IPv4 TCP destination, including
  loopback access to a userland proxy. All three hooks drop the matching
  original conntrack destination/port, including replies and DNAT to a changed
  address or interface. This does not rely on the old serving predicates.
- Native `bridge forward` drops L2 peer forwarding in either direction. As
  with the serving policy, this is not an ARP-to-host isolation claim or a
  global firewall. It does not depend on bridge IP firewall sysctls.

`doc/statements.txt`, Verdict Statements, in both pinned archives explains that
an accept in another base chain cannot override a drop. No serving-table
ordering assumption is required. The two payload rules have the same protocol
dependency elision as the existing source-derived fixtures. The three
conntrack-only tuple rules retain both meta protocol tests on 1.0.9; on 1.1.6
they elide `nfproto` and retain numeric TCP `6`, using the exact source and
`--numeric-protocol` readback described above. Installation bytes are identical
across versions. Actual expressions are never normalized, reordered or repaired.

The caller handoff is deliberately small:

```python
quarantine = Quarantine(owned_policy)
batch = quarantine.batch()
readback_argv = quarantine.readback_args()
# A future bounded owner applies exactly batch, then obtains fresh readback.
quarantine.verify(raw_readback, install_exit_code=apply_exit_code,
                  readback_exit_code=readback_exit_code)
```

`verify` requires literal integer zero for **both** command exits and exact
bounded readback, including the closed version metadata and global rejection
of flowtables. A timeout, signal, missing status, failed readback or changed
rule fails even if the supplied bytes look correct. No success is cached and
constructing/rendering an object never means it is installed. These are caller
inputs, not independent proof that commands ran: the future owner must bind
the statuses and fresh stdout to its exact bounded operations and original
deadline, without logging private paths or raw errors. It must stop serving
on failure and must not report a protected state it cannot verify.

`remove_batch` additionally requires literal `containers_absent=True`,
`networks_absent=True` and `creations_resolved=True`. Unknown/late create
completion, failed inventory and cleanup ambiguity retain the tables. This
only renders removal of the quarantine tables, never the serving tables;
the future owner must establish those facts, revalidate ownership and verify
deletion. There is no automatic rollback after apply/readback failure, timeout
or cancellation. If the kernel rejects quarantine, retained serving rules may
already be compromised: protection remains **unconfirmed**, not fail-safe by
assertion. The helper cannot prevent host-root mutation, guard a container
moved outside the bound bridge/tuple, or prove all resources absent. Inherited
Docker DNS may originate from the host namespace and bypass these bridge
rules, as described below. The future owner must independently retain the
exact loopback-DNS and network-identity contracts; rule verification alone is
not a workload-confinement claim or a remedy for an unverified host relay.

[Focused quarantine tests](../../scripts/test_selected_network_quarantine.py)
cover both pinned forms, failed/unknown command statuses, absent/corrupt old
tables, flowtables, every missing object/predicate, verdict and ordering drift,
IPv6/L2/host/proxy/redirected-publication paths, unrelated traffic and removal
guards. Packet-condition tests are not kernel observations. Network ownership,
watchdog integration, continuous drift supervision and an authorized isolated
kernel experiment remain separate work; no public service or hardware gate is
satisfied by this increment.

### Opt-in isolated quarantine experiment

The runner now implements `--quarantine`, with mocked orchestration tests only.
**It has not executed against either pinned kernel venue.** It does not change
the serving policy, the default experiment, any existing receipt, or the
`--network none` selected-service owner. It is available for both closed venue
profiles without changing their tool, resolver or source-identity admission.

After the original 15 baseline controls and protected two-allow/13-deny stage
pass, the opt-in path:

1. Re-establishes both certificate-verified host and forwarded TLS positives.
2. Checks/applies the exact `Quarantine` create-only batch and verifies fresh
   readback with the actual integer apply and readback exit statuses.
3. Rechecks the private namespace boundary, exact serving tables and independent
   quarantine, then deliberately deletes **only the two owned serving tables**.
   Fresh readback must prove their absence and unchanged quarantine. This is a
   proof-only destructive control, not a production removal API. It does not
   falsely supply resource-absence assertions to `Policy.remove_batch` or relax
   that helper's guards. The JSON `DELETE`/`TABLE` sections of the two pinned
   source archives above define these exact family/name deletion commands.
4. Requires all 15 paths to fail, including both formerly working TLS paths.
   Rechecks all six candidate IPv4/IPv6 loopback listeners, both peer/client
   sinks, exact protected container/DNS admission, unchanged host DNS counters,
   quarantine readback and serving-table absence. A dead listener or changed
   DNS configuration cannot establish success.

The two TLS controls each exchange two frames on a single connection before
closing it. Post-quarantine probes open new connections. This does **not** prove
interruption of a connection held open across installation, arbitrary kernel
traffic, host-root drift resistance or production isolation. Embedded DNS still
depends on the independently checked exact `127.0.0.1` override; drop rules
alone do not constrain a host-originated Docker relay.

Cleanup shares the original resource inventory/removal path. Only after resolved
creation and confirmed container plus network absence may it freshly verify and
remove quarantine, followed by strict readback proving both table sets absent.
Failed/unknown apply, drift, uncertain serving withdrawal, inventory failure or
deadline/cancellation failure cannot become protection or PASS. If cleanup cannot
prove those facts, tables remain until namespace destruction and cleanup stays
unconfirmed. The outer namespace/group/cgroup gates remain mandatory. No retries
or additional operation time are added: the same absolute 480/90/25-second
budgets apply, including the additional probes.

Opt-in inner and outer receipts use `nib.selected-nat-proof/v2` and
`nib.selected-nat-proof-host/v2`. The inner `quarantine` object contains only
closed steps, booleans, the exact batch hash and fixed control names. A step is
an attempted operation, not proof of its completion. `installed_verified` means
the first exact readback succeeded; `serving_withdrawn` means deletion and absence
readback succeeded; `verified` additionally requires all denials, live controls,
DNS and final readbacks; `removed` records confirmed cleanup deletion. None
claims an enduring protected service. Schema/mode mismatch, unknown fields,
integer-for-boolean substitution or missing PASS gates fail validation. Common
serving fields reuse the existing strict validator. Default argv, v1 receipt
shape, source-file set and source-byte/Git-head checks are unchanged; the actual
modified runner bytes must of course be newly source-bound.

After independent review, CI and separate execution authorization, append
`--quarantine` to the existing closed command below (and use
`--venue-profile ubuntu26-ordinary` only for that exact reviewed profile).
Always choose a new output directory and preserve prior observations.
[Focused tests](../../scripts/test_selected_quarantine_proof.py) use no sockets,
Docker, nft, mounts or namespaces. Real quarantine observations, existing-stream
revocation, continuous drift supervision and a service owner remain future gates.

### DNS is a separate boundary

Bridge filtering alone does not block inherited Docker DNS. At Docker tag
`docker-v29.1.3`, commit `fbf3ed25f893e6ce21336f1101590e40a13934f4`,
[TransformForIntNS](https://github.com/moby/moby/blob/fbf3ed25f893e6ce21336f1101590e40a13934f4/daemon/libnetwork/internal/resolvconf/resolvconf.go)
marks inherited resolvers `HostLoopback`; the
[resolver's dialExtDNS](https://github.com/moby/moby/blob/fbf3ed25f893e6ce21336f1101590e40a13934f4/daemon/libnetwork/resolver.go)
then dials from the daemon's host namespace. Explicit resolver overrides instead
use the container namespace. The protected fixture therefore requires exactly
`--dns 127.0.0.1`, with no extra resolver/search/options/hosts overrides, checked
before start. This is container loopback, not host loopback, as documented in
[Docker DNS services](https://docs.docker.com/engine/network/#dns-services).
Docker-local names may still resolve; no upstream DNS forwarding is authorized.

## Concrete experiment

[prove-selected-nat.py](../../scripts/prove-selected-nat.py) stages its committed
source closure (including its venue manifest) and a hash-verified, private read-only copy of a supplied
offline Python image archive. It launches util-linux `unshare` with new mount,
network and PID namespaces, private mount propagation, a new procfs and
`--kill-child=KILL`. The inner process must be PID 1, have different network and
mount namespace inodes, initially only loopback, and an empty nft ruleset.
The [pinned util-linux implementation](https://github.com/util-linux/util-linux/blob/v2.39.3/sys-utils/unshare.c)
and [PID namespace semantics](https://man7.org/linux/man-pages/man7/pid_namespaces.7.html)
are the basis for this teardown boundary.

Inside that namespace it hides `/run`, substitutes a private resolver file,
and starts its **own** Docker daemon with private socket, config, data/exec roots,
CDI directory and cgroup name. It never queries or connects to an existing
Docker daemon. The classic VFS image store avoids an unverified containerd image
store switch. Multiple daemons are explicitly experimental in the
[Docker daemon documentation](https://docs.docker.com/reference/cli/dockerd/);
the failed observations do not qualify this combination.

There is no external interface, veth to the real host, default uplink or public
port. Two private dual-stack Docker bridges simulate ingress and the service
network. Three synthetic fixture containers run nonroot, read-only, without
capabilities/devices/restarts/logs, limited to 128 MiB, one CPU and 32 PIDs each.
The synthetic fixture disables AppArmor confinement to avoid loading a global
Docker profile; **this is not a production container profile**. Its generated
short-lived TLS key stays in the private output tree, mode 0600, mounted read-only.

The archive must contain the already-pinned Python 3.12.13 slim-bookworm amd64
configuration `sha256:64d91f7b885eed272bba87909446b12ff408d4aaa5f1a0e9ca787bbea1a020b9`.
Its known reference manifest is
`sha256:6e13e65c55e33adf203d77ee371cf8bf5d81bd4902ef07565721f46bf44917af`,
from the existing [classic-store pin](p2-t4-provider-classic-store.md).
The report distinguishes this reference from the actually inspected config ID;
it does not invent a registry receipt for a Docker-save archive. No pull occurs.

Before protection, positive controls must reach all 15 tested paths: host and
forwarded TLS, host engine/guard/IPv6, container-to-host/L2-peer/forwarded IPv4
and IPv6, and host-direct plus embedded DNS over TCP and UDP. A controlled
host-namespace DNS sink proves the inherited-relay bypass actually operates.
Only then is the baseline candidate removed, policy installed and read back,
and a replacement candidate created with the exact DNS override. Afterward,
only the two TLS paths may succeed. TLS verifies a generated certificate for
`nib.invalid` and exchanges two fixed frames on the same connection. DNS sink
counters must remain unchanged; all sinks must still be alive after negative
probes. No client-selected payload, URL or hostname is accepted.

## Lifecycle and reporting

The original monotonic budget is 480 seconds of operations plus 90 seconds for
namespace cleanup. Bounded tool collectors retain at most 256 KiB stdout and
64 KiB stderr, using the existing process-group helper. The outer owner reserves
a further bounded 25 seconds for final group absence/reaping. SIGINT/SIGTERM
cooperatively stop new work but retain the original cleanup budget; repeated
cancellation does not extend it. No retries restart the experiment.

Cleanup checks exact labels/image identities, removes the fixtures, proves
container and network absence, and reads policy back before deletion. Unknown
create completion, inventory failure or policy drift prevents rule removal.
An ambiguous namespace is terminated with protection retained until namespace
destruction; neither timeout nor an empty inventory promotes an uncertain create
to success. Only an empty, uniquely owned cgroup is removed, never `cgroup.kill`.
Cleanup or final reaping failure prevents PASS. Original failures remain FAIL
even if subsequent cleanup succeeds.

The output is `observation.json`, not a generic hardware `run.json`. It binds
source hashes/revision, archive hash, UTC interval and strict content-free
observations. Stderr, exceptions, DNS bodies and TLS frames are never copied into
the report. The private scratch tree retains daemon data and the ephemeral key;
do not publish that tree. Publish only the reviewed original receipt in a
separate evidence task. Early CLI/source/input preflight errors occur before any
namespace and must be retained as failed command results, not synthesized PASS.

After the first failed run, policy setup records the optional closed
`policy_step` enum: `check`, `apply`, `readback`, `dns-baseline`, or
`protected-create`. It names the attempted step, not successful completion.
It does not retain stderr, exceptions, rule contents or response data. Cleanup
does not overwrite it. Historical receipts without the field remain valid;
their missing detail is not reconstructed. The
[diagnostic observation](../../evidence/p2/2026-10-25-selected-nat-policy-failure/readback-observation.json)
records `readback`, with cleanup unconfirmed and the namespace reaped. The
diagnostic does not change policy, deadlines or pass gates.

## Legacy CPU venue execution gate

The following instructions apply only to `ubuntu24-cpu`. The separate
[ordinary-host profile](p2-t4-selected-nat-venue.md) has its own closed admission
and measured result. Before another legacy-venue execution, independently
review the exact commit and verify a dedicated CPU-only Linux amd64 venue with:

- CPython **3.12.13** as the host supervisor, Docker daemon **29.1.3**, nftables
  **1.0.9**, and util-linux unshare **2.39.3**. Host and image interpreters are
  distinct inputs. Neither a host running 3.14.4 nor a service image running
  3.12.3 satisfies the host interpreter gate.
- Cgroup v2, root namespace privileges, ordinary nonsymlink `/etc/resolv.conf`,
  and available bridge/netfilter/IPv6 support. Pin/review the venue's actual
  kernel, Docker CLI, containerd, runc, iproute2, mount and OpenSSL binaries before
  approval. The script does not install packages or change daemon configuration.
- A verified offline archive (maximum 512 MiB), a new private scratch directory
  and adequate local space for its VFS store. Existing cgroup names are rejected.

This is not a hardened sandbox against malicious privileged tools. Kernel-module
autoload and cgroup operations share the kernel: the venue must be disposable
and approved, with required modules already available. Do not execute on the GPU
host, a production host, or adapt version checks until the actual compatibility
question has been independently reviewed.

After those gates, the closed command template is:

```sh
"$PROOF_PYTHON" -I -S -B scripts/prove-selected-nat.py --run \
  --archive "$OFFLINE_IMAGE_ARCHIVE" --archive-sha256 "$ARCHIVE_SHA256" \
  --output "$NEW_PRIVATE_OUTPUT_DIRECTORY"
```

The focused test module uses only mocks and temporary files, never sockets,
namespaces, Docker or nft. It covers policy rules/readback, DNS admission,
archive sealing, all-control requirements, sink liveness, cancellation,
uncertain create, cleanup drift, failed spawn, lost/oversized output and reap
failure. It is **not** a kernel or daemon compatibility result. A real, separately
approved namespace observation is still required before adapting the existing
selected-service host owner to this policy. No public-owner integration,
external ingress, production firewall mutation or listing is implemented here.
