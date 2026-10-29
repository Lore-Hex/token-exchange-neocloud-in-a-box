# Original image archive verified

The fresh official-model image passed the committed CPU-only archive verifier
on October 29. This validates image bytes and source bindings, not model weights
on a GPU, inference quality, deployment, or confidentiality.

- [receipt.json](receipt.json): byte-original verifier receipt, SHA256
  `d1da9b8ce809a9cf4a7996a98eec3abb1a4f69f41053e8e1097cb16223b7c8cb`.
- [output.log](output.log): original stopped-container output, SHA256
  `6ca680d7eef85f6ff979851cd42c0624af89c2fc97b76ed4a2cf686ad6829519`.
- [build.json](build.json): original successful build receipt, SHA256
  `2e86711c2462efa5cff9e09ee455f593c7450bede1aae1fc11e638228ad80cee`.
- [run.json](run.json): preservation metadata for the actual verification.

Producer `bb3064eddf091b754e8213f81d2b583043a7a9b1` is preserved in normal
merge ancestry. The verifier checked the exact preparation and build receipts,
authenticated helper sources, original checkpoint identity and execution
profile, and the complete 9,247,235,584-byte archive. The runnable image is
Linux/amd64; the local verification container was Linux/arm64 and did not
execute the image. All 44 ordered layer DiffIDs and archive blob hashes passed,
including the provenance graph's runnable-manifest subject. The summed expanded
layer bytes were 23,744,649,728, including repeated layers; that is not a remote
disk-capacity measurement or a Docker import-space guarantee.

The fresh build supplies a local-only image name so BuildKit produces a bound
provenance subject. Export used the exact image ID. No metadata was stripped,
provenance disabled, or validator relaxed. The
[earlier failed archive](../2026-10-29-original-image-archive-failure/README.md)
and its original result remain unchanged and are not relabelled as this run.

The verifier exited zero, confirmed its worker exit, and the retained local
container exited without OOM. It had no network, GPU, Docker socket, credentials
or production endpoint. Only its new receipt directory was writable. The
archive and large preparation remain operator-retained inputs, not repository
assets. Their exact hashes are in the original receipt.

`image_loaded`, `runtime_started`, `deployable`, `hardware_validated`,
`published` and `confidential` remain false. Before serving, independently verify
the complete official weight pack, complete bounded storage/image handoffs,
coordinate a drained identity-correct cutover with rollback, and run fresh
conformance, quality, cache and accounting checks. This result does not change
any live model, tariff, catalog entry or phase exit.
