# Original handoff image archive verified

The fresh original-model image for the paired mount-owner/consumer implementation
passed local CPU-only archive verification. This preserves the actual run, not a
rerun or evidence of mount handoff, image import, inference or deployment.

- [receipt.json](receipt.json): byte-original verifier receipt, SHA256
  `786edafae5e5dff82f46bb4465742a803a349c89cc097c78e5dcd5f698568dc7`.
- [output.log](output.log): byte-original stopped-container output, SHA256
  `6ca680d7eef85f6ff979851cd42c0624af89c2fc97b76ed4a2cf686ad6829519`.
- [build.json](build.json): byte-original successful build receipt, SHA256
  `1a779ce2aee671cd10639c6567dee7e35e84fc81128eac486e74d24567345e98`.
- [run.json](run.json): preservation metadata using the verifier receipt's times.

Producer `d18aee4ce01062134d0e893c72555ef104a9ed84` is already preserved in
normal merge ancestry. The verifier authenticated the fresh preparation/build
joins and helper sources, closed original-model profile and complete archive:
9,247,318,016 bytes, SHA256
`7b2f9939774a96a940c42509eb894df0e39470edaaca8faf5e5c8215b6a7c787`.
The image identity is
`sha256:da41df7ab7cf102c223107e70881fc42417fbbac57fa0eafa16c3000fb6f31ff`.
All 44 ordered layer DiffIDs, blob hashes and provenance bindings passed.
Expanded ordered-layer bytes total 23,745,049,088, including repeated layers;
this is not a filesystem-capacity or Docker import-space guarantee.

The verifier was Linux/arm64; the image is Linux/amd64 and was not executed.
The receipt confirms worker exit; the operator reports container exit zero
without OOM under the isolated resource limits recorded in `run.json`.
Privacy review found no redaction necessary in these three original artifacts;
their bytes are unchanged. The archive, large preparation, private plans and
operator/container identities are not published here. The
[earlier failure](../2026-10-29-original-image-archive-failure/README.md) and
[previous image PASS](../2026-10-29-original-image-verified/README.md) remain
unchanged, separate records; neither is relabelled as this run.

`image_loaded`, `runtime_started`, `deployable`, `hardware_validated`,
`published` and `confidential` remain false. This does not establish a live
official-model endpoint, successful consumer namespace/mount execution, model
weight validation, quality, conformance, cache behavior, accounting or CC.
Image import, lifecycle-bound storage handoff, drained cutover/rollback and
fresh runtime checks retain their separate gates. No live profile, tariff,
catalog entry or phase exit changes.
