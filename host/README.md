# Host setup

Reserved for version-pinned BIOS, TDX, GPU CC and QEMU launch procedures.
Host commands require the hardware gates in PLAN.md. No BIOS/GPU settings or
confidential launch procedure is implemented here yet.

## Private Test-Host Preparation

`prepare-lab.py` prepares operator-owned mode-0700 directories for an already
approved rental. Transfer this committed script alone, verify its SHA-256 against
the trusted local checkout, and run it using `python3 -I -S`. `init --root PATH`
creates the private root and `stage` subdirectory; the parent must already be
private and owned. This does not install packages or change GPU modes.

Transfer the approved `source-45e7a09.bundle` into that root, then run
`checkout --root PATH`. Its fixed SHA-256 and revision must match before Git runs.
The checkout is new-only, with hooks and ambient Git configuration disabled.
Use the checkout's committed `host/record-probe.py` for actual hardware evidence.
`prerequisites --root PATH` reports only whether expected executables exist.
These setup results are not hardware qualification or a model-serving result.

## Approved weight staging

The [bounded staging wrapper](../docs/tasks/p0-t4-host-weight-staging.md) prepares
a source-bound CLI bundle, checks actual free storage, records content-free
progress and private evidence, and supervises an explicitly approved transfer.
It does not launch an engine, qualify hardware or stop rental billing. The
510.31 GB DeepSeek pack needs at least 561.35 GB free under its conservative
full-pack-plus-overhead policy; a 500 GB disk is insufficient.

## Read-only inventory

After a scoped hardware-run approval, transfer the committed `host/probe.py`
to the test node and run `python3 -I probe.py`. It reads Linux CPU/memory/device
facts and the NVIDIA GPU inventory. It installs nothing, changes no GPU state,
does not use the network, and does not need root. Retain its JSON and script
hash with the run evidence. A failing command is reported with a fixed error
code, not raw diagnostics. Hostnames, IPs, serials, UUIDs and environment values
are not emitted.

Command output is capped at 64 KiB across stdout/stderr, with a 15-second
command deadline and a one-second cleanup wait. The whole command process
group is killed on success or failure before reaping its leader. OS-level
uninterruptible I/O cannot be forcibly completed by this bound; cleanup failure
is an error, never permission to continue to an engine launch.

An inventory `pass` means only that eight B200s were reported and the bounded
inventory reads succeeded. It is **not attestation, confidentiality, topology
validation, capacity qualification or an engine-ready verdict**. Device paths
are hints, not proof. The report always keeps those trust decisions false.

Sources: [NVIDIA's nvidia-smi query interface](https://docs.nvidia.com/deploy/nvidia-smi/),
[Linux TSM report interface](https://www.kernel.org/doc/html/v7.2/admin-guide/abi-testing-files.html#abi-file-testing-configfs-tsm-report),
and [Verda's SEV-SNP and GPU attestation architecture](https://docs.verda.com/cpu-and-gpu-instances/confidential-computing/).
Verda's CPU TEE is AMD SEV-SNP, not the Intel TDX required by this repository's
current production verifier. Testing model compatibility on that hardware
does not grant a confidential verdict or satisfy the TDX phase exit.
These sources were read on 2026-10-19; no GPU/driver/engine settings are changed
by the probe.
