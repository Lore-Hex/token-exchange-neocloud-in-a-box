# Bounded Ordinary-Serving Soak

**Implemented, tested against fakes, and validated by a
[five-minute live observation](../../evidence/p2/2026-10-27-ordinary-short-observer/README.md).
No completed live 24-hour run is recorded.** This provider-neutral observer repeats the existing
[selected-service probe](../../scripts/probe-selected-service.py) against an
already-running, separately approved ordinary-TLS service. It neither launches
nor renews an owner, modifies hardware, changes catalog routing, nor tears down
server resources. It does not run through a marketplace gateway or implement
customer billing.

This is a sparse, serial availability observation, not a load soak at 50% of
measured peak. It cannot satisfy PLAN P5.T5, certify continuous readiness or SLOs,
prove memory stability, quality, confidential execution or attestation, or mark
any phase complete. P2 benchmark and quality criteria remain unchanged. A
passing short run is not a 24-hour result.

## Scope and Limits

The [runner](../../scripts/soak-selected-service.py) reuses the committed probe's
fixed selected model and synthetic greeting, health request, missing/wrong-key
rejection, verified TLS, secret-file validation and streaming usage parser.
There is no model, prompt, URL-path, arbitrary header or insecure-TLS override.
Provider identity, destination, credentials and certificate material remain in
private operator configuration outside this repository.

| Limit | Bound |
|---|---|
| Observation duration | Default 86,400 seconds; explicit shorter runs 300-86,400 seconds |
| Interval | Default 300 seconds; allowed 300-3,600 seconds |
| Concurrency | One sample, at most one authenticated request, at a time |
| Sample budget | Explicit `--max-samples`, 1-288; must cover the whole planned window |
| Per sample | At most four HTTP requests: health, two rejected-key checks, one authenticated stream |
| Maximum day budget | 288 authenticated / 864 total model POSTs / 1,152 total HTTP attempts |
| Requested output allowance | 73,728 tokens across authenticated requests; 221,184 across all POSTs including negative-key checks |
| Request bounds | Original probe: 150-second sample, 120-second authenticated request, 256 output tokens, 1 MiB stream, 64 KiB event |
| Retries | Zero within a sample; failed samples are never replaced |
| Receipts | At most 578 files, each at most 32 KiB, for a full day; bounded local finalization gets five additional seconds |

The client freezes both a monotonic cutoff and a UTC cutoff at invocation.
`--deadline` is an independently approved absolute **not-after** bound and must
cover the requested observation window; it does not renew a serving lease.
The internal window never slides after setup, requests, failures or sleeps.
An observed forward wall-clock jump shortens the window irreversibly; a rollback
cannot extend the original monotonic limit. A request already in progress still
has the original monotonic sample bound; wall time is rechecked between samples
and every second while idle, not by a separate in-flight wall-clock monitor.

Samples are scheduled from the original start, but actual sample starts must
also be at least one interval apart. Late work is never caught up in a burst.
The runner refuses a sample without its full 150-second remaining allowance.
It waits out the remaining window after the last sample; gaps between samples
are **unobserved**, not assertions of continuous availability.

## Before Any Live Run

Obtain separate approval for the time window, endpoint, GPU retention and maximum
request/token costs. Coordinate with the serving owner: its admission cutoff
must outlast the entire observation plus any separately required drain/cleanup
reserve. A rented node's funding does not extend the software lease. Existing
[weekly lease](p2-t4-weekly-serving-lease.md) rules are unchanged.

Use a clean checkout of an independently reviewed exact commit. The runner
checks its own source and the probe's named source set against HEAD before the
first request and again before each sample, and binds each returned receipt to
the initial revision. A changed HEAD or checked file stops the run. This is
local producer-source provenance, not remote runtime attestation or a complete
transitive dependency measurement.

Choose one existing TLS mode: pinned certificate plus its SHA-256, or installed
system roots. Numeric destination selection avoids DNS/proxy changes; hostname
verification still applies. The credential is read only after the authenticated
connection's TLS verification. The probe rejects environment CA overrides in
system-root mode. Private key and pinned-certificate files must retain the
existing owner-only `0700` parent / `0600` regular-file checks. Do not put any
private inputs into this repository, a receipt, terminal transcript, or PR.

The output directory must be a **new absolute path**, with no symlink ancestor
or `..` component. Parent directories must already exist. Use a fresh path for
every invocation, including after failure; there is deliberately no resume,
overwrite, automatic retry or lease-extension switch.

## Approved Invocation

