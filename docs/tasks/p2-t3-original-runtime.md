# P2.T3: Official DeepSeek service runtime

Status: implementation and synthetic tests only. No running GPU, completed
migration, throughput, cache discount or confidential-computing claim.

The closed `original-long1m-graphs` execution profile binds the existing finite
service owner to the [official lab pack](p2-t3-original-lab.md). The model served
by the engine and API guard is `deepseek/deepseek-v4.1-flash-1017`, never the
selected abliterated model's ID. It requires the separately implemented
`original-deepseek-million` Go guard profile in the prepared image.

## Boundaries

- No model path, revision or upstream URL is accepted from a caller. The full
  official revision and manifest remain pinned in `deepseek_model_profile.py`.
- Preparation preserves the lab bundle, creates a new source-bound launcher
  and validates the official manifest. Host inventory expects all 83 files;
  the in-container owner hashes them before engine startup.
- `host/prepare-selected-weight-view.py --model-profile original` creates a
  separate source-preserving view using the same bounded atomic copy and
  full-hash verification. It emits a distinct v2 receipt carrying the complete
  official identity. The default selected-checkpoint v1 behavior is unchanged.
- The existing 1M runtime settings remain: 1048576 context, 393216 maximum
  output, global admission C1, 512 GiB memory, no swap, prefix caching, actual
  cached-token accounting, 3540-second request deadline and 7240-second
  cleanup reserve. These are configuration limits, not measured qualifications.
- Model identity, revision and manifest in the immutable lifecycle contract
  derive from the explicit execution profile. Readiness rejects the other
  checkpoint even if its engine reports identical context limits.
- An ordinary 24-hour or seven-day owner still needs an explicit absolute
  stop time. Restarting cannot renew it. No resource is claimed by PID alone.
- Existing embedded inference, tool, conformance and long-qualification modes
  are selected-checkpoint-specific and reject the official profile before
  preparation or child launch. Checker image composition is also rejected.
  External tests must be freshly bound to the official runtime; old evidence
  cannot be relabelled. Existing selected modes remain unchanged.

The engine arguments still derive from the same pinned
[vLLM 5df63a7](https://github.com/vllm-project/vllm/tree/5df63a7203cdb9328d824a27b682e77c7b9928be)
source checks in `deepseek_tuning.py`; no new engine flag is introduced.
Existing host preparation/build/owner commands accept the explicit execution
profile only with a matching newly prepared image. Old selected images cannot
opt into it. The optional cache-read price retains its existing validation;
this change sets no live price.

## Before Cutover

Pass independent final-head review and full CI, recover or download the official
weights and verify every hash, prepare and build a new image, and run bounded
direct readiness, streaming, tools, code-scored quality and cache tests. Keep
the old endpoint/model catalog paired until a coordinated cutover with rollback.
Use a fresh durable run identity, actual budget reconciliation and the existing
privacy/accounting gates. Do not overwrite live inputs to create staging space.

`scripts/test_original_service_runtime.py` uses synthetic owners, clocks and
source bundles to cover these boundaries. It is not a hardware receipt or
evidence of a successful deployment.
