# Retained Candidate: External HTTPS Smoke Passed

These unchanged, content-free receipts record an external health and inference
check after the separate [1M qualification](../2026-10-26-selected-million-pass/README.md).
Both clients ran from committed source
`317c3f3b3dd4734900148c4d30bbf88a10827627`:

- [startup.json](startup.json), SHA-256
  `2ccf30d6c6541ebffb0ddba6c7f9714353ddc0980c075b0e509def6fdcf7509f`.
- [probe.json](probe.json), SHA-256
  `d5f8e0c3f2df02d2cb1d40008749af9759283ba44c8bf83b9a31928aa0fc7aa6`.

The credential-free startup observer began at 22:30:55 UTC on October 26, 2026.
It saw 94 pre-verification transport failures during startup, then verified
the pinned TLS peer and received a healthy response on attempt 95 at 22:46:30.
It read no API key and made no model requests. These are bounded startup
observations, not an uptime denominator or outages of a ready service.

The separate authenticated smoke ran from 22:46:50 to 22:46:54 UTC. All four
checks passed on separately verified TLS connections: health, missing-key
rejection, wrong-key rejection and authenticated streaming. The one inference
request reported 36 input and 13 output tokens, zero cache reads and no retries
or unknown usage. Its finish reason was `stop`. First content arrived in 0.667 seconds;
the measured inference exchange took 0.781 seconds. This small external smoke
includes the client's network path and is not a throughput benchmark. Rejected
requests were not independently counted at the engine.

Reproduction uses `scripts/wait-selected-service.py --run`, then
`scripts/probe-selected-service.py --run`, each with `python3 -I -S -B`,
`--tls-mode pinned`, the authorized numeric target, verified certificate hash
and a fixed deadline. Only the second command reads the disposable test API key.
Keep operator addresses, credentials and certificate material outside the repo.
Neither client records prompt/response text. The external reports cannot
independently identify the server image or join it to the earlier owner; the
named client-source hashes are not platform attestation.

The owner was retained rather than terminated for this observation, so there
is no terminal cleanup receipt here. Its eventual result must be recorded
separately. These receipts do not prove continued availability, full provider
conformance, quality, marketplace readiness, ZDR or confidential inference.
The 1M candidate remains unlisted under the
[marketplace integration gate](../../../docs/tasks/p2-t3-million-context.md#marketplace-integration-gate).
All broader eligibility and privacy flags remain false. Shared
[offline replay tests](../../../scripts/test_selected_external_evidence.py)
check both the old and new originals without changing either result.
