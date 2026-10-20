#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fetch the reviewed immutable engine image; never launch it."""

import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site):
    sys.exit("Use python3 -I -S pull-lab-image.py")

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess

IMAGE = "vllm/vllm-openai@sha256:6ee133356d49ed24cee03dd0cda3d3980279a11c107ce3e752c89e76540f41c9"
CONFIG = "sha256:bd8a5d7a8774800c92d714c0c226b32c1863f929150f36efd610aebcd12a335d"
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/nonexistent",
       "LANG": "C", "LC_ALL": "C", "DOCKER_CONFIG": "/nonexistent"}


def image_identity():
    value = subprocess.run(
        ["/usr/bin/docker", "image", "inspect", "--format",
         "{{.Id}} {{.Os}}/{{.Architecture}}", IMAGE],
        env=ENV, stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
    if value.returncode:
        return False
    if value.stdout.decode().strip() != CONFIG + " linux/amd64":
        raise ValueError("image-identity-mismatch")
    return True


def main():
    cached = image_identity()
    if not cached:
        child = subprocess.Popen(
            ["/usr/bin/docker", "pull", "--platform", "linux/amd64", IMAGE],
            env=ENV, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            if child.wait(timeout=900) != 0:
                raise ValueError("image-pull-failed")
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)
    if not image_identity():
        raise ValueError("image-not-present")
    print(json.dumps({"result": "pass", "cached": cached, "image": IMAGE,
                      "config": CONFIG, "executed": False, "confidential_qualified": False,
                      "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.SubprocessError):
        print('{"result":"fail","reason":"lab-image-pull-rejected"}')
        sys.exit(1)
