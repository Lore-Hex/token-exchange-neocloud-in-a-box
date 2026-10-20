#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prepare or record a bounded CPU-only, pre-serve engine-entry diagnostic."""

import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site):
    sys.exit("Use python3 -I -S scripts/record-engine-cpu-entry.py")

import hashlib
import os
from pathlib import Path
import pwd
import re
import signal
import stat
import tempfile
import types
import uuid

ROOT = Path(__file__).absolute().parent.parent
SCRIPT = "scripts/record-engine-cpu-entry.py"
BASE = "scripts/record-engine-import-smoke.py"
BASE_SHA = "8fadc2b6f408b25ac3238593cb591d8ff4f9324a8855b867c624f8085c4307f9"
RUNTIME = "scripts/engine_cpu_entry.py"
LAB = "scripts/engine_lab.py"
LAB_BOOTSTRAP = "scripts/engine_lab_bootstrap.py"
TEST = "scripts/test_engine_cpu_entry.py"
LABEL = "nib.cpu-entry"


def read_source(path):
    parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        path = Path(path)
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("source rejected")
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= 2 << 20:
                raise ValueError("source rejected")
            raw = stream.read((2 << 20) + 1)
            def identity(row):
                return (row.st_dev, row.st_ino, row.st_mode, row.st_nlink, row.st_size, row.st_mtime_ns, row.st_ctime_ns)
            if (len(raw) != before.st_size or identity(before) != identity(os.fstat(stream.fileno()))
                    or identity(before) != identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False))):
                raise ValueError("source changed")
            return raw
    finally:
        os.close(parent)


def load(name, path, expected=None):
    raw = read_source(path)
    if expected is not None and hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("source differs")
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec", flags=0, dont_inherit=True), module.__dict__)
    return module, raw


base, BASE_SOURCE = load("cpu_entry_base", ROOT / BASE, BASE_SHA)
cpu, CPU_SOURCE = load("cpu_entry_payload", ROOT / RUNTIME)
lab, LAB_SOURCE = load("cpu_entry_lab", ROOT / LAB)
cpu.require = base.overlay.require
builder, overlay, recorder, checker = base.builder, base.overlay, base.recorder, base.checker


def inputs(root=ROOT):
    revision, bundle, extra = base.inputs(root)
    for name in (SCRIPT, BASE, RUNTIME, LAB, LAB_BOOTSTRAP, TEST, "scripts/test_deepseek_lab.py"):
        row = checker.git(root, "ls-tree", revision, "--", name).decode().split()
        overlay.require(len(row) == 4 and row[:2] == ["100644", "blob"] and row[3] == name)
        overlay.require(int(checker.git(root, "cat-file", "-s", row[2])) <= checker.LIMIT)
        extra[name] = checker.git(root, "cat-file", "blob", row[2])
    overlay.require(extra[SCRIPT] == read_source(Path(__file__).absolute())
                    and extra[BASE] == BASE_SOURCE and extra[RUNTIME] == CPU_SOURCE
                    and extra[LAB] == LAB_SOURCE)
    return revision, bundle, extra


def prepare_files(revision, bundle, extra):
    files, _ = builder.prepare_files(revision, bundle, extra)
    application = overlay.load_json(files["payload/contract.json"])
    measured = builder.application.measurement(overlay, extra[builder.application.EVIDENCE])
    payload = {"application/" + name.removeprefix("payload/"): raw for name, raw in files.items() if name.startswith("payload/")}
    prefix = b'import sys\nif not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):\n    sys.exit("Isolated CPU entry required.")\n'
    payload["launch.py"] = prefix + extra[base.BOOTSTRAP_FILE] + b"\n" + extra[RUNTIME]
    payload["lab.py"] = extra[base.BOOTSTRAP_FILE] + b"\n" + extra[LAB_BOOTSTRAP] + b"\n" + extra[LAB]
    plan = recorder.prep.launch_plan()
    payload["launch-plan.json"] = overlay.json_bytes(plan)
    contract = {"schema": "nib.engine-cpu-entry-input/v1", "status": "BLOCKED", "deployable": False,
                "confidential": False, "execution": "software", "git_sha": revision,
                "base_image": overlay.BASE_IMAGE, "official_config": application["official_config"],
                "source_bundle_sha256": checker.bundle_digest({**bundle, **extra}),
                "candidate_bundle_sha256": application["candidate_bundle_sha256"],
                "application_contract_sha256": overlay.digest(payload["application/contract.json"]),
                "measurement_sha256": builder.application.EVIDENCE_SHA,
                "paths": measured["proposed_paths"], "arguments": lab.lab_args(plan),
                "interpreter": {key: measured["files"][0][key] for key in ("resolved", "bytes", "sha256")},
                "environment": lab.lab_environment(plan), "sources": {name: overlay.digest(raw) for name, raw in payload.items()}}
    payload["entry.json"] = overlay.json_bytes(contract)
    payload["python"] = lab.wrapper_bytes(contract["interpreter"]["resolved"], contract["environment"], overlay.digest(payload["entry.json"]))
    overlay.require(len(payload) <= 110 and sum(map(len, payload.values())) <= overlay.MAX_CONTEXT)
    return payload, contract


