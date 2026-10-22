#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prepare offline or explicitly run one scripted, closed-network tau trial."""

import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    sys.exit("Use python3 -I -S -B scripts/tau_trial.py")

import argparse
import builtins
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import tempfile
import time
import types
import uuid
import zipfile

ROOT = Path(__file__).absolute().parent.parent
SCRIPT = "scripts/tau_trial.py"
WORKER = "scripts/tau_trial_worker.py"
RUNTIME_SHA = "1c6cd5cc2aa3cbfe3a49f5bbc4c920281fbdd9cd40e83334c93dec010437ffc9"
LABEL = "nib.tau-scripted-trial"
SECONDS = 300
TYPER_SHARED = frozenset(("__init__.py", "__main__.py", "_completion_classes.py",
                         "_completion_shared.py", "_types.py", "_typing.py", "cli.py", "colors.py",
                         "completion.py", "core.py", "main.py", "models.py", "params.py", "py.typed",
                         "rich_utils.py", "testing.py", "utils.py"))
ENVIRONMENT = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/scratch", "LANG": "C.UTF-8",
               "LC_ALL": "C.UTF-8", "TMPDIR": "/scratch", "PYTHON_DOTENV_DISABLED": "1",
               "LITELLM_LOCAL_MODEL_COST_MAP": "True", "TAU2_DATA_DIR": "/opt/nib/upstream/data",
               "HF_HUB_OFFLINE": "1", "HF_HOME": "/scratch/hf", "TIKTOKEN_CACHE_DIR": "/scratch/tiktoken"}


def require(value):
    if not value:
        raise ValueError("tau trial rejected")


def load(name, expected=None, *, modules=None):
    path = ROOT / name
    # Only own committed helpers; upstream code is never loaded in the host.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= 1 << 20)
        raw = stream.read((1 << 20) + 1)
        after = os.fstat(stream.fileno())
        require(len(raw) == before.st_size and
                (before.st_dev, before.st_ino, before.st_mode, before.st_nlink, before.st_size,
                 before.st_mtime_ns, before.st_ctime_ns) ==
                (after.st_dev, after.st_ino, after.st_mode, after.st_nlink, after.st_size,
                 after.st_mtime_ns, after.st_ctime_ns))
    require(expected is None or hashlib.sha256(raw).hexdigest() == expected)
    module = types.ModuleType(name)
    module.__file__ = str(path)
    if modules is not None:
        def source_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name in modules:
                require(level == 0)
                return modules[name]
            return builtins.__import__(name, globals, locals, fromlist, level)
        module.__dict__["__builtins__"] = {**vars(builtins), "__import__": source_import}
    exec(compile(raw, str(path), "exec", dont_inherit=True), module.__dict__)
    return module


def parent_driver(toolkit, *, live_local=False):
    # Compile the exact verified closure, never timestamp-valid .pyc files or
    # preexisting same-name modules from an ambient import path.
    modules = {}
    names = ("tau_preflight", "tau_trial_worker", "tau_aggregate", "tau_transport")
    if live_local:
        names += ("tau_local_vllm", "tau_live_identity")
    for name in (*names, "tau_pipe_driver"):
        path = "scripts/" + name + ".py"
        modules[name] = load(path, toolkit["files"][path], modules=modules)
    driver = modules["tau_pipe_driver"]
    if live_local:
        driver.live = modules["tau_live_identity"]
    return driver


runtime = load("scripts/tau_runtime.py", RUNTIME_SHA)
tau = runtime.tau
aggregate = load("scripts/tau_aggregate.py")
worker = load(WORKER)
probe = load("host/probe.py")
IMAGE_DIGEST = runtime.TARGET["image"].split("@")[1]


def manifest_identity(descriptor):
    require(type(descriptor) is dict
            and descriptor.get("mediaType") == "application/vnd.oci.image.manifest.v1+json"
            and type(descriptor.get("digest")) is str
            and re.fullmatch("sha256:[0-9a-f]{64}", descriptor["digest"])
            and type(descriptor.get("size")) is int and 0 < descriptor["size"] <= 1 << 20
            and descriptor.get("platform") == {"os": "linux", "architecture": "amd64"})
    return {key: descriptor[key] for key in ("mediaType", "digest", "size", "platform")}


