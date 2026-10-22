# CPU Readiness Diagnostics

The [original actual CPU failure](../../evidence/p2/2026-10-22-cpu-parity-readiness-failure/README.md)
compiled both pinned native targets and completed the independent 0.6B helper,
but failed readiness before endpoint token comparison. Its cause is not known
from that receipt. In particular, no emulator, engine flag, deadline or procfs
check is blamed without a diagnostic observation.

The [existing producer](p2-t3-tau-cpu-parity.md) now retains a closed `failure`
object on a caught worker exception. It contains one fixed category, numeric
errno when available, the server's exit code observed before our cleanup signals
(null when still running or no server exists), and at most eight source/line
frames from six explicitly named committed helpers. Parent validation rejects
unknown sources, fields, categories, boolean numbers and out-of-source line
numbers. These helpers already belong to the rehashed source closure. No
exception text, traceback rendering, locals, native stderr or dynamic paths are
captured. A successful receipt cannot contain a failure diagnostic.

All source/model hashes, unchanged four probes, process/socket checks, deadlines,
container bounds, no-content logging and cleanup rules remain in force. No
emulation fallback or live admission is added. A real diagnostic rerun still
requires committed final-head review, full tests and CI; the new fixture tests
do not establish the cause or a parity pass.

The immutable failure receipt includes the already-pinned public probe source
hash. Its scanner exception requires that exact value, JSON field and evidence
path together. The existing positive-control loop checks unknown values,
different paths and an `api_key` field still fail; no original bytes are scrubbed
or edited to avoid the detector.
