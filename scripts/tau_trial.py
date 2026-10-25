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
RUNTIME_SHA = "2a04a7198f9c95a53abed9d03b28abfaf34792f544ead550a94620248b3a90d4"
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


SELECTED_PARENT_MODULES = ("deepseek_model_profile", "tau_support_probe", "tau_selected_policy",
    "tau_local_vllm", "tau_local_llama", "engine_tokenizer_parity", "tau_llama_identity",
    "tau_live_identity", "tau_cpu_parity", "tau_owner_channel", "tau_selected_session")


def parent_driver(toolkit, *, live_local=False, selected_operational=False):
    # Compile the exact verified closure, never timestamp-valid .pyc files or
    # preexisting same-name modules from an ambient import path.
    modules = {}
    require(type(selected_operational) is bool and not (selected_operational and live_local))
    names = ("tau_preflight", "tau_trial_worker", "tau_aggregate", "tau_transport")
    if live_local:
        names += ("tau_local_vllm", "tau_local_llama", "engine_tokenizer_parity",
                  "tau_llama_identity", "tau_live_identity")
    if selected_operational:
        names += SELECTED_PARENT_MODULES
    elif "scripts/tau_support_probe.py" in toolkit["files"]:
        names += ("tau_support_probe",)
    names += ("tau_pipe_driver",)
    if selected_operational:
        names += ("tau_selected_driver",)
    for name in names:
        path = "scripts/" + name + ".py"
        modules[name] = load(path, toolkit["files"][path], modules=modules)
    driver = modules["tau_selected_driver" if selected_operational else "tau_pipe_driver"]
    if live_local:
        driver.live = modules["tau_live_identity"]
    return driver


runtime = load("scripts/tau_runtime.py", RUNTIME_SHA)
tau = runtime.tau
aggregate = load("scripts/tau_aggregate.py")
worker = load(WORKER)
probe = load("host/probe.py")
IMAGE_DIGEST = runtime.TARGET["image"].split("@")[1]
PROBE_MANIFEST = "sha256:6e13e65c55e33adf203d77ee371cf8bf5d81bd4902ef07565721f46bf44917af"
PROBE_CONFIG = "sha256:64d91f7b885eed272bba87909446b12ff408d4aaa5f1a0e9ca787bbea1a020b9"
PREFIX_IMAGE_PROFILES = ("docker28-classic", "docker29-descriptor")


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


def check_probe_image(image):
    # Fixed Docker 28 classic-store profile, not a fallback for the ordinary
    # descriptor path. The exact OCI index -> manifest -> config chain and
    # Moby source semantics are documented in p2-t3-tau-cpu-prompt-fit.md.
    references = {name + "@" + IMAGE_DIGEST for name in ("python", "library/python", "docker.io/library/python")}
    require(image["Id"] == PROBE_CONFIG and image["Os"] == "linux" and image["Architecture"] == "amd64"
            and image.get("Variant") in (None, "") and image.get("Descriptor") is None
            and not image.get("Manifests") and not image["Config"].get("Volumes")
            and type(image["RepoDigests"]) is list
            and any(type(item) is str and item in references for item in image["RepoDigests"]))
    return {"manifest": PROBE_MANIFEST, "config": PROBE_CONFIG, "platform": "linux/amd64"}


def selected_policy(toolkit):
    modules = {"tau_preflight": tau}
    for name in ("deepseek_model_profile", "tau_support_probe", "tau_selected_policy"):
        path = "scripts/" + name + ".py"
        modules[name] = load(path, toolkit["files"][path], modules=modules)
    return modules["tau_selected_policy"]


def selected_mode(value, parent_pipe, live_local, support_probe, agent_prefix):
    require(type(value) is bool and (not value or parent_pipe is True
            and not live_local and not support_probe and not agent_prefix))


