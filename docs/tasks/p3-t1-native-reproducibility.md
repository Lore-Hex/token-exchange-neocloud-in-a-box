# P3.T1 Native Export Comparison

The existing main-only `mkosi-native-build.yml` workflow can run two clean
builds of one exact commit. Enable all six build/export flags. Wait for the
first run to finish, retain its five JSON artifacts, then rerun that workflow
run: GitHub reuses the original commit, even if main has advanced. Download
each attempt separately. Do not overwrite the first attempt's evidence.

The export verifier rehashes the actual GPT and unsigned UKI, verifies their
build and cleanup receipts, deletes the private images, then uploads only
JSON. See [native export validation](p3-t1-native-private-export-test.md).
Do not enable image publication or remove its resource bounds for this test.

```sh
python3 -B scripts/compare_native_exports.py /absolute/attempt-1 /absolute/attempt-2
```

The offline comparator reuses the strict JSON, bounded descriptor-anchored
file reader, export proof and cleanup validators. It requires identical build
source and validator/workflow hashes, exact receipt bindings, nonoverlapping
successful build windows, and unqualified original receipts. Copied receipts,
extra files, symlinks, ambiguous JSON and false cleanup claims reject. Valid
but different image bytes produce a failure report rather than a parsing error.

The report compares GPT, UKI and verity-root hashes. It does not authenticate
the builder or prove that two hosts were independent. Review the corresponding
GitHub job/attempt identities and authenticated artifact provenance separately.
These unsigned receipts are evidence from a trusted builder, not a substitute
for cryptographic attestation. No guest boot, kernel verity enforcement,
release signing, CPU/GPU attestation or confidential-service claim follows from
matching image hashes. Two independent builders in P8 remain a separate gate.

The [actual same-commit pair](../../evidence/p3/2026-10-22-native-repro-mismatch/README.md)
failed reproducibility: both builds verified and cleaned up, but GPT, verity-root
and UKI hashes differ. Original attempt metadata and comparison output are
preserved unchanged. Compiled drivers and initrd match; the changing root files
are not yet identified. The comparator also has synthetic mutation coverage.
