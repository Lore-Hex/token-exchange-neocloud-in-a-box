# DeepSeek Functional Smoke Pass

On October 22, 2026, committed producer
`c73590ef9bba125f655fc09f53421ffadd35742e` completed all eight fixed synthetic
cases on four selected B300 GPUs within an eight-GPU Spheron lab. Ten requests
covered arithmetic, ordering, logic, extraction, two JSON outputs and two
two-turn tool interactions. The engine reached ready after 759,217 ms; the
request phase took 22,846 ms. These are this small run's measurements, not
throughput or quality benchmarks.

The original [run](../../evidence/p2/2026-10-22-b300-functional-pass/run.json)
and [output](../../evidence/p2/2026-10-22-b300-functional-pass/output.log)
are copied byte-for-byte. The container exited zero with no reported OOM,
and engine/container cleanup was confirmed. No prompt or completion is retained.

The earlier two 7/8 failures remain unchanged. The
[explicit-format fixture](p2-t3-explicit-answer-case.md) asks for lowercase;
the comparator still rejects case and punctuation mismatches. This run is
not a retroactive pass for the earlier fixture.

`functional_pass` is true, but the outer evidence deliberately remains
`result: fail`, `status: BLOCKED`, `hardware_validated: false`,
`deployable: false`, and `confidential: false`. This proves only the bounded
TP4/DP1 functional experiment. Eight-GPU DP2 serving, B200 qualification,
provider-check streaming/front-door acceptance, quality/tau testing, immutable
boot/weight integrity and complete CPU/GPU/service attestation remain pending.
