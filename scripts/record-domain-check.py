#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Record the read-only DNS preflight; never publish raw subprocess errors."""

import argparse
import datetime
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def command(root, args):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return subprocess.run(args, cwd=root, env=env, capture_output=True, text=True, timeout=120)


def record(root, domain, slug, execute=command, now=utc_now):
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug) or len(slug) > 64:
        raise ValueError("invalid evidence slug")
    # A clean source tree makes the recorded revision meaningful. Ignored build
    # outputs are not source; the checker itself is compiled from this revision.
    revision = execute(root, ["git", "rev-parse", "--verify", "HEAD"])
    status = execute(root, ["git", "status", "--porcelain", "--untracked-files=all"])
    sha = revision.stdout.strip()
    if revision.returncode or status.returncode or status.stdout or not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise ValueError("commit the source tree before recording evidence")
    started = now()
    args = ["go", "run", "./cmd/nib-domain-check", "--domain", domain]
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
    scaffold = execute(root, ["go", "run", "./cmd/nib", "evidence", "new", "P1", slug])
    relative = scaffold.stdout.strip()
    if scaffold.returncode or not re.fullmatch(r"evidence/p1/\d{4}-\d{2}-\d{2}-" + re.escape(slug), relative):
        raise ValueError("cannot create new evidence directory")
    destination = root / relative
    run = {
        "schema": "nib.evidence/v1", "phase": "P1", "task": "T5", "git_sha": sha,
        "command": " ".join(args), "started_utc": started, "ended_utc": ended,
        "host": {"provider": "local", "machine": platform.system().lower() + "/" + platform.machine(),
                 "gpus": "none", "driver": None, "cuda": None},
        "result": "pass" if passed else "fail", "execution": "software", "hardware_validated": False,
        "notes": "DNS-only preflight. No HTTP, inference, cloud mutations or attestation validation. "
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
    except (OSError, ValueError, subprocess.SubprocessError):
        print("DNS evidence recording failed; no successful result may be inferred.", file=sys.stderr)
        return 1
    print(relative)
    return code


if __name__ == "__main__":
    sys.exit(main())
