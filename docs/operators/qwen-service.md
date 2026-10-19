# Public Qwen pilot service

This is the first small **standard-TLS** Hixtube service, not a confidential
release. Joseph requested public hosting and TrustedRouter integration on
2026-10-19. The service implementation is being validated locally; no public
endpoint, cloud deployment or customer billing has been verified yet.

The [recorded Linux run](../../evidence/p3/2026-10-19-qwen-retirement-linux/run.json)
passed all 18 service conformance checks, including Catalog v2, auth, streaming
and integer usage. It signals the entire service after the first real content
delta and requires the final finish reason, usage and `[DONE]` before exit.
[Results](../../evidence/p3/2026-10-19-qwen-retirement-linux/output.log) confirm
closed listeners and removal of the temporary container/image. It ran offline
on local Docker using real model weights, not on a cloud VM.
The shutdown proof checks complete single-data-field UTF-8 LF/CRLF frames and
requires finish, one usage chunk, then `[DONE]`. It rejects errors, truncation,
unsupported wire formats, invalid finish reasons and malformed or missing usage.
TLS readiness retains
system CA roots for public fullchains; a real root/intermediate/leaf handshake
regression checks both successful validation and wrong-hostname rejection.

## Service contract

- Model: `qwen/qwen3-0.6b`, pinned Q8_0 weights from the [local pilot](qwen-pilot.md).
- Context: 4,096 tokens. Maximum generated tokens: 256. Text chat only.
- Public `GET /v1/models`: Catalog v2, including explicit operator-supplied
  decimal prices per million tokens. Missing or malformed prices stop startup.
- Authenticated `POST /v1/chat/completions`: one completion, streaming and
  non-streaming, original engine token usage. No tools, images or JSON grammar.
- `GET /healthz`: checks the local API and model; not an attestation claim.
- At most two admitted requests, with 64 KiB request bodies. Excess requests
  fail before inference. The API enforces a 120-second request deadline.
- Only bounded sampling fields are forwarded: temperature, top-p, top-k,
  penalties, seed and stop strings. Native engine/template, probability and
  grammar options are rejected. Non-thinking compatibility hints (`thinking`
  disabled, `reasoning.effort`/`reasoning_effort` none or low) are accepted but
  omitted upstream; the catalog explicitly declares reasoning unsupported.

The engine and API guard bind only to loopback. Only the TLS front door may bind
publicly. Neither requests nor environment variables can select another backend.
No prompt, completion, header, body or user-selected path is logged by the pilot.
These controls are not a ZDR contract or cryptographic protection from the host.

## Persistent mode

The reviewed Linux image contains the pinned model, CPU engine, TLS shim and
API guard. It downloads nothing at runtime. Its default entrypoint runs tests;
deployment must explicitly select the persistent service:

```sh
python -I /opt/nib-pilot/scripts/pilot/serve.py \
  --prepared /opt/nib-pilot \
  --cert /run/hixtube/tls-chain.pem \
  --key /run/hixtube/tls-key.pem \
  --api-key-file /run/hixtube/api-key \
  --hostname api.hixtube.com --port 8443 --public \
  --input-price "$APPROVED_INPUT_USD_PER_MTOK" \
  --output-price "$APPROVED_OUTPUT_USD_PER_MTOK" \
  --retirement-at "$APPROVED_RETIREMENT_UTC" \
  --max-seconds 86400
```

This is a process invocation, **not a complete cloud deployment recipe**. Use a
reviewed image digest, never a floating tag. Mount only private configuration,
read-only; the key files must be regular non-symlink files accessible only to
their owner. The service snapshots them into private temporary memory. Run as
UID/GID 65532, with a read-only root filesystem, dropped capabilities,
no-new-privileges, bounded tmpfs, 4 CPUs, 3 GiB memory and a PID limit.

The certificate must validate for the exact public name. Local test certificates
are not public deployment certificates. Never disable TLS verification. Forward
only public port 443 to the container's TLS port; do not expose 8000 or 8001.

Public mode requires a fixed `--retirement-at` (`YYYY-MM-DDTHH:MM:SSZ`, within
24 hours). The catalog declares that same retirement time. At the cutoff, the
API rejects new inference and the supervisor drains existing streams. A slow
upload cannot enter inference after retirement; restarting with an expired
cutoff fails. Wall-clock and monotonic checks prevent a clock change extending
the run. Loopback-only local tests may omit this operator cutoff.
The real Linux proof checks that the declared cutoff survives the full service
path. Deterministic unit tests exercise the exact expiry boundary, slow uploads,
invalid/expired restarts and clock changes; this is not a 24-hour cloud test.

TrustedRouter's Hixtube parser must publish this cutoff using its existing
runtime manifest deadline, not merely retain the descriptive lifecycle field.
A successful refresh must not reset it to the ordinary stale-catalog window.
Withdraw the route and DNS before the VM's deletion deadline, allowing at least
15 minutes for DNS propagation and bounded process drain. Verify withdrawal;
TTL is not proof that every client cache has expired. A hard VM deadline still
takes precedence over collecting evidence or a stuck drain. Automatic deletion
does not remove DNS records, firewall rules or other project resources.

The supervisor stops accepting traffic and drains the TLS front door before
stopping the API and engine. Each serving process has a bounded shutdown budget;
a forced kill or unsuccessful exit is a failure, not a clean shutdown. Container
and host stop timeouts must allow the documented drain, not the Docker default.
The 24-hour service limit does **not** delete the VM or stop disk charges; enforce
cloud lifetime and verify teardown separately. Never configure automatic restart
to reset the approved run duration.

## Activation order

1. Approve the separate-project billing/API setup, exact CPU run budget and route
   prices. Resolve a pinned host image, inventory quota/network/IAM, and review
   the bounded deployment and cleanup commands. No GPU is required for this pilot.
2. Deploy in `hixtube-neocloud`, without any TrustedRouter runtime identity,
   private network connection or secret store. Generate a dedicated revocable
   inference key and trusted certificate. Keep keys out of Git and logs.
3. Verify the candidate directly before publishing DNS. Then verify the public
   hostname, auth rejection, known-answer generation, usage, streaming, Catalog
   v2, capacity rejection and shutdown. Record sanitized source-bound evidence.
4. Provision the dedicated upstream key separately in each TR cloud's own secret
   store. Deploy the reviewed generic-provider binding, including AWS vsock
   egress and Azure sealed-bundle binding. Do not claim all-cloud activation from
   a GCP-only secret or successful local tests.
5. Activate the catalog parser only after its live fetch and inference canary
   pass. Until then leave the TR registration PR unmerged: a registered but
   unreachable mandatory provider can block the global price refresh.
6. Use one approved, provider-pinned TR request to verify actual routing, integer
   usage, price calculation and settlement. Check streaming as well. Publish
   as a small CPU pilot with `zdr=false`, `confidential=false`, `e2ee=false`.

The pilot does not satisfy P2-P7's confidential hardware or large-model criteria.
The 8xB200 work still requires hardware qualification, verified boot/model
integrity, real CPU/GPU attestation and the generic verifier before a confidential
route can be advertised.
