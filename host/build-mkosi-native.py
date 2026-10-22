#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Reconstruct and run one fixed offline image-installation candidate."""
import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
    sys.exit("Use python3 -I -S -B host/build-mkosi-native.py")

import argparse
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import tarfile
import tempfile
import time
import types

ROOT = Path(__file__).absolute().parent.parent
SCRIPT = "host/build-mkosi-native.py"
PROBE = "host/probe-mkosi-native-tools.py"
WORKER = "scripts/mkosi_build_worker.py"
ENTRY = "scripts/mkosi_build_entry.py"
RESOURCES = "host/probe-build-resources.py"
LAUNCHER = "host/run-mkosi-build.py"
DKMS = "scripts/mkosi_dkms.py"
RUNTIME_POLICY = "scripts/mkosi_runtime_policy.py"
APPRAISAL = "scripts/mkosi_appraisal_probe.py"
VERITY = "scripts/mkosi_verity.py"
BOOT = "scripts/mkosi_boot.py"
BOOT_PREP = "scripts/prepare_mkosi_boot.py"
EXPORT = "scripts/mkosi_export.py"
PUBLICATION = "scripts/pilot/export.py"
HOOKS = "scripts/review_guest_hooks.py"
RUNTIME_APT = "evidence/p3/2026-10-22-runtime-apt-plan/report.json"
probe = types.SimpleNamespace(**runpy.run_path(str(ROOT / PROBE), run_name="build_probe"))
resources = types.SimpleNamespace(**runpy.run_path(str(ROOT / RESOURCES), run_name="build_limits"))
prep, base, tools, require = probe.prep, probe.base, probe.tools, probe.require
mkosi = prep.mkosi
hooks = types.SimpleNamespace(**runpy.run_path(str(ROOT / HOOKS)))


def preflight():
    probe.native.preflight()
    membership = Path("/proc/self/cgroup").read_text()
    match = re.fullmatch(r"0::/(nib-build-[0-9a-f]{32})\n", membership)
    require(match is not None)
    group = Path("/sys/fs/cgroup") / match[1]
    require(group.stat().st_uid == 0 and not group.stat().st_mode & 0o022)
    require({name: (group / name).read_text().strip() for name in resources.LIMITS} == resources.LIMITS)


def command(root, payload, output, mapped, compile_driver=False, install_driver=False, verity_root=False,
            boot_uki=False, stage_boot=False):
    require(not install_driver or compile_driver)
    require(type(verity_root) is bool and (not verity_root or install_driver))
    require(type(boot_uki) is bool and (not boot_uki or verity_root))
    require(type(stage_boot) is bool and (not stage_boot or boot_uki))
    before = [os.readlink("/proc/self/ns/" + name) for name in ("user", "mnt", "net", "ipc", "pid")]
    return [*probe.ownership.prefix(root, mapped), probe.native.PYTHON, "-I", "-S", "-B",
        str(payload / "mkosi-sandbox.py"), "--ro-bind", str(root), "/",
        "--ro-bind", str(payload), "/nib", "--ro-bind", "/proc", "/proc",
        "--bind", str(output), "/out", "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", "/work",
        "--unshare-net", "--unshare-ipc", "--become-root",
        "/usr/bin/unshare", "--pid", "--fork", "--mount-proc=/proc", "--kill-child=KILL", "--",
        "/usr/bin/python3.12", "-I", "-S", "-B", "/nib/build-worker.py", *before,
        *(["--install-driver"] if install_driver else ["--compile-driver"] if compile_driver else []),
        *(["--verity-root"] if verity_root else []), *(["--boot-uki"] if boot_uki else []),
        *(["--stage-boot"] if stage_boot else [])]


def export_helpers(sources):
    result = []
    for name in (EXPORT, PUBLICATION):
        helper = types.ModuleType("bound_boot_export")
        helper.__file__ = str(ROOT / name)
        exec(compile(sources[name], helper.__file__, "exec", dont_inherit=True), helper.__dict__)
        result.append(helper)
    return result


