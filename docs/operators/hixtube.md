# Hixtube

Hixtube is our first operator of Token Exchange Neocloud-in-a-Box, selected by
Joseph on 2026-10-19. Other operators can use the same Apache-2.0 toolkit without
using this brand or domain.

| Item | Intended value |
|---|---|
| Provider display name | Hixtube |
| Provider slug | `hixtube` |
| Dedicated GCP project | `hixtube-neocloud` (active, billing disabled; no serving node) |
| Public site | `hixtube.com` |
| First-node API base | `https://api.hixtube.com/v1` (planned, not live) |
| First pilot | `qwen/qwen3-0.6b`, local real CPU inference, not listed |
| First large production target | `deepseek/deepseek-v4.1-flash-1017` |
| Native large-model ID | `deepseek-v4.1-flash-1017` |
| Second model | `z-ai/glm-5.3`, on a separately validated node |
| Current launch status | Not listed; no validated confidential inference node |

The API and site have different trust requirements. The site can serve public
documentation and a catalog; it must not receive inference requests. The API
must terminate TLS **inside the attested confidential VM**. A DNS record, ordinary
web certificate, or successful health check is not evidence of that property.

Joseph's 2026-10-19 update prioritizes a [small real Qwen pilot](qwen-pilot.md)
before renting the large-model node. Hixtube must have its own cloud project,
service accounts, credentials, network and resources. Sharing the Apache-2.0
toolkit is fine; sharing TrustedRouter production infrastructure is not.

The [initial recorder output](../../evidence/p3/2026-10-19-qwen-local-1/README.md)
captured passing local inference checks but had an invalid metadata envelope.
It is retained as a draft, not accepted evidence; a new schema-valid run is
required. No public service or confidential attestation is claimed.

## Check the domain

The read-only command checks address resolution for the apex and `api` hostname:

```sh
go run ./cmd/nib-domain-check --domain hixtube.com
```

It reports counts, not infrastructure addresses or raw resolver errors. It does
not contact either web server, send credentials, create DNS records or start a
machine. A nonzero exit means at least one hostname does not resolve or its
lookup failed. A zero exit means only that both names resolve through the local
resolver: it does **not** validate delegation, certificates, HTTP, inference,
attestation, IP ownership or marketplace readiness.

Keep each live check's script-produced evidence under `evidence/p1/`. After
committing the checker and recorder (the source tree must be clean), run:

```sh
python3 scripts/record-domain-check.py --domain hixtube.com --slug hixtube-dns
```

This explicitly creates a new local evidence directory, recording the source
revision, timestamps and exact sanitized DNS report. It builds the checker and
evidence helper from a committed Git snapshot, excluding ignored/untracked Go
files, overlays, workspaces and ambient build flags. Local Git replacement refs
are disabled. Build dependencies may need to be downloaded; the DNS checker
itself makes no HTTP requests. Use Python 3.12 or later for the recorder's
explicit archive extraction filter. It never overwrites an existing run.
Use a new slug for a subsequent check. The checker is reusable
for any operator; its tests do not depend on Hixtube's DNS.

The [2026-10-19 snapshot-built check](../../evidence/p1/2026-10-19-hixtube-dns-v3/run.json)
failed: [both names had no address](../../evidence/p1/2026-10-19-hixtube-dns-v3/output.log).
This is a DNS setup blocker, not an inference outage or attestation failure.
The earlier working-tree-built failure is retained as historical evidence,
not as proof that Git cleanliness alone excludes ignored build inputs.

## Connect the real endpoint

1. Finish the P1 fake-node checks and merge their independently reviewed
   dependencies. Never expose the `nibfake` runtime publicly or rename the
   synthetic catalog fixture to Hixtube.
2. Obtain the separate lab project and run approvals, and complete P2/P3.
   The existing production project hosting the DNS zone is **not** the GPU
   experiment project. Do not reuse a TrustedRouter production VM.
3. Complete P4/P5 with the actual confidential hardware: verified boot and
   weight integrity, CPU and every GPU/NVSwitch evidence, freshness and TLS-key
   binding, negative controls, and provider-check Tiers 1-4.
4. Record the exact approved node address in private deployment configuration.
   Create the API A/AAAA record only for that node or a reviewed L4 passthrough.
   Do not use a Cloud Run, CDN, ordinary HTTPS load balancer or TrustedRouter
   frontend that decrypts TLS before the confidential VM. Do not publish an
   IPv6 record without testing its complete path.
5. Run verification against the public hostname on the same TLS connection
   used for inference. Check the expected model and release, not merely a valid
   CPU quote. Commit redacted evidence; keep keys and infrastructure IPs out of
   Git. Test cold starts and rejected/tampered evidence as well as success.
6. With actual measured capacity, agreed prices and applicable commercial CC
   entitlement, publish the real catalog and add `hixtube` through the normal
   reviewed TR/NYTE provider process. Until then publish no callable route or
   confidential badge. Adding GLM later needs its own model-specific evidence.

The current synthetic catalog prices are test fixtures, not approved Hixtube
prices. The Hixtube naming/domain request does not grant GPU spending, unrelated
production changes or release publication approval; the
[existing gates](../gates.md) and phase dependencies still apply. Domain setup
does not need another naming decision; it needs a validated target first.
