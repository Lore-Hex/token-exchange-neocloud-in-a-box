# SPDX-License-Identifier: Apache-2.0
"""Fixed owner/guest handshake and CPU checker lifecycle; no arbitrary endpoint."""

import hashlib
import json
import os
from pathlib import Path
import re
import select
import selectors
import subprocess
import time

IMAGE = "docker.io/library/python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2"
CLASSIC_PROFILE = "docker28-classic"
CLASSIC_MANIFEST = "sha256:6e13e65c55e33adf203d77ee371cf8bf5d81bd4902ef07565721f46bf44917af"
CLASSIC_CONFIG = "sha256:64d91f7b885eed272bba87909446b12ff408d4aaa5f1a0e9ca787bbea1a020b9"
READY = b"NIB-PROVIDER-READY "
DONE = b"NIB-PROVIDER-DONE "
SECONDS = 300
OVERALL_SECONDS = 2700
TEARDOWN_RESERVE = 240
LABEL = "nib.deepseek-lab.provider"


def require(value):
    if not value:
        raise ValueError("owned provider session rejected")


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def decode(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result)
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: require(False))


def sha(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value))
    return value


def enabled(contract):
    value = contract.get("provider_conformance")
    require(type(value) is dict and set(value) in ({"schema", "image", "payload_sha256"},
            {"schema", "image", "payload_sha256", "image_profile"})
            and value["schema"] == "nib.owned-provider-input/v1" and value["image"] == IMAGE
            and contract.get("operation") == "local-correctness" and contract.get("gpu_family") == "B300"
            and ((contract.get("gpu_security", "confidential") == "confidential" and "model_profile" not in contract)
                 or (contract.get("gpu_security") == "nonconfidential"
                     and type(contract.get("model_profile")) is dict
                     and contract["model_profile"].get("name") == "uncensored"))
            and type(contract.get("data_parallel_size")) is int and contract["data_parallel_size"] == 2
            and "stream_cohort" not in contract and "execution_profile" not in contract
            and contract.get("runtime_observation") is True
            and contract.get("tokenizer_parity") is True and "tokenizer_materialization" in contract)
    sha(value["payload_sha256"])
    if "image_profile" in value:
        require(value["image_profile"] == CLASSIC_PROFILE)
    return value


def guest_wait(contract_sha, sample, deadline, *, read_fd=0, write_fd=1):
    packet = {"schema": "nib.owned-provider-ready/v1", "contract_sha256": sha(contract_sha),
              "parity_pass": True, "sample": sample}
    raw = encoded(packet)
    require(len(READY) + len(raw) + 1 <= 4096 and time.monotonic() < deadline)
    require(os.write(write_fd, READY + raw + b"\n") == len(READY) + len(raw) + 1)
    data = bytearray()
    while b"\n" not in data:
        remaining = deadline - time.monotonic()
        require(0 < remaining <= SECONDS and select.select([read_fd], [], [], remaining)[0])
        block = os.read(read_fd, 513 - len(data))
        require(block)
        data.extend(block)
        require(len(data) <= 512)
    require(data.endswith(b"\n") and data.startswith(DONE) and data.count(b"\n") == 1)
    value = decode(bytes(data[len(DONE):-1]))
    require(type(value) is dict and set(value) == {"ready_sha256", "report_sha256", "accepted"}
            and value["ready_sha256"] == digest(raw) and type(value["accepted"]) is bool)
    sha(value["report_sha256"])
    return value


def bind_model(plan, contract):
    """Called after both the checker payload and engine identity are verified."""
    enabled(contract)
    require(("model_profile" in plan) == ("model_profile" in contract)
            and plan.get("model_profile") == contract.get("model_profile"))


def validate_ack(value):
    require(type(value) is dict and set(value) == {"ready_sha256", "report_sha256", "accepted"}
            and type(value["accepted"]) is bool)
    sha(value["ready_sha256"])
    sha(value["report_sha256"])


