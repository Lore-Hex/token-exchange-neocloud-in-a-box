# Selected NAT proof: separate ordinary-host venue

Status: the bounded synthetic experiment **passed on the exact ordinary host**;
see the [original observation and limits](../../evidence/p2/2026-10-25-selected-nat-ubuntu26-pass/README.md).
This extends the existing [closed-NAT proof](p2-t4-selected-nat-proof.md), not the
inference service owner. The earlier Ubuntu 24.04 CPU observation did not qualify
this newer host; its separate reviewed profile and actual run were required.

## Closed admission

The default `--venue-profile ubuntu24-cpu` keeps the original Python 3.12.13,
nftables 1.0.9, util-linux 2.39.3 and regular resolver-file contract. The opt-in
`--venue-profile ubuntu26-ordinary` selects the committed
[venue manifest](../../scripts/fixtures/selected-nat-ubuntu26.json):

- Kernel `7.0.0-38-generic`, Python `3.14.4`, nftables `1.1.6` and util-linux
  `2.41.3`.
- Ubuntu Docker `29.1.3-0ubuntu4.1`, containerd `2.2.2-0ubuntu1.1` and runc
  `1.4.0-0ubuntu1`. These are observed distro packages, **not** the static
  Docker archive installed for the old CPU venue.
- Exact package versions and 38 observed SHA-256 identities: eleven tools,
  the installed kernel image/config and 25 resolved shared-library files.
  Checked files must be root-owned regular files, not group/world writable,
  bounded in size, opened without following a terminal symlink, and unchanged
  during the bounded hash read. The command search path must resolve the named
  tools to the selected paths.

These identities came from read-only inspection on 2026-10-25, with the exact
zlib security-update pin revised on 2026-10-26 as described below. They are admission
pins for an **ordinary trusted-host experiment**, not proof of kernel boot
identity, package provenance, a complete dynamically loaded execution closure,
or defense against a malicious host administrator. Library aliases, Python's
entire standard library, firmware and arbitrary loader configuration are not
attested. Unknown venues or drift fail; there is no automatic host detection,
package installation, upgrade or fallback to looser checks.

### Ubuntu zlib security update, 2026-10-26

A replacement ordinary host failed admission before the baseline snapshot or
network experiment: 37 of the 38 file pins matched, but the resolved zlib library
had changed. Its package is `zlib1g:amd64`, version
`1:1.3.dfsg+really1.3.1-1ubuntu3.2`. The current manifest replaces only that
library hash and adds an explicit package-version check; it does not accept both
old and new hashes or change the machine's installed library.

Primary source: [Ubuntu's exact amd64 security-update package](https://security.ubuntu.com/ubuntu/pool/main/z/zlib/zlib1g_1.3.dfsg+really1.3.1-1ubuntu3.2_amd64.deb),
downloaded and inspected on 2026-10-26. Its packaged changelog dates the update
2026-10-21 and identifies a `gz_vacate()` heap-buffer-overflow fix. The package's
SHA-256 is `10084666f747247550c3e82f9a12cbfd9184c19241a42eb34d8893ecb8e03b35`.
The extracted `usr/lib/x86_64-linux-gnu/libz.so.1.3.1` hash is
`27fa6c6cb94fb5b099ead61758a300dbc392d1dbf307fff6c16a9b8e57fdc9de`,
matching the replacement host. The previous pin was
`47b61967895b30e8c0c6818dd633ffeb87711cf637c688f981896f0dd84ce23b`.

Historical evidence retains its original git and manifest hashes and is not
upgraded into proof for this revision. A new exact-source baseline and network
experiment must pass before inference. Package and hash matching are ordinary
host admission checks, not attestation or a confidential-computing claim.

## Private resolver handling

The new venue accepts only the root-owned `/etc/resolv.conf` symlink whose
literal value is `../run/systemd/resolve/stub-resolv.conf` and whose resolved
destination is `/run/systemd/resolve/stub-resolv.conf`. It does **not** replace
the host's link or resolver contents.

The existing owner first creates separate mount, PID and network namespaces,
requests private propagation, and verifies their identity. Only inside that
boundary does it mount its private `/run`, recreate the exact resolver target
there and bind the fixed synthetic DNS configuration onto that private target.
The link and final resolution are rechecked. A failed private mount prevents
directory/file creation. The host daemon sockets remain hidden as before.

The flags remain those in
[util-linux v2.41.3 unshare](https://github.com/util-linux/util-linux/blob/v2.41.3/sys-utils/unshare.c).
Private mount propagation prevents mount events from propagating to the parent
namespace; see [mount_namespaces(7), shared subtrees](https://man7.org/linux/man-pages/man7/mount_namespaces.7.html).
This mechanism is not a permission to change DNS on an existing serving host.

## Unchanged proof and limits

The shared policy selects its exact 1.1.6 numeric-protocol readback. Installed
and removed rule bytes are unchanged. The same private daemon, pinned offline
image, positive controls, protected controls, DNS sink checks, TLS verification,
ownership checks, fixed 480-second operation budget and bounded cleanup apply.
No GPU devices, inference, credentials, model weights, external uplink, public
ports, existing daemon or customer traffic participate in this experiment.

Source staging now also binds the venue manifest. New-venue receipts name the
profile in both inner and outer reports and bind its manifest hash. The outer
owner rejects a different inner profile. Historical receipts without a profile
retain their original meaning; they are not upgraded or rewritten.

Tests cover legacy argv preservation, strict profile admission, changed file
identity/ownership/mode, wrong resolver target, private mount ordering, package
and kernel drift, and the existing positive/negative/cleanup state machine under
the new profile. These use fakes and do not prove kernel or daemon compatibility.
The separate measured experiment passed on 2026-10-25; it did not use GPUs or
serve inference. Its private outer-host baseline retained only a raw firewall
digest, not the full ruleset. Because that digest changed, exact outer-host
firewall invariance was not established; do not substitute the isolated PASS
for that missing comparison.

Before any actual run: require final-head independent review and full CI, a
bounded approved execution window, no concurrent host workload, a fresh read-only
inventory, and the pinned offline archive. Preserve the original terminal receipt
and verify host resolver, daemon inventory, GPU allocation and firewall remain
unchanged afterward. Preserve a structured firewall baseline with an explicit
counter comparison policy, not only a digest containing changing traffic counts.
A failure must not trigger a retry or host workaround.
Even a PASS remains a synthetic namespace result, not a persistent public
service owner, complete production networking qualification or confidentiality.
