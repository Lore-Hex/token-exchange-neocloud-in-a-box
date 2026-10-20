# Python Descriptor Prerequisite Diagnostics

This diagnostic-only follow-up to the [recorded layout descriptor](p0-t4-engine-python-descriptor.md#recorded-outcome)
does not change overlay admission, apply patches, execute vLLM, or qualify an
interpreter, ABI, GPU or confidential runtime. Candidate status remains
`BLOCKED`, `deployable:false`, `image:null`. No changed-source image run has
been performed for this task.

## File Observations

The `nib.engine-python-descriptor/v2` report adds fixed file rejection stages
and reasons for resolution, stat, type, link count, size budget, read and identity
checks. An already-observed stat contributes only type, mode, size and link
count; unknown values are null. No inode, owner, exception text or arbitrary
symlink target is emitted. A rejected file has no accepted hash or resolved path.

The requirements are unchanged: anchored no-follow resolution, bounded explicit
symlink traversal, regular files with one link, per-file and cumulative read
limits, bounded reads and final identity checks. The prior `/usr/bin/python3`
rejection is **not resolved or explained by this implementation**. Its next
approved observation should identify the failing stage and safely available
metadata instead of discarding those facts. A fixed `read-rejected` can still
represent more than one underlying reader invariant; it is not an invented
root-cause claim.

## Optional Roots

A measured site-root path may be irrelevant to the proposed canary paths only
when it is confirmed absent under a stable anchored parent with two no-follow
misses, without a followed link, and is neither a required path nor an ancestor
of one. Successful scan aliases are checked too. This is role/path based: no
numeric `site_root_N` is privileged. The absent observation remains in the report.

Missing selected, stdlib, dynload or CUTLASS paths still block. Dangling links,
missing ancestors, permission failures, unstable absence and contradictory
required aliases do not become optional absence. Existing populated alternate
roots are still scanned, not admitted to the proposed import path or deleted.
Startup hooks remain inert in the unchanged isolated canary; no admission
exception for hooks or alternate roots is added.

## Canary Outcomes

Before any execution the producer reports a sorted bounded list of **all**
prerequisite blockers: runtime/flags, rejected locations, path/cache policy,
required scans, interpreter-file verification and payload/process-helper
availability. The validator independently recomputes that list.

Blocked canaries explicitly report `not-run`, with null path digest and child
results. Otherwise, fresh and child-group attempts each report pass or failed
with fixed reasons (launch/I/O, timeout, nonzero exit, rejected execution or
malformed output). A successful fresh result survives a failed child group,
and vice versa. A group failure does not invent failures or successes for its
individual fork/spawn/grandchild/forkserver members. Output is published only
after validating that entire group's fixed contract.

The unchanged synthetic canary uses `-I -S -B`, fixed paths and stdlib-only
dispatch; this is not real vLLM worker qualification. Existing 45-second/8-KiB
per-group limits, container limits and owned-container cleanup remain in force.
The descriptor stays partial when a required observation/attempt fails; even a
complete collection records overall qualification failure, not runtime readiness.

## Verification And History

```sh
uv run --project tools --frozen python -I -m unittest discover -s scripts -p 'test_engine*.py'
```

Regressions exercise real synthetic files, FIFO/hardlink/link/size rejection,
injected syscall and identity failures, sanitized exceptions, optional/required
absence, prerequisite no-execution, attempt failures/timeouts/malformed output,
preserved independent successes and report tampering. Real host stdlib child
tests require local Unix socket permission, not an image or GPU.

The original v1 [run metadata](../../evidence/p0/2026-10-20-official-vllm-python-layout/run.json)
and [output](../../evidence/p0/2026-10-20-official-vllm-python-layout/output.log)
remain byte-for-byte unchanged. Their regression loads the exact hash-checked
reviewed v1 descriptor from local Git history, with lazy fetch and transports
disabled; current production validation accepts only v2. No old evidence is
rewritten to claim the new diagnostics were measured.

Independent final-source review and parent validation must precede a new approved
read-only image collection. No image run, application or production activation
is authorized by passing these software tests.