def check_image(image):
    selected = manifest_identity(image["Descriptor"])
    references = {name + "@" + IMAGE_DIGEST for name in
                  ("python", "library/python", "docker.io/library/python")}
    require(image["Os"] == "linux" and image["Architecture"] == "amd64"
            and image["Id"] == selected["digest"]
            and type(image["RepoDigests"]) is list
            and any(type(item) is str and item in references for item in image["RepoDigests"])
            and not image["Config"].get("Volumes"))
    return selected


def identity(deadline, parent_pipe=False, *, live_local=False):
    require(not live_local or parent_pipe)
    names = (SCRIPT, WORKER, "scripts/tau_runtime.py", "scripts/tau_aggregate.py",
             runtime.CATALOG, "host/probe.py", "scripts/test_tau_trial.py", "scripts/e2e/pins.json",
             "schemas/evidence.schema.json", "schemas/common.schema.json")
    if parent_pipe:
        names += ("scripts/tau_transport.py", "scripts/tau_pipe_trial.py", "scripts/tau_pipe_driver.py",
                  "scripts/test_tau_pipe_driver.py")
    if live_local:
        names += ("scripts/tau_live_trial.py", "scripts/tau_live_identity.py", "scripts/tau_local_vllm.py",
                  "scripts/test_tau_live_trial.py", "scripts/test_tau_local_vllm.py")
    result = tau.committed_identity(deadline)
    for start in range(0, len(names), 4):
        item = tau.committed_identity(deadline, extra_files=names[start:start + 4])
        require(item["git_sha"] == result["git_sha"])
        result["files"].update(item["files"])
    return result


def select(manifest, domain, task_id, trial, role):
    require(role in ("candidate", "reference") and domain in tau.DOMAINS
            and task_id in manifest["domains"][domain]["task_ids"]
            and type(trial) is int and 0 <= trial < manifest["trials_per_task"])
    return {"domain": domain, "task_id": task_id, "trial": trial,
            "seed": manifest["trial_seeds"][trial], "role": role}


def add_wheel(files, owners, raw, package, expected, deadline):
    runtime.check_wheel(raw, expected, deadline)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for row in archive.infolist():
            require(time.monotonic() < deadline)
            if row.is_dir():
                continue
            name = row.filename
            runtime.safe_name(name)
            parts = name.split("/")
            require(not name.endswith((".pyc", ".pyo", ".pth", ".egg-link"))
                    and not any(p.endswith(".data") for p in parts)
                    and not any(p in ("sitecustomize", "usercustomize", "sitecustomize.py", "usercustomize.py")
                                for p in parts))
            data = archive.read(row)
            target = "packages/" + name
            if target in files:
                require({owners[target], package} == {"typer", "typer-slim"}
                        and len(parts) == 2 and parts[0] == "typer" and parts[1] in TYPER_SHARED
                        and data == files[target])
            else:
                files[target], owners[target] = data, package
    require(len(files) <= worker.MAX_FILES and sum(map(len, files.values())) <= worker.MAX_PAYLOAD)


