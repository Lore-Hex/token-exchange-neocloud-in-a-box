# Selected NAT policy failure

**FAIL, not network qualification.** The original committed producer at
`742c6a1e4f6920a3001415aa75ee5fd8b2a4a299` ran once on a dedicated, disposable
Ubuntu 24.04 amd64 CPU venue. No GPU, weights, real inference credentials,
customer traffic or external interface was involved.

- [Bootstrap check](bootstrap-check.json): PASS, no installation or daemon.
- [Bootstrap installation](bootstrap-install.json): PASS, pinned private Python
  3.12.13 and static Docker 29.1.3; exact existing Ubuntu tool payloads checked.
  The resolver became a regular copy of its current bytes. No daemon started.
- [Original namespace observation](observation.json): 2026-10-25
  01:42:29.980815 to 01:42:39.237401 UTC, **FAIL at policy**.

All 15 baseline positive controls passed, including both TLS paths, engine and
guard reachability, IPv4/IPv6 peers and host access, and both inherited DNS
transports. The strict negative controls were **not reached**. The report does
not establish whether policy parsing, application, readback, DNS baseline or
protected-container setup failed. It contains no raw diagnostic output and we
do not infer the cause from its coarse stage.

`cleanup_confirmed=false` and `policy_removed=false` remain unchanged. The
outer owner reports `namespace_reaped=true`; this is containment, not proof of
normal fixture cleanup or a passing policy. No confidentiality, deployment or
publication claim is made. All four JSON files are unmodified originals.

## Diagnostic observation

The [second original observation](readback-observation.json), produced by
`c82b983ef950b24cec820647a010dc6afb7c8211`, ran from 2026-10-25
02:10:39.408534 to 02:10:49.242604 UTC. Its SHA-256 is
`3797ef9d3264a547a7d5fc484341ec1fdee77cb966e8aa21eff941c5411bfd5c`.
It again **failed**, with all 15 positive controls passing and no negative
controls reached. The archive, inspected image and policy-source hashes match
the first observation; the producer adds the closed stage diagnostic.

`policy_step=readback` establishes that check/apply completed and readback was
attempted. That step includes both fetching and validating the rules. No actual
rules or exception details were captured, so it does not distinguish retrieval
failure from a comparison mismatch. The
[source-backed canonicalization fix](../../../docs/tasks/p2-t4-selected-nat-proof.md#pinned-canonical-readback)
is a candidate explanation, not a measured diagnosis or passing rerun.
`namespace_reaped=true`, `cleanup_confirmed=false` and `policy_removed=false`
remain unchanged. All deployment, confidentiality, GPU and publication flags
remain false.

## Inputs and command

The observed kernel was `6.8.0-147-generic`; the bootstrap receipt binds exact
tool versions and hashes. This is compatibility evidence for the limited test,
not a confidential/hardened kernel qualification. The offline image archive
hash and pinned inspected image configuration are in the observation.

The producing command shape was:

```sh
"$PRIVATE_PYTHON_3_12_13" -I -S -B scripts/prove-selected-nat.py --run \
  --archive "$VERIFIED_OFFLINE_IMAGE" \
  --archive-sha256 b741f35a8a651f06f150ac747be6277298cd0726939222d363f9e2a1cf4cb170 \
  --output "$NEW_PRIVATE_OUTPUT"
```

Private hosts, paths, namespace scratch data and the synthetic TLS private key
are not published. Neither observation qualifies the policy. The canonical
readback correction still requires independent review and a separately gated
run; this evidence does not report execution of that correction.
