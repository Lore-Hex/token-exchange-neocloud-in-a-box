# Original Cohort Window Exhaustion

Producer `8d8d6167285dfe40b88d23fb813818f8f2b95222` ran once on the retained
eight-B300 lab from 2026-10-23 02:39:18 to 03:08:44 UTC. The four JSON files
here preserve the original run, report, prepared contract and bundle receipt
byte-for-byte. No prompts, completions, token IDs or private progress log are
included. Both the original producer's offline `bundle_inputs`/`validate_result`
and an independent replay accepted the provenance and result.

The corrected workload preparation succeeded: all nine requests were measured
at exactly 2,048 input tokens, with 2,012 padding repetitions. The engine became
ready after 1,213,356 ms. All four tokenizer parity cases passed, using eight
requests, before the stream cohort started.

The warmup completed 400 output tokens in 43.992 seconds. Three measured
responses each completed 400 tokens in 37.982, 37.807 and 38.016 seconds. Their
TTFT p50 was 0.196 seconds and p95 was 0.584 seconds, from only three completed
samples. These are partial observations, not a full benchmark or capacity claim.

The fourth measured request timed out after 22.203 seconds when the entire
cohort reached its fixed 180-second window. It had 235 output events but no
terminal usage; its token count remains unknown. Four scheduled requests never
started. The original stop reason remains `request-failed`; measured scheduled
noncompletion is 5/8, and started-request failure is 1/4. The 1,200 completed
measured output tokens divided by the full 136.009-second measured wall and
eight allocated GPUs yields 1.103 completed tokens/second/GPU. That denominator
includes the failed attempt, not just successful generation time.

The window was too short for this observed sequential workload. **Estimate:**
43.992 seconds of warmup plus eight times 37.935 seconds (mean completed
measured duration) is about 347.5 seconds, before variation. A separately named
600-second cohort option can accommodate the existing nine 60-second request
caps plus 60 seconds of control overhead. It does not guarantee completion or
retry failures, and an earlier owner deadline still wins. The default remains
180 seconds. No token, request, context, GPU, isolation or cleanup limit changes.

Terminal engine identity matched. Both before/after runtime observations were
recorded. The engine stopped, container exited zero without OOM and cleanup was
confirmed. The parent separately observed the exact owned container and PID
absent at 03:09:19 UTC. The original outer result remains `fail`/`BLOCKED`, with
`functional_pass=false`, `deployable=false`, `confidential=false` and no quality,
tau or full-sweep acceptance. The host remains available for later approved
tests; no VM, cache or SSH-key teardown occurred.