def identity(deadline, parent_pipe=False, *, live_local=False, support_probe=False, agent_prefix=False,
             selected_operational=False):
    selected_mode(selected_operational, parent_pipe, live_local, support_probe, agent_prefix)
    require(not live_local or parent_pipe)
    require(not support_probe or parent_pipe and not live_local)
    require(type(agent_prefix) is bool and (not agent_prefix or support_probe))
    names = (SCRIPT, WORKER, "scripts/tau_runtime.py", "scripts/tau_aggregate.py",
             runtime.CATALOG, "host/probe.py", "scripts/test_tau_trial.py", "scripts/e2e/pins.json",
             "schemas/evidence.schema.json", "schemas/common.schema.json")
    if parent_pipe:
        names += ("scripts/tau_transport.py", "scripts/tau_pipe_trial.py", "scripts/tau_pipe_driver.py",
                  "scripts/test_tau_pipe_driver.py")
    if support_probe:
        names += ("scripts/tau_support_probe.py", "scripts/tau_cpu_support_models.json", "scripts/tau_cpu_identity.py")
        if not agent_prefix:
            names += ("scripts/e2e/snapshot.py",)
    if agent_prefix:
        names += ("scripts/tau_agent_prefix.py", "scripts/test_tau_agent_prefix.py")
    if selected_operational:
        names += ("scripts/deepseek_model_profile.py", "scripts/tau_support_probe.py",
                  "scripts/tau_cpu_support_models.json", "scripts/tau_selected_policy.py",
                  "scripts/test_tau_selected_scorer.py", "scripts/test_tau_selected_termination.py",
                  "scripts/test_tau_tool_origin.py")
        names += tuple("scripts/" + name + ".py" for name in SELECTED_PARENT_MODULES)
        names += ("scripts/tau_selected_driver.py", "scripts/test_tau_selected_driver.py",
                  "scripts/test_tau_selected_run.py", "scripts/tau_cpu_parity.cpp",
                  "scripts/tau_cpu_identity.py", "scripts/tau_customer_resolution.py")
    if live_local:
        names += ("scripts/tau_live_trial.py", "scripts/tau_live_identity.py", "scripts/tau_local_vllm.py",
                  "scripts/test_tau_live_trial.py", "scripts/test_tau_local_vllm.py",
                  "scripts/test_tau_selected_target.py", "scripts/tau_local_llama.py",
                  "scripts/tau_llama_identity.py", "scripts/engine_tokenizer_parity.py",
                  "scripts/tau_cpu_support_models.json", "scripts/tau_cpu_parity.cpp",
                  "scripts/test_tau_cpu_admission.py")
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
            live_binding=None, support_probe=False, agent_prefix=False, selected_operational=False, identity_followup=False,
            identity_followup_17b=False, customer_resolution=False):
    require(type(customer_resolution) is bool and (not customer_resolution or selected_operational))
    require(type(identity_followup) is bool and type(identity_followup_17b) is bool
            and not (identity_followup and identity_followup_17b)
            and (not (identity_followup or identity_followup_17b) or support_probe and not agent_prefix))
    selected_mode(selected_operational, parent_pipe, live_binding is not None, support_probe, agent_prefix)
    require(not support_probe or parent_pipe and live_binding is None)
    require(type(agent_prefix) is bool and (not agent_prefix or support_probe))
    toolkit = (identity(deadline, parent_pipe, live_local=True) if live_binding is not None
               else identity(deadline, parent_pipe, **({"support_probe": True} if support_probe else {}),
                             **({"agent_prefix": True} if agent_prefix else {}),
                             **({"selected_operational": True} if selected_operational else {})))
    pins_raw = runtime.read_file(ROOT / tau.PINS, 1 << 20, deadline)
    require(tau.digest(pins_raw) == tau.PINS_SHA)
    pins = tau.json_value(pins_raw)
    if support_probe or selected_operational:
        require(manifest_path is None and manifest_sha is None and selection is None)
        policy = (selected_policy(toolkit) if selected_operational else
                  load("scripts/tau_support_probe.py", modules={"tau_preflight": tau}))
        manifest = policy.configuration(**({"identity_followup": True} if identity_followup else {}),
                                        **({"identity_followup_17b": True} if identity_followup_17b else {}),
                                        **({"customer_resolution": True} if customer_resolution else {}))
        manifest_sha = tau.digest(tau.encoded(manifest))
        source_policy = policy.support if selected_operational else policy
        chosen = dict(source_policy.SELECTION)
    else:
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
    if support_probe or selected_operational:
        require(source_policy.source_selection(sources) == chosen)
        if identity_followup or identity_followup_17b or customer_resolution:
            load("scripts/tau_cpu_identity.py").fixture(sources, pins)
    else:
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
    if selected_operational:
        receipt["schema"] = "nib.tau-selected-scorer-inputs/v1"
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
    if support_probe:
        contract["support_probe"] = True
        contract["schema"] = "nib.tau-cpu-probe-plan/v1"
    if agent_prefix:
        contract.update(schema="nib.tau-agent-prefix-plan/v1", agent_prefix=True)
    if selected_operational:
        contract.update(schema="nib.tau-selected-scorer-plan/v1", selected_operational=True,
                        seconds=policy.LIMITS["run_seconds"], transport="selected-operational-parent-pipe")
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


