# P2.T4: Selected-Trial GPU Owner

Implemented and synthetic-tested library, **not hardware validated**. This is
the candidate half of the [process-owner transport](p2-t4-tau-owner-channel.md),
not a complete tau launcher or a score. No existing run selects it automatically.

The adapter accepts only the selected checkpoint's ordinary 8xB300, TP4/DP2,
8K eager lab profile, with actual tokenizer parity and runtime observation.
It operates beside the already owned engine process: the current child PID,
start time, namespaces, executable, arguments and listening socket are checked
before publishing readiness and before each request. Callers must authenticate
the lab contract and source bundle before attachment. A structurally valid
readiness document alone does not authorize a runtime.

Readiness binds the original contract digest, process identity, before-request
observation and measured parity result. The published expiry is at most 960
seconds and never renewed. The controller uses the existing shared resource
budget; the adapter adds no second allowance or retries. Count/complete pairing,
model and context limits, request identity and exact finish acknowledgements
use the same transport as the CPU owner. At most 32 completions are allowed.

`tau_gpu_sources.py` compiles a fixed, digest-checked source closure without
ambient application imports or bytecode. Shared modules have one identity,
including exception classes. The outer lab remains responsible for stopping
the engine, checking post-request identity and collecting final cleanup evidence.
An acknowledgement is not a successful cleanup or a quality qualification.

Verification: `test_tau_gpu_owner.py` exercises private OS pipes, real owned
subprocess lifetimes, synthetic kernel/HTTP observations, expiry and readiness
tampering, exact source hashes and poisoned ambient modules. These tests use no
GPU and make no inference-quality or confidential-compute claim.

The lab now has a separate `--tau-owner` preparation flag. It requires the
ordinary selected-checkpoint DP2 target, `--execution-profile tau8k-eager`,
`--tokenizer-parity`, `--tokenizer-materialization` and `--runtime-observation`.
Provider checks and stream cohorts cannot share this mode. The resulting bundle
keeps a network-disabled, 512 GiB, no-extra-swap container; its only additional
channel is private FIFO stdin/stdout. A terminal is not a controller.

After measured parity, the worker serves the bounded owner protocol. The host
checks original readiness before relaying it, then joins the exact finish status,
request count, parity and initial runtime sample to the final lab JSON. Known
count/complete failures preserve that final JSON without reporting a successful
release. The common relay is shared with the CPU owner. Existing lab teardown
and the absolute-deadline watchdog still own container cleanup.

`test_tau_gpu_lab.py` covers opt-in selection, unchanged default payloads,
source tampering, constrained container arguments, actual supervisor pipe
exchanges, deadline reaping and final-report joins. These are synthetic tests.

Still required: joined CPU/GPU/scorer launch and teardown, fresh selected-weight
execution and committed hardware receipts. The production provider and
confidential-admission paths are unchanged.
