# Contributing

Contributions use Apache-2.0 inbound = outbound. No DCO sign-off or separate
contributor agreement is required. See LICENSE and NOTICE.

Read [PLAN.md](PLAN.md), [AGENTS.md](AGENTS.md) and the threat model
(`docs/threat-model.md`, written in P0) before changing anything that runs
inside the confidential VM or touches attestation.

Rules that matter most:

- Go code that runs inside the guest uses only the standard library. A test
  enforces it.
- Never log inference bodies, headers or query strings, including in error
  paths and debug builds.
- Never guess vendor flags, kernel parameters, BIOS settings or engine
  arguments. Cite the source next to the setting, or mark it `TODO(verify)`.
- Hardware claims need committed evidence (see AGENTS.md). "Tested against
  fakes" is not "validated".
- Pin everything that ends up in a measured image or model pack by digest,
  commit or hash.

Run `make check` before opening a pull request (available once P0.T1 lands).
