#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build pinned public inputs, then run a secret-free, network-none fixture node."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import urllib.request
import uuid


ROOT = Path(__file__).resolve().parents[2]
PIN_FILE = ROOT / "scripts/e2e/pins.json"


class PendingPinError(ValueError):
    pass


def load_pins(path=PIN_FILE, *, engine_only=False):
    pins = json.loads(path.read_text())
    if pins.get("schema") != "nib.e2e-pins/v1" or pins.get("platform") != "linux/amd64":
        raise ValueError("unsupported E2E pin format or platform")
    for field in ("go_image", "python_image"):
        if not re.fullmatch(r"docker\.io/library/[a-z]+:[\w.-]+@sha256:[0-9a-f]{64}", pins.get(field, "")):
            raise ValueError("base image is not digest-pinned")
    if not engine_only:
        trcs = pins.get("trcs")
        if not isinstance(trcs, dict):
            raise PendingPinError("full E2E awaits the parent's merged trcs pin")
        for field, length in (("commit", 40), ("archive_sha256", 64), ("binary_sha256", 64)):
            if not re.fullmatch("[0-9a-f]{%d}" % length, trcs.get(field, "")):
                raise ValueError("trcs commit/archive/binary pin is incomplete")
    return pins


def source_paths(root=ROOT, *, env=None):
    names = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root, env=env
    ).decode().split("\0")
    for name in sorted(set(names) - {""}):
        p = Path(name)
        allowed = name in ("go.mod", "go.sum") or (
            p.suffix in (".go", ".json", ".yaml", ".txt", ".py", ".lock", ".in", ".nft", ".sha256")
            and (p.parts[0] in ("guest", "cmd", "internal", "schemas") or name.startswith(("tests/e2e/", "scripts/e2e/")))
        )
        if not allowed:
            continue
        source = root / p
        if source.is_symlink() or not source.resolve().is_relative_to(root.resolve()) or not source.is_file():
            raise ValueError("unsafe build-context source")
        yield p


def build_context(destination, pins, *, engine_only=False):
    digest = hashlib.sha256()
    for relative in source_paths():
        data = (ROOT / relative).read_bytes()
        digest.update(str(relative).encode() + b"\0" + data + b"\0")
        target = destination / "repo" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    shutil.copyfile(ROOT / "scripts/e2e/Dockerfile", destination / "Dockerfile")
    digest.update(b"Dockerfile\0" + (destination / "Dockerfile").read_bytes())
    inputs = {
        "git_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip(),
        "source_sha256": digest.hexdigest(),
        "scope": "engine-only" if engine_only else "full-fake-node",
        "hardware_validated": False,
        "pins": pins,
    }
    (destination / "inputs.json").write_text(json.dumps(inputs, indent=2) + "\n")
    if not engine_only:
        url = "https://codeload.github.com/Lore-Hex/trustedrouter-confidential-stack/tar.gz/" + pins["trcs"]["commit"]
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read((16 << 20) + 1)
        if len(data) > 16 << 20 or hashlib.sha256(data).hexdigest() != pins["trcs"]["archive_sha256"]:
            raise ValueError("trcs source archive digest mismatch")
        (destination / "trcs.tar.gz").write_bytes(data)
    return inputs


def runtime_command(image, name):
    # Docker 29.4 CLI reference: network none, no host mounts/ports/secrets/devices.
    return ["docker", "run", "--name", name, "--rm", "--init", "--network", "none",
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--user", "65532:65532", "--pids-limit", "128", "--memory", "512m", "--cpus", "2",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777", "--platform", "linux/amd64", image]


