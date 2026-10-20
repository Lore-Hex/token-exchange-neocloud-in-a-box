# P3.T1 exact tools-tree member preparation

## Gaps and bounded correction

These are data-only preparation findings from the 203-package pinned mkosi
tools graph, not observations of running its programs. The original archives,
package locks, source licenses and historical evidence are unchanged.

1. The existing ASCII-only payload reader rejects the regular certificate
   member `usr/share/ca-certificates/mozilla/NetLock_Arany_=Class_Gold=_F\u0151tan\u00fas\u00edtv\u00e1ny.crt`
   (the spelling above uses Unicode escapes) from `ca-certificates`
   `20260601~24.04.1`, archive SHA-256
   `6bac2a01979e210d9eac1d4d56747ec709ea60654744d66705dc3c36e7629e50`.
   Correction: permit only the exact archive-bound regular data member with
   exact UTF-8 spelling, size, mode and content hash. No general Unicode path
   mode, normalization, link exception or executable exception is introduced.
2. Raw package payloads contain `usr/bin/gawk`, but no `usr/bin/awk`.
   `gawk` `1:5.2.1-2ubuntu0.1`, archive SHA-256
   `baaf6853bea746485bff4ae0e506599b3c5093ebe6ec9b9584e4272f94f75b93`,
   has `control.tar` member `postinst:4-7` registering that alternative.
   Correction: verify that script and the binary without executing either,
   then add only `usr/bin/awk -> /etc/alternatives/awk -> /usr/bin/gawk`.
   This is a recorded preparation alias, not a configured package or an
   update-alternatives database. Existing conflicting paths reject.
3. `grub-pc-bin` `2.12-1ubuntu7.3`, archive SHA-256
   `698d80bc51c0c593fbc829758365a0e4bce96819e2c8c754ae77dfe39b0388df`,
   contains 278 ELF32 i386 members under `usr/lib/grub/i386-pc`: 277
   relocatable objects and one ET_EXEC `kernel.img` boot-loader input.
   These are boot-loader inputs, not Linux/amd64 host executables.
   Correction: classify only the complete exact path/size/mode/hash/header
   inventory as non-host data. Missing, changed, relocated or additional
   foreign ELF rejects; no prefix-based architecture exemption is allowed.

Primary archive references are the authenticated `Filename`, `Version` and
`SHA256` entries in the unchanged
[tools lock](../../image/inputs/mkosi-tools-ubuntu2404.json). No proprietary
source or packaged binary is copied into this repository.

The new [member policy](../../image/inputs/mkosi-tools-members.json) binds each
of these three packages to that exact lock and records its public archive URL,
archive size/hash and relevant member size/mode/hash. The preparer additionally
pins the policy's complete bytes. It is not a user-supplied whitelist.

## Data-only preparation

From a committed source tree, use the existing
[mkosi input bundle](p3-t1-mkosi-build-inputs.md) as input:

```sh
python3 -I scripts/prepare_mkosi_tools.py \
  --inputs /absolute/private/mkosi-inputs \
  --output /absolute/private/new-tools-output
python3 -I scripts/prepare_mkosi_tools.py \
  --inputs /absolute/private/mkosi-inputs \
  --verify /absolute/private/new-tools-output
```

Preparation revalidates signed index coverage and every selected archive and
control record; it does not trust the input bundle's receipt alone. Existing
output is never overwritten. Inputs are no-follow, nonblocking, single-link
regular reads. Publication uses a private directory and files. Limits remain
50,000 virtual members, 256 MiB expanded per package, 512 MiB aggregate output,
and a shared 600-second preparation deadline; this task's tools graph is also
bounded to 256 packages and 128 MiB of compressed archives.

The four output files are `tools-root.tar`, `root-inventory.json`,
`member-policy.json` and `receipt.json`. The tar preserves certificate bytes
and package ownership paths in a virtual merged-/usr tree; it is not extracted
or loaded as an image. The receipt records the two prepared aliases, all
classified GRUB members, unchanged host executable/interpreter header checks,
source hashes and output hashes. Verification reconstructs all four files and
requires byte identity. No mkosi launcher, OCI configuration or runtime grant
is produced.

Focused regressions cover real synthetic archive parsing/assembly/publication,
default ASCII rejection, exact literal data, foreign ELF headers and membership,
tampering, path/type/link conflicts, deadlines, no-overwrite and deterministic
reconstruction. Synthetic maintainer scripts are never executed. Existing
APT simulation and source preparation contracts retain their default behavior.

## Scope

Preparation must reuse bounded no-follow input reads, virtual merged-/usr
resolution, collision checks, deterministic archive writing and private
no-overwrite publication. The default ASCII payload reader and existing
amd64 executable/interpreter checks remain strict.

No package, maintainer script, mkosi program or container executes. Static
header classification and file availability do not prove dynamic loading,
symbol versions, ABI compatibility, namespaces, installation, boot,
reproducibility or confidential qualification. Those gates remain separate.
