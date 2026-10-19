# Human gates

A gate is a step an agent may prepare but not take. Joseph approves each one,
per action. An agent asking for a gate appends a row to **Requests** with a link
to the PR that has the exact command, the cost per hour, the expected duration
and the total. Approval is recorded by Joseph in that PR. An approval covers
that request only.

## Gate definitions

| Gate | What | Start | Notes |
|---|---|---|---|
| G-HW-RENT | Rent (or buy) an HGX 8×B200 server with TDX enabled in the BIOS and NVIDIA multi-GPU CC allowed | **now** | Questionnaire and candidates in PLAN.md §3.4. Lead time is weeks. |
| G-NVIDIA-LICENSE | Confirm commercial CC entitlement and applicable vendor/service terms | **now** | Free development is not authorization to offer/distribute a service. SDK source licensing is separate; see [hardware licensing](hardware.md#licenses-and-action-gates). No quote obtained. |
| G-GCP-PROJECT | Create a lab project on our existing billing account; enable the compute, cloudquotas and storage APIs; grant the agent's service account roles scoped to that project only | **now** | Never use `quill-cloud-proxy` (production) for experiments. |
| G-GCP-QUOTA | GPU quota in the lab project: 8× B200 (`a4-highgpu-8g`, Spot or flex-start) in a region with capacity; 1× H100 Spot in us-central1, us-east5 or europe-west4 (the zones with TDX + GPU confidential VMs) | **now** | Quota requests can take days. |
| G-INTEL-PCS | Intel PCS API subscription key for the PCCS on bare-metal hosts | **now** | Free registration on Intel's trusted-services portal. |
| G-NGC | NGC API key for NVIDIA's RIM and OCSP services | **now** | Used by `nib-gpu-gate` and `nib verify`. |
| G-RUN | Start any billable machine: GPU VMs, bare metal, disks, buckets | per run | State the hours, the price per hour and the teardown. |
| G-PUBLISH | Publish anything: GitHub Pages (catalog, trust page), GHCR images, tags and releases; also turn on private vulnerability reporting | per action | GHCR packages default to private; making them public is part of this gate. |
| G-TR-PROD | Merge or deploy changes to TrustedRouter production (quill-cloud-proxy, quill-router) | per PR | Merging quill-cloud-proxy main deploys the production enclave. |
| G-TR-CREDITS | Send requests through the TR gateway with an API key | per run | The provider checker runs directly against our own node and needs no gateway. |
| G-PRICE | Set route prices on TR | per route | Proposals in PLAN.md P6 and P7. |
| G-DNS | Create DNS names for node endpoints and the catalog | per name | Joseph selected `hixtube.com`; see the [Hixtube launch checklist](operators/hixtube.md). Address records still require an identified, validated target. |

## Software coordination

On 2026-10-19 Joseph authorized continued software work and merging. Source-backed
factual corrections include B300's documented CC SKU capacity and all-file model
totals. Task branches may be pushed and PRs prepared. Self-merge is authorized
only after CI is green and independent review of the final head is clean,
with findings resolved.

This authorization does not grant any resource, spending, production, release,
DNS, settings or third-party-contact gate. It does not change phase dependencies.
In particular, P1 still requires merged P0.T1-T3.

## Requests and owner-reported status

Joseph's subsequent instruction, "make it public, integrate to TR", authorizes
public Hixtube pilot preparation and the scoped TR integration. It does not
approve an unspecified cloud bill or a price. Pending questions propose one
small CPU VM for at most 24 hours (estimated under $5, subject to final inventory
and bounded deployment review) and $0.10 per million input/output tokens.
No GPU spending, new confidentiality claim or unrelated production change is
included. The [service runbook](operators/qwen-service.md) records activation
order; earlier setup requests below remain historical rather than silently
superseded cost approvals.

| Date (UTC) | Gate | Request (PR link) | Cost estimate | Status |
|---|---|---|---|---|
| 2026-10-19 | G-HW-RENT | Joseph reports existing HydraHost email concerning eight B200 GPUs; [P0.T2 hardware questionnaire](hardware.md#procurement-and-questionnaire), [PR #2](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/pull/2) | Not quoted | Outreach performed by Joseph; hardware, terms and qualification pending. No rental, spending or further contact approved. |
| 2026-10-19 | G-DNS | Joseph selected Hixtube, reports delegating `hixtube.com` to GCP, and requested making the provider work; [launch checklist](operators/hixtube.md) | Read-only checks; no new resources | Hixtube naming/domain setup authorized; address publication awaits a validated target. No serving IP, GPU rental, lab project, quota, route price or commercial CC entitlement established by this instruction. No records or production routes changed. |
| 2026-10-19 | G-GCP-PROJECT / G-RUN | Joseph requested a small Qwen first and a totally separate project; [local pilot](operators/qwen-pilot.md) | Local CPU test has no cloud/API charge; cloud quote pending | Separate empty project creation authorized and completed: `hixtube-neocloud`, ACTIVE, billing disabled. Existing personal login used explicitly; active ops credential/configuration unchanged. GCP automatically enabled its base Cloud APIs service. No production API, billing, IAM or resources changed; no compute started. Dedicated runtime identity, any paid setup and bounded cloud run still require their gates. |
| 2026-10-19 | G-GCP-PROJECT (setup subset) | [PR #13](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/pull/13), [exact billing/API setup proposal](operators/hixtube-cloud-approval.md#immediate-request-billing-and-api-setup-only) | No billable allocation proposed; billing attachment permits future charges and Compute activation has documented identity/network side effects | NOT APPROVED. Request only Cloud Billing API, guarded billing attachment, Compute API and read-only discovery in Hixtube. No VM/run authorization. G-RUN is NOT READY until image/quota, offline bundle, bounded execution, teardown and the final all-in estimate are reviewed. |
