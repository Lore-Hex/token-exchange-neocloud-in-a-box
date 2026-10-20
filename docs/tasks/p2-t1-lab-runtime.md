# Test-Only Container Runtime

`host/install-lab-runtime.py` provisions only an already approved, fresh Ubuntu
26.04 amd64 test VM. It is not part of a confidential release image.

Read-only package metadata and an apt simulation on the approved test VM on
2026-10-20 resolved exactly seven new packages, with zero upgrades or removals:
Docker `29.1.3-0ubuntu4.1`, containerd `2.2.2-0ubuntu1.1`, runc `1.4.0-0ubuntu1`
and the four NVIDIA container-toolkit packages at `1.20.1-1`. The script pins
those versions and rejects any changed dependency plan. It does not add package
sources, bypass apt signatures, install drivers, change CC modes, or expose ports.

Ubuntu's signed distribution packages supply Docker/containerd/runc; the
already-configured official NVIDIA CUDA repository supplies the toolkit.
See [Ubuntu Docker package](https://packages.ubuntu.com/resolute/docker.io) and
[NVIDIA's install and Docker configuration guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).
These are test-host runtime dependencies, not qualified production versions.

Transfer the committed script, independently verify its SHA-256, and run:

```sh
python3 -I -S install-lab-runtime.py
python3 -I -S install-lab-runtime.py --install
```

The first command only checks the platform and apt plan. The second installs
the fixed packages, verifies installed versions, configures the NVIDIA Docker
runtime using the official `nvidia-ctk` command, and starts Docker.
Only a fresh VM is accepted; an existing Docker installation/configuration or
a partial failed install requires inspection, not a blind rerun.
Command diagnostics are discarded; only fixed status, package versions and the
script hash are emitted. Package-manager failure can leave a partial install
and is not automatically rolled back. Never continue to an engine experiment
after a failed result.

After installation, `python3 -I -S host/pull-lab-image.py` downloads only the
repository's reviewed immutable vLLM image digest. It checks the exact expected
image configuration and Linux/amd64 platform before and after pulling, reuses
an already matching local image, uses no Docker credentials, and launches no
container. The 15-minute CLI timeout does not prove cancellation of daemon-side
downloads; inspect the daemon and retain the independent rental deadline after
a timeout. Image download is not image qualification; the separate engine
recorder still verifies its source, layers and application inputs.

Tests use synthetic apt plans. They prove rejection of upgrades, removals,
extra packages, changed versions, missing packages and duplicates. They do not
prove GPU readiness, inference, attestation or model quality.
