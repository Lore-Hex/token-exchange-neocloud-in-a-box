# Selected persistent-service owner

This software-only increment builds on the selected API profile from PR316
and the fixed absolute alarm from PR317. It implements a closed, runnable
[process owner and CLI](../../scripts/selected_service_process.py), a small
[selected engine entry](../../scripts/selected_service_engine.py), and the
[lifecycle](../../scripts/selected_service.py). It does not build an image,
publish a listener, run inference or establish a hardware result. The current
root-owned `0700/0600` public-weight cache is **not usable by UID 65532**;
permission preparation and real read-only/nonroot cache behavior remain gates.

## Fixed contract and reuse

The [selected checkpoint profile](../../scripts/deepseek_model_profile.py)
supplies its exact revision, manifest hash and total bytes. The canonical API
ID is `dealignai/deepseek-v4.1-flash-1017-uncensored-fp8`: by default 8192 context tokens,
256 maximum output tokens, one admitted request, 512 GiB and no swap. Provider
metadata, API key, approved prices, TLS material and absolute retirement are
operator inputs. There is no model, plugin, upstream URL, port, capacity or
engine-argument override. The [Go API](../../cmd/nib-pilot-api/main.go) retains
provider validation and request enforcement; Python does not duplicate them.
The explicit [graph profile](p2-t4-selected-service-graphs.md) instead binds
4096 context and eight total admissions; output, memory and privacy limits stay
unchanged. Execution and lifetime are separate closed choices.

`pilot/serve.py` supplies private input snapshots, key validation, TLS keypair
and client trust setup, and the original UTC retirement rules. The process
owner uses the pilot's controlled environment and trcs command. Existing lab
helpers supply sealed Python/bootstrap workers, application/source validation,
manifest checking and process/listener identity. Lab default arguments and
Qwen behavior are unchanged. Frontend drain follows the pilot's TERM/wait
semantics, but clips every wait to the absolute cutoff rather than using its
unclipped 140-second helper.