def validate_ready(raw, contract, contract_sha, observer):
    require(type(raw) is bytes and len(raw) + len(READY) + 1 <= 4096)
    value = decode(raw)
    require(type(value) is dict and set(value) == {"schema", "contract_sha256", "parity_pass", "sample"}
            and value["schema"] == "nib.owned-provider-ready/v1"
            and value["contract_sha256"] == contract_sha and value["parity_pass"] is True
            and encoded(value) == raw)
    observer.validate_sample(value["sample"], contract)
    require(value["sample"]["event"] == "before-requests")
    return value


def validate_join(row, ready, experiment, worker, *, image_profile=None):
    """Bind the CPU report to the same guest's before/after owned API samples."""
    require(image_profile in (None, CLASSIC_PROFILE))
    fields = {"schema", "stage", "accepted", "cleanup", "worker",
              "worker_exit_code", "namespace_verified", "engine_identity_sha256", "image_manifest"}
    if image_profile is not None:
        fields.add("image_profile")
    require(type(row) is dict and set(row) == fields and row.get("image_profile") == image_profile
            and row["schema"] == "nib.owned-provider-run/v1" and type(row["accepted"]) is bool
            and row["stage"] in ("verification", "create", "checker", "completed")
            and row["cleanup"] in ("not-created", "unconfirmed", "create-completion-unknown", "confirmed")
            and type(row["namespace_verified"]) is bool)
    if row["engine_identity_sha256"] is not None:
        sha(row["engine_identity_sha256"])
    if row["image_manifest"] is not None:
        require(re.fullmatch(r"sha256:[0-9a-f]{64}", row["image_manifest"]))
        require(image_profile is None or row["image_manifest"] == CLASSIC_MANIFEST)
    accepted = False
    if row["worker"] is not None:
        require(type(row["worker_exit_code"]) is int)
        accepted = validate_worker(row["worker"], worker, row["worker_exit_code"])
    else:
        require(row["worker_exit_code"] is None)
    require(not row["accepted"] or (accepted and row["stage"] == "completed"
            and row["cleanup"] == "confirmed" and row["namespace_verified"]
            and row["engine_identity_sha256"] is not None and row["image_manifest"] is not None))
    ack = experiment["provider_conformance"]
    validate_ack(ack)
    require(ack == {"ready_sha256": digest(encoded(ready)), "report_sha256": digest(encoded(row)),
                    "accepted": row["accepted"]}
            and experiment["runtime_observation"]["samples"][0] == ready["sample"]
            and experiment["runtime_observation"]["contract_sha256"] == ready["contract_sha256"]
            and experiment["tokenizer_parity"]["all_cases_pass"] is True)
    if experiment["functional_pass"]:
        require(row["accepted"] and len(experiment["runtime_observation"]["samples"]) == 2)


def validate_worker(value, worker, exit_code):
    require(type(value) is dict and value.get("stage") in ("completed", "rejected")
            and value == worker.result(value["stage"], value.get("projection")))
    projection = value["projection"]
    if value["stage"] == "rejected":
        require(projection is None and exit_code == 2)
        return False
    require(type(projection) is dict and set(projection) == {"checks", "upstream_conformance_gate",
            "mandatory_checks_passed", "native_identity_matches"}
            and all(type(projection[key]) is bool for key in projection if key != "checks"))
    checks = projection["checks"]
    require(type(checks) is dict and 0 < len(checks) <= len(worker.ALLOWED)
            and set(checks) <= set(worker.ALLOWED)
            and all(type(s) is str and s in ("pass", "fail", "warn", "skip") for s in checks.values())
            and projection["upstream_conformance_gate"] == ("fail" not in checks.values()))
    accepted = (projection["upstream_conformance_gate"] and projection["native_identity_matches"]
                and set(checks) == set(worker.ALLOWED) and checks["catalog.declared-v2"] == "skip"
                and all(checks[name] == "pass" for name in worker.MANDATORY))
    require(projection["mandatory_checks_passed"] == accepted and exit_code == (0 if accepted else 1))
    return accepted


