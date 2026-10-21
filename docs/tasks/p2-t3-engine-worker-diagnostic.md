# P2.T3 bounded engine-worker diagnostic

The [B300 trial](../../evidence/p2/2026-10-21-b300-engine-core-exit/run.json)
at `70f3d65ee6a966d5f73370577288045901cb6f09` passed complete weight verification
and all five application stages. Unlike the earlier ModelConfig rejection, it
reached engine-core startup and exited after 162064 ms. The original
[typed output](../../evidence/p2/2026-10-21-b300-engine-core-exit/output.log)
records no OOM kill, stopped engine and confirmed container cleanup. No inference
or quality result was produced.

The final source hash is
`8740c9af331597573d59941d3d65a58dbfd93c92f0144fd187b44bb25a6e4a2e`,
matching pinned vLLM
[`v1/engine/utils.py:1431`](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/v1/engine/utils.py#L1431).
This proves the parent detected a failed core process, not the worker's root
cause. The earlier registry adapter got past ModelConfig; it did not establish
working inference.

The lab-only spawn dispatcher now observes exceptions at `BaseProcess.run`,
before multiprocessing catches and formats them. It re-raises the same exception
and restores the original method. Success and observer failure do not change
target execution. Reentry failures are observed through the same callback.

Only the first fixed-size binary exception packet is exclusively created in
the trial's fresh private cache. It contains the existing category and at most
eight source hash/line pairs. No exception text, arguments, locals, input,
output or stderr is stored. The parent uses the existing anchored no-follow
regular-file reader, rejects oversized/partial/hardlinked packets, and reads
only after the serving process exits. This diagnostic is never attestation or
admission evidence. Older receipts without the new optional field remain valid;
unknown fields still fail closed.

Verification: `python -m unittest discover -s scripts -p test_deepseek_lab.py`
includes a real isolated multiprocessing failure, unchanged error identity,
observer-failure containment, success without a diagnostic, first-writer
behavior, fixed size, and symlink/hardlink/partial/oversize rejection.
`make check` remains required before merging. A new hardware run is required to
identify the actual worker failure; no successful runtime is claimed here.
