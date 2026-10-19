# Model packs

DS1017 and GLM-5.3 now have pinned revisions, complete file manifests and
source-backed plain-only draft profiles; see [verification and limits](SOURCES.md).
Model licenses are separate from this repository's Apache-2.0
license. No weights are distributed here.

## Check staged model bytes

After an approved download has materialized all files into a separate, quiescent
directory (not a symlink-based Hugging Face cache), run on Linux or macOS:

```sh
nib model check-local models/deepseek-v4.1-flash-1017/1 /path/to/staged-model
```

The pack and its `files.sha256` must come from your trusted, pinned toolkit
revision. Keep them outside the staged model directory. The command checks the
exact file set and each file's size and SHA-256, with a 256 KiB hashing buffer;
it does not load a shard into RAM. Missing/extra files, extra directories,
symlinks, special files and observed replacements fail closed. It checks again
after reading for changes to the file set, identity, size, mode or mtime.
Linux/macOS nonblocking, no-follow file opens prevent a leaf FIFO/link swap
from turning verification into a blocking read or following that link.

No downloads, writes, engine launch, cloud resources or credentials are involved.
Ctrl-C interrupts between file reads; a blocked local filesystem read is subject
to the operating system's I/O behavior. Only counts and sanitized errors are
printed, never model content or caller-provided paths.

**This is an offline integrity check, not a secure snapshot.** Stop all writers
first. A malicious writer can still change files after they have been read or
restore metadata; hard-linked files also remain mutable. A successful check is
not a boot/readiness token. A built dm-verity volume and measured guest remain
required to protect the eventual confidential runtime. Small synthetic fixtures
test this command; the complete DeepSeek weights have not been downloaded or
validated locally yet.