def source_identity(revision, sources):
    return {"git_sha": revision, "sources": {name: base.digest(raw) for name, raw in sources.items()}}


def runtime_preflight(sources, archives):
    lock_raw = sources[hooks.RUNTIME_LOCK]
    rehearsal = json.loads(sources[RUNTIME_APT])
    simulation = rehearsal["simulation"]
    plan = simulation["plan"]
    require(rehearsal["schema"] == "nib.apt-container-run/v2"
            and rehearsal["result"] == "pass" and rehearsal["cleanup"] == "confirmed"
            and rehearsal["target_profile"] == "guest-runtime"
            and simulation["schema"] == "nib.apt-simulation/v2"
            and simulation["result"] == "pass" and simulation["stage"] == "complete"
            and simulation["diagnostic"] is None
            and simulation["guest_lock_sha256"] == base.digest(lock_raw)
            and simulation["target_packages"] == plan["installed"] == plan["configured"] == 212
            and plan["removed"] == 0
            and type(simulation["target_packages"]) is int
            and all(type(plan[k]) is int for k in ("installed", "configured", "removed")))
    rows = hooks.inventory(json.loads(lock_raw), archives)
    return hooks.validate(json.loads(sources[hooks.RUNTIME_POLICY]), lock_raw, rows)


def prepare(downloads, source_archive, revision, sources, guest_runtime=False, verity_root=False,
            boot_uki=False, stage_boot=False):
    require(type(guest_runtime) is bool)
    require(type(verity_root) is bool and (not verity_root or guest_runtime))
    require(type(boot_uki) is bool and (not boot_uki or verity_root))
    require(type(stage_boot) is bool and (not stage_boot or boot_uki))
    review = None
    if guest_runtime:
        review = runtime_preflight(sources, downloads / "archives")
    raw = base.read(source_archive, mkosi.MAX_SOURCE)
    prepared, preparation = mkosi.prepare(downloads / "metadata", downloads / "archives", raw, revision, sources,
                                           guest_runtime=guest_runtime)
    if review is not None:
        prepared["runtime-hook-review.json"] = base.encoded(review)
    pin = json.loads(sources[mkosi.EXTRA[1]])
    _, upstream = mkosi.verify_source(raw, pin)
    upstream[mkosi.PATCH_TARGET] = prepared["mkosi-overlay/" + mkosi.PATCH_TARGET]
    payload = {"prepared/" + name: data for name, data in prepared.items()}
    payload.update({"upstream/" + name: data for name, data in upstream.items()
                    if pin["source_files"][name]["mode"] != "120000"})
    # Only regular upstream source is needed. Preserve links as links later;
    # never accidentally execute a link target serialized as file contents.
    links = {"upstream/" + name: data.decode("utf-8") for name, data in upstream.items()
             if pin["source_files"][name]["mode"] == "120000"}
    payload.update({"mkosi-sandbox.py": upstream["mkosi/sandbox.py"],
        "build-worker.py": sources[WORKER], "mkosi-entry.py": sources[ENTRY],
        "snapshot.py": sources[tools.FILES[4]], "package-inputs.py": sources[tools.FILES[3]],
        "namespace-check.py": sources[probe.native.CHECKER], "pid-check.py": sources[probe.PID_CHECKER],
        "ownership.py": sources[probe.OWNERSHIP],
        "package-worker.py": prepared["nib/package-worker.py"],
        "confinement.py": prepared["nib/confinement.py"]})
    payload["dkms-worker.py"] = sources[DKMS]
    if guest_runtime:
        payload["runtime-policy.py"] = sources[RUNTIME_POLICY]
        payload["appraisal-probe.py"] = sources[APPRAISAL]
    if verity_root:
        payload["verity-root.py"] = sources[VERITY]
    if boot_uki:
        helper = types.ModuleType("boot_preparation")
        exec(compile(sources[BOOT_PREP], BOOT_PREP, "exec", dont_inherit=True), helper.__dict__)
        payload["boot-uki.py"] = sources[BOOT]
        payload["boot-inputs.json"] = helper.prepare(
            downloads / "archives", sources[hooks.RUNTIME_LOCK], tools)
    if stage_boot:
        payload["boot-export.py"] = sources[EXPORT]
        payload["artifact-inputs.json"] = base.encoded(source_identity(revision, sources))
    modes = {"upstream/" + name: 0o555 if row["mode"] == "100755" else 0o444
             for name, row in pin["source_files"].items() if row["mode"] != "120000"}
    return prepared, preparation, payload, modes, links


