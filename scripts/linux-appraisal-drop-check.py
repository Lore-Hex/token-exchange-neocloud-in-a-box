#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Linux-only local container regression; never runs nvattest or uses GPUs."""
import os
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parent
probe = runpy.run_path(str(ROOT / "mkosi_appraisal_probe.py"), run_name="fixture_probe")
guard = runpy.run_path(str(ROOT / "mkosi_confinement.py"), run_name="fixture_guard")
require = probe["require"]
require(sys.platform == "linux" and sys.flags.isolated and sys.flags.no_site
        and sys.flags.dont_write_bytecode)
if sys.argv[1:] == ["--after-exec"]:
    probe["unprivileged_status"](Path("/proc/self/status").read_text(), guard["CAP_MASK"])
    print("Unprivileged UID/GID and capability state survived exec; no vendor or GPU execution.")
elif len(sys.argv) == 1:
    guard["status_check"](Path("/proc/self/status").read_text(), filtered=True)
    require(os.geteuid() == os.getegid() == 0)
    os.chroot("/")
    os.chdir("/")
    probe["drop_privileges"](guard["CAP_MASK"])
    os.execve(sys.executable, [sys.executable, "-I", "-S", "-B", str(Path(__file__).resolve()),
                              "--after-exec"], {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C"})
else:
    raise ValueError("fixed-regression-only")
