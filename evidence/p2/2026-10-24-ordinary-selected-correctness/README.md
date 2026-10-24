# Selected DeepSeek correctness on eight ordinary B300 GPUs

**Measured:** all eight fixed correctness cases and ten requests passed on
October 24, 2026. Both tool cases completed their second turn. This is the
user-selected abliterated checkpoint, not the original DeepSeek release.

| Field | Recorded value |
| --- | --- |
| Checkpoint | `dealignai/DeepSeek-V4.1-Flash-1017-UNCENSORED-FP8` |
| Revision | `c19ca5ca4eaeb79d29fdf08aea8327da95b1116a` |
| Manifest SHA-256 | `5c6891a9649702b1ac0327b945600acb40c571a069942517cf14ba661a3cfcbc` |
| Complete pack | 79 files, 510,311,056,155 bytes |
| Producer commit | `5b4e2f54f2b9c14ff46fb9c1efcfbcdbf37b49f1` |
| Run window | 2026-10-24 09:18:03-09:51:02 UTC |
| Execution | Eight B300 GPUs, TP4 x DP2; eager; one sequence per rank; 4096 context |
| Observed driver | 580.173.02; driver-supported CUDA API 13.0 |
| Security | GPU CC disabled; attestation not verified |
| Engine startup | 1,153,420 ms; readiness observed |
| Correctness request phase | 34,109 ms; 8/8 cases, 10/10 requests |
| Cleanup | Engine stopped; container exited 0, no OOM; removal confirmed |

The cases cover arithmetic, ordering, logic, extraction, two structured JSON
answers and two independent tool-call round trips. Their tiny outputs are **not
a throughput benchmark**. No tau, GSM8K, MMLU, sustained load, public endpoint,
TrustedRouter integration or confidential-service qualification is claimed.

## Original artifacts

The recorder's [run.json](run.json) and [output.log](output.log), plus the exact
prepared [input-contract.json](input-contract.json) and [receipt.json](receipt.json),
are copied without reformatting. The producer verifies the selected pack and
source-bound launch contract. It retains content-free case IDs, numeric usage,
timings and fixed statuses, not prompt/completion bodies or private host details.

The outer result deliberately remains `fail`, with `status=BLOCKED`,
`deployable=false`, `confidential=false` and `hardware_validated=false`.
`functional_pass=true` is the narrower successful correctness experiment.
Do not rewrite the outer qualification result or infer confidentiality from the
engine's compatibility settings.

The [offline regression](../../../scripts/test_selected_correctness_evidence.py)
pins all four original artifacts, binds their source/checkpoint identity and
replays the unchanged correctness validator from the exact producer. Mutation
cases reject missing results, broken tool turns, incorrect accounting, missing
usage and inflated acceptance. The real execution evidence is the original
receipt; offline replay is not another hardware run.