def capture(argv, env, deadline, snapshot, *, after_start=None, ready=None, cancelled=lambda: False):
    """Bound both pipes while preserving one private readiness frame, if enabled."""
    process = subprocess.Popen(argv, env=env, stdin=subprocess.PIPE if ready else subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0, start_new_session=True)
    output, error, total, dispatched = bytearray(), bytearray(), 0, False
    try:
        if after_start:
            after_start()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, output)
            selector.register(process.stderr, selectors.EVENT_READ, error)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                require(remaining > 0 and not cancelled())
                for key, _ in selector.select(min(remaining, 0.2)):
                    block = os.read(key.fd, 4096)
                    if not block:
                        selector.unregister(key.fileobj)
                        continue
                    key.data.extend(block)
                    require(len(error) <= 65536)
                    if key.data is output:
                        total += len(block)
                        require(total <= 65536)
                        if ready and not dispatched and b"\n" in output:
                            line, rest = bytes(output).split(b"\n", 1)
                            if line.startswith(READY):
                                require(len(line) <= 4096 + len(READY) and not rest)
                                acknowledgement = DONE + encoded(ready(line[len(READY):])) + b"\n"
                                require(len(acknowledgement) <= 512 and time.monotonic() < deadline)
                                process.stdin.write(acknowledgement)
                                process.stdin.close()
                                output.clear()
                                dispatched = True
            code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
            require(not error and not cancelled())
            return code, bytes(output)
    finally:
        try:
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
        finally:
            snapshot.cleanup_process_group(process)


def create_command(name, owner, engine_id, payload, plan_sha, worker):
    require(re.fullmatch(r"nib-deepseek-[0-9a-f]{32}-provider", name)
            and re.fullmatch(r"[0-9a-f]{32}", owner) and re.fullmatch(r"[0-9a-f]{64}", engine_id))
    require(payload.is_absolute() and not any(c in str(payload) for c in (",", "\n", "\r")))
    return ["docker", "container", "create", "--name", name, "--label", LABEL + "=" + owner,
            "--pull", "never", "--platform", "linux/amd64", "--runtime", "runc", "--user", "65534:65534",
            "--network", "container:" + engine_id, "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--memory", "1g", "--memory-swap", "1g",
            "--cpus", "1", "--pids-limit", "32", "--ipc", "private", "--cgroupns", "private",
            "--shm-size", "16m", "--restart", "no", "--log-driver", "none", "--no-healthcheck",
            "--workdir", "/scratch", "--mount", "type=bind,src=" + str(payload) + ",dst=/opt/nib,readonly",
            "--tmpfs", "/scratch:rw,nosuid,nodev,noexec,size=16m,mode=0700,uid=65534,gid=65534",
            "--entrypoint", "/usr/bin/env", IMAGE, "-i",
            *[key + "=" + value for key, value in sorted(worker.ENVIRONMENT.items())],
            "/usr/local/bin/python3", "-I", "-S", "-B", "/opt/nib/worker.py", sha(plan_sha)]


def image_manifest(info, *, image_profile=None):
    require(image_profile in (None, CLASSIC_PROFILE))
    repository, pinned_digest = IMAGE.split("@")
    # RepoDigests may drop the human-readable tag but must retain this exact pin.
    canonical = repository.rsplit(":", 1)[0] + "@" + pinned_digest
    references = {reference.removeprefix(prefix) for reference in (IMAGE, canonical)
                  for prefix in ("", "docker.io/", "docker.io/library/")}
    require(info["Architecture"] == "amd64" and info["Os"] == "linux"
            and not info["Config"].get("Volumes")
            and type(info["RepoDigests"]) is list
            and all(type(reference) is str for reference in info["RepoDigests"])
            and any(reference in references for reference in info["RepoDigests"]))
    if image_profile == CLASSIC_PROFILE:
        # Docker 28's classic store identifies images by config, not descriptor.
        # See docs/tasks/p2-t4-provider-classic-store.md for the pinned chain.
        require(info["Id"] == CLASSIC_CONFIG and info.get("Descriptor") is None
                and not info.get("Manifests") and not info.get("Variant"))
        return CLASSIC_MANIFEST
    descriptor = info["Descriptor"]
    require(info["Id"] == descriptor["digest"]
            and descriptor["mediaType"] == "application/vnd.oci.image.manifest.v1+json"
            and descriptor["platform"] == {"architecture": "amd64", "os": "linux"}
            and re.fullmatch(r"sha256:[0-9a-f]{64}", descriptor["digest"]))
    return descriptor["digest"]


