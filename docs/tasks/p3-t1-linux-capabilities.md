# Read-Only Linux Capability Report

Status: parent synthetic tests pass; independent final review and CI pending.
This is a sanitized capability inventory, not a privileged integration test,
delegation proof, deployment gate, capacity admission or P3 completion.

## Scope

[`host/inspect-linux-capabilities.py`](../../host/inspect-linux-capabilities.py)
uses only the standard library. A single non-sudo step in the existing
[`check` job](../../.github/workflows/check.yml) uses its already-created pinned
Python interpreter after the checker setup. There is no additional job, runner
allocation, dependency installation, cache or artifact upload. Existing triggers,
permissions and other checks are unchanged. The report goes only to stdout.

No private source, operator identity, target, receipt, approval, credentials or
model/image data is an input. There are no subprocesses, network calls, Docker
access, environment reads, filesystem writes, cgroup changes, namespace setup,
PID signalling or process enumeration. The collector must run with `-I -S -B`.
It does not invoke or alter the existing resource probe or any pinned helper.

## Exact Read Boundary

- Process facts: Linux/architecture enum, real/effective-root booleans and Python
  availability of pidfd and Unix peer/packet interfaces. No hostname or username.
- Current process `mountinfo`, `cgroup`, `status`, `uid_map`: emit only the cgroup
  mount/membership classifications, `NoNewPrivs`, `Seccomp`, normalized `CapEff`
  and root-mapping classification. Raw mount sources, paths and UID mappings are
  never printed. `/proc/self` is addressed using the collector's own PID through
  no-follow directory descriptors, not a general magic-link resolver.
- Only `cgroup`, `mnt`, `pid`, `user`, `time` namespace handles for this process
  and PID 1 are intentionally followed by stat. Output is same/different/unknown,
  never device/inode values. A relation to PID 1 is not host-isolation proof.
- `/sys/fs/cgroup` root ownership/mode and `cgroup.controllers`,
  `cgroup.subtree_control`, `cgroup.type`, `cgroup.procs`. Membership emits only an
  emptiness boolean, never PIDs; controller output includes only CPU/memory/PIDs.
- For an unambiguous cgroup2 mount rooted at `/`, a canonical restricted current
  membership path can map to visible ancestors. Each is opened descriptor-relative
  without following filesystem symlinks. Any mount below `/sys/fs/cgroup` is
  rejected conservatively, including directory, file and same-filesystem bind
  mounts, even when outside the requested ancestry. Only `memory.max`, `memory.swap.max`,
  `memory.oom.group`, `pids.max`, `cpu.max`, `memory.peak`, selected numeric OOM
  events in `memory.events`, and regular-file presence of `cgroup.kill` are read.
  There is no sibling scan. Arbitrary subtree mount roots, traversal, unsupported
  component spellings and more than 16 visible levels return unknown; no prefix
  stripping, fallback path or guessed namespace correspondence is used.
- Only `/proc/<own-pid>/fdinfo/<owned-open-fd>` adds an identity read: the descriptor
  must be currently retained by this collector. Only its positive, unique `mnt_id`
  is used internally; raw fdinfo, descriptor numbers, mount IDs and inodes are
  never emitted. The root descriptor is matched to the parsed mount ID and held
  through collection. Every opened descendant directory and leaf must have that
  same mount ID before any cgroup contents are read. Device equality alone is
  never sufficient. The mount-ID pattern comes from the existing
  [`mount_id`](../../host/inspect-public-weight-cache.py) helper; it is adapted to
  the collector's shared byte budget instead of importing or changing that helper.
  A final fresh root open and mountinfo read must still match before releasing
  the retained root. Missing, denied or mismatched identities leave mapping unknown.

The parser decodes the four mountinfo escapes for space, tab, newline and
backslash before classification. Parsed path values remain internal. Missing
files mean unavailable at the observed location, not kernel-wide absence.
Hidden ancestors remain unknown; visible `max` never implies unlimited effective
capacity. No raw exception, mount line, process ID, namespace ID or path is output.
These are separate read-only observations, not an atomic snapshot or proof of
writer exclusion. The collector's stdin is not an input or transport protocol.
The `cgroup.kill` presence check opens read-only/nonblocking and validates its
descriptor without reading or writing its contents. A denied read-only open is
unknown, not an invitation to open for write, use a fallback flag or claim absence.

## Bounds and Interpretation

Implementation ceilings: 4 KiB per small file, 1 MiB mountinfo, 2 MiB aggregate
bytes, 16 visible ancestor levels, and 64 KiB serialized output. Aggregate
accounting includes rejected, partial and truncation-sentinel bytes. Reads stop
at the remaining budget. Malformed or truncated input cannot become positive
capability evidence. Pure parser/injected-I/O tests exercise these boundaries.
Every fdinfo read has the same 4 KiB ceiling plus one charged truncation sentinel;
both mountinfo snapshots have the 1 MiB ceiling and share the 2 MiB total with all
other reads. Descriptor identity is charged before calculating payload capacity.
Repeated ancestor checks or a large mount table can exhaust this budget; the
collector does not expand it or return a supported mapping on incomplete reads.

