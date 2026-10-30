# Closed Runtime Repository Metadata Candidate

Status: software-only candidate. The parent-serialized focused suite passed all
41 synthetic tests on 2026-10-30; independent review is required before merge. The author
ran no tests. No real collection, verifier invocation or network acquisition has
run. This is neither an enabled collector nor installation, runtime, operational
or P3 approval.

## Implemented Boundary

[`inspect-runtime-repository-metadata.py`](../../host/inspect-runtime-repository-metadata.py)
implements a bounded metadata validation core. Its executable always returns a
fixed, sanitized blocked report and exits nonzero. It has no network, process,
filesystem, environment, configuration, keyring or dynamic-module access.
`LIVE_PRECONDITIONS` contains only `None`. Changing those values, supplying an
argument or importing the helper cannot enable an executable acquisition path.

The internal `_evaluate_synthetic` composition boundary exists only for tests.
Injected reads, parser results, verification streams, clocks and lifecycle facts
are synthetic, never trusted runtime plugins. Even complete fixture validation
returns `synthetic-complete`, not PASS or repository authentication. Every result
keeps repository authentication, installed-status verification, dependency
closure, installability, executed-version verification, runtime support,
delegation, admission, operational approval, cleanup and absence authority false.
No synthetic receipt may be consumed as operational evidence.

The real HTTPS adapter, pinned python-debian parser/relationship adapter, explicit
keyring reader and gpgv process owner are deliberately **unimplemented**, not
implicitly approved. They require another reviewed implementation after exact
tool/parser/keyring/signer identities, key acceptability, trusted clock,
per-pocket authenticated rollback baselines, admission and finite scratch,
whole-process CPU/memory/PID/lifecycle bounds are supplied. No such facts or
operational configuration are supplied by this change. There is no CLI callback,
test flag, ambient keyring or executable verifier-injection route.

## Closed Set and Provenance

