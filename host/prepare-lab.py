#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prepare private directories and an exact public source checkout on a test VM."""

import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site):
    sys.exit("Run with python3 -I -S prepare-lab.py")

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess

BUNDLE_SHA256 = "764ef9f90f3c5989440b14dc56fe9da36db17a26466bc8418bc8dde3a97b6c50"
REVISION = "45e7a09039a312429a73b605021b8d18828af4b3"
ENV = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/nonexistent",
    "LANG": "C", "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0",
    "GIT_ALLOW_PROTOCOL": "file",
}


def private_directory(path, *, create=False):
    value = Path(path)
    if not value.is_absolute() or str(value) != str(value.resolve()):
        raise ValueError("canonical-absolute-path-required")
    parent = value.parent.stat()
    if parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) & 0o077:
        raise ValueError("private-owned-parent-required")
    if create:
        try:
            value.mkdir(mode=0o700)
        except FileExistsError:
            pass
    info = value.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077):
        raise ValueError("private-owned-directory-required")
    return value


def command(argv, *, cwd=None):
    result = subprocess.run(argv, cwd=cwd, env=ENV, stdin=subprocess.DEVNULL,
                            capture_output=True, timeout=60, check=False)
    if result.returncode or len(result.stdout) > 65536 or len(result.stderr) > 65536:
        raise ValueError("preparation-command-failed")
    return result.stdout


def checkout(root):
    bundle = root / "source-45e7a09.bundle"
    info = bundle.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_size > 16 * 1024 * 1024
            or hashlib.sha256(bundle.read_bytes()).hexdigest() != BUNDLE_SHA256):
        raise ValueError("source-bundle-mismatch")
    source = root / "source"
    if source.exists() or source.is_symlink():
        raise ValueError("source-destination-exists")
    git = ["/usr/bin/git", "-c", "core.hooksPath=/dev/null"]
    command(git + ["clone", "--no-checkout", "--", str(bundle), str(source)])
    command(git + ["-C", str(source), "checkout", "--detach", REVISION])
    actual = command(git + ["-C", str(source), "rev-parse", "HEAD"]).decode().strip()
    if actual != REVISION:
        raise ValueError("source-revision-mismatch")
    return {"source_revision": actual, "source_bundle_sha256": BUNDLE_SHA256}


def prerequisites():
    names = ("python3", "git", "docker", "containerd", "nvidia-container-cli",
             "nvidia-ctk", "nvidia-smi", "apt-cache", "apt-get")
    return {name: shutil.which(name, path=ENV["PATH"]) is not None for name in names}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("init", "checkout", "prerequisites"))
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    root = private_directory(args.root, create=args.operation == "init")
    result = {"operation": args.operation, "confidential_qualified": False}
    if args.operation == "checkout":
        result.update(checkout(root))
    elif args.operation == "prerequisites":
        result["commands_present"] = prerequisites()
    else:
        private_directory(root / "stage", create=True)
    result["script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.SubprocessError):
        print('{"result":"fail","reason":"lab-preparation-rejected"}')
        sys.exit(1)