def prepare(archive_path, wheelhouse, manifest_path, manifest_sha, selection, deadline, *, parent_pipe=False,
            live_binding=None):
    toolkit = (identity(deadline, parent_pipe, live_local=True) if live_binding is not None
               else identity(deadline, parent_pipe))
    pins_raw = runtime.read_file(ROOT / tau.PINS, 1 << 20, deadline)
    require(tau.digest(pins_raw) == tau.PINS_SHA)
    pins = tau.json_value(pins_raw)
    raw = runtime.read_file(manifest_path, 1 << 20, deadline)
    manifest = tau.json_value(raw)
    aggregate.validate_manifest(manifest, manifest_sha, pins)
    chosen = select(manifest, **selection)
    archive = runtime.read_file(archive_path, runtime.ARCHIVE_LIMIT, deadline)
    catalog_raw = runtime.read_file(ROOT / runtime.CATALOG, 1 << 20, deadline)
    require(tau.digest(catalog_raw) == runtime.CATALOG_SHA)
    catalog = tau.json_value(catalog_raw)
    source_rows, sources = runtime.source_inputs(archive, pins, deadline)
    require(runtime.make_catalog(source_rows, sources) == catalog)
    rebuilt = tau.preflight(Path("/verified"), manifest["settings"], pins, manifest["toolkit"],
                            reader=lambda path, limit, end: sources[path.relative_to("/verified").as_posix()],
                            deadline=deadline)
    require(rebuilt == manifest)
    files = {"upstream/" + name: data for name, data in sources.items()}
    owners = {}
    for package, row in catalog["wheels"].items():
        raw = runtime.read_file(wheelhouse / row["filename"], runtime.WHEEL_LIMIT, deadline)
        add_wheel(files, owners, raw, package, row, deadline)
    for name, sha in toolkit["files"].items():
        raw = runtime.read_file(ROOT / name, 1 << 20, deadline)
        require(tau.digest(raw) == sha)
        files[name] = raw
    files["manifest.json"] = tau.encoded(manifest)
    receipt = {"schema": "nib.tau-scripted-inputs/v1", "toolkit": toolkit,
               "runtime_lock_sha256": runtime.CATALOG_SHA, "source_archive_sha256": runtime.ARCHIVE_SHA,
               "target": runtime.TARGET, "selected_wheels": len(catalog["wheels"]),
               "source_files": len(source_rows), "root_mode": catalog["root"]["mode"],
               "dependencies_installed": False, "upstream_executed": False, "live_transport": False}
    files["inputs.json"] = tau.encoded(receipt)
    if live_binding is not None:
        require(parent_pipe)
        files["live-binding.json"] = tau.encoded(live_binding)
    contract = {"schema": "nib.tau-scripted-plan/v1", "image": runtime.TARGET["image"],
                "platform": runtime.TARGET["platform"], "manifest_sha256": manifest_sha,
                "selection": chosen, "environment": ENVIRONMENT, "seconds": SECONDS,
                "producer": {"git_sha": toolkit["git_sha"], "adapter_sha256": toolkit["files"][WORKER],
                             "runtime_receipt_sha256": tau.digest(files["inputs.json"])},
                "files": {name: {"bytes": len(data), "sha256": tau.digest(data)}
                          for name, data in sorted(files.items())}}
    if parent_pipe:
        contract["transport"] = "scripted-parent-pipe"
    files["trial.json"] = tau.encoded(contract)
    require(len(files["trial.json"]) <= 4 << 20 and time.monotonic() < deadline)
    return files, contract


def publish(destination, files):
    """Exclusive private parent; readable bind subtree without DAC capabilities."""
    destination = Path(destination)
    with runtime.directory(destination.parent) as parent:
        os.mkdir(destination.name, 0o700, dir_fd=parent)
    payload = destination / "payload"
    payload.mkdir(mode=0o700)
    for name, raw in files.items():
        runtime.safe_name(name)
        path = payload / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o444)
    for root, dirs, names in os.walk(payload, topdown=False):
        with runtime.directory(root) as fd:
            os.fchmod(fd, 0o555)


def validate_plan(payload, sha, toolkit, deadline, *, parent_pipe=False, live_local=False):
    require(not live_local or parent_pipe)
    with runtime.directory(payload.parent) as parent:
        info = os.fstat(parent)
        require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)
    raw = runtime.read_file(payload / "trial.json", 4 << 20, deadline)
    require(tau.digest(raw) == sha)
    plan = tau.json_value(raw)
    expected_keys = {"schema", "image", "platform", "manifest_sha256",
                     "selection", "environment", "seconds", "producer", "files"}
    if parent_pipe:
        expected_keys.add("transport")
    require(type(plan) is dict and set(plan) == expected_keys
            and plan["schema"] == "nib.tau-scripted-plan/v1" and plan["image"] == runtime.TARGET["image"]
            and plan["platform"] == runtime.TARGET["platform"] and plan["environment"] == ENVIRONMENT
            and plan["seconds"] == SECONDS and plan["producer"]["git_sha"] == toolkit["git_sha"]
            and plan["producer"]["adapter_sha256"] == toolkit["files"][WORKER])
    require(not parent_pipe or plan["transport"] == "scripted-parent-pipe")
    require(("live-binding.json" in plan["files"]) is live_local)
    for name, expected in toolkit["files"].items():
        require(plan["files"][name]["sha256"] == expected)
    worker.verify_tree(payload, plan["files"], deadline)
    manifest = tau.json_value(runtime.read_file(payload / "manifest.json", 1 << 20, deadline))
    pins = tau.json_value(runtime.read_file(ROOT / tau.PINS, 1 << 20, deadline))
    aggregate.validate_manifest(manifest, plan["manifest_sha256"], pins)
    selection = plan["selection"]
    require(selection == select(manifest, selection["domain"], selection["task_id"],
                                selection["trial"], selection["role"]))
    require(plan["producer"]["runtime_receipt_sha256"] ==
            tau.digest(runtime.read_file(payload / "inputs.json", 1 << 20, deadline)))
    return plan, manifest


