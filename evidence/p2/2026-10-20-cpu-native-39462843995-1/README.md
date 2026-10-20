# Native CPU Entry Failure

The original [run.json](run.json) and [output.log](output.log) are unchanged
from GitHub Actions run
[39462843995, attempt 1](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39462843995),
produced by committed source `badaab20717e9c8f14f45f0821a87f9d50bc6b9a`.
The recorder ran from **17:10:14 to 17:10:32 UTC on October 20, 2026** on a
native Linux/amd64 software runner, without GPU, model weights or inference.

## Observed Failure

The recorder reached `application-and-entry`, but returned no validated
`experiment` object. It reports `cleanup: completion-unknown` and
`failure: cleanup-unresolved`. This does not prove whether application or entry
began inside the container, how far either progressed, or the underlying
exception. There is no application receipt, entry checkpoint, finder identity,
child exit/OOM observation or phase timing in these artifacts.

The raw OCI link and local manifest/platform/DiffID binding passed. The report
explicitly does not claim local config-byte or layer-byte rehashing.
`entry_pass`, `inference_started`, `confidential`, `deployable`,
`hardware_validated` and `abi_validated` remain false; candidate status remains
`BLOCKED`. This failure neither identifies Botocore nor authorizes the held
adapter, a guard exception or an automatic rerun.

## Separate Workflow Observation

The [final workflow step log](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39462843995/job/118766499126)
reaches the offline checker's fixed rejection message at
`17:10:35.8966001Z`. The
[committed workflow](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/blob/badaab20717e9c8f14f45f0821a87f9d50bc6b9a/.github/workflows/engine-cpu-entry.yml)
first requires a successful, empty Docker query for label `nib.cpu-entry`,
exiting immediately if the query fails or returns a container.

Thus, under the workflow's trusted-host/shell assumption, the later checker
message supports **no matching labeled container at that final query**. This
is a separate workflow observation, not recorder-confirmed cleanup, cancellation
of pending daemon work, or proof of future absence. The original
`completion-unknown` remains unchanged. The workflow/job itself failed, and
the offline checker correctly did not accept this incomplete diagnostic.

## Byte And Source Bindings

| Object | SHA-256 |
| --- | --- |
| Original `run.json` | `9a267a1c656619ba3387362b4dfe9314dbb468abe5c7baac725e8faa104e49ea` |
| Original `output.log` | `f579dc91dce55ad4f0cb01cd00bc7bef05dc1c1a45fbe872fe3ea0a86ad1cf0c` |
| Reconstructed payload | `f041cf7acb3280670534cb8d9d85d12b14f986c9cf2a417181439c570e43bcd0` |
| Producer recorder source | `418a78ef884dfcc1ff6d8232c20e4e0293161d0641b0f48b09ae2f75d8f0f6b1` |
| Producer workflow source | `c826afbb695657b00031ef3c78b5959dda05615e3987e91cc34394f960421772` |

The original Actions artifact is `cpu-native-39462843995-1`, ID `12283577980`;
GitHub reports a 1,839-byte archive with SHA-256
`ee6355c6da3ec4481a7b987bac5db72a110a001563c957c1e767cb96ba84ffe2`.
That service-reported archive digest is distinct from the file hashes above.

The [offline regression](../../../scripts/test_native_entry_evidence.py)
reconstructs the full payload with the producer's historical repository helper
closure, compares source-bundle/measurement/validator-source/OCI bindings,
checks the complete original envelope, and requires the historical success
checker to reject both this failure and fabricated pass/cleanup fields.
It executes no image, upstream engine, package or GPU code.
