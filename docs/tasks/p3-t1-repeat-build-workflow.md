# Native Repeat-Build Workflow

`mkosi-repeat-build.yml` composes two sequential calls to the existing bounded
native builder, then compares their metadata on a third runner. Local reusable
workflow references resolve to the same commit as the caller, per the
[GitHub workflow contract](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows#calling-a-reusable-workflow).
No branch is resolved again between builds. The first call is main-only; later
jobs require their predecessors to succeed. Both builds enable all six existing
build/export flags and use the unchanged 55-minute cap, pinned inputs, private
resource limits, output verification and cleanup. They use fresh hosted runners
and do not reuse a root directory or compiled driver cache.

The reusable builder adds only a fixed metadata namespace (`first` or `second`).
Ordinary manual builds keep their original artifact names and default-off flags.
Invalid namespaces fail before fetching or installing. The shared builder lock
continues to serialize native build work; the enclosing repeat workflow has its
own distinct lock. No build implementation is copied into the repeat workflow.

The comparison job has read-only repository/action permissions. It downloads
only the two artifacts named for its own run ID and attempt, then invokes the
existing bounded metadata comparator in a cleared environment with an explicit
expected producer SHA. An earlier attempt, another commit, overlapping/copied
receipts, changed image hashes, malformed metadata or unverified cleanup cannot
produce a green comparison. An image mismatch retains the comparator's failure
JSON and fails the job; unavailable/invalid evidence also fails. Only metadata
is uploaded, never private images. No credentials are passed into the comparator
or guest build.

After final-head review and green checks, an authorized operator can dispatch:

```sh
gh workflow run mkosi-repeat-build.yml --ref main
```

This is opt-in, not a scheduled billable build or a change to branch protection.
It does not authorize a boot, cloud deployment or publication of an image. A
passing comparison remains a trusted-builder receipt comparison, not independent
builder trust, cross-day/cross-platform reproducibility, signing, hardware
attestation or a confidential service. An actual workflow run is required to
validate orchestration; unit tests alone do not establish that result.