def create_command(name, owner, payload, sha, *, parent_pipe=False):
    return ["create", *(["--interactive"] if parent_pipe else []),
            "--name", name, "--label", LABEL + "=" + owner, "--pull", "never",
            "--platform", "linux/amd64", "--runtime", "runc", "--user", "65534:65534",
            "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--memory", "4g", "--memory-swap", "4g", "--cpus", "2", "--pids-limit", "64",
            "--ipc", "private", "--cgroupns", "private", "--shm-size", "16m", "--restart", "no",
            "--log-driver", "none", "--no-healthcheck", "--workdir", "/scratch",
            "--mount", "type=bind,src=" + str(payload) + ",dst=/opt/nib,readonly",
            "--tmpfs", "/scratch:rw,nosuid,nodev,noexec,size=256m,mode=0700,uid=65534,gid=65534",
            "--entrypoint", "/usr/bin/env", runtime.TARGET["image"], "-i",
            *[key + "=" + value for key, value in sorted(ENVIRONMENT.items())],
            "/usr/local/bin/python3", "-I", "-S", "-B", "/opt/nib/" + WORKER, sha,
            *(["--parent-pipe"] if parent_pipe else [])]


def check_container(info, name, owner, payload, sha, selected_manifest, *, parent_pipe=False):
    cfg, host = info["Config"], info["HostConfig"]
    command = create_command(name, owner, payload, sha, parent_pipe=parent_pipe)
    # Moby containerd keeps the outer index in Image, not the selected manifest.
    # See the exact daemon/CLI source chain in p2-t3-tau-single-trial.md.
    require(info["Image"] == IMAGE_DIGEST and info["Platform"] == "linux"
            and manifest_identity(info["ImageManifestDescriptor"]) == selected_manifest)
    require(info["Name"] == "/" + name
            and cfg["Image"] == runtime.TARGET["image"] and cfg["Labels"].get(LABEL) == owner
            and cfg["Entrypoint"] == ["/usr/bin/env"]
            and cfg["Cmd"] == command[command.index(runtime.TARGET["image"]) + 1:]
            and cfg["User"] == "65534:65534" and cfg["WorkingDir"] == "/scratch"
            and cfg["Healthcheck"]["Test"] == ["NONE"] and not cfg["Tty"]
            and cfg["OpenStdin"] is parent_pipe)
    require(host["NetworkMode"] == "none" and host["ReadonlyRootfs"] is True
            and host["Runtime"] == "runc" and host["CapDrop"] == ["ALL"] and not host["Privileged"]
            and not host.get("CapAdd") and not host.get("Devices") and not host.get("DeviceRequests")
            and not host.get("DeviceCgroupRules") and not host.get("PortBindings") and not host.get("PublishAllPorts")
            and not host.get("PidMode") and not host.get("UTSMode") and not host.get("VolumesFrom")
            and not host.get("Binds") and host["IpcMode"] == host["CgroupnsMode"] == "private"
            and host["Memory"] == host["MemorySwap"] == 4 << 30 and host["NanoCpus"] == 2000000000
            and host["PidsLimit"] == 64 and host["ShmSize"] == 16 << 20
            and host["RestartPolicy"]["Name"] == "no" and host["LogConfig"]["Type"] == "none"
            and set(host["SecurityOpt"]) in ({"no-new-privileges"}, {"no-new-privileges=true"}))
    require(len(info["Mounts"]) == 1 and info["Mounts"][0]["Type"] == "bind"
            and info["Mounts"][0]["Source"] == str(payload) and info["Mounts"][0]["Destination"] == "/opt/nib"
            and info["Mounts"][0]["RW"] is False
            and host["Tmpfs"] == {"/scratch": "rw,nosuid,nodev,noexec,size=256m,mode=0700,uid=65534,gid=65534"})


