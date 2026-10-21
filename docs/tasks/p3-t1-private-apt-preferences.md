# P3.T1 private APT preferences preparation

This separate change follows the frozen
[`a3d2657` diagnostics](p3-t1-apt-diagnostics.md). It prepares an explicitly
empty APT preference policy inside the existing private `/work` tmpfs:

- `/work/etc/preferences`: empty regular file, mode `0600`, created exclusively
  with `O_NOFOLLOW`, using the existing setup writer.
- `/work/etc/preferences.d`: empty directory, mode `0700`.
- `Dir::Etc::preferences` and `Dir::Etc::preferencesparts`: the exact absolute
  paths above, not inherited or host paths.

Setup still requires an empty, absolute, non-symlink work directory. Existing
or misplaced entries are rejected rather than reused, replaced or repaired.
Creation errors remain a sanitized `setup-error` before any APT command runs.
The file contains no pin priorities and the directory contains no fragments.
This is not a package-lock or plan-validation change.

## Source basis and limits

Official Ubuntu [`apt_2.8.3.tar.xz`](https://archive.ubuntu.com/ubuntu/pool/main/a/apt/apt_2.8.3.tar.xz),
SHA-256 `088522b3613b28fdbcfa61f1f7e476bf6dc6b0120a8f74409e9527580c9f9d3b`,
defines the preference defaults in `apt-pkg/init.cc:158-159`.
`apt-pkg/policy.cc:382-391` warns when the preference directory is missing;
`policy.cc:414-420` tolerates a missing preference file. The explicit empty file
avoids relying on that absence behavior. The diagnostic document records the
source-backed warning formatting and exact fixed classification.

Source inspection establishes that the omitted directory produces that
warning under the fixed APT configuration. **The historical `03d52b4` failure
cause remains UNPROVEN** until the parent's unchanged `a3d2657` diagnostic run
provides an observation. No historical report or evidence bytes are changed.

All nonempty stderr, including the exact missing-directory warning, still
rejects. Package inputs, locks, exact Inst/Conf coverage, no-download simulation,
120-second shared deadline, output caps, container isolation/resource limits
and cleanup checks are unchanged. The source-bound bundle includes the new
entrypoint bytes; an old bundle is not a test of this configuration.

## Verification and next gate

Filesystem tests verify exact paths, empty contents, regular/directory types
and permissions under permissive and restrictive umasks. They reject existing
files, directories, symlinks and FIFOs at the preference paths, misplaced
preferences, invalid work roots and injected creation failures. Fake process
tests retain stderr rejection and the original deadline/resource contracts.
No APT, packaged program, maintainer script or container is executed by these
tests.

The parent/operator must first run the frozen diagnostic after its review and
CI gates. A separate decision, exact-head independent review and green CI are
required before running this changed configuration. This preparation does not
establish resolution success, installability, mkosi execution, LKCA, boot,
reproducibility or hardware qualification.