def validate_plan(payload, sha, toolkit, deadline, *, parent_pipe=False, live_local=False, support_probe=False,
                  agent_prefix=False, selected_operational=False):
    selected_mode(selected_operational, parent_pipe, live_local, support_probe, agent_prefix)
    require(not live_local or parent_pipe)
    require(not support_probe or parent_pipe and not live_local)
    require(type(agent_prefix) is bool and (not agent_prefix or support_probe))
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
    if support_probe:
        expected_keys.add("support_probe")
    if agent_prefix:
        expected_keys.add("agent_prefix")
    if selected_operational:
        expected_keys.add("selected_operational")
    policy = selected_policy(toolkit) if selected_operational else None
    require(type(plan) is dict and set(plan) == expected_keys
            and plan["schema"] == ("nib.tau-selected-scorer-plan/v1" if selected_operational else
                                   "nib.tau-agent-prefix-plan/v1" if agent_prefix else
                                   "nib.tau-cpu-probe-plan/v1" if support_probe else "nib.tau-scripted-plan/v1")
            and plan["image"] == runtime.TARGET["image"]
            and plan["platform"] == runtime.TARGET["platform"] and plan["environment"] == ENVIRONMENT
            and type(plan["seconds"]) is int
            and plan["seconds"] == (policy.LIMITS["run_seconds"] if selected_operational else SECONDS)
            and plan["producer"]["git_sha"] == toolkit["git_sha"]
            and plan["producer"]["adapter_sha256"] == toolkit["files"][WORKER])
    require(not parent_pipe or plan["transport"] == ("selected-operational-parent-pipe"
                                                   if selected_operational else "scripted-parent-pipe"))
    require(not support_probe or plan["support_probe"] is True)
    require(not agent_prefix or plan["agent_prefix"] is True)
    require(not selected_operational or plan["selected_operational"] is True)
    require(("live-binding.json" in plan["files"]) is live_local)
    for name, expected in toolkit["files"].items():
        require(plan["files"][name]["sha256"] == expected)
    worker.verify_tree(payload, plan["files"], deadline)
    manifest = tau.json_value(runtime.read_file(payload / "manifest.json", 1 << 20, deadline))
    pins = tau.json_value(runtime.read_file(ROOT / tau.PINS, 1 << 20, deadline))
    selection = plan["selection"]
    if support_probe or selected_operational:
        if not selected_operational:
            policy = load("scripts/tau_support_probe.py", toolkit["files"]["scripts/tau_support_probe.py"],
                          modules={"tau_preflight": tau})
        policy.validate(manifest, plan["manifest_sha256"])
        require(not agent_prefix or manifest["schema"] == policy.SCHEMA)
        prefix = "data/tau2/domains/retail/"
        sources = {prefix + name: runtime.read_file(payload / "upstream" / prefix / name, 16 << 20, deadline)
                   for name in ("tasks.json", "split_tasks.json")}
        for name, raw in sources.items():
            require(tau.digest(raw) == pins["files"][name]["sha256"] and len(raw) == pins["files"][name]["bytes"])
        source_policy = policy.support if selected_operational else policy
        require(tau.encoded(selection) == tau.encoded(source_policy.source_selection(sources)))
    else:
        aggregate.validate_manifest(manifest, plan["manifest_sha256"], pins)
        require(selection == select(manifest, selection["domain"], selection["task_id"],
                                    selection["trial"], selection["role"]))
    require(plan["producer"]["runtime_receipt_sha256"] ==
            tau.digest(runtime.read_file(payload / "inputs.json", 1 << 20, deadline)))
    return plan, manifest


def create_command(name, owner, payload, sha, *, parent_pipe=False, selected_operational=False):
    selected_mode(selected_operational, parent_pipe, False, False, False)
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
            *(["--parent-pipe"] if parent_pipe else []),
            *(["--selected-operational"] if selected_operational else [])]


