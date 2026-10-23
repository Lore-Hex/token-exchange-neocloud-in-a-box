#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prepare or record a bounded CPU-only, pre-serve engine-entry diagnostic."""

import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site):
    sys.exit("Use python3 -I -S scripts/record-engine-cpu-entry.py")

import datetime
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
BASE_SHA = "3548873bb00cdd07b00dd3cfcd2aa8744f74c93d4cddb61726725ef88338ebb3"
RUNTIME = "scripts/engine_cpu_entry.py"
LAB = "scripts/engine_lab.py"
LAB_BOOTSTRAP = "scripts/engine_lab_bootstrap.py"
EXCEPTIONS = "scripts/engine_exception.py"
TEST = "scripts/test_engine_cpu_entry.py"
SIX_TEST = "scripts/test_botocore_six.py"
OPENCV_TEST = "scripts/test_opencv_loader.py"
LABEL = "nib.cpu-entry"
TOKENIZER_FILES = ("scripts/engine_tokenizer_diagnostic.py", "scripts/engine_tokenizer_parity.py",
                   "scripts/test_engine_tokenizer_diagnostic.py")
PREFIX_FILES = ("scripts/engine_target_prefix.py", "scripts/engine_tokenizer_parity.py",
                "scripts/engine_local_correctness.py", "scripts/tau_agent_prefix.py",
                "scripts/test_tau_agent_prefix.py", "scripts/test_engine_target_prefix.py",
                "scripts/record-tau-agent-prefix.py")


def read_source(path, limit=2 << 20):
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
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= limit:
                raise ValueError("source rejected")
            raw = stream.read(limit + 1)
            def identity(row):
                return (row.st_dev, row.st_ino, row.st_mode, row.st_nlink, row.st_size, row.st_mtime_ns, row.st_ctime_ns)
            if (len(raw) != before.st_size or identity(before) != identity(os.fstat(stream.fileno()))
                    or identity(before) != identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False))):
                raise ValueError("source changed")
            return raw
    finally:
        os.close(parent)


def load(name, path, expected=None, prefix=b""):
    raw = read_source(path)
    if expected is not None and hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("source differs")
    module = types.ModuleType(name)
    module.__file__ = str(path)
    if prefix:
        exec(compile(prefix, EXCEPTIONS, "exec", flags=0, dont_inherit=True), module.__dict__)
    exec(compile(raw, str(path), "exec", flags=0, dont_inherit=True), module.__dict__)
    return module, raw


base, BASE_SOURCE = load("cpu_entry_base", ROOT / BASE, BASE_SHA)
EXCEPTION_SOURCE = read_source(ROOT / EXCEPTIONS)
cpu, CPU_SOURCE = load("cpu_entry_payload", ROOT / RUNTIME, prefix=EXCEPTION_SOURCE)
lab, LAB_SOURCE = load("cpu_entry_lab", ROOT / LAB)
bootstrap, BOOTSTRAP_SOURCE = load("cpu_entry_lab_bootstrap", ROOT / LAB_BOOTSTRAP)
cpu.require = base.overlay.require
builder, overlay, recorder, checker = base.builder, base.overlay, base.recorder, base.checker


def inputs(root=ROOT, tokenizer_continuation=False, tokenizer_materialization=False, *, agent_prefix=False):
    overlay.require(type(tokenizer_materialization) is bool and (not tokenizer_materialization or tokenizer_continuation))
    overlay.require(type(agent_prefix) is bool and (not agent_prefix or not tokenizer_continuation and not tokenizer_materialization))
    revision, bundle, extra = base.inputs(root)
    for name in (SCRIPT, BASE, RUNTIME, LAB, LAB_BOOTSTRAP, EXCEPTIONS, TEST, SIX_TEST, OPENCV_TEST, "scripts/test_deepseek_lab.py",
                 *(TOKENIZER_FILES if tokenizer_continuation else ()),
                 *(PREFIX_FILES if agent_prefix else ()),
                 *(("scripts/test_tokenizer_materialization.py",) if tokenizer_materialization else ())):
        row = checker.git(root, "ls-tree", revision, "--", name).decode().split()
        overlay.require(len(row) == 4 and row[:2] == ["100644", "blob"] and row[3] == name)
        overlay.require(int(checker.git(root, "cat-file", "-s", row[2])) <= checker.LIMIT)
        extra[name] = checker.git(root, "cat-file", "blob", row[2])
    overlay.require(extra[SCRIPT] == read_source(Path(__file__).absolute())
                    and extra[BASE] == BASE_SOURCE and extra[RUNTIME] == CPU_SOURCE
                    and extra[EXCEPTIONS] == EXCEPTION_SOURCE
                    and extra[LAB] == LAB_SOURCE and extra[LAB_BOOTSTRAP] == BOOTSTRAP_SOURCE)
    return revision, bundle, extra


