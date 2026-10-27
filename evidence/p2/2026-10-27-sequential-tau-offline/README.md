# Sequential Controller Offline Replay

The committed producer is `a0852bda1dc1b892f6992b17c6b303653427cc06`.
Both controller and original scorer ran with Docker networking disabled.
The scorer used the existing pinned archive, wheelhouse, image inspection,
payload authentication and owned cleanup. The controller image contained only
cached Python/Git and Docker CLI tools. No host listening port was used.

Fifteen original telecom task/trial rows were frozen under a **synthetic**
$70.044864 cap. One original task ran with fixed peers: two candidate and three
simulator generations, three synthetic count calls, and one tool round trip
for each. The original zero-assertion evaluator made no judge calls. The
original reward was `0.0`, not a measured quality result.

The completed owned receipt settled a synthetic conservative reservation of
$6.2862129, including $6.285456 still reserved for the three count attempts.
The remaining $63.7586511 could not cover the next complete $70.044864 trial.
The controller therefore stopped `incomplete` / `cap-exhausted` after one of
15 rows, without changing the plan or dispatching a replacement. Cleanup was
confirmed; no owned scorer container remained.

`receipt.json`, `output.log` and the entire `journal/` retain their original
producer bytes. `run.json` is only the evidence envelope produced by the
committed recorder's `evidence_record` helper. The earlier socket-permission
failure is retained separately in
[the failed run](../2026-10-27-sequential-tau-offline-socket-denied/README.md).

There were zero paid generation or counting calls. These observations do not
establish tokenizer parity, billing correctness, available credit, benchmark
quality, full tau coverage, live admission or confidential execution.