These variables denote private operator-supplied inputs; no endpoint or key is
provided by this repository. Set an approved not-after UTC time far enough ahead
to cover the full invocation window, but never beyond the owner's admission
cutoff. Run under an operator-owned `tmux`/`nohup` session, not a 24-hour SSH
connection. Do not enable shell tracing or capture private argv in evidence.

```sh
python3 -I -S -B scripts/soak-selected-service.py \
  --run --tls-mode system-roots \
  --address "$SERVICE_ADDRESS" --hostname "$SERVICE_HOSTNAME" --port "$SERVICE_PORT" \
  --api-key-file "$PRIVATE_KEY_FILE" --deadline "$APPROVED_NOT_AFTER_UTC" \
  --duration-seconds 86400 --interval-seconds 300 --max-samples 288 \
  --output-directory "$NEW_PRIVATE_RECEIPT_DIRECTORY"
```

For the existing pinned mode, replace `--tls-mode system-roots` with
`--tls-mode pinned --certificate "$PRIVATE_CERTIFICATE_FILE"
--certificate-sha256 "$CERTIFICATE_SHA256"`. Mixed trust modes fail closed.
The committed short observation used the system-root mode with a 300-second
window and one sample. It does not validate a full-day result.

## Evidence and Failure Handling

The new directory is mode `0700`; files are exclusive-create mode `0600` and
fsynced with their directory. Each canonical JSON envelope contains an ordinal
and the SHA-256 of the complete preceding file bytes. Hashes detect later
changes; these are not signed receipts or a protection against an owner rewriting
the entire chain. Files are never overwritten, deleted or resumed by the runner.

- `run.json`: frozen UTC window, source hashes, model-request hash, target hash,
  TLS mode and budgets. It contains no raw target, command line, key or body.
- `NNNN-intent.json`: durable reservation before each probe; at most four HTTP
  attempts and one authenticated attempt may follow it.
- `NNNN-sample.json`: the original content-free probe result, including failed
  checks, known usage and unknown-usage state. It is not rewritten after failure.
- `terminal.json`: outcome, elapsed monotonic duration, all counts, known token
  totals, uncertain attempts, cleanup scope and explicit false qualification
  claims. Stdout contains the final summary and terminal file hash.

Any failed sample prevents an overall pass, even if later samples succeed.
Known-usage completion failures and pre-authentication transient health/transport
failures may be followed only by the next independently scheduled sample. Unknown
authenticated usage, identity/authentication/protocol failure, cancellation or
deadline exhaustion stops immediately without retry. Unknown usage is never
silently treated as zero tokens. Disk/source failures also stop further probes.
Negative-key requests are not assumed to be cost-free if the authentication
boundary is broken; their upstream calls remain unmeasured, and an unexpected
response stops the run. Approve the all-POST bound, not just authenticated usage.

SIGINT/SIGTERM and normal deadline failures produce a terminal failure when local
finalization succeeds. The probe's context managers attempt socket cleanup; the
runner restores signal handlers and owns no child or GPU process. It does **not**
claim verified remote cancellation or server teardown. The server owner remains
responsible for its separate finite lease and actual resource-cleanup evidence.

SIGKILL, power loss, failed writes or disk failure may leave an intent without a
sample or terminal receipt. Preserve every file, including partial files: treat
each unmatched intent as up to four HTTP attempts, including three model POSTs
and one unknown authenticated attempt. Missing,
malformed, reordered, mismatched or incomplete receipts can never prove a pass.
An error before a durable intent cannot have issued a probe. Preflight rejection
may have only the content-free stdout failure, with no receipt directory.

For any later hardware claim, independently review the exact producer commit
and original receipts, source/hash-chain continuity, planned versus observed
window, unknown usage and failures. Commit appropriately reviewed, content-free
originals under `evidence/p2/<date>-<slug>/`, including failures. Keep the private
target-to-hash mapping and authorization outside this reusable repository. Do
not replace originals with a summary or relabel this as confidential or quality
evidence. No live evidence or phase completion is added here.

## Offline Verification

```sh
python3 -B -m unittest discover -s scripts -p 'test_ordinary_soak.py' -v
make check
make test-e2e
```

The new tests use fake clocks, synthetic HTTP streams and blocked real sockets
and credentials. They cover the full 24-hour schedule, budgets, both clock
jumps, source changes, interruption, exclusive receipts, write failures,
unknown usage, retained failures, and reuse of the real probe's TLS/key/usage
control flow. They are implementation evidence only, not a live soak result.
