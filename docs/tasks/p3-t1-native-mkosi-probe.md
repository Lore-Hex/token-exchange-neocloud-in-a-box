# P3.T1 native namespace prerequisite

The restricted Docker [sandbox run](../../evidence/p3/2026-10-21-mkosi-sandbox-denied/run.json)
started all seven tools but could not create namespaces. This separate diagnostic
tests the same pinned sandbox on a disposable native GitHub-hosted Ubuntu 22.04
x86-64 runner, without changing Docker policy or host security settings.

Run the **Native mkosi namespace probe** workflow on merged `main`. It downloads
only the public mkosi 27.1 source archive, then authenticates the entire tree
against the committed archive hash, per-file hashes and Git blob identities.
The script requires Ubuntu 22.04's system Python 3.10 and refuses root, other
platforms, tool-cache interpreters and non-hosted runners. The sandbox
receives a fresh private directory with only the verified sandbox, existing
synthetic checker and upstream licenses. Its environment contains no credentials.

The existing [command builder](../../scripts/mkosi_tools_probe.py) mounts the
runtime and payload read-only, exposes only its own `/proc/self/ns` handles
(not host process environments or descriptors), creates private temporary storage, and invokes
the [checker](../../scripts/mkosi_namespace_check.py). Success requires distinct
user, mount, network and IPC namespaces, only loopback networking, EROFS for
payload writes, and writable private temporary storage. Upstream's optional
network-unshare behavior is not accepted as proof of isolation.

The child is bounded to ten seconds and 64 KiB output; the existing process-group
runner reaps descendants. Only fixed outcomes and source hashes are retained.
Temporary payloads are removed. The workflow saves `run.json` and `result.json`
even for a failed probe. It does not retry or modify AppArmor, seccomp, sysctls,
capabilities or other host controls if namespace creation is denied.

## Scope

Implemented and covered by synthetic tests. A workflow result is required to
claim native namespace validation. The runner's Python, kernel and userspace
are **not pinned build inputs**, so a passing probe is not a reproducible build,
package installation, maintainer-hook review, ABI qualification, boot, GPU test
or confidential attestation. The offline pinned tools path stays unchanged.

Source: [`mkosi/sandbox.py` at ecf7f570](https://github.com/systemd/mkosi/blob/ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b/mkosi/sandbox.py),
`enter`, `acquire_privileges` and the documented CLI. The shared command is
unchanged for the restricted container. This native-only invocation maps a fresh
read-only `/nib` payload and restricts procfs exposure to `/proc/self/ns`.

Tests: `python -B -m unittest discover -s scripts -p 'test_mkosi*.py'`.

## First Workflow Attempt

[Run 39610247599](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39610247599)
on `efff3c789f43940e9a84ff9620551636c662ca54` failed before downloading or
executing anything: the fresh checkout had no `build` parent directory.
The setup now creates that parent. A regression test executes the actual YAML
setup shell in an empty directory with only the download replaced by a marker;
it also confirms that a non-hosted runner fails before directory creation or
download. This attempt produced no namespace evidence and is not an isolation
failure or success. The workflow must be rerun after the correction merges.

## Native Ubuntu 24.04 Result

[Run 39611644488](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39611644488)
on `f165c89fc090157b149236452ca192c674d4a684` authenticated the source and
attempted the sandbox. It **failed** with `permission-denied`; namespaces were
not validated. Process cleanup and temporary-payload removal passed. The
unaltered [evidence](../../evidence/p3/2026-10-21-native-mkosi-24-denied/run.json)
records that result. Its fixed error class does not identify the exact kernel
or AppArmor rule responsible.

The next diagnostic uses a separate Ubuntu 22.04 runner, not weaker checks on
the denied host. The [official runner inventory](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2204-Readme.md)
lists system Python 3.10.12 (checked 2026-10-21); pinned mkosi's
[requirements](https://github.com/systemd/mkosi/blob/ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b/pyproject.toml)
permit Python >=3.9. The [pinned manual](https://github.com/systemd/mkosi/blob/ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b/mkosi/resources/man/mkosi.1.md#unprivileged-user-namespaces)
describes Ubuntu's namespace restrictions; this workflow does not follow its
sysctl workaround. The future guest remains Ubuntu 24.04, and the pinned tools
container still invokes Python 3.12. A build-host probe is not a guest change.
Ubuntu 22.04 runners are entering deprecation, so this is a bounded diagnostic,
not a durable release-builder commitment.

## Native Ubuntu 22.04 Result

[Run 39614110868](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39614110868)
on `6e864bebbb123a79f063ca562bcd686b8ab5365c` passed the unchanged isolation
checks at 2026-10-21 14:07:55 UTC. The original
[evidence](../../evidence/p3/2026-10-21-native-mkosi-22-pass/run.json) confirms
distinct namespaces, the fixed checker, source hashes and cleanup. The earlier
24.04 failure remains recorded. This validates a native sandbox prerequisite,
not a guest image or a reproducible host runtime.

The next [pinned-tools probe](p3-t1-native-pinned-tools.md) replaces the host
runtime inside the sandbox with the reconstructed Ubuntu 24.04 tools tree.
