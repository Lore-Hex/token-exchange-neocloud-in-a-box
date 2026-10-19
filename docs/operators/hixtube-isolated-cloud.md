# Hixtube isolated cloud preflight

Official Google sources checked **2026-10-19 UTC**. This document is research,
not deployment approval, a completed cloud preflight or hardware evidence.
The parent verified dedicated project **`hixtube-neocloud` is ACTIVE** and
**`billingEnabled: false`**, separate from TrustedRouter production; the active
credential was untouched. A later **05:15Z read-only service inventory** found 23
default services enabled; Compute and Cloud Billing API were absent. A billing
recheck using Hixtube as the quota project returned `SERVICE_DISABLED`, not a new
billing result. The earlier `billingEnabled: false` remains the last successful
read. This is not a completed isolation or zero-asset audit.
Keep billing/user identifiers and the immutable project number out of Git.
No further cloud mutations or spending are authorized by this document.

## Recommended sequence

Use the local real Qwen3-0.6B CPU pilot first; current status belongs in
`docs/operators/qwen-pilot.md` in the parent integration. Standard TLS is not
attestation. This sidecar asserts no accepted pilot proof or confidential result.
After separate setup/run approval, prefer **C3-4 TDX Spot** for a bounded,
synthetic CPU test: it is the cheapest priced option in this C3/H100 comparison,
not a claim about every available TEE. Preserve the final **TDX + H100 and
multi-GPU/NVSwitch verification** objective; a CPU-only pass cannot satisfy it.
Never remove expected GPUs from a profile or use `nibfake` to obtain acceptance.

## Hardware and prices

