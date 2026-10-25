# External selected-service HTTPS smoke: PASS

These are unchanged, content-free reports from two committed external clients:

- [startup.json](startup.json), source `0f14583de4e91f2162c14d0fc067872de1668996`,
  SHA-256 `60ad327e3a7c9df028f335c83a2005f66b67343a54dbb17f921570862ea06fc4`.
- [probe.json](probe.json), source `cebbaa21ce38316ea853ffd87d3ac9a8a25d7dce`,
  SHA-256 `c216833c7571a71edf37e0e855d3419b34f6fa2adad0cfd049e404cacc0197d7`.

The startup observer ran from 2026-10-25 05:55:51.373940 to
06:10:26.035427 UTC. It observed 88 pre-verification transport failures during
startup, then verified the pinned TLS peer and received one successful health
response on attempt 89. It read no API credential and sent no model request.
These transport failures are bounded startup observations, not an uptime
measurement or evidence of a previously serving endpoint becoming unavailable.

The separate smoke ran from 06:11:28.132478 to 06:11:34.263661 UTC. All four
checks passed, each on its own certificate- and hostname-verified TLS connection:
health, missing-key rejection, wrong-key rejection, and an authenticated stream.
Exactly one authenticated inference request was attempted, with no retries.
The completed response reported **36 prompt tokens and 13 completion tokens**,
with `finish_reason=stop`. First content arrived after **2.598 seconds**; the
measured inference exchange took **3.349 seconds**. These are one external
client's observations, including its network path, not an engine-only latency
or representative throughput benchmark. Cache usage was not reported and stays
unknown. Rejected requests were not independently measured at the engine.

The clients used a private numeric destination and an explicitly pinned test
certificate, not public DNS or an operating-system trust-store bypass. Their
reports retain source-file hashes but neither destination, key, prompt nor
response text. The private destination cannot be reconstructed or independently
joined to the server from this evidence. Source checks cover the named files,
not a complete platform attestation.

This proves one externally reachable ordinary HTTPS inference path. It does
**not** prove the server owner's terminal cleanup, sustained availability, full
provider conformance, model quality, marketplace readiness or confidential
inference. All such flags remain false in the originals. The running owner's
eventual result must be recorded separately; client success cannot upgrade it.
See the [finite owner contract](../../../docs/tasks/p2-t4-selected-finite-owner.md)
and [offline evidence tests](../../../scripts/test_selected_external_evidence.py).