def check_result(value, plan, manifest, *, live_local=False):
    require(type(value) is dict and set(value) == {"schema", "stage", "completed", "report", "accounting",
            "quality_accepted", "confidential", "live_transport", "token_usage", "upstream_executed"}
            and value["schema"] == ("nib.tau-live-trial/v1" if live_local else "nib.tau-scripted-trial/v1")
            and value["stage"] in ("verification", "trial", "completed")
            and value["token_usage"] == ("engine-reported" if live_local else "synthetic-not-tokenized")
            and value["live_transport"] is live_local
            and all(value[key] is False for key in ("quality_accepted", "confidential")))
    require(type(value["completed"]) is bool and type(value["upstream_executed"]) is bool)
    if not value["completed"]:
        require(value["stage"] != "completed" and value["report"] is value["accounting"] is None)
        return
    require(value["stage"] == "completed")
    report = value["report"]
    aggregate.validate_report(report, manifest, plan["manifest_sha256"], plan["selection"]["role"])
    require(report["kind"] == ("declared-unverified" if live_local else "synthetic-fixture")
            and report["producer"] == plan["producer"]
            and len(report["trials"]) == 1
            and report["binding"] == aggregate.expected_binding(manifest, plan["selection"]["role"]))
    row = report["trials"][0]
    require(all(row[key] == plan["selection"][key] for key in ("domain", "task_id", "trial", "seed")))
    accounting = value["accounting"]
    require(type(accounting) is dict and set(accounting) == {"role_requests", "reserved_tokens", "reserved_cost_usd",
                                                           "usage_known"}
            and type(accounting["role_requests"]) is dict
            and set(accounting["role_requests"]) == {"agent", "simulator", "evaluator"}
            and type(accounting["usage_known"]) is bool
            and accounting["usage_known"] is (row["usage"] is not None))
    for count in accounting["role_requests"].values():
        tau.integer(count, 0, 100000)
    require(sum(accounting["role_requests"].values()) == row["requests"])
    tau.integer(accounting["reserved_tokens"], 0, 100000000)
    tau.amount(accounting["reserved_cost_usd"])
    require(row["requests"] <= manifest["settings"]["limits"]["requests"]
            and accounting["reserved_tokens"] <= manifest["settings"]["limits"]["total_tokens"]
            and tau.amount(accounting["reserved_cost_usd"]) <= tau.amount(manifest["settings"]["limits"]["cost_usd"]))
    if row["usage"] is not None:
        require(row["usage"]["total_tokens"] == accounting["reserved_tokens"]
                and row["usage"]["cost_usd"] == accounting["reserved_cost_usd"])
    if row["status"] == "evaluated":
        require(value["upstream_executed"] and accounting["usage_known"] and accounting["role_requests"]["agent"] > 0
                and accounting["role_requests"]["simulator"] > 0)


