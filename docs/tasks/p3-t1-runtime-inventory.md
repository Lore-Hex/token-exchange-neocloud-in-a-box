# Read-Only Runtime File Inventory

Status: 38 parent-serialized synthetic tests passed on 2026-10-30; final exact-head
review and CI remain pending. A read-only step is prepared in the existing CI job,
but no actual inventory collection has run for this change. This is not runtime
capability, privileged integration, installation, deployment approval or P3
completion.

## Closed Input and Output

[`inspect-runtime-inventory.py`](../../host/inspect-runtime-inventory.py) uses
only the standard library and fixed file/metadata reads. It observes:

| Input | Allowed observation |
| --- | --- |
| `/var/lib/dpkg/status` | Only `systemd` and `systemd-container`: bounded version, package-state enum and status-ok boolean |
| `/usr/lib/systemd/systemd`, `/lib/systemd/systemd` | Manager file byte count and SHA-256 |
| `/usr/bin/systemd-run`, `/bin/systemd-run` | Launcher file byte count and SHA-256 |
| `/usr/bin/systemd-nspawn`, `/bin/systemd-nspawn` | Container runtime file byte count and SHA-256 |
| `/proc/1/exe` | Stat-only correspondence: matches observed manager, different, or unknown/missing |

There is no PATH search, directory enumeration, package command, binary execution,
version invocation, subprocess, network, environment/configuration inspection,
installation, filesystem mutation, namespace/cgroup probe or fallback runtime.
Stdout is the sole output. No process IDs, paths, inode/device identifiers,
symlink text, arbitrary package names/fields, exceptions or process contents are
printed. The parser follows the existing stdlib header-parser pattern in
[`guest_package_inputs.paragraphs`](../../scripts/guest_package_inputs.py),
without importing its unrelated build/install functions. All paragraphs receive
structural and duplicate-field checks; package/architecture pairs must be unique.
Multiple records for either selected package are ambiguous, including multiarch
records. Other package fields are discarded, not dumped. Selected versions are
bounded single-line ASCII version spellings, not compared to a guessed release.

Package status and binary identity are separate observations. They do not bind a
binary to a signed package, authenticate repository provenance, identify loaded
libraries, determine a running executable's version or establish compatibility.
Missing means unavailable at the fixed observed locations, not absent everywhere.
No supported-version threshold or installed runtime is inferred from a runner
label. No operational facts or private inputs belong in this schema.

## Filesystem Boundary

Paths are opened component-by-component relative to retained directory
descriptors. Root and every directory must be root-owned and not group/world
writable. Regular inputs must be root-owned, protected, singly linked and stable;
binary files must also have an execute bit. No file is executed. Leaf symlinks,
special files, unsafe parents and changed/disappearing identities are rejected.
Final descriptor and anchored directory-entry checks include device/inode, owner,
mode, link count, size and modification/change timestamps. All ancestors remain
retained for their final checks. Reads use no-follow/nonblocking flags.

The only allowed directory symlinks are `/lib` to `usr/lib` or `/usr/lib`, and
`/bin` to `usr/bin` or `/usr/bin`. Direct directories at those locations are also
supported. All other symlinks, including `/usr` or binary leaf links, are unknown;
there is no general resolver. An allowed link is pinned using Linux
`O_PATH|O_NOFOLLOW`, checked for root ownership and a size of 1..32 bytes, then
read through the descriptor using the empty-name `readlinkat` interface exposed
by `os.readlink`. The pinned link's bytes are charged, checked against the closed
target set and rechecked. The target is traversed independently from the retained
root, never followed through the untrusted entry. Unsupported Linux interfaces
return unknown; no alternative flags or discovery are attempted.

If both fixed paths exist they must identify the same stable inode; it is hashed
once. Distinct files remain ambiguous even when their bytes match. A denied or
unsafe candidate cannot be bypassed through the other path. A missing file after
a successful stat is a change, not permission to try a different binary.

Only `/proc/1/exe` is intentionally followed by stat, from a no-follow,
protected directory chain. Its link and target metadata are checked twice while
the observed manager descriptor remains open; neither link text nor executable
contents are read. This is correspondence within the collector's visible proc
view, not host PID identity, proc-mount authentication or an atomic execution
snapshot. A deleted executable, denied lookup or changing metadata remains
unknown. All filesystem observations are point-in-time and do not exclude a
privileged writer, mount replacement or later changes. Hashes are observations,
not authenticated pins supplied by this collector.

## Bounds and Meaning

Implementation ceilings, not capacity measurements or run approvals:

- 8 MiB status content; 16 MiB per binary; 64 MiB aggregate returned content/link
  bytes, including partial, rejected and oversize-sentinel bytes.
