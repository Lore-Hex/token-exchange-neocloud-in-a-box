#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fetch the reviewed immutable engine image; never launch it."""

import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site):
    sys.exit("Use python3 -I -S pull-lab-image.py")

import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile

IMAGE = "vllm/vllm-openai@sha256:6ee133356d49ed24cee03dd0cda3d3980279a11c107ce3e752c89e76540f41c9"
CONFIG = "sha256:bd8a5d7a8774800c92d714c0c226b32c1863f929150f36efd610aebcd12a335d"
MANIFEST = IMAGE.rsplit("@", 1)[1]
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/nonexistent",
       "LANG": "C", "LC_ALL": "C"}


@contextmanager
def docker_environment():
    # A nonempty auth map prevents Docker's native credential-store discovery.
    # https://github.com/docker/cli/blob/v29.1.3/cli/config/config.go
    with tempfile.TemporaryDirectory(prefix="nib-docker-auth-") as temporary:
        path = Path(temporary) / "config.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump({"auths": {"https://index.docker.io/v1/": {}}}, output)
        yield {**ENV, "DOCKER_CONFIG": temporary}


def image_identity(env):
    value = subprocess.run(
        ["/usr/bin/docker", "image", "inspect", IMAGE],
        env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
    if value.returncode:
        return False
    if len(value.stdout) > 65536:
        raise ValueError("image-metadata-too-large")
    rows = json.loads(value.stdout)
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError("image-metadata-rejected")
    row = rows[0]
    descriptor = row.get("Descriptor") or {}
    digests = row.get("RepoDigests") or []
    # Docker's classic store reports the config ID; its containerd store reports
    # the manifest ID. Both must still identify this exact requested manifest.
    if (row.get("Id") not in (CONFIG, MANIFEST) or row.get("Os") != "linux"
            or row.get("Architecture") != "amd64" or not isinstance(descriptor, dict)
            or not isinstance(digests, list) or not all(isinstance(item, str) for item in digests)
            or (descriptor and descriptor.get("digest") != MANIFEST)
            or (IMAGE not in digests
                and descriptor.get("digest") != MANIFEST)):
        raise ValueError("image-identity-mismatch")
    return True


def main():
    with docker_environment() as env:
        cached = image_identity(env)
        if not cached:
            child = subprocess.Popen(
                ["/usr/bin/docker", "pull", "--platform", "linux/amd64", IMAGE],
                env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True)
            try:
                if child.wait(timeout=900) != 0:
                    raise ValueError("image-pull-failed")
            finally:
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=5)
        if not image_identity(env):
            raise ValueError("image-not-present")
    print(json.dumps({"result": "pass", "cached": cached, "image": IMAGE,
                      "expected_config": CONFIG, "binding": "manifest-reference-and-platform",
                      "config_bytes_verified": False, "executed": False, "confidential_qualified": False,
                      "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.SubprocessError):
        print('{"result":"fail","reason":"lab-image-pull-rejected"}')
        sys.exit(1)
