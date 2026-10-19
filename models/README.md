# Model packs

DS1017 and GLM-5.3 now have pinned revisions, complete file manifests and
source-backed plain-only draft profiles; see [verification and limits](SOURCES.md).
Model licenses are separate from this repository's Apache-2.0
license. No weights are distributed here.

## Stage pinned weights

Start with an offline plan. This only reads the trusted pack and manifest:

```sh
nib model stage models/deepseek-v4.1-flash-1017/1
```

The current DeepSeek pack contains **83 files, 510,311,045,295 bytes**. The
minimum transfer budget is **510,311,045,378 bytes**, allowing one overflow
detection byte per file. Budget counts consumed successful-response body bytes,
not TLS overhead, HTTP headers, kernel buffering or total wire traffic.
Planning neither allocates storage nor authorizes a download or cloud expense.

After the host, storage, network transfer and run budget are approved, on Linux
or macOS with a local POSIX filesystem:

```sh
nib model stage --download --max-bytes 510311045378 --timeout 12h \
  models/deepseek-v4.1-flash-1017/1 /approved/storage/deepseek-1017
```

The destination's parent must exist. Use a dedicated, canonical absolute path,
owned by the current user, with no other writers. A new directory is created
with mode `0700`; an existing one must already have that mode. Files are created
with mode `0600`. The command takes a nonblocking exclusive directory lock, so
two cooperating staging processes cannot modify the same directory together.
Allocate space for the entire pack plus filesystem overhead. Creating the later
immutable weight image requires separate space; this command does not create it.

Downloads are sequential, public and pinned to the full Hugging Face commit.
The downloader sends no credentials or cookies, ignores proxy/token environment
variables, uses only a fixed HTTPS host allowlist and at most four redirects,
and never prints signed URLs or response bodies. It hashes while streaming with
a 256 KiB buffer. Size/hash failures never publish a new final filename. No
retries, HTTP Range requests, alternate mirrors or mutable revisions are used.
There is a one-hour timeout per HTTP request and an explicit overall timeout
of at most 24 hours, including existing-file hashing and final verification.

### Restart and recovery

Rerun the same command after an ordinary transfer failure. All existing files
are checked before the first new download; only complete, size-and-SHA-256
verified files are reused. Corrupt files, unexplained entries, symlinks and
special files cause failure rather than being replaced or deleted. Final success
requires an exact-set reread against the **same in-memory manifest** used when
staging began; changed metadata on disk cannot alter that contract mid-run.

Ctrl-C, SIGTERM and deadline cancellation clean up the current temporary file;
completed files remain for reuse. Files are synced before atomic, no-overwrite
hard-link publication. The staging root's parent is synced before downloading;
containing directories are synced after publication and again before success,
including on a fully reused run. The staging root is synced after temporary-name
cleanup. A sync failure returns
an error even if the completed file was linked; rerunning rechecks its bytes.
Filesystem and hardware durability guarantees still apply: this is not a
power-failure-tested snapshot or a replacement for dm-verity.

SIGKILL or a host crash can leave a `.nib-part-*` file. The next run rejects it;
it does not delete files merely because their names match a prefix. Stop all
writers, inspect the directory and remove only the identified incomplete file
before restarting. Do not run wildcard cleanup against a live staging area.
A download or filesystem error can require operator intervention. Cancellation
cannot forcibly interrupt kernel-blocked filesystem I/O.

See [implementation and verification limits](../docs/tasks/p0-t4-weight-staging.md).
This tool is tested with small synthetic files; no full DeepSeek download,
inference, confidential admission or hardware acceptance is claimed.

## Check staged model bytes

After an approved download has materialized all files into a separate, quiescent
directory (not a symlink-based Hugging Face cache), run on Linux or macOS:

```sh
nib model check-local models/deepseek-v4.1-flash-1017/1 /path/to/staged-model
```

Use a canonical directory path, without a trailing slash, `/.` or `..` segments.
The pack and its `files.sha256` must come from your trusted, pinned toolkit
revision. Keep them outside the staged model directory. The command checks the
exact file set and each file's size and SHA-256, with a 256 KiB hashing buffer;
it does not load a shard into RAM. Missing/extra files, extra directories,
symlinks, special files and observed replacements fail closed. It checks again
after reading for changes to the file set, identity, size, mode or mtime.
Directory enumeration uses batches of at most 64 entries, rejecting unexpected
entries and checking cancellation without sorting an entire directory first.
Linux/macOS nonblocking, no-follow file opens prevent a leaf FIFO/link swap
from turning verification into a blocking read or following that link.

No downloads, writes, engine launch, cloud resources or credentials are involved.
Ctrl-C stops the CLI process. Library context cancellation is checked between
reads; a blocked local filesystem read is subject to the operating system's
I/O behavior. Only counts and sanitized errors are
printed, never model content or caller-provided paths.

**This is an offline integrity check, not a secure snapshot.** Stop all writers
first. A malicious writer can still change files after they have been read or
restore metadata; hard-linked files also remain mutable. A successful check is
not a boot/readiness token. A built dm-verity volume and measured guest remain
required to protect the eventual confidential runtime. Small synthetic fixtures
test this command; the complete DeepSeek weights have not been downloaded or
validated locally yet.
