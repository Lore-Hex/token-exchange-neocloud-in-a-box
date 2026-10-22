# Private provider checker

The preparation increment exercises the **unchanged upstream provider checker**,
not replacement HTTP checks. The subsequent opt-in owned-runtime integration is
implemented and software-tested, but has not run against DeepSeek. Neither
increment grants P2 conformance or changes engine imports/logging. A live run
requires final-head independent review and parent coordination; no run is
implied by these tests or the historical closed-endpoint receipt.

## Audited Source

Audited 2026-10-22: the [0.1.8 PyPI metadata](https://pypi.org/pypi/trustedrouter-provider-check/0.1.8/json)
and [original wheel](https://files.pythonhosted.org/packages/b7/68/da22f00ab3bf960ed80b4decbd9149311257b5a32ca68c96fb814947e0c3/trustedrouter_provider_check-0.1.8-py3-none-any.whl).
Wheel SHA-256 is `591a11295d3f1e0ef20fbc51c788cabfbedf176e79ce56de8378252bd13adecd`.
The existing [e2e lock](../../scripts/e2e/requirements.lock) is unchanged:
`d5fbea8926b7410c34730593b8ebd305f01b04a160eb30e9867330c45799ade7`.
All 17 packages, including native Brotli/RPDS wheels, come from that lock. The
target is the existing pinned Linux/amd64 CPython 3.12.13 image in
[e2e pins](../../scripts/e2e/pins.json), not the engine's Python environment.

Relevant wheel sources are `tr_provider_check/checks/__init__.py` (`run_checks`),
`http.py` (`GatewayClient`), `checks/catalog.py`, `checks/callability.py`,
`checks/chat.py`, `checks/streaming.py`, and `report.py` (`report_document`).
The contract snapshot is
`973e380e5ab7e6303576dd7555c186244649befdc2da5c64017611abe9918769`.

- `run_checks` executes real tiers 1-4. The worker passes the fixed local endpoint,
  fixed native DeepSeek model, no API key, no catalog URL, and `max_sweep_models=1`.
  No monkeypatches, replacement parser, altered prompts, or altered check statuses.
- Omitting the catalog URL makes `catalog.declared-v2` skip and avoids the public
  schema-fetch path. This is not Catalog v2 qualification or gateway testing.
- HTTPX follows redirects and uses environment defaults. The worker starts from
  an exact empty-derived environment, with no proxy/credential variables. A Python
  audit hook rejects DNS/connection destinations other than literal IPv4 loopback
  port 8000 and rejects subprocess creation. The kernel network namespace, not
  the audit hook, must provide the security boundary.
- Upstream has a 600-second HTTP timeout, streaming retries before first output,
  and unbounded aggregate decoded-stream retention. Several synthetic requests
  deliberately omit `max_tokens`; do not silently rewrite those checks. A normal
  successful path has one discovery GET and nine POSTs, **not** a hard request or
  token bound: redirects/retries and response behavior can change it.
- Upstream's gate rejects `fail` but accepts `warn`/`skip`. An unreachable endpoint
  actually produced warnings/skips and gate `true` in the local Linux negative
  control. The wrapper preserves that gate and every status, but requires all 17
  existing mandatory checks to pass, the declared-catalog skip, and exactly the
  selected native model in discovery. Missing/unknown/duplicate check IDs cannot
  become acceptance. This matches the existing e2e mandatory-check policy.

## Implemented Boundary

[The offline preparer](../../scripts/provider_conformance.py) requires exactly
one compatible wheel for each locked package; validates hashes, metadata, tags,
archive paths, membership and sizes; rejects links, duplicates, `.data`, import
hooks and bytecode; and never imports or installs the wheel code on the host.
The CLI checks its own source closure against Git before and after preparation.
It creates a new 0700 parent with a read-only payload and prints the manifest
SHA-256. It does not fetch dependencies or accept an endpoint/container ID.

```sh
python3 -I -S -B scripts/provider_conformance.py \
  --wheelhouse /absolute/private/locked-wheels --output /absolute/private/new-payload
```

The worker is an internal component, **not a standalone live launcher**. Its
caller must independently reproduce/verify that payload from the reviewed
preparer and retain the manifest digest as a trust root. A caller-supplied
manifest is not itself source authentication or an endpoint identity receipt.

[The worker](../../scripts/provider_conformance_worker.py) checks its exact
file tree before importing the authenticated packages. It requires `-I -S -B`,
the exact interpreter/stdlib paths/environment, nonroot UID, loopback-only network
interfaces, a 1-GiB cgroup memory ceiling, zero swap, and 32 PIDs. It adds only the
verified package directory to the stdlib path. No ambient site packages or engine
bootstrap exceptions. CPU time is limited to 60 seconds and wall time to 180
seconds. A supervisor must independently terminate/remove the container; a Python
alarm cannot bound uninterruptible native code. No request-byte/token bound is
claimed; resource exhaustion must fail, never be retried with relaxed limits.

Stdout/stderr are redirected to `/dev/null` before wheel imports, including native
writes and exception paths. Core/file dumps are disabled. Only fixed check IDs,
statuses, booleans and source identities leave on a bounded 16-KiB result channel.
No prompts, completions, headers, raw errors, response-derived model lists,
measurements, raw upstream JSON or performance report are published. Exit 0 means
the strict mandatory-check gate passed, 1 means completed but not accepted, and
2 means boundary/timeout failure. Even exit 0 leaves endpoint identity,
confidentiality and quality acceptance false in this component.

## Local Verification

[Tests](../../scripts/test_provider_conformance.py) cover lock/hash/tree
mutations, import hooks, duplicates, wrong/missing/warn/skip checks, privacy
canaries including native writes, timeout handling and destination restrictions.
The opt-in test executes the actual locked upstream checker in the already-cached
CPU image with `--network none`, no GPUs, no ports, no Docker socket mount,
read-only filesystem/payload, no capabilities, and no container logging. An
independent 200-second process-group deadline is followed by owned-label removal
and absence verification. Docker stdout and stderr each retain their 64-KiB cap;
the private worker report is additionally limited to 16 KiB.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=scripts \
NIB_PROVIDER_WHEELHOUSE=/absolute/private/locked-wheels \
NIB_PROVIDER_DOCKER_SOCKET=/absolute/private/local-docker.sock \
NIB_PROVIDER_PROOF=/absolute/private/new-evidence \
python3 -m unittest -v test_provider_conformance.RealCheckerClosedEndpointTest
```

The test never starts a model or a fake endpoint. A negative-control PASS means
the real checker ran, the strict wrapper rejected the closed endpoint, and its
owned container was removed. It is not DeepSeek conformance. The
[original committed-source Linux receipt](../../evidence/p2/2026-10-22-provider-check-closed-endpoint/README.md)
records exactly this result: seven warnings, eleven skips, upstream gate true,
strict gate false, worker exit 1, cleanup confirmed.

## Owned Runtime Integration

The [producer](../../host/record-deepseek-lab.py) owns engine readiness and cleanup.
The [session helper](../../scripts/provider_conformance_session.py) is included in
the source-bound private payload only with the explicit preparation flag below.
The unchanged locked checker payload is a separate read-only subtree: no wheel
code is imported into the engine or host. Default commands/contracts remain
unchanged when the flag is absent.

1. Parent approves a fresh fixed run window, startup allowance and checker budget
   against retained/cache-verified weights. No new resource/API authorization is
   inferred. Keep the existing exact image/model/import/logging confinement.
2. After the existing parity cases pass, the guest emits one fixed metadata frame
   containing the source-contract digest and the first owned API observation.
   It waits at most 300 seconds for a digest-bound acknowledgement. The parent
   validates the observation, exact owned container ID/configuration, host PID,
   start time, and namespace identities. No arbitrary container ID is accepted.
3. Start a separate CPU checker with `--network container:<exact-owned-id>`, not
   `docker exec`, host networking, a bridge, port publication, an engine import
   hook, or a general HTTP proxy. Docker documents that this shares the engine's
   loopback namespace; do not combine it with unsupported DNS/hostname/publish
   flags ([primary source](https://docs.docker.com/engine/network/#container-networks)).
   Keep separate PID/cgroup/filesystem, no GPU devices, no credentials/socket
   mount, and the same worker limits. Inspect the realized Docker configuration,
   pinned image/platform and configuration before start; verify its realized
   kernel network namespace equals the engine's and its PID/cgroup namespaces
   differ immediately after start. Recheck the unchanged engine identity after
   checks. A checker that exits before its namespace can be observed is rejected.
4. Parent retains a bounded content-free result, original status vector, upstream
   gate and strict gate. Failure, warning, skip, timeout, OOM, changed identities
   or uncertain cleanup remains nonacceptance. Do not upload raw upstream output.
5. Remove the checker before sending the acknowledgement. The guest obtains the
   second process/executable/listener/argv observation, then stops its engine.
   The parent joins both observations, the exact readiness frame, worker report
   digest and acknowledgement. Both owned container absences must be verified.
   Preserve a source-bound original run receipt. Only an actual run can establish
   the requested direct-node conformance result;
   tau/quality, attestation, front-door/catalog and public listing remain separate.

Only the B300 confidential-mode DP2 profile with parity, pinned tokenizer
materialization and runtime observation can enable this mode:

```sh
python3 -I -S -B host/record-deepseek-lab.py \
  --prepare-local-correctness-b300-dp2 /absolute/private/new-bundle \
  --tokenizer-parity --tokenizer-materialization --runtime-observation \
  --provider-conformance-wheels /absolute/private/locked-wheels
```

Preparation requires a committed source tree and the existing authenticated raw
engine metadata. Transfer the prepared bundle privately without changing bytes.
The retained engine image, exact verified model cache and pinned CPU image must
already be present. Runtime uses `--pull never`; CPU image inspection precedes
engine creation, and failure does not trigger a pull, installation or fallback.
Docker may report the pinned repository in tagless `python@sha256:...` form
after a digest pull ([digest-reference semantics](https://docs.docker.com/reference/cli/docker/image/pull/#pull-an-image-by-digest-immutable-identifier)).
Inspection accepts only the existing repository aliases with the exact pinned
digest, with or without its fixed display tag; it still requires the same
Linux/amd64 OCI manifest descriptor, matching image ID and no declared volumes.
An actual retained-host preflight exposed this spelling difference before any
container was created. It is not an engine run or conformance measurement.
The ordinary `--run` invocation consumes the receipt digest and an explicit UTC
deadline. No runtime flag can enable or override the prepared checker profile.

The authorized trial is at most 45 minutes overall. The producer starts teardown
four minutes before that cutoff. It leaves the existing 1,800-second engine
startup bound unchanged. The checker keeps its 180-second alarm and has an
independent process issuing owned-checker removal at 200 seconds or parent-pipe
EOF. The whole-engine watchdog also removes the owned checker, then engine, on
parent death or the operational cutoff. Cleanup retries cover the existing
create-client race; daemon failure, unknown completion or unproven absence
remains failure. A software watchdog cannot guarantee teardown of an unresponsive
kernel/daemon; that condition requires operator intervention, never acceptance.
The VM, image/cache and SSH key are never deleted by this producer.

The parent stdout cap remains 64 KiB including the readiness frame; stderr also
retains its 64-KiB cap. The checker projection remains capped at 16 KiB. Only a
fixed metadata acknowledgement enters guest stdin; engine application stdin,
stdout and stderr remain `/dev/null`. No API listener, port, network interface,
engine argument, application stage, import exception or logging bypass is added.

An actual accepted narrow trial requires **both** outer `functional_pass=true`
and `cleanup=confirmed`, the matching guest/parent checker acknowledgements,
the strict worker gate and stable complete runtime observations. A nested worker
gate alone cannot prove the join or whole-engine cleanup. The top-level evidence
remains `result=fail`, `status=BLOCKED`, `deployable=false`, `confidential=false`:
this is not full P2, tau, quality, attestation, front-door or catalog acceptance.

[Session controls](../../scripts/test_provider_conformance_session.py) exercise
real local pipe/EOF/deadline handling and process reaping, plus synthetic Docker
state/namespace/ownership, lifecycle, privacy and binding negative controls.
They do not start a GPU, simulate conformance acceptance as hardware evidence,
or weaken the pinned upstream checker. Before launch, notify the parent and
coordinate with other agents; no concurrent retained-GPU job is permitted.
