# Actual Single-Engine Runtime Observations

Producer `2b39b366c801f9cb970cc164c97baded87630c6f`, 2026-10-22
17:54:48 through 18:21:17 UTC, retained eight-B300 CC lab, TP4 x DP2.
`run.json` and `output.log` are the original recorder bytes.
`input-contract.json` is the exact prepared `payload/lab.json`, not a reconstructed
runtime receipt. It contains fixed public settings, source hashes and image paths,
not operator credentials or private host addresses.

## Measured Result

- Both before/after observations passed: stable owned process, actual executable,
  argv and loopback listener hashes, null stdout/stderr, unchanged configured
  cgroup limits. The executable matches the prepared interpreter digest.
- Cgroup peak memory: 41,216,716,800 then 41,216,843,776 bytes, approximately
  **38.4 GiB**. Current task counts: 582 then 583; limit 1,024. Memory limit is
  1 TiB, swap limit zero. No OOM; container exit 0; cleanup confirmed.
- All four unchanged tokenizer cases passed again: text, system, tools and
  tool continuation. Eight requests; independent and endpoint token IDs and
  generated input usage matched at 12, 16, 275 and 332 input tokens respectively.
  Every generation was bounded to one output token.
- Startup readiness: 1,219,344 ms. Parity request phase: 11,868 ms. The engine
  was stopped and its owned container removed; the separately approved VM,
  weight cache and access key were retained.

The original source-bound bundle was rehashed offline and the original producer's
`bundle_inputs` and `validate_result` accepted these bytes. Regression tests
reuse the runtime and parity validators and pin the original file hashes.

## Boundaries

`functional_pass=true` is separate from the unchanged outer `result=fail`,
`status=BLOCKED`, `deployable=false` and `confidential=false`. No evidence was
rewritten to manufacture qualification. `tau_admission` and `quality_eligible`
remain false. The host is AMD SEV-SNP, not the plan's Intel TDX deployment.

This is one API process and fixed synthetic requests at context 4,096 and one
sequence, not the full tau topology, a quality score, throughput/TTFT benchmark,
or production capacity test. Cgroup memory includes earlier application and
observer work but is **not** GPU memory, whole-node memory, API-only RSS or a
complete weight-memory measurement: mounted weights were staged outside this
container. Task counts include threads. The samples are sequential observations,
not atomic snapshots or peak task counts.

Null stdio and listener ownership do not establish full logging policy, immutable
runtime provenance, CPU/image/TLS/fabric attestation or confidentiality. GPU CC
readback is not cryptographic appraisal. See the
[observer contract](../../../docs/tasks/p2-t3-tau-runtime-observation.md) for the
implemented checks and the remaining live-admission requirements. Earlier failed
parity evidence and the prior successful parity run remain intact.