The ten-second monotonic deadline is cooperative, checked between reads and after
descriptor cleanup; Python cannot interrupt a stalled filesystem call. The
one-minute CI step timeout is a separate backstop, not proof of termination or
owned absence. This step launches no worker or descendant to supervise.

The report has `current_helper_preconditions` equal to `blocked`, `unknown`, or
`not_disproved`. Non-root is an expected blocked current context and does not
fail collection or create a new deployment gate. Timeout/incomplete observations
never produce `not_disproved`. Exit zero means only report generation, including
expected missing/denied/unsupported observations; malformed, truncated, exhausted
byte budget, timeout or internal collection failures are nonzero. Depth-limited
or unsupported mappings remain explicit unknown diagnostics, not guessed reads.

Even a complete synthetic or actual report retains `delegation_verified=false`,
`kernel_integration_verified=false`, `operationally_approved=false` and incomplete
effective-limit knowledge. Root/mode/controller observations cannot establish
write authority, namespace creation, pidfd syscall permission, child confinement,
actual `0::/<group>` membership, terminal exit or cleanup. The unchanged
[resource helper](../../host/probe-build-resources.py) requires Linux x86_64 root,
one writable cgroup-v2 mount and pre-enabled CPU/memory/PID controllers. This
collector neither enables them nor authorizes a subsequent privileged test.

## Sources and Accounting

Source review date: **2026-10-29**. The owner supplied verified current public
repository status and these official GitHub source facts for this change; the
author did not make a network request to re-fetch them:

- [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions):
  standard GitHub-hosted runner compute is free for public repositories.
- [GitHub-hosted runners](https://docs.github.com/en/actions/reference/runners/github-hosted-runners):
  `ubuntu-24.04` is a standard public x64 runner label.

That is a compute-only billing fact for this public standard job, not a claim
about other runner classes, storage, aggregate operational accounting, approved
spend or available capacity. No billing/settings changes or storage uploads are
introduced. The label does not prove actual kernel capabilities, root access,
delegation or isolation. Linux interface reference:
[cgroup v2](https://docs.kernel.org/admin-guide/cgroup-v2.html). The implementation
interprets observations conservatively and does not infer support from a kernel
version string.

## Parent Verification

Only synthetic input and injected filesystem/time boundaries are exercised by
[`scripts/test_linux_capabilities.py`](../../scripts/test_linux_capabilities.py).
Existing unittest discovery includes it without test-runner changes:

```sh
python -B -W error::ResourceWarning -m unittest discover -s scripts -p 'test_linux_capabilities.py'
```

Cases cover escaped fields, ambiguous mounts, subtree refusal, traversal/depth,
partial/truncated reads, aggregate-budget exhaustion, timeout, non-root diagnostics,
namespace relations, fixed output and secret-string non-disclosure. New synthetic
descriptor-table tests exercise directory/leaf overmounts, same-device bind mounts,
every accepted handle's mount ID, retained-root lifetime, final table/root changes,
unreadable fdinfo and identity reads consuming the remaining payload budget. No test reads
the test host's proc/cgroup state. On 2026-10-30 the parent ran the changed suite
in an offline, read-only, non-root CPU container with only the collector and
test file mounted read-only, all capabilities dropped, no-new-privileges,
2 CPUs, 1 GiB memory, 128 PIDs and a 60-second command timeout:

```sh
python3 -I -S -B -W error::ResourceWarning /source/scripts/test_linux_capabilities.py
```

All 29 synthetic tests passed in 0.027 seconds. The container exited zero,
with no OOM and no remaining process, before removal. Exact-head independent
review and CI remain pending; no actual collector or kernel observation is
claimed by this task note.

Review history: the preliminary exact-file review was **BLOCKED** by a P2:
the prior parser discarded mounts below the cgroup root, and no-follow traversal
alone did not prevent mount crossings. This candidate addresses that finding
with the correspondence checks above; acceptance is pending new independent
review. Two initial pretest checks rejected before container creation; these
are not test results. The first actual synthetic run executed 29 tests in
0.029 seconds and failed one oversized-input subtest because its expected byte
sum included the prior subtest's call history. The repaired fixture starts each
subtest independently and checks one status read; the exact aggregate-byte
equality assertion is unchanged. The failed result is preserved separately
from the later passing run, not combined into a larger test count. The author
has not run tests, imported/compiled/executed the collector, or changed the
workflow as part of the corrections. The parent has not run the real collector.
