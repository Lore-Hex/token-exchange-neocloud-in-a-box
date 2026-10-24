# Private selected-service smoke owner

This host owner is a bounded follow-up to the
[service lifecycle](p2-t4-selected-service-lifecycle.md) and
[image preparation](p2-t4-selected-service-image.md). Its
[first actual private smoke](../../evidence/p2/2026-10-24-selected-service-smoke-failure/README.md)
failed at the lifecycle stage with container cleanup confirmed; readiness and
inference were not established. It has not served a public endpoint.
Independent final-head review and complete validation gates are required before
an operator separately authorizes a run.

## Closed input and image handoff

[record-selected-service-smoke.py](../../host/record-selected-service-smoke.py)
uses the image builder's `validated_files` and `validate_build_record` directly.
The operator supplies the exact preparation receipt and successful build-record
digests. Raw OCI metadata is checked again using the existing lab recorder,
followed by fresh local daemon inspection of the pinned base and derived image.
No current-HEAD equality is required: an older approved image can be consumed
by a later reviewed host owner without rebuilding or silently replacing it.

The derived image must retain the approved prepared-source hash in its exact
CPython 3.12.3 entrypoint, Linux/amd64, UID/GID 65532, working directory `/`,
no command override, volume, exposed port or active healthcheck, and the
approved base layer prefix and environment. Local image configuration and
layer bytes are not independently rehashed; the local daemon and host root
remain trusted, as in the existing image recorder.

Provider configuration, the long API key, certificate and TLS key are validated
by the existing service snapshot helpers. New copies have `0600` modes and
UID/GID 65532 in a `0700` staging directory behind a separate private wrapper.
Original paths, bytes and permissions are unchanged. No input path, certificate
name, private input hash, raw exception chain, Docker output, request or response
body is included in the result. This does not hide command arguments from host
root. Abrupt host-process death may leave the private staging directory for
operator removal; container absence is not a secret-erasure or ZDR claim.

The weight mount is the selected [public view](p2-t4-selected-weight-view.md)'s
`model` child, not its private wrapper. The host checks the exact manifest
inventory and public file/directory modes. The service then performs the full
manifest hash check before starting its engine. Original cache permissions
are never relaxed by this owner.

## Runtime and lifetime

There is exactly one create and one start attempt, with no image pull, build,
automatic restart, inference retry, network creation or public publication.
Docker create is inspected before start and after the terminal receipt:

- No network except the container's private loopback; no published ports,
  host networking, sibling-container namespace or external probe URL.
- Read-only root and read-only model/input binds; all capabilities dropped,
  no-new-privileges, private PID/IPC/cgroup namespaces, no init wrapper, and
  UID/GID 65532. The existing service owner must be PID 1.
- Exactly GPU device indices 0 through 7, the NVIDIA runtime, 512 GiB memory,
  zero additional swap, 64 CPU quota, 1024 PIDs and 2 GiB shared memory.
- A 16 GiB executable, UID-owned private cache and a 256 MiB non-executable
  general temporary mount. No Docker log persistence or healthcheck process.
- Exact image ID, command, private mount sources, environment and owner label.

