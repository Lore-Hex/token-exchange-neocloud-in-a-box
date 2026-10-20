# P2.T3: appraisal-gated lab readiness

This is an explicit, operator-approved transition on an existing eight-GPU test
host, not a manual readiness bypass or production admission. Default inspection
and `--attest` remain nonmutating with respect to readiness. Only
`--attest --enable-readiness` can call the setter. It cannot be combined with
installation. Already installed packages must not be reinstalled to retry an
inspection failure.

## Transition contract

The helper retains the [pinned appraisal contract](p2-t3-nvidia-attestation.md):
exact installed packages, actual CLI help/version assertions, fresh nonce,
two successful NVIDIA verifier calls with no policy override, eight unique
authenticated GPU claims and equality with the local certificate identities.
Raw tokens, certificate identities and nonce values are never recorded.

Before enabling readiness it additionally requires:

- Root and a nonblocking owner-private lock at `/run/nib-lab-gpu-readiness.lock`.
  The lock is never removed and covers appraisal through final NVML shutdown.
- One live NVML session throughout appraisal, rechecks, setter and readback.
- All eight existing persistence flags equal enabled. The helper does not
  enable persistence, install a daemon or change CC/devtools/multi-GPU mode.
- The original device identities, certificates, persistence and CC configuration
  unchanged, and the shared appraisal deadline still live immediately before
  the setter.
- One setter call at most, only when readiness was zero; readiness equal one and
  another identical inventory before reporting `enabled-readback-verified`.
  Already-ready devices still need fresh appraisal and all preconditions, but
  skip the setter and report `already-ready-verified`.

The ABI and constant are taken from the immutable
[NVIDIA NVML header](https://github.com/NVIDIA/go-nvml/blob/1a1e86da474d340d7cf58274588f4a4afc8772e7/pkg/nvml/nvml.h):
`nvmlSystemSetConfComputeGpusReadyState(unsigned int)`,
`NVML_CC_ACCEPTING_CLIENT_REQUESTS_TRUE == 1`, `NVML_SUCCESS == 0`.
This is a system-wide API, not a per-device setter. The
[persistence documentation](https://docs.nvidia.com/deploy/driver-persistence/persistence-mode-legacy.html)
describes retaining GPU initialization without an application client; observing
that flag is not a promise that readiness persists indefinitely.

There is no atomic hardware operation spanning remote verification and readiness.
The operator must reserve an exclusive state-change window: the lock serializes
this helper, not unrelated privileged tools. Host OS/Python/NVML integrity and
other root processes remain trust assumptions. The final readiness observation
is before NVML shutdown; successful shutdown is separately required. A timeout,
signal, setter error, failed readback or failed shutdown can leave readiness
changed. Such a run fails, is not retried or automatically rolled back, and does
not authorize engine launch.

The worker remains bounded to 285 seconds, including preflight and cleanup;
appraisal and transition share the existing 240-second deadline. Output and
process-group limits are unchanged. No new service endpoint, credentials, raw
exceptions, UUIDs, device paths or arbitrary strings enter the report.
Claims-policy failures include only an allowlisted failure code, source
(`verified-cli`, `signed-token`, `attested-cli` or comparison) and known public
claim-field name. Missing security claims, malformed, false and expired claims
still reject. Only the documented
[four raw serialization fields](p2-t3-nvidia-attestation.md#raw-token-serialization-compatibility)
can be absent after successful signature verification; no security assertion is
defaulted.
No rejected value, identity, nonce or token is returned; a shutdown error does
not replace the original claim rejection.

## Source-bound recording

`host/record-gpu-appraisal.py` requires a local Git checkout containing the
reviewed commit, not just an archive with an asserted SHA. It reads four committed
blobs: itself, the appraiser, `host/probe.py` and `host/record-probe.py`. Consumed
helper bytes and self-bootstrap bytes must match; helper hashes are also pinned.
Git replacements, lazy fetching, network protocols and prompts are disabled.
Source reads reject symlinks, hard links, special files and oversized inputs.

The recorder reuses the probe's bounded process-group capture and private,
no-overwrite, fsynced evidence writer. It runs committed source snapshots with
`-I -S -B`, not cached bytecode or ambient module imports. Both child source files
are rechecked after execution. It writes `run.json` and `output.log` under a
private `evidence/p2/<date>-gpu-appraisal-<time>/` directory. The records include
UTC interval, exact commit, source hashes, fixed operation, sanitized observations
and separate failure/shutdown outcomes. Package-directory arguments are
explicitly normalized, not represented as literal recorded paths.

After the corresponding operator authorization, the committed commands are:

```sh
# Fresh appraisal only; no readiness setter:
python3 -I -S -B host/record-gpu-appraisal.py --attest

# A separately authorized state transition; fresh appraisal is mandatory:
python3 -I -S -B host/record-gpu-appraisal.py --attest --enable-readiness
```

Do not run both merely to warm state or retry an unchanged failure. An earlier
audit result or saved JWT cannot authorize a later transition.

These are software appraisal observations, not GPU execution tests. Records use
`execution:software`, `hardware_validated:false`, null driver/CUDA fields, and
always retain `runtime_ready:false`, `confidential_qualified:false`,
`deployable:false`. Even a successful transition is not CPU, NVSwitch/fabric,
image, model, TLS or service qualification. AMD SNP does not satisfy the Intel
TDX/GCP production gates.

## Software verification

Fake NVML/CLI tests exercise fresh appraisal before the setter, every rejection
boundary, unchanged configuration, persistence, already-ready behavior, setter
ABI, failed readback/shutdown and lock lifecycle. Recorder tests cover committed
source substitution, unsafe files, timeout/cancellation, output privacy,
no-overwrite, Git transport suppression and real offline `nib lint` acceptance.
The negative schema control still rejects `execution:hardware` with null
driver/CUDA; the schema is not weakened. These tests authenticate no real token
and perform no hardware transition.