def image_inspect_args(image_profile=None):
    require(image_profile in (None, CLASSIC_PROFILE))
    return ["image", "inspect", *([] if image_profile else ["--platform", "linux/amd64"]), IMAGE]


def preflight_image(recorder, env, *, image_profile=None):
    info = decode(recorder.call(["docker", *image_inspect_args(image_profile)],
                                env, timeout=20, limit=65536))
    require(type(info) is list and len(info) == 1)
    return image_manifest(info[0], image_profile=image_profile)


def check_container(info, command, name, owner, engine_id, payload, manifest, *, image_profile=None):
    require(image_profile in (None, CLASSIC_PROFILE))
    if image_profile == CLASSIC_PROFILE:
        require(info["Image"] == CLASSIC_CONFIG and manifest == CLASSIC_MANIFEST
                and info.get("ImageManifestDescriptor") is None and info["Platform"] == "linux")
    else:
        require(info["Image"] == IMAGE.split("@")[1] and info["ImageManifestDescriptor"]["digest"] == manifest)
    cfg, host = info["Config"], info["HostConfig"]
    require(info["Name"] == "/" + name and cfg["Image"] == IMAGE and cfg["Labels"].get(LABEL) == owner
            and cfg["Entrypoint"] == ["/usr/bin/env"] and cfg["Cmd"] == command[command.index(IMAGE) + 1:]
            and cfg["User"] == "65534:65534" and cfg["WorkingDir"] == "/scratch"
            and not cfg["Tty"] and not cfg["OpenStdin"] and cfg["Healthcheck"]["Test"] == ["NONE"])
    require(host["NetworkMode"] == "container:" + engine_id and host["ReadonlyRootfs"] is True
            and host["Runtime"] == "runc" and host["CapDrop"] == ["ALL"] and not host["Privileged"]
            and not host.get("CapAdd") and not host.get("Devices") and not host.get("DeviceRequests")
            and not host.get("DeviceCgroupRules") and not host.get("PortBindings") and not host.get("PublishAllPorts")
            and not host.get("PidMode") and not host.get("UTSMode") and not host.get("VolumesFrom")
            and not host.get("Binds") and host["IpcMode"] == host["CgroupnsMode"] == "private"
            and host["Memory"] == host["MemorySwap"] == 1 << 30 and host["NanoCpus"] == 1000000000
            and host["PidsLimit"] == 32 and host["ShmSize"] == 16 << 20
            and host["RestartPolicy"]["Name"] == "no" and host["LogConfig"]["Type"] == "none"
            and set(host["SecurityOpt"]) in ({"no-new-privileges"}, {"no-new-privileges=true"})
            and host["Tmpfs"] == {"/scratch": "rw,nosuid,nodev,noexec,size=16m,mode=0700,uid=65534,gid=65534"})
    require(len(info["Mounts"]) == 1 and info["Mounts"][0]["Type"] == "bind"
            and info["Mounts"][0]["Source"] == str(payload) and info["Mounts"][0]["Destination"] == "/opt/nib"
            and info["Mounts"][0]["RW"] is False)


def kernel_identity(info):
    state = info["State"]
    require(state["Running"] is True and state["Status"] == "running" and not state["OOMKilled"]
            and type(state["Pid"]) is int and state["Pid"] > 1)
    return {"process_pid": state["Pid"], "started": state["StartedAt"], "id": info["Id"],
            "namespaces": {name: os.stat(f"/proc/{state['Pid']}/ns/{name}").st_ino
                           for name in ("net", "pid", "cgroup")}}