def create_command(name, owner, payload, sha, config, contract):
    # Reuse P0's CPU-only container policy; only the fixed entry/environment,
    # owned label and a bounded noexec cache tmpfs differ.
    args = base.create_command(name, owner, payload, sha, config)
    args = args[:args.index(overlay.BASE_IMAGE)]
    args[args.index("nib.import-smoke=" + owner)] = LABEL + "=" + owner
    args += ["--tmpfs", "/cache:rw,nosuid,nodev,noexec,size=512m,mode=0700"]
    return args + [overlay.BASE_IMAGE, "-i", *[key + "=" + value for key, value in sorted(contract["environment"].items())],
                   "/usr/bin/python3.12", "-I", "-S", "-B", "/opt/nib/launch.py", sha, "apply"]


def check_container(info, owner, payload, sha, image_id, config_input, contract):
    command = create_command("unused", owner, payload, sha, config_input, contract)
    cfg, host = info["Config"], info["HostConfig"]
    overlay.require(cfg["Labels"].get(LABEL) == owner and info["Image"] == image_id
        and cfg["Image"] == overlay.BASE_IMAGE and cfg["Entrypoint"] == ["/usr/bin/env"]
        and cfg["Cmd"] == command[command.index(overlay.BASE_IMAGE) + 1:]
        and cfg["User"] == "0:0" and cfg["WorkingDir"] == "/"
        and cfg["Healthcheck"]["Test"] == ["NONE"] and not cfg.get("Tty") and not cfg.get("OpenStdin"))
    expected_env = [command[i + 1] for i, arg in enumerate(command) if arg == "--env"]
    overlay.require(len(cfg["Env"]) == len(expected_env) and set(cfg["Env"]) == set(expected_env))
    overlay.require(host["NetworkMode"] == "none" and host["ReadonlyRootfs"] is False
        and host["Privileged"] is False and host["Runtime"] == "runc" and host["CapDrop"] == ["ALL"]
        and not host.get("CapAdd") and not host.get("Devices") and not host.get("DeviceRequests")
        and not host.get("DeviceCgroupRules") and not host.get("PortBindings") and not host.get("PublishAllPorts")
        and not host.get("PidMode") and not host.get("UTSMode") and not host.get("VolumesFrom") and not host.get("Binds")
        and host["IpcMode"] == "private" and host["ShmSize"] == 16 << 20 and host["CgroupnsMode"] == "private"
        and host["Memory"] == host["MemorySwap"] == 8 << 30 and host["NanoCpus"] == 2000000000 and host["PidsLimit"] == 128
        and host["RestartPolicy"]["Name"] == "no" and host["LogConfig"]["Type"] == "none"
        and set(host["SecurityOpt"]) in ({"no-new-privileges"}, {"no-new-privileges=true"})
        and host["Tmpfs"] == {"/tmp": "rw,nosuid,nodev,noexec,size=512m,mode=1777",
                             "/cache": "rw,nosuid,nodev,noexec,size=512m,mode=0700"})
    mounts = info["Mounts"]
    overlay.require(len(mounts) == 1 and mounts[0]["Type"] == "bind" and mounts[0]["Source"] == str(payload)
                    and mounts[0]["Destination"] == "/opt/nib" and mounts[0]["RW"] is False)


def report_start():
    return {"schema": "nib.engine-cpu-entry-record/v1", "result": "fail", "execution": "software",
            "stage": "image-preflight", "cleanup": "not-created", "entry_pass": False,
            "candidate_status": "BLOCKED", "deployable": False, "confidential": False,
            "abi_validated": False, "hardware_validated": False, "inference_started": False}


