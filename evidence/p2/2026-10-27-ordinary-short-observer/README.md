# Five-Minute Ordinary-TLS Observer: Pass

One scheduled sample passed against the already-running ordinary eight-B300
selected-model service. The observer waited out its full 300-second window,
from October 27, 2026 at 04:59:30 to 05:04:30 UTC. This is a short live validation
of the observer, not a completed 24-hour observation or a sustained-load test.
Time between samples is unobserved; a passing request is not continuous uptime.

These are the unmodified, content-free originals from committed producer
`fd380d29456816f329dbffca0c46a67c4c6530f8`:

| Receipt | SHA-256 |
|---|---|
| [observer-run.json](observer-run.json) | `92c6d36597f24f3a39831ba5d8271dcd85bfab1f488824e1446c610cf4c35c74` |
| [0000-intent.json](0000-intent.json) | `8e93b7a65eae279f561ebc3a54ef0ffb3a4c0e5fdff689d7b218506b70fa7970` |
| [0000-sample.json](0000-sample.json) | `23fb7364e9fb63d1e004e54771daa96bb3a32a04cdb011656deb558c32013516` |
| [terminal.json](terminal.json) | `b166eeeb4525988a178a4a4b5c1df14d8095420c57ac200e0fba670917651166` |

`observer-run.json` preserves the producer's original `run.json` bytes. Only the
filename differs because the evidence linter reserves `run.json` for the generic
`nib.evidence/v1` format. The receipt hash and chain have not changed.

The four HTTP attempts covered health, missing-key rejection, wrong-key rejection
and one authenticated stream. TLS verification used system roots. The stream
reported 36 input tokens, 13 output tokens and zero cached tokens, with `stop`.
First content arrived in 0.627 seconds; the inference exchange took 0.787 seconds.
There were no retries, failed samples, unknown token usage or unmatched intents.
The rejected-key checks do not measure upstream calls behind the server.

Reproduce the [observer procedure](../../../docs/tasks/p2-t4-ordinary-soak.md)
with `--duration-seconds 300 --interval-seconds 300 --max-samples 1`, a new
receipt directory and an independently authorized cutoff. Keep the address,
hostname, API key and deployment identity in private operator configuration.
This observer did not launch, alter, renew or tear down the running service.

The [offline replay tests](../../../scripts/test_ordinary_soak_evidence.py)
verify exact file hashes, chain order, the historical named-source closure,
counts and false qualification flags. A hash chain is not a signature or remote
attestation. This result proves neither marketplace billing nor production SLOs,
memory stability, peak-load capacity, model quality or confidential inference.
No plan phase is marked complete by this result.
