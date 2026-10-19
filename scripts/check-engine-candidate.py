#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Reproduce the BLOCKED vLLM source candidate, never build or activate it."""

import sys

if __name__ == "__main__" and not sys.flags.isolated:
    sys.exit("Use python3 -I scripts/check-engine-candidate.py")

import argparse
import ast
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import signal
import ssl
import stat
import subprocess
import tempfile
import time
import types
import unittest
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = "scripts/check-engine-candidate.py"
TEST = "scripts/test_engine_candidate.py"
DIRECTORY = "models/engine-patches/vllm-0.32.0-cc"
BASE = "5df63a7203cdb9328d824a27b682e77c7b9928be"
PARENT = "ce49174247fd84c322f293a31cbe1d0460aaa7fe"
COMMITS = ("c8309e4215fefe376a190ad68c872f832418d9f2", "c16c248aa2255dc2b13f732b00b90e44bb7311e4")
PATCHES = ("0001-v2-copy-backport.patch", "0002-prep-stream-ordering.patch",
           "0003-fail-closed-and-buffer-ordering.patch", "0004-reject-confidential-host-mapping.patch")
PATHS = ("vllm/envs.py", "vllm/platforms/cuda.py", "vllm/platforms/interface.py",
         "vllm/utils/platform_utils.py", "vllm/utils/torch_utils.py", "vllm/v1/conf_compute_utils.py",
         "vllm/v1/utils.py", "vllm/v1/worker/gpu/async_utils.py", "vllm/v1/worker/gpu/buffer_utils.py",
         "vllm/v1/worker/gpu/model_states/encoder_decoder.py", "vllm/v1/worker/gpu/model_states/encoder_only.py")
LIMIT = 2 << 20
BOOTSTRAP = Path(__file__).read_bytes()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_regular(path, limit=LIMIT):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError("bounded regular input required")
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        def identity(value):
            return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
                    value.st_mtime_ns, value.st_ctime_ns)
        if (len(data) > limit or identity(before) != identity(after)
                or identity(after) != identity(path.lstat())):
            raise ValueError("input changed or exceeded limit")
        return data


def tool_env():
    # No inherited Git replacements/config/index, Python path, proxy or credentials.
    return {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin", "HOME": "/nonexistent",
            "LANG": "C", "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_NO_REPLACE_OBJECTS": "1"}


def git(root, *args, input=None):
    result = subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull, "-C", str(root), *args],
                            env=tool_env(), input=input, capture_output=True, timeout=30)
    if result.returncode or len(result.stdout) > LIMIT or len(result.stderr) > 65536:
        raise ValueError("bounded Git operation failed")
    return result.stdout