- Reads use at most 64 KiB per call and never request beyond the remaining
  aggregate budget. Oversize stat metadata can reject before any payload read;
  that spends no payload bytes. Short reads are joined until EOF; observed file
  size and stable metadata must agree before a hash or package record is returned.
- At most 8,192 package records, 64 KiB per paragraph and 128 fields per record.
  Fixed paths have at most four parent components; alias targets cannot recurse
  into additional aliases. There is no ancestry/path discovery.
- 16 KiB serialized output; one unchanged ten-second monotonic collection
  deadline, checked through reads, parsing, descriptor cleanup and CLI completion.

The deadline is cooperative: Python cannot preempt a stalled filesystem, metadata
call, close or stdout write. No finite termination/absence claim follows. A future
CI step uses a separately reviewed one-minute outer timeout. No actual CI
collection has run at this revision's preparation.

`collection_complete` means no observation errors, not a usable runtime. Expected
missing/denied/unsafe/ambiguous observations return sanitized diagnostics; these
alone do not fail the process. Malformed input, record/read limits, exhausted byte
budget, deadline or internal failures return nonzero. There is no positive
capability/admission status. Every report, including errors, keeps all of these
false: `package_provenance_verified`, `executed_version_verified`,
`runtime_compatible`, `delegation_verified`, `kernel_integration_verified`,
`operationally_approved`.

The existing [capability collector](../../host/inspect-linux-capabilities.py),
[resource helper](../../host/probe-build-resources.py), published metadata helpers,
their pins are unchanged. This inventory cannot establish
delegated ownership, namespace isolation, effective ancestor limits, syscall
permission, confinement, terminal exits, independent absence or operational
approval. A subsequent supported-runtime plan still needs those separate facts.

## Parent Verification and Remaining Step

[`test_runtime_inventory.py`](../../scripts/test_runtime_inventory.py) uses pure
parser inputs and an in-memory descriptor table patched over the actual collector
filesystem boundary. Collection never reads the test host. Fixtures cover
duplicate/malformed/oversize records, exact aliases, hostile parents/types/links,
ambiguous candidates, replacement/disappearance, short/error/truncated reads,
shared-budget accounting, deadline/cleanup failures, PID 1 mismatch/denial,
sanitization and the unchanged resource-helper SHA-256. Static tests restrict
imports and mutation/process/network/environment access.

Synthetic `main()` coverage patches its actual function globals: `collect`,
`Linux`, monotonic time, invocation arguments/flags and stdout. These cases cannot
collect host files. They cover invocation refusal, collection exit propagation,
write exceptions/short counts, flush exceptions and the final deadline check;
every attempted JSON payload retains false authority flags. An attempted write
is not proof that stdout delivered the report.

The preliminary independent pretest review reported no blocking static issues
and requested the entrypoint coverage above as a nonblocking addition. Parent
pretest attempts were refused before any intent or container creation because
other Python work appeared. These refusals created no test container and are not
failed suites. After a fresh no-overlap check, all 38 tests passed in 0.134 seconds
on 2026-10-30. The network-disabled, read-only, nonroot test container used three
individual read-only source mounts, dropped all capabilities, disallowed privilege
escalation and had finite memory, CPU, process and time bounds. It exited with
status zero, no OOM and no remaining container PID before removal. This synthetic
result is not collection from, or validation of, a real runtime. Final exact-head
review and CI are still pending.

Parent-only focused invocation, not run by the author:

```sh
python3 -I -S -B -W error::ResourceWarning /source/scripts/test_runtime_inventory.py
```

Minimum read-only fixture layout: this collector at
`/source/host/inspect-runtime-inventory.py`, the test at the path above and the
unchanged `/source/host/probe-build-resources.py` for its hash assertion. No actual
dpkg database, system binaries, proc/cgroup mounts, credentials or private files
are fixture inputs. Existing unittest discovery can run the same suite without
test-runner changes. Preserve any failed test results separately from later runs.

Following independent scope review and the parent synthetic pass, the existing
standard CI job now has one prepared non-sudo inventory step immediately after
the Linux capability report, using its already-pinned Python and a one-minute
step timeout. No new job, runner, cache, artifact upload, installation or runtime
test is added. Normal exit status is preserved; printed JSON alone is not a
successful job outcome, and timeout or cancellation is not absence proof.

The allowlisted observations will be visible in public CI logs. They describe
that particular job after its existing setup steps, not a pristine runner image
or any operator host. Package-to-binary authenticity, loaded libraries, executed
versions, compatibility, delegation, confinement and cleanup remain unverified.
