# CPU-Only Parity Retry

Original CPU-only result from producer
`9eeebe1a56e2980929b8b3f637f8d3189e5bc797`, recorded
2026-10-24 18:13:08-18:16:29 UTC:

- Outer and runtime result: `pass`, stage `complete`.
- Qwen3 0.6B and 1.7B each passed all four fixed tokenizer/usage cases, with
  matching local/server token hashes and counts. Readiness was 467/566 ms.
- CPU container, image and build-completion cleanup are all `confirmed`.
- Server/helper binary hashes are exactly the same as the original
  [18:00 failed attempt](../2026-10-24-selected-tau-readiness-failure-1800/cpu/output.log).
  The new committed command bounds both compute and HTTP threads to four;
  context remains 4096, parallelism one, output cap 256, with GPU offload off.

This is an offline CPU prerequisite, not a held owner-pair admission, selected
DeepSeek trial, full tau score, original-revision quality comparison, or CC
validation. It does not establish a precise root cause for the earlier failure
or retroactively confirm that run's GPU/controller cleanup. The original
hardware-validation, quality, live-admission and confidentiality flags stay
false. No private deployment name or address is added.

Byte-exact originals: [run.json](run.json), [output.log](output.log).
[Regressions](../../../scripts/test_cpu_http_threads_evidence.py) bind immutable
hashes, retained producer ancestry and all 27 original source files; use the
historical validator for the eight cases and rejection checks; verify original
command limits; and join the two measured binary hashes to the failed attempt.
