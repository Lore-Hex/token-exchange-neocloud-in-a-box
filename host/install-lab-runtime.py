#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Install a fixed Docker/NVIDIA container runtime on a fresh approved test VM."""

import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site):
    sys.exit("Use python3 -I -S install-lab-runtime.py")

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import signal
import subprocess

# Observed in the signed Ubuntu Resolute and NVIDIA CUDA apt repositories.
# https://packages.ubuntu.com/resolute/docker.io
# https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html
PACKAGES = {
    "docker.io": "29.1.3-0ubuntu4.1",
    "containerd": "2.2.2-0ubuntu1.1",
    "runc": "1.4.0-0ubuntu1",
    "nvidia-container-toolkit": "1.20.1-1",
    "nvidia-container-toolkit-base": "1.20.1-1",
    "libnvidia-container-tools": "1.20.1-1",
    "libnvidia-container1": "1.20.1-1",
}
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root", "LANG": "C",
       "LC_ALL": "C", "DEBIAN_FRONTEND": "noninteractive", "NEEDRESTART_MODE": "l"}


def run(argv, *, capture=False):
    if capture:
        result = subprocess.run(argv, env=ENV, stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=30, check=True)
        if len(result.stdout) + len(result.stderr) > 65536:
            raise ValueError("runtime-metadata-too-large")
        return result.stdout.decode("utf-8", errors="strict")
    child = subprocess.Popen(argv, env=ENV, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
    try:
        if child.wait(timeout=600) != 0:
            raise ValueError("runtime-operation-failed")
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)
    return ""


def installation_plan(text):
    installs = {}
    for line in text.splitlines():
        if line.startswith("Remv "):
            raise ValueError("package-removal-rejected")
        if line.startswith("Inst "):
            match = re.fullmatch(r"Inst ([a-z0-9.-]+) \(([^ ]+) .+\)", line)
            if not match or match[1] in installs:
                raise ValueError("upgrade-or-unknown-package-rejected")
            installs[match[1]] = match[2]
    if installs != PACKAGES:
        raise ValueError("package-plan-mismatch")
    return installs


def platform_check():
    if os.geteuid() != 0 or platform.machine() != "x86_64":
        raise ValueError("fresh-approved-root-amd64-host-required")
    release = {}
    for line in Path("/etc/os-release").read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            release[key] = value.strip('"')
    if release.get("ID") != "ubuntu" or release.get("VERSION_ID") != "26.04":
        raise ValueError("ubuntu-2604-required")
    for name in ("/etc/docker/daemon.json", "/var/lib/docker", "/usr/bin/docker"):
        if os.path.lexists(name):
            raise ValueError("existing-runtime-rejected")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    platform_check()
    pins = [f"{name}={version}" for name, version in PACKAGES.items()]
    base = ["/usr/bin/apt-get", "--no-install-recommends", "--no-remove", "--no-upgrade"]
    installation_plan(run(base + ["-s", "install", *pins], capture=True))
    if args.install:
        run(base + ["-y", "install", *pins])
        actual = run(["/usr/bin/dpkg-query", "-W", "-f=${Package}=${Version}\\n",
                      *PACKAGES], capture=True)
        if dict(line.split("=", 1) for line in actual.splitlines()) != PACKAGES:
            raise ValueError("installed-version-mismatch")
        # Official toolkit configuration; no GPU driver or CC-mode modification.
        run(["/usr/bin/nvidia-ctk", "runtime", "configure", "--runtime=docker"])
        config = json.loads(Path("/etc/docker/daemon.json").read_text())
        if ("hosts" in config or config.get("runtimes", {}).get("nvidia", {}).get("path")
                not in ("nvidia-container-runtime", "/usr/bin/nvidia-container-runtime")):
            raise ValueError("unexpected-docker-runtime-configuration")
        run(["/usr/bin/systemctl", "restart", "docker"])
        run(["/usr/bin/systemctl", "is-active", "--quiet", "docker"])
    print(json.dumps({"result": "pass", "installed": args.install, "packages": PACKAGES,
                      "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                      "hardware_validated": False, "confidential_qualified": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.SubprocessError):
        print('{"result":"fail","reason":"lab-runtime-rejected"}')
        sys.exit(1)
