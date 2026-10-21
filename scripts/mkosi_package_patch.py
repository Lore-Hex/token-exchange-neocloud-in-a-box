# SPDX-License-Identifier: Apache-2.0
"""Exact source transformation, applied after the pinned service-denial fix."""
import hashlib
from pathlib import Path
import runpy
import types

ROOT = Path(__file__).absolute().parent.parent
PIN = "image/inputs/mkosi-27.1-package-boundary.json"
GUARD = "scripts/mkosi_confinement.py"
WORKER = "scripts/mkosi_package_worker.py"
guard = types.SimpleNamespace(**runpy.run_path(str(ROOT / GUARD)))

# This LGPL-2.1-or-later excerpt is from the pinned mkosi Apt.invoke; the
# generated derivative retains upstream's license and original source bundle.
BEFORE = b"""        return run(
            cls.cmd(context) + [operation, *arguments],
            sandbox=cls.sandbox(context, apivfs=apivfs, options=options),
            env=cls.finalize_environment(context),
            stdout=stdout,
        )
"""


def replacement():
    prefix = [*guard.PREFIX, "/usr/bin/python3.12", "-I", "-S", "-B", "/nib/package-worker.py"]
    return ("        return run(\n"
            "            " + repr(prefix) + " + cls.cmd(context) + [operation, *arguments],\n"
            "            sandbox=cls.sandbox(context, apivfs=apivfs,\n"
            "                options=[*options, '--ro-bind', '/nib', '/nib', '--chdir', '/']),\n"
            "            env=cls.finalize_environment(context),\n"
            "            stdout=stdout,\n"
            "        )\n").encode("ascii")


def apply(original, pin):
    if not (set(pin) == {"schema", "source_path", "mkosi_git_sha", "input_bytes", "input_sha256",
                         "result_bytes", "result_sha256"}
            and pin["schema"] == "nib.mkosi-package-boundary-patch/v1"
            and pin["source_path"] == "mkosi/installer/apt.py"
            and pin["mkosi_git_sha"] == "ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b"
            and len(original) == pin["input_bytes"] <= 2 << 20
            and hashlib.sha256(original).hexdigest() == pin["input_sha256"]
            and original.startswith(b"# SPDX-License-Identifier: LGPL-2.1-or-later\n")
            and original.count(BEFORE) == 1):
        raise ValueError("package-boundary-patch-rejected")
    result = original.replace(BEFORE, replacement(), 1)
    if not (len(result) == pin["result_bytes"] <= 2 << 20
            and hashlib.sha256(result).hexdigest() == pin["result_sha256"]):
        raise ValueError("package-boundary-patch-rejected")
    return result
