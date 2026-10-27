# Public 1M Endpoint: External HTTPS Smoke Passed

The selected ordinary eight-B300 service answered on public HTTPS after starting
with the finite `ordinary-7d` owner. This is a point-in-time endpoint check, not
a completed seven-day soak, marketplace settlement or confidential qualification.
The service remains running; there is no terminal owner receipt yet.

Both unmodified, content-free client receipts identify producer
`0ac1642de8a6c84bbb23ae24b212b54fbd88ac1d`:

- [startup.json](startup.json), SHA-256
  `0e935eaccf19f883a209fbc1f232804d8d69243539ac2cd6174e1abbb582733c`.
- [probe.json](probe.json), SHA-256
  `05537fcd64b60d749fc979b11d2400f3d6168eb37a9cf6e6a1f8e740b740cef2`.

The startup observer ran on October 27, 2026 from 03:00:50 to 03:16:39 UTC.
It saw 95 pre-verification transport failures during cold startup, then a
verified TLS connection and healthy response on attempt 96. It accessed no
credential and made no inference request. Startup attempts are not a live
service's uptime denominator.

The separate smoke ran from 03:17:10 to 03:17:14 UTC. Health, missing-key and
wrong-key rejection, and authenticated streaming all passed. The one inference
request returned 36 prompt and 13 completion tokens, zero cache reads, no
retries and known usage. First content arrived in 0.671 seconds; the measured
inference exchange took 0.793 seconds and ended with `stop`. This small exchange
includes the external network path and is not a throughput benchmark.

Reproduce with committed `scripts/wait-selected-service.py --run`, followed by
`scripts/probe-selected-service.py --run`, each under `python3 -I -S -B`, with
`--tls-mode system-roots`, an authorized address and hostname, and fixed
deadlines. Only the probe reads the operator API key. Keep operator identity,
DNS, addresses, credentials and certificate material outside this repository.

The producer was squash-merged as
`95de501a21a2977e7c7aa6a70695512dd33a41f9`. Its difference from the producer is
README text only. The [offline replay tests](../../../scripts/test_selected_external_evidence.py)
require that merged revision in history and verify every named producer source
hash against it; they do not rewrite the original producer revision or receipts.
The observations do not independently identify the running server image or
prove continuous readiness. All conformance, quality, deployment, confidential
and E2EE eligibility flags remain false.
