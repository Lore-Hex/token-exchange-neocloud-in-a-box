#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prepare or explicitly record one bounded, non-qualifying application/import smoke."""

import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site):
    sys.exit("Use python3 -I -S scripts/record-engine-import-smoke.py")

import hashlib
import os
from pathlib import Path
import pwd
import re
import signal
import stat
import subprocess
import tempfile
import types
import uuid

ROOT = Path(__file__).absolute().parent.parent
SCRIPT = "scripts/record-engine-import-smoke.py"
BUILDER = "scripts/prepare-engine-application.py"
BOOTSTRAP_FILE = "scripts/engine_python_bootstrap.py"
SMOKE = "scripts/engine_import_smoke.py"
TESTS = ("scripts/test_engine_python_bootstrap.py", "scripts/test_engine_import_smoke.py", "scripts/test_engine_callsite.py")
BUILDER_SHA = "09a9b2b15e269285422a4e8aae1ddb1b1cb311415c07c146cdfcc6a5094a3792"
BOOTSTRAP_SHA = "4287c42d3d34f42a2e89901600c11c2fe20669f3bb6b5b120f2671b992a5df92"
SMOKE_SHA = "57b7fa12bc7cb972ca0f17a251aa424d9f164e3b43082b38fe6b9d239c010307"


def read_source(path):
    parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        path = Path(path)
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("smoke source rejected")
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 2 << 20:
                raise ValueError("smoke source rejected")
            data = stream.read((2 << 20) + 1)
            def identity(info):
                return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            if (len(data) != before.st_size or identity(before) != identity(os.fstat(stream.fileno()))
                    or identity(before) != identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False))):
                raise ValueError("smoke source changed")
            return data
    finally:
        os.close(parent)


def load(name, path, expected):
    data = read_source(path)
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("smoke source differs")
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(data, str(path), "exec"), module.__dict__)
    return module, data


try:
    BOOTSTRAP = read_source(Path(__file__).absolute())
    builder, BUILDER_SOURCE = load("smoke_application_preparer", ROOT / BUILDER, BUILDER_SHA)
    bootstrap, BOOTSTRAP_SOURCE = load("smoke_spawn_bootstrap", ROOT / BOOTSTRAP_FILE, BOOTSTRAP_SHA)
    smoke, SMOKE_SOURCE = load("smoke_payload", ROOT / SMOKE, SMOKE_SHA)
except Exception:
    if __name__ == "__main__":
        sys.exit("Smoke preparation bootstrap rejected.")
    raise

overlay, checker, recorder = builder.overlay, builder.checker, builder.recorder


def inputs(root=ROOT):
    revision, bundle, extra = builder.inputs(root)
    for name in (SCRIPT, BOOTSTRAP_FILE, SMOKE, *TESTS):
        entry = checker.git(root, "ls-tree", revision, "--", name).decode().split()
        overlay.require(len(entry) == 4 and entry[:2] == ["100644", "blob"] and entry[3] == name)
        overlay.require(int(checker.git(root, "cat-file", "-s", entry[2])) <= checker.LIMIT)
        extra[name] = checker.git(root, "cat-file", "blob", entry[2])
    overlay.require(extra[SCRIPT] == BOOTSTRAP and extra[BUILDER] == BUILDER_SOURCE
                    and extra[BOOTSTRAP_FILE] == BOOTSTRAP_SOURCE and extra[SMOKE] == SMOKE_SOURCE)
    return revision, bundle, extra


def environment():
    return {**recorder.prep.launch_plan()["fixed_engine_environment"], **smoke.IMPORT_ENVIRONMENT,
            "VLLM_WORKER_MULTIPROC_METHOD": "spawn",
            "PATH": "/usr/bin:/bin", "HOME": "/tmp", "TMPDIR": "/tmp", "LC_ALL": "C.UTF-8"}


