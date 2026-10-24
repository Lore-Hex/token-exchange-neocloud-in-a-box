# Selected service image built on the ordinary node

The original metadata-only [build receipt](build.json) reports `result: pass`,
`image_built: true` and `build_completed: true`. The
[preparation receipt](preparation.json) is also retained byte-for-byte.
The full prepared context is not included in this evidence directory.
The producing script is
`scripts/selected_service_image.py` at
`93a6a86ce7d177d2be20db67f950f581e58c529e` (PR323).

This was a local Linux/amd64 image build on the already-rented ordinary
eight-B300 host. It did not start inference or use GPU attestation. The builder
uses the cached pinned base, network-disabled build steps, a private build
context, and no operator credentials or model weights in image layers.

## Exact handoff

| Artifact | SHA-256 |
| --- | --- |
| `preparation.json` | `e920ff237959b92dcc2fbac9f4762135108e11c3efbfa362269381d9a9856740` |
| Derived lab contract (not included) | `9e133c5a16accb8c222266344bd8dfdfdaa1cd841c65e90973f8569f920104df` |
| `build.json` | `903fb7a91f79f7e184674633235a573867dd279f4dfe20d33efe337d928d185b` |
| Prepared service | `a65e5139f704c54943e0e8e47b1137e9472e311235b54a79b3b3c7dd764c6750` |
| Local derived image ID | `eb0c7a2a2ccf7f000d1678a3c4aba57e6918ef97a4817eb5a8e829c2768741b0` |

The image ID is a local Docker identity, not a published registry digest.
The following is the exact invocation with private operator paths substituted
by variables; its original values are intentionally not public:

```sh
python3 -I -S -B scripts/selected_service_image.py build \
  --preparation "$APPROVED_PREPARATION" \
  --receipt-sha256 e920ff237959b92dcc2fbac9f4762135108e11c3efbfa362269381d9a9856740 \
  --docker-host unix:///var/run/docker.sock --output "$NEW_BUILD_RECORD"
```

The successful terminal receipt was observed on 2026-10-24 at 22:16 UTC.
This recorder does not include exact start/end timestamps or a generic
`run.json` wrapper; none have been fabricated. Its 1800-second build timeout
is a limit, not a measured duration. The original private preparation directory
was independently revalidated in full with `validated_files`, then joined to
the transferred build receipt with `validate_build_record`.

The checked-in regression pins these original receipt bytes and checks their
handoff using the same build-record validator, with the base identity derived
from the preparation's metadata hashes. It is not a replacement for
checking every prepared source file or freshly inspecting the runtime image.

## Limits

The receipt explicitly trusts the local daemon, host OS and interpreter.
It verifies the raw OCI metadata link, platform and base DiffID list, but
does **not** independently rehash local layer or configuration bytes.
This is not a reproducible measured-image release or a remote attestation.

`runtime_started`, `hardware_validated`, `deployable` and `published` remain
false. Nonroot GPU startup, authenticated inference, streaming, shutdown,
public service, quality and confidential-computing qualification require
their own evidence. The separate failed weight-view preparation remains
[unchanged](../2026-10-24-selected-weight-view-parent-failure/README.md).
