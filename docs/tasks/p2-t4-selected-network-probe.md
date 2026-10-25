# Selected-service ingress experiment

The private selected service uses `--network none`. A public service needs a
separately validated ingress mechanism, not an assumption that `--internal`
is a complete no-egress firewall.

Docker documents that an internal bridge permits connections to host services
on its gateway address. Gateway mode `isolated` removes that address. The
Docker 29.1.3 source does not assign a gateway to an internal endpoint, while
NAT port programming requires a selected gateway endpoint. This predicts that
ordinary `--publish` is not a working ingress mechanism for that topology:

- [Docker network internal mode](https://docs.docker.com/reference/cli/docker/network/create/#network-internal-mode---internal).
- [Docker gateway modes](https://docs.docker.com/engine/network/port-publishing/#gateway-modes).
- [Pinned bridge implementation](https://github.com/moby/moby/blob/fbf3ed25f893e6ce21336f1101590e40a13934f4/daemon/libnetwork/drivers/bridge/bridge_linux.go).
- [Pinned port-selection implementation](https://github.com/moby/moby/blob/fbf3ed25f893e6ce21336f1101590e40a13934f4/daemon/libnetwork/drivers/bridge/port_mapping_linux.go).

Sources accessed 2026-10-25. These are source-derived expectations, not measured
deployment results.

## Bounded local proof

[`probe-selected-network.py`](../../scripts/probe-selected-network.py) records
a fixed synthetic TCP listener on the port the real service uses. It first
requires a working loopback-published ordinary bridge as a positive control,
then tests an isolated internal bridge. It distinguishes requested
`HostConfig.PortBindings` from actual `NetworkSettings.Ports` and independently
probes the host's loopback port. Both cases require the listener to answer an
in-container positive control first.

The script uses a cached immutable Linux/amd64 Python image, nonroot read-only
containers, dropped capabilities, no secrets, no GPUs, no model files and no
public port binding. The ordinary bridge is only the local positive control,
never a serving fallback. There are no outbound probes or real metadata
requests. The experiment does **not** prove egress isolation, TLS, supported
host firewall configuration or production reachability. If isolated ingress
does not work, a new reviewed ingress design is still required.

```sh
python3 -I -S -B scripts/probe-selected-network.py \
  --docker-host "$APPROVED_LOCAL_DOCKER_SOCKET" --engine-version 29.4.0
```

Version 29.4.0 is the inspected local Linux VM, not the GPU host's 29.1.3.
The recorder requires its own and the listener's bytes to match the named Git
commit and includes their hashes, without embedding any machine-local paths.
Results must retain the actual engine version and may not qualify a different
daemon. The cached image is never pulled. All owned containers and networks
are identity-checked and removed, including after a partially successful
creation. Operations have a 240-second deadline plus a fixed 60-second cleanup
reserve; the listener exits after 180 seconds. Cleanup ambiguity remains a
failed result. SIGTERM and SIGINT cancel subsequent work without interrupting
the fixed cleanup reserve. An ambiguous create receives bounded recovery but
never an absence-based cleanup success. Synthetic frames are accumulated under
absolute deadlines; TCP packet boundaries are not message boundaries. A local
result cannot authorize a production network change.
