# Bounded ordinary selected-service day profile

Software implementation only. No 24-hour run, finite-owner hardware PASS,
continuous availability, public deployment or confidential qualification is
established by this change. Hardware use requires the separate finite-owner
proof, final independent review, exact-head CI and operator approval.

## Explicit profile, one cutoff

The existing [finite owner](../../host/record-selected-service.py) accepts
`--lifetime-profile ordinary-24h` for one ordinary serving run. The omitted
option remains `smoke`, capped at 4,800 seconds. The network-none smoke CLI
cannot opt into the day profile. Unknown profiles and combinations of the day
profile with inference, conformance, protocol or tool smoke modes fail closed.
The standalone in-container entrypoint also defaults to the 4,800-second
profile; a longer lifetime now requires the explicit day option there too.

Both limits come from the existing
[selected service contract](../../scripts/selected_service.py), not separate
CLI/environment overrides. The day profile accepts an operator-supplied
`--hard-stop-at` no more than 86,400 seconds ahead, captured before preparation
as wall-clock and monotonic bounds. This reuses the existing
[pilot absolute-retirement parser](../../scripts/pilot/serve.py) and its
24-hour ceiling, not a new engine flag or an indefinite process mode.

For original host cutoff H:

- The host owner and independent watchdog expire at H. The watchdog admits the
  full original lifetime before reserving cleanup time, not a discounted bound.
- The container receives the same profile and H minus the existing 180-second
  host cleanup reserve.
- The guard's existing retirement flag and catalog lifecycle deadline are that
  container cutoff minus the existing 375-second drain/reap reserve. The Go API
  and catalog code are unchanged.
- The 1,800-second startup budget, per-operation timeouts, request limits,
  8,192-token context, 256-token output and single admission remain unchanged.

A forward wall-clock jump permanently shortens remaining time; rollback cannot
extend the running host/watchdog/container monotonic bound. Delayed preparation
or readiness consumes the original budget. An expired start is rejected. There
is no timer reset, renewal protocol, restart loop, service-manager installation
or second create/start.

## Binding and compatibility

The day profile uses version-2 network/container intent envelopes. Each carries
the closed profile and original cutoff. The watchdog reconstructs and verifies
the exact private network journal; the container intent must match it. Its
recorded launch must contain exactly one matching profile and derived cutoff,
without a smoke mode. Default version-1 journals and default generated command
bytes remain unchanged.

The day lifecycle receipt uses `nib.selected-service-lifecycle/v6`, retaining
the existing readiness, failure, front-first drain and reaping fields. It adds
only the profile, absolute retirement boundaries and prepared-source digest.
The host joins these against its original contract and approved build before
accepting a result. Old/default receipts cannot satisfy the day join. Existing
default receipts are unchanged.

Only the day profile emits a bounded sequence of content-free startup/drain
records: `preparation`, `inputs`, `weights`, `engine-starting`, `guard-starting`,
`tls-starting`, `ready`, `draining`. The container flushes these records through
its existing attached stdout. The host reuses the existing bounded process-group
reader's output sink, validates exact schema/order/profile/cutoffs/prepared digest,
and reconstructs only those records on host stderr with an observation timestamp.
Its stdout remains the single terminal host receipt. No raw Docker output,
child stderr, paths, prompts, credentials or model content is forwarded.

The whole attached stdout remains capped at 16 KiB, including its terminal
receipt. Malformed, duplicate, extra or oversized records fail closed. A
successful terminal join requires the complete phase sequence; an early failure
can retain its partial sequence without becoming PASS. Reporting failure still
runs existing cleanup. No new mount, listener, thread, heartbeat, percentage,
ETA or timeout is introduced. A phase describes an observation, not ownership
proof, continued readiness or an uptime guarantee. `engine-starting` may remain
the last phase throughout a long bounded engine load.

The host receipt uses `nib.selected-service-owner/v2` for this profile and also
records the original outer cutoff. Cleanup, quarantine, private-input removal,
image labels, resource caps, closed venue, DNS, ownership and isolation checks
are unchanged. All qualification/privacy flags remain false. TrustedRouter
continues to own billing; there is no new accounting store.

## Fresh preparation and image required

An older approved service image remains valid for its existing smoke contract,
but cannot implement the new lifetime protocol. Day admission requires the
authenticated preparation's embedded `selected_service.py` and
`selected_service_process.py` to equal the reviewed host source, before image
inspection or private-input staging. The final receipt additionally joins the
exact prepared digest. Missing or old owner source is rejected.

After the final source is frozen, use the existing
[image preparation and build pipeline](../../scripts/selected_service_image.py)
to create a new immutable preparation, generated Dockerfile/context, prepared
receipt and image from that exact commit. Preserve earlier artifacts. The
builder already hashes the Python source inventory and requires committed
source equality, and its build receipt joins the resulting image ID. Reuse the
approved base, model weights, metadata and binaries only through those checks;
do not relabel an old image as supporting the day profile. No new base,
interpreter, engine arguments or model flags are introduced.

## Verification and later work

[Focused tests](../../scripts/test_selected_service_lifetime.py) use fake clocks,
owned-process fixtures and synthetic Docker observations. They cover the exact
4,800/86,400-second boundaries, unknown profiles, source/profile/cutoff mismatch,
clock jumps, expired restart, smoke conflicts, original reserves, catalog flag,
closed receipts, fragmented progress, privacy canaries, and cleanup failures. Existing lifecycle,
network, owner, image and recovery tests remain required.

This increment does not implement automatic renewal, certificate/key rotation,
external probe scheduling or seamless handover. Input snapshots remain immutable;
a later approved run needs new snapshots and proven cleanup of the old run.
Cold-loading the single eight-GPU engine still causes a service gap between
runs. A day-profile software test is neither a 24-hour soak nor 24/7 evidence.
