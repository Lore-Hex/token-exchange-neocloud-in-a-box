# Host setup

Reserved for version-pinned BIOS, TDX, GPU CC and QEMU launch procedures.
Host commands require the hardware gates in PLAN.md. No host mutation or
confidential launch procedure is implemented here yet.

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
