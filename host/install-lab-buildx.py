#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Install one verified Buildx binary on an approved Linux amd64 test host."""

import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site):
    sys.exit("Use python3 -I -S install-lab-buildx.py")

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import stat
import subprocess
import tempfile

# Official manual-install directory and release checksums, checked 2026-10-24:
# https://github.com/docker/buildx#manual-download
# https://github.com/docker/buildx/releases/tag/v0.38.1
VERSION = "v0.38.1"
SIZE = 68366498
SHA256 = "e25d5cd675092bb5d1d03632fdc46cdf3fede7b514dc5f023a105e00d3ce5ad4"
TARGET = Path("/usr/local/lib/docker/cli-plugins/docker-buildx")


def verified_bytes(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size != SIZE:
            raise ValueError("binary-size-or-type-mismatch")
        data = stream.read(SIZE + 1)
    if len(data) != SIZE or hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError("binary-digest-mismatch")
    return data


def secure_directory(path):
    for directory in reversed((path, *path.parents)):
        try:
            directory.mkdir(mode=0o755)
        except FileExistsError:
            pass
        info = directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
                or info.st_mode & 0o022):
            raise ValueError("unsafe-install-directory")


def install(data):
    secure_directory(TARGET.parent)
    if os.path.lexists(TARGET):
        info = TARGET.lstat()
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o755:
            raise ValueError("existing-plugin-permissions-mismatch")
        verified_bytes(TARGET)
        return False
    fd, temporary = tempfile.mkstemp(prefix=".buildx-", dir=TARGET.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fchmod(output.fileno(), 0o755)
            os.fsync(output.fileno())
        # Atomic create, never replace a plugin installed by another operator.
        os.link(temporary, TARGET)
    finally:
        os.unlink(temporary)
    return True


def verify_plugin():
    with tempfile.TemporaryDirectory(prefix="nib-buildx-check-") as directory:
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": directory,
               "DOCKER_CONFIG": directory, "BUILDX_CONFIG": directory + "/buildx",
               "DOCKER_HOST": "unix:///var/run/docker.sock", "LANG": "C", "LC_ALL": "C"}
        result = subprocess.run(["/usr/bin/docker", "buildx", "version"],
                                env=env, stdin=subprocess.DEVNULL, capture_output=True,
                                timeout=10, check=True)
        fields = result.stdout.decode("ascii").strip().split()
        if len(fields) != 3 or fields[:2] != ["github.com/docker/buildx", VERSION]:
            raise ValueError("installed-plugin-version-mismatch")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0 or platform.system() != "Linux" or platform.machine() != "x86_64":
        raise ValueError("approved-root-linux-amd64-host-required")
    data = verified_bytes(args.binary)
    installed = install(data)
    verify_plugin()
    print(json.dumps({"result": "pass", "installed": installed, "version": VERSION,
                      "binary_sha256": SHA256, "binary_size": SIZE,
                      "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                      "hardware_validated": False, "confidential_qualified": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.SubprocessError):
        print('{"result":"fail","reason":"lab-buildx-rejected"}')
        sys.exit(1)
