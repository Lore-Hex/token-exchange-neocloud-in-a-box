# Native CPU entry diagnostic

The manual `Native CPU engine entry` workflow runs the existing CPU-only
diagnostic on a disposable native Linux/amd64 GitHub-hosted runner. It is not
a GPU run, model test, release build, attestation or production deployment.
Dispatch only after independent final-head review and required CI are green.
It accepts no source override and runs only from `main`; it is not scheduled.

The container's existing isolation, source checks, 720-second application
budget, 300-second child budget and 1,080-second attach limit are unchanged.
The 45-minute outer job also covers the bounded public-image pull and setup.
No weights, API keys, secrets, GPUs or published ports are supplied. Network
access is used on the host to download pinned Go dependencies and the image,
never inside the diagnostic container. No paid/larger/self-hosted runner is used.
Unused preinstalled SDK directories are removed only on this disposable hosted
runner to leave at least 50 GiB for the approximately 9.2 GB compressed and
33.1 GB unpacked image plus working space. The job checks available space and
at least 10 GiB of host memory before pulling; it fails rather than changing
the container limits. GitHub's [public runner specifications](https://docs.github.com/en/actions/reference/runners/github-hosted-runners#standard-github-hosted-runners-for-public-repositories)
list 4 CPUs and 16 GB memory for `ubuntu-24.04` (checked 2026-10-20).
They do not guarantee the reclaimed disk space, so the disk check is required.

The host temporary directory remains mode `0700`. Only the read-only mounted
payload is mode `0555` for directories and its executable wrapper, and `0444`
for source/data files. Native Linux preserves the host UID on bind mounts;
the container's UID 0 cannot read another UID's owner-only files with all
capabilities dropped. See Linux [capabilities](https://man7.org/linux/man-pages/man7/capabilities.7.html),
`CAP_DAC_OVERRIDE` and `CAP_DAC_READ_SEARCH`. No capability is added and no
private host data becomes readable through the enclosing private directory.
This follows the existing image recorder's payload-mode pattern.

`oci/manifest.json` and `oci/config.json` are unmodified public Docker registry
responses for the immutable image, not Docker's reformatted inspection output:

- Image/manifest: `vllm/vllm-openai@sha256:6ee133356d49ed24cee03dd0cda3d3980279a11c107ce3e752c89e76540f41c9`
- Config SHA-256: `bd8a5d7a8774800c92d714c0c226b32c1863f929150f36efd610aebcd12a335d`

The recorder validates their exact hashes, manifest/config linkage and image
binding before container execution. Its evidence records `github-actions`
as the operator-declared host, not a cryptographic platform claim. The original
`run.json` and `output.log` are retained as a seven-day workflow artifact even
on diagnostic failure. Download and preserve failed artifacts in the repository
through a reviewed evidence PR before they expire.

The recorder deliberately exits nonzero because this cannot qualify a release.
The final workflow step separately rechecks the exact producer/payload and the
existing diagnostic validator. It fails unless `entry_pass=true` and cleanup
is confirmed. A green workflow still does not mean inference or confidentiality.
