# P3.T1 bounded APT failure diagnostics

The reviewed `03d52b4b078c8102b296e03603ec34c00c0c3401` run failed at the
guest `resolve` stage after APT version checking and local indexing. The outer
runner completed and reported cleanup confirmed. No packages or maintainer
scripts were installed/executed. Its v1 report cannot distinguish a failed
process, nonempty stderr, deadline/collector failure or rejected plan. **The
exact cause remains unknown.**

The [historical record](../../evidence/p3/2026-10-20-apt-resolve-failure/run.json)
was produced by committed importer
`e3c2bc747312874175e3402e88919da39ba5b22a`. Its `output.log` is unchanged from the
original v1 report. The regression rechecks it with the original `03d` producer's
validator, without executing APT or reconstructing a new runtime observation.

## Diagnostic-only change

The v2 guest/host reports add one failure diagnostic; successful reports require
it to be null. This does not change APT arguments, configuration, package inputs,
Docker policy, cleanup, timeouts, output caps or plan acceptance.

- Fixed process reasons distinguish nonzero exit, nonempty stderr, timeout,
  collector output limit, collector error, OS error, invalid collector result,
  shared deadline expiry and wrong APT version.
- A completed process contributes its bounded exit code and stdout/stderr byte
  counts. Stderr contributes only counts of `E:`, `W:`, `N:` and other nonempty
  lines, plus the single exact warning classifier below. Prefix counts alone
  are observations, not attribution of a root cause.
- A collector exception has unavailable counts/exit code, not invented zeroes.
  Exception messages, command arguments and partial output are never serialized.
  Only the pinned collector's exact fixed output-limit exception is classified;
  other collector failures remain generic.
- Plan failures distinguish encoding/control characters, line/output bounds,
  forbidden operations/warnings, record syntax, unknown package, wrong version,
  duplicate records, rejected transient-dependency suffix and incomplete
  Inst/Conf coverage.
- Plan observations contain counts and at most 16 sorted missing IDs per
  Inst/Conf set. A subject ID is emitted only after membership in the committed
  target set is established. Unknown package names, versions, repository
  descriptions, paths and raw output are not emitted or hashed on failure.

The host validates the diagnostic's exact keys, enums, stage consistency,
numeric bounds, coverage arithmetic, ID allowlist and pass/fail consistency.
It source-binds the loaded validation helper to the same bytes included in the
prepared image. No diagnostic field grants installation or deployment.

The original 120-second shared APT deadline, 2 MiB stdout/64 KiB stderr limits
and [container boundary](p3-t1-linux-tools-simulation.md) remain unchanged.
The existing plan SHA-256 is still returned only on accepted plans. New reports
use `nib.apt-simulation/v2` and `nib.apt-container-run/v2`; historical v1 reports
are not retroactively given new observations.

Tests use fake APT outputs, producer-to-host validation, malformed/private
sentinels, finite coverage, deadline cases and comparison with the original
committed v1 plan validator. They do not run APT or containers.

## Exact missing-preferences warning

`stderr-missing-preferences-directory` identifies only this entire stderr byte
string, including its trailing slash and single LF, at `resolve` with exit zero
before the shared deadline:

```text
W: Unable to read /work/etc/preferences.d/ - DirectoryExists (2: No such file or directory)
```

This is derived from the exact official Ubuntu
[`apt_2.8.3.tar.xz`](https://archive.ubuntu.com/ubuntu/pool/main/a/apt/apt_2.8.3.tar.xz),
SHA-256 `088522b3613b28fdbcfa61f1f7e476bf6dc6b0120a8f74409e9527580c9f9d3b`:
`apt-pkg/init.cc:158-159` sets the relative preferences defaults;
`apt-pkg/contrib/configuration.cc:339-405` resolves the private parent and adds
the directory slash; `apt-pkg/contrib/fileutl.cc:355-360` uses `stat`;
`apt-pkg/policy.cc:382-391` calls `WarningE` with `DirectoryExists` and
`Unable to read %s` on absence. `apt-pkg/contrib/error.cc:49-104,192-203,280-296`
formats the errno suffix, warning prefix and newline. The same source's
`test/integration/test-apt-get-update-unauth-warning:35` fixes the Linux C-locale
missing-file errno rendering as `(2: No such file or directory)`. The entrypoint
already fixes `LC_ALL=C`; these sources were read, not executed.

The host requires the exact stderr byte count and exactly one warning, with
zero errors/notices/other lines. The fixed reason is not raw stderr or a hash of
it. Changed paths/errno/spacing, CRLF, extra lines, multiple warnings, private
sentinels and every single-byte mutation retain the generic stderr rejection.
Other stages/nonzero exits/deadline expiry do not gain this classification.
All such results still fail; configuration and acceptance are unchanged.
The historical run remains unattributed until new observations are available.

## Historical report preservation

The [importer](../../scripts/record_apt_report.py) accepts **only** the original
failed report bytes, SHA-256
`37801abac400cd6cda8be1986591c8b7f1cae8c01de1f42e401246af98c2dd4f`.
It is not an arbitrary report publication API.

```sh
python3 -I scripts/record_apt_report.py \
  --report "$NIB_ORIGINAL_APT_REPORT" \
  --output "$NIB_APT_HISTORICAL_EVIDENCE"
```

From a committed producer, it creates private, no-overwrite `run.json` and
`output.log`. The latter is byte-identical to the original report, not a
reformatted or enriched substitute. `run.json` retains the original producer
SHA, reported execution timestamps and failure. It identifies the importer SHA
and helper hashes separately. The fixed original CLI is shown with normalized
private paths, whose literal values were not captured in the report.

This imports reported observations; it neither reruns APT nor independently
re-establishes historical container state. No raw logs are recovered and no
failure cause is fabricated. Symlinks, hard links, special/oversized input,
changed bytes and existing output reject.

## Next run gate

Only the parent/operator may run the changed diagnostic after exact-head review,
full tests and green CI, with a newly prepared source-bound bundle. Preserve the
original failed report. A changed diagnostic run is not an unchanged-input retry;
it still cannot install packages or execute maintainer scripts. Installation,
mkosi build, LKCA, boot, reproducibility and hardware qualification remain
separate gates.

A [separate private-preferences preparation](p3-t1-private-apt-preferences.md)
adds explicit empty preference paths. It does not change the frozen diagnostic
run source or attribute the historical failure.