Docker documents that the `none` driver provides only loopback, and that tmpfs
is charged to the memory cgroup and may otherwise use swap. These are distinct
from a public TLS networking policy. See [none networking](https://docs.docker.com/engine/network/drivers/none/),
[tmpfs mounts](https://docs.docker.com/engine/storage/tmpfs/) and
[container create](https://docs.docker.com/reference/cli/docker/container/create/)
(accessed 2026-10-24). Nonroot native cache behavior remains unvalidated.

The inspect fixture follows the recorded Docker 28.0.4 source, not a hardware
observation: [`--tmpfs` parsing](https://github.com/docker/cli/blob/v28.0.4/cli/command/container/opts.go#L375-L380)
fills [`HostConfig.Tmpfs`](https://github.com/docker/cli/blob/v28.0.4/cli/command/container/opts.go#L655-L659).
Moby commit `6430e49a55babd9b8f4d08e70ecb2b68900770fe`
[registers binds and `HostConfig.Mounts` separately](https://github.com/moby/moby/blob/6430e49a55babd9b8f4d08e70ecb2b68900770fe/daemon/volumes.go#L137-L280).
Its [`GetMountPoints`](https://github.com/moby/moby/blob/6430e49a55babd9b8f4d08e70ecb2b68900770fe/container/container_unix.go#L372-L413)
feeds [inspect `.Mounts`](https://github.com/moby/moby/blob/6430e49a55babd9b8f4d08e70ecb2b68900770fe/daemon/inspect.go#L42-L85)
without adding `HostConfig.Tmpfs`. Thus this command requires exactly two bind
rows in `.Mounts` and both tmpfs entries in `HostConfig.Tmpfs`. Substituting
`--mount type=tmpfs` would use a different inspect representation and is not
accepted by this closed command/fixture contract.

The caller supplies one UTC cutoff, at most 80 minutes ahead. Setup counts
against it. The container receives a derived cutoff 180 seconds earlier for
host teardown; this is never a renewed duration. PR320 retains its shared
1800-second startup budget and front-first drain reserve. Its startup budget
accounts for observed selected lab startup times of roughly 821 to 892 seconds,
not the old 90-second Qwen startup assumption.

A detached watchdog starts before create. It retains the same wall and
monotonic bound; wall rollback cannot extend it. Parent EOF or the final
60-second host reserve triggers cleanup. A successfully returned container ID
is passed over a private inherited pipe. Interrupted create recovery can look
up only the random owned name, verify its label and image, then act on the exact
full ID. Removal never targets an unverified name, host PID or unrelated group.
TERM precedes forced removal where time allows, and denied TERM does not skip
the forced-removal attempt. Every local Docker process uses the existing
bounded process-group helper with time reserved for client reaping.

Docker daemon completion cannot be proven from a cancelled create client.
Recovery observations are bounded, and that result remains
`create-completion-unknown`, never successful cleanup. An unavailable or
unresponsive daemon can leave cleanup unconfirmed; neither a client exit nor
a watchdog timeout proves container absence.

## Receipt and limits

All service probes run inside the owned container: identity-bound engine
`/v1/models`, guard `/health`, and certificate/peer-checked TLS `/healthz`.
There is no new client payload or synthetic inference request in this smoke.
It proves neither authenticated generation, streaming, usage conformance nor
drain with an active request. Those remain later validation work.

A passing smoke requires the original service receipt to say ready and retired,
all three ordered role drains/reaps confirmed, Docker exit zero without OOM,
revalidated container configuration, owned-container absence and watchdog exit
zero. Malformed or content-bearing output is rejected, not retained. Before
the terminal observation, an attempted start has unknown (`null`) runtime
completion rather than a fabricated successful or failed launch.

Only closed metadata and public image/preparation digests are printed.
Hardware-validation, deployable, publication, tools, reasoning, ZDR,
confidential and E2EE flags remain false even for a successful private smoke.
The result is not a tau score, qualification, production-readiness or
confidential-computing claim.

On an owner exit of 1, the host now retains a strictly validated `owner_failure`
metadata object from the bounded stdout capture, including the fixed
preparation failure or a full failed lifecycle with partial cleanup. It never
retains stderr, exception text, arbitrary fields or raw output. The result
stays failed, attempted runtime completion stays unknown, and container cleanup
still runs. This diagnostic projection neither retries nor relaxes admission.
The numeric start-client exit code is retained even when stdout is rejected;
it is not evidence that the in-container owner ran or reached readiness.

## Operator interface

The following is a preparation example, not authorization to execute it. Every
value comes from separately approved local inputs; no credentials are generated
or installed by this task. Use `--inspect` in place of `--run` for read-only
image admission checks and private snapshot validation. Inspect-only results
remain BLOCKED and exit nonzero because no runtime proof exists.

```sh
python3 -I -S -B host/record-selected-service-smoke.py --run \
  --preparation "$APPROVED_PREPARATION" \
  --preparation-sha256 "$APPROVED_PREPARATION_SHA256" \
  --build-report "$APPROVED_BUILD_JSON" --build-sha256 "$APPROVED_BUILD_SHA256" \
  --docker-host "$LOCAL_UNIX_DOCKER_HOST" --weights "$PUBLIC_WEIGHT_VIEW_MODEL" \
  --provider-config "$PRIVATE_PROVIDER_CONFIG" --api-key-file "$PRIVATE_API_KEY_FILE" \
  --cert "$PRIVATE_CERTIFICATE" --key "$PRIVATE_TLS_KEY" --hostname "$CERTIFICATE_NAME" \
  --input-price "$APPROVED_INPUT_PRICE" --output-price "$APPROVED_OUTPUT_PRICE" \
  --hard-stop-at "$APPROVED_UTC_CUTOFF"
```

Focused verification uses Python 3.12 and synthetic Docker receipts, including
wrong identity/limits/network/mounts, interrupted create/start, denied cleanup,
parent EOF, live-parent cutoff, rollback, malformed lifecycle joins, private
exception canaries and private snapshot preservation:

```sh
cd scripts
python3.12 -B -W error::ResourceWarning -m unittest -v \
  test_selected_service_smoke test_selected_service_image
```

The initial implementation was software-only. Before the first recorded private
attempt, its integrated head passed full local and hosted checks and independent
review. Any subsequent diagnostic or runtime change still requires its own
reviewed source and complete gates before another run.
