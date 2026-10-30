# Metadata Helper Source Publication

Status: software-only source publication, not runtime, privileged execution,
deployment or P3 completion. The three privacy-reviewed generic helpers are
copied byte-for-byte with their original basenames and pins. No private tests,
contracts, fixtures, requests, records, credentials or operator configuration
are included. No workflow or existing helper changes are part of this task.

## Exact Sources

| File | SHA-256 |
| --- | --- |
| [Bootstrap adapter](../../host/metadata-bootstrap-v2-20261029.py) | `f98d33ceb399e8c3a4c4c2d4c33f385f9b4ed751c0a02b9fa3e0b9a27ef7ce27` |
| [Bootstrap monitor](../../host/metadata-bootstrap-monitor-v2-20261029.py) | `52c7e8429ed142cec6b4be43bc4fc5c6207bd78f17272cf4ddebba8057139d53` |
| [Source capsule library](../../host/metadata-source-capsule-20261029.py) | `3baec236b5418692beff94f3e181b945694e3201eb13bb65036251d2079070b7` |

The adapter keeps `OPERATIONAL_ADMISSION_PIN = None`; its default `run` entry
rejects before artifacts or dispatch. The bootstrap and monitor explicitly
refuse standalone execution. The capsule is an inert library with no install
CLI; installation requires an authenticated context and existing supervision.
Importing these definitions does not grant authorization. Callers must
authenticate source and runtime before loading them. No invocation is supplied.

## Fixed Predecessor Contract

The embedded producer remains `31162745cb90f4ab268f1b2b8eb8c768b750442a`, not
this publication's commit. The capsule's exact five-file inventory and the
adapter's raw seed, capsule and capture-helper pins are unchanged. Existing
`private.*` schema names are preserved protocol identifiers, not bundled data.
Prepared/layout records do not fabricate a full source archive or invoke a
metadata controller, importer, model runtime or mount operation.

The existing [resource helper](../../host/probe-build-resources.py),
[ownership/bootstrap helper](../../host/inspect-public-weight-cache.py) and
[capture helper](../../host/probe.py) remain hash-pinned dependencies. Worker
limits stay two CPU-equivalents, 8 GiB memory, zero swap, 256 tasks and
whole-group OOM handling. Exact namespace-relative `0::/<group>` membership,
the original absolute cutoff, authenticated terminal/exit joins and independent
complete-inventory absence are required; unknown helpers cannot become absent
through local cleanup, retries or a replacement directory.

## Remaining Gates and Verification

Publication does not supply a reviewed supported runtime with a finite delegated
cgroup and matching namespace view. The seed owner, capture helper and observers
outside the worker group also need finite outer bounds and owned cleanup.
The [read-only capability report](p3-t1-linux-capabilities.md) is not that proof.
No privileged setup, runtime flags, target or transport pins are invented here.

Private operational authorization, target and runtime authentication, accounting,
capacity, writer exclusion, monitoring and uncertain-state handoff remain
separate prerequisites. Default admission remains unavailable; publication
cannot release holds or authorize a live attempt.

Verification for this copy is limited to static source inspection, exact hashes
and bytewise equality with the approved originals. No new tests have run; the
helpers were not imported, compiled or executed. Earlier reviewed synthetic
results remain historical, not new kernel-composition or deployment evidence.
Independent publication review and repository CI remain required.
