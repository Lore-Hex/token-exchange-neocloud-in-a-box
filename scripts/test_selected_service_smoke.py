# SPDX-License-Identifier: Apache-2.0
"""Synthetic Docker receipts only; no Docker, GPU or listening ports."""

from contextlib import contextmanager, ExitStack, nullcontext, redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import types
import unittest
from unittest import mock

from test_selected_service import Clock

ROOT = Path(__file__).absolute().parents[1]
path = ROOT / "host/record-selected-service-smoke.py"
smoke = types.ModuleType("selected_service_smoke_test")
smoke.__file__ = str(path)
exec(compile(path.read_bytes(), str(path), "exec"), smoke.__dict__)


def encoded(value):
    return json.dumps(value, sort_keys=True).encode()


def identity(name):
    return "sha256:" + smoke.digest(name.encode())


def lifecycle():
    return {"schema": "nib.selected-service-lifecycle/v1", "reason": "retired", "status": "retired",
        "ready": True, "cleanup_confirmed": True,
        "cleanup": [{"role": role, "drained": True, "reaped": True} for role in ("tls", "guard", "engine")],
        **{key: False for key in ("deployable", "hardware_validated", "tools", "reasoning", "zdr", "confidential", "e2ee")}}


class SmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lab, cls.recorder = smoke.host_recorder()

    def setUp(self):
        self.base = {"Id": identity("base"), "Os": "linux", "Architecture": "amd64",
            "RepoDigests": [self.recorder.overlay.BASE_IMAGE],
            "RootFS": {"Type": "layers", "Layers": [identity("base layer")]},
            "Config": {"Env": ["PATH=/bin", "NVIDIA_REQUIRE_CUDA=synthetic constraint"]}}
        self.metadata = {"rootfs": {"diff_ids": self.base["RootFS"]["Layers"]}, "config": self.base["Config"]}
        self.lab_contract = {"execution_profile": "tau8k-eager", "base_image": self.recorder.overlay.BASE_IMAGE, "official_config":
             {"sha256": self.recorder.overlay.DIAGNOSTIC_INVENTORY_POLICY["config_sha256"]}}
        binding = self.recorder.image_binding(self.base, self.metadata, self.lab_contract)
        self.build = {"schema": smoke.image_builder.BUILD_SCHEMA, "result": "pass", "preparation_sha256": smoke.digest(b"preparation"),
            "prepared_sha256": smoke.digest(b"prepared"), "base_binding": binding, "image_id": identity("derived"),
            "image_built": True, "build_completed": True, "runtime_started": False,
            "hardware_validated": False, "deployable": False, "published": False}
        self.image = {"Id": self.build["image_id"], "Os": "linux", "Architecture": "amd64",
            "RootFS": {"Type": "layers", "Layers": self.base["RootFS"]["Layers"] + [identity("derived layer")]},
            "Config": {"User": "65532:65532", "WorkingDir": "/", "Healthcheck": {"Test": ["NONE"]},
                "Env": list(self.base["Config"]["Env"]), "Entrypoint": ["/usr/bin/python3.12", "-I", "-S", "-B",
                    "/opt/nib-service/scripts/selected_service_process.py", "--prepared-sha256", self.build["prepared_sha256"]]}}
        self.clock = Clock()
        self.args = types.SimpleNamespace(weights="/private/public-view/model", hostname="private-name.invalid",
            input_price="1", output_price="2", hard_stop_at=self.clock.timestamp(3000), run=True,
            build_sha256=smoke.digest(encoded(self.build)), preparation_sha256=self.build["preparation_sha256"],
            preparation="/private/preparation", docker_host="unix:///var/run/docker.sock",
            build_report="/private/build.json", provider_config="/private/provider.json",
            api_key_file="/private/key", cert="/private/cert", key="/private/tls-key")

    def approved(self, row=None, **overrides):
        raw = encoded(self.build if row is None else row)
        values = {"expected": smoke.digest(raw), "preparation_sha": self.build["preparation_sha256"],
                  "receipt": {"prepared_sha256": self.build["prepared_sha256"]}, "contract": self.lab_contract, **overrides}
        return smoke.image_builder.validate_build_record(raw, **values)

    def container(self, command):
        value = lambda flag: command[command.index(flag) + 1]
        self.name, self.token = value("--name"), value("--label").split("=", 1)[1]
        return {"Id": smoke.digest(b"owned container"), "Name": "/" + self.name, "Image": self.build["image_id"],
            "Config": {"Image": self.build["image_id"], "Labels": {smoke.LABEL: self.token},
                "Entrypoint": self.image["Config"]["Entrypoint"], "Cmd": command[command.index(self.build["image_id"]) + 1:],
                "User": "65532:65532", "WorkingDir": "/", "Healthcheck": {"Test": ["NONE"]},
                "Env": [command[i + 1] for i, item in enumerate(command) if item == "--env"]},
            "HostConfig": {"NetworkMode": "none", "Privileged": False, "ReadonlyRootfs": True, "Runtime": "nvidia",
                "CapDrop": ["ALL"], "IpcMode": "private", "ShmSize": 2 << 30, "CgroupnsMode": "private",
                "Memory": 512 << 30, "MemorySwap": 512 << 30, "NanoCpus": 64000000000, "PidsLimit": 1024,
                "RestartPolicy": {"Name": "no"}, "LogConfig": {"Type": "none"},
                "Ulimits": [{"Name": "core", "Hard": 0, "Soft": 0}],
                "SecurityOpt": ["no-new-privileges"], "Tmpfs": dict(smoke.TMPFS),
                "DeviceRequests": [{"DeviceIDs": list(map(str, range(8))), "Capabilities": [["gpu"]],
                                    "Count": 0, "Driver": "nvidia"}]},
            "Mounts": [{"Type": "bind", "Source": self.args.weights, "Destination": "/model", "RW": False},
                {"Type": "bind", "Source": "/private/stage", "Destination": "/run/nib-inputs", "RW": False}],
            "NetworkSettings": {"Networks": {"none": {}}, "Ports": {}},
            "State": {"Status": "created", "Running": False, "ExitCode": 0, "OOMKilled": False}}

    def test_build_hash_source_join_and_closed_claims(self):
        self.assertEqual(self.approved(), self.build)
        for field, value in (("schema", "other"), ("result", "fail"), ("image_built", 1),
                ("published", True), ("runtime_started", True), ("deployable", True), ("hardware_validated", True),
                ("preparation_sha256", smoke.digest(b"wrong")), ("prepared_sha256", smoke.digest(b"wrong")),
                ("image_id", "floating:tag"), ("extra", "PRIVATE_CANARY")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.approved({**self.build, field: value})
        with self.assertRaises(ValueError):
            self.approved(expected=smoke.digest(b"wrong"))
        for field in self.build:
            with self.subTest(missing=field), self.assertRaises(ValueError):
                self.approved({key: value for key, value in self.build.items() if key != field})

    def test_image_identity_user_entry_and_layer_negatives(self):
        smoke.check_image(self.base, self.image, self.build, self.recorder, self.metadata, self.lab_contract)
        for scope, field, value in (("image", "Id", identity("wrong")), ("image", "Architecture", "arm64"),
                ("Config", "User", "0:0"), ("Config", "WorkingDir", "/tmp"),
                ("Config", "Cmd", ["--override"]), ("Config", "Entrypoint", ["sh"]),
                ("Config", "Env", ["PRIVATE_CANARY=value"]), ("Config", "Volumes", {"/model": {}}),
                ("Config", "ExposedPorts", {"8000/tcp": {}}),
                ("RootFS", "Layers", [identity("wrong layer")])):
            row = copy.deepcopy(self.image)
            (row if scope == "image" else row[scope])[field] = value
            with self.subTest(scope=scope, field=field), self.assertRaises(ValueError):
                smoke.check_image(self.base, row, self.build, self.recorder, self.metadata, self.lab_contract)

    def test_container_closed_gpu_memory_mount_network_and_command(self):
        command = smoke.container_command("nib-selected-" + "1" * 32, "2" * 32, self.build, self.base,
            self.args.weights, "/private/stage", self.args, self.args.hard_stop_at)
        info = self.container(command)
        validate = lambda row: smoke.check_container(row, info["Id"], self.name, self.token, self.build,
            self.image, command, self.args.weights, "/private/stage")
        validate(info)
        self.assertEqual(command[command.index("--ulimit") + 1], "core=0:0")
        for limits in (None, [], [{"Name": "core", "Hard": -1, "Soft": 0}],
                       [{"Name": "core", "Hard": 0, "Soft": 1}], info["HostConfig"]["Ulimits"] * 2):
            row = copy.deepcopy(info)
            row["HostConfig"]["Ulimits"] = limits
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                validate(row)
        self.assertNotIn("--publish", command)
        self.assertNotIn("--init", command)
        self.assertNotIn("--rm", command)
        self.assertIn('"device=0,1,2,3,4,5,6,7"', command)
        # Docker 28.0.4 --tmpfs uses HostConfig.Tmpfs, not inspect Mounts;
        # the task doc binds this synthetic fixture to the upstream source.
        self.assertEqual([command[i + 1] for i, item in enumerate(command) if item == "--tmpfs"],
                         [f"{path}:{options}" for path, options in smoke.TMPFS.items()])
        self.assertEqual(info["HostConfig"]["Tmpfs"], smoke.TMPFS)
        self.assertEqual([mount["Type"] for mount in info["Mounts"]], ["bind", "bind"])
        row = copy.deepcopy(info)
        row["Mounts"].append({"Type": "tmpfs", "Destination": "/tmp", "RW": True})
        with self.assertRaises(ValueError):
            validate(row)
        for scope, field, value in (("HostConfig", "NetworkMode", "host"),
                ("HostConfig", "ReadonlyRootfs", False), ("HostConfig", "Privileged", True),
                ("HostConfig", "Memory", 1 << 40), ("HostConfig", "MemorySwap", -1),
                ("HostConfig", "CapAdd", ["SYS_ADMIN"]), ("HostConfig", "PortBindings", {"8443/tcp": [{}]}),
                ("HostConfig", "DeviceRequests", []), ("HostConfig", "Tmpfs", {}),
                ("HostConfig", "Init", True), ("Config", "User", "root"),
                ("Config", "Cmd", []), ("Config", "Env", []), ("Config", "Labels", {}),
                ("NetworkSettings", "Networks", {"bridge": {}})):
            row = copy.deepcopy(info)
            row[scope][field] = value
            with self.subTest(scope=scope, field=field), self.assertRaises(ValueError):
                validate(row)
        for field, value in (("RW", True), ("Source", "/private/other"), ("Destination", "/host")):
            row = copy.deepcopy(info)
            row["Mounts"][0][field] = value
            with self.subTest(mount=field), self.assertRaises(ValueError):
                validate(row)

    def test_shared_runtime_is_not_network_admission_and_none_wrapper_stays_closed(self):
        command = smoke.container_command("nib-selected-" + "1" * 32, "2" * 32, self.build, self.base,
            self.args.weights, "/private/stage", self.args, self.args.hard_stop_at)
        info = self.container(command)
        arguments = (info["Id"], self.name, self.token, self.build, self.image, command,
                     self.args.weights, "/private/stage")
        for scope, field, value in (("Config", "ExposedPorts", {"8443/tcp": {}}),
                ("HostConfig", "NetworkMode", "bridge"),
                ("HostConfig", "PortBindings", {"8443/tcp": [{"HostPort": "443"}]}),
                ("HostConfig", "PublishAllPorts", True),
                ("NetworkSettings", "Networks", {"none": {}, "additional": {}}),
                ("NetworkSettings", "Ports", {"8443/tcp": [{"HostPort": "443"}]})):
            row = copy.deepcopy(info)
            row[scope][field] = value
            before = copy.deepcopy(row)
            with self.subTest(scope=scope, field=field):
                smoke.check_container_runtime(row, *arguments)
                self.assertEqual(row, before)
                with self.assertRaises(ValueError):
                    smoke.check_container(row, *arguments)
        with mock.patch.object(smoke, "check_container_runtime", wraps=smoke.check_container_runtime) as shared:
            smoke.check_container(info, *arguments)
        shared.assert_called_once_with(info, *arguments)

    def test_shared_runtime_retains_identity_resources_devices_and_mount_guards(self):
        command = smoke.container_command("nib-selected-" + "1" * 32, "2" * 32, self.build, self.base,
            self.args.weights, "/private/stage", self.args, self.args.hard_stop_at)
        info = self.container(command)
        arguments = (info["Id"], self.name, self.token, self.build, self.image, command,
                     self.args.weights, "/private/stage")
        mutations = ((None, "Id", "wrong"), (None, "Name", "/wrong"), (None, "Image", identity("wrong")),
            ("Config", "Image", identity("wrong")), ("Config", "Labels", {}),
            ("Config", "Entrypoint", ["sh"]), ("Config", "Cmd", []), ("Config", "User", "0:0"),
            ("Config", "WorkingDir", "/tmp"), ("Config", "Tty", True), ("Config", "OpenStdin", True),
            ("Config", "Healthcheck", {"Test": ["CMD", "true"]}), ("Config", "Env", []),
            ("HostConfig", "Privileged", True), ("HostConfig", "ReadonlyRootfs", False),
            ("HostConfig", "Runtime", "runc"), ("HostConfig", "CapDrop", []),
            ("HostConfig", "CapAdd", ["NET_ADMIN"]), ("HostConfig", "Devices", [{}]),
            ("HostConfig", "DeviceCgroupRules", ["a"]), ("HostConfig", "PidMode", "host"),
            ("HostConfig", "UTSMode", "host"), ("HostConfig", "VolumesFrom", ["other"]),
            ("HostConfig", "Binds", ["/host:/host"]), ("HostConfig", "GroupAdd", ["0"]),
            ("HostConfig", "Init", True), ("HostConfig", "IpcMode", "host"),
            ("HostConfig", "ShmSize", 1 << 30), ("HostConfig", "CgroupnsMode", "host"),
            ("HostConfig", "Memory", 1 << 40), ("HostConfig", "MemorySwap", -1),
            ("HostConfig", "NanoCpus", 128000000000), ("HostConfig", "PidsLimit", 2048),
            ("HostConfig", "RestartPolicy", {"Name": "always"}),
            ("HostConfig", "LogConfig", {"Type": "json-file"}), ("HostConfig", "SecurityOpt", []),
            ("HostConfig", "Tmpfs", {}), ("HostConfig", "DeviceRequests", []),
            (None, "Mounts", []))
        for scope, field, value in mutations:
            row = copy.deepcopy(info)
            (row if scope is None else row[scope])[field] = value
            for validate in (smoke.check_container_runtime, smoke.check_container):
                with self.subTest(scope=scope, field=field, validator=validate.__name__), self.assertRaises(ValueError):
                    validate(row, *arguments)
        for field, value in (("DeviceIDs", ["0"]), ("Capabilities", [["compute"]]),
                ("Count", -1), ("Driver", "other"), ("Options", {"override": "true"})):
            row = copy.deepcopy(info)
            row["HostConfig"]["DeviceRequests"][0][field] = value
            with self.subTest(device=field), self.assertRaises(ValueError):
                smoke.check_container_runtime(row, *arguments)
        for field, value in (("Type", "volume"), ("Source", "/wrong"), ("Destination", "/host"), ("RW", True)):
            row = copy.deepcopy(info)
            row["Mounts"][0][field] = value
            with self.subTest(mount=field), self.assertRaises(ValueError):
                smoke.check_container_runtime(row, *arguments)

    @contextmanager
    def running(self, *, failure=None, output=None, monitor_code=0):
        self.commands, self.info = [], None
        def call(command, env, *, timeout, limit):
            self.commands.append(command)
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 3000 - smoke.CLIENT_REAP)
            action = command[2]
            if action == "create":
                self.info = self.container(command)
                if failure == "create":
                    raise subprocess.TimeoutExpired(command, timeout, output=b"PRIVATE_CANARY")
                return self.info["Id"].encode()
            if action == "ls":
                return b"" if self.info is None else self.info["Id"].encode()
            if action == "inspect":
                return encoded(self.info)
            if action == "start":
                self.info["State"].update(Status="exited", Running=False)
                if isinstance(failure, BaseException):
                    raise failure
                return encoded(lifecycle()) if output is None else output
            if action == "rm":
                if failure == "cleanup":
                    raise PermissionError("PRIVATE_CANARY")
                self.info = None
                return b""
            self.fail("Unexpected Docker operation")
        monitor = mock.Mock(returncode=monitor_code)
        monitor.poll.return_value = None
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(smoke.time, "time", self.clock.time))
            stack.enter_context(mock.patch.object(smoke.time, "monotonic", self.clock.monotonic))
            stack.enter_context(mock.patch.object(smoke, "weights_view"))
            stack.enter_context(mock.patch.object(smoke.core_limits, "check_host"))
            stack.enter_context(mock.patch.object(smoke.service.identity.worker, "alarm", side_effect=lambda _: nullcontext()))
            stack.enter_context(mock.patch.object(smoke.subprocess, "Popen", return_value=monitor))
            stack.enter_context(mock.patch.object(self.recorder, "call", side_effect=call))
            stack.enter_context(mock.patch.object(self.recorder.snapshot, "cleanup_process_group"))
            contract = smoke.service.Contract.begin(self.args.hard_stop_at, input_price="1", output_price="2")
            result = smoke.report()
            yield contract, result

    def run_owner(self, contract, result):
        smoke.run_container(self.args, contract, self.lab, self.recorder, self.lab.controlled_env(self.args.docker_host),
            self.build, self.base, self.image, Path("/private/stage"), result)

    def test_single_create_start_success_requires_both_cleanup_joins(self):
        with self.running() as (contract, result):
            self.run_owner(contract, result)
        self.assertEqual(result["result"], "pass")
        self.assertEqual(result["cleanup"], "confirmed")
        self.assertEqual(sum(command[2] == "create" for command in self.commands), 1)
        self.assertEqual(sum(command[2] == "start" for command in self.commands), 1)
        create = next(command for command in self.commands if command[2] == "create")
        self.assertEqual(create[-1], self.clock.timestamp(3000 - smoke.HOST_RESERVE))
        self.assertNotIn("private-name", encoded(result).decode())
        self.assertNotIn("/private", encoded(result).decode())
        for field in ("deployable", "hardware_validated", "confidential", "zdr", "e2ee", "tools", "reasoning"):
            self.assertIs(result[field], False)

    def test_failed_or_interrupted_start_still_cleans_owned_id(self):
        for failure in (KeyboardInterrupt("PRIVATE_CANARY"), SystemExit("PRIVATE_CANARY"),
                ValueError("PRIVATE_CANARY"), smoke.service.identity.worker.Abort("timeout"),
                subprocess.CalledProcessError(1, "PRIVATE_CANARY", output=b"PRIVATE_CANARY")):
            with self.subTest(error=type(failure).__name__), self.running(failure=failure) as (contract, result):
                self.run_owner(contract, result)
                self.assertEqual(result["result"], "fail")
                self.assertEqual(result["cleanup"], "confirmed")
                self.assertIsNone(result["runtime_started"])
                self.assertNotIn("PRIVATE_CANARY", encoded(result).decode())
                self.assertIsNone(self.info)

    def test_unknown_create_completion_never_becomes_confirmed_success(self):
        with self.running(failure="create") as (contract, result):
            self.run_owner(contract, result)
        self.assertEqual(result["cleanup"], "create-completion-unknown")
        self.assertEqual(result["result"], "fail")
        self.assertFalse(any(command[2] == "start" for command in self.commands))
        self.assertIsNone(self.info)

    def test_nonzero_owner_failure_is_preserved_without_private_output(self):
        failed = {"schema": "nib.selected-service-lifecycle/v1", "status": "failed",
                  "reason": "preparation", "deployable": False, "hardware_validated": False}
        def execute(command, env, *, timeout, limit):
            raise subprocess.CalledProcessError(1, command, output=encoded(failed), stderr=b"PRIVATE_CANARY")
        with self.running() as (contract, result):
            original = self.recorder.call.side_effect
            def call(command, env, **kwargs):
                return execute(command, env, **kwargs) if command[2] == "start" else original(command, env, **kwargs)
            self.recorder.call.side_effect = call
            self.run_owner(contract, result)
        self.assertEqual(result["owner_failure"], failed)
        self.assertEqual(result["start_client_exit_code"], 1)
        self.assertEqual(result["result"], "fail")
        self.assertEqual(result["cleanup"], "confirmed")
        self.assertIsNone(result["runtime_started"])
        self.assertNotIn("PRIVATE_CANARY", encoded(result).decode())
        self.assertEqual(sum(command[2] == "create" for command in self.commands), 1)

    def test_failure_projection_is_closed_and_keeps_partial_cleanup(self):
        row = lifecycle()
        row.update(status="failed", reason="child-exited", ready=False, cleanup_confirmed=False)
        row["cleanup"] = [{"role": "engine", "drained": True, "reaped": False}]
        self.assertEqual(smoke.lifecycle_failure(encoded(row)), row)
        for key, value in (("reason", "PRIVATE_CANARY"), ("extra", "PRIVATE_CANARY"),
                           ("status", "retired"), ("ready", 1), ("confidential", True),
                           ("cleanup", [{"role": "tls", "drained": True, "reaped": True}])):
            with self.subTest(key=key), self.assertRaises(ValueError):
                smoke.lifecycle_failure(encoded({**row, key: value}))
        for raw in (b"PRIVATE_CANARY", b"x" * 16385, b'{}', None):
            with self.subTest(raw_type=type(raw)), self.assertRaises(ValueError):
                smoke.lifecycle_failure(raw)

    def test_nonzero_client_code_survives_rejected_output_without_runtime_claim(self):
        for code in (1, 125, 126, 127):
            with self.subTest(code=code), self.running() as (contract, result):
                original = self.recorder.call.side_effect
                def call(command, env, **kwargs):
                    if command[2] == "start":
                        raise subprocess.CalledProcessError(code, command, output=b"PRIVATE_CANARY",
                                                            stderr=b"PRIVATE_CANARY")
                    return original(command, env, **kwargs)
                self.recorder.call.side_effect = call
                self.run_owner(contract, result)
            self.assertEqual(result["start_client_exit_code"], code)
            self.assertNotIn("owner_failure", result)
            self.assertNotIn("PRIVATE_CANARY", encoded(result).decode())
            self.assertIsNone(result["runtime_started"])
            self.assertEqual(result["cleanup"], "confirmed")

    def test_cleanup_or_watchdog_failure_cannot_pass(self):
        for failure, code in (("cleanup", 0), (None, 1)):
            with self.subTest(failure=failure, code=code), self.running(failure=failure, monitor_code=code) as (contract, result):
                self.run_owner(contract, result)
                self.assertEqual(result["cleanup"], "unconfirmed")
                self.assertEqual(result["result"], "fail")

    def test_private_or_malformed_lifecycle_output_is_never_retained(self):
        for output in (b"PRIVATE_CANARY", encoded({**lifecycle(), "error": "PRIVATE_CANARY"}),
                encoded({**lifecycle(), "ready": False}), encoded({**lifecycle(), "confidential": True})):
            with self.subTest(output=output[:10]), self.running(output=output) as (contract, result):
                self.run_owner(contract, result)
                self.assertEqual(result["result"], "fail")
                self.assertNotIn("lifecycle", result)
                self.assertNotIn("PRIVATE_CANARY", encoded(result).decode())
        row = lifecycle()
        row["cleanup"][0]["drained"] = 1
        with self.assertRaises(ValueError):
            smoke.lifecycle(encoded(row))

    def test_cleanup_refuses_unowned_name_and_only_removes_exact_id(self):
        with self.running() as (contract, _):
            docker = smoke.Docker(self.recorder, {}, contract.hard_wall, contract.hard_monotonic)
            command = smoke.container_command("nib-selected-" + "1" * 32, "2" * 32, self.build, self.base,
                self.args.weights, "/private/stage", self.args, self.args.hard_stop_at)
            self.info = self.container(command)
            with self.assertRaises(ValueError):
                docker.cleanup(self.name, "3" * 32, self.build["image_id"])
            self.assertFalse(any(command[2] in ("rm", "kill") for command in self.commands))
            cid = self.info["Id"]
            docker.cleanup(self.name, self.token, self.build["image_id"], cid)
            removal = [command for command in self.commands if command[2] == "rm"]
            self.assertEqual(removal, [["docker", "container", "rm", "--force", cid]])

    def test_original_deadline_never_renews_on_wall_rollback(self):
        with mock.patch.object(smoke.time, "time", self.clock.time), \
                mock.patch.object(smoke.time, "monotonic", self.clock.monotonic), \
                mock.patch.object(self.recorder, "call", return_value=b"") as call:
            docker = smoke.Docker(self.recorder, {}, self.clock.wall + 100, self.clock.mono + 100)
            self.clock.wall -= 500
            self.assertEqual(docker.remaining(), 100)
            self.clock.wall += 580
            self.assertEqual(docker.remaining(), 20)
            self.clock.wall -= 500
            self.assertEqual(docker.remaining(), 20)
            with self.assertRaises(ValueError):
                docker.call(["docker", "container", "ls"])
            call.assert_not_called()

    def test_private_paths_cannot_inject_bind_mount_options(self):
        smoke.private_path("/private/" + "long-name-" * 100 + "/key")
        for value in ("relative", "/private/../root", "/private/a,readonly=false", "/private/a\nsecret"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                smoke.private_path(value)

    def test_private_snapshots_keep_originals_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            root.chmod(0o700)
            for name in ("provider.json", "api-key", "cert.pem", "key.pem"):
                (root / name).write_bytes(b"PRIVATE_CANARY")
                (root / name).chmod(0o600)
            originals = {name: ((root / name).read_bytes(), (root / name).stat().st_mode) for name in
                         ("provider.json", "api-key", "cert.pem", "key.pem")}
            @contextmanager
            def inputs(**_):
                yield types.SimpleNamespace(root=root, check=lambda: None)
            with mock.patch.object(smoke.service, "snapshot_inputs", inputs), \
                    mock.patch.object(smoke.os, "fchown") as file_owner, mock.patch.object(smoke.os, "chown") as directory_owner:
                with smoke.staged_inputs(self.args, self.lab) as stage:
                    self.assertEqual(stat.S_IMODE(stage.stat().st_mode), 0o700)
                    for name in originals:
                        self.assertEqual((stage / name).read_bytes(), b"PRIVATE_CANARY")
                        self.assertEqual(stat.S_IMODE((stage / name).stat().st_mode), 0o600)
                    self.assertEqual(file_owner.call_count, 4)
                    directory_owner.assert_called_once_with(stage, 65532, 65532)
            for name, expected in originals.items():
                self.assertEqual(((root / name).read_bytes(), (root / name).stat().st_mode), expected)

    def test_cli_does_not_echo_private_parse_errors(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(smoke.main(["--wrong", "PRIVATE_CANARY"]), 1)
        row = json.loads(output.getvalue())
        self.assertEqual(row["reason"], "contract")
        self.assertNotIn("PRIVATE_CANARY", output.getvalue())

    @contextmanager
    def admission(self, *, cleanup_error=False):
        @contextmanager
        def staged(*_):
            yield Path("/private/stage")
            if cleanup_error:
                raise OSError("PRIVATE_CANARY")
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(smoke.time, "time", self.clock.time))
            stack.enter_context(mock.patch.object(smoke.time, "monotonic", self.clock.monotonic))
            stack.enter_context(mock.patch.object(smoke.sys, "platform", "linux"))
            stack.enter_context(mock.patch.object(smoke.os, "getuid", return_value=0))
            stack.enter_context(mock.patch.object(smoke.service.identity.worker, "alarm", side_effect=lambda _: nullcontext()))
            stack.enter_context(mock.patch.object(smoke, "host_recorder", return_value=(self.lab, self.recorder)))
            stack.enter_context(mock.patch.object(self.lab, "read", return_value=encoded(self.build)))
            stack.enter_context(mock.patch.object(smoke.image_builder, "validated_files", return_value=(
                {"prepared_sha256": self.build["prepared_sha256"]}, {}, self.lab_contract)))
            stack.enter_context(mock.patch.object(self.recorder, "raw_metadata", return_value=self.metadata))
            inspect = stack.enter_context(mock.patch.object(self.recorder, "docker_json", side_effect=[self.base, self.image]))
            stack.enter_context(mock.patch.object(smoke, "staged_inputs", staged))
            run = stack.enter_context(mock.patch.object(smoke, "run_container"))
            yield inspect, run

    def test_preflight_joins_approved_preparation_before_any_start(self):
        self.args.run = False
        with self.admission() as (inspect, run):
            row = smoke.preflight(self.args)
            self.assertTrue(row["preflight_pass"])
            self.assertEqual(row["reason"], "not-run")
            self.assertEqual(row["result"], "fail")
            self.assertEqual(inspect.call_count, 2)
            run.assert_not_called()
        self.assertNotIn("private", encoded({key: value for key, value in row.items() if key != "reason"}).decode())

    def test_shared_admission_yields_once_outside_setup_alarm_with_inputs_alive(self):
        events = []
        @contextmanager
        def staged(*_):
            events.append("stage-enter")
            try:
                yield Path("/private/stage")
            finally:
                events.append("stage-exit")
        @contextmanager
        def alarm(deadline):
            self.assertEqual(deadline, self.clock.mono + smoke.SECONDS)
            events.append("alarm-enter")
            try:
                yield
            finally:
                events.append("alarm-exit")
        with self.admission() as (inspect, run), mock.patch.object(smoke, "staged_inputs", staged), \
                mock.patch.object(smoke.service.identity.worker, "alarm", alarm), \
                mock.patch.object(smoke.service.Contract, "begin", wraps=smoke.service.Contract.begin) as begin:
            result = smoke.report()
            with smoke.admitted_runtime(self.args, result) as admitted:
                self.assertEqual(events, ["alarm-enter", "stage-enter", "alarm-exit"])
                contract, lab, recorder, env, build, base, image, inputs = admitted
                self.assertIs(lab, self.lab)
                self.assertIs(recorder, self.recorder)
                self.assertEqual(env, self.lab.controlled_env(self.args.docker_host))
                self.assertEqual((build, base, image, inputs),
                                 (self.build, self.base, self.image, Path("/private/stage")))
                self.assertEqual(inspect.call_count, 2)
                run.assert_not_called()
                bounds = contract.hard_wall, contract.hard_monotonic, contract.startup_deadline
                self.clock.advance(200)
                self.clock.wall -= 400
                self.assertEqual(bounds, (contract.hard_wall, contract.hard_monotonic, contract.startup_deadline))
                self.assertTrue(result["preflight_pass"])
                self.assertEqual((result["result"], result["reason"]), ("fail", "not-run"))
                events.append("consumer")
            begin.assert_called_once_with(self.args.hard_stop_at, input_price="1", output_price="2")
        self.assertEqual(events, ["alarm-enter", "stage-enter", "alarm-exit", "consumer", "stage-exit"])

    def test_shared_admission_consumer_cancellation_closes_inputs_and_preserves_error(self):
        for failure in (KeyboardInterrupt("PRIVATE_CANARY"), SystemExit("PRIVATE_CANARY"),
                        ValueError("PRIVATE_CANARY")):
            events = []
            @contextmanager
            def staged(*_):
                events.append("enter")
                try:
                    yield Path("/private/stage")
                finally:
                    events.append("exit")
            with self.admission(), mock.patch.object(smoke, "staged_inputs", staged):
                result = smoke.report()
                with self.assertRaises(type(failure)) as caught:
                    with smoke.admitted_runtime(self.args, result):
                        raise failure
                self.assertIs(caught.exception, failure)
            self.assertEqual(events, ["enter", "exit"])
            self.assertNotIn("PRIVATE_CANARY", encoded(result).decode())

    def test_shared_admission_expired_staging_never_yields_and_still_cleans(self):
        events = []
        @contextmanager
        def staged(*_):
            try:
                self.clock.advance(smoke.SECONDS + 1)
                yield Path("/private/stage")
            finally:
                events.append("cleaned")
        with self.admission() as (_, run), mock.patch.object(smoke, "staged_inputs", staged):
            result = smoke.report()
            with self.assertRaises(ValueError):
                with smoke.admitted_runtime(self.args, result):
                    self.fail("expired inputs must not be handed off")
            run.assert_not_called()
        self.assertEqual(events, ["cleaned"])
        self.assertFalse(result["preflight_pass"])

    def test_shared_admission_preserves_host_thread_lifetime_and_name_guards(self):
        for target, attribute, value in ((smoke.sys, "platform", "darwin"), (smoke.os, "getuid", lambda: 1),
                                        (smoke.threading, "current_thread", lambda: object())):
            with self.subTest(attribute=attribute), self.admission(), \
                    mock.patch.object(target, attribute, value), \
                    mock.patch.object(smoke, "host_recorder") as load:
                with self.assertRaises(ValueError):
                    with smoke.admitted_runtime(self.args, smoke.report()):
                        self.fail("invalid host must not yield")
                load.assert_not_called()
        for key, value in (("hostname", "private.invalid/extra"), ("hard_stop_at", self.clock.timestamp(4801))):
            args = copy.copy(self.args)
            setattr(args, key, value)
            with self.subTest(key=key), self.admission(), mock.patch.object(smoke, "host_recorder") as load:
                with self.assertRaises(ValueError):
                    with smoke.admitted_runtime(args, smoke.report()):
                        self.fail("invalid contract must not yield")
                load.assert_not_called()

    def test_shared_admission_rejects_source_failure_before_image_or_inputs(self):
        failure = ValueError("PRIVATE_CANARY")
        with self.admission() as (inspect, run), \
                mock.patch.object(smoke.image_builder, "validated_files", side_effect=failure) as validate, \
                mock.patch.object(smoke, "staged_inputs") as staged:
            result = smoke.report()
            with self.assertRaises(ValueError) as caught:
                with smoke.admitted_runtime(self.args, result):
                    self.fail("invalid preparation must not yield")
            self.assertIs(caught.exception, failure)
            validate.assert_called_once_with(Path(self.args.preparation), self.args.preparation_sha256)
            inspect.assert_not_called()
            staged.assert_not_called()
            run.assert_not_called()
        self.assertEqual(result["reason"], "build-identity")
        self.assertFalse(result["preflight_pass"])
        self.assertNotIn("PRIVATE_CANARY", encoded(result).decode())

    def test_shared_admission_inspection_callback_is_closed_and_absolute_bounded(self):
        callbacks = []
        def inspected(kind, name, env, execute):
            callbacks.append(execute)
            return self.base if name == self.recorder.overlay.BASE_IMAGE else self.image
        with self.admission() as (inspect, _), \
                mock.patch.object(self.recorder.snapshot, "execute_process_group") as execute:
            inspect.side_effect = inspected
            with smoke.admitted_runtime(self.args, smoke.report()):
                self.assertIs(callbacks[0], callbacks[1])
                callback = callbacks[0]
                command = ["docker", "image", "inspect", "--format", "{{json .}}", self.build["image_id"]]
                callback(command, env={}, timeout=100)
                self.assertEqual(execute.call_args.kwargs["timeout"], smoke.SECONDS - smoke.CLIENT_REAP)
                self.clock.advance(20)
                self.clock.wall -= 100
                callback(command, env={}, timeout=100)
                self.assertEqual(execute.call_args.kwargs["timeout"], smoke.SECONDS - smoke.CLIENT_REAP - 20)
                for bad in (["docker", "run", "unapproved"], command[:-1] + [identity("unapproved")],
                            command + ["extra"]):
                    with self.assertRaises(ValueError):
                        callback(bad, env={}, timeout=100)
                self.clock.advance(smoke.SECONDS - smoke.CLIENT_REAP - 20)
                with self.assertRaises(ValueError):
                    callback(command, env={}, timeout=100)
                self.assertEqual(execute.call_count, 2)

    def test_bad_build_hash_and_remote_daemon_never_inspect_or_start(self):
        for key, value in (("build_sha256", smoke.digest(b"wrong")), ("docker_host", "tcp://private.invalid:2375")):
            args = copy.copy(self.args)
            setattr(args, key, value)
            with self.subTest(field=key), self.admission() as (inspect, run):
                row = smoke.preflight(args)
                self.assertFalse(row["preflight_pass"])
                inspect.assert_not_called()
                run.assert_not_called()

    def test_preflight_docker_output_exception_chain_never_leaks(self):
        with self.admission() as (inspect, run):
            failure = ValueError("PRIVATE_CANARY")
            failure.__cause__ = OSError("private certificate/path/name")
            inspect.side_effect = failure
            row = smoke.preflight(self.args)
            self.assertEqual(row["reason"], "image-identity")
            self.assertNotIn("PRIVATE_CANARY", encoded(row).decode())
            self.assertNotIn("certificate/path", encoded(row).decode())
            run.assert_not_called()

    def test_private_input_teardown_failure_cannot_retain_pass(self):
        with self.admission(cleanup_error=True) as (_, run):
            def completed(*args):
                args[-1].update(result="pass", status="PASS", reason="completed")
            run.side_effect = completed
            row = smoke.preflight(self.args)
        self.assertEqual(row["result"], "fail")
        self.assertEqual(row["reason"], "private-input-cleanup")
        self.assertNotIn("PRIVATE_CANARY", encoded(row).decode())

    def test_failed_graceful_signal_still_attempts_owned_forced_removal(self):
        with self.running() as (contract, _):
            docker = smoke.Docker(self.recorder, {}, contract.hard_wall, contract.hard_monotonic)
            command = smoke.container_command("nib-selected-" + "1" * 32, "2" * 32, self.build, self.base,
                self.args.weights, "/private/stage", self.args, self.args.hard_stop_at)
            self.info = self.container(command)
            self.info["State"].update(Status="running", Running=True)
            original = docker.call
            def call(command, **kwargs):
                if command[2] == "kill":
                    raise PermissionError("PRIVATE_CANARY")
                return original(command, **kwargs)
            with mock.patch.object(docker, "call", side_effect=call):
                docker.cleanup(self.name, self.token, self.build["image_id"], self.info["Id"])
            self.assertIsNone(self.info)
            self.assertEqual(sum(command[2] == "rm" for command in self.commands), 1)

    def test_watchdog_parent_eof_retains_received_exact_id(self):
        reader, writer = os.pipe()
        cid = smoke.digest(b"owned container")
        os.write(writer, (cid + "\n").encode())
        os.close(writer)
        docker = mock.Mock()
        docker.remaining.return_value = 500
        with mock.patch.object(smoke.sys, "platform", "linux"), \
                mock.patch.object(smoke.os, "getuid", return_value=0), \
                mock.patch.object(smoke, "host_recorder", return_value=(self.lab, self.recorder)), \
                mock.patch.object(smoke, "Docker", return_value=docker):
            self.assertEqual(smoke.watchdog("nib-selected-" + "1" * 32, "2" * 32, self.build["image_id"],
                "1000", "1000", str(reader), self.args.docker_host), 0)
        docker.cleanup.assert_called_once_with("nib-selected-" + "1" * 32, "2" * 32, self.build["image_id"], cid)
        with self.assertRaises(OSError):
            os.fstat(reader)

    def test_watchdog_unknown_create_is_bounded_and_never_starts(self):
        reader, writer = os.pipe()
        os.close(writer)
        docker = mock.Mock()
        docker.remaining.return_value = 500
        with mock.patch.object(smoke.sys, "platform", "linux"), \
                mock.patch.object(smoke.os, "getuid", return_value=0), \
                mock.patch.object(smoke.time, "monotonic", self.clock.monotonic), \
                mock.patch.object(smoke.time, "sleep", self.clock.advance), \
                mock.patch.object(smoke, "host_recorder", return_value=(self.lab, self.recorder)), \
                mock.patch.object(smoke, "Docker", return_value=docker):
            self.assertEqual(smoke.watchdog("nib-selected-" + "1" * 32, "2" * 32, self.build["image_id"],
                "1000", "1000", str(reader), self.args.docker_host), 0)
        self.assertEqual(self.clock.mono, 140)
        self.assertEqual(docker.cleanup.call_count, 41)
        self.assertTrue(all(call.args[-1] is None for call in docker.cleanup.call_args_list))
        self.assertEqual({call[0] for call in docker.method_calls}, {"remaining", "cleanup"})

    def test_watchdog_fires_at_original_bound_even_if_parent_pipe_stays_open(self):
        reader, writer = os.pipe()
        cid = smoke.digest(b"owned container")
        os.write(writer, (cid + "\n").encode())
        docker = mock.Mock()
        initial = self.clock.mono
        docker.remaining.side_effect = lambda: 100 - (self.clock.mono - initial)
        first = True
        def wait(readers, *_):
            nonlocal first
            if first:
                first = False
                return readers, [], []
            self.clock.advance(1)
            return [], [], []
        try:
            with mock.patch.object(smoke.sys, "platform", "linux"), \
                    mock.patch.object(smoke.os, "getuid", return_value=0), \
                    mock.patch.object(smoke.time, "monotonic", self.clock.monotonic), \
                    mock.patch.object(smoke.select, "select", side_effect=wait), \
                    mock.patch.object(smoke, "host_recorder", return_value=(self.lab, self.recorder)), \
                    mock.patch.object(smoke, "Docker", return_value=docker):
                self.assertEqual(smoke.watchdog("nib-selected-" + "1" * 32, "2" * 32, self.build["image_id"],
                    "1000", "1000", str(reader), self.args.docker_host), 0)
            self.assertEqual(self.clock.mono - initial, 40)
            docker.cleanup.assert_called_once_with("nib-selected-" + "1" * 32, "2" * 32, self.build["image_id"], cid)
        finally:
            os.close(writer)


if __name__ == "__main__":
    unittest.main()
