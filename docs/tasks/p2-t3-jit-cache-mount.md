# P2.T3: Executable private JIT cache

The [seventh B300 trial](../../evidence/p2/2026-10-21-b300-triton-import/run.json)
passed all 83 weight checks and all five application stages, then failed after
257099 ms of engine startup. The original bounded diagnostic in
[output.log](../../evidence/p2/2026-10-21-b300-triton-import/output.log) identifies
an `ImportError` in Triton's compiled `cuda_utils` loader, not an established
memory-allocation failure. Cleanup succeeded and Docker reported no OOM kill.

The exact source hashes match Triton revision
`f797708c0626e5f9840ca5b0a98790e2c7cb09ad`:

- [NVIDIA driver.py](https://github.com/triton-lang/triton/blob/f797708c0626e5f9840ca5b0a98790e2c7cb09ad/third_party/nvidia/backend/driver.py)
  lines 336 and 66, SHA-256
  `992ba21e12ee170a904f3a1caca24a78ce3dd66b661087faea5973b995480f63`.
- [runtime/build.py](https://github.com/triton-lang/triton/blob/f797708c0626e5f9840ca5b0a98790e2c7cb09ad/python/triton/runtime/build.py)
  lines 97 and 69, SHA-256
  `26c6ddd4dba3534ce54790f12d2b75729f51e467c48b2f740dd62b7c75a85b39`.
  Line 69 loads the newly compiled extension with `module_from_spec`.

The lab requested a tmpfs cache without an explicit `exec` option.
[Moby's mount implementation](https://github.com/moby/moby/blob/ff225faca0b9a4a7e40da68aa8de9ffe196ab7bd/daemon/oci_linux.go)
sets `noexec`, `nosuid`, and `nodev` before merging explicit tmpfs options.
[Docker documents the override](https://docs.docker.com/engine/storage/tmpfs/).
Leaving `noexec` in effect is incompatible with
loading native JIT libraries from that cache. The previous effective mount
flags were not recorded, so the loader failure's attribution remains a
hypothesis until a new hardware run passes this point.

The lab now explicitly requests `exec` only for its private `/cache` tmpfs.
Its 16 GiB limit, mode 0700, `nosuid`, and `nodev` remain unchanged. General
`/tmp` stays `noexec`. Before any hardware probe, weight hashing, or engine
startup, Linux `statvfs` must confirm both mounts' effective execution,
writeability, device, and set-ID flags. Failure stops the run without inspecting
or logging exception messages. Docker configuration inspection independently
rejects missing or changed options.

Tests cover effective flags, readonly mounts, missing restrictions, inaccessible
mounts, early failure, and create/inspect option consistency. Run the complete
`scripts.test_deepseek_lab` suite and `make check` before any paid experiment.
This changes only the bounded lab harness, not production filesystem policy,
native-code provenance, attestation requirements, or model-quality acceptance.
