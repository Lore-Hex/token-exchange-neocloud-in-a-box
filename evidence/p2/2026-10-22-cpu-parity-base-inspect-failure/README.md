# Native CPU Base Inspection Failure

Original two-file artifact from
[workflow 39853952934 attempt 1](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39853952934),
producer `099396af3218e718ab1ba763f178af02f6efce9e`. Downloaded bytes are
preserved without rewriting. The actual recorder ran on the native
GitHub-hosted Linux/amd64 runner from **2026-10-22 21:03:19 to 21:03:30 UTC**.

| File | SHA-256 |
| --- | --- |
| `run.json` | `c73895f9c81402f7e679b89570bf9cd542ec7af523fb336e3b653b2544639a0e` |
| `output.log` | `9ea6a7bbe96f2e7eb63429b7f4c97684ff0a8941242483733a6d8ba87ef46668` |

The workflow's fixed public-input preparation passed. The recorder **failed**:
stage `inputs`, 21 source hashes and context/base-image pins present, no image
ID and no runtime result. Source control flow places this failure at the
pre-build base-image inspection. Owned container/image absence and build
completion were confirmed; no build was started. These cleanup fields do not
claim deletion of downloaded inputs or base/BuildKit caches.

The original receipt does not retain the inspect exit status or stderr. The
[source-backed diagnosis and narrow correction](../../../docs/tasks/p2-t3-tau-cpu-native.md#native-base-inspection-failure)
therefore distinguish the observed failed boundary from the independently
established Docker CLI incompatibility. There was no tokenizer, process-argv,
model inference, parity, live-admission, tau, quality or confidential result.
In particular, this native failure does not confirm or disprove the separate
OrbStack argv mismatch. No automatic retry was performed.
