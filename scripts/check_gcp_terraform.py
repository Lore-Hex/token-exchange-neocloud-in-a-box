#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Credential-free Terraform validation and real Terraform *mock* plan receipts."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile

from e2e.snapshot import execute_process_group


ROOT = Path(__file__).resolve().parent.parent
MODULE = Path("platforms/gcp/terraform")
FILES = ("versions.tf", "variables.tf", "main.tf", "outputs.tf",
         ".terraform.lock.hcl", "tests/lab.tftest.hcl")
RUNS = {"default_zero", "one_spot_lab", "explicit_zero", "reject_two",
        "reject_fraction", "reject_public_ssh", "reject_subnet_ssh", "reject_ipv6",
        "reject_family", "reject_production", "reject_unbounded_runtime",
        "h100_default_zero", "one_h100_tdx_spot_lab", "h100_europe_zone",
        "h100_east_zone", "reject_h100_other_zone", "reject_unknown_hardware"}
COMMANDS = (
    ("fmt", "-check", "-recursive", "-no-color"),
    ("init", "-backend=false", "-input=false", "-lockfile=readonly", "-no-color"),
    ("validate", "-no-color"),
    ("test", "-filter=tests/lab.tftest.hcl", "-no-color", "-verbose"),
    ("test", "-filter=tests/lab.tftest.hcl", "-json"),
)


def utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def isolated_environment(control):
    for name in ("home", "tmp"):
        (control / name).mkdir()
    config = control / "terraform.rc"
    config.write_text("disable_checkpoint = true\n", encoding="ascii")
    return {
        "PATH": "/usr/bin:/bin", "HOME": str(control / "home"),
        "TMPDIR": str(control / "tmp"), "LANG": "C", "LC_ALL": "C",
        "TF_CLI_CONFIG_FILE": str(config), "TF_IN_AUTOMATION": "1",
        "CHECKPOINT_DISABLE": "1",
    }


def stage_module(source, destination):
    # Do not copy state, auto.tfvars, CLI config, provider cache or extra tests.
    actual = {p.name for pattern in ("*.tf", "*.tf.json") for p in source.glob(pattern)}
    if actual != {n for n in FILES if n.endswith(".tf")}:
        raise ValueError("unreviewed Terraform source file set")
    for name in FILES:
        original = source / name
        if original.is_symlink() or not original.is_file():
            raise ValueError("Terraform inputs must be regular files")
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(original.read_bytes())


def require_pass(output):
    events = [json.loads(line) for line in output.splitlines() if line.strip()]
    summaries = [e["test_summary"] for e in events if e.get("type") == "test_summary"]
    expected = {"status": "pass", "passed": len(RUNS), "failed": 0, "errored": 0, "skipped": 0}
    completed = [e["test_run"] for e in events if e.get("type") == "test_run"
                 and e["test_run"].get("progress") == "complete"]
    if summaries != [expected] or len(completed) != len(RUNS) or {
        r["run"] for r in completed if r.get("status") == "pass"
    } != RUNS:
        raise ValueError("missing, failed or skipped Terraform mock plan tests")


def run(destination):
    binary = shutil.which("terraform")
    if binary is None:
        raise ValueError("install the pinned Terraform CLI 1.14.9")
    destination.mkdir(parents=True, exist_ok=False)
    started = utc()
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, timeout=10).strip()
    bound = [MODULE / name for name in FILES] + [
        Path("scripts/check_gcp_terraform.py"), Path("scripts/e2e/snapshot.py")]
    hashes = {str(p): hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in bound}
    passed = False
    try:
        with tempfile.TemporaryDirectory(prefix="nib-terraform-") as temporary:
            control = Path(temporary)
            env = isolated_environment(control)
            work = control / "module"
            stage_module(ROOT / MODULE, work)
            with (destination / "output.log").open("wb") as log:
                log.write(b"MOCK PLANS ONLY. No cloud plan, apply, credentials or resource allocation.\n")
                for args in COMMANDS:
                    log.write(("\n$ terraform " + " ".join(args) + "\n").encode())
                    result = execute_process_group(
                        [binary, *args], cwd=work, env=env, timeout=300,
                        max_output_bytes=2 * 1024 * 1024, stdin=subprocess.DEVNULL)
                    log.write(result.stdout)
                    log.write(result.stderr)
                    log.flush()
                    if result.returncode:
                        raise ValueError("Terraform " + args[0] + " failed; see output.log")
                    if "-json" in args:
                        (destination / "tests.jsonl").write_bytes(result.stdout)
                        require_pass(result.stdout)
            passed = True
    finally:
        hashes.update({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in destination.iterdir() if p.is_file()})
        (destination / "sha256.json").write_text(json.dumps(hashes, indent=2) + "\n")
        receipt = {
            "schema": "nib.evidence/v1", "phase": "P2", "task": "T1", "git_sha": revision,
            "command": "python3 scripts/check_gcp_terraform.py --evidence <new-directory>",
            "started_utc": started, "ended_utc": utc(),
            "host": {"provider": "local", "machine": platform.system() + " " + platform.machine(),
                     "gpus": "none used", "driver": None, "cuda": None},
            "result": "pass" if passed else "fail", "execution": "fake", "hardware_validated": False,
            "notes": "Actual pinned Terraform mock-provider plans and schema validation, NOT a GCP/cloud plan. "
                     "Synthetic inputs only; no apply, credentials, cloud API call or allocation. "
                     "sha256.json binds the executed source bytes (including any local edits) and logs. "
                     "The git SHA identifies the checkout, not a claim that it is clean. "
                     "No quota, capacity, billing, permissions, image, API acceptance or boot validation.",
        }
        (destination / "run.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"Terraform mock plans passed: {len(RUNS)} cases; evidence: {destination}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, help="new directory; never overwrites receipts")
    args = parser.parse_args()
    if args.evidence is None:
        build = ROOT / "build"
        build.mkdir(exist_ok=True)
        args.evidence = Path(tempfile.mkdtemp(prefix="gcp-terraform-", dir=build)) / "evidence"
    run(args.evidence.resolve())


if __name__ == "__main__":
    main()
