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
| G-NVIDIA-LICENSE | NVIDIA Enterprise license for commercial confidential computing (covers the attestation SDK, NRAS, RIM, OCSP) | **now** | Development and PoC use is free; production is not. No public price. |
| G-GCP-PROJECT | Create a lab project on our existing billing account; enable the compute, cloudquotas and storage APIs; grant the agent's service account roles scoped to that project only | **now** | Never use `quill-cloud-proxy` (production) for experiments. |
| G-GCP-QUOTA | GPU quota in the lab project: 8× B200 (`a4-highgpu-8g`, Spot or flex-start) in a region with capacity; 1× H100 Spot in us-central1, us-east5 or europe-west4 (the zones with TDX + GPU confidential VMs) | **now** | Quota requests can take days. |
| G-INTEL-PCS | Intel PCS API subscription key for the PCCS on bare-metal hosts | **now** | Free registration on Intel's trusted-services portal. |
| G-NGC | NGC API key for NVIDIA's RIM and OCSP services | **now** | Used by `nib-gpu-gate` and `nib verify`. |
| G-RUN | Start any billable machine: GPU VMs, bare metal, disks, buckets | per run | State the hours, the price per hour and the teardown. |
| G-PUBLISH | Publish anything: GitHub Pages (catalog, trust page), GHCR images, tags and releases; also turn on private vulnerability reporting | per action | GHCR packages default to private; making them public is part of this gate. |
| G-TR-PROD | Merge or deploy changes to TrustedRouter production (quill-cloud-proxy, quill-router) | per PR | Merging quill-cloud-proxy main deploys the production enclave. |
| G-TR-CREDITS | Send requests through the TR gateway with an API key | per run | The provider checker runs directly against our own node and needs no gateway. |
| G-PRICE | Set route prices on TR | per route | Proposals in PLAN.md P6 and P7. |
| G-DNS | Create DNS names for node endpoints and the catalog | per name | Domain is an open question in PLAN.md §11. |

## Requests

| Date (UTC) | Gate | Request (PR link) | Cost estimate | Status |
|---|---|---|---|---|
