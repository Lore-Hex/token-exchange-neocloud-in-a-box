# CPU topology attempt stopped before build

**Failed; no guest boot or topology proof.** The first authorized topology
attempt was [workflow run 40509832521, attempt 1](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/40509832521),
producer `4e0c065cf37b24108e5c371a05d226958391df8e`, started
2026-10-27T13:11:11Z. The job finished at 13:16:08Z. The manual selection was:

```sh
gh workflow run debug-boot.yml --ref main -f debug_boot=true -f guest_network=true -f debug_topology=true
```

The original failed-step log reports the metadata archive checksum passing at
13:11:24Z and `Pinned input fetch failed; see content-free diagnostic.` at
13:16:04Z. The producer's `scripts/fetch_mkosi_tools.py` emits that message only
for its typed `FetchFailure`. The metadata archive supplies the indexes before
`fetch()` starts, so the remaining fetches are pinned package archives. However,
the workflow redirected the structured stdout failure to `input-fetch.log` and
never uploaded that file. **The failed archive identity, curl status and failure
category are unknown.** Do not infer a timeout, HTTP status, integrity failure or
kernel issue from this generic message or its elapsed time.

The build and debug/QEMU steps were skipped. The unchanged unused-export
validator emitted `validation.json`, a failed
`nib.native-private-export-validation/v1` receipt with `build_result: skipped`,
null installation/resources, independent private-image cleanup and metadata-only
flags. Its 68 native source hashes and two validator hashes bind the original
producer. It is not a topology receipt and has no compiled probe or guest proof.
No synthetic `run.json` or reconstructed package failure has been added.

## Original bytes

These are unchanged originals: the extracted cleanup artifact, GitHub run and
artifact metadata, and the failed-step log. The log contains only public pinned
source URLs, fixed commands and the generic failure; no inference requests were
made, and no response bodies or credentials are in the log. Retrieval used `gh run view` JSON/log
views and the GitHub artifacts API. The original ZIP, retained privately, has
3,761 bytes and SHA256
`e08cde1503f04f01037de5adcdbc48f21f5f9754ca5b6f3ac31ea51398ab3029`, matching
GitHub's artifact digest; its only member is the committed `validation.json`.

| File | Bytes | SHA256 |
| --- | ---: | --- |
| `validation.json` | 8261 | `c10e675221d4e4e2868a0226cbf89a655f9fce736d397f516d5351fb98802f9f` |
| `github-run.json` | 3081 | `270cc96010aa41de9fde757a8e9b8004792a9686b54a5e8617b5f29d8ef250d9` |
| `github-artifacts.json` | 773 | `e96ea19fb439f8779fb48e130fd46af91bda1e393de4b4636c4a9052ca9e63b4` |
| `failed-workflow.log` | 3479 | `ce2b2955e0e18aca6999a099ceac28ec04f4cb379498ad2b2115fc1a562ac58e` |

## Diagnostic correction and limits

The workflow correction leaves the fetcher's already content-free stdout in the
Actions log instead of discarding it. The fetcher itself is unchanged: records
contain only lock size/SHA, fixed failure enums and bounded numeric fields, not
response bytes, stderr, headers, URLs or exception strings. Existing transport
retry records and the terminal failure become visible; no new retry is added.
Shell failure still stops the following runtime fetch, build and boot. Artifact
paths and cleanup-gated JSON-only uploads are unchanged.

Offline tests pin every original byte and source identity, assert the skipped
steps and false proof flags, and exercise the workflow's fetch command against
a local failing stub. They make no network requests and do not bind host ports.
Existing fetcher tests cover content redaction and unchanged finite retries.

```sh
TMPDIR=/private/tmp PYTHONPATH=scripts uv run --project tools --frozen python -m unittest test_debug_fetch_failure_evidence test_debug_topology_workflow test_native_tools test_concurrent_fetch
```

The correction cannot recover the lost diagnostic or prove input recovery.
Any subsequent attempt must follow independent review, normal gates and run
coordination; do not automatically retry this failure. No boot, veth, networkd,
packet isolation, GPU, confidential-computing or phase-completion claim is made.
