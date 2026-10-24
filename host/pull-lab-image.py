#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fetch one of the reviewed immutable lab images; never launch it."""

import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site):
    sys.exit("Use python3 -I -S pull-lab-image.py")

import argparse
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
# Same OCI index -> linux/amd64 manifest -> config as scripts/tau_trial.py
# and scripts/e2e/pins.json; no floating tag or caller-supplied reference.
CHECKER_IMAGE = "docker.io/library/python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2"
CHECKER_MANIFEST = "sha256:6e13e65c55e33adf203d77ee371cf8bf5d81bd4902ef07565721f46bf44917af"
CHECKER_CONFIG = "sha256:64d91f7b885eed272bba87909446b12ff408d4aaa5f1a0e9ca787bbea1a020b9"
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


def selection(profile):
    if profile == "engine":
        return IMAGE, CONFIG, MANIFEST
    if profile == "provider-check":
        return CHECKER_IMAGE, CHECKER_CONFIG, CHECKER_MANIFEST
    raise ValueError("image-profile-rejected")


def image_identity(env, profile="engine"):
    image, config, manifest = selection(profile)
    # The existing hosted Docker 28 engine path has no inspect --platform flag.
    # The checker uses Docker 29's descriptor path, like its runtime supervisor.
    arguments = ["/usr/bin/docker", "image", "inspect"]
    if profile == "provider-check":
        arguments += ["--platform", "linux/amd64"]
    value = subprocess.run(
        arguments + [image],
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
    if (row.get("Id") not in (config, manifest) or row.get("Os") != "linux"
            or row.get("Architecture") != "amd64" or not isinstance(descriptor, dict)
            or not isinstance(digests, list) or not all(isinstance(item, str) for item in digests)
            or (descriptor and descriptor.get("digest") != manifest)
            or (profile == "engine" and image not in digests
                and descriptor.get("digest") != manifest)):
        raise ValueError("image-identity-mismatch")
    if profile == "provider-check":
        references = {name + "@" + image.rsplit("@", 1)[1] for name in
                      ("python", "library/python", "docker.io/library/python")}
        if (not references.intersection(digests) or row.get("Variant") not in (None, "")
                or not isinstance(row.get("Config"), dict) or row["Config"].get("Volumes")
                or (descriptor and (descriptor.get("mediaType") != "application/vnd.oci.image.manifest.v1+json"
                    or descriptor.get("platform") != {"os": "linux", "architecture": "amd64"}))):
            raise ValueError("image-identity-mismatch")
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("engine", "provider-check"), default="engine")
    args = parser.parse_args(argv)
    image, config, _manifest = selection(args.profile)
    with docker_environment() as env:
        cached = image_identity(env, args.profile)
        if not cached:
            child = subprocess.Popen(
                ["/usr/bin/docker", "pull", "--platform", "linux/amd64", image],
                env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True)
            try:
                if child.wait(timeout=900) != 0:
                    raise ValueError("image-pull-failed")
            finally:
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=5)
        if not image_identity(env, args.profile):
            raise ValueError("image-not-present")
    print(json.dumps({"result": "pass", "cached": cached, "image": image,
                      "expected_config": config, "binding": "manifest-reference-and-platform",
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
