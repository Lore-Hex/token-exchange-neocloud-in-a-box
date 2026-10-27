# Authenticated offline telecom catalog

Status: offline catalog extraction and staged binding implemented. No paid
request, token-count parity, live simulator admission, benchmark success, or
confidential-computing result is claimed.

## Source and semantics

The original upstream revision is
`fc0055dc4e0a316c3f83133267fbd6faaa770992` of
[sierra-research/tau2-bench](https://github.com/sierra-research/tau2-bench/tree/fc0055dc4e0a316c3f83133267fbd6faaa770992).
The existing runtime verifier authenticates the archive SHA256, complete
`src/tau2` Git tree, pinned data files, and hash-locked dependency wheels before
any upstream import. The recorder then runs only in the existing pinned
Python 3.12.13 Linux/amd64 image, without network, credentials, image pulls,
writable inputs, or a live owner. It retains the existing container isolation,
bounded subprocess supervision, monotonic deadline and owned-only cleanup.

The extraction calls the original `telecom.environment.get_environment()` and
`Environment.get_user_tools()`, which uses `ToolKitBase.get_tools()` and
`Tool.openai_schema`. It does not recreate signatures or parse docstrings with a
new implementation. Pydantic 2.12.4, pydantic-core 2.41.5 and docstring-parser
0.17.0 remain pinned by the existing runtime lock. Definition order,
descriptions, defaults, nullability, enums, nested definitions and required
fields are retained as upstream emits them.

The original runner filters user tools with `include=task.user_tools`. The
recorder repeats that exact lookup for the first five lexicographic telecom
`base` tasks, confirms all five expose the same 30-tool catalog, and retains
their unmodified filters. A different task filter fails this contract; it is
never repaired by restoring missing tools. No task initialization, tool method,
or orchestrator step is executed by extraction. The base split has 114 tasks;
this contract covers the existing first-five staged selection only.

The source-derived public artifact is [the catalog](../../bench/tau-telecom-catalog.json).
It carries the [upstream MIT notice](../../bench/tau-telecom-catalog.LICENSE). Its canonical bytes and tool-list
digest are pinned separately. Reproduction is mechanical, not handwritten:

```sh
python3 -I -S -B scripts/record-tau-telecom-catalog.py \
  --source-archive /absolute/pinned-tau-archive.tar.gz \
  --wheelhouse /absolute/verified-wheelhouse \
  --docker-socket /absolute/local-docker.sock \
  --output /absolute/new-catalog.json \
  --receipt /absolute/new-receipt.json
```

Output must not already exist. Compare it byte-for-byte with the committed
catalog. Public schemas alone may be persisted; requests, tool arguments and
results from projection remain private in memory.
The content-free receipt records the source commit, working-tree state, exact
runtime/source identities, catalog digests, timestamps and owned cleanup.
Failures and cancellation also produce an exclusive content-free receipt with
`result: fail`, `completed: false`, `source_authenticated: false`, a finite stage
and cleanup status. Existing receipts are never overwritten or followed through
symlinks. No upstream exception text appears in a receipt or CLI diagnostic.
Successful extraction does not authenticate request history or task execution.
The [recorded extraction](../../evidence/p2/2026-10-27-telecom-catalog/run.json)
was produced from clean implementation commit `f7bb2cb2a4751181942c6451c2b398899376d2bd`;
the [raw content-free output](../../evidence/p2/2026-10-27-telecom-catalog/output.log)
records the same identities. Three local extractions were byte-identical.
The [failure-receipt implementation reproduction](../../evidence/p2/2026-10-27-telecom-catalog-receiptfix/run.json)
from clean commit `1dcdbb52c529c658401de8fddd0881cfdde81f18` also matched the
catalog byte-for-byte, with owned container removal confirmed. A separate
[deliberately missing-source run](../../evidence/p2/2026-10-27-telecom-catalog-rejected-source/run.json)
exited 1 and retained `source_verification` failure provenance, false completion
and authentication, and `not_created` cleanup. It created no catalog or
container. These local software checks are not live parity or budget evidence.

## New staged contract

`tau_telecom_catalog.bind` creates `nib.tau-telecom-catalog-stage/v1`. It verifies
the archive again, authenticates the original selection-plan task/split files,
requires a telecom selection, and joins that selected task to the pinned
source-derived catalog. The contract contains the unchanged original plan,
selection binding, archive/tree/runtime identities, and both catalog hashes.
Caller-supplied schemas or an asserted `source_authenticated` flag are not
accepted as evidence. `validate` rebuilds the binding; `project` authenticates
it before invoking the merged PR438 projection with the pinned original tools.

`source_authenticated: true` means the catalog is bound to the reproduced
pinned source, not that Chat/Responses counts agree or that a real trajectory
succeeded. `token_count_parity_verified`, `live_admission`, `execution_authorized`
and `quality_eligible` stay false. Live policy validation rejects this new
contract. No live owner imports the new module, and the existing text-only
simulator path, candidate 16-tool limit, per-frame limits, count reservations,
pricing, weekly commitment declarations, retries and deadlines are unchanged.

## Verification and remaining gates

```sh
python3 -B -m unittest discover -s scripts -p 'test_tau_telecom_catalog.py'
NIB_TAU_SOURCE_ARCHIVE=/absolute/pinned-tau-archive.tar.gz \
  python3 -B -m unittest discover -s scripts -p 'test_tau_telecom_catalog.py'
make check
make test-e2e
```

The explicit archive test authenticates each selected original task through the
complete source reader, binds its catalog, then projects a synthetic tool
history into both requests. Unit tests also cover tampered catalogs/contracts,
selection rebinding, failed source checks, expired deadlines, retained private
semantics, unchanged live rejection, and exact container entrypoint/isolation.
Failure tests exercise the CLI signal handlers, start and cleanup errors,
pre-container failures, available provenance, repeated cancellation during
cleanup, and preservation of existing or concurrently created receipts.
Synthetic budget inputs in tests are not operational commitment evidence.

Future owner integration must carry this binding across the existing verified
worker/parent channel before opening a distinct admission path. It must preserve
count-before-generation, measured-count equality, known usage on failures, and
finite lifetime/drain behavior. Live count parity still requires separately
approved budget reconciliation and a bounded smoke; this change neither grants
that approval nor alters upstream simulation or evaluation semantics.