def run_container(payload, contract, config, env, execute=recorder.snapshot.execute_process_group):
    name, owner = "nib-cpu-entry-" + uuid.uuid4().hex, uuid.uuid4().hex
    report = report_start()
    create_started = create_complete = runtime_started = runtime_complete = False
    absent = ["docker", "container", "ls", "--all", "--quiet", "--filter", "name=^/" + name + "$"]
    sha = overlay.digest(read_source(payload / "entry.json"))
    try:
        value = recorder.docker_json("image", overlay.BASE_IMAGE, env, execute)
        report["image"] = recorder.image_binding(value, config, contract)
        overlay.require(not recorder.CANCELLED and not recorder.call(absent, env, execute=execute).strip())
        image_env = dict(item.split("=", 1) for item in config["config"]["Env"])
        overlay.require(image_env["LD_LIBRARY_PATH"] == lab.IMAGE_LD_PATH and image_env["CUDA_HOME"] == "/usr/local/cuda")
        report["stage"] = "container-create"
        create_started = True
        created = recorder.call(create_command(name, owner, payload, sha, config, contract), env, execute=execute).decode().strip()
        overlay.require(re.fullmatch(r"[0-9a-f]{64}", created))
        create_complete = True
        report["stage"] = "isolation-check"
        info = recorder.docker_json("container", name, env, execute)
        check_container(info, owner, payload, sha, value["Id"], config, contract)
        overlay.require(info["Id"] == created and info["State"]["Status"] == "created" and not recorder.CANCELLED)
        report["stage"] = "application-and-entry"
        runtime_started = True
        raw = recorder.call(["docker", "container", "start", "--attach", name], env, execute=execute,
                            timeout=cpu.ATTACH_SECONDS, limit=65536)
        report["stage"] = "result-validation"
        result = overlay.load_json(raw)
        application = overlay.load_json(read_source(payload / "application/contract.json"))
        measured = builder.application.measurement(overlay, read_source(payload / "application/measurement.json"))
        passed = cpu.validate(overlay, result, application, measured, base.smoke)
        terminal = recorder.docker_json("container", name, env, execute)
        overlay.require(terminal["Id"] == created and terminal["State"]["Status"] == "exited"
                        and terminal["State"]["ExitCode"] == 0 and not terminal["State"].get("OOMKilled"))
        runtime_complete = True
        report.update(experiment=result, entry_pass=passed, stage="completed")
    except Exception:
        report["failure"] = "bounded-cpu-entry-stage-failed"
    finally:
        if create_started:
            report["cleanup"] = "unknown"
            try:
                if recorder.call(absent, env, execute=execute).strip():
                    owned = recorder.docker_json("container", name, env, execute)
                    overlay.require(owned["Config"]["Labels"].get(LABEL) == owner)
                    recorder.call(["docker", "container", "rm", "--force", name], env, execute=execute)
                if not recorder.call(absent, env, execute=execute).strip():
                    report["cleanup"] = "confirmed"
            except Exception:
                pass
            if not create_complete or (runtime_started and not runtime_complete):
                report["cleanup"] = "completion-unknown"
            if report["cleanup"] != "confirmed":
                report.update(entry_pass=False, failure="cleanup-unresolved")
    return report


def evidence_record(revision, slug, started, report):
    return {"schema": "nib.evidence/v1", "phase": "P2", "task": "T3", "git_sha": revision,
            "command": "python3 -I -S scripts/record-engine-cpu-entry.py --slug " + slug,
            "started_utc": started, "ended_utc": recorder.now(), "result": "fail", "execution": "software",
            "hardware_validated": False, "host": {"provider": "local", "machine": "local-software-experiment",
                                                "gpus": "none", "driver": None, "cuda": None},
            "notes": "CPU-only pre-serve diagnostic, not the P0 deny-all import sandbox. "
                     "No model/weights/GPU runtime/ports/network/credentials; serve.cmd and worker routes never invoked. "
                     "Imports may probe absent CUDA or compile dependencies; cache is bounded noexec. "
                     "A failure is not proof of the earlier GPU startup cause; a pass is not GPU/ABI/inference qualification. "
                     "Candidate BLOCKED; confidential=false. Docker/OS/interpreter trusted. Payload SHA-256: "
                     + report["provenance"]["payload_sha256"]}


def main():
    signal.signal(signal.SIGTERM, recorder.cancel)
    signal.signal(signal.SIGINT, recorder.cancel)
    parser = overlay.QuietParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--prepare", metavar="PRIVATE_OUTPUT", help="Offline preparation only.")
    action.add_argument("--slug", help="Explicit bounded local CPU-only execution; no qualification.")
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
    destination = ROOT / "evidence/p2" / (started[:10] + "-" + args.slug)
    overlay.require(not os.path.lexists(destination))
    with tempfile.TemporaryDirectory(prefix="nib-cpu-entry-", dir="/private/tmp" if sys.platform == "darwin" else None) as temporary:
        work = Path(temporary).resolve()
        env = recorder.offline_environment(work / "control")
        nib, validator_sha = recorder.build_validator(ROOT, revision, work, env)
        report = report_start()
        report["stage"] = "raw-oci-metadata"
        verified = False
        try:
            config = recorder.raw_metadata(ROOT / "build/engine-image-metadata", contract)
            verified = True
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
        except Exception:
            report["failure"] = "preflight-failed"
        report["provenance"] = {"git_sha": revision, "source_bundle_sha256": contract["source_bundle_sha256"],
            "payload_sha256": overlay.bundle_digest(payload), "validator_source_sha256": validator_sha,
            "requested_image": overlay.BASE_IMAGE, "expected_config_sha256": contract["official_config"]["sha256"],
            "raw_oci_metadata_verified": verified, "measurement_sha256": contract["measurement_sha256"]}
        record = evidence_record(revision, args.slug, started, report)
        recorder.publish_evidence(record, report, nib, work, destination, env)
    print(destination.relative_to(ROOT).as_posix())
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit("CPU entry operation rejected; no qualification may be inferred.")