def prepare_files(revision, bundle, extra, tokenizer_continuation=False, tokenizer_materialization=False,
                  *, agent_prefix=None, tokenizer_files=None):
    overlay.require(type(tokenizer_materialization) is bool and (not tokenizer_materialization or tokenizer_continuation))
    overlay.require((agent_prefix is None) == (tokenizer_files is None)
                    and (agent_prefix is None or not tokenizer_continuation and not tokenizer_materialization))
    files, _ = builder.prepare_files(revision, bundle, extra)
    application = overlay.load_json(files["payload/contract.json"])
    measured = builder.application.measurement(overlay, extra[builder.application.EVIDENCE])
    payload = {"application/" + name.removeprefix("payload/"): raw for name, raw in files.items() if name.startswith("payload/")}
    prefix = b'import sys\nif not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):\n    sys.exit("Isolated CPU entry required.")\n'
    payload["launch.py"] = prefix + extra[base.BOOTSTRAP_FILE] + b"\n" + extra[EXCEPTIONS] + b"\n" + extra[RUNTIME]
    payload["lab.py"] = extra[base.BOOTSTRAP_FILE] + b"\n" + extra[LAB_BOOTSTRAP] + b"\n" + extra[LAB]
    plan = recorder.prep.launch_plan()
    payload["launch-plan.json"] = overlay.json_bytes(plan)
    if tokenizer_continuation:
        payload["tokenizer-diagnostic.py"] = extra[TOKENIZER_FILES[0]]
        payload["tokenizer-parity.py"] = extra[TOKENIZER_FILES[1]]
    if agent_prefix is not None:
        def prefix_module(name):
            module = types.ModuleType(name)
            exec(compile(extra[name], str(ROOT / name), "exec", dont_inherit=True), module.__dict__)
            return module
        helper = prefix_module(PREFIX_FILES[3])
        helper.validate(agent_prefix)
        parity = prefix_module(PREFIX_FILES[1])
        overlay.require(type(tokenizer_files) is dict and set(tokenizer_files) == set(parity.MODEL_FILES))
        for name, (size, sha) in parity.MODEL_FILES.items():
            raw = tokenizer_files[name]
            overlay.require(type(raw) is bytes and len(raw) == size and overlay.digest(raw) == sha)
            payload["model/" + name] = raw
        for name, source in zip(("target-prefix.py", "tokenizer-parity.py", "correctness.py", "agent-prefix.py"), PREFIX_FILES):
            payload[name] = extra[source]
        payload["agent-prefix.json"] = helper.encoded(agent_prefix)
    contract = {"schema": "nib.engine-cpu-entry-input/v1", "status": "BLOCKED", "deployable": False,
                "confidential": False, "execution": "software", "git_sha": revision,
                "base_image": overlay.BASE_IMAGE, "official_config": application["official_config"],
                "source_bundle_sha256": checker.bundle_digest({**bundle, **extra}),
                "candidate_bundle_sha256": application["candidate_bundle_sha256"],
                "application_contract_sha256": overlay.digest(payload["application/contract.json"]),
                "measurement_sha256": builder.application.EVIDENCE_SHA,
                "paths": measured["proposed_paths"], "arguments": lab.lab_args(plan),
                "interpreter": {key: measured["files"][0][key] for key in ("resolved", "bytes", "sha256")},
                "environment": lab.lab_environment(plan),
                "sources": {name: overlay.digest(raw) for name, raw in payload.items()
                            if agent_prefix is None or not name.startswith("model/")}}
    if tokenizer_continuation:
        contract["diagnostic"] = "tokenizer-continuation"
    if agent_prefix is not None:
        contract["diagnostic"] = "tau-agent-prefix"
        contract["model_files"] = {name: {"bytes": size, "sha256": sha} for name, (size, sha) in parity.MODEL_FILES.items()}
    if tokenizer_materialization:
        overlay.require(extra[LAB_BOOTSTRAP] == BOOTSTRAP_SOURCE)
        contract["tokenizer_materialization"] = bootstrap.tokenizer_adapter_identity()
    payload["entry.json"] = overlay.json_bytes(contract)
    payload["python"] = lab.wrapper_bytes(contract["interpreter"]["resolved"], contract["environment"], overlay.digest(payload["entry.json"]))
    model_bytes = sum(map(len, tokenizer_files.values())) if tokenizer_files is not None else 0
    overlay.require(len(payload) <= 110 and sum(map(len, payload.values())) - model_bytes <= overlay.MAX_CONTEXT)
    return payload, contract


def payload_source(directory, name):
    return read_source(directory / name, 6367257 if name == "model/tokenizer.json" else 2 << 20)