def run(payload, sha, destination, socket, *, parent_pipe=False, live_deployment=None):
    deadline = time.monotonic() + 60
    live_local = live_deployment is not None
    toolkit = (identity(deadline, parent_pipe, live_local=True) if live_local else identity(deadline, parent_pipe))
    options = {"parent_pipe": parent_pipe, **({"live_local": True} if live_local else {})}
    plan, manifest = validate_plan(payload, sha, toolkit, deadline, **options)
    deployment = driver = None
    if live_local:
        driver = parent_driver(toolkit, live_local=True)
        deployment = driver.live.Deployment(*live_deployment, manifest, deadline)
        require(tau.json_value(runtime.read_file(payload / "live-binding.json", 1 << 20, deadline))
                == deployment.binding)
        deployment.check(deadline)
    require(payload.is_absolute() and not any(c in str(payload) for c in (",", "\n", "\r")))
    require(socket.is_absolute() and stat.S_ISSOCK(socket.stat().st_mode))
    require(not destination.exists())
    owner = uuid.uuid4().hex
    name = "nib-tau-" + owner
    started = now()
    result, stage, cleaned, create_unknown = None, "image", False, False
    with tempfile.TemporaryDirectory(prefix="nib-tau-docker-") as private:
        config = Path(private) / "config.json"
        config.write_text('{"auths":{"https://index.docker.io/v1/":{}}}\n')
        config.chmod(0o600)
        env = {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin", "HOME": private,
               "DOCKER_CONFIG": private, "LANG": "C", "LC_ALL": "C"}
        docker = shutil.which("docker", path=env["PATH"])
        require(docker is not None)
        operation_deadline = time.monotonic() + SECONDS
        def command(*args, seconds=20, limit=65536):
            remaining = operation_deadline - time.monotonic()
            require(remaining > 0)
            return probe.capture([docker, "--host", "unix://" + str(socket), "--config", private, *args],
                                 timeout=min(seconds, remaining), env=env, limit=limit)
        old = signal.getsignal(signal.SIGTERM)
        def cancelled(signum, frame):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, cancelled)
        try:
            rows = tau.json_value(command("image", "inspect", "--platform", plan["platform"],
                                          runtime.TARGET["image"]))
            require(type(rows) is list and len(rows) == 1)
            selected_manifest = check_image(rows[0])
            stage = "create"
            # Killing the bounded client cannot cancel an in-flight daemon create.
            # Only the exact owned-container inspection establishes completion.
            create_unknown = True
            command(*create_command(name, owner, payload, sha, parent_pipe=parent_pipe))
            info = tau.json_value(command("inspect", name))[0]
            check_container(info, name, owner, payload, sha, selected_manifest, parent_pipe=parent_pipe)
            create_unknown = False
            stage = "trial"
            if parent_pipe:
                observed = (driver or parent_driver(toolkit)).run_process(
                    [docker, "--host", "unix://" + str(socket), "--config", private,
                     "start", "--attach", "--interactive", name], env, payload, plan, manifest,
                    min(operation_deadline, time.monotonic() + worker.SECONDS),
                    **({"deployment": deployment} if live_local else {}))
            else:
                raw = command("start", "--attach", name, seconds=SECONDS, limit=16384)
                observed = tau.json_value(raw)
            check_result(observed, plan, manifest, **({"live_local": True} if live_local else {}))
            # Preserve validated accounting even if the later container check fails.
            result = observed
            terminal = tau.json_value(command("inspect", name))[0]
            check_container(terminal, name, owner, payload, sha, selected_manifest, parent_pipe=parent_pipe)
            require(terminal["State"]["Running"] is False and terminal["State"]["OOMKilled"] is False
                    and terminal["State"]["ExitCode"] == 0)
            if deployment is not None:
                deployment.check(operation_deadline)
            stage = "completed"
        except BaseException:
            pass
        finally:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            operation_deadline = time.monotonic() + 60
            try:
                names = command("ps", "-aq", "--filter", "label=" + LABEL + "=" + owner).decode().split()
                require(len(names) <= 1 and all(re.fullmatch("[0-9a-f]{12,64}", item) for item in names))
                if names:
                    command("rm", "--force", *names)
                empty = not command("ps", "-aq", "--filter", "label=" + LABEL + "=" + owner).strip()
                cleaned = empty and not create_unknown
            except Exception:
                cleaned = False
            signal.signal(signal.SIGTERM, old)
    record = {"schema": "nib.tau-live-run/v1" if live_local else "nib.tau-scripted-run/v1",
              "toolkit": toolkit, "plan_sha256": sha,
              "image": plan["image"], "platform": plan["platform"], "execution": "software",
              "started_utc": started, "ended_utc": now(),
              "stage": stage, "cleanup_confirmed": cleaned, "diagnostic": result,
              "cleanup": "create-completion-unknown" if create_unknown else "confirmed" if cleaned else "failed",
              "result": "pass" if stage == "completed" and cleaned and result and result["completed"]
                        and result["report"]["trials"][0]["status"] == "evaluated" else "fail",
              "confidential": False, "quality_accepted": False, "live_transport": live_local}
    envelope = evidence_record(toolkit["git_sha"], sha, started, record, parent_pipe=parent_pipe)
    if live_local:
        record["deployment"] = deployment.binding
        envelope.update(command="python3 -I -S -B scripts/tau_live_trial.py run --payload PRIVATE "
            "--plan-sha256 " + sha + " --docker-socket PRIVATE --output PRIVATE --deployment PRIVATE "
            "--deployment-sha256 " + deployment.binding["deployment_sha256"],
            host={"provider": "local", "machine": "isolated-scorer-with-local-engines",
                  "gpus": "unobserved", "driver": None, "cuda": None},
            notes="Explicit local live single-trial smoke. Engine-reported usage, no quality or confidential "
                  "acceptance. Private deployment receipts are operator-authenticated inputs, not attestation.")
    publish_evidence(destination, {"run.json": tau.encoded(envelope), "output.log": tau.encoded(record)})
    print(tau.encoded({"result": record["result"], "cleanup_confirmed": cleaned}).decode(), end="")
    return 0 if record["result"] == "pass" else 1


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def evidence_record(revision, sha, started, record, *, parent_pipe=False):
    return {"schema": "nib.evidence/v1", "phase": "P2", "task": "T3", "git_sha": revision,
                "command": "python3 -I -S -B scripts/tau_trial.py run --payload PRIVATE "
                           "--plan-sha256 " + sha + " --docker-socket PRIVATE --output PRIVATE"
                           + (" --parent-pipe" if parent_pipe else ""),
                "started_utc": started, "ended_utc": record["ended_utc"],
                "host": {"provider": "local", "machine": "isolated-linux-amd64-container",
                         "gpus": "none", "driver": None, "cuda": None},
                "result": record["result"], "execution": "software", "hardware_validated": False,
            "notes": "Scripted single-trial integration only. Synthetic usage, no model endpoint, "
                     "no quality or confidential acceptance. output.log contains fixed/numeric observations only."}


