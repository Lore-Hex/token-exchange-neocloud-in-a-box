# Native verity pass, boot member rejected

Original artifacts from [run 39794127265](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39794127265),
producer `527adf09912614515c8313df82499643be2c19a8`. The run failed; do not
replace this receipt with a rerun or infer a successful UKI build from it.

The 212-package installation, five driver builds/installations, startup policy,
and all three `nvattest` CLI help probes passed. The quiet EROFS build passed:
GPT/partition identity, filesystem checking, userspace verity verification and
the deliberately wrong-root rejection all passed. `verity-reject-root.stderr`
is the expected negative control, not the build failure.

Boot assembly then rejected an installed member at `mkosi_boot.py:95`, called
from `assemble` line 173. This check requires a bounded, regular, single-link
file. The original diagnostic does not name the member or distinguish the
failed condition. A specific size, hard-link or file-type cause is **not yet
proven**. The next diagnostic records only the pinned package member name,
expected size/hash, and observed file type/link count/size; no file contents or
exception strings. No admission check or resource limit changes.

All private artifacts and processes were removed. The 8 GiB cgroup peaked at
7,106,355,200 bytes, had zero OOM kills and was empty before removal. No boot,
export, signing, TDX, GPU execution or confidentiality verification occurred.

Original SHA-256:

- `run.json`: `a4d0e0cb6dc3f5ba09ed8c8ab35fccfad00bb6a182f7be8b4c8a5dcbe78189b6`
- `result.json`: `7ba63d04be2662ff3e3fcb3cda09c3251f3d367b542596640c9d310f245d360f`
- `evidence-resources.json`: `55a68dfee7e0fadd57413c2e2a5576d86086cca09446aa6a603dd7a6a366aeb7`