def publish_container_payload(directory, payload):
    # As in record-engine-image: the host parent stays private, while the
    # read-only bind must be readable without CAP_DAC_OVERRIDE on native Linux.
    directory = Path(directory)
    with overlay.directory(directory.parent) as parent:
        info = os.fstat(parent)
        overlay.require(stat.S_IMODE(info.st_mode) == 0o700 and info.st_uid == os.getuid())
    recorder.prep.publish(directory, payload)
    overlay.require({name: payload_source(directory, name) for name in payload} == payload)
    directories = {directory}
    for name in payload:
        path = directory / name
        with overlay.directory(path.parent) as parent:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            try:
                info = os.fstat(fd)
                overlay.require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
                os.fchmod(fd, 0o555 if name == "python" else 0o444)
            finally:
                os.close(fd)
        directories.update(path.parents[:len(Path(name).parts)])
    for path in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        with overlay.directory(path) as fd:
            os.fchmod(fd, 0o555)


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
        overlay.require(result.get("diagnostic") == contract.get("diagnostic"))
        application = overlay.load_json(read_source(payload / "application/contract.json"))
        measured = builder.application.measurement(overlay, read_source(payload / "application/measurement.json"))
        passed = cpu.validate(overlay, result, application, measured, base.smoke,
                              contract.get("tokenizer_materialization"),
                              **({"prefix_sources": {name: payload_source(payload, name) for name in contract["sources"]}}
                                 if contract.get("diagnostic") == "tau-agent-prefix" else {}))
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


def evidence_record(revision, slug, started, report, host_kind="local"):
    overlay.require(host_kind in ("local", "github-actions"))
    suffix = "" if host_kind == "local" else " --host-kind github-actions"
    diagnostic = report.get("diagnostic") == "tokenizer-continuation"
    if diagnostic:
        suffix += " --tokenizer-continuation"
    if "tokenizer_materialization" in report:
        suffix += " --tokenizer-materialization"
    return {"schema": "nib.evidence/v1", "phase": "P2", "task": "T3", "git_sha": revision,
            "command": "python3 -I -S scripts/record-engine-cpu-entry.py --slug " + slug + suffix,
            "started_utc": started, "ended_utc": recorder.now(), "result": "fail", "execution": "software",
            "hardware_validated": False, "host": {"provider": host_kind, "machine": "local-software-experiment"
                                                if host_kind == "local" else "linux-amd64-software-runner",
                                                "gpus": "none", "driver": None, "cuda": None},
            "notes": ("CPU-only tokenizer request/parser/normalizer diagnostic; no BPE, renderer or HTTP execution. "
                      if diagnostic else "CPU-only pre-serve diagnostic, not the P0 deny-all import sandbox. ") +
                     "No model/weights/GPU runtime/ports/network/credentials; serve.cmd and worker routes never invoked. "
                     "Imports may probe absent CUDA or compile dependencies; cache is bounded noexec. "
                     "A failure is not proof of the earlier GPU startup cause; a pass is not GPU/ABI/inference qualification. "
                     "Candidate BLOCKED; confidential=false. Docker/OS/interpreter trusted. Payload SHA-256: "
                     + report["provenance"]["payload_sha256"]}


def check_evidence(record, report, revision, slug, payload, contract, host_kind, validator_sha):
    """Recheck the same producer's evidence; a diagnostic pass is not admission."""
    for key in ("started_utc", "ended_utc"):
        value = record[key]
        overlay.require(type(value) is str and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value))
        datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    overlay.require(record["started_utc"] <= record["ended_utc"])
    expected = evidence_record(revision, slug, record["started_utc"], report, host_kind)
    expected["ended_utc"] = record["ended_utc"]
    overlay.require(overlay.json_bytes(record) == overlay.json_bytes(expected))
    overlay.require(set(report) == set(report_start()) | {"image", "experiment", "provenance"}
                    | ({"diagnostic"} if "diagnostic" in contract else set())
                    | ({"tokenizer_materialization"} if "tokenizer_materialization" in contract else set()))
    overlay.require(report.get("tokenizer_materialization") == contract.get("tokenizer_materialization"))
    overlay.require(report.get("diagnostic") == contract.get("diagnostic")
                    and report["experiment"].get("diagnostic") == contract.get("diagnostic"))
    for key, value in report_start().items():
        if key not in ("stage", "cleanup", "entry_pass"):
            overlay.require(type(report.get(key)) is type(value) and report[key] == value)
    overlay.require(report["stage"] == "completed" and report["cleanup"] == "confirmed"
                    and type(report["entry_pass"]) is bool and "failure" not in report)
    provenance = report["provenance"]
    expected_provenance = {
        "git_sha": revision, "source_bundle_sha256": contract["source_bundle_sha256"],
        "payload_sha256": overlay.bundle_digest(payload), "requested_image": overlay.BASE_IMAGE,
        "expected_config_sha256": contract["official_config"]["sha256"],
        "measurement_sha256": contract["measurement_sha256"], "raw_oci_metadata_verified": True,
        "validator_source_sha256": validator_sha,
    }
    overlay.require(overlay.json_bytes(provenance) == overlay.json_bytes(expected_provenance))
    image = report["image"]
    overlay.require(type(image["docker_reported_id"]) is str
                    and re.fullmatch(r"sha256:[0-9a-f]{64}", image["docker_reported_id"]))
    expected_image = {
        "manifest_sha256": contract["base_image"].rsplit("sha256:", 1)[1],
        "raw_config_sha256": contract["official_config"]["sha256"],
        "docker_reported_id": image["docker_reported_id"], "platform": "linux/amd64",
        "binding": "local-manifest-reference-platform-and-diffids",
        "raw_config_link_verified": True, "local_config_bytes_rehashed": False,
        "local_layers_rehashed": False, "daemon_os_interpreter_trusted": True,
    }
    overlay.require(overlay.json_bytes(image) == overlay.json_bytes(expected_image))
    application = overlay.load_json(payload["application/contract.json"])
    measured = builder.application.measurement(overlay, payload["application/measurement.json"])
    passed = cpu.validate(overlay, report["experiment"], application, measured, base.smoke,
                          contract.get("tokenizer_materialization"))
    overlay.require(report["entry_pass"] == passed)
    return passed


