# P2.T4: Pinned Buildx for the Ordinary GPU Lab

An installed Docker Engine is not sufficient for the selected-model tau trial:
the CPU support-image builder requires Buildx in an isolated Docker configuration.
The ordinary Linux test host did not have this plugin. No inference or tau score
is established by installing it.

For an approved **Linux amd64 test host only**, download
[Buildx v0.38.1](https://github.com/docker/buildx/releases/tag/v0.38.1), asset
`buildx-v0.38.1.linux-amd64`. The official release's `checksums.txt`, read on
2026-10-24, specifies SHA-256
`e25d5cd675092bb5d1d03632fdc46cdf3fede7b514dc5f023a105e00d3ce5ad4`;
the release API declares 68,366,498 bytes. Transfer that file to a private
directory on the approved host, then run the committed installer as root:

```sh
python3 -I -S host/install-lab-buildx.py --binary /private/input/buildx-v0.38.1.linux-amd64
```

The installer verifies exact bytes before execution, installs atomically into
Docker's documented system plugin directory, refuses to replace a different
existing binary, and checks the version with an empty temporary Docker
configuration. It neither changes the daemon configuration nor restarts Docker,
installs a driver, downloads dependencies, or modifies any guest image. Repeating
the command accepts only the same root-owned binary with mode 0755.

[Docker's manual-install guidance](https://github.com/docker/buildx#manual-download)
warns that this path does not receive automatic security updates. This is a
bounded lab prerequisite, not a production update policy. Preserve its report
with the trial evidence and review the pin before future deployments.

Unit coverage exercises digest/size/type failures, symlinks, FIFO rejection,
exclusive installation, unsafe directories, and isolated bounded verification.
Hardware validation and the selected-model tau result remain separate gates.
