# Bounded Concurrent Input Fetches

The native build at
[run 39710390335](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39710390335)
used producer `8254d5c8cb43343a32677dd53eb5bddb8fc25313`. On October 22,
2026, its input-fetch stage ran from 02:16:24 to 02:26:26 UTC and failed before
package installation. The original
[output](../../evidence/p3/2026-10-22-bounded-fetch-deadline/output.log)
records five transient failures followed by retries; later inputs were reached,
so those retries recovered. The aggregate 600-second deadline then expired.
The public bytes have SHA-256
`8a65931bf7ec36c40a43895153298458a32acbfb3514597599229e28135779cf`.
No build `run.json` was produced; none is fabricated here.

The fixed guest/tools union contains 269 archives totaling 516,283,494 bytes.
Serial transfers made independent slow downloads accumulate against the shared
deadline. This evidence does not identify the underlying network fault or prove
an upstream outage. Increasing retries or the time budget is not this change.

The CLI now fetches at most four inputs concurrently. All workers use the same
600-second deadline and existing per-attempt process-group supervisor. There
are never more than four submitted transfers. On the first observed failure,
the coordinator stops admitting work, cancels queued work, prevents retries
from starting and joins active workers before returning. An in-flight attempt
retains its existing maximum 60-second bound and bounded process cleanup.

The exact host, path, HTTPS, byte, digest and archive-set checks are unchanged.
All inputs still have to pass before any output directory is published. No
partial download cache, package execution, mirror substitution, proxy, redirect,
TLS relaxation, credential or raised deadline is introduced. Retry records use
a lock to keep each fixed, content-free JSON record intact across workers.

The single-worker library default remains available for deterministic callers;
the only accepted worker counts are 1 and 4. Tests cover overlap, the four-worker
ceiling, cancellation and joining, the shared deadline, unchanged content/TLS
rejection, verified metadata reuse and concurrent log framing. Synthetic tests
are not native installation evidence. Boot, UKI/verity, full attestation and
production qualification remain unresolved.
