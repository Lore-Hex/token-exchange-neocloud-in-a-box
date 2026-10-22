# CPU Parity Argv Rejection

Original unedited receipts from exact producer
`8ae250569bc73b6b79acef448584c6f129967911`, 2026-10-22
20:32:14-20:36:43 UTC, on the local Linux/amd64 OrbStack container. The diagnostic
failed; owned container/image removal and build completion are confirmed.

Both native targets compiled to the same hashes as the
[first failure](../2026-10-22-cpu-parity-readiness-failure/README.md), and the
independent first-model helper completed. The server was still running when
`scripts/tau_cpu_parity.py:203` rejected its kernel-observed argv hash. This check
occurs after a loopback listener exists and after `process_facts` succeeds,
but before executable and listener-owner validation. No HTTP parity comparison,
generation or second-model test ran.

This establishes the failed comparison, not why argv differed. No raw process
arguments or exception text were retained. The normal launch encoding already
includes its trailing NUL. Neither a startup race nor emulator rewriting is
proven, and no identity exception is justified. The next controlled route is
[native Linux/amd64](../../../docs/tasks/p2-t3-tau-cpu-native.md) with the same
strict checks, not a relaxed retry in the same environment.

The files match the originals at the operator's private run directory:

- `run.json`: `919e7f6d12a6a6624f36c2fec2a82a5f8be8cfa3b7c4ad705a092f2b1f9af929`
- `output.log`: `b42bb2d3e7d0e15b4084da69868ff1c9a168663dc60b12737301ba2b45a760de`

No tau, live admission, quality, confidentiality or public-service claim follows.
