# CPU Entry After The Pattern-Matcher Adapter

**Failed. No inference or confidentiality qualification.**

One bounded local CPU-only run used independently reviewed source
`732fe9fd77e5e2b877fdfa04ac18547d4e437f25` after its final CI passed.
The original recorder outputs are preserved without modification:

| File | SHA256 |
| --- | --- |
| `run.json` | `a45e523ef0bef472c6ade2878019aa8a6ce237f84192d51c0b7547cfa5edf6fa` |
| `output.log` | `d0bc25522652f61e63e258bef3c47688400d88cbc8cf68948c372043b7874035` |

The exact cached image passed preflight and all five application stages.
The child exited 1 at checkpoint 11, before importing `ServeSubcommand`
completed. Its final observation again reports `finders=true`; the other nine
process-state mismatch bits are false. Preflight took 8,490 ms, application
497,815 ms, and entry 12,527 ms. Cleanup was confirmed by the recorder and an
independent post-run container listing.

The [source-bound pattern adapter](../../../docs/tasks/p2-t3-pattern-matcher-compat.md)
fixes a demonstrated upstream compatibility conflict, but this run shows it
does **not resolve the observed startup failure**. The finite observation does
not identify which finder changed, whether the adapter was reached, or the
first rejection. Do not infer that the earlier GPU failure has the same cause.
The next step is a narrowly bounded diagnostic, not broader finder permission.

No model weights, GPU devices, credentials, network, ports, serving call or paid
resource were used. `entry_pass`, `inference_started`, `hardware_validated`,
`deployable` and `confidential` remain false. The prior failed run remains
[preserved separately](../2026-10-20-cpu-entry-125036c/README.md).
