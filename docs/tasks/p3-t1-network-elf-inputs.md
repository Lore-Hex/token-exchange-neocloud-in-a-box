# Network ELF Input Inventory

Status: **offline direct-dependency inventory only**. This is a small follow-up
to [isolated network proof inputs](p3-t1-network-proof-inputs.md), not a loader
closure, image builder, guest/network proof or P3 completion. All activation,
runtime-closure, hardware, confidential and deployable fields remain false.

## Boundary

[nib-network-elf-inputs](../../cmd/nib-network-elf-inputs/main.go) accepts only
`--target networkd` or `--target nft` and one ELF byte stream on stdin. It checks
exact size and SHA-256 **before** parsing with Go's standard `debug/elf` package.
It binds the dynamic section used by that parser to the ELF dynamic segment and
requires the observed interpreter, ordered direct libraries and search path to
match the fixed inventory. Malformed or changed bytes reject without metadata.
No package binary, loader, `ldd`, shell, network manager or child process runs.

The input limit is the selected pinned size plus one byte; a whole-process
ten-second deadline also bounds a blocked stdin read. Output is deterministic
JSON under 4096 bytes. Errors are fixed and content-free. The command writes no
files, searches no host libraries, reads no environment configuration and has no
execution, privilege, namespace, download or workflow mode. Its host compiler
and runtime are not authenticated by this inventory.

```sh
go build -trimpath -o build/nib-network-elf-inputs ./cmd/nib-network-elf-inputs
build/nib-network-elf-inputs --target networkd < "$NETWORKD_ELF"
build/nib-network-elf-inputs --target nft < "$NFT_ELF"
```

Use the exact regular members of the already authenticated packages from the
existing preparer. The command authenticates the ELF member itself, not a DEB
archive or installation. It does not create an immutable evidence bundle or
authorize use of the input as a host program. Missing input is a blocker, not a
reason to download, substitute a host executable or change a pin.

## Source Inventory

The existing [preparer](../../scripts/prepare_network_proof.py) and
[guest package lock](../../image/inputs/guest-runtime-ubuntu2404.json) bind the
following original Ubuntu package members. Their raw ELF metadata was inspected
as data, without executing either member:

| Target | Package | Member | Bytes | SHA-256 |
| --- | --- | --- | --- | --- |
| networkd | systemd 255.4-1ubuntu8.18 | `usr/lib/systemd/systemd-networkd` | 1669864 | `e4ab77a36ee958f052b0100b60583716904a393159227e1e895469199e1fd8f9` |
| nft | nftables 1.0.9-1ubuntu0.1 | `usr/sbin/nft` | 26856 | `3f1c21553e62716ef1abfcf31f51ff94eb73ff4234a6653bac754a42f936d1c7` |

Both are little-endian amd64 ELF64 dynamic executables declaring interpreter
`/lib64/ld-linux-x86-64.so.2`. Networkd declares, in order,
`libsystemd-shared-255.so`, `libc.so.6`, and one `DT_RUNPATH` entry
`/usr/lib/x86_64-linux-gnu/systemd`. Nft declares `libnftables.so.1`,
`libedit.so.2`, `libc.so.6`, with no `DT_RUNPATH`. Neither declares `DT_RPATH`.
These strings name dependencies; they do not authenticate any library file.

Public package/source identities and primary-source links remain in the existing
[proof-input source findings](p3-t1-network-proof-inputs.md#source-findings).
Tests bind these member pins back to the preparer and this document, exercise
synthetic ELF mutations and deadline rejection, and retain the deny-all default.
Synthetic test ELFs are not runnable programs or evidence of package execution.

## Remaining Closure

Before assembling a synthetic-veth debug guest, authenticate the actual loader,
the declared libraries and their recursive dependencies against the existing
locked package archives. Reuse the bounded virtual package reader and symlink
resolver in [prepare_guest_tools.py](../../scripts/prepare_guest_tools.py), never
host filesystem resolution. Resolve search-path precedence, loader cache,
symbol/version requirements, architecture and collisions explicitly. Package
dependency metadata alone is insufficient; do not relabel this inventory as
verified runtime linkage.

Even a complete static dependency graph would not prove dynamic loading, NSS,
CA behavior, networkd configuration selection, privilege delivery, veth/kernel
support or the [isolated runtime cases](p3-t1-network-proof-inputs.md). Retained
archives at a different package revision must not substitute for missing locked
bytes. No image, release/default activation or existing preparation format is
changed here. Actual Linux execution and hardware remain separately gated.
