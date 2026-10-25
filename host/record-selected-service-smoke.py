#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""One private selected-service container, bounded by an original absolute cutoff."""

import sys
from pathlib import Path

ROOT = Path(__file__).absolute().parents[1]
if __name__ == "__main__":
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        sys.exit("Use python3 -I -S -B host/record-selected-service-smoke.py")
    sys.path.insert(0, str(ROOT / "scripts"))

import argparse
from contextlib import ExitStack, contextmanager
import datetime
import json
import os
import re
import select
import signal
import stat
import subprocess
import threading
import time
import types
import uuid

import selected_service_process as process
import selected_service_image as image_builder
import selected_service_inference as inference
import selected_service_conformance as conformance
import selected_service_protocol as protocol

service = process.service
require, digest = service.require, process.digest
SCHEMA = "nib.selected-service-smoke/v1"
SECONDS = 60
HOST_RESERVE = 180
CLIENT_REAP = 25
LABEL = "nib.selected-service-smoke"
CACHE_SHA = "30f8a0ef0fa2fcb88d6d6a63ac636cf518593ef240b74e67cd98e38e83aeca70"
TMPFS = {"/cache": "rw,nosuid,nodev,exec,size=16g,mode=0700,uid=65532,gid=65532",
         "/tmp": "rw,nosuid,nodev,noexec,size=256m,mode=1777"}


def host_recorder():
    path = ROOT / "host/record-deepseek-lab.py"
    lab = types.ModuleType("selected_service_smoke_lab")
    lab.__file__ = str(path)
    exec(compile(process.bootstrap.read_source(str(path)), str(path), "exec",
                 dont_inherit=True), lab.__dict__)
    return lab, lab.load("selected_service_smoke_application", ROOT / lab.BUILDER,
                         lab.BUILDER_SHA).recorder


def image_id(value):
    require(type(value) is str and re.fullmatch("sha256:[0-9a-f]{64}", value), "identity")
    return value


def check_image(base, image, build, recorder, config, contract):
    # Raw OCI metadata was revalidated against the preparation's pins. Local
    # layer bytes still rely on the trusted daemon, just as in the builder.
    layers = config["rootfs"]["diff_ids"]
    binding = recorder.image_binding(base, config, contract)
    require(binding == build["base_binding"], "identity")
    require(base["Config"].get("Env") == config["config"].get("Env"), "identity")
    config = image["Config"]
    command = ["/usr/bin/python3.12", "-I", "-S", "-B",
        "/opt/nib-service/scripts/selected_service_process.py", "--prepared-sha256", build["prepared_sha256"]]
    require(image["Id"] == build["image_id"] and image["Id"] != base["Id"]
        and image["Os"] == "linux" and image["Architecture"] == "amd64"
        and config["User"] == "65532:65532" and config["WorkingDir"] == "/"
        and config["Entrypoint"] == command and not config.get("Cmd")
        and not config.get("Volumes") and not config.get("ExposedPorts")
        and config["Healthcheck"]["Test"] == ["NONE"]
        and not config.get("OnBuild") and not config.get("Shell")
        and config.get("Env") == base["Config"].get("Env"), "identity")
    derived = image["RootFS"]["Layers"]
    require(image["RootFS"]["Type"] == "layers" and type(derived) is list
        and len(layers) < len(derived) <= 128 and derived[:len(layers)] == layers, "identity")
    for value in derived:
        image_id(value)


def report():
    return {"schema": SCHEMA, "result": "fail", "status": "BLOCKED", "reason": "contract",
        "preflight_pass": False, "container_created": False, "runtime_started": False,
        "cleanup": "not-started", "hardware_validated": False, "deployable": False,
        "published": False, "tools": False, "reasoning": False, "zdr": False,
        "confidential": False, "e2ee": False}


def private_path(value):
    path = Path(value)
    require(path.is_absolute() and str(path) == os.path.normpath(path)
            and ".." not in path.parts and not any(char in str(path) for char in ",\r\n\0"), "mounts")
    return path


