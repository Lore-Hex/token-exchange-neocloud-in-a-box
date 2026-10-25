# Isolated quarantine and private host baseline: PASS

These are the two unchanged content-free originals from source
`3f86b9946155995a690cd6125a459868d8b0e467` on the closed Ubuntu26 ordinary venue:

- [summary.json](summary.json), SHA256
  `54a8aa789592b08d2909c0b3db3de8d822ed63d6b4384804ba1089740f83160d`.
- [observation.json](observation.json), SHA256
  `f28c90ecfde5a1cb471e260299aa09f3f5d1f21402862e5afd62e9d2cfd12df0`.

The inner proof's recorded interval is 2026-10-25
04:56:01.937660-04:57:04.178771 UTC. The wrapper summary has no timestamps;
do not treat this interval as its complete snapshot/cleanup duration. The summary
joins the exact original proof hash; the proof binds its Git revision, five-file
source closure, existing offline image archive and exact venue-manifest hash.
The wrapper source at that revision is checked separately by the
[evidence tests](../../../scripts/test_selected_quarantine_evidence.py).

All 15 synthetic baseline controls passed. With serving protection installed,
only `tls_host` and `tls_forward` succeeded; the other 13 paths were blocked,
covering host/L2 peers, IPv4/IPv6, engine/guard and direct/host-relayed DNS.
The two TLS controls succeeded again before quarantine. Verified drop-only
quarantine was installed, the original serving policy was deliberately withdrawn
**only inside the isolated namespace**, and all 15 paths then failed. Quarantine
verification/removal, resource cleanup and final namespace reaping are true.
The existing DNS relay observation remained unchanged. No GPU or model was used.

The wrapper reports complete, equal pre/post host firewall and DNS baselines.
It retained full private snapshots and ignored only packet/byte values in the
two documented nft counter forms during firewall comparison. Handles and all
other fields/order remained significant; DNS link, target metadata and bytes
were compared. The original raw files/manifests remain private and are **not**
published here, so this public evidence cannot independently recompute those
comparisons. It is an ordinary source-bound producer report, not an attestation.
The [wrapper contract](../../../docs/tasks/p2-t4-selected-host-baseline.md)
states the exact comparison and local-I/O limits.

Python3.14.4, Docker29.1.3 and the separately pinned Ubuntu26/nft1.1.6 venue were
admitted; this observation does not qualify the legacy CPU profile or different
hosts/tool versions. Proof limits stayed 480 seconds operations +90 cleanup +25
final reap, within the wrapper's existing maximum 730 seconds. The
[original pre-snapshot failure](../2026-10-25-selected-quarantine-presnapshot-failure/README.md)
is unchanged and remains failed. Nothing is inferred retroactively about it.

This was a bounded synthetic namespace experiment, not the real selected-service
owner or public Internet ingress. Pre/post equality is not continuous host
integrity or proof against root tampering. GPU use, publication, deployment
eligibility and confidentiality remain false. No persistent-public-service,
API/quality or confidential-inference qualification is claimed.