def main():
    signal.signal(signal.SIGTERM, recorder.cancel)
    signal.signal(signal.SIGINT, recorder.cancel)
    parser = overlay.QuietParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--prepare", metavar="PRIVATE_OUTPUT", help="Offline preparation only.")
    action.add_argument("--slug", help="Explicit bounded local CPU-only execution; no qualification.")
    action.add_argument("--check-result", metavar="EVIDENCE_DIRECTORY", help="Offline check against this exact producer.")
    parser.add_argument("--host-kind", choices=("local", "github-actions"), default="local")
    parser.add_argument("--tokenizer-continuation", action="store_true",
                        help="Fixed CPU-only parity request/parser/normalizer failure diagnostic.")
    parser.add_argument("--tokenizer-materialization", action="store_true",
                        help="Opt-in source-pinned server validator correction; requires --tokenizer-continuation.")
    args = parser.parse_args()
    revision, bundle, extra = inputs(tokenizer_continuation=args.tokenizer_continuation,
                                    tokenizer_materialization=args.tokenizer_materialization)
    payload, contract = prepare_files(revision, bundle, extra, tokenizer_continuation=args.tokenizer_continuation,
                                      tokenizer_materialization=args.tokenizer_materialization)
    if args.check_result:
        directory = Path(args.check_result).absolute()
        match = re.fullmatch(r"\d{4}-\d{2}-\d{2}-([a-z0-9]+(?:-[a-z0-9]+)*)", directory.name)
        overlay.require(match is not None and len(match[1]) <= 64)
        record = overlay.load_json(read_source(directory / "run.json"))
        report = overlay.load_json(read_source(directory / "output.log"))
        overlay.require(directory.name[:10] == record["started_utc"][:10])
        recorder.raw_metadata(ROOT / "bench/engine-cpu-entry/oci", contract)
        with tempfile.TemporaryDirectory(prefix="nib-cpu-check-", dir="/private/tmp" if sys.platform == "darwin" else None) as temp:
            work = Path(temp).resolve()
            env = recorder.offline_environment(work / "control")
            _, validator_sha = recorder.build_validator(ROOT, revision, work, env)
            passed = check_evidence(record, report, revision, match[1], payload, contract, args.host_kind, validator_sha)
        print(overlay.json_bytes({"record_valid": True, "entry_pass": passed, "confidential": False}).decode(), end="")
        return 0 if passed else 1
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
            publish_container_payload(directory, payload)
            overlay.require({name: read_source(directory / name) for name in payload} == payload)
            report = run_container(directory, contract, config, env)
        except Exception:
            report["failure"] = "preflight-failed"
        report["provenance"] = {"git_sha": revision, "source_bundle_sha256": contract["source_bundle_sha256"],
            "payload_sha256": overlay.bundle_digest(payload), "validator_source_sha256": validator_sha,
            "requested_image": overlay.BASE_IMAGE, "expected_config_sha256": contract["official_config"]["sha256"],
            "raw_oci_metadata_verified": verified, "measurement_sha256": contract["measurement_sha256"]}
        if args.tokenizer_continuation:
            report["diagnostic"] = "tokenizer-continuation"
        if args.tokenizer_materialization:
            report["tokenizer_materialization"] = contract["tokenizer_materialization"]
        record = evidence_record(revision, args.slug, started, report, args.host_kind)
        recorder.publish_evidence(record, report, nib, work, destination, env)
    print(destination.relative_to(ROOT).as_posix())
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit("CPU entry operation rejected; no qualification may be inferred.")