The selected adapter copies validated `tau8k-eager`, DP2/TP4 lab arguments and
changes exactly the value of `--served-model-name` at engine launch. It never
rewrites request or response bodies. At pinned vLLM revision
`5df63a7203cdb9328d824a27b682e77c7b9928be`,
[`ModelConfig.served_model_name`](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/config/model.py#L325-L333)
sets accepted names and the response model name;
[`--served-model-name`](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/engine/arg_utils.py#L1006)
is the corresponding option.
[`show_available_models`](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/entrypoints/openai/models/serving.py#L64-L77)
reports that name and configured `max_model_len`. These primary sources were
inspected at the pin, not measured in this new service.

The serialized lab contract retains the original bare-name arguments for
the existing lab validation. A newly composed, hash-bound launcher preserves
all original definitions and sealed worker dispatch; only its new
`selected-service-engine` entry supplies the canonical argument copy to
`run_engine`. This is a new service payload, not the original lab receipt.
The API profile rejects tools. The later
[reasoning-control follow-up](p2-t4-selected-reasoning-controls.md) adds bounded
native effort forwarding, not reasoning qualification. Nothing here claims tau
quality, sustained throughput, ZDR, confidential computing or E2EE.

## Concrete lifecycle

The supervisor must be nonroot PID 1 in a private container PID/network/cgroup
namespace. It starts an owned `nib model check-local --workers 8` validator,
reaps that group, and then launches one retained engine, the guard, and trcs.
All children have controlled environments, closed inherited descriptors, their
own process groups, and null stdin/stdout/stderr. Model/exception text is not
logged. The same engine supplies readiness and all subsequent API requests;
the owner does not reload it for separate proofs.

Engine readiness checks the exact owned PID/start time/executable/arguments,
namespace and loopback listener. Each HTTP connection is peer-checked before
sending its fixed request, with post-response identity verification. Engine
`/v1/models` must report exactly one canonical ID and the selected integer
context (8192 eager, 4096 graphs).
Guard and TLS readiness verify owned listeners/binaries; TLS additionally
checks certificate/name trust and the accepted server-side socket owner.
TLS alone accepts either one IPv4 wildcard listener or one IPv6 wildcard
listener, never one of each or duplicate listeners. This follows pinned trcs
[`net.Listen("tcp", addr)`](https://github.com/Lore-Hex/trustedrouter-confidential-stack/blob/8a87d9765ceabe76bbfa5e56a72c91da6d8290d5/cmd/trcs/main.go#L117)
and Go 1.27.2's
[`favoriteAddrFamily`](https://github.com/golang/go/blob/go1.27.2/src/net/ipsock_posix.go#L89-L155):
an IPv4 wildcard can select a dual-stack IPv6 socket. The chosen family,
listener inode, PID, arguments, start time and executable stay bound. The
internal IPv4 probe requires a unique owned accepted socket with exactly
`127.0.0.1` on both sides, represented as IPv4-mapped IPv6 for a tcp6 listener.
Engine and guard remain strict IPv4 loopback-only. No trcs pin changes.
The whole probe, including slow headers/body chunks, uses the PR317 absolute
alarm, not just per-receive socket timeouts. No public-network probe is made.

`Contract.begin` runs before snapshots/preparation. The original absolute UTC
lifetime is capped at 4,800 seconds by default; the explicit
[ordinary day profile](p2-t4-selected-service-day-profile.md) permits at most
24 hours. It is also captured as a monotonic bound. Wall-clock
rollback cannot extend that bound; a forward jump shortens it permanently.
An external restart controller must retain the original cutoff. This process
is not a persistent trusted clock, and host-enforced expiry remains required.

One shared 1800-second startup budget includes preparation, weight validation,
engine readiness and both frontends. It is clipped to lifetime minus cleanup
reserve, never restarted at READY. The 821-second late-readiness test is a fake
clock regression, not a new measured result. Qwen's 90-second wait is not used.

Admission stops 375 seconds before hard expiry: two frontend drain allowances
of 150 seconds and three group-reap allowances of 25 seconds. These bounds
derive from the existing pilot and snapshot cleanup helpers. TLS drains first
while guard/engine remain alive, then guard drains, then engine/workers are
reaped. Every operation is clipped to hard expiry. Forced or nonzero frontend
exit is not graceful; signal delivery alone does not prove cleanup. Every
registered child is attempted after failures, followed by an at-most-25-second
sweep (also clipped to the remaining original hard deadline) of
the owned PID namespace for interrupted spawns/orphans. Permission denial
without positive absence proof remains failure.

Child exit, changed private snapshots/mounts, wrong identity/limits, shutdown,
or expired deadlines fail closed. Cleanup never upgrades an earlier failure.
Reports contain fixed categories, fixed role names and booleans, not exception
strings, model bodies, keys, paths or operator metadata. `deployable` and
`hardware_validated` always remain false in this software-only increment.

## Offline builder interface

The next minimal follow-up is a reviewed offline image builder plus inspected
container creation/retirement command, not another owner abstraction. It must:

1. Start from the pinned selected GPU runtime and an exactly validated **non-owner**
   uncensored B300/nonconfidential, DP2, `tau8k-eager` lab bundle. Do not use a
   tau-owner/provider/stream-cohort payload. Preserve its original receipt and
   files. Apply and validate the reviewed engine overlay offline using the
   existing application tooling; never apply into a live writable root.
2. Use `selected_service_process.prepare_payload(payload_bytes)` to derive a
   separate service payload. It checks original launcher/source/manifest
   bindings, composes the selected entry, and rehashes `lab.json`, `launch.py`
   and the sealed Python wrapper. It does not itself validate image provenance
   or apply the overlay. Install the result at `/opt/nib`, including its pack
   metadata and `nib` binary, with final immutable runtime permissions.
3. Install this exact checkout's complete `scripts/**/*.py` tree,
   `scripts/e2e/pins.json`, the selected-profile `bin/nib-pilot-api`, and the
   pinned `bin/trcs` under `/opt/nib-service`. No repository `models`, `docs`,
   `host`, Git metadata or arbitrary working tree is needed at import time.
   Generate `preparation_receipt(root, lab_sha256)` only after final packaging,
   serialize it as `prepared.json`, and approve its exact SHA-256 externally.
   At runtime the owner validates the complete bounded source/bin inventory,
   trcs pin, payload/wrapper, interpreter hash and read-only mount identities.
4. Use the **GPU runtime's CPython 3.12.3**, resolved path and exact executable
   hash from `lab.json.interpreter`, for both supervisor and engine. The
   supervisor rejects other versions or another executable. This is not the
   CPU pilot's slim Python 3.12.13. Use `-I -S -B`; only the fixed scripts path
   is inserted for supervisor imports. Workers retain the existing sealed
   bootstrap. Local unit tests on another 3.12 patch version do not prove
   runtime interpreter parity.
5. Prepare a dedicated readable public-weight view for UID 65532 and verify
   exact manifest bytes/hashes again. The currently observed root-owned
   directories `0700` and files `0600` cannot be mounted as-is for this UID.
   A reviewed helper may prepare only allowlisted regular public model files
   and their directories, rejecting links/special files and unexpected input.
   For a separate immutable copy, directories `0555` and model files `0444`
   preserve no-write access while permitting traversal/read. Do not mutate
   the original test bundle/cache implicitly, mix secrets into that helper,
   or recursively relax arbitrary paths. Runtime code, pins and binaries
   likewise need final readable/traversable modes (and executable binaries).
   The separate public-view helper exposes `destination/model` inside a private
   `0700` atomic wrapper: bind that model subdirectory, not the wrapper itself.
6. Stage private operator inputs separately for the runtime UID: private
   directories `0700`, API key and TLS private key `0600`, no group/world
   readability. Original root-owned secret files may need a separate securely
   owned copy, **not** permission relaxation. The service snapshots all four
   inputs into its own `0700` directory with `0600` files. Key/cert rotation
   requires a bounded restart; there is no in-place secret reload.
7. Enforce a read-only root and immutable `/opt/nib-service`, `/opt/nib`, and
   `/model` mounts; private bounded writable scratch/cache; nonroot PID 1;
   private PID/network/cgroup namespaces; 512 GiB memory and zero effective
   swap. Root-owned immutable public files need not become runtime-owned.
   The owner reuses the lab's exec-cache versus noexec-temp mount checks.
   Actual nonroot loading/JIT/cache compatibility remains **unproven** until
   the prepared container is tested. Do not weaken the existing root-owned lab
   payload guard or claim its writable-root/root-UID profile is this service.

Only trcs `0.0.0.0:8443` may be published. Engine `127.0.0.1:8000` and guard
`127.0.0.1:8001` remain inside the container. Host networking is forbidden;
Docker documents that it shares the host network namespace and ignores port
mappings. The isolated network, egress policy, capabilities, device selection,
image provenance, mount permissions and host-enforced absolute retirement all
need explicit inspect-based acceptance in the builder follow-up.
[Docker host networking](https://docs.docker.com/engine/network/drivers/host/),
[user selection](https://docs.docker.com/engine/containers/run/#user), accessed
2026-10-24. Nonroot compatibility cannot be inferred from those settings.

Docker's equal positive memory and memory-plus-swap settings disable swap;
zero memory-swap is ignored. The owner checks effective cgroup `memory.max`
and `memory.swap.max`, not just requested options.
[Docker memory/swap semantics](https://docs.docker.com/engine/containers/resource_constraints/#--memory-swap-details),
accessed 2026-10-24. No unverified Docker profile is supplied here.

## Operator entrypoint

After **all** preparation and permission gates above, the container's entry
must exec the verified CPython 3.12.3 binary as PID 1. `VERIFIED_PYTHON` below
must be exactly `lab.json.interpreter.resolved`, not a PATH-selected Python.
Inputs are external operator paths; no private values belong in the image.
This is the implemented inside-container interface, **not a command runnable
on the current weight mount or an authorization to publish/launch**:

```sh
exec "$VERIFIED_PYTHON" -I -S -B /opt/nib-service/scripts/selected_service_process.py \
  --prepared-sha256 "$PREPARED_SHA256" \
  --provider-config /run/operator/provider.json \
  --api-key-file /run/operator/api-key \
  --cert /run/operator/cert.pem --key /run/operator/key.pem \
  --hostname "$SERVICE_HOSTNAME" \
  --input-price "$INPUT_PRICE" --output-price "$OUTPUT_PRICE" \
  --hard-stop-at "$HARD_STOP_AT"
```

## Verification and remaining gates

The shared `engine_lab.check_scratch_mounts()` is self-contained for both the
concatenated lab launcher and the ordinary service import. Previously the latter
raised `NameError` because `os` and `require` existed only in the concatenated
namespace. A regression calls the real helper through `Prepared.mount_state()`
with only OS observations mocked: both scratch mounts must remain writable,
`nosuid` and `nodev`; `/cache` must allow execution and `/tmp` must prohibit it.
The existing lab rejection type/message and all mount predicates are unchanged.
This reproduces a software startup blocker, not the exact failing stage of an
older catch-all hardware receipt.

Both the lab `apply` path and the selected engine entry call the same fixed
`initialize_cache()` helper before engine startup. It creates the existing eight
`0700` directories under `/cache`, including the configured `/cache/tmp`, without
changing mount policy or accepting alternate paths. Real temporary-directory
tests check permissions, idempotence, `TMPDIR` selection and failure propagation;
entry tests require initialization before application/engine execution. This
prevents the known missing initialization, not a proven cause of an older run.

Changing this helper changes the composed launcher bytes. Before another run,
regenerate the non-owner lab bundle from the reviewed final source, derive a new
service payload/preparation receipt, rebuild the image and validate the new
receipt/image handoff. Preserve old bundles, images and failure receipts; do not
patch a staged payload or substitute hashes. The old prepared image is not fixed
by updating the host recorder alone. No new container or hardware run is claimed.

From `scripts/`:

```sh
python -B -W error::ResourceWarning -m unittest -v \
  test_selected_service test_selected_service_process
```

Coverage uses fake clocks/processes, temporary private inputs, mocked HTTP/peer
operations, isolated import-only Python children, and a separate real-alarm
child reading a trickling in-memory HTTP body. It includes 821-second
readiness, immutable deadlines, wrong model/limits/ownership, canonical-only
argument transformation, original payload preservation, child/cancellation
failure, slow-trickle bounds, denied signals and positive reap proof, mount/key
replacement, closed CLI/preparation rejection, and missing-repository import
closure. Import smoke copies only the packaged Python tree, pins, dummy bins
and an empty sibling payload directory; it is not container or engine parity.
The Linux-only listener regression compiles a small fixture with pinned Go
1.27.2 offline and tests real ephemeral `tcp` and `tcp4` wildcard listeners,
actual process identity, socket ownership and accepted IPv4/mapped peers. It
emulates only the required private-cgroup root, since the test runs outside
the proposed container. On other hosts this one test is explicitly skipped;
it is not evidence of running trcs/TLS or the prepared image. Fixture tests
also reject duplicates, changed identity, wrong ownership and wrong families.
No fixed-port server, Docker operation or inference is used. Actual container
isolation, trcs/TLS peers, stream drain, reaping and native cache execution
still need prepared-container proof, independent final-head review, CI, and
separate explicit hardware/network authorization.
