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

## Historical producer sources

Use a full-history clone for `make check`; a normal `--single-branch` clone is
supported. Shallow clones and source archives do not contain the complete
producer history. Tests never fetch missing commits or depend on retained
feature branches, tags, local alternates or replacement refs.

Every committed `evidence/**/run.json` names this repository's exact original
producer in `git_sha`. The offline history regression requires every such
commit to be an ancestor of `HEAD`, not merely present through another ref.
Existing evidence-specific tests continue checking original source hashes and
artifact bytes. Nested vendor revisions, such as mkosi's `source.git_sha`, are
separate upstream pins, not this repository's producer commits.

The initial repair preserves 13 historical producers with an append-only
provenance merge. Its tree is byte-identical to its first parent, reviewed main
`8e7704c6279a779382c018b574c226cede8dcc0b`; the other parents are the exact
original producers. This is **provenance preservation, not code integration or
hardware validation**. No existing commit IDs, parent links, source files or
evidence bytes were rewritten, and no tags or repository settings changed.
Git documents the tree-preserving
[`ours` merge strategy](https://git-scm.com/docs/git-merge#_merge_strategies)
and [ancestor checks](https://git-scm.com/docs/git-merge-base#_operation_modes).
Do not confuse `-s ours` with conflict-resolution option `-X ours`.

Future evidence PRs must retain their actual producer ancestry. Cherry-picking
equivalent source bytes does not preserve the original commit named in a receipt.
Preservation merges must retain their parent links when integrated: squashing or
rebasing them would lose the repair. Keep the original source and receipt; do not
rewrite their hashes, bypass the regression or fetch from the network in tests.

Go code running in the confidential guest belongs under `guest/`. The dependency
test enumerates its complete import graph for Linux, including tests and the
`nibfake` build tag and both cgo modes, and rejects third-party packages even through an internal
wrapper. Operator-only code belongs under `cmd/nib` and `internal/`.

## Honest status

`make test-e2e` exercises the pinned TLS front door, fake engine/evidence and
verification failure cases in an isolated container. The release verifier must
reject that fake evidence. This software-only test is implemented and accepted
in [PR #10](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/pull/10);
it is not confidential hardware validation. The separate
[real Qwen pilot](operators/qwen-pilot.md) tests standard-TLS inference locally.
Hardware work has its own gates and committed evidence requirements in
[AGENTS.md](../AGENTS.md) and [PLAN.md](../PLAN.md).