def run_checker(payload, contract, worker, recorder, env, engine_name, owner, engine_id,
                owner_check, cleanup_checker, start_watchdog, deadline, cancelled=lambda: False):
    settings = enabled(contract)
    image_profile = settings.get("image_profile")
    name = engine_name + "-provider"
    row = {"schema": "nib.owned-provider-run/v1", "stage": "verification", "accepted": False,
           "cleanup": "not-created", "worker": None, "worker_exit_code": None,
           "namespace_verified": False, "engine_identity_sha256": None, "image_manifest": None}
    if image_profile is not None:
        row["image_profile"] = image_profile
    created = completed_create = False
    monitor = None
    life = None
    def call(*args, timeout=20):
        remaining = deadline - time.monotonic() - 60
        require(remaining > 0 and not cancelled())
        return recorder.call(["docker", *args], env, timeout=min(timeout, remaining), limit=65536)
    def inspect(name):
        value = decode(call("container", "inspect", name))
        require(type(value) is list and len(value) == 1)
        return value[0]
    try:
        require(deadline - time.monotonic() > 240)
        worker.verify(payload, settings["payload_sha256"])
        before = kernel_identity(owner_check())
        row["engine_identity_sha256"] = digest(encoded(before))
        info = decode(call(*image_inspect_args(image_profile)))
        require(type(info) is list and len(info) == 1)
        manifest = image_manifest(info[0], image_profile=image_profile)
        row["image_manifest"] = manifest
        command = create_command(name, owner, engine_id, payload, settings["payload_sha256"], worker)
        require(not call("container", "ls", "--all", "--quiet", "--filter", "name=^/" + name + "$").strip())
        row["stage"], row["cleanup"], created = "create", "unconfirmed", True
        call(*command[1:])
        completed_create = True
        check_container(inspect(name), command, name, owner, engine_id, payload, manifest, image_profile=image_profile)
        require(kernel_identity(owner_check()) == before)
        # A separate process owns the 200-second kill deadline even if this
        # process dies or stalls. The outer engine watchdog is a second backstop.
        monitor, life = start_watchdog(time.time() + 200)
        def verify_started():
            end = min(time.monotonic() + 10, deadline - 60)
            while True:
                current = inspect(name)
                if current["State"]["Status"] != "created":
                    break
                require(time.monotonic() < end)
                time.sleep(0.05)
            check_container(current, command, name, owner, engine_id, payload, manifest, image_profile=image_profile)
            child = kernel_identity(current)
            require(kernel_identity(owner_check()) == before
                    and child["namespaces"]["net"] == before["namespaces"]["net"]
                    and all(child["namespaces"][n] != before["namespaces"][n] for n in ("pid", "cgroup")))
            row["namespace_verified"] = True
        row["stage"] = "checker"
        code, raw = capture(["docker", "container", "start", "--attach", name], env,
                            min(deadline - 60, time.monotonic() + 200), recorder.snapshot,
                            after_start=verify_started, cancelled=cancelled)
        require(len(raw) <= 16384)
        value = decode(raw)
        accepted = validate_worker(value, worker, code)
        row.update(worker=value, worker_exit_code=code)
        final = inspect(name)
        check_container(final, command, name, owner, engine_id, payload, manifest, image_profile=image_profile)
        require(final["State"]["Status"] == "exited" and final["State"]["ExitCode"] == code
                and not final["State"]["OOMKilled"] and kernel_identity(owner_check()) == before)
        worker.verify(payload, settings["payload_sha256"])
        row.update(stage="completed", accepted=accepted)
    except Exception:
        row["accepted"] = False
    finally:
        if created:
            try:
                cleanup_checker()
                row["cleanup"] = "confirmed" if completed_create else "create-completion-unknown"
            except Exception:
                row["cleanup"] = "unconfirmed"
        if life is not None:
            os.close(life)
        if monitor is not None:
            try:
                monitor.wait(timeout=45)
                require(monitor.returncode == 0)
            except Exception:
                row["cleanup"] = "unconfirmed"
                recorder.snapshot.cleanup_process_group(monitor)
        if row["cleanup"] != "confirmed":
            row["accepted"] = False
    return row
