# Selected-service network reservation

This is the first software-only network ownership increment for the future
finite ordinary selected-service owner. It is **not that owner**, a public
launcher or a measured network result. No container, engine, listener or
publication can be started by this module. There is no executable CLI and no
transition out of quarantine. Independent final-head review, complete CI and
separate operator authorization are required before any host use.

## Reuse and boundary

[selected_service_network.py](../../scripts/selected_service_network.py) reuses
the [smoke owner's](../../host/record-selected-service-smoke.py) bounded Docker
calls and original wall/monotonic deadline, the existing process-group executor,
the [ordinary venue](p2-t4-selected-nat-venue.md) admission and pinned tools, and
the pure [Policy and Quarantine](p2-t4-selected-nat-proof.md) contracts. It does
not edit those implementations or disguise a NAT inspection as network-none.

Admission requires Linux/root, the existing Ubuntu26 tool/package/kernel/Python
pins, exact nftables 1.1.6 and Docker server 29.1.3 Linux/amd64, the one local
Unix daemon socket, and a closed environment. Tools use the existing pinned
PATH, no ambient Docker context, proxy or preload. Calls retain the original
deadline, ten-second operation cap, 25-second client-reap reserve and bounded
stdout/stderr. No timeout renews the lifetime. This is not attestation of the
running daemon, host root or an execution closure.

The explicit operator-selected IPv4 subnet must be RFC1918 and /24 through /30;
gateway and reserved service address must be distinct usable members. The
published address must be outside that subnet. The bridge name is derived from
the ownership token, and the full Docker network name and label retain the full
token. No automatic subnet/port selection occurs. Ingress and publication
values bind policy rendering only; this increment does not establish routability,
interface/address ownership or approval to expose them.

## Durable creation and cleanup

The caller supplies an empty private `0700` journal directory. Files are
exclusive-created `0600`, fsynced before the directory is fsynced. Fixed ordered
events bind the original network contract and both deadlines. Symlinks, hard
links, unsafe modes/owners, extra files/fields, duplicate JSON keys, partial
writes and skipped/repeated events reject. Journal paths and network details
are private operator state, not public evidence; no credentials, exception
text or Docker output are stored.

The reservation checks name/bridge/table collisions, journals quarantine intent,
installs and freshly reads the exact drop-only tables, then does the same for
the serving tables. Quarantine remains active throughout. Only then is network
create intent durably recorded, before the single daemon call. A returned full
ID is durably recorded before further inspection. Network name uniqueness is
not assumed: a bounded complete inventory rejects duplicate matches. Inspection
requires the full ID/name/label, local bridge driver, explicit IPv4-only NAT
options, exact IPAM subnet/gateway and no attached endpoints or swarm features.

Cleanup freshly verifies independent quarantine before any removal. Serving
policy drift cannot prevent this drop-only check. Only the exact full network
ID with matching ownership and the full empty-network contract can be removed;
no force-disconnect, unrelated container deletion or host process kill exists.
Successful inventory, including an exact-ID query for a resolved create, is
required afterward. A changed/occupied network is left for operator inspection.

An interrupted/nonzero/malformed create stays unresolved even if a later owned
network is recovered and removed, or inventory is empty. The result distinguishes
observed absence from confirmed completion. **No installed policy or quarantine
table is ever deleted**, including after successful empty-network cleanup. This
increment cannot establish absence of future container creations and therefore
does not authorize policy deletion. No retry, resume-to-serve or background
watchdog is installed. Filesystem/policy failures propagate without an invented
successful receipt; callers must not print their raw exception chains.

## Source grounding

Docker CLI v29.1.3
[create flags and request mapping](https://github.com/docker/cli/blob/v29.1.3/cli/command/network/create.go#L70-L120)
define the explicit network options and returned ID. Its
[list formatter](https://github.com/docker/cli/blob/v29.1.3/cli/command/network/formatter.go#L70-L82)
retains full IDs with no truncation. Empty IPAM options are an empty map from
[NewMapOpts](https://github.com/docker/cli/blob/v29.1.3/opts/opts.go#L154-L163),
not a null fixture invented to match a test.

An unset IP allocation range is accepted only as an omitted `IPRange` or the
empty string. The pinned Moby IPAM type below uses `netip.Prefix`, whose zero
value can still serialize as an empty string with `omitempty`. Both forms mean
no separately configured allocation range; a nonempty range, null, auxiliary
addresses, multiple IPAM configurations or any other changed field reject.
The shared synthetic network fixture includes the explicit empty string so
reservation, service ownership and cleanup tests exercise that representation.
This does not relax subnet, gateway, driver, empty options or ownership checks,
nor prove that a previously failed hardware run recovered.

Moby `docker-v29.1.3`, commit
`fbf3ed25f893e6ce21336f1101590e40a13934f4`, defines the
[network fields](https://github.com/moby/moby/blob/fbf3ed25f893e6ce21336f1101590e40a13934f4/api/types/network/network.go),
[attachment inventory](https://github.com/moby/moby/blob/fbf3ed25f893e6ce21336f1101590e40a13934f4/api/types/network/inspect.go)
and [IPAM representation](https://github.com/moby/moby/blob/fbf3ed25f893e6ce21336f1101590e40a13934f4/api/types/network/ipam.go).
Its [network implementation](https://github.com/moby/moby/blob/fbf3ed25f893e6ce21336f1101590e40a13934f4/daemon/network.go#L263-L390)
applies default driver options, so any unexpected resulting options reject.
These are source-derived synthetic fixtures, not actual daemon compatibility
evidence. Existing isolated NAT observations do not qualify this new path.

## Required next increment

The future host owner must compose `admitted_runtime` through final cleanup,
validate the public weight view before create, and preserve the shared container
identity/resource/mount checks. It must add durable container-create intent,
mandatory `--dns 127.0.0.1` with exact fresh DNS inspection, a single configured
TLS8443 publication to the approved ingress, and exact requested/operational
network/IP/publication joins. It must verify local ingress/address and route
nonoverlap before reservation; none of these host deployment values are inferred.

Before allowing start or lifting quarantine it also needs an independently
owned watchdog using the same original deadline and durable journal, bounded
attached-output handling, continuous fresh network/rule/identity checks,
quarantine-before-teardown on drift, and final container/network/create joins.
The watchdog and supervisor must not share a renewed deadline or trust a cached
readback. Unknown creation completion must retain quarantine. Actual isolation,
inference, auth/catalog/usage/stream/drain and public readiness require subsequent
separately authorized evidence; all deployment/privacy claims remain false.

## Focused validation

`test_selected_service_network.py` uses temporary files and synthetic command
results only. It covers exact identity/options, private durable journals,
intent-before-daemon ordering, policy-before-create ordering, interrupted create
before/after daemon completion, malformed IDs, collisions, readback drift,
unrelated/occupied networks, denied removal, renamed IDs, original deadlines,
closed host versions and environment. Existing smoke/policy/quarantine/venue
regressions run unchanged. No full suite or privileged proof is implied.
