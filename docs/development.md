# Development

This repository is Apache-2.0 licensed. Model weights, NVIDIA software and
third-party dependencies keep their own licenses. Nothing here grants a license
to operate NVIDIA confidential-computing services commercially.

## Local checks

Install Go 1.27.2, Python 3.11 or later, uv, shellcheck and OpenSSL. Go's
toolchain selection downloads the version in `go.mod` when necessary. Versions
were checked against [Go releases](https://go.dev/dl/) and upstream package
releases on 2026-10-19. Checks run on Linux in CI and support local macOS use.

```sh
make check
make build
./build/nib --help
```

Go lint tools live in `tools/go.mod` and `tools/go.sum`. Python lint and Markdown
parsing dependencies live in `tools/uv.lock`. Neither is a guest dependency.
GitHub Actions are pinned by commit; workflows have read-only repository access
and do not publish artifacts, deploy, provision hardware or consume inference
credits. The first run downloads development tools.

`make check` runs formatting, vet, staticcheck, shellcheck, yamllint, local
Markdown link and heading checks, race-enabled Go tests, gitleaks on both the
working tree and full fetched Git history, and a secret
scanner positive control. Remote documentation URLs are citations, not an
availability check; verify their contents when using a vendor setting.

The secret-scanner positive control creates a nonfunctional token in a temporary
directory, requires the scanner's explicit leak exit code, deletes the fixture,
and requires the cleaned directory to pass. A separate temporary Git repository
proves a token removed in a later commit is still detected in history. Neither
test prints the token.

Go code running in the confidential guest belongs under `guest/`. The dependency
test enumerates its complete import graph for Linux, including tests and the
`nibfake` build tag and both cgo modes, and rejects third-party packages even through an internal
wrapper. Operator-only code belongs under `cmd/nib` and `internal/`.

## Honest status

P0 is repository preparation, not a confidential inference implementation.
`make test-e2e` deliberately exits unsuccessfully until P1 supplies the fake
engine, real shim integration and verification tests. A placeholder must never
produce a green end-to-end result. Hardware work has its own gates and committed
evidence requirements in [AGENTS.md](../AGENTS.md) and [PLAN.md](../PLAN.md).
