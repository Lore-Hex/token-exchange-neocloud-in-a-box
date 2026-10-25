# Finite ordinary selected-service owner

This is an executable, software-tested owner, not a measured host deployment.
Independent final-head review, complete CI and a separately approved bounded
host test are required before use. No hardware, production route, DNS record,
marketplace listing or provider identity is added by this task.

[record-selected-service.py](../../host/record-selected-service.py) composes the
[shared smoke admission](../../host/record-selected-service-smoke.py),
[network reservation](p2-t4-selected-network-reservation.md) and
[finite ownership helpers](../../scripts/selected_service_owner.py). The old
network-none smoke and the pure policy/quarantine implementations are unchanged.
The owner remains ordinary/non-confidential, with 8192 context, 256 output,
single admission, eight selected GPUs, 512 GiB memory and no additional swap.
Engine/parser flags, canonical model identity and accounting are unchanged.

## Admission and execution

The entrypoint requires root Linux, isolated Python startup (`-I -S -B`), the
existing closed [Ubuntu26 venue](p2-t4-selected-nat-venue.md), nftables 1.1.6,
Docker server 29.1.3 Linux/amd64 and the fixed local Unix daemon socket. It
reuses preparation/build/image/source joins, private credential snapshots,
the public weight-view check and all existing nonroot/read-only container
limits and mount checks. Private snapshots stay alive through watchdog join
and resource cleanup; failure removing them prevents PASS.

The shared smoke admission keeps its existing closed lab environment. Both run
and inspect validate that exact yielded environment, then construct the separate
closed network-proof environment for pinned network tools and the watchdog.
Neither environment is widened, merged with the ambient shell or substituted
for the other at admission.

The operator must explicitly provide ingress interface, host IPv4 address and
port, RFC1918 subnet, gateway, service IPv4 address and an empty root-owned
`0700` state directory, in addition to the existing smoke inputs. There is no
default address, discovery of public identity, arbitrary endpoint or subnet
fallback. `--inspect` performs read-only admission, host-target, weight-view and
exact bounded runtime-intent checks,
never starts a container and never returns a runtime PASS. `--run` is the
explicit mutating mode. Neither invocation is authorized by this document.

Before installing any published-tuple drop, the ingress must be administratively
UP and uniquely own the exact global-scope address. Existing IPv4 interface
networks and non-default routes may not overlap the requested subnet. The first
owner additionally requires an otherwise empty Docker container inventory and
no TCP listener on the requested port, including IPv6 wildcard listeners. These
are point-in-time checks on a trusted operator-controlled host, not protection
against a hostile root creating a concurrent listener. The same bridge must
subsequently retain its exact gateway/prefix. Nothing changes host resolver
files, daemon configuration, unrelated networks, routes or global nft tables.

The single visible `/usr/bin/dockerd` process must use the already pinned
executable inode and share the owner's network, PID and user namespaces. Its
start ticks, executable inode, namespaces and protected `/run/docker.sock`
identity are checked again during supervision. A private/different daemon,
PID reuse, socket replacement or namespace mismatch rejects. This is a local
trusted-root identity binding, not daemon attestation or a hostile-root defense.

The requested container uses the shared command plus only an exact full network
ID, fixed service IP, `--dns 127.0.0.1` and the single explicit
`HOST_ADDRESS:HOST_PORT:8443/tcp` publication. Requested and operational ports,
IPAM, full network ID, gateway, prefix, endpoint ID/MAC, aliases, DNS settings,
labels and complete network attachment inventory must agree. No extra endpoint,
host networking, exposed engine port, IPv6 address, DNS relay override or
foreign container is admitted. The runtime inspection is never fabricated as
network-none. Network definition and exact endpoint membership are checked
separately. This does not assert packet-level isolation from inspections alone.

## One cutoff and independent supervision

Both processes retain the original wall and monotonic cutoff, capped at 4800
seconds by default. The explicit [ordinary day profile](p2-t4-selected-service-day-profile.md)
admits at most 24 hours with newly bound owner source and no smoke mode.
The in-container owner retains the existing 180-second outer cleanup
reserve and its existing front-first drain. There is no deadline extension,
second start, auto-retry or unattended relaunch. Optional existing inference,
conformance, protocol and tool smoke modes remain mutually exclusive.

A new-session watchdog starts and acknowledges readiness before network create.
The network and container intents are exclusive, private, fsynced records made
before daemon calls; returned full IDs are recorded before inspection. The
container journal rejects unknown files/fields, duplicate JSON keys, unsafe
modes/owners, symlinks, hard links, partial records and reordered events. Its
private command/path data are never copied into public receipts. A journal
size failure is checked before firewall mutation. Network contracts and event
records retain their 4096-byte limit. Only `runtime.json` has an 8192-byte limit,
used identically by preflight, exclusive writes and bounded reads. The pinned
base image includes 25 environment entries and a long mandatory CUDA driver
constraint; a complete normal runtime intent does not fit the original 4096
bytes. No constraint is dropped or abbreviated to fit. The regression fixture
is the unchanged public OCI config, SHA-256
`bd8a5d7a8774800c92d714c0c226b32c1863f929150f36efd610aebcd12a335d`,
from the already pinned base manifest. Tests exercise its actual command,
round-trip storage, exact limit, oversized reads/writes, and rejection before
any firewall/network/container creation. This is a software regression test,
not evidence of successful deployment.

