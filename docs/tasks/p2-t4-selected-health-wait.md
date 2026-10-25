# Bounded External Startup Health Wait

Status: software implementation and synthetic tests only. No endpoint, model,
GPU, container or firewall is exercised by this change. Independent final-head
review, CI and separate operator authorization are required before execution.

[wait-selected-service.py](../../scripts/wait-selected-service.py) is a separate,
credential-free scheduling aid for the [external smoke](p2-t4-selected-external-client.md).
It does not change that smoke's no-retry inference budget, the serving image,
read-only mounts, owner journal, network policy or cleanup. It does not start
the smoke automatically or extend an owner's deadline.

## Fixed Boundary

The operator supplies exactly one numeric global IPv4 address, port, TLS
hostname, private certificate-file path, exact certificate-file SHA256 and UTC
cutoff in the default pinned mode. Explicit `--tls-mode system-roots` instead
requires both certificate arguments to be absent and validates against the
client's installed roots using the shared
[closed TLS policy](p2-t4-selected-external-client.md#explicit-system-roots).
No API key argument, environment credential, arbitrary URL/path,
redirect, DNS lookup, prompt, model, callback or target fallback is supported.
The target fields are hashed together in the report for a private operator join;
the literal address, hostname and file path are not emitted. This digest is
provenance, not anonymization or proof of remote process ownership.

The command reuses the external smoke's private certificate reader,
hostname/chain verification, exact DER leaf pin and strict `GET /healthz`
parser. Every attempt uses a new verified TLS connection with the same selected
trust mode; a chain/hostname failure never falls back to pinned trust or retries.
A successful response
requires HTTP 200, exactly one `Content-Length: 0`, no transfer/content encoding,
empty body and complete EOF. Sockets and readers close before success.
Pinned [trcs health](https://github.com/Lore-Hex/trustedrouter-confidential-stack/blob/8a87d9765ceabe76bbfa5e56a72c91da6d8290d5/internal/shim/server.go#L122-L143)
checks its listener audit and the guard; the
[guard](../../internal/pilotapi/handler.go) checks engine health. These checks do
not submit a model request. A PASS is only point-in-time health of this pinned
peer, not ownership, continued readiness, capacity, quality or deployment proof.
System-root mode proves only this client's chain and hostname validation, not
universal public trust or revocation checking. Client roots and clock are trusted
inputs; this command never installs a CA or modifies a certificate.

## Bounds and Failure

One invocation has an original absolute UTC cutoff at most **1200 seconds**
away, at most **128 TLS attempts**, and at most **five seconds per attempt**.
Backoff is 1, 2, 4, 8, then at most 10 seconds; sleeps check the cutoff at least
once per second. Source validation, certificate input, connection, HTTP parsing,
finalization and backoff all share the original main-thread alarm. Nested
attempt alarms never renew it, and an earlier pre-existing one-shot alarm can
only shorten the bound (periodic alarms are rejected). Forward wall-clock changes shorten the monotonic
bound at checkpoints; backward changes cannot extend it. No subprocess/model
work is launched by polling; bounded local Git reads verify producer source.

Only the closed set of pre-verification refusal/reset/unreachable/timeout
transport failures, per-attempt timeouts and rejected health observations can
retry. A rejected health observation includes non-200 or malformed framing;
it is counted as rejected and never converted into success. Only a subsequent,
fresh, completely valid check may PASS. Certificate, hostname, pin and other
TLS errors stop immediately. Unexpected failures, post-verification transport
errors and socket-finalization errors fail closed. SIGINT/SIGTERM cancel the
wait and restore the previous handlers; the command requires the main thread.

The single closed JSON report contains source hashes, deadline, target digest,
attempt/verified-TLS/health-attempt counts, rejected observations, timeouts,
retry count and fixed failure category. A health request is counted at its
attempt boundary, not as proof of delivery or acceptance. It never includes
headers, bodies, exception text, paths, target names or credentials.
`model_requests` is zero and `credential_accessed` is false. All ownership,
continued-readiness, conformance, quality, deployment and privacy claims remain
false. The named source hashes are producer provenance, not a measured software
closure. Preserve failed observations; a new invocation needs separate approval.

Pinned mode retains its v1 receipt and exact target-digest inputs. System-root
mode emits `nib.selected-external-startup/v2` with `tls_mode: "system-roots"` and
binds that mode instead of a certificate hash in the private target digest. The
same 1200-second/128-attempt/five-second bounds, cutoff checks, cleanup and
content-free counters apply. Selecting a mode is not proof of successful TLS:
the result, TLS counters and health status remain authoritative.

## Operator Template

This is not an execution record. Supply the same approved peer and out-of-band
certificate as the one-shot external probe; keep private values outside Git.
Choose a cutoff no later than the already-approved finite owner's deadline,
leaving its existing inference/cleanup reserve intact. This external process
does not independently authenticate an owner cutoff or ownership receipt.

```sh
python3 -I -S -B scripts/wait-selected-service.py --run \
  --address "$APPROVED_IPV4" --port "$APPROVED_PORT" \
  --hostname "$EXPECTED_TLS_NAME" \
  --certificate "$PRIVATE_CLIENT_DIRECTORY/cert.pem" \
  --certificate-sha256 "$APPROVED_CERTIFICATE_FILE_SHA256" \
  --deadline "$FIXED_STARTUP_UTC_CUTOFF"
```

For a separately approved system-root observation, replace both certificate
arguments with `--tls-mode system-roots`. Keep the same private address/name and
bounded cutoff. No inference, service action or credential access is added.

After PASS the operator may separately run the already-approved one-shot
external probe. Its own pinned health check remains mandatory, since readiness
can change between observations. This wait makes no authenticated request.

[Focused tests](../../scripts/test_selected_health_wait.py) use deterministic
clocks, synthetic sockets and the real shared health parser. They cover exact
wire bytes, closed target/source binding, no credential/model access, startup
retry bounds, malformed health, permanent TLS errors, original deadlines and
wall jumps, cancellation, reader/socket cleanup and private-data canaries.
