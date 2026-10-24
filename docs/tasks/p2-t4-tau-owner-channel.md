# Tau Process-Owner Channel

Status: software prerequisite, tested with synthetic clients and private pipes.
Not wired into a launcher; no hardware, real tau trial or confidential result.

The GPU and CPU support engines have different process owners and network
namespaces. A central benchmark process must not bypass those boundaries to
reach them. `scripts/tau_owner_channel.py` adds a private pipe transport that
delegates count and completion to the existing guarded client beside each
engine. It accepts no URL, credential, endpoint, PID, namespace or executable
from a packet, and does not weaken the existing listener/process checks.

The coordinator still uses the existing `LocalVllmSession`, `Bridge` and
`Budget`. The new owner channel has no independent generation budget. Caller
wiring must replace the admitted endpoints with the channel clients, retain
the single budget, and reserve generation before sending a completion.

Each side is constructed with independently admitted readiness bytes, their
digest, exact served model and context per role, and a finite deadline. A
readiness hash is an operator binding, not authentication or attestation on
its own. The launcher must own the private pipes and admit both owners before
connecting them. Candidate and CPU-support channels are separate; the latter
serves the distinct simulator and evaluator models serially.

Every frame binds the readiness descriptor digest and monotonically increasing
sequence. A successful count binds role, canonical request digest, actual
prompt-token count and deadline. Exactly one identical completion may consume
it, under the same or shorter deadline. Recounts, changed bodies, role swaps,
replays and widening deadlines fail closed before generation. Token/context
bounds are checked on both sides. A failed dispatch cannot be retried using
the same count. Bodies exist only transiently in memory and bounded pipes;
no body logging or persistent transcript is introduced.

Known owner failures use a bound terminal frame with only an allowed status
and the request digest when the existing deadline still allows delivery. The
coordinator preserves that category, closes the channel and never retries.
Malformed or unbound error frames remain protocol failures; expired deadlines
are not extended to deliver an error.

Reuse: the existing bounded `tau_transport.Channel`, strict JSON codec,
`GuardedClient`, `GuardedLlamaClient` and CPU parity `MeasuredClient` retain
their own HTTP and process identity checks. The owner accepts only these exact
client types. Transport framing changes do not qualify arbitrary clients.

The [CPU owner adapter](p2-t4-tau-cpu-owner.md) adds an explicit measured-pair
mode and bound finish acknowledgement. EOF is never a successful release.
The [GPU owner adapter](p2-t4-tau-gpu-owner.md) supplies the matching selected
8K candidate library; live lab/host wiring remains separate work.

Remaining work: receipt admission and live owner/scorer launcher wiring,
selected-checkpoint operational policy, fresh full-prefix/history fitting,
and an actual upstream tau trial. The frozen original-model quality benchmark
is unchanged. The [8K prerequisite](p2-t4-tau-context.md) alone is not a trial.

Validation: `test_tau_owner_channel.py`, existing tau transport/local-client
tests, full `make check`, hosted CI and independent final-head review.