def committed_bundle(root):
    revision = git(root, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("commit required")
    entries = git(root, "ls-tree", "-r", "-z", revision, "--", DIRECTORY, SCRIPT, TEST)
    bundle = {}
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        meta, path = entry.decode().split("\t", 1)
        mode, kind, oid = meta.split()
        if mode not in ("100644", "100755") or kind != "blob" or len(bundle) >= 32:
            raise ValueError("regular bounded source bundle required")
        if int(git(root, "cat-file", "-s", oid)) > LIMIT:
            raise ValueError("source blob exceeds limit")
        bundle[path] = git(root, "cat-file", "blob", oid)
        if sum(map(len, bundle.values())) > LIMIT:
            raise ValueError("source bundle exceeds limit")
    return revision, bundle


def working_bundle(root=ROOT):
    bundle = {name: read_regular(root / name) for name in (SCRIPT, TEST)}
    for path in sorted((root / DIRECTORY).rglob("*")):
        if path.is_symlink():
            raise ValueError("candidate symlink rejected")
        if not path.is_dir():
            bundle[path.relative_to(root).as_posix()] = read_regular(path)
    if len(bundle) > 32 or sum(map(len, bundle.values())) > LIMIT:
        raise ValueError("source bundle exceeds limit")
    return bundle


def bundle_digest(bundle):
    digest = hashlib.sha256()
    for path, data in sorted(bundle.items()):
        digest.update(path.encode() + b"\0" + len(data).to_bytes(8, "big") + data)
    return digest.hexdigest()


def validate_bootstrap(bundle):
    if bundle.get(SCRIPT) != BOOTSTRAP:
        raise ValueError("recorder differs from committed source")


def metadata(bundle):
    local = {path.removeprefix(DIRECTORY + "/"): data for path, data in bundle.items()
             if path.startswith(DIRECTORY + "/")}
    manifest = json.loads(local["candidate.json"])
    if (manifest["schema"] != "nib.engine-candidate/v1" or manifest["status"] != "BLOCKED"
            or manifest["deployable"] is not False or manifest["image"] is not None
            or manifest["base_revision"] != BASE or manifest["original_parent"] != PARENT
            or manifest["license"] != "Apache-2.0"
            or manifest["upstream_repository"] != "https://github.com/vllm-project/vllm"
            or manifest["pull_request"] != 59981
            or tuple(f["path"] for f in manifest["files"]) != PATHS
            or tuple(p["path"] for p in manifest["patches"]) != PATCHES):
        raise ValueError("candidate identity or blocked state differs")
    expected = {"candidate.json", "LICENSE", "NOTICE", "README.md", *PATCHES}
    if sha256(local["LICENSE"]) != manifest["license_sha256"]:
        raise ValueError("license differs")
    if tuple(c["revision"] for c in manifest["upstream_commits"]) != COMMITS:
        raise ValueError("upstream commit series differs")
    for commit, parent in zip(manifest["upstream_commits"], (PARENT, COMMITS[0])):
        name = "upstream/" + commit["revision"] + ".patch"
        expected.add(name)
        if (commit["patch"] != name or commit["parent"] != parent
                or commit["url"] != "https://github.com/vllm-project/vllm/commit/" + commit["revision"]
                or sha256(local[name]) != commit["sha256"]):
            raise ValueError("upstream provenance differs")
    if local[PATCHES[1]] != local["upstream/" + COMMITS[1] + ".patch"]:
        raise ValueError("upstream ordering patch must be preserved verbatim")
    for item in manifest["patches"]:
        if sha256(local[item["path"]]) != item["sha256"]:
            raise ValueError("candidate patch differs")
    for item in manifest["files"]:
        hashes = item["sha256"]
        if (len(hashes) != len(PATCHES) + 1 or any(not isinstance(h, str) or not re.fullmatch(r"[0-9a-f]{64}", h)
                                   for h in hashes[1:]) or item["mode"] not in ("100644", "100755")):
            raise ValueError("invalid file contract")
        if item["path"] == "vllm/v1/conf_compute_utils.py":
            if hashes[0] is not None or item["base_url"] is not None or item["base_bytes"] != 0:
                raise ValueError("new file must be absent at base")
            continue
        name = "base/" + item["path"]
        expected.add(name)
        url = "https://raw.githubusercontent.com/vllm-project/vllm/" + BASE + "/" + item["path"]
        if (item["base_url"] != url or len(local[name]) != item["base_bytes"]
                or sha256(local[name]) != hashes[0]):
            raise ValueError("pinned base differs")
    if set(local) != expected or set(bundle) != {DIRECTORY + "/" + p for p in expected} | {SCRIPT, TEST}:
        raise ValueError("unexpected or missing bundle input")
    return manifest, local


def verify(bundle):
    manifest, local = metadata(bundle)
    with tempfile.TemporaryDirectory(prefix="nib-cc-check-") as directory:
        root = Path(directory)
        for item in manifest["files"]:
            if item["sha256"][0] is not None:
                path = root / item["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(local["base/" + item["path"]])
                path.chmod(int(item["mode"], 8) & 0o777)
        for stage, patch in enumerate(PATCHES, 1):
            git(root, "apply", "--check", "--whitespace=error-all", "-", input=local[patch])
            git(root, "apply", "--whitespace=error-all", "-", input=local[patch])
            if {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()} != set(PATHS):
                raise ValueError("patch changed unexpected paths")
            for item in manifest["files"]:
                path = root / item["path"]
                data = read_regular(path)
                if (sha256(data) != item["sha256"][stage]
                        or stat.S_IMODE(path.stat().st_mode) != int(item["mode"], 8) & 0o777):
                    raise ValueError("patched file differs")
        result = {path: read_regular(root / path) for path in PATHS}
    for data in result.values():
        ast.parse(data)  # Syntax only: never import the upstream package.
    return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("source redirect rejected")


def fetch_sources(bundle):
    manifest, local = metadata(bundle)
    sources = [(item["base_url"], local["base/" + item["path"]])
               for item in manifest["files"] if item["base_url"] is not None]
    sources.append(("https://raw.githubusercontent.com/vllm-project/vllm/" + BASE + "/LICENSE", local["LICENSE"]))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                        urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    proofs = []
    deadline = time.monotonic() + 180
    for url, expected in sources:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("source fetch deadline exceeded")
        request = urllib.request.Request(url, headers={"Accept-Encoding": "identity"})
        with opener.open(request, timeout=min(20, remaining)) as response:
            if response.status != 200 or response.headers.get("Content-Encoding", "identity") != "identity":
                raise ValueError("unexpected source response")
            length = response.headers.get("Content-Length")
            if length is not None and int(length) != len(expected):
                raise ValueError("source size differs")
            data = bytearray()
            while len(data) <= len(expected):
                if time.monotonic() >= deadline:
                    raise ValueError("source fetch deadline exceeded")
                part = response.read1(min(65536, len(expected) + 1 - len(data)))
                if not part:
                    break
                data.extend(part)
            if data != expected:
                raise ValueError("source bytes differ")
        proofs.append({"url": url, "bytes": len(data), "sha256": sha256(data)})
    return proofs


def contract_tests(bundle):
    # Direct committed source loading avoids ignored bytecode and Python paths.
    module = types.ModuleType("candidate_contract_tests")
    module.__file__ = str(ROOT / TEST)
    module.CHECKER = sys.modules[__name__]
    module.BUNDLE = bundle
    exec(compile(bundle[TEST], TEST, "exec"), module.__dict__)
    result = unittest.TextTestRunner(stream=io.StringIO()).run(unittest.defaultTestLoader.loadTestsFromModule(module))
    if not result.wasSuccessful() or result.testsRun == 0:
        raise ValueError("software contract tests failed")
    return result.testsRun


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="Compare 11 fixed public source responses; no packages")
    parser.add_argument("--record", action="store_true", help="Use committed Git blobs and retain software-only evidence")
    args = parser.parse_args()
    started = now()
    if args.record:
        # Read raw blobs, never status/checkout filters or working-tree inputs.
        revision, bundle = committed_bundle(ROOT)
        validate_bootstrap(bundle)
    else:
        revision, bundle = None, working_bundle()
    report = {"result": "fail", "candidate_status": "BLOCKED", "deployable": False,
              "hardware_validated": False, "scope": "source-and-software-contracts-only",
              "git_sha": revision, "bundle_sha256": bundle_digest(bundle), "public_sources": []}
    # A process-wide bound also covers slow-drip response headers, not only reads.
    def expired(signum, frame):
        raise TimeoutError("candidate check deadline exceeded")
    previous = signal.signal(signal.SIGALRM, expired)
    signal.alarm(300)
    try:
        verify(bundle)
        report["focused_tests"] = contract_tests(bundle)
        if args.fetch:
            report["public_sources"] = fetch_sources(bundle)
        report["files"] = json.loads(bundle[DIRECTORY + "/candidate.json"])["files"]
        report["result"] = "pass"
    except Exception:
        report["failure_code"] = "candidate-check-failed"
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
    if args.record:
        record = {"schema": "nib.evidence/v1", "phase": "P0", "task": "T4", "git_sha": revision,
                  "command": "python3 -I scripts/check-engine-candidate.py --record" + (" --fetch" if args.fetch else ""),
                  "started_utc": started, "ended_utc": now(),
                  "host": {"provider": "local", "machine": platform.system().lower() + "/" + platform.machine(),
                           "gpus": "none", "driver": None, "cuda": None},
                  "result": report["result"], "execution": "software", "hardware_validated": False,
                  "notes": "BLOCKED candidate. Sparse source application and fake-object software contracts only; "
                           "no upstream package import, build, GPU, attestation or deployment. "
                           "Committed input bundle SHA-256: " + report["bundle_sha256"]}
        parent = ROOT / "evidence/p0"
        if parent.is_symlink() or parent.parent.is_symlink():
            raise ValueError("evidence parent must not be a symlink")
        destination = parent / (started.replace(":", "") + "-vllm-cc-candidate")
        destination.mkdir(mode=0o700)  # Existing evidence is never overwritten.
        for name, value in (("run.json", record), ("output.log", report)):
            with (destination / name).open("x") as stream:
                stream.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
        print(destination.relative_to(ROOT))
    else:
        print(json.dumps({key: report[key] for key in ("result", "candidate_status", "scope")}, sort_keys=True))
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit("Candidate check failed; no successful result may be inferred.")