def check_container(info, name, owner, payload, sha, selected_manifest, *, parent_pipe=False, support_probe=False,
                    selected_operational=False):
    selected_mode(selected_operational, parent_pipe, False, support_probe, False)
    cfg, host = info["Config"], info["HostConfig"]
    command = create_command(name, owner, payload, sha, parent_pipe=parent_pipe,
                             **({"selected_operational": True} if selected_operational else {}))
    # Moby containerd keeps the outer index in Image, not the selected manifest.
    # See the exact daemon/CLI source chain in p2-t3-tau-single-trial.md.
    if support_probe:
        require(parent_pipe and selected_manifest == {"manifest": PROBE_MANIFEST, "config": PROBE_CONFIG,
                                                      "platform": "linux/amd64"}
                and info["Image"] == PROBE_CONFIG and info["Platform"] == "linux"
                and info.get("ImageManifestDescriptor") is None)
    else:
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


def run(payload, sha, destination, socket, *, parent_pipe=False, live_deployment=None, support_probe=None,
        agent_prefix=False, scorer_image_profile=None, selected_owners=None):
    deadline = time.monotonic() + 60
    live_local = live_deployment is not None
    selected_operational = selected_owners is not None
    selected_mode(selected_operational, parent_pipe, live_local, support_probe is not None, agent_prefix)
    if selected_operational:
        require(type(selected_owners) is dict and set(selected_owners) == {"manifest", "handles", "deadline"})
    require(support_probe is None or parent_pipe and not live_local)
    require(type(agent_prefix) is bool and (not agent_prefix or parent_pipe and not live_local and support_probe is None))
    require(scorer_image_profile is None or agent_prefix and type(scorer_image_profile) is str
            and scorer_image_profile in PREFIX_IMAGE_PROFILES)
    prefix_profile = PREFIX_IMAGE_PROFILES[0] if scorer_image_profile is None else scorer_image_profile
    probe_sources = support_probe is not None or agent_prefix
    classic = support_probe is not None or agent_prefix and prefix_profile == PREFIX_IMAGE_PROFILES[0]
    toolkit = (identity(deadline, parent_pipe, live_local=True) if live_local else
               identity(deadline, parent_pipe, **({"support_probe": True} if probe_sources else {}),
                        **({"agent_prefix": True} if agent_prefix else {}),
                        **({"selected_operational": True} if selected_operational else {})))
    options = {"parent_pipe": parent_pipe, **({"live_local": True} if live_local else {}),
               **({"support_probe": True} if probe_sources else {}), **({"agent_prefix": True} if agent_prefix else {}),
               **({"selected_operational": True} if selected_operational else {})}
    plan, manifest = validate_plan(payload, sha, toolkit, deadline, **options)
    deployment = driver = None
    if selected_operational:
        driver = parent_driver(toolkit, selected_operational=True)
        driver.policy.transport_manifest(selected_owners["manifest"], tau.digest(tau.encoded(selected_owners["manifest"])))
        require(tau.encoded(selected_owners["manifest"]["input"]) == tau.encoded(manifest)
                and type(selected_owners["deadline"]) in (int, float)
                and driver.RESERVE < selected_owners["deadline"] - time.monotonic() <= plan["seconds"])
    if live_local:
        driver = parent_driver(toolkit, live_local=True)
        deployment = driver.live.Deployment(*live_deployment, manifest, deadline)
        deployment.require_target(plan["selection"]["role"])
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
    prefix_body = None
    selected_manifest = None
    with tempfile.TemporaryDirectory(prefix="nib-tau-docker-") as private:
        config = Path(private) / "config.json"
        config.write_text('{"auths":{"https://index.docker.io/v1/":{}}}\n')
        config.chmod(0o600)
        env = {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin", "HOME": private,
               "DOCKER_CONFIG": private, "LANG": "C", "LC_ALL": "C"}
        docker = shutil.which("docker", path=env["PATH"])
        require(docker is not None)
        operation_deadline = (selected_owners["deadline"] if selected_operational else time.monotonic() + SECONDS)
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
            inspect_args = [] if classic else ["--platform", plan["platform"]]
            rows = tau.json_value(command("image", "inspect", *inspect_args, runtime.TARGET["image"]))
            require(type(rows) is list and len(rows) == 1)
            selected_manifest = check_probe_image(rows[0]) if classic else check_image(rows[0])
            inspection = {"parent_pipe": parent_pipe, **({"support_probe": True} if classic else {}),
                          **({"selected_operational": True} if selected_operational else {})}
            stage = "create"
            # Killing the bounded client cannot cancel an in-flight daemon create.
            # Only the exact owned-container inspection establishes completion.
            create_unknown = True
            command(*create_command(name, owner, payload, sha, parent_pipe=parent_pipe,
                                    **({"selected_operational": True} if selected_operational else {})))
            info = tau.json_value(command("inspect", name))[0]
            check_container(info, name, owner, payload, sha, selected_manifest, **inspection)
            create_unknown = False
            stage = "trial"
            if selected_operational:
                observed = driver.run_owned([docker, "--host", "unix://" + str(socket), "--config", private,
                    "start", "--attach", "--interactive", name], env, payload, plan,
                    selected_owners["manifest"], operation_deadline, selected_owners["handles"])
            elif agent_prefix:
                prefix = load("scripts/tau_agent_prefix.py", toolkit["files"]["scripts/tau_agent_prefix.py"])
                prefix_body = prefix.attach([docker, "--host", "unix://" + str(socket), "--config", private,
                    "start", "--attach", "--interactive", name], env, sha,
                    min(operation_deadline, time.monotonic() + worker.SECONDS), parent_driver(toolkit).transport)
                observed = {"request_sha256": prefix.digest(prefix.encoded(prefix_body)),
                            "messages": 1, "tools": len(prefix_body["tools"]), "generation_performed": False}
            elif support_probe is not None:
                # Fixed reviewed CPU-parent mode; no caller-supplied command or callback.
                probe_driver = parent_driver(toolkit)
                observed = probe_driver.attach_probe_scorer(
                    [docker, "--host", "unix://" + str(socket), "--config", private,
                     "start", "--attach", "--interactive", name], env, support_probe,
                    min(operation_deadline, time.monotonic() + worker.SECONDS),
                    load("scripts/e2e/snapshot.py", toolkit["files"]["scripts/e2e/snapshot.py"]))
                probe_driver.validate_support_probe(observed)
                require(observed["plan_sha256"] == sha and observed["selection"] == plan["selection"])
                probe_policy = load("scripts/tau_support_probe.py", toolkit["files"]["scripts/tau_support_probe.py"],
                                    modules={"tau_preflight": tau})
                require(observed["schema"] == probe_policy.report_schema(manifest))
            elif parent_pipe:
                observed = (driver or parent_driver(toolkit)).run_process(
                    [docker, "--host", "unix://" + str(socket), "--config", private,
                     "start", "--attach", "--interactive", name], env, payload, plan, manifest,
                    min(operation_deadline, time.monotonic() + worker.SECONDS),
                    **({"deployment": deployment} if live_local else {}))
            else:
                raw = command("start", "--attach", name, seconds=SECONDS, limit=16384)
                observed = tau.json_value(raw)
            if support_probe is None and not agent_prefix and not selected_operational:
                check_result(observed, plan, manifest, **({"live_local": True} if live_local else {}))
            # Preserve validated accounting even if the later container check fails.
            result = observed
            terminal = tau.json_value(command("inspect", name))[0]
            check_container(terminal, name, owner, payload, sha, selected_manifest, **inspection)
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
    if selected_operational:
        record = {"schema": "nib.tau-selected-scorer-run/v1", "toolkit": toolkit, "plan_sha256": sha,
                  "image": plan["image"], "platform": plan["platform"],
                  "manifest_sha256": tau.digest(tau.encoded(selected_owners["manifest"])),
                  "started_utc": started, "ended_utc": now(), "stage": stage,
                  "cleanup": "create-completion-unknown" if create_unknown else "confirmed" if cleaned else "failed",
                  "diagnostic": result,
                  "result": "pass" if stage == "completed" and cleaned and result and driver.accepted(result) else "fail",
                  "quality_eligible": False, "confidential": False, "live_admission": False}
        envelope = evidence_record(toolkit["git_sha"], sha, started, record, parent_pipe=True)
        envelope.update(command="Internal selected scorer lifecycle with original admitted owner pipes.",
            notes="Operational single-trial scorer only. Actual usage, no dollar-price estimate. "
                  "Owner-container cleanup and readiness provenance must be joined by the enclosing launcher. "
                  "No benchmark qualification or confidential claim.")
        publish_evidence(destination, {"run.json": tau.encoded(envelope), "output.log": tau.encoded(record)})
        return record
    if agent_prefix:
        record = {"schema": "nib.tau-agent-prefix-render/v1", "toolkit": toolkit, "plan_sha256": sha,
                  "selection": plan["selection"], "image": plan["image"], "platform": plan["platform"],
                  "scorer_image_profile": prefix_profile, "selected_image_identity": selected_manifest,
                  "started_utc": started, "ended_utc": now(), "stage": stage,
                  "cleanup": "create-completion-unknown" if create_unknown else "confirmed" if cleaned else "failed",
                  "result": "pass" if stage == "completed" and cleaned and result is not None else "fail",
                  "prefix": result, "quality_eligible": False, "confidential": False, "full_fit_proven": False}
        envelope = evidence_record(toolkit["git_sha"], sha, started, record, parent_pipe=True)
        envelope.update(command="python3 -I -S -B scripts/record-tau-agent-prefix.py --source-archive PRIVATE "
            "--wheelhouse PRIVATE --tokenizer-directory PRIVATE --docker-socket PRIVATE --output PRIVATE "
            "--scorer-image-profile " + prefix_profile,
            notes="Original static agent policy and tools only; no generated user, model generation or full-fit claim.")
        publish_evidence(destination, {"run.json": tau.encoded(envelope), "output.log": tau.encoded(record)})
        require(record["result"] == "pass")
        return prefix_body, record
    if support_probe is not None:
        record = {"schema": "nib.tau-cpu-probe-scorer/v1", "toolkit": toolkit, "plan_sha256": sha,
                  "cpu_ready_sha256": support_probe["ready_sha256"], "image": plan["image"],
                  "platform": plan["platform"], "started_utc": started, "ended_utc": now(),
                  "result": "pass" if stage == "completed" and cleaned and result and result["result"] == "pass" else "fail",
                  "stage": stage, "cleanup": "create-completion-unknown" if create_unknown else "confirmed" if cleaned else "failed",
                  "diagnostic": result, "quality_eligible": False, "confidential": False, "live_admission": False}
        envelope = probe_evidence_record(record)
        publish_evidence(destination, {"run.json": tau.encoded(envelope), "output.log": tau.encoded(record)})
        return record
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