The runtime intent also records the exact labels of the admitted image.
[Docker preserves labels inherited from base images](https://docs.docker.com/reference/dockerfile/#label).
Container inspection therefore requires those exact labels plus the unique
owner label, not only the owner label. Missing, altered or extra labels reject;
an image that already declares the reserved owner label rejects before network
mutation. Label keys/values/counts are bounded and the complete intent still
must fit 8192 bytes. The public OCI fixture exercises the inherited labels as
well as the long environment. No labels are removed from the image, no new
image is required, and legacy intents lacking this binding fail closed.

Weight-view and runtime-intent failures have separate fixed reason labels.
An early failure remains failed even when private snapshot cleanup succeeds;
the cleanup flag records that success independently. A cleanup exception still
prevents PASS and leaves the flag false.

Only the watchdog performs the one serving transition. It first rechecks the
created container, complete network identity, host target, serving policy and
independent quarantine. It durably records start intent, then deletes only the
two owned quarantine tables and verifies serving policy plus quarantine absence
before acknowledging. This is an explicit live-resource admission, **not** the
teardown-only `Quarantine.remove_batch` API with fabricated absence arguments.
The original pure cleanup gates remain unchanged.

While the parent consumes bounded attached lifecycle output, the watchdog
checks the daemon, container, network, host address and exact nft readback at
each bounded supervision cycle. A changed start/exit snapshot permits at most
three fresh cross-inspection samples; a stable mismatch rejects immediately.
This is not a retry of a workload or normalization of actual policy. Checks
have ten-second operation caps, existing 25-second client reaping and a
180-second phase reserve; cleanup never exceeds the original cutoff. Detection
is sampled and command-bounded, not instantaneous atomic enforcement.

Drift, parent-pipe EOF, cancellation and expiry enter cleanup. Fresh independent
quarantine is attempted before any teardown. If installing it fails, only the
exact owned workload may still be stopped, but confinement/overall cleanup is
not reported successful. Container TERM/drain/forced removal reuses the existing
ownership checks; network removal requires an exact empty owned network.
Only resolved creates plus fresh container/network absence permit removal of
the serving and quarantine tables, followed by fresh owned-table absence.
Unknown create completion always remains unresolved and retains quarantine,
even after observing or removing an object. No foreign endpoint is disconnected.

The parent joins watchdog status, bounded output, process-group reaping,
lifecycle result and private-input cleanup. A dead/unreaped watchdog cannot
race a parent fallback quarantine release: fallback runs only after confirmed
watchdog group removal, reinstalls quarantine first and uses the same resolved
creation/absence gates before removing it. Confirmed cleanup does
not turn a failed lifecycle/drift/cancellation into PASS. Conversely, a failed
run can retain truthful confirmed cleanup. Unknown/denied operations remain
unconfirmed and require operator inspection of the retained private journal.
Simultaneous loss of both owners, host crash or privileged interference is not
claimed recoverable; no persistent supervisor/restart service is installed.

## Source and evidence limits

Docker CLI v29.1.3 [container option mapping](https://github.com/docker/cli/blob/v29.1.3/cli/command/container/opts.go)
defines static IP/DNS/publication requests and adds published ports to exposed
ports. Moby `docker-v29.1.3`, commit
`fbf3ed25f893e6ce21336f1101590e40a13934f4`, defines
[endpoint fields](https://github.com/moby/moby/blob/fbf3ed25f893e6ce21336f1101590e40a13934f4/api/types/network/endpoint.go)
and the [network attachment map](https://github.com/moby/moby/blob/fbf3ed25f893e6ce21336f1101590e40a13934f4/api/types/network/inspect.go).
Its [create path](https://github.com/moby/moby/blob/fbf3ed25f893e6ce21336f1101590e40a13934f4/daemon/create.go)
records requested settings before start. Its
[network lifecycle](https://github.com/moby/moby/blob/fbf3ed25f893e6ce21336f1101590e40a13934f4/daemon/container_operations.go)
keys endpoint settings by network name even when requested by full ID, allocates
operational endpoint data during start, derives DNS names and clears operational
addresses during release. Synthetic fixtures preserve those distinct states.
Linux documents the fixed [TCP proc fields](https://docs.kernel.org/networking/proc_net_tcp.html);
the implementation reuses the existing bounded kernel reader.

`result: pass` means this finite owner observed its closed software lifecycle
and cleanup joins. It is not an external client reachability test, packet proof,
uptime/SLA, full-quality tau gate, catalog publication, confidential service or
marketplace readiness. `tls_publication_requested` distinguishes a real Docker
publication request from the still-false deployment/publication claims. No
raw output, exception chain, private address, hostname, credential path,
request or response content is emitted. Optional validated lifecycle reports
retain their original usage and failure outcomes. New hardware compatibility,
ingress reachability and packet-isolation evidence remain required.

## Validation

[Focused tests](../../scripts/test_selected_service_owner.py) exercise private
intent and mutation boundaries, exact stopped/running/exited inspections,
network/DNS/publication negatives, host overlap/listener rejection, daemon
identity, original deadlines, one serving transition, interrupted creates,
drift quarantine ordering, unrelated-container protection, cancellation,
watchdog reaping and sticky cleanup/result joins. One GPU-free subprocess test
checks the actual acknowledgment/final-output pipe boundary. All other daemon,
kernel and host observations are synthetic. No Docker/nft/hardware execution
or full local suite is implied by those tests.
