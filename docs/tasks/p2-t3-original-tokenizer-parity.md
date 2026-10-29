# P2.T3: Official tokenizer parity identity

Status: software receipt support, not serving-side parity evidence, a live
runner or operational admission. This change does not rebuild an image, modify
an existing service, expose `/tokenize`, or change any public API.

The separate external comparison helper `original_tokenizer_parity` requires an explicit
`execution_profile="original-long1m-graphs"` only with the actual configured
`max_model_len=1048576` and canonical model
`deepseek/deepseek-v4.1-flash-1017`. Its new v3 receipt binds those values,
the official revision and manifest in
[the closed model profiles](../../scripts/deepseek_model_profile.py), and the
existing source/tokenizer/probe binding, reusing the frozen lab helper's common
functions. The original `engine_tokenizer_parity` source, its archive-verifier
dependency pins, and v1 default and v2 8K receipts remain byte-identical. The
external comparison loop and receipt validator are versioned separately to
preserve that frozen dependency graph. Neither legacy receipts nor a smaller reported context
can qualify as an official-profile result.

The [existing source audit and comparison method](p2-t3-tokenizer-parity.md)
are unchanged: four fixed non-thinking probes, at most eight serial requests,
one generated token per generation, no retries, 30 seconds per request and
300 seconds overall, subordinate to the caller's existing deadline. Actual
token IDs are compared by hash and count, followed by generated prompt usage.
The local reference's 1,024-token probe bound is not the service context limit.
Identical tokenizer file hashes permit reference reuse, not historical result
reuse or a claim that the whole weight pack is identical.

An eventual external caller must independently verify the deployed official
pack, runtime source and engine identity; bind its own verifier source separately
from the already-built image; use a reviewed transport to the exact serving
network namespace; and preserve admission, accounting, immutable deadlines,
durable intents and uncertain exits. A matching receipt is not that proof.
The existing lab lifecycle starts and stops an engine and is not an external
service adapter. The legacy long-qualification entry remains closed to the
original profile. Do not substitute that lifecycle for the missing caller.

Fresh authenticated transport, the external caller and its independent review,
deployed identity, real parity, conformance, quality and cache measurements
remain unfinished. Even a real eight-request pass would establish only these
small framing probes and reported configured context, not a 1M-token execution,
full benchmark, confidential computing or competitive pricing.

Focused synthetic checks (no model, external API or GPU execution):

```sh
python3 -B -m unittest discover -s scripts -p test_original_tokenizer_parity.py
python3 -B -m unittest discover -s scripts -p test_engine_tokenizer_parity.py
```
