# SPDX-License-Identifier: Apache-2.0
"""Local Linux Qwen proof: build public inputs, then execute with no network."""

import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import types
import uuid

ROOT = Path(__file__).resolve().parents[2]


def source_module(name, relative):
    path = ROOT / relative
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    return module


pilot = source_module("linux_pilot", "scripts/pilot/run.py")
e2e = source_module("linux_e2e", "scripts/e2e/run.py")
snapshot = pilot.source_module("snapshot")


def build_context(inputs, context):
    pins = json.loads((ROOT / "scripts/pilot/pins.json").read_text())
    bases = e2e.load_pins()
    source_files = ("scripts/pilot/run.py", "scripts/pilot/pins.json",
                    "scripts/pilot/build-requirements.lock", "scripts/e2e/pins.json",
                    "scripts/e2e/snapshot.py", "scripts/e2e/exercise.py",
                    "scripts/e2e/requirements.lock")
    for relative in source_files:
        path = ROOT / relative
        pilot.require(path.is_file() and not path.is_symlink(), "context-source")
        target = context / "repo" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    shutil.copyfile(ROOT / "scripts/pilot/Dockerfile", context / "Dockerfile")
    private = context / "inputs"
    private.mkdir()
    for filename, digest, limit in (
        ("model.gguf", pins["model"]["sha256"], pins["model"]["bytes"]),
        ("llama.tar.gz", pins["engine"]["archive_sha256"], 64 << 20),
        ("trcs.tar.gz", bases["trcs"]["archive_sha256"], 16 << 20),
    ):
        pilot.checked_copy(inputs / filename, private / filename, digest, limit)
    for archive, target in (("llama.tar.gz", "engine"), ("trcs.tar.gz", "trcs")):
        unpacked = context / (target + "-archive")
        root = pilot.unpack(private / archive, unpacked)
        root.rename(context / target)
        unpacked.rmdir()
        (private / archive).unlink()
    # Hash exactly the private context consumed by Docker, not a live checkout.
    digest = hashlib.sha256()
    for path in sorted(context.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(context).as_posix().encode() + b"\0")
            digest.update(bytes.fromhex(pilot.file_digest(path)))
    return {"context_sha256": digest.hexdigest(), "base_images": bases, "model": pins}


def runtime_command(image, name):
    # No host filesystem, credentials, ports or external network enter the pilot.
    return ["docker", "run", "--name", name, "--rm", "--init", "--pull", "never",
            "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--user", "65532:65532",
            "--pids-limit", "128", "--memory", "3g", "--cpus", "4",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=128m,mode=1777",
            "--platform", "linux/amd64", image]


def run(inputs, summary, env, home):
    name = "nib-qwen-" + uuid.uuid4().hex[:12]
    image = "nib-qwen-local:" + name.removeprefix("nib-qwen-")
    summary["container"] = {"platform": "linux/amd64", "network": "none",
                            "read_only": True, "published": False}
    build_started, build_finished = False, False
    runtime_started, runtime_finished = False, False
    try:
        env = {**env, "DOCKER_HOST": snapshot.local_docker_endpoint(env, home)}
        snapshot.require_buildkit(env)
        with tempfile.TemporaryDirectory(prefix="nib-qwen-container-") as temporary:
            work = Path(temporary)
            context = work / "context"
            context.mkdir()
            summary["stage"] = "prepare-container"
            summary["container_inputs"] = build_context(inputs, context)
            bases = summary["container_inputs"]["base_images"]
            summary["stage"] = "build-container"
            iid = work / "image-id"
            build_started = True
            built = snapshot.execute_process_group(["docker", "build", "--platform", bases["platform"],
                            "--tag", image, "--iidfile", str(iid),
                            "--build-arg", "GO_IMAGE=" + bases["go_image"],
                            "--build-arg", "PYTHON_IMAGE=" + bases["python_image"], str(context)],
                           env=env, capture_output=False, timeout=1200)
            built.check_returncode()
            identity = iid.read_text().strip()
            pilot.require(re.fullmatch(r"sha256:[0-9a-f]{64}", identity), "container-identity")
            build_finished = True
            summary["container"]["image_id"] = identity
            summary["stage"] = "execute-container"
            runtime_started = True
            result = snapshot.execute_process_group(runtime_command(identity, name), env=env, timeout=600)
            pilot.require(len(result.stdout) <= 65536, "container-report-size")
            report = json.loads(result.stdout)
            pilot.require(isinstance(report, dict) and report.get("execution") == "real-cpu-model"
                          and report.get("confidential") is False
                          and report.get("hardware_validated") is False
                          and report.get("result") in {"pass", "fail"}
                          and result.returncode in {0, 1}
                          and (report["result"] == "pass") == (result.returncode == 0), "container-report")
            pilot.require(report.get("runtime_platform") == {"system": "Linux", "machine": "x86_64"},
                          "container-platform")
            runtime_finished = True
            # Host-side input and isolation evidence cannot be replaced by child output.
            summary["runtime"] = report
            summary["result"] = report["result"]
            summary["stage"] = "complete" if report["result"] == "pass" else "container-runtime-failed"
    except pilot.PilotError as error:
        summary.update(result="fail", failure_code=str(error))
    except (OSError, ValueError, TypeError, KeyError, pilot.tarfile.TarError, subprocess.SubprocessError):
        summary.update(result="fail", failure_code="container-execution-failed")
    finally:
        summary["container_cleanup"] = e2e.cleanup_resources(image, name, env=env)
        if build_started and not build_finished:
            # Client death does not prove server-side BuildKit cancellation.
            # A later image could appear: fail closed even if current lists are empty.
            summary["container_cleanup"].update(result="fail", build_completion="unknown")
        if runtime_started and not runtime_finished:
            summary["container_cleanup"].update(result="fail", runtime_completion="unknown")
        if summary["container_cleanup"]["result"] != "pass":
            summary.update(result="fail", failure_code="container-cleanup-unresolved")
