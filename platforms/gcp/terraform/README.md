# GCP GPU Lab Skeleton

P2.T1 / P3.T2 preparation only: reusable Terraform configuration with **mocked plans**, not
a real cloud plan, allocated capacity, boot validation or deployment approval.
No cloud resource, billing link, API enablement, quota request or IAM change was
performed to develop or test it. Never use the production project for model
bring-up. [Human gates](../../../docs/gates.md) still apply.

## Fixed Scope

- `instance_count` defaults to **0** and accepts only 0 or 1. Terraform reserves
  the name `count`, so this is the PLAN's count control under a valid variable
  name. All six resources share it: VPC, subnet, SSH firewall, dedicated service
  account, template and instance. Zero requests no resources in a fresh plan.
  On an existing deployment, an approved apply of zero **destroys** the entire
  managed lab, including boot disk and ephemeral Local SSD data; it is not a
  reversible stopped VM. A plan alone never stops anything.
- The default `a4-b200-ordinary` profile uses `a4-highgpu-8g`. The explicit
  `a3-h100-tdx` profile uses `a3-highgpu-1g`. Both are **Spot only**, `TERMINATE` on host maintenance,
  no automatic restart, and `DELETE` on termination. No on-demand fallback,
  reservations, managed group or autoscaler. Each run must supply a
  whole-second lifetime of 60 through 28,800 seconds (our conservative policy,
  not Google's service limit). Automatic termination can begin late and is not
  a substitute for separately verified teardown. Reapplying count one after
  deletion can allocate another VM; it requires new approval.
- A4 automatically attaches eight B200 GPUs and **32 Local SSDs**, totaling
  12,000 GiB. No `guest_accelerator` or explicit scratch disk is added. The only
  configured disk is a 100-GiB **Hyperdisk Balanced** auto-deleted boot disk.
  `pd-standard`, `pd-balanced` and `pd-ssd` are not supported A4 alternatives.
  No formatting, mounting, RAID, driver or model bootstrap is performed.
- The H100 profile requests **Intel TDX**, a 100-GiB NVMe **Balanced Persistent
  Disk**, Secure Boot, vTPM and integrity monitoring. It adds neither Local SSD
  nor a separate accelerator declaration. Only `us-central1-a`, `us-east5-a`
  and `europe-west4-c` are admitted, based on Google's October 22, 2026
  supported-configuration documentation. It is labeled `unverified-tdx`:
  requesting TDX does not prove CPU attestation, GPU CC enablement, driver
  signing, model integrity or end-to-end confidentiality. GPU mode and
  attestation must be verified after boot; no automatic downgrade is allowed.
- Separate custom-mode IPv4 VPC/subnet, one GVNIC and ephemeral external IPv4.
  Only TCP/22 from an explicit operator IPv4 **/32** is allowed, targeted to
  this VM's dedicated service account. No 443 or internal-traffic allow rule,
  no default VPC, no peering and no IPv6. Other ingress relies on GCP's implied
  deny rule; inherited organization/hierarchical firewall policies must be
  audited before any run. Egress is not restricted by this skeleton.
- Dedicated service account, explicitly attached with **empty OAuth scopes**,
  no roles, keys, IAM bindings or default Compute service account. This module
  creates the account only during a separately approved real apply. Existing
  organization policy or inherited grants are not evaluated by mocks.
- OS Login enabled, project SSH keys blocked, serial-port access disabled.
  Operator login/actAs permissions must already be separately approved and
  provisioned. No guest secrets, startup script or SSH key in Terraform.

## Inputs And Gates

Required inputs are `project_id`, `zone`, `operator_cidr`, `boot_image` and
`max_run_seconds`. `name` defaults to `nib-a4-lab`; choose a unique lab prefix
(for example `nib-h100-lab` for the H100 profile). `hardware_profile` defaults
to `a4-b200-ordinary`; selecting H100 alone still creates **zero** resources.
Region is derived from the zone. The project must already be isolated and
approved, with the required APIs, billing, quota, policy and operator access.
The provider does not create a project, enable an API or grant any role.

`boot_image` must be an explicit `projects/PROJECT/global/images/IMAGE` URI,
not a family or `latest`. An exact name is not a cryptographic image digest;
operators must verify its numeric image identity, provenance, license and
A4/GVNIC/OS Login compatibility in the approval record. There is deliberately
no default image or zone. The test image, project, zone and RFC 5737 SSH address
are synthetic and **must not be used to deploy**.

For H100, verify the exact image's `TDX_CAPABLE`, `UEFI_COMPATIBLE` and `GVNIC`
features, supported GPU image provenance, signed driver boot path and numerical
image identity before a real plan. Google documents GPU support for specific
Ubuntu and Container-Optimized OS images, not every `TDX_CAPABLE` image.
The module deliberately does not resolve a moving image family or assert that
an arbitrary custom image works. PLAN P3.T2 still requires stopping/reporting if
Google rejects the custom image; a vendor-image plumbing test is not proof of
our own image measurements. Quota and supported-zone membership are not
evidence of available capacity. The module does not request quota or rent a VM
when its software tests run.

TODO(verify): select and pin an A4-supported Linux image; review its bootstrap
and driver compatibility in P2.T2. Real provider/API acceptance, template
inheritance, automatic SSD inventory, SSH access and teardown are unproven.
TODO(verify): if Spot is unsuitable, separately source-review Flex-start's
queue, duration and lifecycle requirements. This increment does not expose it.
TODO(verify): complete G-GCP-PROJECT, G-GCP-QUOTA and a per-run G-RUN request,
then obtain an approved credentialed plan before any apply. Nothing here
authorizes changing IAM, quota or billing, or contacting a provider.

No hourly price or expected cash total is claimed. Before G-RUN, obtain current
region-specific A4 Spot pricing from Google's
[pricing page](https://cloud.google.com/products/compute/pricing) and calculator,
with a dated quote/source. Include Hyperdisk capacity/performance, external
IPv4, networking, any image license and teardown margin in the **estimate**.
State the inputs, arithmetic, hard deadline and verified cleanup procedure in
the private approval record. A runtime limit is not a spending cap.

## Software Verification

The preserved [2026-10-22 mock-plan receipt](../../../evidence/p2/2026-10-22-gcp-a4-mock-plan/README.md)
records 11 passing Terraform plan cases from the committed producer, with
byte-bound raw output. It is not cloud evidence.

Pins: Terraform **1.14.9**, `hashicorp/google` **8.7.0**, with signed-package
checksums locked for Linux amd64 and macOS arm64. CI installs the exact CLI.
The provider release source is commit
`40d7f42ae1094aeb14f8cb5c9fb24ab560b196cb`.

```sh
make terraform-check
# Preserve a new receipt directory, without overwriting previous evidence:
uv run --project tools --frozen python scripts/check_gcp_terraform.py \
  --evidence /tmp/nib-a4-mock-plan-new
```

The committed runner copies only reviewed HCL/lock/test inputs into a temporary
directory. Empty HOME, fixed CLI config and an environment allowlist exclude
credentials, auto.tfvars, existing state, provider reattachment and CLI overrides.
It runs `fmt -check`, `init -backend=false -lockfile=readonly`, `validate`, and
`test -verbose`/`test -json`. Every test run is `command = plan` with
`mock_provider "google"`; there is **no apply**, including mock apply.
Public registry/provider downloads need network access; plan execution is
credential-free and mocks all Google operations. Terraform/provider local IPC
must be permitted. Per-command time/output limits and process-group cleanup
use the existing repository helper.

`output.log` contains actual Terraform-rendered plans and all check output;
`tests.jsonl` contains the completion events; `sha256.json` binds source and
output bytes; `run.json` labels execution **fake**, never hardware. These plans
exercise default/explicit zero, count one, dependency bindings, A4/boot-disk/
network/identity/scheduling settings and rejected unsafe inputs. Mocks do not
execute the provider's real API logic or prove that Google accepts these
resources. The PLAN's real-cloud plan acceptance remains pending.
Terraform's canned "real infrastructure" and "after apply" wording in the raw
log does not change this: all plans use synthetic state and provider values.

## Primary Sources

Reviewed 2026-10-22; external documented behavior, not our hardware measurements:

- H100 addition reviewed 2026-10-25 against Google's
  [GPU-confidential VM creation guide](https://docs.cloud.google.com/confidential-computing/confidential-vm/docs/create-a-confidential-vm-instance-with-gpu)
  (**Standard or spot**, A3 High `a3-highgpu-1g` / `TDX`, Secure Boot) and
  [supported configurations](https://docs.cloud.google.com/confidential-computing/confidential-vm/docs/supported-configurations)
  (**Intel TDX**, NVMe Balanced PD; **NVIDIA Confidential Computing**, zones;
  **Supported images**, explicit image-family limits). The pinned provider's
  [confidential and shielded template blocks](https://github.com/hashicorp/terraform-provider-google/blob/40d7f42ae1094aeb14f8cb5c9fb24ab560b196cb/website/docs/r/compute_instance_template.html.markdown)
  supply the exact field names. All H100 verification here is mock-only.

- [Google A4 creation guide](https://docs.cloud.google.com/compute/docs/gpus/create-gpu-vm-a3u-a4),
  **Create VPC networks** (single GVNIC explicitly allowed for a quick test),
  **Spot**, and boot-image/disk requirements. This is a single-node quick-test
  skeleton, not the multi-NIC RDMA cluster configuration.
- [Accelerator-optimized machines](https://docs.cloud.google.com/compute/docs/accelerator-optimized-machines),
  **A4** configuration/storage and restrictions; [Local SSD](https://docs.cloud.google.com/compute/docs/disks/local-ssd)
  automatic attachment counts. Hyperdisk is not the Persistent Disk product;
  the REST disk attachment field `type: PERSISTENT` can still describe Hyperdisk.
- [VM runtime limits](https://docs.cloud.google.com/compute/docs/instances/limit-vm-runtime),
  **Restrictions** and **Duration**: Spot supported, DELETE explicit; minimum
  30 seconds and possible delayed termination. Our 60-second/eight-hour policy
  is intentionally narrower.
- [VPC firewall rules](https://docs.cloud.google.com/firewall/docs/firewalls),
  implied ingress deny and higher-priority policy evaluation;
  [OS Login](https://docs.cloud.google.com/compute/docs/oslogin/set-up-oslogin).
- [Instance-template CLI](https://docs.cloud.google.com/sdk/gcloud/reference/compute/instance-templates/create),
  `--no-scopes`; [pinned provider source](https://github.com/hashicorp/terraform-provider-google/blob/40d7f42ae1094aeb14f8cb5c9fb24ab560b196cb/google/services/compute/resource_compute_instance_template.go),
  service-account schema/expansion and scheduling fields.
- Pinned provider docs for [instance templates](https://github.com/hashicorp/terraform-provider-google/blob/40d7f42ae1094aeb14f8cb5c9fb24ab560b196cb/website/docs/r/compute_instance_template.html.markdown)
  and [instances from templates](https://github.com/hashicorp/terraform-provider-google/blob/40d7f42ae1094aeb14f8cb5c9fb24ab560b196cb/website/docs/r/compute_instance_from_template.html.markdown),
  including unique template link identity.
- [Terraform mock providers](https://developer.hashicorp.com/terraform/language/tests/mocking)
  and [test command](https://developer.hashicorp.com/terraform/cli/commands/test),
  plan-only tests and verbose output. These are not real-cloud plan evidence.