def prepare_files(revision, bundle, extra):
    files, _ = builder.prepare_files(revision, bundle, extra)
    application = overlay.load_json(files["payload/contract.json"])
    measured = builder.application.measurement(overlay, extra[builder.application.EVIDENCE])
    payload = {"application/" + name.removeprefix("payload/"): data for name, data in files.items() if name.startswith("payload/")}
    prefix = b'import sys\nif not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):\n    sys.exit("Isolated smoke required.")\n'
    payload["launch.py"] = prefix + extra[BOOTSTRAP_FILE] + b"\n" + extra[SMOKE]
    payload["processes.py"] = extra[recorder.SNAPSHOT]
    contract = {"schema": "nib.engine-import-smoke-input/v1", "status": "BLOCKED", "deployable": False, "image": None,
                "git_sha": revision, "base_image": overlay.BASE_IMAGE, "official_config": application["official_config"],
                "source_bundle_sha256": checker.bundle_digest({**bundle, **extra}),
                "candidate_bundle_sha256": application["candidate_bundle_sha256"],
                "application_contract_sha256": overlay.digest(payload["application/contract.json"]),
                "measurement_sha256": builder.application.EVIDENCE_SHA,
                "python": measured["runtime"]["python"], "paths": measured["proposed_paths"],
                "interpreter": {key: measured["files"][0][key] for key in ("resolved", "bytes", "sha256")},
                "modules": list(smoke.MODULES), "environment": environment(),
                "import_environment_sources": list(smoke.ENVIRONMENT_SOURCES),
                "process_callsite_sources": smoke.callsite_specs(),
                "metadata_source_sha256": dict(bootstrap.METADATA_SOURCES),
                "permitted_dependency_entrypoint": list(bootstrap.CONTEXT_ENTRYPOINT),
                "sources": {name: overlay.digest(data) for name, data in payload.items()}}
    payload["smoke.json"] = overlay.json_bytes(contract)
    payload["python"] = smoke.wrapper_bytes(contract["interpreter"]["resolved"], environment(), overlay.digest(payload["smoke.json"]))
    overlay.require(len(payload) <= 100 and sum(map(len, payload.values())) <= overlay.MAX_CONTEXT)
    return payload, contract


def create_command(name, owner, payload, sha, config):
    # Only the ephemeral container layer is writable. The cached base and
    # host payload stay unchanged; no image build, commit or publication.
    args = ["docker", "container", "create", "--name", name, "--label", "nib.import-smoke=" + owner,
        "--pull", "never", "--platform", "linux/amd64", "--runtime", "runc", "--network", "none",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "0:0",
        "--pids-limit", "128", "--memory", "8g", "--memory-swap", "8g", "--cpus", "2",
        "--ipc", "private", "--shm-size", "16m", "--cgroupns", "private", "--no-healthcheck",
        "--restart", "no", "--log-driver", "none", "--workdir", "/",
        "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=512m,mode=1777",
        "--entrypoint", "/usr/bin/env", "--mount", "type=bind,src=" + str(payload) + ",dst=/opt/nib,readonly"]
    names = set()
    for entry in config["config"].get("Env", []):
        name, separator, _ = entry.partition("=")
        overlay.require(separator and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name) and name not in names)
        names.add(name)
    for name in sorted(names | {"PATH", "HOME"}):
        args += ["--env", name + "="]
    return args + [overlay.BASE_IMAGE, "-i", *[key + "=" + value for key, value in sorted(environment().items())],
        "/usr/bin/python3.12", "-I", "-S", "-B", "/opt/nib/launch.py", sha, "apply"]


def check_container(info, owner, payload, sha, image_id, config_input):
    config, host = info["Config"], info["HostConfig"]
    command = create_command("unused", owner, payload, sha, config_input)
    overlay.require(config["Labels"].get("nib.import-smoke") == owner and info["Image"] == image_id
        and config["Image"] == overlay.BASE_IMAGE and config["Entrypoint"] == ["/usr/bin/env"]
        and config["Cmd"] == command[command.index(overlay.BASE_IMAGE) + 1:]
        and config["User"] == "0:0" and config["WorkingDir"] == "/"
        and config["Healthcheck"]["Test"] == ["NONE"] and not config.get("Tty") and not config.get("OpenStdin"))
    expected_env = [command[n + 1] for n, arg in enumerate(command) if arg == "--env"]
    overlay.require(len(config["Env"]) == len(expected_env) and set(config["Env"]) == set(expected_env))
    overlay.require(host["NetworkMode"] == "none" and host["ReadonlyRootfs"] is False
        and host["Privileged"] is False and host["Runtime"] == "runc" and host["CapDrop"] == ["ALL"]
        and not host.get("CapAdd") and not host.get("Devices") and not host.get("DeviceRequests")
        and not host.get("DeviceCgroupRules") and not host.get("PortBindings") and not host.get("PublishAllPorts")
        and not host.get("PidMode") and not host.get("UTSMode") and not host.get("VolumesFrom") and not host.get("Binds")
        and host["IpcMode"] == "private" and host["ShmSize"] == 16 << 20 and host["CgroupnsMode"] == "private"
        and host["Memory"] == host["MemorySwap"] == 8 << 30 and host["NanoCpus"] == 2000000000 and host["PidsLimit"] == 128
        and host["RestartPolicy"]["Name"] == "no" and host["LogConfig"]["Type"] == "none"
        and set(host["SecurityOpt"]) in ({"no-new-privileges"}, {"no-new-privileges=true"})
        and host["Tmpfs"] == {"/tmp": "rw,nosuid,nodev,noexec,size=512m,mode=1777"})
    mounts = info["Mounts"]
    overlay.require(len(mounts) == 1 and mounts[0]["Type"] == "bind" and mounts[0]["Source"] == str(payload)
                    and mounts[0]["Destination"] == "/opt/nib" and mounts[0]["RW"] is False)


