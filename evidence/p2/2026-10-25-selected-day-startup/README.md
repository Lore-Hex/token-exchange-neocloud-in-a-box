# Ordinary Day-Profile Startup

These are original metadata-only receipts, not engine logs or model text.
The first owner attempt failed before starting a container. It remains FAIL;
neither the successful build nor a later attempt repairs that result.

## Build and First Attempt

`build.json` records a completed local image build. Its SHA-256 is
`01adccd8995ec65b7bea04d941c6cf9bec1adb78ba5c2fbbc85610cfa87e379c`.
The original build record explicitly leaves runtime, publication, deployment
and hardware validation false. The base binding trusts the local Docker daemon;
it does not claim independent rehashing of local image configuration or layers.

`failed-owner.json` joins that exact build receipt, image and preparation.
It records `result=fail`, `status=BLOCKED`, `cleanup=not-started`, no created
container, no started runtime and private-input removal. Its original
`preflight_pass=true` and `reason=not-run` are misleading diagnostics, not proof
that every launch prerequisite passed; they are preserved rather than rewritten.

The operator observed that the required empty, root-owned `0700` state
directory had not been created. At the operator-attributed source revision
`4e0bfd7052d9a1e74d9fa83dda54272efd425351`, inspect does not check that directory,
and run calls `state_directories` before resetting preflight/reason. That source
explains why an early rejection can retain those fields. The receipt does not
itself record the failing filesystem operation. Neither owner nor build receipt
embeds a Git revision, start/end time or the requested day cutoff; attribution
to this source and invocation is operator evidence, not a cryptographic join.

`cancelled-startup.json` is the separate external observer's original failure.
It made six unsuccessful transport attempts, then was cancelled: no verified
TLS peer, no health request, no credential access and no model request. Its
source closure and timestamps are embedded, unlike the owner receipt.

## Separate Corrected Attempt

The operator started a separate owner with a new explicitly prepared state
directory and the same image, inputs and original fixed cutoff. No running
owner was extended or replaced. The operator-observed `ready` phase was
07:20:39 UTC. That association with the build is not embedded in the external
client receipts and is not remote software attestation.

`r2-startup.json` records a credential-free, certificate-pinned health PASS at
07:20:40 UTC: 69 attempts, 68 transport failures while starting, one verified
TLS connection and one successful health request. No model request occurred.

`r2-probe.json` then records one bounded external smoke from 07:21:26 to
07:21:32 UTC: health, missing-key rejection, wrong-key rejection and one
authenticated stream all passed, with a separately verified TLS peer for each.
The stream reported 36 prompt tokens and 13 completion tokens, stopped normally,
and delivered ten output events. Client-observed first content was 2.630 seconds
and the inference exchange took 3.370 seconds. This single short cross-network
sample is not a throughput benchmark, uptime measurement or SLA. The probe does
not measure upstream call counts for rejected credentials. No retry occurred.

The still-running owner's planned admission cutoff is 2026-10-26 06:40:45 UTC,
child cutoff 06:47 and outer cleanup cutoff 06:50, as operator context only.
This evidence does not include terminal owner completion or cleanup. The
planned run is shorter than 24 hours, and the external clients prove only the
observed API exchanges, not continuous readiness. There is no public trusted
DNS/TLS, production gateway settlement, full provider conformance, tau quality
result, confidential-compute or E2EE claim. Earlier failures remain failures.

The [replay tests](../../../scripts/test_selected_day_startup_evidence.py) pin
the original bytes, input joins, source closure and false-capability fields.
Private addresses, credentials, paths and raw process output are not published.
