# Original Stream Preparation Failure

Producer `dc65584ba6414ec0859d648ce0b610a293dd89a6` ran once on the retained
eight-B300 lab, 2026-10-23 01:33:20-01:59:50 UTC. The four JSON documents here
are unchanged original bytes: the result envelope/report and the prepared
input contract/bundle receipt. No raw progress log or request content is included.

The exact-image application completed, and the engine reached readiness after
1,229,399 ms. The runtime observer recorded its before-request sample. The run
then failed in the request stage with both tokenizer parity and stream cohort
null. It produced no accepted throughput result. The engine stopped, the owned
container exited without OOM, and cleanup was confirmed. The original outer
result remains `fail`, with `functional_pass=false`, `deployable=false` and
`confidential=false`.

The original producer's `bundle_inputs` and `validate_result` accepted these
receipts on offline replay. Regression tests pin all original bytes, the
assembled launcher and collector source closure. This establishes provenance
and the recorded failure, not a successful benchmark or confidential service.

The old workload estimator extrapolated from empty padding and 64 filler
repetitions. A one-token boundary change between empty and nonempty filler can
move its estimate outside the fixed search radius. The new synthetic regression
uses counts 35 at zero and 36+n at positive padding: the old estimate is 1,982,
whose +/-16 neighborhood cannot reach 2,048 tokens, whereas padding 2,012 can.
The estimator now samples two nonempty lengths. It still measures and requires
an exact fit, with the same 35-call maximum per request and the same deadlines.

That reproduces a concrete preparation defect consistent with this run; the
original receipt did not retain the child exception or internal substage, so
it cannot prove that this was the only failure. A new real endpoint run must
still pass tokenizer parity and the stream cohort. Preparation now precedes
engine startup for this opt-in benchmark, avoiding startup work on a bad fixture.
Neither the normal launch profile nor the request/token/resource limits change.