def publish_evidence(destination, files):
    require(set(files) == {"run.json", "output.log"} and sum(map(len, files.values())) <= 1 << 20)
    with runtime.directory(destination.parent) as parent:
        os.mkdir(destination.name, 0o700, dir_fd=parent)
        fd = os.open(destination.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            for name, raw in files.items():
                child = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
                with os.fdopen(child, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
            os.fsync(fd)
        finally:
            os.close(fd)


def main():
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            raise ValueError("tau trial arguments rejected")
    parser = Parser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True, parser_class=Parser)
    prep = sub.add_parser("prepare")
    for name in ("source-archive", "wheelhouse", "manifest", "manifest-sha256", "domain", "task-id", "role", "output"):
        prep.add_argument("--" + name, required=True)
    prep.add_argument("--trial", required=True, type=int)
    prep.add_argument("--parent-pipe", action="store_true")
    execution = sub.add_parser("run")
    for name in ("payload", "plan-sha256", "docker-socket", "output"):
        execution.add_argument("--" + name, required=True)
    execution.add_argument("--parent-pipe", action="store_true")
    args = parser.parse_args()
    if args.operation == "prepare":
        files, plan = prepare(Path(args.source_archive), Path(args.wheelhouse), Path(args.manifest),
                              args.manifest_sha256, {key: getattr(args, key) for key in
                                                    ("domain", "task_id", "trial", "role")},
                              time.monotonic() + runtime.SECONDS, parent_pipe=args.parent_pipe)
        publish(Path(args.output), files)
        print(tau.encoded({"plan_sha256": tau.digest(files["trial.json"]), "upstream_executed": False,
                          "live_transport": False}).decode(), end="")
        return 0
    return run(Path(args.payload), args.plan_sha256, Path(args.output), Path(args.docker_socket),
               parent_pipe=args.parent_pipe)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit("Tau scripted trial rejected; no qualification claim.")
