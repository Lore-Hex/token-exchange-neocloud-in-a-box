# Cancelled Ordinary Owner: Terminal Failure

[owner-terminal.json](owner-terminal.json) is the byte-exact terminal JSON line
copied by the reviewed attach-release helper. Its SHA-256 is
`9114915d81ffbaef55896a1dbd760ac471d186291a22700c1ca8c5dca2355605`.
It joins the previously published [day-profile build](../2026-10-25-selected-day-startup/build.json)
by its original build, preparation, prepared-payload and image identities.
The earlier failed first attempt and separate startup/probe receipts are unchanged.

## Recorded Outcome

The owner remains `result=fail`, `status=BLOCKED`, `reason=lifecycle`.
Its original `runtime_started=null` is retained; neither this field nor the
failure is rewritten using the earlier successful external probe.
The terminal record reports `cleanup=confirmed`, all six `owner_cleanup`
fields true, `watchdog_confirmed=true` and `private_inputs_removed=true`.
These are the owner's recorded cleanup assertions, not a new independent
Docker, firewall, process or GPU inventory.

The ordinary-day lifetime fields are the original planned cutoffs, not observed
retirement times. This artifact contains no start/end timestamp, process exit
code, inner lifecycle terminal result or complete soak measurement. The original
owner process exit status is unknown. Do not infer a natural-retirement PASS,
completed 24-hour run, graceful drain of each engine role, or continuous service
availability from cleanup alone.

## Attribution and Privacy

The operator attributes the owner to
`4e0bfd7052d9a1e74d9fa83dda54272efd425351` and the copying helper to
`75b66500621793d668959a5e71baf3f26760eed1`. Neither revision is embedded in this
terminal artifact. The replay tests bind their historical source bytes and
ancestry; they do not turn operator attribution into embedded provenance or
attestation of the originally loaded process.

The preceding inspect, cancellation and release receipts remain private because
they carry current process, namespace, inode and boot-identity observations.
Private expectations, state journals, log output and launch wrappers are also
excluded. No redacted helper receipt is presented as an original, and no public
helper-success or signal/exit timeline is reconstructed from those private files.
Ad-hoc post-run absence checks are not synthesized into a public receipt.

The source-backed cancellation failure and recovery design are documented in
[the owner interruption task](../../../docs/tasks/p2-t4-selected-owner-interruption.md).
This evidence does not by itself prove the signal mechanism or distinguish an
engine failure from an interrupted attach reader. All original publication,
deployment, hardware-validation, tools, reasoning, confidentiality, E2EE and
zero-data-retention flags remain false. No model text, provider identity,
addresses, credentials, private paths or raw process identities are published.

[Focused replay tests](../../../scripts/test_selected_cancelled_owner_evidence.py)
check original bytes, the closed field set, build/source joins and these limits.