The only version is `255.4-1ubuntu8.18`; binary architecture is `amd64`. The binary
set is `systemd`, `systemd-container`, `libsystemd-shared`; the source is `systemd`.
The fixed version, artifact names and shared-library dependency come from prior
indexed [Ubuntu source catalogue](https://packages.ubuntu.com/source/noble/amd64/systemd)
and [binary catalogue](https://packages.ubuntu.com/ca/noble/amd64/admin/systemd-container)
research. Direct catalogue retrieval did not succeed. These are candidate
selectors, not freshly authenticated availability or artifact digest provenance;
missing exact descriptors must block without substituting another version.
Each pocket must independently contain the complete matching set:

| Pocket | Fixed root |
| --- | --- |
| security | `https://security.ubuntu.com/ubuntu/dists/noble-security/` |
| updates | `https://archive.ubuntu.com/ubuntu/dists/noble-updates/` |

Only `InRelease`, `main/binary-amd64/Packages.xz` and `main/source/Sources.xz`
exist in the acquisition model. There are at most six sequential requests, with
no redirects, retries, alternate mirrors/compression, package/source payloads or
recursive discovery. Missing metadata in either pocket blocks completion even
if the other pocket contains it; this conservative scope is not a statement that
the package is absent from every repository.

Each index binds to its **own** authenticated release text and expected suite,
Ubuntu origin/label, noble codename, main component and amd64 membership. Both
compressed and uncompressed sizes/SHA256 values must match that pocket's release
table. A union of release checksum tables cannot authorize an index. Identical
package/source descriptors across pockets are permitted, but both provenance
records remain in the result; conflicting descriptors are rejected. Duplicate
fields, selected records or checksum paths are rejected, not overwritten.
Failure reports retain any completed synthetic pocket chains, but reset overall
completion and retain every false authority flag; partial chains are not approval
or evidence that their freshness still holds after a failure.

The core checks exact source names/versions, binary filenames, the source
directory and the three source artifact descriptors (upstream tarball, Ubuntu
patch archive and dsc). Those descriptors are never fetched. Other metadata
versions do not become fallback candidates. Missing exact rows reject the set.
The two executable packages must declare the exact matching shared-library
dependency. The core consumes standard-parser relationship results, not an APT
solver. Alternatives, qualified dependencies and dependencies outside the closed
three-package set remain unresolved. Raw dependency expressions are bounded and
hashed for descriptor comparison; only hashes and group counts are emitted, not
arbitrary dependency names or repository text. This does not establish that
external dependencies are missing, satisfied, installed or jointly installable.

## Trust and Time

The synthetic verifier boundary receives the explicit expected keyring digest
and approved signer list; no implicit keyring is available. The core requires
successful verifier exit, observed verifier termination and one authorized
machine-readable `VALIDSIG`, including an authorized primary fingerprint when
present. Error/unknown status records, duplicate signatures, truncated status,
weak hash selections, oversized streams and expired signature metadata reject.
Verifier diagnostics and user-ID text are never printed. Key expiry/revocation
acceptability is a separate prerequisite: gpgv success is insufficient.

For **each** pocket a missing authenticated rollback baseline is
`rollback-unestablished`, never a pass. Older dates reject; an equal baseline date
requires identical InRelease bytes. Release dates may be at most seven days old
or sixty seconds ahead of the trusted clock. Any earlier `Valid-Until` or
signature expiry wins; missing `Valid-Until` is capped at Date plus seven days.
Expiry is rechecked after cleanup and again before returning the final result.
Seven-day freshness does not prove latest metadata or eliminate replay within
that window. Tests supply fictional baselines and clocks only.

One caller-origin monotonic cutoff is origin plus 60 seconds, including parsing,
verification exits, descriptor closure and final output preparation. The proposed
independent outer cutoff is **origin plus 90 seconds total**, not another 90
seconds after the first deadline. The synthetic owner receives that same outer
cutoff on cleanup, at most once. Neither cutoff is reset by a pocket, stream or
verification stage. Python checks cannot preempt blocking calls, malicious
callbacks, filesystem stalls or process cleanup. No independent supervisor or
actual resource enforcement is implemented here; timeout never proves absence.

## Finite Ceilings

These are conservative implementation limits, not measured capacity or approval:

| Boundary | Ceiling |
| --- | --- |
| InRelease / compressed index | 1 MiB / 16 MiB |
| Acquired bytes, including headers and rejected/error reads | 64 MiB aggregate |
| Expanded index / aggregate expansion | 64 MiB / 256 MiB |
| XZ decoder memory | 64 MiB |
| Processed bytes, including rereads and parser passes | 512 MiB aggregate |
| Index records / stanza / physical line | 100,000 / 256 KiB / 16 KiB |
| Release stanza / fields / checksum entries | 1 MiB / 128 / 16,384 |
| Response headers / rejected HTTP body | 16 KiB / 4 KiB |
| Combined verifier status and diagnostic streams | 16 KiB |
| Sanitized JSON | 16 KiB |
| A read or decoder output chunk | 32 KiB |
| Dependency groups / alternatives per group | 128 / 8 |
| Acquisition attempts / connection or idle interval | 6 / 10 seconds |

Readers receive a bound before allocation, including remaining aggregate room;
every returned byte is charged before rejection. A future real adapter must bound
headers, error bodies and all process streams before buffering, not merely pass
already unbounded objects into this core. The synthetic response model accepts
only identity HTTP content encoding, an exact positive Content-Length and no
transfer encoding. Its adapter must not follow redirects, credentials, ambient
proxies or configuration. No actual HTTP implementation is claimed.

XZ decoding has a memory cap and per-call output cap. Incomplete streams,
concatenated streams, trailing bytes, malformed data and size/hash mismatch reject.
All index records are consumed before acceptance, including after selected rows
have been found. Framing/duplicate guards precede standard parser normalization;
field/line/record limits prevent accidental whole-index parsing. Output includes
only fixed names, numeric counts/times, SHA256 values, authorized synthetic
fingerprints and fixed status enums. No HTTP bodies, diagnostics, private
identities, arbitrary paths, package descriptions or raw exceptions are emitted.

## Evidence Levels and Next Review

Catalogue existence, repository-authenticated metadata, installed observations
and executed/runtime support remain separate. This change performs none of those
observations. Even future authenticated metadata alone would not authenticate an
installed binary, inspect maintainer effects, solve an installation transaction,
prove a running manager or authorize delegation. Missing prerequisites or an
unestablished exact chain remain blocked/unknown without scope expansion.

The existing [inventory](p3-t1-runtime-inventory.md),
[capability collector](p3-t1-linux-capabilities.md),
[resource helper](../../host/probe-build-resources.py), other helpers, pins,
workflows and dependencies are unchanged. There is no installation, source/key
download, configuration modification, workflow dispatch or new runner allocation.

[`test_runtime_repository_metadata.py`](../../scripts/test_runtime_repository_metadata.py)
uses in-memory streams, synthetic compressed indexes and a closed fixture table
for dependency relations. Its parser stand-in uses the standard-library email
parser following existing repository patterns; it does not impersonate or prove
availability of the future pinned python-debian adapter. The cryptographic
fixture is explicitly not OpenPGP and no gpgv process is started. Tests cover
per-pocket joins/conflicts, baseline absence/rollback, expiry at final acceptance,
signer/exit/key-acceptability failures, stream and decompression limits,
duplicate/malformed records, missing versions/dependencies/source joins, secret
redaction, cleanup/deadline failures and unconditional executable refusal.

Parent-only focused invocation, **not run by the author**:

```sh
python3 -I -S -B -W error::ResourceWarning /source/scripts/test_runtime_repository_metadata.py
```

Minimum read-only fixtures: the new helper and test at their repository-relative
locations under `/source`, plus `/source/host/probe-build-resources.py` for its
unchanged-hash assertion. No real repository metadata, host package database,
keyring, executable verifier, credentials or private artifacts are inputs.
The parent ran this suite once in a pinned CPU container with three individual
read-only source mounts, no network, a read-only root, a nonroot user, dropped
capabilities and finite CPU/memory/PID/time limits. All 41 synthetic tests passed;
the container exited without OOM and was removed after verifying it had stopped.
No actual metadata collection, cryptographic verifier or runtime was exercised.
The existing repository test discovery includes this suite; no workflow change
or new job is needed. Final independent review remains required.

## Primary Contracts

External documentation consulted 2026-10-30, not measurements of an installed
tool version or runtime:

- [APT archive authentication](https://manpages.ubuntu.com/manpages/noble/man8/apt-secure.8.html):
  signed Release to index checksum to artifact descriptor; trust anchors must be
  authenticated independently, not supplied by the queried repository itself.
- [gpgv verification](https://manpages.ubuntu.com/manpages/noble/man1/gpgv.1.html):
  explicit keyring, machine-readable status and exit status; key expiry/revocation
  acceptability is not established by gpgv alone.
- [Debian control-file syntax](https://www.debian.org/doc/debian-policy/ch-controlfields.html):
  structured fields and continuations, case-insensitive unique field names.
- [APT time checks](https://manpages.ubuntu.com/manpages/noble/man5/apt.conf.5.html):
  trusted time and release freshness. The seven-day/sixty-second values above
  are this candidate's proposed restrictive policy, not asserted APT defaults.
