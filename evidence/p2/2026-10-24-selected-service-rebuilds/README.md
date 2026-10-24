# Selected service image rebuilds

Two original build receipts report `result: pass`, `image_built: true` and
`build_completed: true`. Each matching preparation receipt is also retained
byte-for-byte. These are image-build results, not startup or inference results.
The full prepared contexts are not included.

The producer is `scripts/selected_service_image.py` at these exact commits:

- `638a3ad1483910e894901f57363a4286b515e2dc` (PR332): the self-contained
  [scratch-mount check](../../../docs/tasks/p2-t4-selected-service-lifecycle.md).
- `ab759d43b82e5e235ddb99eb5e5d5ebcfb360e63` (PR333): shared cache-directory
  initialization before engine startup.

The second image supersedes the first for the cache-initialization fix; it does
not overwrite or invalidate the first build result. The earlier
[93a6a86 build](../2026-10-24-selected-service-image/README.md) is unchanged.

## Exact handoffs

| Source | Original artifact | SHA-256 |
| --- | --- | --- |
| `638a3ad` | [Preparation](638a3ad/preparation.json) | `2a714f0cd04a9242e0f03bf8e55dfbd0f5c6fa86b97762ffb54c2fd1d8d4eca7` |
| `638a3ad` | [Build](638a3ad/build.json) | `67b963a0f6bf35c294a265799db39bc958db16867a468569eea96a6c95310410` |
| `ab759d` | [Preparation](ab759d/preparation.json) | `08e7d4b45a7041f53a866d67a359b20350578f63d876af3d64027ca2d71341bf` |
| `ab759d` | [Build](ab759d/build.json) | `68f9f233858f568469a8c7f4f8f8daa14deac7b525f6a5583032ff11df36df96` |

| Source | Prepared-service SHA-256 | Local Docker image ID (without `sha256:`) |
| --- | --- | --- |
| `638a3ad` | `6cbc32ce2e6cc6125b811724ad1a35ac86f06c1c31ce581539732776accd52ed` | `2857237c7ad23b66bcb73f5943d51721b37ed5c60542f4d5eabab51c49df1119` |
| `ab759d` | `d4c5c5f962f0558574a1d45dcbd2697f88de7fec0e8d7e47177723a5a73c5322` | `e1b4bd3d5713a412c8ca1e55aa537bb679fa29ca1a88e663e934b809dbbff49b` |

Both were local Linux/amd64 builds on the already-rented ordinary eight-B300
host, using the cached pinned base and network-disabled build steps. No model
weights or private operator inputs were added to the build contexts. The local Docker IDs
are not published registry digests.

The build invocation was the following, with private paths replaced by variables
and `PREPARATION_SHA256` selected from the matching row above:

```sh
/usr/bin/python3 -I -S -B scripts/selected_service_image.py build \
  --preparation "$APPROVED_PREPARATION" \
  --receipt-sha256 "$PREPARATION_SHA256" \
  --docker-host unix:///var/run/docker.sock --output "$NEW_BUILD_RECORD"
```

The host interpreter was Python 3.14.4; this is not the container interpreter,
which remains pinned to Python 3.12.3. Both successful terminal receipts were
observed on 2026-10-24. They contain no exact start/end timestamps or generic
`run.json` envelope; none have been fabricated. The 1800-second build timeout
is a bound, not a measured duration.

## Verification and limits

The original private contexts were rechecked read-only with `validated_files`
(407 receipt-bound files each) and joined to their build receipts with
`validate_build_record`. The regression extends the existing build-evidence
tests to all three builds: exact bytes, source/lab identity, handoff validation,
wrong receipt hashes, cross-preparation substitution and forbidden serving or
publication claims. Receipt-only regression tests do not replace full context
validation or a fresh runtime image inspection.

The records trust the local daemon, OS and interpreter. They verify the raw OCI
metadata link, platform and base DiffIDs, but do not independently rehash local
configuration or layer bytes. This is not a reproducible measured-image release
or remote attestation.

`runtime_started`, `hardware_validated`, `deployable` and `published` remain
false in both build receipts. Nonroot GPU startup, authenticated inference,
streaming, shutdown, public service, quality and confidential qualification
require separate evidence. Neither build receipt is promoted by any later run.