def collect_diagnostics(resultdir, verity_root=False, boot_uki=False):
    artifacts = {}
    for name in ("installer.stdout", "installer.stderr", "dkms-compiler.stdout", "dkms-compiler.stderr",
                 "dkms-add.stdout", "dkms-add.stderr", "dkms-build.stdout", "dkms-build.stderr",
                 "dkms-install.stdout", "dkms-install.stderr",
                 "dkms-symbols.stdout", "dkms-symbols.stderr", "dkms-make-0.log", "dkms-make-1.log"):
        if (resultdir / name).exists():
            artifacts[name] = base.read(resultdir / name, 2 << 20)
    for operation in ("help", "attest-help", "token-help"):
        for stream in ("stdout", "stderr"):
            name = "appraisal-cli-" + operation + "." + stream
            if (resultdir / name).exists():
                artifacts[name] = base.read(resultdir / name, 65536)
    if verity_root:
        for operation in ("mkfs", "repart", "fsck", "verify", "reject-root", "verify-final"):
            for stream in ("stdout", "stderr"):
                name = "verity-" + operation + "." + stream
                if (resultdir / name).exists():
                    artifacts[name] = base.read(resultdir / name, 65536)
    if boot_uki:
        for operation in ("depmod", "cpio", "ukify", "inspect"):
            for stream in ("stdout", "stderr"):
                name = "boot-" + operation + "." + stream
                if (resultdir / name).exists():
                    artifacts[name] = base.read(resultdir / name, 65536)
    return artifacts


def run(downloads, archive, output, revision, sources, compile_driver=False, install_driver=False,
        guest_runtime=False, verity_root=False, boot_uki=False, stage_boot=False):
    require(type(stage_boot) is bool and (not stage_boot or (boot_uki and verity_root and guest_runtime
                                                          and install_driver and compile_driver)))
    if stage_boot:
        export, publication = export_helpers(sources)
        with export.Pending(output, base, publication) as pending:
            return run_build(downloads, archive, output, revision, sources, compile_driver, install_driver,
                             guest_runtime, verity_root, boot_uki, pending)
    return run_build(downloads, archive, output, revision, sources, compile_driver, install_driver,
                     guest_runtime, verity_root, boot_uki)