def run_container(payload, contract, config, env, execute=recorder.snapshot.execute_process_group):
    name, owner = "nib-engine-import-" + uuid.uuid4().hex, uuid.uuid4().hex
    report = {"result": "fail", "stage": "image-preflight", "cleanup": "not-created", "smoke_pass": False,
              "candidate_status": "BLOCKED", "deployable": False, "abi_validated": False, "hardware_validated": False}
    create_started = create_complete = runtime_started = runtime_complete = False
    absent = ["docker", "container", "ls", "--all", "--quiet", "--filter", "name=^/" + name + "$"]
    sha = overlay.digest(read_source(payload / "smoke.json"))
    try:
        value = recorder.docker_json("image", overlay.BASE_IMAGE, env, execute)
        report["image"] = recorder.image_binding(value, config, contract)
        overlay.require(not recorder.call(absent, env, execute=execute).strip())
        report["stage"] = "container-create"
        create_started = True
        created = recorder.call(create_command(name, owner, payload, sha, config), env, execute=execute).decode().strip()
        overlay.require(re.fullmatch(r"[0-9a-f]{64}", created))
        create_complete = True
        report["stage"] = "isolation-check"
        info = recorder.docker_json("container", name, env, execute)
        check_container(info, owner, payload, sha, value["Id"], config)
        overlay.require(info["Id"] == created and info["State"]["Status"] == "created")
        report["stage"] = "application-and-import"
        runtime_started = True
        raw = recorder.call(["docker", "container", "start", "--attach", name], env, execute=execute,
                            timeout=smoke.ATTACH_SECONDS, limit=65536)
        result = overlay.load_json(raw)
        application = overlay.load_json(read_source(payload / "application/contract.json"))
        measured = builder.application.measurement(overlay, read_source(payload / "application/measurement.json"))
        smoke_pass = smoke.validate(overlay, result, application, measured)
        terminal = recorder.docker_json("container", name, env, execute)
        overlay.require(terminal["Id"] == created and terminal["State"]["Status"] == "exited"
                        and terminal["State"]["ExitCode"] == 0 and not terminal["State"].get("OOMKilled"))
        runtime_complete = True
        report.update(experiment=result, smoke_pass=smoke_pass,
                      stage="smoke-complete" if smoke_pass else "smoke-rejected")
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError):
        report["failure"] = "bounded-experiment-stage-failed"
    finally:
        if create_started:
            report["cleanup"] = "unknown"
            try:
                if recorder.call(absent, env, execute=execute).strip():
                    owned = recorder.docker_json("container", name, env, execute)
                    overlay.require(owned["Config"]["Labels"].get("nib.import-smoke") == owner)
                    recorder.call(["docker", "container", "rm", "--force", name], env, execute=execute)
                if not recorder.call(absent, env, execute=execute).strip():
                    report["cleanup"] = "confirmed"
            except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError):
                pass
            if not create_complete or (runtime_started and not runtime_complete):
                report["cleanup"] = "completion-unknown"
            if report["cleanup"] != "confirmed":
                report.update(smoke_pass=False, failure="cleanup-unresolved")
    return report