| Option | Supported configuration and limits |
|---|---|
| `c3-standard-4` | 4 vCPUs, 16 GiB, Sapphire Rapids; CPU-only TDX, including `us-central1-a`; no GPU/live migration. TDX restrictions specify NVMe Balanced Persistent Disk. [C3](https://docs.cloud.google.com/compute/docs/general-purpose-machines#c3_series), [CC matrix](https://docs.cloud.google.com/confidential-computing/confidential-vm/docs/supported-configurations). |
| `a3-highgpu-1g` | One H100 SXM 80 GB, 26 vCPUs, 234 GiB RAM, 750 GiB Local SSD; TDX + NVIDIA CC. Spot/Flex-start only, not on-demand; CC zones `us-central1-a`, `us-east5-a`, `europe-west4-c`. [A3](https://docs.cloud.google.com/compute/docs/accelerator-optimized-machines), [GPU guide](https://docs.cloud.google.com/confidential-computing/confidential-vm/docs/create-a-confidential-vm-instance-with-gpu), [CC matrix](https://docs.cloud.google.com/confidential-computing/confidential-vm/docs/supported-configurations). |

**Image gap:** Google's A3 GPU guidance names `ubuntu-2204-lts` or
`cos-tdx-113-lts`; another `TDX_CAPABLE` image is not automatically GPU-supported.
Qualify PLAN's Ubuntu 24.04/custom image before H100 use. Pin exact image/kernel/
driver versions; the guide recommends open NVIDIA drivers 580+, LKCA and
persistence setup, which are prerequisites, not appraisal evidence.
[Supported images](https://docs.cloud.google.com/confidential-computing/confidential-vm/docs/supported-configurations),
[GPU setup](https://docs.cloud.google.com/confidential-computing/confidential-vm/docs/create-a-confidential-vm-instance-with-gpu).

**Estimate: USD/hour, Iowa (`us-central1`), checked 2026-10-19**, public list
prices without negotiated discounts/commitments; refresh before run approval.

| Configuration | Base VM | CC surcharge | Compute + CC estimate |
|---|---:|---:|---:|
| C3-4 on-demand | $0.201608 | `4 * 0.0033982 + 16 * 0.0004555 = $0.0208808` | **$0.2224888** |
| C3-4 Spot | $0.079528 | `4 * 0.001155 + 16 * 0.0001549 = $0.0070984` | **$0.0866264** |
| Single-H100 A3 Spot | $6.599276301 | $0.4391592 | **$7.038435501** |

Sources: [C3 on-demand](https://cloud.google.com/products/compute/pricing/general-purpose?hl=en),
[Spot](https://cloud.google.com/spot-vms/pricing?hl=en),
[CC surcharges](https://cloud.google.com/confidential-computing/confidential-vm/pricing).
H100 base includes CPU/RAM/bundled Local SSD; do not count them twice.
Flex-start CC surcharge is **$0.512173102/hour**, but its single-H100 base/total
was not established: do not extrapolate an exact quote from eight GPUs.
[Accelerator pricing](https://cloud.google.com/products/compute/pricing/accelerator-optimized).
Extras excluded: persistent disks, images/snapshots, IP/NAT/network transfer,
registries/storage, logs, licenses, tax and applicable entitlement. Spot prices
vary; quota is not capacity. H100 needs regional preemptible H100 and global GPU
quota. [Quota requirements](https://docs.cloud.google.com/confidential-computing/confidential-vm/quotas).

**Proposed, not approved:** one C3-4 Spot VM, no automatic retry/replacement,
maximum **30 running minutes**, **USD 1.00 all-in ceiling**.
Compute + CC: `0.5 * $0.0866264 = $0.0433132`; remaining allowance:
`$1.00 - $0.0433132 = $0.9566868`. Itemize every extra through verified deletion
before approval; if the conservative total exceeds $1, stop and re-request.
This is neither an enforced cloud cap nor permission to spend. The
[setup approval request](hixtube-cloud-approval.md) separates immediate API/billing
setup from the still-unready VM run and itemizes a provisional run estimate.
[Alerts-only budgets do not cap spending](https://docs.cloud.google.com/billing/docs/how-to/budgets).

## Required isolation

These are future setup requirements, not controls verified as installed.

- **Project/IAM:** lab resources only; review project and inherited ancestor
  policies. No production roles, cross-project impersonation or shared pipeline.
- **Service accounts:** separate bootstrap/deployer/runtime identities, least
  privilege; no Owner/Editor shortcut, default SA reuse or downloaded JSON keys.
  Prefer no attached runtime SA when preloaded inference needs no Google APIs.
- **Secrets/artifacts:** lab-only TLS keys, secrets, storage and registries;
  no production credentials/ADC in images, metadata or source. Pin all inputs.
- **Network:** dedicated VPC/subnet; no production Shared VPC, peering, VPN,
  Interconnect or private-service connections. Review routes/effective firewalls;
  default-deny ingress, reviewed egress, inference engine loopback-only.
- **DNS/TLS:** no production DNS changes or runtime DNS privilege. Any lab
  delegation/publication is separately gated; later CC TLS terminates in guest.
- **Billing:** separate project accounting; shared billing still shares payer/
  admins. Strict financial isolation needs an approved separate billing boundary.
- **Data/cleanup:** synthetic prompts only; no body/header/query/path logs,
  production traffic or gateway credits. Verify removal of every created
  resource, including disks/addresses; stopping a VM is insufficient.

Sources: [IAM inheritance](https://docs.cloud.google.com/iam/docs/resource-hierarchy-access-control),
[SA boundaries](https://docs.cloud.google.com/iam/docs/best-practices-service-accounts),
[key risks](https://docs.cloud.google.com/iam/docs/best-practices-for-managing-service-account-keys).

## Concise read-only preflight

Templates only, not cloud checks executed here. Use Bash and an explicitly
provided, already authenticated account; do not log in, switch active accounts
or assume a named configuration. Keep raw outputs private; never print tokens,
read secret payloads, enable HTTP logging or send source/keys to Claude.

```bash
set -euo pipefail
: "${PREFLIGHT_ACCOUNT:?provide the approved existing account privately}"
GCLOUD_BIN="$(command -v gcloud)"
test -x "$GCLOUD_BIN" || exit 1
ro() {
  env -i HOME="$HOME" PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" \
    CLOUDSDK_CORE_SHOULD_PROMPT_TO_ENABLE_API=false \
    CLOUDSDK_CORE_DISABLE_USAGE_REPORTING=true CLOUDSDK_CORE_DISABLE_FILE_LOGGING=true \
    "$GCLOUD_BIN" --account="$PREFLIGHT_ACCOUNT" --project=hixtube-neocloud \
    --billing-project=hixtube-neocloud --quiet --no-log-http "$@" </dev/null
}
ro version
ro config list --all --format=json |
  python3 -I -c '
import json, sys
c = json.load(sys.stdin)
auth = c.get("auth", {})
overrides = ("access_token_file", "credential_file_override", "impersonate_service_account",
             "client_id", "client_secret", "login_config_file")
blocked = any(auth.get(k) for k in overrides)
blocked = blocked or str(auth.get("disable_credentials", "false")).lower() == "true"
blocked = blocked or any(c.get("proxy", {}).values()) or any(c.get("api_endpoint_overrides", {}).values())
print("STOP: configured auth/proxy/endpoint override" if blocked else "Configuration override check passed")
sys.exit(1 if blocked else 0)'
```

The clean environment still reads the existing default config/credential store.
Stop on configured impersonation, credential overrides, proxy or API endpoints.
Do not dump their values: auth and proxy sections can contain credentials. The
isolated Python check above prints only a fixed pass/stop message.
API-prompt suppression was checked in SDK **566.0.0** `core/properties.py` and
`api_lib/{util/apis.py,compute/batch_helper.py}`; recheck on upgrade.
[CLI flags](https://docs.cloud.google.com/sdk/gcloud/reference); `--quiet` alone is not a safety boundary.

```bash
ro projects describe hixtube-neocloud --format='yaml(projectId,projectNumber,lifecycleState,parent)'
ro billing projects describe hixtube-neocloud --format='yaml(projectId,billingEnabled)'
ro services list --enabled --format='table(config.name,state)'
ro projects get-ancestors-iam-policy hixtube-neocloud --include-deny --format=json
ro iam service-accounts list --format='table(email,disabled)'
```

Match the project number privately. Any missing permission/disabled API blocks
that inspection, not proof of absence or permission to repair/fall back to prod.
Sources: [billing](https://docs.cloud.google.com/sdk/gcloud/reference/billing/projects/describe),
[services](https://docs.cloud.google.com/sdk/gcloud/reference/services/list),
[ancestor policies](https://docs.cloud.google.com/sdk/gcloud/reference/projects/get-ancestors-iam-policy).

Only if the inventory confirms Compute is already enabled, inspect without allocation:

```bash
ro compute machine-types describe c3-standard-4 --zone=us-central1-a --format='yaml(name,guestCpus,memoryMb)'
ro compute machine-types describe a3-highgpu-1g --zone=us-central1-a --format='yaml(name,guestCpus,memoryMb,accelerators)'
ro compute regions describe us-central1 --format='json(quotas)'
ro compute project-info describe --format='json(quotas)'
```

[Machine lookup](https://docs.cloud.google.com/sdk/gcloud/reference/compute/machine-types/describe),
[regional](https://docs.cloud.google.com/sdk/gcloud/reference/compute/regions/describe) and
[global quotas](https://docs.cloud.google.com/sdk/gcloud/reference/compute/project-info/describe)
do not establish capacity, image compatibility, model fit or real attestation.

## Next approval

Keep billing disabled until explicit setup approval. Parent owns PLAN/ADR/gates
and committed create/test/teardown scripts; use the [existing gates](../gates.md)
instead of treating project creation as spending, IAM, API or DNS permission.
Require real CPU collateral/policy/freshness/TLS-key appraisal, then NVIDIA
chain/RIM/OCSP and expected device counts with tamper negatives. Missing appraisal
stays fail/partial. No `nibfake` bypass, hardware claim or provider listing;
follow the [Hixtube launch checklist](hixtube.md) for later publication.