def run_build(downloads, archive, output, revision, sources, compile_driver=False, install_driver=False,
              guest_runtime=False, verity_root=False, boot_uki=False, pending=None):
    require(type(guest_runtime) is bool)
    require(not install_driver or compile_driver)
    require(type(verity_root) is bool and (not verity_root or (guest_runtime and install_driver)))
    require(type(boot_uki) is bool and (not boot_uki or verity_root))
    require(not output.exists() and not output.is_symlink())
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    report = {"schema": "nib.mkosi-installation/v1", "git_sha": revision, "result": "fail",
              "stage": "preparation", "worker": None, "payload_removed": False,
              "process_cleanup_confirmed": False, "failure_kind": None,
              "bootable": False, "deployable": False}
    artifacts = {}
    with tempfile.TemporaryDirectory(prefix="nib-image-build-", dir="/tmp") as temporary:
        temporary = Path(temporary).resolve()
        try:
            prepared, preparation, payload_files, modes, links = prepare(
                downloads, archive, revision, sources, guest_runtime, verity_root, boot_uki,
                **({"stage_boot": True} if pending is not None else {}))
            if guest_runtime:
                artifacts["runtime-hook-review.json"] = prepared["runtime-hook-review.json"]
            inputs = temporary / "prepared"
            base.publish(inputs, prepared)
            files, tree_receipt = prep.assemble(inputs, revision, sources)
            mapped, helpers = probe.ownership.host_inputs()
            report["mapping_helper_hashes"] = helpers
            payload_files["ownership-input.json"] = base.encoded(mapped)
            root, payload, resultdir = temporary / "root", temporary / "payload", temporary / "result"
            report["stage"] = "tools-materialization"
            probe.materialize(files, root)
            for name in ("work", "out"):
                require(not (root / name).exists() and not (root / name).is_symlink())
                (root / name).mkdir(mode=0o755)
            report["stage"] = "payload-publication"
            base.publish(payload, payload_files)
            report["stage"] = "upstream-modes-and-links"
            for name, mode in modes.items():
                (payload / name).chmod(mode)
            for name, target in links.items():
                require(tools.link_target(name, target).startswith("upstream/"))
                (payload / name).parent.mkdir(parents=True, exist_ok=True)
                os.symlink(target, payload / name)
            resultdir.mkdir(mode=0o700)
            report.update(stage="installation", prepared_receipt_sha256=base.digest(prepared["receipt.json"]),
                          tools_receipt_sha256=base.digest(files["receipt.json"]))
            result = base.processes.execute_process_group(command(
                root, payload, resultdir, mapped, compile_driver, install_driver, verity_root, boot_uki,
                **({"stage_boot": True} if pending is not None else {})),
                env={**probe.native.worker.ENV, "RUNNER_ENVIRONMENT": "github-hosted"},
                timeout=1900, max_output_bytes=2 << 20, stdin=subprocess.DEVNULL)
            report["process_cleanup_confirmed"] = True
            artifacts["launcher.stdout"] = result.stdout
            artifacts["launcher.stderr"] = result.stderr
            if (resultdir / "result.json").exists():
                worker = json.loads(base.read(resultdir / "result.json", 65536))
                require(worker["schema"] == "nib.mkosi-install-worker/v1"
                        and worker["bootable"] is False and worker["deployable"] is False)
                report["worker"] = worker
            artifacts.update(collect_diagnostics(resultdir, verity_root, boot_uki))
            result.check_returncode()
            require(report["worker"] and report["worker"]["result"] == "pass"
                    and report["worker"]["configured_state_verified"] is True
                    and report["worker"]["installed_packages"] == (212 if guest_runtime else 145))
            if compile_driver:
                require(report["worker"]["dkms_built"] is True
                        and report["worker"]["driver_build"]["result"] == "pass")
                require(report["worker"]["driver_build"]["installed"] is install_driver)
            if verity_root:
                verity = report["worker"]["verity_root"]
                require(verity["schema"] == "nib.private-verity-root/v1" and verity["result"] == "pass"
                        and all(verity[k] is True for k in ("erofs_checked", "userspace_verity_verified",
                                                          "wrong_root_rejected", "private_images_removed"))
                        and all(verity[k] is False for k in ("images_exported", "boot_verified", "kernel_verity_verified",
                                                           "reproducibility_verified", "confidentiality_verified")))
            if boot_uki:
                boot = report["worker"]["boot_uki"]
                require(boot["schema"] == "nib.private-boot-uki/v1" and boot["result"] == "pass"
                        and boot["root_hash"] == verity["root_hash"]
                        and verity["boot_partuuids_checked"] is True
                        and all(boot[k] is True for k in ("archive_checked", "sections_checked", "private_images_removed"))
                        and all(boot[k] is False for k in ("signed", "images_exported", "boot_verified",
                                                         "reproducibility_verified", "confidentiality_verified")))
            report.update(result="pass", stage="complete")
            if pending is not None:
                staged = report["worker"]["artifact_stage"]
                require(staged["source"] == source_identity(revision, sources))
                pending.capture(resultdir / "boot-stage", staged)
        except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, subprocess.SubprocessError) as exc:
            report["result"] = "fail"
            report["failure_kind"] = probe.failure_kind(exc)
    report["payload_removed"] = not temporary.exists()
    if pending is not None and report["result"] == "pass":
        try:
            pending.validate(report)
        except (ValueError, KeyError, TypeError) as exc:
            report["result"] = "fail"
            report["failure_kind"] = probe.failure_kind(exc)
    ended = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    evidence = probe.native.evidence(report, revision, started, ended)
    evidence.update(command="host/run-mkosi-build.py --downloads PRIVATE --archive PRIVATE --output PRIVATE",
        notes="One bounded offline installation; no boot, UKI, verity, DKMS, reproducibility or hardware qualification.")
    if compile_driver:
        evidence["command"] += " --compile-driver"
        evidence["notes"] = "Bounded offline installation and explicit DKMS build; no module loading, boot, LKCA, reproducibility or hardware qualification."
    if install_driver:
        evidence["command"] += " --install-driver"
        evidence["notes"] = "Bounded offline driver build and guest-root installation; no module loading, boot, LKCA, reproducibility or hardware qualification."
    if guest_runtime:
        evidence["command"] += " --guest-runtime"
    if verity_root:
        evidence["command"] += " --verity-root"
        evidence["notes"] = "Bounded private EROFS/verity generation and userspace verification; images removed, no export, UKI, boot, LKCA, reproducibility or confidentiality proof."
    if boot_uki:
        evidence["command"] += " --boot-uki"
        evidence["notes"] = "Private initrd and unsigned UKI assembly/inspection after userspace verity verification; images removed. No export, boot, signing, LKCA, reproducibility or confidentiality proof."
    if pending is not None:
        evidence["command"] += " --export-boot"
        evidence["notes"] = "Verified GPT and unsigned UKI staged privately; final publication requires independent resource cleanup. No boot, signing, LKCA, reproducibility or confidentiality proof."
    artifacts.update({"run.json": base.encoded(evidence), "result.json": base.encoded(report)})
    base.publish(output, artifacts)
    if pending is not None and report["result"] == "pass":
        pending.accept(report, output)
    return 0 if report["result"] == "pass" else 1


