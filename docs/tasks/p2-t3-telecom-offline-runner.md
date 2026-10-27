# P2.T3: original telecom runner with offline peers

This is software integration, not a benchmark, real counting, token parity,
quality, confidential inference, live admission or spending authorization.
The original selected source, evaluator, tools and user tools are unchanged.
The live admission, observer and candidate's shared 16-tool limit are unchanged.

## Minimal slice

`scripts/record-tau-telecom-offline.py` authenticates the pinned source archive,
wheel closure, original first-five telecom selection and the catalog introduced
by [the catalog binding](p2-t3-telecom-catalog-binding.md). It prepares and checks
the existing original selected worker, then attaches the existing finite parent
pipe. The container retains network-none, read-only inputs/root, exact image,
non-root user, bounded resources and the existing external-operation audit hook.
No model or credential constructor runs on the host. No live owner imports the
new offline adapter.

Fixed scripted peers exercise the original `check_status_bar` user tool and
`get_customer_by_name` agent tool, require their original tool-result round trips,
then stop. The unchanged original runner evaluates the task with
`ALL_WITH_NL_ASSERTIONS`; zero reward remains a scored failure, not a claimed
quality success. NL assertions, when present, receive explicit false fixture
answers. Task data, tool output and prompts remain in the private pipe/memory;
receipts contain only bounded structural diagnostics, hashes and accounting.

The simulator uses the authenticated 30-tool Chat/Responses projection. Counts
and usage are a fixed **synthetic 100 input / 1 output**, not actual tokenizer
or provider observations. The existing shared count-call cap, generation
reservation, count/request binding, known/unknown usage settlement, monotonic
deadline, process-group reaping and cleanup remain active. Reservation numbers
inside diagnostics are fixture arithmetic, not money spent or available credit.
The fixture selection budget is explicitly synthetic, never operational approval.

The host authenticates its committed helper closure and the worker independently
checks its prepared file closure. Source authentication does not authenticate a
remote endpoint and never sets live, execution, parity or quality eligibility.

## Reproduction

Use the pinned archive and complete wheelhouse described in the catalog task.
The pinned image must already be local; no image pull is allowed. Use a clean
committed producer and unused absolute receipt paths. Each index is one of the
unchanged first-five base selections, with the original staged seed derivation.

```sh
TMPDIR=/private/tmp python3 -I -S -B scripts/record-tau-telecom-offline.py \
  --source-archive /absolute/pinned-source.tar.gz \
  --wheelhouse /absolute/pinned-wheelhouse \
  --docker-socket /absolute/local-docker.sock \
  --index 0 --receipt /absolute/new-receipt.json

PYTHONPATH=scripts python3 -B -m unittest -q test_tau_telecom_offline \
  test_tau_telecom_parser test_tau_pipe_driver \
  test_tau_telecom_catalog test_tau_telecom_projection \
  test_tau_external_trial test_tau_external_selection
```

Set `NIB_TAU_SOURCE_ARCHIVE` to the same absolute archive path for the optional
real-source parser and catalog tests; neither imports upstream code on the host.

The receipt is exclusively created on success **or failure**, with a known stage
and cleanup state, never raw exception text. Existing receipts are not replaced.
Only the uniquely owned container is removed, including after ambiguous create;
cleanup has a separate finite allowance. The committed `evidence_record` helper
packages a standard `run.json` without changing the original `receipt.json`.
Original failed receipts must remain alongside later successful evidence.

## Remaining gates

The initial committed producer `74b980a38bdca8435a890a306204af1cd7ba1ded`
failed before container creation. Its original receipt and output are retained in
`evidence/p2/2026-10-27-telecom-offline-initial-rejected/`. The pinned telecom
`tasks.json` contains 2,285 rows, whereas `tau_pipe_driver.selected_assertions`
requires at most 1,024. The exact source archive authenticated successfully;
the original worker did not run. No source filtering or guard bypass was used.

The shared reader now permits at most 2,285 rows only for telecom bytes matching
the exact SHA-256 and byte count in the authenticated pinned source manifest.
The source manifest itself must match the existing trusted `PINS_SHA` and upstream
revision. There is no caller-controlled bound. Other inputs retain 1,024 rows;
the existing byte, duplicate-key, selected-task uniqueness and bounded assertion
snapshot checks are unchanged. This is source compatibility, not live admission.

## Software evidence

Producer `38e35bcd2f9512eb38d6cf6d86c82d9458639c32` completed selected indices
0 and 1 in the original network-disabled worker. Original receipts and output
are retained in `evidence/p2/2026-10-27-telecom-offline-index0/` and
`evidence/p2/2026-10-27-telecom-offline-index1/`. Each reached `user_stop`, with
two agent and three simulator generations, both original tool round trips,
zero tool errors, original evaluation reward `0.0`, and confirmed attach and
container cleanup. Both original tasks have zero NL assertions, so no LLM
evaluator generation occurred. These are scripted software results, not task
successes by a model. The other three selected tasks have parser/catalog
coverage, not recorded worker execution in this slice.

The focused set passed 100 tests including the authenticated archive checks.
The task file SHA-256 is
`37e562e1ae3242577407e1303b1548bc64e7ea68e37d36173e6747990ceaf8a4`,
13,977,063 bytes, 2,285 rows; the trusted pins manifest and full archive are
verified before accepting this identity. Forged pins, same-size changed bytes,
wrong-size bytes, another domain, 2,286 rows, duplicate keys/tasks, oversized
strings/counts/files and symlinks all retain fail-closed tests.

Complete local/E2E checks remain required. Independent final-head review and hosted CI are required
before merge. Live count/generation parity, reconciled budget approval, source-
bound live support, a competent candidate and full multi-domain matched-reference
evaluation remain separate gates. This slice does not satisfy P2.T3 quality.
