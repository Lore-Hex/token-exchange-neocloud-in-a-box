# Selected NAT proof: separate ordinary-host venue

Status: implemented and tested against fakes; **not hardware validated**. This
extends the existing [closed-NAT proof](p2-t4-selected-nat-proof.md), not the
inference service owner. The earlier Ubuntu 24.04 CPU observation cannot qualify
a newer host simply because both report Docker 29.1.3.

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

These identities came from read-only inspection on 2026-10-25. They are admission
pins for an **ordinary trusted-host experiment**, not proof of kernel boot
identity, package provenance, a complete dynamically loaded execution closure,
or defense against a malicious host administrator. Library aliases, Python's
entire standard library, firmware and arbitrary loader configuration are not
attested. Unknown venues or drift fail; there is no automatic host detection,
package installation, upgrade or fallback to looser checks.

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

Before any actual run: require final-head independent review and full CI, a
bounded approved execution window, no concurrent host workload, a fresh read-only
inventory, and the pinned offline archive. Preserve the original terminal receipt
and verify host resolver, daemon inventory, GPU allocation and firewall remain
unchanged afterward. A failure must not trigger a retry or host workaround.
Even a PASS remains a synthetic namespace result, not a persistent public
service owner, complete production networking qualification or confidentiality.