def committed_sources(guest_runtime=False, verity_root=False, boot_uki=False, stage_boot=False):
    return tools.committed((*mkosi.EXTRA, prep.SCRIPT, prep.POLICY,
        SCRIPT, PROBE, WORKER, ENTRY, RESOURCES, LAUNCHER, probe.NATIVE, probe.OWNERSHIP,
        probe.PID_CHECKER, probe.native.CHECKER, probe.native.WORKER, DKMS,
        *((HOOKS, hooks.RUNTIME_LOCK, hooks.RUNTIME_POLICY, RUNTIME_APT, RUNTIME_POLICY, APPRAISAL)
          if guest_runtime else ()), *((VERITY,) if verity_root else ()),
        *((BOOT, BOOT_PREP) if boot_uki else ()), *((EXPORT, PUBLICATION) if stage_boot else ())))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("downloads", "archive", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--compile-driver", action="store_true")
    parser.add_argument("--install-driver", action="store_true")
    parser.add_argument("--guest-runtime", action="store_true")
    parser.add_argument("--verity-root", action="store_true")
    parser.add_argument("--boot-uki", action="store_true")
    parser.add_argument("--stage-boot", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    require(not args.install_driver or args.compile_driver)
    require(not args.verity_root or (args.guest_runtime and args.install_driver))
    require(not args.boot_uki or args.verity_root)
    require(not args.stage_boot or args.boot_uki)
    preflight()
    tools.separate_output(args.output, (args.downloads, args.archive))
    revision, sources = committed_sources(args.guest_runtime, args.verity_root, args.boot_uki, args.stage_boot)
    return run(args.downloads, args.archive, args.output, revision, sources, args.compile_driver,
               args.install_driver, args.guest_runtime, verity_root=args.verity_root, boot_uki=args.boot_uki,
               **({"stage_boot": True} if args.stage_boot else {}))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        sys.exit("Native image installation rejected; no qualification.")
