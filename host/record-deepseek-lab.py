#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prepare offline, or explicitly run one private nonqualifying CC experiment."""

import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site):
    sys.exit("Use python3 -I -S host/record-deepseek-lab.py")

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import select
import signal
import stat
import subprocess
import tempfile
import time
import types
import uuid

ROOT = Path(__file__).absolute().parent.parent
SCRIPT = "host/record-deepseek-lab.py"
BUILDER = "scripts/prepare-engine-application.py"
BUILDER_SHA = "4fa7545b8b36b141d2eb700cd3ca19c2ab995b7a26aaf9be56a1d380e8db17c9"
HELPER = "scripts/engine_python_bootstrap.py"
RUNTIME = "scripts/engine_lab.py"
LAB_BOOTSTRAP = "scripts/engine_lab_bootstrap.py"
EXCEPTIONS = "scripts/engine_exception.py"
PROBE = "scripts/engine_lab_probe.py"
CORRECTNESS = "scripts/engine_local_correctness.py"
PARITY = "scripts/engine_tokenizer_parity.py"
SMOKE = "scripts/engine_import_smoke.py"
TEST = "scripts/test_deepseek_lab.py"
SIX_TEST = "scripts/test_botocore_six.py"
PACK = "models/deepseek-v4.1-flash-1017/1"
SUPPORT = ("scripts/record-engine-image.py", "scripts/prepare-engine-overlay.py",
           "scripts/check-engine-candidate.py", "scripts/engine_overlay.py",
           "scripts/engine_image_diagnostic.py", "scripts/engine_python_descriptor.py", "scripts/e2e/snapshot.py")
MAX_BYTES = 64 << 20
MAX_SECONDS = 4800
ENGINE_MEMORY_GIB = 512
WATCHDOG_SETTLE_SECONDS = 40
LABEL = "nib.deepseek-lab"
CANCELLED = False
CLEANING = False


