# Current-Identity Owner Cancellation

Software-only operator helper: [cancel-selected-service.py](../../host/cancel-selected-service.py)
can identify a single running [finite owner](p2-t4-selected-finite-owner.md) even
when its original launcher did not retain a PID/start record. No live cancellation
has been validated by this increment. It does not change the owner, watchdog,
service image, network policy, runtime limits, or original absolute retirement.

## Closed Operation

The operator supplies a private, root-owned mode-0600 JSON expectation. These are
the only fields (paths and argv remain private; this is a schema description,
not a filled deployment configuration):

| Field | Required value |
| --- | --- |
| `schema` | `nib.selected-owner-cancel-input/v1` |
| `source_root` | Absolute, root-controlled checkout of the running owner |
| `source_revision` | Independently reviewed, exact 40-character Git commit |
| `executable` | Exact absolute `/proc/PID/exe` target, not a symlink alias |
| `executable_sha256` | Independently checked SHA-256 of that interpreter |
| `argv` | Complete exact ordered launch argv, including argv[0], separate `-I`, `-S`, `-B`, absolute owner script, and all original arguments |
| `state_dir` | Exact existing root-owned mode-0700 owner state directory |
| `stdout`, `stderr` | Exact existing regular log paths; may name the same combined log |

The input must be reconstructed from reviewed launch records, not automatically
trusted because an arbitrary discovered process supplies it. Only the explicit
`host/record-selected-service.py --run` shape is eligible; inspection/watchdog
commands, repeated state flags, relative source paths and duplicate JSON fields
are rejected. A missing expected command is a blocker, not permission to guess.

From a separate clean helper checkout, with a private mode-0700 output parent:

```sh
python3 -I -S -B host/cancel-selected-service.py --inspect \
  --expectation "$PRIVATE_EXPECTATION" --output-dir "$NEW_PRIVATE_INSPECTION"
# Independent operator review of that current-identity receipt precedes cancel.
python3 -I -S -B host/cancel-selected-service.py --cancel \
  --expectation "$PRIVATE_EXPECTATION" --output-dir "$NEW_PRIVATE_CANCELLATION"
```

Both commands require Linux/root, a usable procfs and pidfd support. They never
import the target checkout. They verify its HEAD and every committed Python/JSON
file under `host/` and `scripts/` against Git blob identities; unexpected import
shadows, bytecode, extensions, symlinks and file drift reject the request. The
source scan is bounded to 4,096 selected files, 64 MiB, and 16,384 directory
entries. A root-owned sticky temporary ancestor is allowed, but final source,
state, input and output objects remain root-controlled and non-writable by
other users. All path traversal rejects symlink ancestors.

Discovery reads bounded procfs cmdlines, not `pgrep` or a supplied PID. Zero or
multiple exact matches, unreadable process inventory, a non-root UID, changed
start ticks, executable bytes/inode, output log inode/UID/mode, state inode, boot
identity, or PID/user/mount/network namespace mismatch all block signaling.
The [procfs signal masks](https://man7.org/linux/man-pages/man5/proc_pid_status.5.html)
must show SIGTERM caught, unblocked, unignored and not pending, so the helper
rejects the window before the owner's cancellation handler is installed.
Appending to the original log is allowed; log content is never read or copied.
The observer binds a pidfd and repeats source/process/uniqueness checks, including
after persisting intent. There is one 60-second admission/request deadline using
the existing bounded alarm. Reused Git-child cleanup may take up to its existing
25-second reap allowance; no target signal is allowed after admission expiry.

[Linux `pidfd_open(2)`](https://man7.org/linux/man-pages/man2/pidfd_open.2.html)
binds the process descriptor; [Linux `pidfd_send_signal(2)`](https://man7.org/linux/man-pages/man2/pidfd_send_signal.2.html)
avoids signaling a recycled numeric PID. The implementation uses the matching
[Python 3.12 pidfd API](https://docs.python.org/3.12/library/signal.html#signal.pidfd_send_signal).
It sends exactly one SIGTERM through that descriptor, never numeric-PID or
process-group signals to existing processes, watchdog signals, Docker commands,
SIGKILL to the target, or retries. Git subprocesses alone use the existing
owned-child cleanup helper.
It cannot attest to the code originally loaded or defeat a malicious root
administrator changing the process between observations; this is a bounded
current-identity operation, not historical launch provenance or attestation.

## Receipt and Follow-Up

The new output directory is exclusive and private. `initial.json` is fsynced
before inspection; `signal-intent.json` is fsynced before the only target mutation;
`receipt.json` records the final closed result. Existing output is never replaced.
Receipts contain hashes and identity metadata, never argv, paths, model text,
log bodies, exception strings, credentials, or network identifiers. Keep all
three files private. Missing terminal receipt or an `unconfirmed` outcome means
the operator must preserve the ambiguity, not repeat the signal automatically.
`signal_may_have_been_sent` is set conservatively before writing intent, not
proof that the syscall ran; only `signal_sent` confirms its successful return.

Exit zero means only `identified` (inspection) or `signal-requested` (the one
pidfd syscall returned). It never means the process exited, Docker resources
were removed, a service run passed, or a soak completed. These assertions remain
false, including `original_launch_identity_proven`, in every helper receipt.
The original owner remains responsible for its existing cancellation/finally
path and independent watchdog cleanup. Preserve its untouched terminal receipt
and separately verify cleanup before starting a replacement. Never promote a
cancelled owner to natural-retirement PASS or extend the old cutoff.

Tests use real temporary files/Git blobs, synthetic procfs, mocked pidfd races,
denied signals, durable-write failure and repeated cancellation. Hosted Linux
also exercises pidfd signaling/exit against only a freshly created benign test
child. No service, Docker, GPU, network or production process is targeted.
