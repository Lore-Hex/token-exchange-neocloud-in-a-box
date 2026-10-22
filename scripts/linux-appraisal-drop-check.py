#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Linux-only local container regression; never runs nvattest or uses GPUs."""
import codecs
import encodings
import io
import os
from pathlib import Path
import runpy
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
probe = runpy.run_path(str(ROOT / "mkosi_appraisal_probe.py"), run_name="fixture_probe")
guard = runpy.run_path(str(ROOT / "mkosi_confinement.py"), run_name="fixture_guard")
require = probe["require"]
require(sys.platform == "linux" and sys.flags.isolated and sys.flags.no_site
        and sys.flags.dont_write_bytecode)
if sys.argv[1:] in (["--codec-cold"], ["--codec-warm"]):
    warm = sys.argv[1] == "--codec-warm"
    require(os.geteuid() == 0)
    with tempfile.TemporaryDirectory(prefix="nib-empty-root-") as root:
        pid = os.fork()
        if pid == 0:
            try:
                codecs.unregister(encodings.search_function)
                codecs.register(encodings.search_function)
                sys.modules.pop("encodings.ascii", None)
                if warm:
                    probe["prepare_chroot_runtime"]()
                os.chroot(root)
                os.chdir("/")
                try:
                    text = io.TextIOWrapper(io.BytesIO(b"status\n"), encoding="ascii").read()
                except LookupError:
                    os._exit(1 if warm else 0)
                os._exit(0 if warm and text == "status\n" else 1)
            except BaseException:
                os._exit(2)
        _, status = os.waitpid(pid, 0)
        require(os.waitstatus_to_exitcode(status) == 0)
    print("Empty-root codec positive/negative control passed; no vendor or GPU execution.")
elif sys.argv[1:] == ["--after-exec"]:
    probe["unprivileged_status"](Path("/proc/self/status").read_text(), guard["CAP_MASK"])
    print("Unprivileged UID/GID and capability state survived exec; no vendor or GPU execution.")
elif len(sys.argv) == 1:
    guard["status_check"](Path("/proc/self/status").read_text(), filtered=True)
    require(os.geteuid() == os.getegid() == 0)
    probe["prepare_chroot_runtime"]()
    os.chroot("/")
    os.chdir("/")
    probe["drop_privileges"](guard["CAP_MASK"])
    os.execve(sys.executable, [sys.executable, "-I", "-S", "-B", str(Path(__file__).resolve()),
                              "--after-exec"], {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C"})
else:
    raise ValueError("fixed-regression-only")
