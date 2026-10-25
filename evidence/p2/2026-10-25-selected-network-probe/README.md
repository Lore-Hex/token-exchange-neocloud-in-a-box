# Local isolated-bridge ingress observation

Measured 2026-10-25 using the committed bounded recorder:

```sh
python3 -I -S -B scripts/probe-selected-network.py \
  --docker-host "$APPROVED_LOCAL_DOCKER_SOCKET" --engine-version 29.4.0
```

The original byte-identical receipts are:

| Receipt | Producer | SHA-256 |
|---|---|---|
| [Preflight failure](preflight-failure.json) | `6a9db218606ecc9fb30fd6109928711478fde7d2` | `a906e3e9dd9bb891d24567a5fe2dae26279e615086b311ecef7a3358a44d04ca` |
| [Completed observation](observations.json) | `717c797f85895e1f7de676b6098b544c99870fac` | `41bd726bd95ca1a45016d3f891597e7bc13a7b5881b5a8f5ad956d4765287480` |

The first attempt exited 1 before creating any container/network. Read-only
image inspection then identified the cause: the default view of the cached
multi-platform index selected ARM64, failing the recorder's amd64 check. The
second producer explicitly inspected the already-cached amd64 variant, matching
the requested container platform. No image was pulled or substituted.

The corrected attempt exited 0 with `result: recorded`. Both in-container
listeners worked. The ordinary bridge had an operational host port and answered
the fixed loopback probe. The internal isolated bridge had **no operational
port mapping and was unreachable**, although its requested binding was present.
Both cases recorded confirmed cleanup; independent label-scoped inventories
afterward contained no remaining containers or networks.

This is a negative result for using isolated Docker publishing as public
ingress, not a successful serving profile. It ran with Docker 29.4.0 on an
ARM64 Linux VM and the pinned amd64 Python container. It does not qualify Docker
29.1.3 or prove TLS, egress blocking, public reachability, GPU inference or
confidentiality. All corresponding flags remain false. No credentials, model
weights, customer data, external probes or public port were used.

See the [scope and implementation](../../../docs/tasks/p2-t4-selected-network-probe.md).
