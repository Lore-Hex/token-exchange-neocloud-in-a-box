<!-- SPDX-License-Identifier: Apache-2.0 -->

# P1.T1 guest services

**P1 was implemented and tested against fakes only. Not confidential, not
deployed, not hardware-validated.** This task implements the guest-service slice of
[P1](../PLAN.md#p1-gpu-free-end-to-end-node-ci-only), stacked on P0.T2 commit
`3a6c902`. It does not implement trcs integration, CPU/GPU appraisal, a real
engine launcher, dm-verity, or firewall installation. The separately scoped P3
network implementation below is also not yet validated in a running guest.

## Build boundary

The four Go commands are `nib-network`, `nib-gpu-gate`, `nib-weights`, and
`nib-engine`. All accept exactly `--config <file>`. Invalid invocation exits 2;
gate/startup/service failure exits 1 with a fixed diagnostic; successful fake
oneshots and an intentionally stopped fake engine exit 0. No arguments, paths,
backend errors, request data or arbitrary metric labels are logged.

Ordinary builds now include the separately scoped
[real network gate](../docs/tasks/p3-t1-guest-network.md): Linux only, fixed
protected config and pinned executable, create-only policy installation and
strict whole-ruleset readback. It is unit-tested, not yet guest-validated.
GPU, weights and engine still deny startup without reading configuration or
opening listeners. Environment variables and `--fake` cannot bypass these
refusals. No real appraisal, weights mount, inference or front door is enabled.

The `nibfake` build tag selects the fixture reader and the four `nibfake*`
backend functions. There is no exported verifier interface or runtime backend
injection. All guest production and test dependencies are stdlib or local guest
packages; the repository dependency guard checks both tags and CGO settings.

`go test ./...` runs the tagged guest suite through `TestTaggedGuestSuite`.
It also builds all four Linux/amd64 helpers with and without `nibfake`, runs
`go tool nm`, rejects any release symbol containing `nibfake`, and requires
all four fake backend symbols in every tagged binary as positive controls.
The scan builds unstripped binaries and fails if symbol inspection fails.
These tests run under the existing `make check` CI job without workflow edits.

## Local simulation

Run from the repository root, without installing any unit:

```sh
go build -tags nibfake -trimpath -o build/guest-fake/ ./guest/cmd/...
build/guest-fake/nib-network --config guest/testdata/fake/guest.json
build/guest-fake/nib-gpu-gate --config guest/testdata/fake/guest.json
build/guest-fake/nib-weights --config guest/testdata/fake/guest.json
build/guest-fake/nib-engine --config guest/testdata/fake/guest.json
```

The final command serves synthetic responses at `127.0.0.1:18000` until
SIGINT/SIGTERM or gate failure. It never connects to any upstream. Use only
synthetic requests. Change the fixture's port if occupied; only literal
`127.0.0.1` and ports 1024-65535 except 8443 are accepted.

`GET /health` identifies simulation and says `hardware_validated: false`.
`GET /v1/models` advertises `nibfake-model`. `POST /v1/chat/completions` accepts
that model, string messages with system/user/assistant roles, and optional
`stream`, `stream_options.include_usage`, bounded `max_tokens`,
`max_completion_tokens`, `temperature`, `top_p`, `top_k`, `thinking`, `reasoning`
and `reasoning_effort`. Output is always `PONG`, independent of input. Streaming
uses four flushed deltas spaced 30 ms apart, followed by `[DONE]`; usage is the fixed synthetic 1+1=2, not a
tokenizer measurement. Every response carries `X-Nib-Simulation: nibfake`.
The unadvertised `nibfake-unavailable` model is an explicit error fixture: it
returns 503 with `Retry-After: 7` before any SSE bytes, including for streaming
requests. It is compiled out with the rest of the fake engine in release builds.
Unknown fields/routes, malformed or oversized requests and excess concurrency
are rejected. The optional fields are tolerated for the provider-check 0.1.8
Tier 3/4 contract, not implemented sampling or reasoning controls. Gate loss or
cancellation stops a stream without a success terminator. This is a small
fixture engine, not a real model or a performance benchmark.

## Fixture contract

[The checked fixture](testdata/fake/guest.json) demonstrates the private
`nib.guest-fake/v1` format. It is not a production profile or hardware inventory.
Configuration is read once per invocation; referenced gate files are re-read.

- GPU gate: exact expected identities and kinds, 1-8 GPUs and up to 16 total
  devices. Every GPU and switch must be present exactly once, explicitly
  `approved: true`, `debug: false`, and `mode: fake-cc`. These booleans are
  deliberately unauthenticated simulation, not vendor cryptography.
- Weight gate: hash and size of every listed synthetic file; 1-32 nonempty
  files, at most 1 MiB each and 4 MiB total. Paths are simple basenames, with
  duplicates and gate-file aliases rejected. Reads are bounded and rooted
  beside the config using `os.Root`; symlinks and nonregular fixtures are
  rejected when inspected. This is not an adversarial filesystem or dm-verity
  substitute; no claim of race-proof file verification is made.
- Network gate: `applied: true` plus SHA-256 of the rendered policy bytes in
  `nib.fake-network/v1`. This asserts a fixture's simulated state only. An empty
  egress list is explicit; no destination is discovered or contacted.
- Unknown/case-aliased/duplicate JSON keys, nulls, trailing data, excessive
  nesting and files over 64 KiB are rejected for configuration/inventory/state.

The engine checks all three gates before binding, before each request and
again after reading a completion request. It also polls every 100 ms. A
request-observed failure latches the handler closed; the process exits nonzero
and closes its listener on a failed gate. This is sampled fake-mode behavior,
not instantaneous tamper detection or a guarantee about real GPU execution.
Admission is capped at eight handlers, with fixed 429/503 responses and
`Retry-After: 1`. Timeouts and fixture limits are local test-safety choices,
not hardware or model tuning.

## Systemd units

**Do not install or start these units on a development host:**
`nib-failclosed.target` requests poweroff. Units are image-integration inputs
only, with no automatic enablement. The image must supply the `nib` account,
the helpers and protected `/etc/nib/guest.json`; P1 does not provision them.
The real network unit instead uses `/etc/nib/network.json` and guest-only
`CAP_NET_ADMIN`, with explicit early-boot ordering and no automatic enablement;
see its separate P3 task record. Do not grant this capability to a host helper.

Sources reviewed 2026-10-19 UTC, pinned to **systemd v255**, not a claim that
an image/package version has been selected or qualified:

| Settings | Primary source and relevant directives |
|---|---|
| `Requires`, `BindsTo`, `After`, `Before`, `Conflicts`, `OnFailure` | [systemd.unit v255](https://github.com/systemd/systemd/blob/v255/man/systemd.unit.xml), each named directive under Options |
| `Type=oneshot`, `RemainAfterExit`, `Type=exec`, `ExecStart`, timeouts, `Restart=no` | [systemd.service v255](https://github.com/systemd/systemd/blob/v255/man/systemd.service.xml), Options |
| User/group, `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, `RestrictAddressFamilies`, umask, core limit, journal output | [systemd.exec v255](https://github.com/systemd/systemd/blob/v255/man/systemd.exec.xml), named directives |
| `KillMode=control-group` | [systemd.kill v255](https://github.com/systemd/systemd/blob/v255/man/systemd.kill.xml), Options |
| `DefaultDependencies=no` on failure target | [systemd.target v255](https://github.com/systemd/systemd/blob/v255/man/systemd.target.xml), Default Dependencies; [poweroff.target v255](https://github.com/systemd/systemd/blob/v255/units/poweroff.target) |

Network precedes GPU/weights; the engine both requires and binds to all three.
Its target binds to the engine. Gate failure requests the failclosed target,
which conflicts with the node/engine and pulls in poweroff without the default
shutdown conflicts. The engine does not restart automatically. `Type=exec`
reports process execution, **not HTTP readiness**; future trcs integration must
probe health and gates before exposing a front door. Oneshot active state is
not ongoing hardware appraisal. Structural tests protect this graph and the
hardening settings; no systemd boot/poweroff test has been performed here.

## Network policy

The renderer never calls `nft`, flushes a ruleset or modifies networking.
The separate real backend reuses its exact bytes and validates kernel readback;
that implementation does not change the qualification limits below.
Its candidate `inet` table drops unmatched input, output and forward traffic,
permits loopback and established/related traffic, new TCP 8443 input for the
future front door, and new TCP 443 output only to literal configured addresses.
The ports follow the [architecture contract](../docs/architecture.md#node-and-trust-boundaries),
not an invented vendor flag. There are no DNS, SSH, engine-port or packet-log
rules. At most 32 unique unicast addresses are accepted; CIDRs, zones,
hostnames, loopback/link-local/multicast addresses and syntax injection fail.

Primary source: [nftables 1.0.9 release archive](https://netfilter.org/projects/nftables/files/nftables-1.0.9.tar.xz),
reviewed 2026-10-19 UTC, SHA-256
`a3c304cd9ba061239ee0474f9afb938a9bb99d89b960246f66f0c3a0a85e14cd`.
Relevant sections: `doc/nft.txt` Address Families, Chains and Sets;
`doc/primary-expression.txt` Meta Expressions;
`doc/payload-expression.txt` IPv4/IPv6/TCP headers and Conntrack Expressions;
`doc/statements.txt` Verdict Statement and Log Statement. This pins the syntax
reference, not an installed guest package.

Tests check deterministic rendering, the exact permitted rule surface and
invalid destinations. No kernel firewall test or `nft --check` was run here.
Before real use, image integration must verify atomic installation before any
listener, conntrack initialization, existing-table behavior, routing and
necessary ICMP/IPv6 neighbor discovery against the pinned guest stack.
The candidate is deliberately not presented as a deployable dual-stack policy.

## Verification and remaining work

```sh
go test -race -tags nibfake -count=1 ./guest/...
go test -race -count=1 ./guest/...
make check
```

See [the task record](../docs/tasks/p1-t1-guest-fake-services.md) for actual
results and limits. P1.T2/T3/T4, production backends, measured image integration,
Linux boot/firewall validation and all hardware acceptance remain separate.
