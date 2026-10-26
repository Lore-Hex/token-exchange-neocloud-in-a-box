# Host setup

Reserved for version-pinned BIOS, TDX, GPU CC and QEMU launch procedures.
Host commands require the hardware gates in PLAN.md. No BIOS/GPU settings or
confidential launch procedure is implemented here yet.

## Access Gate Before A Paid Launch

Before creating or replacing a paid host, verify the selected SSH credential
locally. A provider's saved public-key name does not prove that its private key
still exists. Keep the private key in an operator-owned durable directory,
outside temporary directories, disposable worktrees, source bundles and evidence.
Require directory mode `0700` and private-key mode `0600`; retain access through
the complete rental and evidence-retrieval period. Never upload the private key.

With `PRIVATE_KEY` set to that existing private key and `REGISTERED_PUBLIC_KEY`
set to a locally saved copy of the public key actually registered with the
provider, compare these SHA-256 fingerprints before ordering:

```sh
ssh-keygen -y -f "$PRIVATE_KEY" | ssh-keygen -E sha256 -lf -
ssh-keygen -E sha256 -lf "$REGISTERED_PUBLIC_KEY"
```

Both commands must succeed, and their `SHA256:` values must match. The first
derives the public identity from the private key, rather than trusting a sibling
`.pub` file or an agent's cached identity. It may ask for the key's passphrase.
OpenSSH documents these operations in [ssh-keygen(1)](https://man.openbsd.org/ssh-keygen.1)
(`-y`, `-l`, `-E`; read October 26, 2026). This checks local possession only,
not remote login or host authenticity.

Record the exact instance, selected key identity, hourly rate and independent
billing cutoff privately. After creation, verify the host identity using the
provider's authenticated channel and test access before transferring weights.
Do not disable host-key checking or reuse a terminated instance's address.
If access fails, use documented in-place recovery first. Replacement requires
approval for the exact instance and a fresh inventory of its data and volumes;
preserve all retained cache disks and other nodes. A stopped test process does
not stop cloud billing. Confirm provider-reported termination when teardown is
approved, and never remove a registered key still needed by another node.

## Private Test-Host Preparation

`prepare-lab.py` prepares operator-owned mode-0700 directories for an already
approved rental. Transfer this committed script, verify its SHA-256 against
the trusted local checkout, and run it using `python3 -I -S`. `init --root PATH`
creates the private root and `stage` subdirectory; the parent must already be
private and owned. This does not install packages or change GPU modes.

Checkout also requires the exact committed `host/probe.py` alongside the script.
Its pinned SHA-256 is checked before reusing only its bounded subprocess capture;
checkout does not call its inventory or NVIDIA routines. The historical default
remains `checkout --root PATH`, reading `source-45e7a09.bundle` and requiring the
unchanged `45e7a09` revision and bundle digest. It does not select current main.

For a current reviewed source transfer, the operator's approved receipt must
independently name the full 40-character commit and SHA-256 of a complete Git
bundle. Transfer those exact bytes as `source.bundle` into the private root:

```sh
python3 -I -S prepare-lab.py checkout --root "$PRIVATE_ROOT" \
  --revision "$APPROVED_REVISION" --bundle-sha256 "$APPROVED_BUNDLE_SHA256"
```

Both options are mandatory together, only on `checkout`; branches, abbreviated
revisions, automatic updates and receipt digests derived from received bytes are
not substitutes for the operator's approved values. No new receipt format or
signing authority is introduced. Both modes retain the 16 MiB transfer limit.
Missing objects or incremental bundles reject offline, without fetch or repair.

The input is read through no-follow descriptors, must be a single-link owned
regular file with stable identity, and is copied into a private verified snapshot
before Git runs. Replacing the transfer path cannot substitute Git's input.
The checkout root and its parent must be private/owned; symlink components reject.
A nonblocking root-directory lock serializes this helper. The operator must
exclude other writers; this is not protection against a malicious same-UID or
root process, kernel or Git binary.

Git starts with an empty private template, fixed environment, no transports,
hooks, ambient configuration, replacement refs, credentials or lazy fetching.
Only objects are imported, not remote configuration or bundle ref names. The
exact commit, complete object graph and regular-file tree are checked before
materialization. Archive bytes must match the committed blob IDs; symlinks,
submodules, omitted files and attribute-driven byte substitutions reject.
Limits are 4,096 files, 16 MiB per file, 64 MiB total source and 80 MiB archive
output. Ordinary Git command output is capped at 64 KiB and tree metadata at
1 MiB; stdout/stderr share each bound. Work shares a 180-second deadline with
at most 60 seconds per command, plus the existing supervisor's bounded cleanup.
No committed source file, checkout hook, package, image or GPU program executes.

`source` is new-only. Failures may leave a private incomplete directory, which
must not be used or automatically retried; existing destinations are never
overwritten or removed. Successful output retains the exact source revision,
bundle digest and script hash, not raw Git errors or operator paths. Detached
HEAD is installed only after every committed file has been verified.
Use the checkout's committed `host/record-probe.py` for actual hardware evidence.
`prerequisites --root PATH` reports only whether expected executables exist.
These setup results are not hardware qualification or a model-serving result.

## Offline guest-tools probe

`probe-mkosi-tools.py` reconstructs and checks the prepared tools image before
running seven fixed startup checks in the shared network-isolated Docker
sandbox. See [the procedure and limits](../docs/tasks/p3-t1-tools-startup.md).

`simulate-guest-apt.py --target mkosi-tools` reuses the offline resolver for
the expanded build-tools package lock, without installation. See the
[target-selection and evidence contract](../docs/tasks/p3-t1-tools-apt-plan.md).
This proves neither a guest build nor confidential hardware acceptance.

## Approved weight staging

The [bounded staging wrapper](../docs/tasks/p0-t4-host-weight-staging.md) prepares
a source-bound CLI bundle, checks actual free storage, records content-free
progress and private evidence, and supervises an explicitly approved transfer.
It does not launch an engine, qualify hardware or stop rental billing. The
510.31 GB DeepSeek pack needs at least 561.35 GB free under its conservative
full-pack-plus-overhead policy; a 500 GB disk is insufficient.

## Lab GPU Appraisal

The [pinned appraisal helper](../docs/tasks/p2-t3-nvidia-attestation.md) separates
offline package inspection, explicit two-package installation, and authorized
anonymous remote appraisal. Audit mode contains no readiness action. Eight authenticated
nonce-bound claims must match the local certificate identities; installation or
inspection alone cannot pass. The separate
[gated readiness mode and recorder](../docs/tasks/p2-t3-gated-readiness.md) require
fresh successful appraisal, unchanged inventory and existing persistence on all
eight devices before one explicit readiness transition. No actual transition or
confidential qualification is claimed by software tests.

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
