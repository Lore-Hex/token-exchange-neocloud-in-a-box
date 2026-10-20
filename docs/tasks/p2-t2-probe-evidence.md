# P2.T2: Read-only Inventory Recording

This sidecar adds a source-bound recorder for the existing
[hardware probe](../../host/probe.py). The shared capture supervisor now also
cleans nested command groups when its parent dies. Runtime admission is unchanged.
It does not bootstrap a host, install drivers, change GPU mode, stage
weights, run an engine, rent hardware or satisfy P2.T2's weight-integrity exit.

**No real hardware run is included.** Tests use synthetic reports and temporary
directories. Payment/account status, a listing, an SSH key or approval to test
does not prove a host's capabilities. SNP is not the Intel TDX qualification
required by the current production verifier. All existing phase gates remain.

## Operator Command

Only on an already approved Linux test host, with a trusted committed checkout,
Python 3.11+, Git and the probe's existing prerequisites available:

```sh
python3 -I host/record-probe.py
```

The command accepts no arguments, host identifiers, secrets or environment
overrides. It performs no SSH, network access, downloads or installation. Do not
run it as part of provisioning or in lieu of an explicit run approval. See the
[probe's operations and sources](../../host/README.md#read-only-inventory).

Unlike copying `probe.py` alone, recording requires the local Git object store.
The recorder resolves HEAD once, reads only the committed recorder/probe blobs,
checks regular-file modes and size bounds, and requires its bootstrap bytes to
match that commit. Raw blob reads avoid checkout/archive filters. Git replacement
refs and environment/configuration overrides are disabled. It never invokes
`git status`, hooks or checkout filters. Uncommitted probe edits, ignored modules
and bytecode cannot alter the selected input; commit intended changes first.
The operator must independently know which commit is approved.
Missing objects fail locally: `GIT_NO_LAZY_FETCH=1` disables on-demand fetching,
and an empty `GIT_ALLOW_PROTOCOL` independently denies every transport, including
configured remote helpers. See [Git's environment contract](https://git-scm.com/docs/git#_environment_variables).

The exact committed probe is materialized in a private temporary directory and
run directly as `python -I -B <snapshot>/probe.py`. A fixed launcher preserves
its 0/1 exit status while reusing the probe's bounded capture helper. The helper
is loaded directly from the same source bytes, never through a local module
import or cached bytecode. The recorder checks the emitted script digest and
snapshot bytes after execution. Temporary snapshots are removed on normal
success/failure. The installed Python, Git, NVIDIA utility, OS and local user
remain trusted: this is source binding, not attestation of execution or the host.

## Evidence And Failure

The recorder exclusively creates:

```text
evidence/p2/YYYY-MM-DD-hardware-probe-HHMMSSffffff/
  run.json
  output.log
```

The timestamp is UTC and generated locally, not a caller-supplied label. Existing
paths are never overwritten. Reservation happens before probing, so a collision
does not rerun the inventory. Directory operations are confined with Unix
directory descriptors and no-follow opens; directories must be owned by the
current user and not writable by other users. New directories/files use 0700/0600.
Stop other writers during recording. The command prints only the relative path.

`run.json` uses the unchanged `nib.evidence/v1` schema with phase P2, task T2,
`execution:software`, `hardware_validated:false`, UTC times, source commit and
both source SHA-256 values. Its host fields are fixed nonidentifying labels;
driver/CUDA are null rather than invented. `output.log` is reserialized,
allowlisted JSON, not raw child output. It contains only:

- Recognized GPU families, bounded memory counts and driver-version strings.
- A recognized CPU-vendor category and bounded total-memory count.
- Boolean device-interface presence hints, never quotes or attestations.
- Fixed check/error names, source hashes, timestamps and narrowly defined results.

There are no hostnames, IPs, UUIDs, serials, environment values, credentials or
arbitrary raw diagnostic strings. Unexpected fields/types, duplicate JSON keys,
source/time/exit inconsistencies and attempted positive qualification claims
reject the whole report. A valid failed inventory is retained. Timeout, malformed
output, source tampering or execution exceptions create a failed record with a
fixed code and no raw diagnostics. Untrusted report content is never written.

An inventory pass means only that **eight B200s were reported and the inventory
reads succeeded**. Attestation, confidentiality, topology, memory fit, engine
readiness and runtime admission remain false/unproven. The wrapper preserves
the probe's pass definition rather than treating interface presence as proof.
There is no success path into deployment or the release verifier.

The reused helper caps combined child stdout/stderr/status at 64 KiB. Its outer
deadline is 30 seconds with a bounded cleanup wait; the probe retains its own
15-second NVIDIA-command deadline. Raw stderr is counted then discarded. The
helper kills its command process group on success/failure. Each supervisor also
owns a private liveness pipe whose only writer remains in its calling process;
parent exit or outer cancellation closes that pipe and kills the nested group.
Neither timeout proves completion of
kernel-blocked I/O or attests the absence of arbitrary host processes. A cleanup
error fails the run; it never authorizes an engine launch.

Bootstrap/preflight errors occur before evidence creation and return a fixed
failure message. Interrupted or failed file writes may leave a private incomplete
directory; inspect it, never overwrite it or infer success. The sanitized output
is written before `run.json`. Review both before committing returned artifacts.
No remote upload or Git commit is performed by the recorder.

## Software Verification

```sh
python3 -I scripts/test_probe_evidence.py -v
python3 -I -m unittest discover -s scripts -p test_hardware_probe.py -v
go test -race ./host
go run ./cmd/nib lint
python3 -I scripts/check-doc-links.py
```

The new Python tests use synthetic inputs and fake capture functions, plus a
bounded local Python child for exit-status/stderr behavior. They never read real
`/proc`, invoke `nvidia-smi`, or execute a remote probe. Tests cover positive and
failed inventory, timeouts, malformed/oversized reports, privacy fields, positive
trust claims, script tampering including FIFO/symlink replacements, output
no-overwrite, unsafe directories, Git replacements/ambient settings, bootstrap
mismatch, missing promisor objects (with a local-only positive transport control),
outer cancellation of a nested command group, and cleanup failure after a valid
report. The Go test generates synthetic pass/fail records in temporary
directories and validates them with the existing authoritative evidence schema;
it adds no alternate schema or remote Go dependency. Default `make check` discovers
both test sets. Run the same `nib lint` on actual artifacts before accepting them
as inventory evidence, never as hardware qualification.
