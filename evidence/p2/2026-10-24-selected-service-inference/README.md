# Private selected-service inference

[smoke.json](smoke.json) is the unmodified 1973-byte terminal receipt from one
bounded private run on the existing ordinary eight-B300 host. Its SHA-256 is
`a3381c17a08e0c6edf91063a65e60fc514ed10b5cd9176d2010e0f04bbf2c7b9`.
The operator observed completion at `2026-10-24T23:59:26Z`; that observation is
not a timestamp field in the receipt. No start time, duration, generic run
envelope, response content or additional runtime observation is synthesized.

## Result and scope

The original reports `PASS`, all three owned roles ready, and one authenticated
synthetic nonstreaming chat completion through TLS, guard and engine. The
source-bound helper accepted the canonical selected model, nonempty visible
content and consistent integer usage: **36 prompt tokens, 13 completion tokens**.
The preceding missing-key and wrong-key requests both passed their exact 401
guard-error checks. No prompt, completion, key, hostname or address is retained.

The lifecycle reports normal retirement and ordered TLS, guard, engine
drain/reap; the host reports cleanup confirmed. The operator also reported an
independent empty Docker process listing, but that listing is not included and
is not additional machine-readable evidence in this directory.

The container used the existing network-none, no-published-port profile with
nonroot/read-only execution and private input snapshots. This was a private
functional inference smoke, not a public endpoint or sustained serving test.
`auth_upstream_calls_measured` remains false: the two 401 responses do not measure
zero engine calls. Authentication-before-forwarding is separately supported by
the reviewed guard and its transport-count tests, as explained in the
[helper contract](../../../docs/tasks/p2-t4-selected-service-inference.md).

All original hardware-validation, deployment, publication, provider-conformance,
qualification, streaming, tools, reasoning, ZDR, confidential and E2EE flags
remain false. Narrow actual private inference success does not promote those
broader claims or erase earlier failed attempts.

## Exact source and artifact joins

The producer is
[`host/record-selected-service-smoke.py` at ab759d43](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/blob/ab759d43b82e5e235ddb99eb5e5d5ebcfb360e63/host/record-selected-service-smoke.py),
with `--run --inference-smoke`. The image, lifecycle, helper and guard use that
same reviewed source commit. The private invocation supplied the existing
build/preparation paths and hashes, local Docker socket, weight view, private
provider/key/certificate inputs, operator prices and absolute UTC cutoff. Those
private arguments are not reproduced or invented here.

The receipt joins the unchanged [PR334 build/preparation artifacts](../2026-10-24-selected-service-rebuilds/README.md):

| Identity | Exact value |
| --- | --- |
| Source | `ab759d43b82e5e235ddb99eb5e5d5ebcfb360e63` |
| [Build](../2026-10-24-selected-service-rebuilds/ab759d/build.json) SHA-256 | `68f9f233858f568469a8c7f4f8f8daa14deac7b525f6a5583032ff11df36df96` |
| [Preparation](../2026-10-24-selected-service-rebuilds/ab759d/preparation.json) SHA-256 | `08e7d4b45a7041f53a866d67a359b20350578f63d876af3d64027ca2d71341bf` |
| Prepared-service SHA-256 | `d4c5c5f962f0558574a1d45dcbd2697f88de7fec0e8d7e47177723a5a73c5322` |
| Local image ID | `sha256:e1b4bd3d5713a412c8ca1e55aa537bb679fa29ca1a88e663e934b809dbbff49b` |

These are local receipts trusting the daemon, OS and interpreter, not remote
attestation or a published image digest. The host receipt has no standalone
source-commit field; its preparation/build joins and the operator's frozen-source
record establish the stated producer. The original build remains build-only.

## Offline verification

The [existing evidence regression](../../../scripts/test_selected_service_build_evidence.py)
now joins this original to the matching source/build/preparation/image, reuses
the strict lifecycle and inference validators, checks exact usage and rejects
cross-build substitution, cleanup tampering and claim promotion. It does not
replay inference or independently reobserve the host.

The original private prepared context was also checked read-only: all 407
receipt-bound files and the build/preparation handoff passed the existing
validators. The full context and private operator inputs are not committed.

```sh
PYTHONPATH=scripts python3 -B -W error::ResourceWarning -m unittest -q test_selected_service_build_evidence test_selected_service_inference test_selected_service_hook test_selected_startup_diagnostic_evidence
python3 scripts/check-doc-links.py
```

No new hardware run or public action was performed to publish this evidence.
