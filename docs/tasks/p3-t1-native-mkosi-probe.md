# P3.T1 native namespace prerequisite

The restricted Docker [sandbox run](../../evidence/p3/2026-10-21-mkosi-sandbox-denied/run.json)
started all seven tools but could not create namespaces. This separate diagnostic
tests the same pinned sandbox on a disposable native GitHub-hosted Ubuntu 24.04
x86-64 runner, without changing Docker policy or host security settings.

Run the **Native mkosi namespace probe** workflow on merged `main`. It downloads
only the public mkosi 27.1 source archive, then authenticates the entire tree
against the committed archive hash, per-file hashes and Git blob identities.
The script refuses root, other platforms and non-hosted runners. The sandbox
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
