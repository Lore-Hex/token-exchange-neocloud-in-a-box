# Selected Pre-Container Recovery

Status: software and synthetic failure-path tests only. No hardware recovery is
established by this change. The original finite-owner failure remains **FAIL**
with unconfirmed cleanup; a separate recovery receipt cannot rewrite it.

This narrow CLI recovers only the private journal left by
[the finite owner](p2-t4-selected-finite-owner.md) after a resolved network create
and before **any** container-state file. It reuses the existing journal,
reservation, exact Docker network inspection, closed Ubuntu26 venue admission,
daemon identity, bounded subprocess cleanup, and policy/quarantine proof APIs.
The dependency admits Docker 29.1.3's exact empty `IPRange` representation; it
does not admit arbitrary IPAM changes.

## Invocation

After independent review and exact-head CI, the operator may separately authorize
this committed command on the original host using its private state directory:

```sh
/usr/bin/python3.14 -I -S -B host/recover-selected-service.py \
  --cleanup-only --state-dir "$ORIGINAL_PRIVATE_STATE" \
  --hard-stop-at "$ORIGINAL_HARD_STOP_AT"
```

The required UTC timestamp must exactly match the journal. The fixed 240-second
recovery budget is clipped by both original wall and monotonic deadlines;
neither clock rollback nor a new invocation extends the original cutoff.
Existing per-command reaping reserves still apply. Expired state is rejected,
not silently assigned a new deadline. There is no create/start/serve mode,
budget override, remote daemon, credential or weight input.

## Required Proofs

- The operator must have reaped the original owner and watchdog and serialize
  other privileged work. A bounded procfs scan rejects any observable active
  `record-selected-service.py` owner/watchdog before removal and between phases.
  An exclusive existing-directory lock serializes this recovery CLI. This is
  not a defense against an adversarial root racing or concealing a process.
- The root and container directories must remain private, nonsymlinked, owned
  by root. The root contains exactly `network` and `container`; the latter must
  be empty, including no partial runtime/contract/intent or unknown file.
- The network journal must contain the exact ordered prefix through
  `network-resolved`. Unknown create completion and already-removed journals
  are rejected. Initially the exact ID, name, labels, bridge, IPAM, options and
  empty attachment map must pass the shared inspection. Preserve the finite
  owner's otherwise-idle daemon requirement: the all-state container inventory
  must be empty, also excluding renamed owned, stopped and foreign containers.
  Docker's [container listing contract](https://docs.docker.com/reference/cli/docker/container/ls/#show-both-running-and-stopped-containers--a---all)
  makes `--all` explicit; its network filter alone does not prove stopped-container
  absence. No container selector is treated as an ownership shortcut.
- Both original policy and quarantine must match fresh canonical readback.
  Remove only that exact empty network ID, prove name and exact-ID absence and
  journal its removal. Repeat state/process/daemon/container/absence checks,
  then remove only the owned serving tables. Verify their absence while
  quarantine remains valid; repeat the proofs before removing quarantine.
  Finally verify both table pairs and the network are absent.

Unknown command completion, malformed observations, drift, cancellation and
deadline exhaustion fail closed without attempting later removals or retries.
If network removal completed but a later proof failed, quarantine is retained;
this intentionally narrow CLI does not adopt partially completed recoveries.
A failure during quarantine removal cannot prove retention or absence: the
receipt stays failed/unconfirmed. Nothing flushes an entire ruleset or kills
unowned processes. The trusted root/local-daemon boundary is unchanged.

The content-free `nib.selected-precontainer-recovery/v1` receipt contains fixed
phase/result fields, original cutoff, journal-contract digest and separate
cleanup observations. It never prints command output, exception chains, private
paths, host addresses or keys. Even a recovery PASS keeps original-run PASS,
runtime-started, resource-created, deployable, hardware-validated and confidential
claims false. Preserve this receipt separately from the original failure.

## Verification

`PYTHONPATH=scripts python -B -m unittest test_selected_service_recovery
test_selected_service_network test_selected_service_owner test_selected_network_policy`
uses real temporary journals with synthetic Docker/nft/process observations.
Negative controls cover unresolved creation, every container-state filename,
owned/attached containers, drift, active owners, failed commands, changed daemon,
late state creation, private canaries and both original deadlines. No hardware,
network listener, namespace, Docker or firewall operation is exercised.
