#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""One bounded, offline package installation in the prepared namespace."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import types

STAGES = ("preflight", "namespaces", "pid", "ownership", "configuration", "installation", "inventory")
COMMAND = ["/usr/bin/python3.12", "-I", "-S", "-B", "/nib/mkosi-entry.py",
           "--directory=/work/config", "--tools-tree=/", "--output=rootfs",
           "--output-directory=/work/output", "--workspace-directory=/work/workspaces",
           "--cache-directory=/work/cache", "--package-cache-directory=/work/pkgcache",
           "--build-sources=", "--history=no", "build"]


def require(value):
    if not value:
        raise ValueError("build-worker-rejected")


def module(name):
    return types.SimpleNamespace(**runpy.run_path("/nib/" + name + ".py", run_name="fixed_build_" + name))


def configured(raw, expected, parser):
    require(type(raw) is bytes and 0 < len(raw) <= 8 << 20)
    rows = list(parser(raw, fields=("Package", "Version", "Architecture", "Status")))
    actual = {}
    for row in rows:
        require(row["Package"] not in actual and row["Architecture"] in ("amd64", "all")
                and row["Status"] == "install ok installed")
        actual[row["Package"]] = row["Version"]
    require(actual == expected)
    return len(actual)


def installation_environment():
    # mkosi config.py calls getpass.getuser(). UID 0 exists only in the private
    # namespace, not in the extracted tools tree's intentionally absent passwd.
    return {"PATH": "/usr/bin:/usr/sbin:/bin:/sbin", "HOME": "/tmp",
            "USER": "root", "LOGNAME": "root", "LANG": "C", "LC_ALL": "C",
            "PYTHONDONTWRITEBYTECODE": "1", "SOURCE_DATE_EPOCH": "1792454400"}


def main():
    stage = "preflight"
    report = {"schema": "nib.mkosi-install-worker/v1", "result": "fail", "stage": stage,
              "installed_packages": 0, "installer_exit_code": None, "failure_kind": None,
              "package_installation_executed": False, "configured_state_verified": False,
              "dkms_built": False, "reproducibility_verified": False, "bootable": False, "deployable": False}
    try:
        require(sys.platform == "linux" and sys.version_info[:2] == (3, 12)
                and sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode
                and len(sys.argv) == 6 and os.geteuid() == os.getegid() == 0)
        stage = "namespaces"
        module("namespace-check").check(sys.argv[1:5])
        stage = "pid"
        module("pid-check").check(sys.argv[5])
        stage = "ownership"
        module("ownership").check(json.loads(Path("/nib/ownership-input.json").read_bytes()))
        stage = "configuration"
        processes = module("snapshot")
        inputs = module("package-inputs")
        guest = json.loads(Path("/nib/prepared/guest-inputs.json").read_bytes())
        expected = {row["Package"]: row["Version"] for row in guest["packages"]}
        require(len(expected) == len(guest["packages"]) == 145)
        for name in ("config", "output", "workspaces", "cache", "pkgcache"):
            Path("/work", name).mkdir(mode=0o700)
        config = Path("/work/config")
        (config / "mkosi.conf").write_bytes(Path("/nib/prepared/mkosi.conf.candidate").read_bytes())
        for name in ("repo", "mkosi-skeleton"):
            (config / name).symlink_to("/nib/prepared/" + name)
        stage = "installation"
        report["package_installation_executed"] = None
        result = processes.execute_process_group(COMMAND, env=installation_environment(),
            timeout=1800, max_output_bytes=2 << 20,
            stdin=subprocess.DEVNULL)
        report["installer_exit_code"] = result.returncode
        # This is a source/package-only lab with no user data or credentials.
        # Keep bounded local diagnostics separately from the public typed result.
        if result.stdout:
            Path("/out/installer.stdout").write_bytes(result.stdout)
        if result.stderr:
            Path("/out/installer.stderr").write_bytes(result.stderr)
        result.check_returncode()
        stage = "inventory"
        raw = inputs.regular(Path("/work/output/rootfs/var/lib/dpkg/status"), 8 << 20)
        report["installed_packages"] = configured(raw, expected, inputs.paragraphs)
        report.update(result="pass", configured_state_verified=True, package_installation_executed=True)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        report["failure_kind"] = ("timeout" if isinstance(exc, subprocess.TimeoutExpired)
                                  else "process-exit" if isinstance(exc, subprocess.CalledProcessError)
                                  else "rejected")
    report["stage"] = stage
    Path("/out/result.json").write_text(json.dumps(report, sort_keys=True) + "\n")
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
