# Selected Checkpoint: 8K Profile Parity

Measured 2026-10-24 12:55:30-13:14:34 UTC by committed producer
`68ab473fda5b29bf85be07099494702b63aeba69` on eight ordinary B300 GPUs,
with CC disabled. Selected checkpoint:
`dealignai/DeepSeek-V4.1-Flash-1017-UNCENSORED-FP8`, revision
`c19ca5ca4eaeb79d29fdf08aea8327da95b1116a`.

- Fixed `tau8k-eager` profile: TP4 x DP2, 8,192 context, one sequence,
  512 GiB process memory limit, no swap.
- All four unchanged tokenizer cases passed: text, system, tools and tool
  continuation, eight requests total. Local/server token counts and generated
  input usage agree: 12, 16, 275 and 332 tokens respectively.
- Engine readiness took 836,135 ms; parity took 12,830 ms. Readiness time is
  not request latency. Source-bound API runtime identity was stable before
  and after the requests. Engine stopped, cleanup confirmed, no OOM.

The profile's 8K limit is verified, not full 8K utilization or arbitrary
request fit. This is a fixed tokenizer prerequisite, not live tau admission,
a tau score, general tokenizer equivalence, quality or confidentiality.
The original outer `result: fail`, `status: BLOCKED`, `hardware_validated: false`,
`deployable: false` and `confidential: false` remain unchanged despite the
functional prerequisite passing.

[run.json](run.json), [output.log](output.log), [receipt.json](receipt.json) and
[input-contract.json](input-contract.json) preserve the original bytes; only
`payload/lab.json` was renamed to `input-contract.json`.
[Regression tests](../../../scripts/test_selected_operational_evidence.py) pin
all four hashes, join the receipt, contract and producer source closure, and
validate the measurements with the original producer's validators.
