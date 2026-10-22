# Actual Private DeepSeek API Conformance

Producer `efa6a7e8bd38794d1187f15b35aa77b6dc78912f`, 2026-10-22
20:24:20 through 20:51:36 UTC, retained eight-B300 lab, TP4 x DP2.
`run.json` and `output.log` are the original recorder bytes. `input-contract.json`
and `receipt.json` are the unchanged prepared input contract and bundle receipt,
not reconstructed runtime evidence. These files contain bounded metadata only,
not prompts, completions, headers, credentials or private host addresses.

## Measured Result

- The unchanged upstream `trustedrouter-provider-check` 0.1.8 ran real tiers 1-4
  against the owned DeepSeek loopback API: all **17 mandatory checks passed**.
  `catalog.declared-v2` was the one deliberate skip; no catalog URL was supplied.
  Both the upstream gate and the stricter mandatory/native-model gate passed.
- The separate CPU checker used the locked wheel payload and pinned Python
  image, shared only the owned engine's network namespace, and exited 0.
  Namespace verification and checker cleanup passed. No dependency was imported
  into the engine, no endpoint was published, and no logging policy was relaxed.
- The original source-contract readiness frame and report digest match the guest
  acknowledgement. Both runtime samples preserve the same process, executable,
  argv and loopback-listener identities. Null stdio and configured limits passed.
- All four unchanged tokenizer-parity cases passed in eight requests. Startup
  readiness took 1,208,301 ms. The engine exited 0 without OOM, was stopped, and
  both owned containers were removed before the fixed 21:09 UTC cutoff.
  The VM, weight cache, images and access key were retained as authorized.
- Peak observed cgroup memory was 41,233,301,504 bytes (about 38.4 GiB), with
  zero configured swap and 582/583 observed tasks. This is not GPU memory,
  whole-node memory, API-only RSS or a complete weight-memory measurement.

The exact private bundle passed an offline replay of the original producer's
`bundle_inputs` and `validate_result`, plus `validate_ready`, `validate_join` and
`validate_worker`. Repository regressions pin all four original file hashes,
bind the prepared sources to the producer commit, reuse the checker/runtime/parity
validators, and reject changed checks, acknowledgements and cleanup claims.

## Boundaries

This is **direct private API conformance only**. The original outer
`result=fail`, `status=BLOCKED`, `deployable=false`, `confidential=false` and
`hardware_validated=false` remain unchanged alongside `functional_pass=true`.
Worker endpoint attestation, quality acceptance and catalog checking remain false;
the runtime still has `tau_admission=false` and `quality_eligible=false`.

No tau/quality score, public service, TLS/front-door/authentication test, declared
Catalog v2 validation, benchmark, reproducibility, hardware attestation or full
P2/confidential qualification is granted. The host uses AMD SEV-SNP, not the
plan's Intel TDX deployment. The narrow run does not authorize another GPU run.
Earlier failures and the real closed-endpoint negative control remain intact.
See the [source audit and execution boundary](../../../docs/tasks/p2-t3-private-provider-check.md).
