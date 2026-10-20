# Exact Interpreter Chain Diagnostic

This source-only follow-up addresses the one remaining prerequisite identified
by the [recorded Python descriptor](p0-t4-engine-python-prerequisites.md#recorded-outcome).
It does not change generic overlay admission, import paths, startup-hook rules,
packaging rules, worker selection or any of the five patch stages. The candidate
remains `BLOCKED`, `deployable:false`, `image:null`. No engine/GPU execution,
image application, ABI qualification or hardware claim is added.

## Source Basis, Not An Installed Measurement

The official [vLLM Dockerfile at source 5df63a7203cdb9328d824a27b682e77c7b9928be](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/docker/Dockerfile)
uses `update-alternatives --install` and `--set` for `/usr/bin/python3` in
`vllm-runtime-base`. The already-captured raw OCI config, SHA-256
`bd8a5d7a8774800c92d714c0c226b32c1863f929150f36efd610aebcd12a335d`,
also records those operations with `PYTHON_VERSION=3.12` in its public build
history. This is published build metadata, not verification of the installed
filesystem. No image execution or additional image download was used to inspect
that config.

The narrow diagnostic expectation is exactly:

```text
/usr/bin/python3 -> /etc/alternatives/python3 -> /usr/bin/python3.12
```

It is enabled only for the `executable` and `base_executable` observations at
`/usr/bin/python3`, in Python-descriptor mode, for the exact official
Linux/amd64 manifest
`6ee133356d49ed24cee03dd0cda3d3980279a11c107ce3e752c89e76540f41c9`
and config digest above. Unknown image/config inputs do not receive this rule.
Other roles and paths retain the generic public-path resolver. `/etc` is not
added to that resolver or to the public-path classifier.

The previous artifact intentionally omitted the unknown target and cannot prove
this chain. A new reviewed committed-source collection must establish whether
both literal links and the terminal file actually match. No such collection
has been run for this change.

## Boundaries And Results

The reader opens each ancestor no-follow from the held filesystem root. Each
of the two link nodes must be a singly linked symlink, have the exact expected
target length, and match the literal absolute target before the next node is
examined. Equivalent relative paths, normalization tricks, loops, extra hops,
ancestor symlinks and unknown targets are not accepted by this rule.

The terminal must meet the existing regular-file, single-link, byte and time
limits. Its bytes are hashed by the existing bounded reader. Both links,
ancestors and terminal identities are rechecked around hashing; replacements
with even the same target or bytes reject the observation. Identities remain
private implementation state, not emitted owner/inode data. This is bounded
observation on the read-only image, not protection against a malicious kernel
or a persistent runtime guarantee after the check.

Successful observations contain only the two fixed public link edges and the
terminal's observed hash/stat fields. Rejections retain fixed stages/reasons
(`interpreter-chain-mismatch`, `interpreter-chain-ancestor`,
`interpreter-chain-unavailable`, or the existing type/size/read/identity codes),
never arbitrary target strings or raw exceptions. They publish no accepted
hash/resolved path. The validator permits the `/etc` edge only in the exact
complete chain and scoped role; it does not generally allow private paths.

A rejected executable remains an `executable-unverified` prerequisite:
canaries are `not-run`, not failed child executions. Independent observations
remain available. A successful chain permits only the existing stdlib canary
prerequisite checks to continue; it does not qualify vLLM multiprocessing,
native code, GPU memory coherence or confidentiality. Overall diagnostic
qualification remains failure even if collection becomes complete.

## Verification

```sh
uv run --project tools --frozen python -I -m unittest discover -s scripts -p 'test_engine*.py'
```

Real filesystem fixtures cover both roles, unknown pins/modes/roles, unexpected
and private targets, dangling links, loops, extra hops, unsafe ancestors,
terminal FIFO/hardlink/size rejection, fixed diagnostics, chain/ancestor/file
replacement races, report tampering and zero child launches on rejection.
Existing real stdlib child tests require local Unix sockets; no test executes
the official image, imports vLLM or uses a GPU.

The previous layout and both prerequisite artifacts remain byte-for-byte
unchanged. Their payload regression reconstructs the old committed source,
with explicitly hash-checked bootstrap/descriptor bytes and offline Git,
instead of pretending the new policy produced historical results. The recorder
pins the new descriptor hash for all future payloads.

Independent final-head review and parent integration checks must pass before
the next approved read-only collection. No image run or production activation
is authorized by these software tests.
