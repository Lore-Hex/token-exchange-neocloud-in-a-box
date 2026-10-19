# Sell Inference Through TrustedRouter

Run the model. TrustedRouter handles customer accounting, credit enforcement,
billing, payments and provider payouts under your onboarding agreement. This
toolkit supplies the inference endpoint, not a second business platform.

**Current scope:** the small Qwen CPU pilot works locally over ordinary TLS.
The full confidential GPU node is still in development. No deployment command
in this guide grants a confidential, E2EE or ZDR classification.

The [local Linux proof](../../evidence/p3/2026-10-19-provider-neutral-linux/run.json)
exercises configured provider metadata and passes all 18 service checks with
real Qwen inference, streaming usage, retirement and shutdown. It runs offline
in local Docker, not on a public node; it does not test payment settlement.

## 1. Configure Your Endpoint

Keep deployment files outside Git. Start from
[examples/provider.json](../../examples/provider.json):

```json
{
  "id": "acme",
  "status_url": "https://api.example.invalid/healthz",
  "support_contact": "mailto:support@example.invalid",
  "incident_contact": "mailto:ops@example.invalid",
  "regions": ["US"]
}
```

Replace the sample identity, URLs, contacts and region with your own. Declare
where inference actually runs, not where the company is headquartered. These
fields are public catalog metadata, not credentials or verified privacy claims.
The same binary works for different operators without changing source.

For the Qwen pilot, pass this file with `--provider-config`, explicit
`--input-price` and `--output-price` decimal strings in USD per million tokens,
your TLS certificate/key and a dedicated inference API-key file. Follow the
[service runbook](qwen-service.md) for the full command, bounded runtime,
capacity and retirement controls. The prices are your proposed/agreed supply
rates, not permission to bill customers locally. Do not use sample rates as
commercial defaults.

Use an isolated operator-owned project and runtime identity. Do not connect the
node to TrustedRouter's internal networks, databases, secret stores or payment
accounts. Public ingress is TLS only; keep the engine and API guard on loopback.
Keep private keys owner-readable only and arrange rotation with TrustedRouter.

### Dedicated Long API Key

Inference still requires a bearer key even though TrustedRouter handles billing.
Generate **32 cryptographically random bytes as 64 hexadecimal characters**
(256 bits of entropy). The service accepts 64-256 ASCII letters, digits, `_` or
`-`, with one optional trailing newline in the key file. Length validation
cannot prove randomness: never use a memorable password, repeated characters
or a key copied from an example.

In a private deployment directory outside Git, create a new key file with
OpenSSL. This subshell uses owner-only permissions and refuses to overwrite an
existing file. The key is not printed or placed in command-line arguments:

```sh
(
  umask 077
  set -C
  openssl rand -hex 32 > ./trustedrouter-inference.key
)
```

Check that the command succeeded before mounting that file as `--api-key-file`.
Keep the parent directory private too. Hand the key to TrustedRouter through the
agreed secure channel, never email/chat, and use it only for this provider's
inference. Catalog/health remain public; inference rejects absent, wrong or
duplicate credentials. Keys are compared in constant time and never logged.
Rotation replaces the key file and restarts the service in coordination with
TrustedRouter; the running process snapshots its key at startup. Existing keys
shorter than 64 characters must be replaced before upgrading.

## 2. Check the API

The pilot exposes:

| Endpoint | Purpose |
|---|---|
| `GET /v1/models` | Catalog v2: models, explicit prices, capabilities, deadlines and lifecycle |
| `POST /v1/chat/completions` | Bearer-authenticated inference, streaming or non-streaming |
| `GET /healthz` | Current readiness of the local API and engine |

Run the pinned checker directly against your endpoint before applying. Use the
version already hash-locked in `scripts/e2e/requirements.lock`, not an
unreviewed tool upgrade. From the repository root:

```sh
uv venv --python 3.12 build/provider-check
uv pip install --python build/provider-check/bin/python --require-hashes \
  -r scripts/e2e/requirements.lock
build/provider-check/bin/trustedrouter-provider-check \
  --base-url https://api.your-domain.example/v1 \
  --model qwen/qwen3-0.6b \
  --catalog-url https://api.your-domain.example/v1/models \
  --tier 4 --json provider-report.json
```

Replace the example origin. Supply `TR_PROVIDER_API_KEY` through your local
secret manager/environment for the checker; never put it in a command argument,
Git, the catalog or the report. Checks make real inference requests on your
hardware. Run them only within your approved capacity/budget. This is not a
request through the TrustedRouter gateway.

Usage is part of the API contract: preserve integer `prompt_tokens`,
`completion_tokens` and `total_tokens`; preserve applicable cache counters for
models that support them. Streaming must send final usage when requested with
`stream_options.include_usage`. Do not synthesize zero counts for a missing
report, treat a broken stream as success, or calculate customer charges here.
The pilot's engine does not offer billable prompt caching and its catalog says so.

## 3. Connect to TrustedRouter

Use the [marketplace application](https://trustedrouter.com/providers/marketplace)
for the current required business, location, privacy and technical information.
Send your endpoint, catalog URL and redacted checker report to
`providers@trustedrouter.com`. Arrange a separate secure handoff for a revocable
key restricted to inference; never email the key.

Agree supply pricing, settlement/payout terms and operational contacts during
onboarding. TrustedRouter validates the endpoint, configures discovery and
routing, and verifies billing on a controlled request before public activation.
It is not an automatic listing just because `/v1/models` responds. No payment
processor, bank credentials, invoices or customer account database belong on
the inference node.

Publish only callable models with truthful capabilities and prices. Coordinate
price changes, retirement cutoffs and withdrawal before shutting down a node.
Do not silently replace a model behind an unchanged ID. For the finite pilot,
follow the [activation and withdrawal sequence](qwen-service.md#activation-order).

## Basic Operations, No Content Logging

The pilot supervisor emits fixed JSON lifecycle events (`ready`, `stopped`) and
exposes health through `/healthz`. Readiness reports the configured model,
context length and concurrency limit. A stop reports pass/fail without raw
engine errors. Engine and proxy process output is discarded. There is no
telemetry SaaS dependency or local database.

Monitor health and process exits with your existing infrastructure tools. If
you later add counters, keep only bounded aggregates such as requests, errors,
latency and capacity. Use fixed route/status labels, not customer IDs, keys,
request IDs, IPs, paths or arbitrary model names from requests. Do not log
prompts, outputs, bodies, headers, query strings or raw exceptions. Configure
your hosting platform's access logs separately; the toolkit cannot control them.

Operational metrics are not the financial record. TrustedRouter receives usage
with each response and owns accounting. Do not persist request payloads or add
a local billing ledger to make metrics reconcile. The current pilot logs do
not claim measured per-request latency, token aggregates or settlement status.

## Before Claiming Confidential Inference

Ordinary TLS and successful conformance tests are not attestation. A production
confidential route also needs measured software and model integrity, genuine
CPU/GPU verification, channel-bound encryption keys, and TrustedRouter's
accepted verifier policy. Follow [PLAN.md](../../PLAN.md), the
[architecture](../architecture.md) and [hardware gates](../gates.md). Until
those pass, keep the route classified as standard TLS only.
