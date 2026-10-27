# Exact kernel archive timeout before build

**Failed; no guest boot or topology proof.** The second authorized attempt was
[workflow run 40516934761, attempt 1](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/40516934761),
producer `0a77a4d834a7ed18f1a3510f2714ee9276955b00`. The job ran from
2026-10-27T13:59:29Z to 14:04:11Z with the original three explicit inputs:
`debug_boot=true`, `guest_network=true`, `debug_topology=true`.

The committed fetcher and workflow preserved the exact typed failures. After the
metadata archive checksum passed at 13:59:41Z, one archive timed out at
14:02:03Z, 14:03:04Z and terminally at 14:04:06Z, with the existing one- and
two-second backoffs. All three records have `failure: timeout`, null return code
and `package_execution: false`. This is **not** an HTTP or checksum failure. The
log contains no transfer byte progress, so it does not establish why the source
timed out or its throughput. It also does not identify the lost diagnostic from
the separate [first attempt](../2026-10-27-debug-topology-fetch-failure/README.md).

Both original signed-index locks identify the same archive:

- Package `linux-modules-extra-6.8.0-147-generic`, version `6.8.0-147.147`, amd64.
- Filename `pool/main/l/linux/linux-modules-extra-6.8.0-147-generic_6.8.0-147.147_amd64.deb`.
- Size **113500352**; SHA256
  `b3f16d16b54796497884b27810b0942243bdc6ef26da1ce821e948ece39a9492`.
- Index `noble-updates-main`; original base `https://archive.ubuntu.com/ubuntu/`.
- Locks: [guest runtime](../../../image/inputs/guest-runtime-ubuntu2404.json)
  and [kernel/driver build](../../../image/inputs/r595-ubuntu2404.json).

The producer's fetch plan selected Canonical's dated
`https://snapshot.ubuntu.com/ubuntu/20261020T000000Z/` package source. Build and
QEMU were skipped. The cleanup receipt has `build_result: skipped`, null
installation/resources and all build/boot/confidentiality proof flags false;
its 68 source hashes and two validator hashes bind the producer. Cleanup of the
unused private export succeeded. No synthetic `run.json` has been created.

## Original records

These are unchanged originals from GitHub's failed-step/run/artifact views and
the extracted cleanup artifact. The privately retained original ZIP is 3761
bytes, SHA256
`308df18c1ee462bebd5284d33b78896a5362a25538cb22284e910c09e84e92f3`, matching
GitHub's digest. Its only member is `validation.json`. Earlier empty/incomplete
CLI observations remain private; the committed run record is the terminal
completed view, not a manually reconstructed job state.

| File | Bytes | SHA256 |
| --- | ---: | --- |
| `validation.json` | 8261 | `d2fb42bae5dac8c09bdc92d9dbf5de591020202370c8f70d5617f50afbc439fe` |
| `github-run.json` | 3081 | `dae2dbb3159165444d17e55e1c04649b521cc98a5bcd962679dcb81ec5f017f5` |
| `github-artifacts.json` | 773 | `090b0f05080e82be52237e8228c080fd3db5278f67f16a6c6e6ac68d71a08abc` |
| `failed-workflow.log` | 4549 | `326efadad392d55488cb91a8692c48846e68f9eef2c6d3dbd5be56934cd040e6` |
| `primary-transfer.json` | 168 | `0f3b6b3dbc339e3a465718fb20c08a6052b8f5e139eaddf137942e828e870e77` |

## Exact original-source recovery

On 2026-10-27, a separate local source-download diagnostic made one bounded
HEAD (HTTP 200 in 0.478505 seconds), then **one GET with no retries** against the
[lock-declared original Ubuntu archive](https://archive.ubuntu.com/ubuntu/pool/main/l/linux/linux-modules-extra-6.8.0-147-generic_6.8.0-147.147_amd64.deb).
The original curl write-out, `primary-transfer.json`, reports HTTP 200,
113500352 received bytes and 9.603884 seconds. The retained file independently
matched the exact size and SHA256 above. No package was installed or executed.
This is a local download observation, not an Actions throughput guarantee,
authenticated image build, guest result or phase acceptance artifact.

The diagnostic used this command (output directory created separately, private
and empty; a reproduction requires separately coordinated source access):

```sh
url=https://archive.ubuntu.com/ubuntu/pool/main/l/linux/linux-modules-extra-6.8.0-147-generic_6.8.0-147.147_amd64.deb
/usr/bin/curl --disable --noproxy '*' --fail --silent \
  --proto '=https' --tlsv1.2 --connect-timeout 10 --max-time 60 \
  --max-filesize 113500352 --output "$output/primary.deb" \
  --write-out '{"source":"locked-primary","http_status":%{http_code},"elapsed_seconds":%{time_total},"received_bytes":%{size_download},"start_transfer_seconds":%{time_starttransfer},"speed_bytes_per_second":%{speed_download}}\n' \
  "$url" > "$output/primary-transfer.json"
wc -c "$output/primary.deb"
shasum -a 256 "$output/primary.deb"
```

## Narrow source correction

Only the exact original URL, size and SHA tuple now selects the original Ubuntu
source in `fetch_mkosi_tools.py`. All other Ubuntu archives still use the dated
snapshot, including packages removed from the live pool. This is not a generic
mirror fallback, source override, alternate version or mutable lock. Metadata
authentication, exact archive size/SHA, no package execution, four workers,
600-second overall deadline, 60-second per-attempt limit and at most three
existing transport attempts remain unchanged. No extra request is introduced.
The same identity is selected in both build and runtime plans.

Offline regression tests cover both complete plans, source-identity mutations,
unchanged retry/time/byte bounds, checksum rejection and the original failed
records/source hashes. They require no network or host ports:

```sh
TMPDIR=/private/tmp PYTHONPATH=scripts uv run --project tools --frozen python -m unittest test_native_tools test_concurrent_fetch test_debug_fetch_failure_evidence test_debug_kernel_fetch_evidence
```

Independent final-head review and all normal software gates are required. A
further CPU workflow dispatch remains separately coordinated, not automatic.
No boot, veth, networkd, packet isolation, GPU, confidential-computing or
phase-completion claim is made.