def require(value):
    if not value:
        raise ValueError("lab contract rejected")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read(path, limit=2 << 20):
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        raw = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(raw, "rb") as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 < before.st_size <= limit)
            data = stream.read(limit + 1)
            def identity(s):
                return (s.st_dev, s.st_ino, s.st_mode, s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            require(len(data) == before.st_size and identity(before) == identity(os.fstat(stream.fileno()))
                    and identity(before) == identity(os.stat(path.name, dir_fd=fd, follow_symlinks=False)))
            return data
    finally:
        os.close(fd)


def load(name, path, expected=None):
    raw = read(path)
    require(expected is None or digest(raw) == expected)
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec", flags=0, dont_inherit=True), module.__dict__)
    return module


def committed(builder, revision, name):
    row = builder.checker.git(ROOT, "ls-tree", revision, "--", name).decode().split()
    require(len(row) == 4 and row[:2] == ["100644", "blob"] and row[3] == name)
    require(int(builder.checker.git(ROOT, "cat-file", "-s", row[2])) <= 2 << 20)
    return builder.checker.git(ROOT, "cat-file", "blob", row[2])


def parallel_size(contract):
    size = contract.get("data_parallel_size", 1)
    require(type(size) is int and size in (1, 2))
    require(size == 1 or (contract.get("operation") == "local-correctness"
            and contract.get("gpu_family") == "B300"
            and contract.get("gpu_security", "confidential") == "confidential"))
    return size


def make_payload(builder, revision, bundle, extra, binary, operation="functional", family="B200", security="confidential", replicas=1,
                 *, tokenizer_parity=False, tokenizer_materialization=False):
    require(operation in ("functional", "hardware-diagnostic", "local-correctness", "pre-serve"))
    require(family in ("B200", "B300") and (family == "B200" or operation in ("local-correctness", "pre-serve")))
    require(security in ("confidential", "nonconfidential") and (security == "confidential"
            or operation == "local-correctness"))
    parallel_size({"operation": operation, "gpu_family": family, "gpu_security": security,
                   "data_parallel_size": replicas})
    require(type(tokenizer_parity) is bool and (not tokenizer_parity or
            (operation == "local-correctness" and family == "B300" and security == "confidential")))
    require(type(tokenizer_materialization) is bool and (not tokenizer_materialization or tokenizer_parity))
    files, _ = builder.prepare_files(revision, bundle, extra)
    app = builder.overlay.load_json(files["payload/contract.json"])
    measured = builder.application.measurement(builder.overlay, extra[builder.application.EVIDENCE])
    runtime = types.ModuleType("lab_constants")
    exec(compile(extra[RUNTIME], RUNTIME, "exec", dont_inherit=True), runtime.__dict__)
    plan = builder.recorder.prep.launch_plan()
    prefix = b'import sys\nif not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):\n    sys.exit("Isolated lab required.")\n'
    payload = {"application/" + name.removeprefix("payload/"): data for name, data in files.items() if name.startswith("payload/")}
    payload.update({"launch.py": prefix + extra[HELPER] + b"\n" + extra[EXCEPTIONS] + b"\n" + extra[LAB_BOOTSTRAP] + b"\n" + extra[RUNTIME],
                    "exception.py": extra[EXCEPTIONS],
                    "probe.py": extra[PROBE], "launch-plan.json": builder.overlay.json_bytes(plan),
                    "processes.py": extra[builder.recorder.SNAPSHOT],
                    "nib": binary, "pack/pack.yaml": extra[PACK + "/pack.yaml"],
                    "pack/files.sha256": extra[PACK + "/files.sha256"]})
    if operation == "local-correctness":
        payload["correctness.py"] = extra[CORRECTNESS]
    if tokenizer_parity:
        payload["tokenizer-parity.py"] = extra[PARITY]
    if tokenizer_materialization:
        payload["tokenizer-adapter.py"] = extra[LAB_BOOTSTRAP]
    contract = {"schema": "nib.deepseek-lab-input/v1", "status": "BLOCKED", "deployable": False, "confidential": False,
                "operation": operation,
                "git_sha": revision, "base_image": builder.overlay.BASE_IMAGE, "official_config": app["official_config"],
                "source_bundle_sha256": builder.checker.bundle_digest({**bundle, **extra}),
                "application_contract_sha256": digest(payload["application/contract.json"]),
                "candidate_bundle_sha256": app["candidate_bundle_sha256"],
                "validation_source_sha256": digest(extra[SMOKE]),
                "pack_revision": runtime.MODEL_REVISION, "pack_bytes": runtime.TOTAL_BYTES,
                "manifest_sha256": digest(payload["pack/files.sha256"]),
                "interpreter": {key: measured["files"][0][key] for key in ("resolved", "bytes", "sha256")},
                "paths": measured["proposed_paths"], "arguments": runtime.lab_args(plan, replicas),
                "environment": runtime.lab_environment(plan), "sources": {name: digest(data) for name, data in payload.items()}}
    if family == "B300":
        contract["gpu_family"] = family
    if security == "nonconfidential":
        contract["gpu_security"] = security
    if replicas == 2:
        contract["data_parallel_size"] = replicas
    if tokenizer_parity:
        contract["tokenizer_parity"] = True
    if tokenizer_materialization:
        adapter = types.ModuleType("lab_adapter_constants")
        exec(compile(extra[LAB_BOOTSTRAP], LAB_BOOTSTRAP, "exec", dont_inherit=True), adapter.__dict__)
        contract["tokenizer_materialization"] = adapter.tokenizer_adapter_identity()
    payload["lab.json"] = builder.overlay.json_bytes(contract)
    payload["python"] = runtime.wrapper_bytes(contract["interpreter"]["resolved"], contract["environment"], digest(payload["lab.json"]))
    return payload, contract


def prepare(destination, operation="functional", family="B200", security="confidential", replicas=1, *,
            tokenizer_parity=False, tokenizer_materialization=False):
    builder = load("lab_application_builder", ROOT / BUILDER, BUILDER_SHA)
    revision, bundle, extra = builder.inputs(ROOT)
    for name in (SCRIPT, HELPER, RUNTIME, LAB_BOOTSTRAP, EXCEPTIONS, PROBE, SMOKE, TEST, SIX_TEST,
                 PACK + "/pack.yaml", PACK + "/files.sha256"):
        extra[name] = committed(builder, revision, name)
    if operation == "local-correctness":
        for name in (CORRECTNESS, "scripts/test_engine_local_correctness.py"):
            extra[name] = committed(builder, revision, name)
    if tokenizer_parity:
        for name in (PARITY, "scripts/test_engine_tokenizer_parity.py"):
            extra[name] = committed(builder, revision, name)
    if tokenizer_materialization:
        name = "scripts/test_tokenizer_materialization.py"
        extra[name] = committed(builder, revision, name)
    require(extra[SCRIPT] == read(Path(__file__).absolute()))
    recorder = builder.recorder
    with tempfile.TemporaryDirectory(prefix="nib-lab-build-") as temporary:
        work = Path(temporary).resolve()
        env = recorder.offline_environment(work / "control")
        binary, source_sha = recorder.build_validator(ROOT, revision, work, {**env, "GOOS": "linux", "GOARCH": "amd64"})
        payload, contract = make_payload(builder, revision, bundle, extra, read(binary, MAX_BYTES), operation, family, security, replicas,
                                         tokenizer_parity=tokenizer_parity, tokenizer_materialization=tokenizer_materialization)
        config = recorder.raw_metadata(ROOT / "build/engine-image-metadata", contract)
        runtime = load("lab_environment_constants", ROOT / RUNTIME, digest(extra[RUNTIME]))
        image_env = dict(item.split("=", 1) for item in config["config"]["Env"])
        require(image_env["LD_LIBRARY_PATH"] == runtime.IMAGE_LD_PATH and image_env["CUDA_HOME"] == "/usr/local/cuda")
        files = {"record-deepseek-lab.py": extra[SCRIPT], **{"payload/" + n: b for n, b in payload.items()}}
        files["support/" + SMOKE] = extra[SMOKE]
        for name in SUPPORT:
            files["support/" + name] = (bundle if name in bundle else extra)[name]
        for name in ("manifest.json", "config.json"):
            files["metadata/" + name] = read(ROOT / "build/engine-image-metadata" / name)
        receipt = {"schema": "nib.deepseek-lab-bundle/v1", "git_sha": revision,
                   "status": "BLOCKED", "deployable": False, "confidential": False,
                   "platform": "linux/amd64", "validator_source_sha256": source_sha,
                   "payload_sha256": builder.checker.bundle_digest(payload),
                   "source_bundle_sha256": contract["source_bundle_sha256"],
                   "files": {n: {"bytes": len(b), "sha256": digest(b)} for n, b in files.items()}}
        files["receipt.json"] = recorder.overlay.json_bytes(receipt)
        recorder.prep.publish(destination, files)
        for name in ("payload/nib", "payload/python"):
            (Path(destination) / name).chmod(0o500)
    print(json.dumps({"git_sha": revision, "receipt_sha256": digest(files["receipt.json"]),
                      "status": "BLOCKED", "executed": False}, sort_keys=True))


def bundle_inputs(path, expected):
    path = Path(path)
    require(path.is_absolute() and re.fullmatch(r"[0-9a-f]{64}", expected))
    raw = read(path / "receipt.json")
    require(digest(raw) == expected)
    receipt = json.loads(raw)
    require(receipt["schema"] == "nib.deepseek-lab-bundle/v1" and receipt["status"] == "BLOCKED"
            and receipt["deployable"] is False and receipt["confidential"] is False
            and receipt["platform"] == "linux/amd64" and re.fullmatch(r"[0-9a-f]{40}", receipt["git_sha"]))
    require(type(receipt["files"]) is dict and len(receipt["files"]) <= 130)
    total = 0
    for name, row in receipt["files"].items():
        require(re.fullmatch(r"[A-Za-z0-9_./-]+", name) and not name.startswith("/")
                and all(p not in ("", ".", "..") for p in name.split("/")))
        data = read(path / name, MAX_BYTES if name == "payload/nib" else 2 << 20)
        total += len(data)
        require(row == {"bytes": len(data), "sha256": digest(data)} and total <= 80 << 20)
    actual = set()
    directories_seen = 0
    for parent, directories, names in os.walk(path, followlinks=False):
        directories_seen += 1
        require(directories_seen <= 130 and len(Path(parent).relative_to(path).parts) <= 16
                and len(directories) <= 130)
        require(len(actual) + len(names) <= 150)
        for name in directories:
            require(not (Path(parent) / name).is_symlink())
        actual.update((Path(parent) / name).relative_to(path).as_posix() for name in names)
    require(actual == set(receipt["files"]) | {"receipt.json"})
    require(read(Path(__file__).absolute()) == read(path / "record-deepseek-lab.py"))
    # This is the already reviewed raw-OCI/config/platform/DiffID verifier and
    # bounded process implementation, not a second image verification engine.
    recorder = load("lab_record_support", path / "support/scripts/record-engine-image.py",
                    receipt["files"]["support/scripts/record-engine-image.py"]["sha256"])
    contract = recorder.overlay.load_json(read(path / "payload/lab.json"))
    require(contract["git_sha"] == receipt["git_sha"] and contract["status"] == "BLOCKED"
            and contract["deployable"] is False and contract["confidential"] is False
            and contract["operation"] in ("functional", "hardware-diagnostic", "local-correctness", "pre-serve"))
    family = contract.get("gpu_family", "B200")
    require(family in ("B200", "B300") and (family == "B200" or contract["operation"] in ("local-correctness", "pre-serve")))
    probe = load("lab_profile_validation", path / "payload/probe.py", contract["sources"]["probe.py"])
    probe.security_profile(contract)
    require(probe.parallel_size(contract) == parallel_size(contract))
    if "tokenizer_parity" in contract:
        checker = load("lab_parity_profile", path / "payload/tokenizer-parity.py", contract["sources"]["tokenizer-parity.py"])
        require(checker.enabled(contract))
    if "tokenizer_materialization" in contract:
        adapter = load("lab_adapter_profile", path / "payload/tokenizer-adapter.py", contract["sources"]["tokenizer-adapter.py"])
        require(adapter.tokenizer_adapter_options(contract, require))
    config = recorder.raw_metadata(path / "metadata", contract)
    return receipt, contract, config, recorder


def create_command(name, owner, bundle, weights, contract, config):
    require(contract["operation"] in ("functional", "hardware-diagnostic", "local-correctness", "pre-serve"))
    payload = str(Path(bundle) / "payload")
    pre_serve = contract["operation"] == "pre-serve"
    replicas = parallel_size(contract)
    devices = ",".join(str(i) for i in range(4 * replicas))
    memory = str(8 if pre_serve else ENGINE_MEMORY_GIB * replicas) + "g"
    require((weights is None) == pre_serve)
    command = ["docker", "container", "create", "--name", name, "--label", LABEL + "=" + owner,
               "--pull", "never", "--platform", "linux/amd64", "--runtime", "nvidia",
               "--gpus", '"device=' + devices + '"', "--network", "none", "--cap-drop", "ALL",
               "--security-opt", "no-new-privileges", "--pids-limit", "1024",
               "--memory", memory, "--memory-swap", memory,
               "--cpus", "2" if pre_serve else "64",
               "--ipc", "private", "--shm-size", "2g", "--cgroupns", "private",
               "--restart", "no", "--log-driver", "none", "--no-healthcheck", "--user", "0:0",
               "--workdir", "/", "--tmpfs", "/cache:rw,nosuid,nodev,exec,size=16g,mode=0700",
               "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=256m,mode=1777",
               "--mount", "type=bind,src=" + payload + ",dst=/opt/nib,readonly",
               *([] if pre_serve else ["--mount", "type=bind,src=" + str(weights) + ",dst=/model,readonly"]),
               "--entrypoint", "/usr/bin/env"]
    names = set()
    constraints = {}
    for item in config["config"].get("Env", []):
        key, sep, value = item.partition("=")
        require(sep and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", key) and key not in names)
        names.add(key)
        if key.startswith("NVIDIA_REQUIRE_"):
            constraints[key] = value
    # Toolkit device injection is selected at the Docker boundary. Application
    # environment starts from env -i; host credentials/proxies never propagate.
    # Retain the verified image's vendor constraints for the NVIDIA prestart
    # hook; env -i runs only after that hook has enforced them.
    for key in sorted(names | {"PATH", "HOME", "NVIDIA_VISIBLE_DEVICES", "NVIDIA_DRIVER_CAPABILITIES"}):
        command += ["--env", key + "=" + ({**constraints, "NVIDIA_VISIBLE_DEVICES": devices,
                                          "NVIDIA_DRIVER_CAPABILITIES": "compute,utility"}.get(key, ""))]
    return command + [contract["base_image"], "-i", *[key + "=" + value for key, value in sorted(contract["environment"].items())],
                      "/usr/bin/python3.12", "-I", "-S", "-B", "/opt/nib/launch.py",
                      digest(read(Path(bundle) / "payload/lab.json")),
                      "diagnose-hardware" if contract["operation"] == "hardware-diagnostic" else "apply"]


def check_container(info, name, owner, bundle, weights, contract, config, image_id):
    pre_serve = contract["operation"] == "pre-serve"
    command = create_command(name, owner, bundle, weights, contract, config)
    cfg, host = info["Config"], info["HostConfig"]
    require(info["Image"] == image_id and cfg["Image"] == contract["base_image"]
            and cfg["Labels"].get(LABEL) == owner and cfg["Entrypoint"] == ["/usr/bin/env"]
            and cfg["Cmd"] == command[command.index(contract["base_image"]) + 1:]
            and cfg["User"] == "0:0" and cfg["WorkingDir"] == "/" and not cfg.get("Tty") and not cfg.get("OpenStdin"))
    env = [command[i + 1] for i, arg in enumerate(command) if arg == "--env"]
    require(len(cfg["Env"]) == len(env) and set(cfg["Env"]) == set(env))
    require(host["NetworkMode"] == "none" and not host["Privileged"] and host["Runtime"] == "nvidia"
            and host["ReadonlyRootfs"] is False
            and host["CapDrop"] == ["ALL"] and not host.get("CapAdd") and not host.get("Devices")
            and not host.get("DeviceCgroupRules") and not host.get("PortBindings") and not host.get("PublishAllPorts")
            and not host.get("PidMode") and not host.get("UTSMode") and not host.get("VolumesFrom") and not host.get("Binds")
            and host["IpcMode"] == "private" and host["ShmSize"] == 2 << 30 and host["CgroupnsMode"] == "private"
            and host["Memory"] == host["MemorySwap"] == (8 if pre_serve else ENGINE_MEMORY_GIB * parallel_size(contract)) << 30
            and host["NanoCpus"] == (2 if pre_serve else 64) * 1000000000
            and host["PidsLimit"] == 1024 and host["RestartPolicy"]["Name"] == "no" and host["LogConfig"]["Type"] == "none"
            and set(host["SecurityOpt"]) in ({"no-new-privileges"}, {"no-new-privileges=true"})
            and cfg["Healthcheck"]["Test"] == ["NONE"]
            and host["Tmpfs"] == {"/cache": "rw,nosuid,nodev,exec,size=16g,mode=0700",
                                 "/tmp": "rw,nosuid,nodev,noexec,size=256m,mode=1777"})
    devices = host["DeviceRequests"]
    require(len(devices) == 1 and devices[0]["DeviceIDs"] == [str(i) for i in range(4 * parallel_size(contract))]
            and devices[0]["Capabilities"] == [["gpu"]] and devices[0]["Count"] == 0
            and devices[0]["Driver"] in ("", "nvidia") and not devices[0].get("Options"))
    mounts = info["Mounts"]
    expected_mounts = {("bind", str(Path(bundle) / "payload"), "/opt/nib", False)}
    if not pre_serve:
        expected_mounts.add(("bind", str(weights), "/model", False))
    require(len(mounts) == len(expected_mounts))
    require({(m["Type"], m["Source"], m["Destination"], m["RW"]) for m in mounts}
            == expected_mounts)


def cleanup(recorder, name, owner, env):
    absent = ["docker", "container", "ls", "--all", "--quiet", "--filter", "name=^/" + name + "$"]
    if recorder.call(absent, env, timeout=10, limit=4096).strip():
        info = inspect(recorder, "container", name, env)
        require(info["Config"]["Labels"].get(LABEL) == owner)
        recorder.call(["docker", "container", "rm", "--force", name], env, timeout=30, limit=4096)
    require(not recorder.call(absent, env, timeout=10, limit=4096).strip())


def inspect(recorder, kind, name, env):
    return recorder.docker_json(kind, name, env, recorder.snapshot.execute_process_group)


def watchdog(bundle, sha, name, owner, deadline, life_fd, docker_host):
    require(re.fullmatch(r"nib-deepseek-[0-9a-f]{32}", name) and re.fullmatch(r"[0-9a-f]{32}", owner))
    _, _, _, recorder = bundle_inputs(bundle, sha)
    env = controlled_env(docker_host)
    remaining = max(0, deadline - time.time())
    select.select([life_fd], [], [], min(remaining, MAX_SECONDS))
    # Retry across the bounded create-client window: parent death can race a
    # request already accepted by Docker. Uncertain daemon completion is still
    # reported as unknown by the parent, never as successful cancellation.
    end = time.monotonic() + WATCHDOG_SETTLE_SECONDS
    while True:
        try:
            cleanup(recorder, name, owner, env)
        except Exception:
            if time.monotonic() >= end:
                raise ValueError("lab cleanup unconfirmed") from None
        if time.monotonic() >= end:
            break
        time.sleep(1)
    cleanup(recorder, name, owner, env)


def controlled_env(docker_host):
    require(type(docker_host) is str and re.fullmatch(r"unix:///[A-Za-z0-9_./-]+", docker_host))
    return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C",
            "DOCKER_HOST": docker_host, "DOCKER_CONFIG": "/nonexistent/nib-lab-docker-config"}


def container_observation(info, identifier, owner):
    require(info["Id"] == identifier and info["Config"]["Labels"].get(LABEL) == owner)
    state = info["State"]
    require(state["Status"] in ("created", "running", "paused", "restarting", "removing", "exited", "dead")
            and type(state["ExitCode"]) is int and -128 <= state["ExitCode"] <= 255
            and type(state["OOMKilled"]) is bool)
    return {"status": state["Status"], "exit_code": state["ExitCode"], "oom_killed": state["OOMKilled"]}


def run_container(bundle, sha, weights, deadline, recorder, contract, config, env):
    global CLEANING
    name, owner = "nib-deepseek-" + uuid.uuid4().hex, uuid.uuid4().hex
    report = {"result": "fail", "status": "BLOCKED", "deployable": False, "confidential": False,
              "functional_pass": False, "stage": "image", "cleanup": "not-created",
              "experiment_step": None, "container_observation": None, "output_bytes": None}
    monitor = None
    life_r, life_w = os.pipe()
    created = started = terminal = create_finished = False
    try:
        image = inspect(recorder, "image", contract["base_image"], env)
        report["image"] = recorder.image_binding(image, config, contract)
        require(not CANCELLED and deadline > time.time())
        monitor = subprocess.Popen([sys.executable, "-I", "-S", str(Path(__file__).absolute()), "--watchdog",
            str(bundle), sha, name, owner, str(deadline), str(life_r), env["DOCKER_HOST"]],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=env, pass_fds=(life_r,), start_new_session=True)
        report["stage"] = "create"
        require(not recorder.call(["docker", "container", "ls", "--all", "--quiet",
                                    "--filter", "name=^/" + name + "$"], env, timeout=10, limit=4096).strip())
        created = True
        identifier = recorder.call(create_command(name, owner, bundle, weights, contract, config), env).decode().strip()
        require(re.fullmatch(r"[0-9a-f]{64}", identifier))
        create_finished = True
        info = inspect(recorder, "container", name, env)
        check_container(info, name, owner, bundle, weights, contract, config, image["Id"])
        require(info["Id"] == identifier and info["State"]["Status"] == "created"
                and monitor.poll() is None and not CANCELLED)
        report["stage"] = "experiment"
        started = True
        report["experiment_step"] = "attach"
        raw = recorder.call(["docker", "container", "start", "--attach", name], env,
                            timeout=max(1, min(MAX_SECONDS, deadline - time.time())), limit=65536)
        report["output_bytes"] = len(raw)
        report["experiment_step"] = "decode"
        experiment = json.loads(raw)
        report["experiment_step"] = "terminal"
        final = inspect(recorder, "container", name, env)
        report["container_observation"] = container_observation(final, identifier, owner)
        require(final["Id"] == identifier and final["State"]["Status"] == "exited"
                and final["State"]["ExitCode"] == 0 and not final["State"].get("OOMKilled"))
        report["experiment_step"] = "validation"
        validate_result(experiment, bundle, contract, recorder)
        terminal = True
        report.update(experiment=experiment, functional_pass=experiment["functional_pass"],
                      stage="completed", experiment_step="completed")
    except Exception:
        report["failure"] = "bounded-lab-stage-failed"
    finally:
        CLEANING = True
        os.close(life_r)
        if started and report["container_observation"] is None:
            # Observe the owned container before cleanup changes its exit state.
            try:
                report["container_observation"] = container_observation(
                    inspect(recorder, "container", name, env), identifier, owner)
            except Exception:
                pass
        if created:
            report["cleanup"] = "unconfirmed"
            try:
                cleanup(recorder, name, owner, env)
                report["cleanup"] = "confirmed"
            except Exception:
                report["functional_pass"] = False
        os.close(life_w)
        if monitor is not None:
            try:
                monitor.wait(timeout=120)
                require(monitor.returncode == 0)
            except Exception:
                report["cleanup"] = "unconfirmed"
                report["functional_pass"] = False
                if monitor.poll() is None:
                    os.killpg(monitor.pid, signal.SIGKILL)
                    monitor.wait(timeout=10)
        if created and not create_finished:
            report["cleanup"] = "create-completion-unknown"
            report["functional_pass"] = False
        if started and not terminal:
            report["functional_pass"] = False
    return report


def validate_result(row, bundle, contract, recorder):
    if contract.get("operation") == "hardware-diagnostic":
        probe = load("lab_diagnostic_validation", Path(bundle) / "payload/probe.py", contract["sources"]["probe.py"])
        probe.validate_diagnostic(row)
        return
    correctness = contract.get("operation") == "local-correctness"
    parity = contract.get("tokenizer_parity") is True
    pre_serve = contract.get("operation") == "pre-serve"
    fields = {"schema", "status", "deployable", "confidential", "functional_pass",
              "stage", "application", "hardware", "completion", "startup", "engine_stopped"}
    expected_schema = "nib.deepseek-lab-result/v2"
    if "tokenizer_materialization" in contract:
        fields.add("tokenizer_materialization")
        require(parity and row.get("tokenizer_materialization") == contract["tokenizer_materialization"])
    if correctness:
        fields.add("tokenizer_parity" if parity else "correctness")
        expected_schema = "nib.deepseek-lab-tokenizer-parity/v1" if parity else "nib.deepseek-lab-correctness-result/v2"
    if pre_serve:
        fields.add("entry_pass")
        expected_schema = "nib.deepseek-lab-pre-serve/v1"
        require(type(row) is dict and type(row.get("entry_pass")) is bool and row.get("functional_pass") is False
                and row.get("completion") is None and row.get("stage") not in ("weights", "request"))
    require(type(row) is dict and set(row) == fields
            and row["schema"] == expected_schema and row["status"] == "BLOCKED"
            and row["deployable"] is False and row["confidential"] is False
            and type(row["functional_pass"]) is bool and type(row["engine_stopped"]) is bool
            and row["stage"] in ("hardware", "weights", "application", "prelaunch", "startup", "request", "complete"))
    startup = row["startup"]
    if row["stage"] in ("hardware", "weights", "application", "prelaunch"):
        require(startup is None)
    else:
        startup_fields = {"outcome", "wait_ms", "exit_code", "health_probes", "checkpoints", "exception_diagnostic"}
        require(type(startup) is dict
                and set(startup) in (startup_fields, startup_fields | {"worker_exception_diagnostic"}))
        errors = load("lab_exception_validation", Path(bundle) / "payload/exception.py",
                      contract["sources"]["exception.py"])
        errors.require = require
        errors.validate_exception(startup["exception_diagnostic"])
        require(startup["exception_diagnostic"] is None or startup["outcome"] == "child-exited")
        errors.validate_exception(startup.get("worker_exception_diagnostic"))
        require(startup.get("worker_exception_diagnostic") is None or startup["outcome"] == "child-exited")
        outcomes = ("launch-failed", "child-exited", "startup-timeout", "supervisor-failure")
        outcomes += ("prepared",) if pre_serve else ("models-rejected", "ready")
        require(startup["outcome"] in outcomes
                and type(startup["wait_ms"]) is int and 0 <= startup["wait_ms"] <= MAX_SECONDS * 1000
                and type(startup["health_probes"]) is int and 0 <= startup["health_probes"] <= MAX_SECONDS + 1)
        if pre_serve:
            require(startup["health_probes"] == 0)
        checkpoints = startup["checkpoints"]
        require(type(checkpoints) is dict and set(checkpoints) == {"status", "last_id"}
                and checkpoints["status"] in ("not-started", "eof", "incomplete", "invalid", "overflow", "read-failed")
                and (checkpoints["last_id"] is None
                     or type(checkpoints["last_id"]) is int and 1 <= checkpoints["last_id"] <= 17)
                and (checkpoints["status"] != "not-started" or checkpoints["last_id"] is None))
        if startup["outcome"] in ("child-exited", "prepared"):
            require(type(startup["exit_code"]) is int and -128 <= startup["exit_code"] <= 255)
        else:
            require(startup["exit_code"] is None)
        if startup["outcome"] == "launch-failed":
            require(startup["wait_ms"] == 0 and startup["health_probes"] == 0)
        if row["stage"] in ("request", "complete"):
            if pre_serve:
                require(startup["outcome"] == "prepared" and startup["exit_code"] == 0
                        and startup["health_probes"] == 0
                        and checkpoints == {"status": "eof", "last_id": 16})
            else:
                require(startup["outcome"] == "ready" and startup["health_probes"] > 0)
        else:
            require(startup["outcome"] != "ready" and row["functional_pass"] is False)
    if row["hardware"] is not None:
        probe = load("lab_hardware_validation", Path(bundle) / "payload/probe.py", contract["sources"]["probe.py"])
        if probe.security_profile(contract) == "nonconfidential":
            probe.validate_nonconfidential_report(row["hardware"], family=contract.get("gpu_family", "B200"))
        else:
            probe.validate_report(row["hardware"], family=contract.get("gpu_family", "B200"), expected=4 * parallel_size(contract))
    if row["application"] is not None:
        # Reuse the existing exact application-receipt validator unchanged. This
        # temporary validation envelope is not evidence of a P0 import attempt.
        validator = load("lab_application_validation", Path(bundle) / "support" / SMOKE,
                         contract["validation_source_sha256"])
        overlay = recorder.overlay
        raw_application = read(Path(bundle) / "payload/application/contract.json")
        require(digest(raw_application) == contract["application_contract_sha256"])
        application = overlay.load_json(raw_application)
        app = load("lab_measured_layout", Path(bundle) / "payload/application/application.py",
                   contract["sources"]["application/application.py"])
        measured = app.measurement(overlay, read(Path(bundle) / "payload/application/measurement.json"))
        validator.validate(overlay, {
            "schema": "nib.engine-import-smoke/v1", "status": "BLOCKED", "deployable": False,
            "image": None, "abi_validated": False, "hardware_validated": False, "inference_started": False,
            "stage": "import-smoke", "application": row["application"], "smoke": None,
            "failure": "bounded-smoke-stage-rejected",
            "phase_elapsed_ms": {"application-preflight": 0, "application": 0, "import-smoke": 0}},
            application, measured)
        # The writer hashes its complete pre-application report here, not only
        # its files. The measured files-only digest is enforced by app.inspect
        # before application; comparing these different domains rejects valid receipts.
    if row["completion"] is not None:
        value = row["completion"]
        require(type(value) is dict and set(value) == {"generated", "finish", "prompt_tokens", "completion_tokens",
                                                     "final_content_present", "synthetic_answer_match"}
                and value["generated"] is True and value["finish"] in ("stop", "length")
                and type(value["final_content_present"]) is bool and type(value["synthetic_answer_match"]) is bool
                and (not value["synthetic_answer_match"] or value["final_content_present"])
                and type(value["prompt_tokens"]) is int and 0 < value["prompt_tokens"] <= 4096
                and type(value["completion_tokens"]) is int and 0 < value["completion_tokens"] <= 128)
    if row["functional_pass"]:
        require(row["stage"] == "complete" and row["engine_stopped"] is True
                and type(row["application"]) is dict and row["application"].get("deployable") is False
                and type(row["hardware"]) is dict and row["hardware"].get("attestation_verified") is False)
        if not correctness:
            require(type(row["completion"]) is dict and row["completion"].get("generated") is True
                    and row["completion"]["finish"] == "stop"
                    and row["completion"]["final_content_present"] is True
                    and row["completion"]["synthetic_answer_match"] is True)
    if correctness:
        require(row["completion"] is None)
        field = "tokenizer_parity" if parity else "correctness"
        if row[field] is not None:
            checker = load("lab_correctness_validation", Path(bundle) / "payload/correctness.py",
                           contract["sources"]["correctness.py"])
            if parity:
                parity_checker = load("lab_parity_validation", Path(bundle) / "payload/tokenizer-parity.py",
                                      contract["sources"]["tokenizer-parity.py"])
                require(parity_checker.enabled(contract))
                parity_checker.validate(row[field], checker)
            else:
                checker.validate(row[field])
            require(row["hardware"] is not None and row["application"] is not None)
        if row["functional_pass"]:
            require(row[field] is not None and row[field]["all_cases_pass"])
    require(row["hardware"] is not None or (row["application"] is None and row["completion"] is None))
    require(row["application"] is not None or row["completion"] is None)
    if pre_serve:
        require(row["entry_pass"] == (row["stage"] == "complete" and row["engine_stopped"] is True
                                     and row["hardware"] is not None and row["application"] is not None))
    require(len(json.dumps(row)) <= 60000)


def cancel(*_):
    global CANCELLED
    was_cancelled = CANCELLED
    CANCELLED = True
    if not was_cancelled and not CLEANING:
        raise InterruptedError("lab cancelled")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--watchdog":
        require(len(sys.argv) == 9)
        watchdog(Path(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5],
                 float(sys.argv[6]), int(sys.argv[7]), sys.argv[8])
        return 0
    class QuietParser(argparse.ArgumentParser):
        def error(self, message):
            raise ValueError("lab arguments rejected")
    parser = QuietParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    preparations = {
        "prepare": ("functional", "B200", "confidential"),
        "prepare-hardware-diagnostic": ("hardware-diagnostic", "B200", "confidential"),
        "prepare-local-correctness": ("local-correctness", "B200", "confidential"),
        "prepare-local-correctness-b300": ("local-correctness", "B300", "confidential"),
        "prepare-local-correctness-b300-dp2": ("local-correctness", "B300", "confidential", 2),
        "prepare-pre-serve": ("pre-serve", "B200", "confidential"),
        "prepare-pre-serve-b300": ("pre-serve", "B300", "confidential"),
        "prepare-nonconfidential-baseline": ("local-correctness", "B200", "nonconfidential"),
        "prepare-nonconfidential-baseline-b300": ("local-correctness", "B300", "nonconfidential"),
    }
    for option in preparations:
        action.add_argument("--" + option)
    action.add_argument("--run", metavar="PRIVATE_BUNDLE")
    parser.add_argument("--tokenizer-parity", action="store_true",
                        help="Prepare the fixed B300 CC tokenizer prerequisite instead of correctness cases.")
    parser.add_argument("--tokenizer-materialization", action="store_true",
                        help="Prepare the pinned server validator adapter; requires --tokenizer-parity.")
    parser.add_argument("--receipt-sha256")
    parser.add_argument("--weights")
    parser.add_argument("--output")
    parser.add_argument("--deadline", help="Required UTC hard cutoff, YYYY-MM-DDTHH:MM:SSZ, at most 80 minutes away.")
    args = parser.parse_args()
    require(not args.tokenizer_materialization or args.tokenizer_parity)
    for option, profile in preparations.items():
        destination = getattr(args, option.replace("-", "_"))
        if destination:
            require(not any((args.receipt_sha256, args.weights, args.output, args.deadline)))
            if args.tokenizer_parity:
                require(profile[:3] == ("local-correctness", "B300", "confidential"))
                optional = {"tokenizer_materialization": True} if args.tokenizer_materialization else {}
                prepare(destination, *profile, tokenizer_parity=True, **optional)
            else:
                prepare(destination, *profile)
            return 0
    require(not args.tokenizer_parity and not args.tokenizer_materialization)
    require(sys.platform == "linux" and all((args.receipt_sha256, args.output, args.deadline)))
    deadline = dt.datetime.strptime(args.deadline, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp()
    require(0 < deadline - time.time() <= MAX_SECONDS)
    bundle, output = (Path(p) for p in (args.run, args.output))
    weights = Path(args.weights) if args.weights else None
    paths = (bundle, output) + ((weights,) if weights is not None else ())
    require(all(p.is_absolute() and p == p.resolve() for p in paths)
            and not os.path.lexists(output) and not any(a == b or a in b.parents or b in a.parents
                                                       for i, a in enumerate(paths) for b in paths[i + 1:]))
    receipt, contract, config, recorder = bundle_inputs(bundle, args.receipt_sha256)
    require((weights is None) == (contract["operation"] == "pre-serve"))
    if contract["operation"] == "pre-serve":
        require(deadline - time.time() <= 1080)
    if contract["operation"] == "hardware-diagnostic":
        require(deadline - time.time() <= 300)
    signal.signal(signal.SIGTERM, cancel)
    signal.signal(signal.SIGINT, cancel)
    started = recorder.now()
    # Fixed local Docker socket: no account config, remote context, plugins or credentials.
    env = controlled_env("unix:///var/run/docker.sock")
    report = run_container(bundle, args.receipt_sha256, weights, deadline, recorder, contract, config, env)
    report["provenance"] = {"git_sha": receipt["git_sha"], "source_bundle_sha256": receipt["source_bundle_sha256"],
                            "payload_sha256": receipt["payload_sha256"], "receipt_sha256": args.receipt_sha256,
                            "image": contract["base_image"], "config_sha256": contract["official_config"]["sha256"],
                            "manifest_sha256": contract["manifest_sha256"], "pack_revision": contract["pack_revision"]}
    hardware_row = report.get("experiment", {}).get("hardware")
    diagnostic = contract["operation"] == "hardware-diagnostic"
    record = {"schema": "nib.evidence/v1", "phase": "P2", "task": "T3", "git_sha": receipt["git_sha"],
              "started_utc": started, "ended_utc": recorder.now(), "result": "fail",
              "execution": "hardware" if hardware_row else "software", "hardware_validated": False,
              "host": {"provider": "spheron", "machine": "approved-isolated-lab",
                       "gpus": (("eight" if parallel_size(contract) == 2 else "four") + "-selected-"
                                + hardware_row["family"]) if hardware_row else "unobserved",
                       "driver": hardware_row["driver"] if hardware_row else None,
                       "cuda": hardware_row["cuda_driver"] if hardware_row else None},
              "command": "python3 -I -S host/record-deepseek-lab.py --run <private-bundle> --receipt-sha256 <digest> --weights <private-weights> --output <private-evidence> --deadline <approved-UTC-cutoff>",
              "notes": "Partial Spheron functional experiment, not GCP gates or full P2 acceptance. "
                       "Candidate BLOCKED; deployable=false; confidential=false; no attestation or ABI qualification. "
                       "Generated JIT code is private output, not verified native provenance. "
                       "CUDA field is the observed driver-supported CUDA API version, not a runtime measurement."}
    if contract.get("gpu_security") == "nonconfidential":
        record["notes"] += " Explicit nonconfidential baseline; CC-disabled observations are not confidential evidence."
    if diagnostic:
        record["notes"] = ("Denial-only read-only hardware diagnostic; no weights, application, engine or inference. "
                           "Fixed call stages, return codes and bounded numeric observations only. "
                           "All original CC/readiness policy checks retained; no settings changes. "
                           "Candidate BLOCKED; deployable=false; confidential=false; not attestation or qualification.")
    if contract["operation"] == "pre-serve":
        record["notes"] = ("GPU pre-serve diagnostic only: same image application, imports and argument validation. "
                           "No model mount, weight validation, serve.cmd, HTTP probes or inference. "
                           "Candidate BLOCKED; no confidential or ABI qualification.")
        record["command"] = record["command"].replace(" --weights <private-weights>", "")
    with tempfile.TemporaryDirectory(prefix="nib-lab-evidence-") as temporary:
        recorder.publish_evidence(record, report, bundle / "payload/nib", Path(temporary).resolve(), output, env)
    print(json.dumps({"recorded": True, "functional_pass": report["functional_pass"],
                      "status": "BLOCKED", "confidential": False}, sort_keys=True))
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit("Lab operation rejected; no successful qualification may be inferred.")
