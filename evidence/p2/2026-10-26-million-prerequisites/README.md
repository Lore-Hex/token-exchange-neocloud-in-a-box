# 1M candidate prerequisites: passed, no inference qualification

These original, content-free receipts record preparation on an ordinary
eight-B300 test node on October 26, 2026. They do **not** demonstrate 1M-context
inference, 384K output, public availability, quality or confidential computing.
The bounded inference experiment is separate; its terminal result is not part
of this checkpoint.

| Artifact | Observation |
| --- | --- |
| [weights/run.json](weights/run.json), [output.log](weights/output.log) | All 79 selected-checkpoint files downloaded and verified: 510,311,056,155 bytes; 18:26:16-20:45:17 UTC, within the original three-hour deadline |
| [original-failure/summary.json](original-failure/summary.json) | Original host baseline rejected at pre-snapshot; no successful proof or cleanup is claimed |
| [network/summary.json](network/summary.json), [observation.json](network/observation.json) | Subsequent isolation proof passed, 20:47:59-20:49:01 UTC; host firewall and DNS unchanged, test namespace reaped |
| [weight-view.json](weight-view.json) | Separate read-only copy verified, 20:50:08-21:06:49 UTC; original source preserved, worker exit confirmed |
| [preparation.json](preparation.json), [build.json](build.json) | Exact source preparation and derived image build passed; runtime/deployment flags remain false |
| [inspect.json](inspect.json) | Source-bound serving preflight passed; no container or runtime started by this inspection |

Weight staging used producer `cec513f7224659a6f5e59704c07398ff67dd62b1` and
[record-stage.py](../../../host/record-stage.py). Subsequent preparation used
reviewed producer `41058d99a2b2dee790cdd9679bdcf5dabaa325be` and the committed
[host baseline](../../../scripts/selected_host_baseline.py),
[weight-view preparer](../../../host/prepare-selected-weight-view.py),
[image builder](../../../scripts/selected_service_image.py) and
[serving owner](../../../host/record-selected-service.py).

The first baseline encountered Ubuntu's newer zlib security package, which did
not match the existing exact host-library pin. PR #421 verified the official
package and updated the exact pin; the old failure remains preserved. The
subsequent proof allowed only its two TLS controls and blocked the other 13
paths. Quarantine blocked all 15 paths. This synthetic network experiment did
not use the GPUs or establish a confidential-computing boundary. Raw host
firewall/DNS captures remain private.

The weight manifest is
`5c6891a9649702b1ac0327b945600acb40c571a069942517cf14ba661a3cfcbc`, for
`dealignai/DeepSeek-V4.1-Flash-1017-UNCENSORED-FP8` at revision
`c19ca5ca4eaeb79d29fdf08aea8327da95b1116a`. The new copy uses `0444` files and
`0555` directories beneath a private wrapper. Host root remains trusted and
could change it; the runtime independently checks the manifest before serving.

The inspected profile is `long1m-graphs`: 1,048,576 combined context, 393,216
maximum requested output, one admitted request. The inspection's `result=fail`
and `reason=not-run` deliberately prevent preflight from masquerading as a
completed service run, despite `preflight_pass=true`. Its fixed owner cutoff
is October 27 at 18:00 UTC; this is a bound, not an uptime promise.

No model text, API key, TLS material, public address or deployment identity is
included. Original artifact bytes are unchanged. Offline replay:

```sh
python3 -m unittest discover -s scripts -p test_million_prerequisite_evidence.py
```

Replay verifies hashes, historical producer bindings, manifest totals,
isolation controls, build/preparation joins and the distinction between
preflight and completed inference. The [qualification and marketplace
gates](../../../docs/tasks/p2-t3-million-context.md) remain unchanged.
