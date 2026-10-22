# P3.T1 private boot artifact export

Status: implemented and tested with synthetic subprocess outputs and local file
fixtures. **No native export, boot, Secure Boot or reproducibility result.**
This increment preserves the root GPT and unsigned UKI for a later separately
authorized firmware test; it does not complete P3.T1.

## Explicit local entry point

The existing native Ubuntu 22.04 x86-64 runner, pinned inputs and source checks
remain prerequisites. Run only from a clean committed checkout, with a fresh
evidence name under an existing runner-owned mode-0700 private directory:

```sh
sudo /usr/bin/python3.10 -I -S -B host/run-mkosi-build.py \
  --downloads /PRIVATE/downloads --archive /PRIVATE/mkosi-source.tar \
  --output /PRIVATE/run/evidence \
  --compile-driver --install-driver --guest-runtime --verity-root --boot-uki \
  --export-boot
```

These are placeholders for already authenticated local inputs, not a fetch or
execution authorization. The root launcher retains its existing runner identity
gate. Missing any prerequisite flag is rejected. The internal `--stage-boot`
switch is not a standalone export command: only the root resource launcher
finalizes a bundle after independent cleanup. The current manual workflow is
unchanged and has **no export input or binary upload step**. Do not point its
evidence upload at the private parent directory.

For the example evidence name, the only successful bundle is the fixed sibling
`/PRIVATE/run/evidence-boot/`, containing exactly:

- `root.raw`: the original repart-generated GPT with root EROFS and verity hash
  partitions, not a regenerated or reformatted disk.
- `nib.efi`: the original inspected unsigned UKI, not a second assembly.
- `manifest.json`: source revision and full 42-file source hash map, common root
  hash, exact artifact sizes/SHA-256, and receipt sizes/SHA-256.
- `run.json`, `result.json`, `resources.json`: exact copies of the generation
  receipts and the independent cgroup-cleanup receipt.

The bundle and intermediate directories are mode 0700; final files are 0600.
The hidden sibling `.evidence-boot-pending` is reserved for staging. Existing
pending or final names, including dangling links, are errors, never adopted or
overwritten. Binary files are outside the bounded evidence directory. There is
no release upload, signing service, network request or public artifact action.

## Ordering and failure behavior

1. All existing package, driver, runtime-policy and three appraiser CLI checks
   must succeed. EROFS/verity generation, decoding, positive verification,
   wrong-root rejection and final verification remain unchanged.
2. Only in export mode, retain the original GPT, data and hash files through
   the nested UKI build. Require root-hash-derived partition UUIDs and bind the
   UKI command line to that same verified root hash. Rehash the GPT before and
   after consumption, and recheck data/hash/UKI identities. No second generator
   substitutes bytes after verification.
3. Copy the exact verified images into fixed worker paths
   `/out/boot-stage/root.raw` and `/out/boot-stage/nib.efi`. No worker artifact
   name comes from a user path or receipt. UKI and verity helpers remove their
   original private workspaces before returning success. A late verification
   or cleanup failure removes the stage and cannot produce a successful result.
4. After the supervised worker has exited and its process group is gone, the
   unprivileged native builder fully copies and hashes the two staged files to
   the private pending sibling. It removes the entire payload workspace and
   requires all stage proofs before writing a passing generation receipt.
   Pending state binds the exact generation receipt bytes, not only their
   parsed fields. Any failed child-side stage removes pending state.
5. The root launcher checks child exit zero, zero OOM kills, actual cgroup
   limits, empty cgroup and removal. It durably writes the independent resource
   receipt before permanently dropping groups/GID/UID to the runner.
6. The unprivileged finalizer revalidates the current committed source closure,
   exact generation receipts, resource cleanup and artifact bytes. It adds the
   fixed receipts/manifest and uses the existing no-replace directory rename
   from `scripts/pilot/export.py`. Only that final local rename exposes the
   success bundle. No failed stage, unknown cleanup, changed source/root hash,
   changed file, missing receipt or collision can authorize it.

Failures remove only the directory identity created/observed for this run;
replacement identities are not deleted. A final rename or parent-fsync failure
attempts to remove that same bundle identity and returns failure. Cleanup errors
are not suppressed or called success. Process death, power loss, or an actual
filesystem cleanup failure can leave private pending/partial files; these are
not valid exports. There is no resume/adoption bypass. Preserve diagnostics and
investigate residue before a new invocation with a fresh output name.

