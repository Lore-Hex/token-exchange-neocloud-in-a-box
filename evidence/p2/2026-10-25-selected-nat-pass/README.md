# Selected closed-NAT proof pass

**PASS on the disposable CPU venue only.** The original
[observation](observation.json) was produced by committed source
`bc271d6b36a91ebb4b7a2a43a8733b7826c4b000` from 2026-10-25
02:28:02.283548 to 02:28:36.088788 UTC. Its SHA-256 is
`ea6157e42ab971be3b68fea1a416984d88176aa167611ee707f0162c749ac7fe`.
The receipt is copied byte-for-byte, not reconstructed.

All 15 unprotected baseline controls first passed. With protection installed,
only host and forwarded TLS succeeded. The other 13 paths were blocked:
engine/guard/IPv6 TLS, container-to-host and peer IPv4/IPv6, forwarded IPv4/IPv6,
and both host-relayed and embedded DNS over TCP/UDP. The controlled DNS sink
counters stayed unchanged. TLS verification, policy removal, normal cleanup
and final namespace reaping all passed.

The producer uses isolated mount, PID and network namespaces with no real
uplink, its own temporary Docker daemon and synthetic fixture containers.
There were no GPU devices, model weights, inference credentials or customer
requests. The original receipt keeps `gpu_used`, `confidential`, `published`
and `deployment_eligible` false where those fields are defined.

## Reproduction and provenance

The command shape was:

```sh
"$PRIVATE_PYTHON_3_12_13" -I -S -B scripts/prove-selected-nat.py --run \
  --archive "$VERIFIED_OFFLINE_IMAGE" \
  --archive-sha256 b741f35a8a651f06f150ac747be6277298cd0726939222d363f9e2a1cf4cb170 \
  --output "$NEW_PRIVATE_OUTPUT"
```

The same Ubuntu 24.04 amd64 venue, kernel `6.8.0-147-generic`, nftables 1.0.9,
Docker 29.1.3, private Python 3.12.13 and util-linux 2.39.3 were used as in the
[earlier failed observations and bootstrap receipts](../2026-10-25-selected-nat-policy-failure/README.md).
The offline archive, inspected image configuration and reference manifest also
match those runs. All four producer-source hashes are in the receipt. Compared
with the second failed run, only the policy renderer's expected canonical
readback changed; the installed input batch is unchanged, protected by tests.
The [source-backed correction](../../../docs/tasks/p2-t4-selected-nat-proof.md#pinned-canonical-readback)
is now exercised successfully. The earlier failures retain their original
results and cleanup flags; no historical rules or exception text are invented.

## Limits and next gate

This qualifies the bounded synthetic policy experiment on this exact venue,
not a public service owner, GPU host, sustained workload or confidential route.
In particular, the ordinary GPU host has a different kernel, nftables and
host Python. Its compatibility requires separate source review and controlled
verification before integrating this policy with the actual service owner.
No real host firewall, provider listing, DNS or billing was changed. Private
host addresses, scratch trees and generated TLS keys are not published.
