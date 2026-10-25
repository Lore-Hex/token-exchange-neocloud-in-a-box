# Trusted Public TLS and Provider Conformance

These are two byte-preserved observations from the committed external tools at
`66f93d4749d79d00a9b4e702c4e6011ba8b540b3`. The private operator deployment is
not part of this toolkit. Endpoint addresses, hostname, credential, catalog
inputs and operational journals remain private; the tools emitted only hashes
and fixed metadata. No raw prompt, response or tool arguments are published.

## Observed Results

- [Startup observer](startup.json): October 25, 2026, 11:43:00-11:57:24 UTC.
  Eighty-seven transport failures preceded one successful system-root TLS and
  health check. Its 88 attempts used bounded backoff; no credential was read
  and no model request was made. This is startup observation, not an uptime rate.
- [Conformance](conformance.json): 11:58:25-11:58:58 UTC. All 17 required
  provider-check assertions passed, plus health, missing/wrong-key rejection and
  an exact expected-catalog comparison. Every attempt verified system-root TLS.
  Thirteen HTTP attempts included nine authenticated synthetic generations;
  the client recorded 280 prompt and 246 completion tokens, zero retries,
  zero redirects and closed connections. Rejected-key checks do not measure
  whether an upstream engine was invoked.

The upstream declared-v2 check reports `skip`, not `pass`; the wrapper separately
validates the full expected catalog using the pinned catalog parser. The sealed
checker payload, checker wheel, requirements and producer source closure are
identified in the report. [Regression tests](../../../scripts/test_selected_public_conformance_evidence.py)
pin both original byte hashes and verify all 39 named source hashes against the
producer commit, timing, limits, usage and false qualification flags.

The client expected service source `75b66500621793d668959a5e71baf3f26760eed1`
and the recorded preparation digest. Those are operator expectations, not remote
runtime attestation. The catalog identifies the selected abliterated checkpoint
and the bounded ordinary eager profile (8,192 context, 256 output, concurrency
one); these observations do not independently establish loaded weights, runtime
identity or broader capability. The fixed admission cutoff is October 26 at
07:50:45 UTC; this record cannot renew it.

## Not Proven

No sustained availability, completed 24-hour soak, owner termination or resource
cleanup is established by these external observations. No TR marketplace
activation, routed customer request or settlement was tested here. Quality,
confidentiality, E2EE, ownership verification, runtime verification and deployment
eligibility remain false. The separate graph throughput results do not describe
this eager serving profile. No inference was repeated to prepare this evidence.

The initial secret scan flagged one public source hash in the compact report.
Its exception is restricted to that exact path, field and literal digest; the
shared negative controls still reject other values, fields, paths and adjacent
credentials. The original report was not reformatted to evade the scanner.
