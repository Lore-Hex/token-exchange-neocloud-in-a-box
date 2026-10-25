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
publication claim is made. All three JSON files are unmodified originals.

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
are not published. This follow-up adds a closed `policy_step` diagnostic enum
for a future separately gated run. It neither changes the policy nor retries
this failed observation; no new hardware result is claimed.
