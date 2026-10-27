# Actual Restricted Network Static Closure

**PASS, offline static graph only:** 33 original locked Ubuntu packages,
13960658 compressed archive bytes, 34 ELF nodes and 77 library/interpreter edges.
No package executable, maintainer script, host loader or `ldd` was run. No image,
network, GPU, observer, cloud or default/release setting changed. No P3 phase
acceptance, runtime linkage, symbol/version, dlopen/NSS, Linux kernel/network,
hardware, confidential or deployment success is claimed.

## Original Receipts And Source

All six files below are byte-for-byte original outputs, not rewritten summaries.
The successful producer was `e90d78ce905170d2102fe6a8944716956d8e4b7f`.
The preceding failed producer was `9969645c198033eca5f61f888dbd113c6f8cd1d6`,
whose recorded source hashes match merged commit
`50e8a11f4bc0fc3de7039ff581968742a1072eb8`. The later evidence-packaging commit
is not relabeled as either producer. Historical-source and immutable-byte tests
verify both identities and the complete 13-file source closures.

| Committed file | Original name | SHA-256 |
| --- | --- | --- |
| `closure.json` | `receipt.json` | `9cea9f1b00300baf5b2729257d9d63420cca3c2871cfac5041c58232f8a40f3b` |
| `inventory.json` | `inventory.json` | `468282f9bdd630fa8c647ab4481b226016c79cfb7ad96d1c82c9d16e7c75aa2e` |
| `output.log` | captured stdout/stderr | `ed05ac97c9d2d4cb5dc24e8d42334bf42bd5ac75bc0c75ca782caefb1cf30e12` |
| `missing-library.json` | `receipt.json` | `4ee6c86c1e13d157cd4ee5a020abde6bdeb8f5a77b5307b371d3d2055987fdfb` |
| `missing-library-inventory.json` | `inventory.json` | `5a37491cbd92c47cc8dc4002fb5ea93a204501297ef3af637b81e0a314453ffd` |
| `missing-library.log` | captured stdout/stderr | `37989acc798c3d30fcac262cdcb5caa34abbd189f4cfb43e6f2b857d558134d3` |

The custom receipt schema is `nib.network-static-closure/v1`, not the generic
`nib.evidence/v1` schema, so it is not named `run.json`. The run was recorded on
2026-10-27; this producer does not record UTC start/end times or performance
measurements. The local Go 1.27.2 compiler and compiled helper hashes are
observations, not authenticated toolchain or reproducible-build claims.

## Exact Input Recovery

The original runtime lock SHA-256 is
`f3162aeab969aba9a99d3dc96ddeb80b7ee808b55b126762710a08269e182de8`.
Every selected original package row was replayed against the exact locked index,
and every DEB size, SHA-256 and control field was verified before payload parsing.
The trust root remains the reviewed signed-index hash chain, not a new signature
verification or an installed APT dependency closure.

The [workflow-pinned index archive](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/releases/download/build-input-indexes-20261020/metadata.tar.gz)
has 23847293 bytes and SHA-256
`54def86b7b37b3b5178782965e499f0242a98f36bc7828105f112005576663a7`.
The committed bounded decoder verified all seven original indexes; this graph
uses the three Ubuntu main indexes recorded in `closure.json`.

The first actual 32-package graph failed closed because
`libsystemd-shared-255.so` required missing `liblzma.so.5`. Its failure and
authenticated inventory are retained above. Official primary/security pool
requests failed with curl code 22 and the reviewed snapshot transport failed
with code 56; those private fetch receipts/logs were preserved, not retried in
an unchanged loop or treated as valid bytes. The scoped retained bootstrap tar
contained no liblzma5 archive.

The official [Noble binary publication](https://api.launchpad.net/1.0/ubuntu/+archive/primary/+binarypub/244895461)
links the [original amd64 build](https://api.launchpad.net/1.0/~ubuntu-security-proposed/+archive/ubuntu/ppa/+build/32923738).
Its matching [source publication's binary-file list](https://api.launchpad.net/1.0/ubuntu/+archive/primary/+sourcepub/18445203?ws.op=binaryFileUrls)
identified the [retained exact binary](https://launchpad.net/ubuntu/+archive/primary/+files/liblzma5_5.6.1+really5.4.5-1ubuntu0.3_amd64.deb).
This `5.6.1+really5.4.5-1ubuntu0.3` DEB matched 127352 bytes and SHA-256
`d2eabd41ca77d2c2dd9d5d4ef478cccb64ffde6279c47cf4699a857d46785a52`.
Its one bounded fetch allowed HTTPS-only redirects (maximum three), 60 seconds,
and no more than the exact locked byte size. No alternative version was used;
the ordinary subset fetcher's allowlist/retry policy was not broadened.

The original `base-files` DEB supplies `bin`, `sbin`, `lib` and `lib64` links.
No filesystem-layout links were synthesized. Library selection followed actual
`DT_NEEDED`/interpreter metadata, including the recursive library dependencies.
The package-count ceiling increased only to 33. Archive bytes, ELF objects,
parser bounds and fixed time budgets remain unchanged.

## Reproduction

Use the exact producer checkout, authenticated original inputs in new owned
directories, and an absolute Go 1.27.2 path. Local path arguments below are role
placeholders; package arguments and the producer entrypoint match the actual run.
This executes only the locally compiled metadata helper, never a package binary.

```sh
python3 -I -S -B scripts/prepare_network_runtime.py \
  --metadata "$INPUTS/metadata" --archives "$INPUTS/archives" \
  --go "$ABSOLUTE_GO_1_27_2" --output "$NEW_REPORT" \
  --package systemd --package nftables --package libc6 \
  --package libsystemd-shared --package libnftables1 --package libedit2 \
  --package base-files --package libacl1 --package libblkid1 --package libcap2 \
  --package libcrypt1 --package libgcrypt20 --package libkmod2 --package liblz4-1 \
  --package liblzma5 --package libmount1 --package libssl3t64 --package libpam0g \
  --package libseccomp2 --package libselinux1 --package libzstd1 --package libmnl0 \
  --package libnftnl11 --package libxtables12 --package libjansson4 --package libgmp10 \
  --package libtinfo6 --package libbsd0 --package libaudit1 --package libgpg-error0 \
  --package libmd0 --package libpcre2-8-0 --package libcap-ng0
```

`test_network_static_evidence.py` verifies original hashes, historical source,
archive/member provenance, virtual alias resolution, full edge coverage and
unchanged false runtime/activation claims. It replays recorded metadata without
downloads or executing ELF files; it does not replace the recorded actual-input
run. The [runbook](../../../docs/tasks/p3-t1-network-static-closure.md)
defines the restricted resolution contract and remaining Linux/runtime proof.