Independent review found that the initial rollback used `shutil.rmtree(dir_fd=)`,
which [Python added only in 3.11](https://docs.python.org/3.11/library/shutil.html#shutil.rmtree),
not the pinned 3.10 host. Rollback now uses a shared helper in the already
source-bound file-input module. It opens the exact original directory without
following links, enumerates at most 16 flat entries, unlinks leaves relative to
that descriptor, rechecks directory identity, and removes it relative to the
parent descriptor. It never changes cwd or recurses into unexpected directories.
Nested/excess entries cause an explicit cleanup failure before deletion. The
[Python 3.10 OS interfaces](https://docs.python.org/3.10/library/os.html#files-and-directories)
provide descriptor-based scanning and relative unlink/rmdir. Both actual 3.10
and 3.12 interpreters must exercise rollback, including post-rename fsync failure.

## Bounds and reuse

The implementation reuses the descriptor-anchored directory/read helpers and
the existing private export parent's safety and exclusive-rename APIs. Copying
uses full streaming SHA-256, short-write handling, a destination rehash, and
stable descriptor/path identities. Symlinks, hard links, devices, FIFOs,
oversize/truncated/changed files and unexpected directory members are rejected.
There are no hard links, reflinks or artifact regeneration.

Existing GPT/UKI size ceilings remain 1568/256 MiB. Copies use at most 1 MiB per
chunk; stage and host capture each have a shared 120-second copy deadline inside
the existing worker/launcher bounds. All subprocess log caps remain 64 KiB per
fixed diagnostic stream; no raw image bytes enter logs. JSON is capped at 64 KiB.
The existing cgroup limits and 1900/2500-second process limits are unchanged.
Export adds two bounded **post-cleanup** phases: a 120-second fresh source audit
and a 120-second final verification/rename phase, using real-time alarms as well
as copy deadline checks. These phases run unprivileged, stream the images and
launch no image-building process. Ordinary filesystem operations/cleanup still
depend on the host filesystem responding; no wall-clock guarantee against a
kernel-stuck filesystem is claimed.

The generic extra-source limit remains 32. Exactly the 34-entry export closure
is also allowed, and only with the two fixed trailing paths
`scripts/mkosi_export.py` and `scripts/pilot/export.py`: 42 unique sources with
the eight base inputs. No package lock, tool pin, capability, namespace, source
comparison or runtime gate is relaxed. No new format parser is introduced.

## Format Basis and Next Gate

The unchanged [verity construction and primary sources](p3-t1-verity-root.md)
define the GPT partition layout and userspace verification. The unchanged
[initrd/UKI checks and pinned sources](p3-t1-initrd-uki.md) define the exact
systemd v255.4 command line, root-hash-derived device discovery and PE payload
checks. The [v255.4 stub specification, Description](https://raw.githubusercontent.com/systemd/systemd-stable/v255.4/man/systemd-stub.xml)
describes the `.linux`, `.initrd` and `.cmdline` resources in the UEFI binary.
This export preserves those bytes; it does not add an ESP, bootloader, firmware
configuration, GPT parser, writable-state policy or new kernel parameters.

The GPT and UKI are separate inputs to a future UEFI smoke harness, **not a
self-contained boot disk**. That harness must establish how firmware loads this
exact unsigned UKI, attach the unchanged root GPT, avoid auxiliary UKI inputs,
and collect original boot/root-switch/verity-enforcement evidence. Required
device/module and runtime writable-state behavior is still unproven. An unsigned
UKI and local hashes do not authenticate root selection against an adversary.
`signed`, `boot_verified`, `reproducibility_verified` and
`confidentiality_verified` remain false. No production or GPU action is added.

## Checks

From `scripts/`, using the repository's Python 3.12 test environment:

```sh
python -B -m unittest test_mkosi_export test_mkosi_verity test_mkosi_boot \
  test_mkosi_build test_prepare_mkosi_inputs test_build_resources
```

Tests cover exact successful local files/receipts/modes; all prior-stage proofs;
source/root binding; nested image lifetimes; every synthetic tool failure;
late byte changes; links/special files; copy/hash/short-write/deadline failures;
missing/changed cleanup receipts; collisions and replacement identities;
rename/fsync failures; explicit cleanup failure; and privilege-drop/receipt
ordering. No test calls the actual root launcher, native package builders,
firmware, hardware, cloud, or public release infrastructure.

Integration with main `eee2388421e09568ae28951d0d747c84267f1fea` retains the
transformed os-release inventory and bounded boot-member diagnostics. The nested
export callback passes the same diagnostics object as the ordinary boot path;
a rejected member preserves metadata while removing staged images, and successful
assembly clears the diagnostic. Export never bypasses the exact member hash.

Also run `test_mkosi_export` with an actual Python 3.10 interpreter and the test
PyYAML dependency available, not just a 3.10 syntax parse. The host-compatibility
run covers successful export, resource rejection, source-audit failure,
copy/capture failure, pre/post-rename rollback, nested/excess-entry refusal,
directory/parent replacement and leaf-link-safe cleanup. Python 3.12 remains
the worker/full-suite interpreter; neither runtime gate is changed.
