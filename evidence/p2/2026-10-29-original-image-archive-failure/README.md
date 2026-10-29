# Original image archive: failed verification

The committed verifier at `9cbb4575f8b32dc0cdfef3e3114f3409d4c2da79`
returned failure against the locally built official-model image. The original
receipt and stopped-container log are preserved byte-for-byte. This was a
CPU-only archive inspection, not a GPU or inference test. The worker exited;
the image was not started or uploaded. No replacement result is implied.

The original receipt intentionally has only a generic failure label. Subsequent
read-only metadata inspection found this definite rejection condition:

- OCI root: `sha256:1602c196b10a1c450fc0c93a5f7d47f816252c90c8bf36a900cf6290196871a2`.
- Runnable manifest: `sha256:14d8bf5ebdd92cd3f253a1737aed649145fef6e43c5d49f443374c74a213c8d9`.
- Attestation blob: `sha256:717dafa2ed8cc376bd47ba44e7a17df0baa70c3d9d95edc332ae6d4f53100434`.
- Its in-toto `subject` array is empty. The unchanged parser requires the exact
  runnable manifest digest in the subject. Synthetic regression tests reproduce
  that rejection; this does not establish the first failure stage of this run.

BuildKit constructs default subjects from nonempty image names in
[`writer.go` at c19b15f](https://github.com/moby/buildkit/blob/c19b15f5775bd222f93b9741ade08fe77c3d3de7/exporter/containerimage/writer.go).
Our build previously supplied an image-ID output file but no image name.
[Docker's attestation storage contract](https://docs.docker.com/build/metadata/attestations/attestation-storage/#attestation-blob)
describes a subject bound to the target image manifest or an object within it.
Source and documentation were checked October 29, 2026. This explains an
observed blocker, not a successful rebuild or verified provenance claim.

The repair supplies one fresh local-only image name and verifies its readback.
It does not disable provenance, strip metadata, accept empty subjects, change
the image-ID handoff, publish an image, or relax any archive validation.
The old archive and this failed evidence remain unchanged. A separately reviewed
fresh preparation, build, export and complete verification are still required.
