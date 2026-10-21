#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Enter the final package boundary after the trusted mkosi sandbox is ready."""
import os
from pathlib import Path
import runpy
import socket
import sys
import types


def main():
    if not (sys.platform == "linux" and sys.version_info[:2] == (3, 12)
            and sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode
            and os.geteuid() == os.getegid() == 0
            and len(sys.argv) > 2 and sys.argv[1] == "apt-get"
            and socket.if_nameindex() == [(1, "lo")]):
        raise ValueError("package-worker-rejected")
    # /nib is a read-only, source-authenticated payload in the private tools root.
    guard = types.SimpleNamespace(**runpy.run_path("/nib/confinement.py", run_name="package_confinement"))
    guard.check()
    if not Path("/usr/bin/apt-get").is_file():
        raise ValueError("package-worker-rejected")
    os.execv("/usr/bin/apt-get", ["/usr/bin/apt-get", *sys.argv[2:]])


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError):
        sys.exit("NIB_PACKAGE_WORKER_REJECTED")