def evidence_record(revision, slug, started, report):
    return {"schema": "nib.evidence/v1", "phase": "P0", "task": "T4", "git_sha": revision,
        "command": "python3 -I -S scripts/record-engine-import-smoke.py --slug " + slug,
        "started_utc": started, "ended_utc": recorder.now(), "result": "fail", "execution": "software",
        "hardware_validated": False, "host": {"provider": "local", "machine": "local-software-experiment",
            "gpus": "none", "driver": None, "cuda": None},
        "notes": "Non-qualifying application/import experiment; candidate stays BLOCKED even if smoke_pass is true. "
            "Writable owned container layer, read-only payload, no network/GPU/ports/model/inference/build/pull/publication. "
            "Imports execute pinned engine/dependency code; not an adversarial Python sandbox or ABI/confidential proof. "
            "Docker/OS/interpreter trusted. Payload SHA-256: " + report["provenance"]["payload_sha256"]}


def main():
    signal.signal(signal.SIGTERM, recorder.cancel)
    signal.signal(signal.SIGINT, recorder.cancel)
    parser = overlay.QuietParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--prepare", metavar="PRIVATE_OUTPUT", help="Prepare only; no Docker or engine execution.")
    action.add_argument("--slug", help="Explicitly execute the bounded local experiment and retain failed qualification evidence.")
    args = parser.parse_args()
    revision, bundle, extra = inputs()
    payload, contract = prepare_files(revision, bundle, extra)
    if args.prepare:
        recorder.prep.publish(args.prepare, payload)
        print(overlay.json_bytes({"git_sha": revision, "status": "BLOCKED", "deployable": False,
            "payload_sha256": overlay.bundle_digest(payload), "source_bundle_sha256": contract["source_bundle_sha256"],
            "image_executed": False, "engine_imported": False}).decode(), end="")
        return 0
    overlay.require(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", args.slug) and len(args.slug) <= 64)
    started = recorder.now()
    destination = ROOT / "evidence/p0" / (started[:10] + "-" + args.slug)
    overlay.require(not os.path.lexists(destination))
    with tempfile.TemporaryDirectory(prefix="nib-import-smoke-", dir="/private/tmp" if sys.platform == "darwin" else None) as temporary:
        work = Path(temporary).resolve()
        env = recorder.offline_environment(work / "control")
        nib, validator_sha = recorder.build_validator(ROOT, revision, work, env)
        report = {"result": "fail", "stage": "raw-oci-metadata", "cleanup": "not-created", "smoke_pass": False,
                  "candidate_status": "BLOCKED", "deployable": False, "abi_validated": False, "hardware_validated": False}
        raw_verified = False
        try:
            config = recorder.raw_metadata(ROOT / "build/engine-image-metadata", contract)
            raw_verified = True
            report["stage"] = "local-docker-endpoint"
            home = Path(pwd.getpwuid(os.getuid()).pw_dir)
            discovery = {**env, "HOME": str(home), "DOCKER_CONFIG": str(home / ".docker")}
            endpoint = recorder.call(["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
                                     discovery, timeout=10, limit=4096).decode().strip()
            overlay.require(endpoint.startswith("unix:///") and not any(ord(c) < 32 for c in endpoint))
            env["DOCKER_HOST"] = endpoint
            directory = work / "payload"
            recorder.prep.publish(directory, payload)
            (directory / "python").chmod(0o500)
            overlay.require({name: read_source(directory / name) for name in payload} == payload)
            report = run_container(directory, contract, config, env)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError):
            report["failure"] = "preflight-failed"
        report["provenance"] = {"git_sha": revision, "source_bundle_sha256": contract["source_bundle_sha256"],
            "payload_sha256": overlay.bundle_digest(payload), "validator_source_sha256": validator_sha,
            "requested_image": overlay.BASE_IMAGE, "expected_config_sha256": contract["official_config"]["sha256"],
            "raw_oci_metadata_verified": raw_verified, "measurement_sha256": contract["measurement_sha256"]}
        record = evidence_record(revision, args.slug, started, report)
        recorder.publish_evidence(record, report, nib, work, destination, env)
    print(destination.relative_to(ROOT).as_posix())
    return 1  # Always non-qualifying, including a successful bounded import smoke.


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit("Smoke operation failed; no successful evidence or runtime qualification may be inferred.")
