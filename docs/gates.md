# Human gates

A gate is a step an agent may prepare but not take. Joseph approves each one,
per action. An agent asking for a gate records the exact command, hourly cost,
expected duration and total in the operator's deployment workspace, with a
sanitized request link here when needed for toolkit work. Keep project-specific
identifiers and secrets out of public PRs. Approval is recorded by Joseph on
the scoped request and covers that request only.

## Gate definitions

| Gate | What | Start | Notes |
|---|---|---|---|
| G-HW-RENT | Rent (or buy) an HGX 8×B200 server with TDX enabled in the BIOS and NVIDIA multi-GPU CC allowed | **now** | Questionnaire and candidates in PLAN.md §3.4. Lead time is weeks. |
| G-NVIDIA-LICENSE | Confirm commercial CC entitlement and applicable vendor/service terms | **now** | Free development is not authorization to offer/distribute a service. SDK source licensing is separate; see [hardware licensing](hardware.md#licenses-and-action-gates). No quote obtained. |
| G-GCP-PROJECT | Create an isolated operator-owned lab project on an approved billing account; enable required APIs; grant roles scoped to that project only | **now** | Never use TrustedRouter production for experiments. |
| G-GCP-QUOTA | GPU quota in the lab project: 8× B200 (`a4-highgpu-8g`, Spot or flex-start) in a region with capacity; 1× H100 Spot in us-central1, us-east5 or europe-west4 (the zones with TDX + GPU confidential VMs) | **now** | Quota requests can take days. |
| G-INTEL-PCS | Intel PCS API subscription key for the PCCS on bare-metal hosts | **now** | Free registration on Intel's trusted-services portal. |
| G-NGC | NGC API key for NVIDIA's RIM and OCSP services | **now** | Used by `nib-gpu-gate` and `nib verify`. |
| G-RUN | Start any billable machine: GPU VMs, bare metal, disks, buckets | per run | State the hours, the price per hour and the teardown. |
| G-PUBLISH | Publish anything: GitHub Pages (catalog, trust page), GHCR images, tags and releases; also turn on private vulnerability reporting | per action | GHCR packages default to private; making them public is part of this gate. |
| G-TR-PROD | Merge or deploy changes to TrustedRouter production (quill-cloud-proxy, quill-router) | per PR | Merging quill-cloud-proxy main deploys the production enclave. |
| G-TR-CREDITS | Send requests through the TR gateway with an API key | per run | The provider checker runs directly against our own node and needs no gateway. |
| G-PRICE | Set route prices on TR | per route | Proposals in PLAN.md P6 and P7. |
| G-DNS | Create DNS names for node endpoints and the catalog | per name | Operator-owned name and identified, validated target; see the [operator guide](operators/trustedrouter.md). |

## Software coordination

On 2026-10-19 Joseph authorized continued software work and merging. Source-backed
factual corrections include B300's documented CC SKU capacity and all-file model
totals. Task branches may be pushed and PRs prepared. Self-merge is authorized
only after CI is green and independent review of the final head is clean,
with findings resolved.

This authorization does not grant any resource, spending, production, release,
DNS, settings or third-party-contact gate. It does not change phase dependencies.
In particular, P1 still requires merged P0.T1-T3.

## Operator Deployment Records

Keep individual operator projects, credentials, DNS state, approval requests and
commercial terms outside this reusable repository. Removing those records from
the current tree does not approve or cancel any action. Original historical
evidence remains unchanged in Git history; it is not evidence for another
operator or the current release.

For a new run, record the exact target, existing permissions, image pins,
budget, expiry and verified cleanup in the operator's deployment workspace.
The [service runbook](operators/qwen-service.md) defines the pilot sequence;
the [hardware questionnaire](hardware.md#procurement-and-questionnaire) defines
qualification. No cloud/GPU allocation or production activation is granted by
an example configuration, a successful local test or a source-code merge.

The full confidential-node hardware qualification and run gates remain pending.
TrustedRouter owns customer billing/payments; operators still need approval and
a budget for their own hardware and infrastructure costs.

The [P2.T1 Terraform skeleton](../platforms/gcp/terraform/README.md) is software
only, tested with a mock provider. G-GCP-PROJECT, G-GCP-QUOTA and per-run G-RUN
remain required before any real deployment. No billing link, IAM grant, quota
request, resource allocation or cloud plan is authorized by its mock receipts.
