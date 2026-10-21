# Pinned mkosi service-denial patch

`mkosi-27.1-service-denial.patch` is a local modification of mkosi 27.1 commit
`ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b`,
[`mkosi/installer/apt.py`](https://github.com/systemd/mkosi/blob/ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b/mkosi/installer/apt.py#L245-L274).
The patch and modified upstream file are **LGPL-2.1-or-later**, matching the
original file's SPDX declaration. The original source archive, license files,
SPDX declaration and all other source bytes are preserved by the preparer.
This is a local patch, not an upstream release or upstream-approved change.

The sole insertion sets `policy-rc.d` to mode `0755` after writing the existing
`#!/bin/sh\nexit 101\n` body, before invoking APT. This also corrects a prior
`0644` file, and applies each time the installer recreates the file. The existing
cleanup and error behavior are otherwise unchanged. This does not harden
arbitrary pre-existing filesystem links or replace outer installation isolation.

The [patch pin](../inputs/mkosi-27.1-service-denial.json) binds the original,
patch and resulting file sizes and SHA-256 values. The
[input preparer](../../scripts/prepare_mkosi_inputs.py) first verifies the complete
upstream archive, then checks this one hunk at its exact line position. No fuzzy
application, patch subprocess, upstream import or installation is performed.
The bundle retains the original archive and emits the patch, pin and modified
file under `mkosi-overlay/mkosi/installer/apt.py`; it is not a runnable tools tree.
Future execution must explicitly use and verify this overlay, rather than run
the original archive alone.

This addresses a **source-inspection finding**, backed by synthetic filesystem
tests. No service start, package installation, mkosi build or guest boot was
observed. See [scope and remaining gates](../../docs/tasks/p3-t1-mkosi-build-inputs.md).
