# Selected Ubuntu 26.04 closed-NAT proof

**PASS for the isolated synthetic experiment on the ordinary GPU host.**
The original [observation](observation.json) was produced by committed source
`74d10833fbefdea1a7d4ac03e1db34ffdf776b37` from 2026-10-25
03:25:34.997005 to 03:26:08.229350 UTC. Its SHA-256 is
`b17eeddba18c830a71db0527b9f36397f8a3ec4a9666c75bef81d66f37085da1`.
It is copied byte-for-byte, not reconstructed.

All 15 unprotected baseline controls passed. After installing the policy, both
intended TLS paths passed and all 13 prohibited paths were blocked, including
IPv4/IPv6 host, peer and forwarding traffic and embedded/host-relayed TCP/UDP
DNS. TLS verification, unchanged controlled DNS sink counters, policy removal,
cleanup and namespace reaping all passed.

## Exact venue and reproduction

This used the closed `ubuntu26-ordinary` profile, with its exact package,
executable, library and boot-file hashes recorded in the
[venue manifest](../../../scripts/fixtures/selected-nat-ubuntu26.json).
The host was Ubuntu 26.04, kernel `7.0.0-38-generic`, Python 3.14.4,
nftables 1.1.6, Docker 29.1.3 and util-linux 2.41.3. This is a separate venue
from the [earlier CPU-only pass](../2026-10-25-selected-nat-pass/README.md).
The same offline synthetic fixture image was used, not an inference image.

```sh
/usr/bin/python3 -I -S -B scripts/prove-selected-nat.py \
  --archive "$VERIFIED_OFFLINE_IMAGE" \
  --archive-sha256 b741f35a8a651f06f150ac747be6277298cd0726939222d363f9e2a1cf4cb170 \
  --output "$NEW_PRIVATE_OUTPUT" --venue-profile ubuntu26-ordinary --run
```

The receipt binds all five producer files, the venue manifest, fixture image
configuration, reference manifest and installed policy. The experiment has
its own mount, PID and network namespaces and temporary Docker daemon, with
no real uplink. The venue's systemd-resolved symlink is handled inside the
private mount namespace, not by changing the host resolver.

## Limits

No GPU, model weights, inference credentials, public listener or customer
traffic participated. `gpu_used`, `published`, `deployment_eligible` and the
inner `confidential` flag remain false. A trusted ordinary-host profile is
not hardware attestation or a complete confidential image measurement.

The outer host's DNS content and symlink were checked unchanged afterward;
Docker was empty and all eight GPUs were idle. These are private operator
checks, not additional fields in the original proof. The operator's raw host
firewall checksum differed; the baseline did not preserve the full ruleset
or a counter-independent digest. Therefore this evidence does **not** claim
byte-identical outer-host firewall state or assert that counters alone explain
the difference. No experiment-owned table was present in the post-run host
ruleset. Future host-invariance evidence needs a structured baseline.

Persistent service ownership, drift detection and emergency quarantine still
need integration and live verification before any public provider promotion.
Private addresses, scratch paths, generated keys and full work directories
are not included.
