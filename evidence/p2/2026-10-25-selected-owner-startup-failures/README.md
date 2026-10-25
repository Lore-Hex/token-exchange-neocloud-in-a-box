# Finite Selected Owner: Startup Failures and Separate Recovery

These are byte-identical original metadata receipts from two bounded attempts
on 2026-10-25. Neither receipt reports a created container, started runtime or
requested TLS publication. Neither is an inference, network-isolation,
cleanup-success or deployment proof.

| Original | Operator-identified source | SHA-256 |
| --- | --- | --- |
| [owner-8fcfe413.json](owner-8fcfe413.json) | `8fcfe41338d9ca755462044dcc6f165357516518` | `3f08aaf1daa271c191329f72158591081166fea572fb2908abc090f160db8274` |
| [owner-be7b2fc.json](owner-be7b2fc.json) | `be7b2fcc6082b3c0afa1b7b8d536c33ce05ca6d3` | `28c22be33ba828636cc1af6c592984953e56a6b838fb4427928f02858c072992` |

Both retain the same exact image, build, preparation and prepared-payload
identifiers. The receipts contain no producer revision or timestamps; the
source attribution above comes from the operator handoff, not an added receipt
field. Tests preserve the original bytes, input joins and reachable producer
source blobs. No missing observations are synthesized.

The first receipt says `reason=weights`, `preflight_pass=false`,
`cleanup=not-started` and `private_inputs_removed=false`. Its original reason
is not rewritten: at that source revision it also covered runtime-intent
construction. The separate operator/source investigation found that the old
4 KiB intent limit rejected the actual 4,836-byte command. That diagnosis is
not a claim encoded by the original reason field.

The second receipt says `reason=network`, `preflight_pass=true`,
`cleanup=unconfirmed`, `owner_cleanup=null` and `private_inputs_removed=true`.
Separate inspection identified Docker's explicit empty `IPRange` as rejected
by the then-single-shape network check. The original failure does not itself
identify that field. Removal of private inputs is not confirmation of network
or policy cleanup; these states must not be promoted based on later fixes or
separate inventory observations.

All deployment, publication, hardware-validation, tool, reasoning and privacy
claims remain false. No raw host snapshots, private paths, credentials,
request bodies or model output are included. Later source corrections do not
replace either failed receipt.

## Separate Bounded Cleanup

[recovery-dcb8563.json](recovery-dcb8563.json) is the byte-identical original
receipt from the later [pre-container recovery command](../../../docs/tasks/p2-t4-selected-precontainer-recovery.md).
Its SHA-256 is
`ca26771c582f823973dfa40c4e126315d9070305cbe0a8f84ab555eeee444063`;
the operator-identified producer is
`dcb8563ab8a5404add41b277d9c928da0405d92a`.

This receipt reports `result=pass`, `reason=completed`, resolved creations,
absent container and network, verified quarantine, removed serving policy and
quarantine, and `cleanup_confirmed=true`. Its fixed budget is 240 seconds,
bounded by the original `2026-10-25T06:15:00Z` cutoff. It reports
`original_run_pass=false`, `runtime_started=false`, `resources_created=false`,
and false deployment, hardware-validation and confidentiality claims.
This is cleanup evidence, not a successful service or inference run.

The operator associated recovery with the second failed attempt. Neither
failed receipt contains the recovery's contract digest, so no cryptographic
contract join is claimed between them. The private contract and host state are
not published. The recovery receipt has no producer revision or start/end
timestamps; source attribution is from the handoff, not an inserted field.
Tests pin its original bytes and producer source separately. The earlier
`cleanup=unconfirmed` and `cleanup=not-started` remain unchanged.
