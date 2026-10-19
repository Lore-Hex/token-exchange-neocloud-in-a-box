<!-- SPDX-License-Identifier: Apache-2.0 -->
# ADR 0005: Deterministic verified model volume

Status: accepted design; P0 pins files, P5 builds and validates volumes.
Records PLAN D5.

## Decision

Pin the complete model repository to an immutable revision. Record every
included file's exact bytes and SHA-256, including configuration, tokenizer,
license and executable model code, not only weight shards. Build a deterministic
EROFS image and dm-verity tree with explicit reproducibility inputs. Keep the
accepted verity root inside the measured guest image.

The engine reads only the verified read-only device. A host cache is merely an
untrusted source of bytes. It must never become an alternate direct model path,
and an error must never trigger automatic loading of an unchecked replacement.

## Rationale and limits

Public weights need integrity, not secrecy. A startup checksum leaves a
check-then-use gap if the host can alter a mounted volume later.
[Linux dm-verity](https://docs.kernel.org/admin-guide/device-mapper/verity.html)
checks blocks against the supplied trusted root. Its `ignore_corruption` and
`check_at_most_once` options are forbidden here: they do not provide the required
online tamper rejection. A valid old volume also needs rejection when the
relying party's release policy has withdrawn it; verity is not anti-rollback.

Draft packs may have an empty verity root until the volume is actually built.
They are not deployable. Do not invent a root or equate a source-manifest hash
with a filesystem verity root. The [schemas](../../schemas/README.md) enforce
that distinction structurally; they do not prove construction or provenance.

## Verification and revisit

P0 checks complete remote manifests without downloading the weight payloads.
P5 downloads and verifies all bytes, builds twice on independent machines, and
compares output roots. On the real guest, flip a host-side data block after
mount and force a fresh read: it must fail rather than return modified bytes.
Repeat for metadata/model-code substitution, wrong roots and unchecked path
overrides. Reboot tests must reuse the cache only through verification.

Revisit only with an alternative providing equivalent authenticated per-read
integrity, deterministic artifacts and explicit rollback policy.