def probe_evidence_record(record):
    envelope = evidence_record(record["toolkit"]["git_sha"], record["plan_sha256"], record["started_utc"], record,
                               parent_pipe=True)
    envelope.update(command="python3 -I -S -B scripts/record-tau-cpu-parity.py --archive PRIVATE --model-06 PRIVATE "
        "--model-17 PRIVATE --cmake-wheel PRIVATE --output PRIVATE --probe-payload PRIVATE --probe-plan-sha256 " + record["plan_sha256"],
        notes="Fixed CPU support probe scorer only; original upstream prompts, actual CPU support replies, scripted target. "
              "No target quality, full tau trial, live admission or confidential claim. CPU owner cleanup is recorded separately.")
    return envelope


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
    support = sub.add_parser("prepare-probe")
    for name in ("source-archive", "wheelhouse", "output"):
        support.add_argument("--" + name, required=True)
    profiles = support.add_mutually_exclusive_group()
    profiles.add_argument("--identity-followup", action="store_true")
    profiles.add_argument("--identity-followup-17b", action="store_true")
    execution = sub.add_parser("run")
    for name in ("payload", "plan-sha256", "docker-socket", "output"):
        execution.add_argument("--" + name, required=True)
    execution.add_argument("--parent-pipe", action="store_true")
    args = parser.parse_args()
    if args.operation == "prepare-probe":
        files, plan = prepare(Path(args.source_archive), Path(args.wheelhouse), None, None, None,
                              time.monotonic() + runtime.SECONDS, parent_pipe=True, support_probe=True,
                              **({"identity_followup": True} if args.identity_followup else {}),
                              **({"identity_followup_17b": True} if args.identity_followup_17b else {}))
        publish(Path(args.output), files)
        print(tau.encoded({"plan_sha256": tau.digest(files["trial.json"]), "upstream_executed": False,
                          "live_transport": False}).decode(), end="")
        return 0
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