def cleanup_resources(image, name):
    resources = {
        "container": (name, ["docker", "rm", "-f", name],
                      ["docker", "container", "ls", "--all", "--quiet", "--filter", "name=^/" + re.escape(name) + "$"]),
        "image": (image, ["docker", "image", "rm", image],
                  ["docker", "image", "ls", "--all", "--quiet", "--filter", "reference=" + image]),
    }
    report = {}
    # Attempt both removals even if one times out or Docker cannot be reached.
    for kind, (resource, remove, _) in resources.items():
        item = report[kind] = {"resource": resource}
        try:
            result = subprocess.run(remove, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
            item["removal"] = {"status": "exited", "exit_code": result.returncode}
        except subprocess.TimeoutExpired:
            item["removal"] = {"status": "timeout"}
        except (OSError, subprocess.SubprocessError):
            item["removal"] = {"status": "error"}
    # A removal error may mean --rm already removed the container. Only a
    # successful, empty listing proves absence; daemon errors never do.
    for kind, (_, _, query) in resources.items():
        try:
            result = subprocess.run(query, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10)
            report[kind]["absence"] = ("unknown" if result.returncode != 0 else
                                       "present" if result.stdout.strip() else "confirmed")
        except (OSError, subprocess.SubprocessError):
            report[kind]["absence"] = "unknown"
    report["result"] = "pass" if all(report[kind]["absence"] == "confirmed" for kind in resources) else "fail"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-only", action="store_true", help="bounded prerequisite check, not full P1.T3")
    args = parser.parse_args()
    pins = load_pins(engine_only=args.engine_only)
    run_id = uuid.uuid4().hex[:12]
    image, name = "nib-e2e-local:" + run_id, "nib-e2e-" + run_id
    result_dir = ROOT / "build/e2e" / run_id
    result_dir.mkdir(parents=True)
    summary = {"execution": "fake", "hardware_validated": False, "confidential": False,
               "scope": "engine-only" if args.engine_only else "full-fake-node",
               "result": "fail", "failed_check": "host-run-failed"}
    exit_code = 1
    try:
        with tempfile.TemporaryDirectory(prefix="nib-e2e-context-") as tmp:
            context = Path(tmp)
            inputs = build_context(context, pins, engine_only=args.engine_only)
            (result_dir / "inputs.json").write_text(json.dumps(inputs, indent=2) + "\n")
            command = ["docker", "build", "--platform", pins["platform"], "--target",
                       "engine-e2e" if args.engine_only else "node-e2e", "--tag", image,
                       "--build-arg", "GO_IMAGE=" + pins["go_image"],
                       "--build-arg", "PYTHON_IMAGE=" + pins["python_image"]]
            if not args.engine_only:
                command += ["--build-arg", "TRCS_ARCHIVE_SHA256=" + pins["trcs"]["archive_sha256"],
                            "--build-arg", "TRCS_BINARY_SHA256=" + pins["trcs"]["binary_sha256"]]
            subprocess.run(command + [str(context)], check=True, timeout=900)
        result = subprocess.run(runtime_command(image, name), capture_output=True, timeout=180)
        # The fixture runner emits one bounded summary, never process/body logs.
        if len(result.stdout) > 1 << 20:
            raise ValueError("oversized E2E result")
        runtime_summary = json.loads(result.stdout)
        if not isinstance(runtime_summary, dict):
            raise ValueError("invalid E2E summary")
        summary = runtime_summary
        exit_code = 0 if result.returncode == 0 and summary.get("result") == "pass" else 1
        if exit_code:
            summary["result"] = "fail"
    finally:
        summary["cleanup"] = cleanup_resources(image, name)
        if summary["cleanup"]["result"] != "pass":
            summary["result"] = "fail"
            summary["failed_check"] = "cleanup-unresolved"
            exit_code = 1
        # Never publish success before resource absence has been verified.
        (result_dir / "result.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary, sort_keys=True))
        print("Software-only test artifacts:", result_dir)
    return exit_code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PendingPinError:
        raise SystemExit("Full E2E unavailable: merged trcs commit/archive/binary pins are required; no runtime started")
    except (ValueError, OSError, subprocess.SubprocessError):
        # Do not relay tool exceptions that may contain environment/proxy inputs.
        raise SystemExit("E2E failed; check pinned inputs, build output and software-only result artifacts")
