# Python Descriptor Prerequisite Diagnostics

This diagnostic-only follow-up to the [recorded layout descriptor](p0-t4-engine-python-descriptor.md#recorded-outcome)
does not change overlay admission, apply patches, execute vLLM, or qualify an
interpreter, ABI, GPU or confidential runtime. Candidate status remains
`BLOCKED`, `deployable:false`, `image:null`. The recorded collection remains
partial; no child canary executed.

## Recorded Outcome

Both runs used independently reviewed source
`8ceec40b867460f9273a321a16798bb36bbfe1fa`, with payload SHA-256
`2142fd5e894744adb1f92749f0a9d61f99615ee90562c64e22726f685592ac8b`.
Both original artifacts are preserved unchanged:

- [First run metadata](../../evidence/p0/2026-10-20-official-vllm-python-prerequisites/run.json)
  and [output](../../evidence/p0/2026-10-20-official-vllm-python-prerequisites/output.log):
  01:33:51 to 01:34:01 UTC on 2026-10-20, `result:fail`,
  `stage:raw-oci-metadata`, `cleanup:not-created`. The parent reports that input
  metadata had not been copied because the ignored build directory was absent.
  This is a setup failure, with no container attempt or image observation.
- [Second run metadata](../../evidence/p0/2026-10-20-official-vllm-python-prerequisites-2/run.json)
  and [output](../../evidence/p0/2026-10-20-official-vllm-python-prerequisites-2/output.log):
  01:34:13 to 01:35:37 UTC, exit 1, `result:fail`, `collection:partial`,
  `overlay_admission:not-evaluated`, `cleanup:confirmed`. The parent separately
  confirmed an empty owned-container listing. The cached image remains.

The second run identifies both executable observations at `/usr/bin/python3`
as `resolution/unsafe-target`, with an observed symlink mode `0777`, size 25,
link count 1. The target is intentionally omitted; no executable hash or resolved
path is accepted. **The artifact does not establish the exact symlink target or
whether it uses an alternatives mechanism.** No path exception is added here.

All other required file reads and all 14 pinned worker-source hashes pass.
The optional nonselected `/usr/lib/python3.12/dist-packages` root is now correctly
classified `confirmed-absent`; all required scans pass. Packaging observations
are unchanged: exact entrypoints, 2,879 source-verified planned removals,
48,909,952 bytes, plan SHA-256
`210a50805b36797d06e1d17425f6eacba6f90f1e04aaa7c0560692c05ffb3b3f`.
All five patch stages are prepared in host memory only.

The sole canary blocker is `executable-unverified`. Both attempts are explicitly
`not-run`, with null path digest and individual results. This is not evidence
that children ran or failed. Raw OCI linkage passed in the second run; local
config/layer bytes were not rehashed. Neither run qualifies ABI, GPU, engine
launch, confidentiality or hardware. A source-backed resolution-policy change
requires a separate reviewed task before any new collection.

## File Observations

The `nib.engine-python-descriptor/v2` report adds fixed file rejection stages
and reasons for resolution, stat, type, link count, size budget, read and identity
checks. An already-observed stat contributes only type, mode, size and link
count; unknown values are null. No inode, owner, exception text or arbitrary
symlink target is emitted. A rejected file has no accepted hash or resolved path.

The requirements are unchanged: anchored no-follow resolution, bounded explicit
symlink traversal, regular files with one link, per-file and cumulative read
limits, bounded reads and final identity checks. The recorded `/usr/bin/python3`
rejection is **not resolved**: its fixed stage and safe symlink metadata are now
known, but its target and any policy correction remain unverified. A fixed `read-rejected` can still
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

The separate [exact interpreter-chain diagnostic](p0-t4-engine-python-chain.md)
tests the source-backed alternatives hypothesis without changing this recorded
result or generic path/admission policy. It is not an installed-chain measurement.
