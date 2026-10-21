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

## Caught GPU-worker startup exception

The next [original trial](../../evidence/p2/2026-10-21-b300-worker-ready-pipe/run.json)
at `0dc31ec09895721d6b7153fe7c170c99f2911212` again verified all weights and
application stages, then failed startup after 171058 ms with cleanup confirmed.
Its [worker packet](../../evidence/p2/2026-10-21-b300-worker-ready-pipe/output.log)
matches `multiproc_executor.py` SHA-256
`948c15e09617b02e2d0746d9ee20b43168b083da71313d05f06b29a753f8570b`,
[line 815](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/v1/executor/multiproc_executor.py#L815):
the engine core sees EOF on a GPU worker's readiness pipe. That is still a
parent symptom, not proof of the GPU worker's original failure.

The same pinned source catches the underlying exception at lines 946-964,
logs it at line 953, and does not re-raise it. The lab spawn context therefore
also wraps the existing log-record factory. Only that exact module, path,
line and current source hash can feed the existing content-free observer.
The original factory runs unchanged, its record is returned unchanged, and
the previous factory is restored unless another caller replaced it. Observer
failure cannot alter logging or process outcomes. No formatter, message,
exception text, arguments or locals are inspected or persisted. This is a
diagnostic for one pinned source version, never an authorization path.

Tests cover unformattable private message/exception objects, original factory
delegation/restoration, wrong module/path/line, absent exception, source drift
and observer failure. Hardware inference remains unproven; the next fresh
bounded run must produce the underlying diagnostic or a successful result.

## Process-boundary rejection

The [bc54c085 trial](../../evidence/p2/2026-10-21-b300-process-boundary/run.json)
captured the GPU worker's caught exception. Its
[original packet](../../evidence/p2/2026-10-21-b300-process-boundary/output.log)
ends at the lab launcher's `check()` compound process-state condition, then
`reject()`: source SHA-256
`bf8f4b91de36ed6d1b948a474051eaaeb2079ad03b4d9e9e6cc2e795295cca77`,
lines 1440, 1023 and 997. All weights/application stages passed, no OOM
occurred and cleanup was confirmed. There is still no inference result.

That one source site represented eleven separate guards, so the receipt does
not identify which state changed. Each existing predicate now has its own
rejection source line. Evaluation order and rejection behavior remain the
same; no path, environment value, exception text or module representation is
exported. A test independently mutates every guard and proves all eleven
reject at distinct source sites, while unchanged state passes. This only
improves diagnosis; no upstream mutation has been admitted. A fresh bounded
trial is required to identify the exact rejected condition.

The following trial identified the changed path and its pinned CuTe compiler
source. See the [source-backed compatibility fix](p2-t3-cutlass-compiler.md).
