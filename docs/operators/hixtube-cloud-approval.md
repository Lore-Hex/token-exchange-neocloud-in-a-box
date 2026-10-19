# Hixtube cloud approval requests

Prepared **2026-10-19 UTC**; proposals only, not authorization or cloud evidence.
This supplements [isolation requirements](hixtube-isolated-cloud.md) and
[human gates](../gates.md); the current real-model pilot is [Qwen](qwen-pilot.md).

## Current state

Parent previously verified `hixtube-neocloud` ACTIVE with billing disabled.
The parent's **05:15Z** inventory found 23 default services enabled, with
`compute.googleapis.com` and `cloudbilling.googleapis.com` absent. Its billing
recheck using the Hixtube quota project returned `SERVICE_DISABLED`: the earlier
`billingEnabled: false` is historical, not a fresh successful read. Disabled or
denied reads do not prove zero assets. The active credential remains unchanged.

## Immediate request: billing and API setup only

Request approval to enable Cloud Billing in **`hixtube-neocloud`**, verify that
it is still unlinked, attach the privately approved existing billing account,
enable Compute, then perform read-only inventory/image/quota checks there.
No VM, disk, address reservation, custom network, direct IAM grant, quota increase,
DNS, production change or workload is included. Compute activation can create
default network/firewall objects and service identities, with policy-dependent
automatic role grants; these side effects are part of this setup request and
must be inventoried before any workload. No billable allocation is proposed;
attaching billing nevertheless enables future charges and is not a spending cap.
Do not promise zero liability for assets not yet inventoried.
[Default network and firewall behavior](https://docs.cloud.google.com/vpc/docs/vpc#default-network).

Use an explicitly approved, already authenticated account; no login/config switch
or production quota consumer. Keep identifiers and raw outputs private. These
commands are **for execution only after that exact setup approval**:

```bash
set -euo pipefail
: "${SETUP_ACCOUNT:?approved existing account, supplied privately}"
: "${BILLING_ACCOUNT_ID:?approved active billing account, supplied privately}"
GCLOUD_BIN="$(command -v gcloud)"
test -x "$GCLOUD_BIN"
gc() {
  env -i HOME="$HOME" PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" \
    CLOUDSDK_CORE_SHOULD_PROMPT_TO_ENABLE_API=false \
    CLOUDSDK_CORE_DISABLE_USAGE_REPORTING=true CLOUDSDK_CORE_DISABLE_FILE_LOGGING=true \
    "$GCLOUD_BIN" --account="$SETUP_ACCOUNT" --project=hixtube-neocloud \
    --billing-project=hixtube-neocloud --quiet --no-log-http "$@" </dev/null
}
gc config list --all --format=json |
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
gc projects describe hixtube-neocloud --format='yaml(projectId,lifecycleState,parent)'
```

Require the intended ACTIVE project and a successful override check before
proceeding. The check emits presence-only results, never configuration values;
do not print complete auth/proxy sections, which can contain stored credentials.
The clean environment still uses the existing default credential/config store;
no named configuration is assumed. Never print tokens or enable HTTP logging.

```bash
gc services enable cloudbilling.googleapis.com
gc billing projects describe hixtube-neocloud --format=json |
  python3 -I -c 'import json,sys; s=json.load(sys.stdin); sys.exit(0 if s.get("billingEnabled") is False and not s.get("billingAccountName") else 1)'
gc billing projects link hixtube-neocloud \
  --billing-account="$BILLING_ACCOUNT_ID" --format='yaml(projectId,billingEnabled)'
gc services enable compute.googleapis.com
gc billing projects describe hixtube-neocloud --format='yaml(projectId,billingEnabled)'
gc services list --enabled --format='table(config.name,state)'
```

Use an exclusive change window for this project: the read-then-link guard is
not an atomic compare-and-set. If another operator is changing billing, stop.
Stop on any failure or changed billing association; never fall back to a
production consumer, silently move billing, enable extra APIs or grant roles.
The operator needs existing billing-association and service-enablement permissions;
missing permissions require a separate scoped request, not an Owner/Editor grant.
Sources: [billing permissions](https://docs.cloud.google.com/billing/docs/how-to/modify-project),
[link semantics](https://docs.cloud.google.com/sdk/gcloud/reference/billing/projects/link),
[service enablement](https://docs.cloud.google.com/service-usage/docs/enable-disable).

Compute activation creates Google-managed identities and a default Compute
service account; automatic grants depend on policy. Inspect the resulting IAM
and network inventory before another approval; do not reuse the default runtime
account or automatically remove service agents. No new runtime identity is needed
for offline inference: the later VM should use `--no-service-account --no-scopes`.
[Identity side effects](https://docs.cloud.google.com/compute/docs/access/service-accounts),
[VM flags](https://docs.cloud.google.com/sdk/gcloud/reference/compute/instances/create).
Flags and API-prompt suppression were checked against installed SDK **566.0.0**
help/source (`surface/{billing/projects/link,services/enable}.py`,
`command_lib/compute/instances/flags.py`, `core/properties.py`), not by cloud mutation.

## Later setup and run: not ready

After setup, use the [read-only preflight](hixtube-isolated-cloud.md#concise-read-only-preflight)
to establish regional/global quota and inventory. Quota does not guarantee Spot
capacity. Resolve a supported public Ubuntu image to its exact name, numeric ID,
features, kernel and license, retaining Hixtube as the quota consumer. An anonymous
Google Compute `images.getFromFamily` lookup for `ubuntu-os-cloud` /
`ubuntu-2404-lts-amd64` returned HTTP 401; no authenticated fallback was attempted.
**The image pin remains unresolved. No floating family is an approved run pin.**

Request network setup separately after inventory: one custom IPv4 VPC/subnet,
no production sharing/peering, SSH ingress only from the approved operator `/32`,
deny new external egress, and no public inference listener. Use an SSH tunnel to
loopback standard-TLS services; no DNS/listing, NAT, static IP, bucket or registry.
Stage a pinned Linux-amd64 runtime/model bundle over SSH, with no cloud credentials
or online package/model downloads. Bundle compatibility and transfer/egress limits
still need a committed implementation. Stateful firewall return traffic remains
allowed and is **not a byte cap**. [Firewall behavior](https://docs.cloud.google.com/firewall/docs/firewalls).

Proposed G-RUN scope: **one `c3-standard-4` TDX Spot VM in `us-central1-a`,
30 running minutes, no restart/replacement, no GPU**; real Qwen3-0.6B, synthetic
tests only. C3 TDX requires NVMe Balanced Persistent Disk. Standard TLS and a TDX
VM setting do not establish attestation; the real TDX + H100 verifier objective
is unchanged. [Supported configurations](https://docs.cloud.google.com/confidential-computing/confidential-vm/docs/supported-configurations).
Defer the full VM command until image/quota, input bundle and cleanup are reviewed.

## Provisional run estimate

**USD, Iowa, checked 2026-10-19; estimate, not an approved quote or hard cap.**
Carry forward the [C3 Spot base estimate](hixtube-isolated-cloud.md#hardware-and-prices)
of $0.079528/hour; CC adds `4 * 0.001155 + 16 * 0.0001549 = $0.0070984/hour`,
giving **$0.0866264/hour**. [Spot pricing](https://cloud.google.com/spot-vms/pricing?hl=en),
[CC pricing](https://cloud.google.com/confidential-computing/confidential-vm/pricing).

| Item | Assumption through cleanup | Estimate |
|---|---|---:|
| C3 + TDX | 0.5 hour * $0.0866264 | $0.04331320 |
| Boot `pd-balanced` | 30 GiB * $0.000136986/GiB-hour * 1 hour to deletion | $0.00410958 |
| Ephemeral external IPv4 | Spot $0.0025/hour * 1 hour allowance | $0.00250000 |
| Premium internet egress | At most 100 MiB total to North America, 100/1024 * $0.12/GiB | $0.01171875 |
| Subtotal | No free-tier credit assumed | **$0.06164153** |

[Disk pricing](https://cloud.google.com/compute/disks-image-pricing),
[IP and transfer pricing](https://cloud.google.com/vpc/network-pricing?hl=en).
Ingress is uncharged; replies/SSH overhead count toward outbound bytes. Ephemeral
IPv4 releases on stop/delete; disk charges continue until deletion. Reserving a
full hour of compute exposure raises this estimate to **$0.10495473**, not permission
to run longer. Proposed **$1 ceiling** still needs a final all-in estimate: image
license, price refresh, byte-limit enforcement, tax and cleanup failure exposure.
No NAT, snapshots, paid logging, load balancer or other storage is included/approved.
[Alerts-only budgets do not enforce a cap](https://docs.cloud.google.com/billing/docs/how-to/budgets).

## Teardown required in the eventual run approval

Require `--max-run-duration=30m --instance-termination-action=DELETE`, boot-disk
auto-delete and an independent operator watchdog. Google allows up to 30 seconds
extra before termination **begins**; neither timely deletion nor a dollar cap is
guaranteed. [Runtime limits](https://docs.cloud.google.com/compute/docs/instances/limit-vm-runtime).

1. Stop tests; collect bounded sanitized results without delaying deletion.
2. Delete the exact run VM; verify VM/boot-disk absence and ephemeral-IP release.
   Attempt owned-orphan cleanup independently after errors; authenticated absence
   is success, permission/API failure is unresolved cleanup, not absence.
3. Remove owned firewall rules, then subnet, then custom VPC after dependencies.
   No project-wide purge. Record remaining resources and fail the run if unresolved.
4. Keep APIs/billing available for cleanup; any later unlink/disable is separately
   approved, after successful inventory. API disabling alone can leave charges.
   [Disable-service cautions](https://docs.cloud.google.com/service-usage/docs/enable-disable).

**Next decision: approve only the immediate billing/API setup above. G-RUN remains
NOT READY pending image/quota discovery and the final bounded, costed run script.**
