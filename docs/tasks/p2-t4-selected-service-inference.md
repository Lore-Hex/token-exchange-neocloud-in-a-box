# Selected-service authenticated inference smoke

This additive, software-only helper follows the
[selected lifecycle](p2-t4-selected-service-lifecycle.md) and
[offline image builder](p2-t4-selected-service-image.md). It does not install a
post-ready hook, change the host smoke owner, rebuild an image, expose a port
or run inference. Tests use mocked transports, including an isolated real
alarm/HTTP-parser test with a trickling in-memory source and no listening port.

## Closed interface

[`selected_service_inference.run_once(supervisor)`](../../scripts/selected_service_inference.py)
is a trusted in-process call from the existing owner/main thread. It requires
the concrete supervisor, process owner and prepared-runtime types, all three
registered children, and the original engine identity. The helper is consumed
on its first invocation, including failure; there is no retry. There is no CLI,
URL, model, prompt, request-count, capacity or private-input override.

The only network target is the owned `127.0.0.1:8443` TLS listener. The helper
reuses the process owner's PID/start-time/argv/executable/listener binding,
accepted-peer check, certificate/name trust, immutable private snapshots and
prepared-mount checks. Ownership is proved before sending any credential, and
all three children and original engine are rechecked after every response.
The key is read from the existing private snapshot and validated with the
pilot helper; it is never passed through a new configuration protocol.

The prepared receipt already binds every Python source and guard/TLS binary.
The helper additionally checks its executing bytes against that receipt. Its
content-free report carries the exact prepared-receipt hash for joining to the
approved image. `contract()` describes the fixed request/cases/bounds; its hash
identifies those semantics, not a separately supplied runtime configuration.

## Exactly three requests

In order, the helper sends the same fixed synthetic, nonstreaming chat body:

1. Missing key: require HTTP 401 and the guard's exact `invalid_api_key` error.
2. Wrong synthetic key: require the same rejection.
3. Authenticated: require HTTP 200, the canonical selected model ID, one
   assistant choice with nonempty visible text, no tool calls, and actual
   positive integer prompt/completion usage with an exactly consistent total.

Each rejection stops the sequence; there is at most one authenticated request.
The request uses the existing `service.OUTPUT` **256-token** maximum, temperature
zero and no reasoning controls. A smaller limit could consume the entire output
budget before visible content. Even at 256, an empty/all-reasoning response is a
failed smoke with its validated usage preserved, not permission to retry or
increase limits. A `length` finish with visible content proves only a bounded
functional response, not answer completeness or quality.

The existing [completion validator](../../scripts/engine_local_correctness.py)
checks model identity, strict JSON, token integer types/bounds and total equality.
Its 4096 prompt-token sanity ceiling applies to this tiny fixed probe, not the
service's unchanged 8192-token context. The selected
[API profile](../../internal/pilotapi/profile.go) still enforces one admission,
256 output tokens and no tool/reasoning controls. No wire model rewriting or
parser/cap changes are introduced.

HTTP 401 alone does **not** measure zero upstream inference. The report therefore
always sets `auth_upstream_calls_measured=false`. The separate nonforwarding
basis is the source-bound guard's authentication-before-forwarding order in
[`ServeHTTP`](../../internal/pilotapi/handler.go) and the existing transport-count
regression `TestSelectedProfileRejectionsFailureAndRetirement` in
[`profile_test.go`](../../internal/pilotapi/profile_test.go). That test proves
missing/wrong keys do not call its mocked upstream. A future live report must
not recast this software proof as an observed engine request count.

## Bounds and reporting

The entire call is bounded to 140 seconds, each negative to 5 seconds, and the
positive to 120 seconds (matching the guard's existing inference bound). Every
bound is clipped to the original admission cutoff, preserving the 375-second
cleanup reserve. The fixed shared absolute alarm covers connect, TLS, slow
headers/body, parsing and finalization; per-receive timeouts cannot renew it.
Forward wall-clock jumps shorten the original bound; rollback cannot extend it.
SIGINT/SIGTERM mark the existing shutdown event, interrupt the helper, and
restore prior handlers. Main-thread execution is mandatory.

The strict report contains only fixed categories, booleans, public source/
contract hashes and validated token counts. No prompts, response bodies,
reasoning, keys, headers, hostnames or exception text are printed or returned.
An attempted authenticated request with no validated response has
`usage_unknown=true`, never fabricated zero usage. Once usage is validated it
survives later content, identity, deadline, cancellation or resource-close
failure. PASS is set only after all checks and finalization succeed. A report
with unverified prepared provenance cannot pass the receipt-join validator.

All deployment, hardware-validation, provider-conformance, qualification,
streaming, tools, reasoning and privacy claims remain false. This is a narrow
functional smoke, not provider-check Tiers 1-4, a tau result or a quality gate.

## Next reviewed integration

The minimal follow-up is an explicit opt-in call in `Supervisor.run`, immediately
after all three roles are ready, on the same retained engine. It must preserve
the original deadline, make a failed helper report a sticky lifecycle failure,
and always run the existing front-first drain/reap. The host receipt join must
validate the helper report against the approved prepared receipt and require
both successful inference and confirmed lifecycle cleanup; neither substitutes
for the other. No existing owner or builder file is changed in this increment.

That hook must use the canonical `selected_service_process` module for the real
owner/prepared classes. The current script entrypoint defines them under
`__main__`; independently importing the module creates different class objects
and intentionally fails the helper's exact-type checks. Normalize that script
entrypoint in the separately reviewed hook, and test the actual isolated CLI
path with mocked launches, not only imported-class fixtures.

That integrated source needs a new exact prepared receipt, image build,
independent final-head review and CI before any separately authorized private
network-none run. Existing images do not acquire this helper or hook implicitly.
There is no operator inference command in this library-only increment.

Focused, no-port verification from the repository root:

```sh
PYTHONPATH=scripts python3 -B -W error::ResourceWarning -m unittest -q test_selected_service_inference
python3 scripts/check-doc-links.py
```

No full suite or hardware execution is claimed here.