def weights_view(lab, path, deadline):
    cache = lab.load("service_smoke_weight_cache", ROOT / "host/cache-public-weights.py", CACHE_SHA)
    profile = service.models.select("uncensored")
    rows = cache.manifest_rows(lab.read(ROOT / profile["pack"] / "files.sha256"),
                              profile["manifest_sha256"], profile["total_bytes"], 79)
    cache.strict_inventory(path, rows)
    directories = {path}
    for _, _, name in rows:
        info = (path / name).lstat()
        require(info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o444
                and time.monotonic() < deadline, "mounts")
        directories.update(parent for parent in (path / name).parents if parent == path or path in parent.parents)
    for directory in directories:
        with cache.directory(directory) as fd:
            info = os.fstat(fd)
            require(info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o555
                    and time.monotonic() < deadline, "mounts")
    # The in-container owner performs the full manifest hash check before start.


@contextmanager
def staged_inputs(args, lab):
    with service.snapshot_inputs(provider=args.provider_config, cert=args.cert, key=args.key,
                                 api_key=args.api_key_file) as inputs:
        inputs.check()
        stage = inputs.root / "stage"
        stage.mkdir(mode=0o700)
        for name in ("provider.json", "api-key", "cert.pem", "key.pem"):
            raw = lab.read(inputs.root / name, 65536)
            target = stage / name
            with target.open("xb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(raw)
                os.fchown(stream.fileno(), 65532, 65532)
        os.chown(stage, 65532, 65532)
        yield stage


def container_command(name, token, build, base, weights, inputs, args, stop_at):
    command = ["docker", "container", "create", "--name", name, "--label", LABEL + "=" + token,
        "--pull", "never", "--platform", "linux/amd64", "--runtime", "nvidia",
        "--gpus", '"device=0,1,2,3,4,5,6,7"', "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "1024",
        "--memory", "512g", "--memory-swap", "512g", "--cpus", "64",
        "--ipc", "private", "--shm-size", "2g", "--cgroupns", "private", "--restart", "no",
        "--log-driver", "none", "--no-healthcheck", "--user", "65532:65532", "--workdir", "/"]
    for target, options in TMPFS.items():
        command += ["--tmpfs", target + ":" + options]
    for source, target in ((weights, "/model"), (inputs, "/run/nib-inputs")):
        command += ["--mount", "type=bind,src=" + str(private_path(source)) + ",dst=" + target + ",readonly"]
    inherited = {}
    for item in base["Config"].get("Env", []):
        key, sep, value = item.partition("=")
        require(sep and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", key) and key not in inherited, "identity")
        inherited[key] = value if key.startswith("NVIDIA_REQUIRE_") else ""
    inherited.update(NVIDIA_VISIBLE_DEVICES="0,1,2,3,4,5,6,7", NVIDIA_DRIVER_CAPABILITIES="compute,utility")
    for key, value in sorted(inherited.items()):
        command += ["--env", key + "=" + value]
    return command + [build["image_id"], "--provider-config", "/run/nib-inputs/provider.json",
        "--api-key-file", "/run/nib-inputs/api-key", "--cert", "/run/nib-inputs/cert.pem",
        "--key", "/run/nib-inputs/key.pem", "--hostname", args.hostname,
        "--input-price", args.input_price, "--output-price", args.output_price, "--hard-stop-at", stop_at] \
        + (["--protocol-smoke"] if getattr(args, "protocol_smoke", False) else
           ["--conformance-smoke"] if getattr(args, "conformance_smoke", False) else
           ["--inference-smoke"] if getattr(args, "inference_smoke", False) else [])


def check_container(info, identifier, name, token, build, image, command, weights, inputs):
    config, host = info["Config"], info["HostConfig"]
    require(info["Id"] == identifier and info["Name"] == "/" + name and info["Image"] == build["image_id"]
        and config["Image"] == build["image_id"] and config["Labels"].get(LABEL) == token
        and config["Entrypoint"] == image["Config"]["Entrypoint"]
        and config["Cmd"] == command[command.index(build["image_id"]) + 1:]
        and config["User"] == "65532:65532" and config["WorkingDir"] == "/"
        and not config.get("Tty") and not config.get("OpenStdin") and not config.get("ExposedPorts")
        and config["Healthcheck"]["Test"] == ["NONE"], "identity")
    env = [command[index + 1] for index, value in enumerate(command) if value == "--env"]
    require(len(config["Env"]) == len(env) and set(config["Env"]) == set(env), "identity")
    require(host["NetworkMode"] == "none" and set(info["NetworkSettings"]["Networks"]) == {"none"}
        and not info["NetworkSettings"].get("Ports") and host["Privileged"] is False
        and host["ReadonlyRootfs"] is True and host["Runtime"] == "nvidia"
        and host["CapDrop"] == ["ALL"] and not host.get("CapAdd") and not host.get("Devices")
        and not host.get("DeviceCgroupRules") and not host.get("PortBindings") and not host.get("PublishAllPorts")
        and not host.get("PidMode") and not host.get("UTSMode") and not host.get("VolumesFrom")
        and not host.get("Binds") and not host.get("GroupAdd") and not host.get("Init")
        and host["IpcMode"] == "private" and host["ShmSize"] == 2 << 30
        and host["CgroupnsMode"] == "private" and host["Memory"] == host["MemorySwap"] == service.MEMORY_BYTES
        and host["NanoCpus"] == 64000000000 and host["PidsLimit"] == 1024
        and host["RestartPolicy"]["Name"] == "no" and host["LogConfig"]["Type"] == "none"
        and set(host["SecurityOpt"]) in ({"no-new-privileges"}, {"no-new-privileges=true"})
        and host["Tmpfs"] == TMPFS, "runtime-limits")
    devices = host["DeviceRequests"]
    require(len(devices) == 1 and devices[0]["DeviceIDs"] == [str(i) for i in range(8)]
        and devices[0]["Capabilities"] == [["gpu"]] and devices[0]["Count"] == 0
        and devices[0]["Driver"] in ("", "nvidia") and not devices[0].get("Options"), "runtime-limits")
    require(len(info["Mounts"]) == 2 and {(row["Type"], row["Source"], row["Destination"], row["RW"])
        for row in info["Mounts"]} == {("bind", str(weights), "/model", False),
        ("bind", str(inputs), "/run/nib-inputs", False)}, "mounts")


class Docker:
    """Closed local daemon calls using the recorder's bounded process cleanup."""
    def __init__(self, recorder, env, wall, monotonic):
        self.recorder, self.env, self.wall, self.deadline = recorder, env, wall, monotonic

    def remaining(self):
        self.deadline = min(self.deadline, time.monotonic() + self.wall - time.time())
        return self.deadline - time.monotonic()

    def call(self, command, *, seconds=10, limit=65536):
        remaining = self.remaining() - CLIENT_REAP
        require(remaining > 0 and seconds > 0, "deadline")
        return self.recorder.call(command, self.env, timeout=min(seconds, remaining), limit=limit)

    def inspect(self, identifier):
        return service.correctness.strict_json(self.call(
            ["docker", "container", "inspect", "--format", "{{json .}}", identifier]))

    def owned(self, name, token, image, identifier=None):
        selector = "id=" + identifier if identifier else "name=^/" + name + "$"
        raw = self.call(["docker", "container", "ls", "--all", "--quiet", "--no-trunc", "--filter", selector],
                        limit=128).strip()
        if not raw:
            return None
        require(re.fullmatch(rb"[0-9a-f]{64}", raw), "identity")
        cid = raw.decode()
        require(identifier is None or cid == identifier, "identity")
        info = self.inspect(cid)
        require(info["Id"] == cid and info["Name"] == "/" + name
                and info["Image"] == image and info["Config"]["Labels"].get(LABEL) == token, "identity")
        return info

    def cleanup(self, name, token, image, identifier=None):
        info = self.owned(name, token, image, identifier)
        if info is None:
            return
        cid = info["Id"]
        if info["State"]["Running"]:
            try:
                self.call(["docker", "container", "kill", "--signal", "TERM", cid], seconds=5, limit=4096)
                graceful = min(time.monotonic() + service.CLEANUP_RESERVE, time.monotonic() + self.remaining() - 60)
                while time.monotonic() < graceful:
                    current = self.owned(name, token, image, cid)
                    if current is None or not current["State"]["Running"]:
                        break
                    time.sleep(min(.2, max(0, graceful - time.monotonic())))
            except BaseException:
                # A failed/denied TERM is not an excuse to abandon an owned ID.
                # Revalidate ownership below before attempting forced removal.
                pass
        # Resolve once more before deletion. A name collision is never killed.
        if self.owned(name, token, image, cid) is not None:
            self.call(["docker", "container", "rm", "--force", cid], seconds=10, limit=4096)
        require(self.owned(name, token, image, cid) is None, "owner-failed")


def watchdog(name, token, image, wall, monotonic, life_fd, docker_host):
    require(sys.platform == "linux" and os.getuid() == 0 and re.fullmatch("nib-selected-[0-9a-f]{32}", name)
            and re.fullmatch("[0-9a-f]{32}", token), "contract")
    image_id(image)
    lab, recorder = host_recorder()
    docker = Docker(recorder, lab.controlled_env(docker_host), float(wall), float(monotonic))
    require(0 < docker.remaining() <= 4800 and int(life_fd) >= 3, "lifetime")
    identifier = None
    try:
        while docker.remaining() > 60:
            readable, _, _ = select.select([int(life_fd)], [], [], min(1, docker.remaining() - 60))
            if readable:
                raw = os.read(int(life_fd), 65)
                if not raw:
                    break
                require(identifier is None and re.fullmatch(rb"[0-9a-f]{64}\n", raw), "identity")
                identifier = raw[:-1].decode()
        # An interrupted create client may have reached the daemon. Bounded
        # cleanup retries are not a second create/start or proof it completed.
        end = min(time.monotonic() + 40, time.monotonic() + docker.remaining() - 30)
        while True:
            confirmed = False
            try:
                docker.cleanup(name, token, image, identifier)
                confirmed = True
            except BaseException:
                pass
            if (identifier is not None and confirmed) or time.monotonic() >= end:
                break
            time.sleep(min(1, max(0, end - time.monotonic())))
        require(confirmed, "owner-failed")
        return 0
    finally:
        os.close(int(life_fd))


def inference_lifecycle(raw, prepared_sha256, *, failed):
    require(type(raw) is bytes and len(raw) <= 16384, "owner-failed")
    row = service.correctness.strict_json(raw)
    require(type(row) is dict and row.get("schema") == service.INFERENCE_SCHEMA
            and row.get("mode") == service.INFERENCE_MODE and "inference" in row, "owner-failed")
    base = {key: value for key, value in row.items() if key not in ("mode", "inference")}
    if row.get("reason") == "preparation":
        require(failed and type(row.get("preparation_stage")) is str
                and row["preparation_stage"] in process.PREPARATION_STAGES
                and type(row.get("preparation_category")) is str
                and row["preparation_category"] in service.FAILURES | {"internal"}, "owner-failed")
        del base["preparation_stage"], base["preparation_category"]
    base["schema"] = "nib.selected-service-lifecycle/v1"
    (lifecycle_failure if failed else lifecycle)(inference.encoded(base))
    if row["inference"] is not None:
        observed = inference.validate_report(inference.encoded(row["inference"]), prepared_sha256)
        require(row.get("ready") is True and (failed or observed["result"] == "pass"), "owner-failed")
    else:
        require(failed, "owner-failed")
    return row


def conformance_lifecycle(raw, prepared_sha256, *, failed):
    require(type(raw) is bytes and len(raw) <= 16384, "owner-failed")
    row = service.correctness.strict_json(raw)
    require(type(row) is dict and row.get("schema") == service.CONFORMANCE_SCHEMA
        and row.get("mode") == service.CONFORMANCE_MODE and "conformance" in row, "owner-failed")
    base = {key: value for key, value in row.items() if key != "conformance"}
    base.update(schema=service.INFERENCE_SCHEMA, mode=service.INFERENCE_MODE)
    inference_lifecycle(inference.encoded(base), prepared_sha256, failed=failed)
    if row["conformance"] is not None:
        observed = conformance.validate_report(inference.encoded(row["conformance"]),
                                               prepared_sha256, row["inference"])
        require(failed or observed["result"] == "pass", "owner-failed")
    else:
        require(failed, "owner-failed")
    return row


def protocol_lifecycle(raw, prepared_sha256, *, failed):
    require(type(raw) is bytes and len(raw) <= 16384, "owner-failed")
    row = service.correctness.strict_json(raw)
    require(type(row) is dict and row.get("schema") == service.PROTOCOL_SCHEMA
        and row.get("mode") == service.PROTOCOL_MODE and "protocol" in row, "owner-failed")
    base = {key: value for key, value in row.items() if key != "protocol"}
    base.update(schema=service.INFERENCE_SCHEMA, mode=service.INFERENCE_MODE)
    inference_lifecycle(inference.encoded(base), prepared_sha256, failed=failed)
    if row["protocol"] is not None:
        observed = protocol.validate_report(inference.encoded(row["protocol"]), prepared_sha256, row["inference"])
        require(failed or observed["result"] == "pass", "owner-failed")
        require(not row["cleanup_confirmed"] or
            (not observed["drain"]["reader_started"] or observed["drain"]["reader_joined"])
            and (not observed["attempts"]["drain"] or observed["drain"]["resources_closed"]), "owner-failed")
    else:
        require(failed, "owner-failed")
    return row


def lifecycle(raw, *, inference_smoke=False, conformance_smoke=False, protocol_smoke=False, prepared_sha256=None):
    require(sum((inference_smoke, conformance_smoke, protocol_smoke)) <= 1, "owner-failed")
    if protocol_smoke:
        return protocol_lifecycle(raw, prepared_sha256, failed=False)
    if conformance_smoke:
        return conformance_lifecycle(raw, prepared_sha256, failed=False)
    if inference_smoke:
        return inference_lifecycle(raw, prepared_sha256, failed=False)
    row = service.correctness.strict_json(raw)
    require(type(row) is dict and set(row) == {"schema", "reason", "status", "ready", "cleanup_confirmed",
        "cleanup", "deployable", "hardware_validated", "tools", "reasoning", "zdr", "confidential", "e2ee"}
        and row["schema"] == "nib.selected-service-lifecycle/v1" and row["status"] == "retired"
        and row["reason"] == "retired" and row["ready"] is True and row["cleanup_confirmed"] is True
        and all(row[key] is False for key in ("deployable", "hardware_validated", "tools", "reasoning", "zdr",
                                             "confidential", "e2ee"))
        and type(row["cleanup"]) is list and len(row["cleanup"]) == 3, "owner-failed")
    for item, role in zip(row["cleanup"], reversed(service.ROLES)):
        require(type(item) is dict and set(item) == {"role", "drained", "reaped"}
                and item["role"] == role and item["drained"] is True and item["reaped"] is True, "owner-failed")
    return row


def lifecycle_failure(raw, *, inference_smoke=False, conformance_smoke=False, protocol_smoke=False, prepared_sha256=None):
    """Accept only the owner's fixed metadata on a nonzero container exit."""
    require(sum((inference_smoke, conformance_smoke, protocol_smoke)) <= 1, "owner-failed")
    if protocol_smoke:
        return protocol_lifecycle(raw, prepared_sha256, failed=True)
    if conformance_smoke:
        return conformance_lifecycle(raw, prepared_sha256, failed=True)
    if inference_smoke:
        return inference_lifecycle(raw, prepared_sha256, failed=True)
    require(type(raw) is bytes and len(raw) <= 16384, "owner-failed")
    row = service.correctness.strict_json(raw)
    minimal = {"schema", "status", "reason", "deployable", "hardware_validated"}
    require(type(row) is dict and row.get("schema") == "nib.selected-service-lifecycle/v1"
            and row.get("status") == "failed" and row.get("deployable") is False
            and row.get("hardware_validated") is False, "owner-failed")
    if set(row) == minimal:
        require(row["reason"] == "preparation", "owner-failed")
        return row
    require(set(row) == minimal | {"ready", "cleanup_confirmed", "cleanup", "tools",
                                  "reasoning", "zdr", "confidential", "e2ee"}
            and type(row["reason"]) is str and row["reason"] in service.FAILURES | {"retired"}
            and type(row["ready"]) is bool and type(row["cleanup_confirmed"]) is bool
            and all(row[key] is False for key in ("tools", "reasoning", "zdr", "confidential", "e2ee"))
            and type(row["cleanup"]) is list and len(row["cleanup"]) <= len(service.ROLES), "owner-failed")
    roles = []
    for item in row["cleanup"]:
        require(type(item) is dict and set(item) == {"role", "drained", "reaped"}
                and item["role"] in service.ROLES and type(item["drained"]) is bool
                and type(item["reaped"]) is bool, "owner-failed")
        roles.append(item["role"])
    require(roles == list(reversed(service.ROLES[:len(roles)])), "owner-failed")
    return row


def run_container(args, contract, lab, recorder, env, build, base, image, inputs, result):
    weights = private_path(args.weights)
    deadline = min(time.monotonic() + SECONDS, contract.startup_deadline)
    with service.identity.worker.alarm(deadline):
        weights_view(lab, weights, deadline)
    name, token = "nib-selected-" + uuid.uuid4().hex, uuid.uuid4().hex
    docker = Docker(recorder, env, contract.hard_wall, contract.hard_monotonic)
    stop_at = datetime.datetime.fromtimestamp(contract.hard_wall - HOST_RESERVE,
        datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    require(docker.remaining() > HOST_RESERVE + service.CLEANUP_RESERVE, "deadline")
    command = container_command(name, token, build, base, weights, inputs, args, stop_at)
    join = {"inference_smoke": getattr(args, "inference_smoke", False),
            "conformance_smoke": getattr(args, "conformance_smoke", False),
            "protocol_smoke": getattr(args, "protocol_smoke", False),
            "prepared_sha256": build["prepared_sha256"]}
    monitor, identifier, attempted, finished, accepted = None, None, False, False, False
    reader, writer = os.pipe()
    try:
        require(docker.owned(name, token, build["image_id"]) is None, "identity")
        monitor = subprocess.Popen([sys.executable, "-I", "-S", "-B", str(Path(__file__).absolute()),
            "--watchdog", name, token, build["image_id"], str(contract.hard_wall),
            str(contract.hard_monotonic), str(reader), env["DOCKER_HOST"]], env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, pass_fds=(reader,))
        result.update(reason="create", cleanup="unconfirmed")
        attempted = True
        identifier = docker.call(command, seconds=30, limit=128).decode().strip()
        require(re.fullmatch("[0-9a-f]{64}", identifier), "identity")
        finished = True
        result["container_created"] = True
        os.write(writer, (identifier + "\n").encode())
        info = docker.inspect(identifier)
        check_container(info, identifier, name, token, build, image, command, weights, inputs)
        require(info["State"]["Status"] == "created" and monitor.poll() is None, "identity")
        result.update(runtime_started=None, reason="lifecycle")
        start_command = ["docker", "container", "start", "--attach", identifier]
        try:
            raw = docker.call(start_command, seconds=docker.remaining() - 120, limit=16384)
        except subprocess.CalledProcessError as error:
            # The shared bounded process helper retains stdout on nonzero exit.
            # Never expose stderr, exception text or unvalidated output.
            if error.cmd == start_command and type(error.returncode) is int and -64 <= error.returncode <= 255:
                result["start_client_exit_code"] = error.returncode
                if error.returncode == 1:
                    result["owner_failure"] = lifecycle_failure(error.stdout, **join)
            raise
        owner_result = lifecycle(raw, **join)
        if join["inference_smoke"] or join["conformance_smoke"] or join["protocol_smoke"]:
            # Retain validated accounting even when the subsequent inspect or
            # host/watchdog cleanup fails; none of these facts imply PASS.
            result["lifecycle"] = owner_result
        info = docker.inspect(identifier)
        check_container(info, identifier, name, token, build, image, command, weights, inputs)
        require(info["State"]["Status"] == "exited" and type(info["State"]["ExitCode"]) is int
                and info["State"]["ExitCode"] == 0
                and info["State"]["OOMKilled"] is False and monitor.poll() is None, "owner-failed")
        result["runtime_started"] = True
        result["lifecycle"] = owner_result
        accepted = True
    except BaseException:
        pass
    finally:
        os.close(reader)
        if attempted:
            try:
                docker.cleanup(name, token, build["image_id"], identifier if finished else None)
                result["cleanup"] = "confirmed" if finished else "create-completion-unknown"
            except BaseException:
                result["cleanup"] = "unconfirmed"
        os.close(writer)
        if monitor is not None:
            try:
                monitor.wait(timeout=max(0, docker.remaining() - CLIENT_REAP))
                require(monitor.returncode == 0, "owner-failed")
            except BaseException:
                result["cleanup"] = "unconfirmed"
                try:
                    recorder.snapshot.cleanup_process_group(monitor)
                except BaseException:
                    pass
        if accepted and result["cleanup"] == "confirmed" and docker.remaining() > 0:
            result.update(result="pass", status="PASS", reason="completed")


def preflight(args):
    result = report()
    if getattr(args, "inference_smoke", False):
        result.update(schema="nib.selected-service-smoke/v2", mode=service.INFERENCE_MODE)
    if getattr(args, "conformance_smoke", False):
        result.update(schema="nib.selected-service-smoke/v3", mode=service.CONFORMANCE_MODE)
    if getattr(args, "protocol_smoke", False):
        result.update(schema="nib.selected-service-smoke/v4", mode=service.PROTOCOL_MODE)
    try:
        require(sum(getattr(args, name, False) for name in ("inference_smoke", "conformance_smoke", "protocol_smoke")) <= 1,
                "contract")
        require(threading.current_thread() is threading.main_thread(), "contract")
        contract = service.Contract.begin(args.hard_stop_at, input_price=args.input_price,
                                          output_price=args.output_price)
        require(contract.hard_monotonic - time.monotonic() <= 4800, "lifetime")
        deadline = min(time.monotonic() + SECONDS, contract.hard_monotonic - service.CLEANUP_RESERVE)
        with ExitStack() as stack:
            with service.identity.worker.alarm(deadline):
                require(sys.platform == "linux" and os.getuid() == 0, "contract")
                require(type(args.hostname) is str and len(args.hostname) <= 253
                        and re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", args.hostname), "contract")
                lab, recorder = host_recorder()
                env = lab.controlled_env(args.docker_host)
                result["reason"] = "build-identity"
                preparation, _, lab_contract = image_builder.validated_files(Path(args.preparation), args.preparation_sha256)
                build = image_builder.validate_build_record(lab.read(args.build_report, 16384), args.build_sha256,
                                                            preparation, args.preparation_sha256, lab_contract)
                config = recorder.raw_metadata(Path(args.preparation) / "metadata", lab_contract)
                result["reason"] = "image-identity"
                def execute(command, **kwargs):
                    require(command[:5] == ["docker", "image", "inspect", "--format", "{{json .}}"]
                            and len(command) == 6 and command[5] in
                            (recorder.overlay.BASE_IMAGE, build["image_id"]), "contract")
                    remaining = min(deadline - time.monotonic(), contract.hard_wall - time.time()
                                    - service.CLEANUP_RESERVE) - CLIENT_REAP
                    require(remaining > 0, "deadline")
                    kwargs["timeout"] = min(kwargs["timeout"], remaining)
                    return recorder.snapshot.execute_process_group(command, **kwargs)
                base = recorder.docker_json("image", recorder.overlay.BASE_IMAGE, env, execute)
                image = recorder.docker_json("image", build["image_id"], env, execute)
                check_image(base, image, build, recorder, config, lab_contract)
                result["reason"] = "private-inputs"
                inputs = stack.enter_context(staged_inputs(args, lab))
                require(time.monotonic() < deadline and time.time() <
                        contract.hard_wall - service.CLEANUP_RESERVE, "deadline")
                result.update(preflight_pass=True, reason="not-run", build_sha256=args.build_sha256,
                    preparation_sha256=args.preparation_sha256, prepared_sha256=build["prepared_sha256"],
                    image_id=build["image_id"])
            if args.run:
                run_container(args, contract, lab, recorder, env, build, base, image, inputs, result)
    except BaseException:
        # CLI diagnostics never serialize exception messages, chains, argv,
        # Docker output, private paths, certificate names or input hashes.
        result.update(result="fail", status="BLOCKED")
        if result["reason"] == "completed":
            result["reason"] = "private-input-cleanup"
    return result


class QuietParser(argparse.ArgumentParser):
    def error(self, message):
        raise service.Rejected("contract")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    result = report()
    try:
        if argv[:1] == ["--watchdog"]:
            require(len(argv) == 8, "contract")
            return watchdog(*argv[1:])
        parser = QuietParser(description=__doc__, allow_abbrev=False)
        modes = parser.add_mutually_exclusive_group(required=True)
        modes.add_argument("--inspect", action="store_true")
        modes.add_argument("--run", action="store_true")
        probe = parser.add_mutually_exclusive_group()
        probe.add_argument("--inference-smoke", action="store_true")
        probe.add_argument("--conformance-smoke", action="store_true")
        probe.add_argument("--protocol-smoke", action="store_true")
        for name in ("build-report", "build-sha256", "preparation", "preparation-sha256",
                     "docker-host", "provider-config", "api-key-file", "cert", "key",
                     "hostname", "weights", "input-price", "output-price", "hard-stop-at"):
            parser.add_argument("--" + name, required=True)
        cancelled = False
        def cancel(*_):
            nonlocal cancelled
            if not cancelled:
                cancelled = True
                raise InterruptedError("selected smoke cancelled")
        handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            for sig in handlers:
                signal.signal(sig, cancel)
            result = preflight(parser.parse_args(argv))
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
    except BaseException:
        pass
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result["result"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
