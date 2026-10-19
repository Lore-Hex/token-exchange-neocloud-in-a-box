#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Record the read-only DNS preflight; never publish raw subprocess errors."""

import argparse
import contextlib
import datetime
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tarfile
import tempfile


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def command_environment():
    # Build settings must not inject workspaces, overlays, tags, plugins or
    # cross-compilation into a source-pinned evidence check.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("GIT_", "GO", "CGO_"))}
    for cache in ("GOCACHE", "GOMODCACHE"):
        if os.environ.get(cache):
            env[cache] = os.environ[cache]
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GOENV="off", GOWORK="off", GOFLAGS="-mod=readonly", CGO_ENABLED="0",
               GOTOOLCHAIN="auto", GOPROXY="https://proxy.golang.org", GOSUMDB="sum.golang.org")
    return env


def command(root, args):
    env = command_environment()
    return subprocess.run(args, cwd=root, env=env, capture_output=True, text=True, timeout=120)


def extract_snapshot(root, sha, temporary, execute=command):
    archive = temporary / "source.tar"
    snapshot = temporary / "source"
    snapshot.mkdir()
    archived = execute(root, ["git", "archive", "--format=tar", "--output=" + str(archive), sha])
    if archived.returncode or archive.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("cannot create bounded committed-source snapshot")
    with tarfile.open(archive, "r:") as source:
        if any(not (entry.isfile() or entry.isdir()) for entry in source):
            raise ValueError("source snapshot must not contain links or special files")
        # Explicit data filter also protects Python versions whose default was
        # fully_trusted: https://docs.python.org/3/library/tarfile.html#extraction-filters
        source.extractall(snapshot, filter="data")
    return snapshot


@contextlib.contextmanager
def build_snapshot(root, sha):
    with tempfile.TemporaryDirectory(prefix="nib-dns-source-") as directory:
        temporary = Path(directory)
        snapshot = extract_snapshot(root, sha, temporary)
        # Refuse locally modified downloaded modules, even when a build cache
        # would otherwise avoid reading them. No ambient Go workspace is used.
        if command(snapshot, ["go", "mod", "verify"]).returncode:
            raise ValueError("module integrity check failed")
        binaries = {}
        for name in ("nib-domain-check", "nib"):
            binary = temporary / name
            built = command(snapshot, ["go", "build", "-trimpath", "-buildvcs=false",
                                       "-o", str(binary), "./cmd/" + name])
            if built.returncode:
                raise ValueError("committed-source build failed")
            binaries[name] = str(binary)
        yield binaries


def record(root, domain, slug, execute=command, now=utc_now, builder=build_snapshot):
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug) or len(slug) > 64:
        raise ValueError("invalid evidence slug")
    # Cleanliness catches accidental edits; the exact committed snapshot, not
    # git status, excludes ignored source files from the executed checker.
    revision = execute(root, ["git", "rev-parse", "--verify", "HEAD"])
    status = execute(root, ["git", "status", "--porcelain", "--untracked-files=all"])
    sha = revision.stdout.strip()
    if revision.returncode or status.returncode or status.stdout or not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise ValueError("commit the source tree before recording evidence")
    with builder(root, sha) as binaries:
        return record_check(root, domain, slug, sha, binaries, execute, now)


def record_check(root, domain, slug, sha, binaries, execute, now):
    started = now()
    args = [binaries["nib-domain-check"], "--domain", domain]
    checked = execute(root, args)
    ended = now()
    report = json.loads(checked.stdout)
    expected_names = [domain, "api." + domain]
    if (not isinstance(report, dict) or set(report) != {"scope", "assurance", "checks"}
            or report["scope"] != "dns-only" or not isinstance(report["assurance"], str)
            or not isinstance(report["checks"], list) or len(report["checks"]) != 2):
        raise ValueError("unexpected DNS report")
    for item, name in zip(report["checks"], expected_names):
        if (not isinstance(item, dict) or set(item) != {"name", "status", "address_count"}
                or item["name"] != name or item["status"] not in {"resolved", "missing-address", "lookup-error"}
                or type(item["address_count"]) is not int or item["address_count"] < 0
                or (item["status"] == "resolved") != (item["address_count"] > 0)):
            raise ValueError("unexpected DNS report")
    passed = all(item["status"] == "resolved" for item in report["checks"])
    if checked.returncode != (0 if passed else 1):
        raise ValueError("DNS result disagrees with process status")
    after = execute(root, ["git", "rev-parse", "--verify", "HEAD"])
    status = execute(root, ["git", "status", "--porcelain", "--untracked-files=all"])
    if after.returncode or after.stdout.strip() != sha or status.returncode or status.stdout:
        raise ValueError("source changed while recording evidence")

    # Reuse the traversal-resistant, exclusive evidence directory creation.
    scaffold = execute(root, [binaries["nib"], "evidence", "new", "P1", slug])
    relative = scaffold.stdout.strip()
    if scaffold.returncode or not re.fullmatch(r"evidence/p1/\d{4}-\d{2}-\d{2}-" + re.escape(slug), relative):
        raise ValueError("cannot create new evidence directory")
    destination = root / relative
    run = {
        "schema": "nib.evidence/v1", "phase": "P1", "task": "T5", "git_sha": sha,
        "command": "python3 scripts/record-domain-check.py --domain " + domain + " --slug " + slug,
        "started_utc": started, "ended_utc": ended,
        "host": {"provider": "local", "machine": platform.system().lower() + "/" + platform.machine(),
                 "gpus": "none", "driver": None, "cuda": None},
        "result": "pass" if passed else "fail", "execution": "software", "hardware_validated": False,
        "notes": "DNS-only preflight built from a committed Git archive with controlled Go settings. "
                 "No HTTP to candidate hosts, inference, cloud mutations or attestation validation. "
                 "A pass means only that both names resolve through the system resolver.",
    }
    # Store the exact sanitized report before replacing the not_run scaffold.
    (destination / "output.log").write_text(checked.stdout)
    temporary = destination / "run.json.pending"
    with temporary.open("x") as stream:
        stream.write(json.dumps(run, indent=2, sort_keys=True) + "\n")
    temporary.replace(destination / "run.json")
    return relative, 0 if passed else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--slug", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    try:
        relative, code = record(root, args.domain, args.slug)
    except (OSError, ValueError, TypeError, tarfile.TarError, subprocess.SubprocessError):
        print("DNS evidence recording failed; no successful result may be inferred.", file=sys.stderr)
        return 1
    print(relative)
    return code


if __name__ == "__main__":
    sys.exit(main())
