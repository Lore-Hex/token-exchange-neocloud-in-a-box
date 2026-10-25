# Private baseline for the isolated quarantine proof

Status: software and mocked/local-file tests only. No wrapper, nft command,
namespace, Docker daemon or quarantine experiment has been executed by this
increment. Independent final-head review, hosted CI and separate operator
authorization are required before one real observation. This is P2.T4 proof
preparation, not a persistent service owner or permission to change a host
firewall. The earlier ordinary-host observation did not retain a full pre-run
ruleset and cannot be retroactively given an equality claim.

## Closed handoff

[selected_host_baseline.py](../../scripts/selected_host_baseline.py) wraps only
the committed [isolated proof](../../scripts/prove-selected-nat.py), always with
`--quarantine`. There is no arbitrary command, URL, daemon, interface or target
selector. Both existing closed venue profiles retain their exact interpreter,
tool, resolver and source requirements. The original proof, policy, fixture,
receipt bytes and default CLI remain unchanged. The selected-service owner is
not involved.

The wrapper takes a private pre-snapshot before permitting the proof, invokes
its isolated `-I -S -B` CLI once, and attempts the post-snapshot after its process
has been reaped, including proof failure/cancellation. It reuses the existing
bounded output collector, process-group cleanup and idempotent cancellation
helper, not another resource lifecycle. Cancellation during startup is forwarded
to the child and cannot be accepted as success. No stderr, exception text,
firewall text, resolver contents or private path is emitted to stdout.

The proof still owns its original **480 seconds operations +90 cleanup +25 final
reap**, with no retries or extended proof deadline. The wrapper's collector is
capped at that same 595 seconds; its process-group fallback has the existing
25-second bound. Each read-only snapshot has a 30-second operation window plus
the existing command-reap reserve of at most 25 seconds. Late capture completion
fails; these read-only intervals do not grant the proof more runtime. Allow up
to 730 seconds for the complete wrapper including both snapshots and fallback
reaping, subject to the existing local filesystem/kernel-I/O assumptions.

After the final-head software gates and explicit execution approval, run **one**
command from a clean, root-owned checkout of the exact reviewed source bundle.
The following is a template, not a record of execution:

```sh
/usr/bin/python3.14 -I -S -B scripts/selected_host_baseline.py \
  --run --venue-profile ubuntu26-ordinary \
  --archive /root/nib-owned/offline-python-image.tar \
  --archive-sha256 "$APPROVED_ARCHIVE_SHA256" \
  --output /root/nib-owned/quarantine-private-new
```

The archive must be the previously verified offline linux/amd64 image for the
[proof's exact config/manifest](p2-t4-selected-nat-proof.md); no pull occurs.
For `ubuntu24-cpu`, use only the already admitted private CPython **3.12.13**
executable and that profile's regular resolver-file contract. For
`ubuntu26-ordinary`, the executable resolves to **/usr/bin/python3.14**, exact
version **3.14.4**, and every tool/package/kernel/resolver pin in the
[committed venue](p2-t4-selected-nat-venue.md) must match. Do not replace the host
resolver or install/change tools to make admission pass. Do not use this
experimental namespace daemon as a production daemon.

## Private records and equality

The output directory is new, mode 0700, with no overwrite/reuse. Snapshot and
wrapper files are mode 0600. Each `before/` and `after/` retains:

- `firewall.json`: the full, exact stdout of the fixed `nft --json list ruleset`
  command (plus `--numeric-protocol` for 1.1.6). The 256-KiB bound rejects excess
  output; truncated data is never accepted as a complete baseline.
- `resolver.conf`: exact resolver target bytes, bounded at 64 KiB.
- `resolver.json`: literal symlink text or null, exact target path, and
  link/target device, inode, owner, mode, size, mtime and ctime. Atime is not
  captured because the read itself may update it. Both targets and links are
  rechecked around capture. The existing exact resolver guard is unchanged.
- `capture.json`: strict versioned manifest binding every retained file hash,
  Git HEAD, the wrapper plus the proof's complete source closure, venue, UTC
  capture interval, boot hash and host network/mount namespace identities.

Only two forms have counter values ignored in the **comparison copy**, never
the retained raw file: named `counter` objects' `packets`/`bytes`, and anonymous
`rule.expr` counter statements' `packets`/`bytes`. Both must be non-boolean
unsigned 64-bit integers. The two `COUNTER` sections of
`doc/libnftables-json.adoc` in the official
[nftables 1.0.9 archive](https://netfilter.org/projects/nftables/files/nftables-1.0.9.tar.xz)
(SHA-256 `a3c304cd9ba061239ee0474f9afb938a9bb99d89b960246f66f0c3a0a85e14cd`)
and [1.1.6 archive](https://www.netfilter.org/projects/nftables/files/nftables-1.1.6.tar.xz)
(SHA-256 `372931bda8556b310636a2f9020adc710f9bab66f47efe0ce90bff800ac2530c`)
define these exact object/statement shapes and counts. Named references remain
unchanged. No recursive field deletion or policy-expression normalization occurs.

**Handles remain significant.** All objects, fields, rule/expression/element
ordering, predicates, verdicts, quotas/used values, limits and expirations remain
significant, including unknown extensions. Other counter placements are not
normalized: a difference fails conservatively. JSON object-key order/whitespace
does not matter; scalar types and array ordering do. A changed handle, removed
rule, changed DNS link/metadata/content, unknown version, invalid schema,
oversized capture or source/host mismatch fails the wrapper. This compares two
observed endpoints, not continuous host integrity or resistance to host-root
tampering. Unrelated legitimate host changes can therefore cause failure.

The original `proof/observation.json` and bounded `proof-stdout.json` remain
byte-exact. Acceptance requires they match each other, join the pre-snapshot's
Git/source/venue identity and supplied archive hash, use quarantine v2, pass the
existing strict inner validator, and establish namespace and resource cleanup.
The outer receipt must lie between the capture intervals. Invalid, missing or
failed proof output never becomes a successful proof merely because snapshots
match. Post-capture failure likewise cannot be hidden by a passing proof.

`summary.json`/stdout uses the closed `nib.selected-host-baseline-proof/v1`
schema: fixed stage, result, verification booleans and original proof-receipt
hash only. `baseline_complete` means both captures were validated and compared;
`firewall_equal` and `dns_equal` report their separate comparisons. Unconfirmed
facts remain false. `gpu_used`, `published` and `deployment_eligible` always stay
false. A matching baseline is not service qualification or confidentiality.

**Keep the entire snapshot tree private.** Firewall JSON and resolver files can
contain host IPs/names and unrelated sensitive data. Never commit or upload these
raw files, their metadata manifests or a directory archive. Only separately
reviewed content-free summary and original proof receipts are candidates for a
future evidence PR. Preserve failed and partial private captures without retry
or overwriting. [Focused tests](../../scripts/test_selected_host_baseline.py)
exercise both pins, real temporary-file checks, source joins, drift, cancellation,
collector failure and no-content diagnostics; they make no kernel proof claim.
