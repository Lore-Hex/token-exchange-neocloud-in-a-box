# SPDX-License-Identifier: Apache-2.0
"""Real fake-tree application, local bootstrap and mocked Docker; no image run."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def load(name, relative):
    module = types.ModuleType(name)
    path = ROOT / relative
    module.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    return module


tool = load("import_smoke_test", "scripts/record-engine-import-smoke.py")
fixtures = load("import_smoke_application_fixture", "scripts/test_engine_application.py")
overlay, smoke = tool.overlay, tool.smoke


class SmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ApplicationTests.setUpClass()
        cls.extra = dict(fixtures.ApplicationTests.extra)
        for name in (tool.SCRIPT, tool.BOOTSTRAP_FILE, tool.SMOKE, *tool.TESTS):
            cls.extra[name] = (ROOT / name).read_bytes()
        cls.bundle = tool.checker.working_bundle(ROOT)
        cls.files, cls.contract = tool.prepare_files("1" * 40, cls.bundle, cls.extra)
        cls.application = json.loads(cls.files["application/contract.json"])
        cls.measured = tool.builder.application.measurement(overlay, cls.extra[tool.builder.application.EVIDENCE])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nib-smoke-contract-", dir="/private/tmp" if sys.platform == "darwin" else None)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def application_receipt(self):
        # Obtain the exact writer shape from a real fake-wheel application.
        fixture = fixtures.ApplicationTests()
        with fixture.wheel() as (_, tree, console, contract, measured):
            before = fixtures.app.inspect(fixtures.overlay, tree, contract, measured, console)
            receipt = fixtures.app.apply(fixtures.overlay, tree, contract,
                tool.checker.verify_stages(self.bundle), before, measured, console)
        receipt.update(git_sha=self.application["git_sha"], source_bundle_sha256=self.application["source_bundle_sha256"],
                       removed_bytecode_files=2879)
        receipt["packaging"].update(policy_sha256=overlay.digest(overlay.json_bytes(overlay.PACKAGING_POLICY)),
            removal_plan_sha256=self.measured["packaging"]["removal_plan_sha256"], removed_unlisted_bytecode_files=2879)
        return receipt

    def valid_report(self):
        failures = dict.fromkeys(("state", "method", "executable", "preparation", "process", "import", "site"), 0)
        state = {"isolated": True, "no_site": True, "dont_write_bytecode": True, "before_unpickle": True,
                 "path_sha256": overlay.digest(overlay.json_bytes(self.measured["proposed_paths"])), "rejections": failures}
        row = {"state": state, "guard_rejections": failures,
               "imports": [{"module": name, "status": "pass"} for name in smoke.MODULES]}
        grandchild = {**copy.deepcopy(row), "before_unpickle": state}
        report = {"schema": "nib.engine-import-smoke/v1", "status": "BLOCKED", "deployable": False, "image": None,
                "abi_validated": False, "hardware_validated": False, "inference_started": False, "stage": "complete",
                "application": self.application_receipt(), "smoke": {"parent": state, "fresh": row,
                    "tracker": {"launched": True, "exit_zero": True},
                    "spawn": {**copy.deepcopy(grandchild), "grandchild": grandchild}}}
        return json.loads(json.dumps(report))

    def test_deterministic_source_bound_payload_and_sealed_environment(self):
        again, contract = tool.prepare_files("1" * 40, self.bundle, self.extra)
        self.assertEqual(again, self.files)
        self.assertEqual(contract, self.contract)
        for name, digest in contract["sources"].items():
            self.assertEqual(overlay.digest(self.files[name]), digest)
        self.assertEqual(set(self.files) - set(contract["sources"]), {"python", "smoke.json"})
        self.assertIn(self.extra[tool.BOOTSTRAP_FILE], self.files["launch.py"])
        self.assertIn(self.extra[tool.SMOKE], self.files["launch.py"])
        self.assertNotIn("Dockerfile", self.files)
        self.assertEqual(contract["source_bundle_sha256"], tool.checker.bundle_digest({**self.bundle, **self.extra}))
        with mock.patch.dict(os.environ, {"PYTHONPATH": "/private", "NUMACTL_PYTHON": "/private", "VLLM_PLUGINS": "private"}):
            self.assertEqual(tool.environment(), contract["environment"])
        self.assertEqual(contract["environment"]["VLLM_PLUGINS"], "")
        self.assertEqual(contract["environment"]["VLLM_WORKER_MULTIPROC_METHOD"], "spawn")
        self.assertIn(b"/usr/bin/env -i", self.files["python"])
        self.assertFalse(contract["deployable"])

    def test_real_writer_receipt_and_children_are_required(self):
        report = self.valid_report()
        self.assertTrue(smoke.validate(overlay, report, self.application, self.measured))
        for keys, value in ((["deployable"], True), (["abi_validated"], True), (["hardware_validated"], True),
                            (["application", "stages"], 4), (["application", "native_inventory_unchanged"], False),
                            (["application", "packaging", "removed_unlisted_bytecode_files"], 2878),
                            (["smoke", "parent", "isolated"], 1), (["smoke", "spawn", "before_unpickle"], None),
                            (["smoke", "tracker", "exit_zero"], False),
                            (["smoke", "spawn", "grandchild", "state", "path_sha256"], "0" * 64),
                            (["smoke", "fresh", "imports", 0, "module"], "private-url"),
                            (["private"], "synthetic-secret")):
            bad = copy.deepcopy(report)
            node = bad
            for key in keys[:-1]:
                node = node[key]
            node[keys[-1]] = value
            with self.subTest(keys=keys), self.assertRaises((ValueError, TypeError, KeyError)):
                smoke.validate(overlay, bad, self.application, self.measured)

    def test_import_failure_or_caught_guard_rejection_never_passes(self):
        original = self.valid_report()
        for name in ("fresh", "spawn", "grandchild"):
            report = copy.deepcopy(original)
            row = report["smoke"]["spawn"]["grandchild"] if name == "grandchild" else report["smoke"][name]
            row["imports"][0]["status"] = "rejected"
            for item in row["imports"][1:]:
                item["status"] = "not-run"
            self.assertFalse(smoke.validate(overlay, report, self.application, self.measured))
        report = copy.deepcopy(original)
        report["smoke"]["fresh"]["guard_rejections"]["process"] = 1
        self.assertFalse(smoke.validate(overlay, report, self.application, self.measured))

    def test_application_failure_preserves_fixed_stage_and_no_claim(self):
        for stage in ("application-preflight", "application", "import-smoke"):
            report = self.valid_report()
            report.update(stage=stage, smoke=None, failure="bounded-smoke-stage-rejected")
            if stage != "import-smoke":
                report["application"] = None
            self.assertFalse(smoke.validate(overlay, report, self.application, self.measured))
            report["failure"] = "private raw error"
            with self.assertRaises(ValueError):
                smoke.validate(overlay, report, self.application, self.measured)

    def test_application_budget_is_one_shared_deadline_for_all_eight_passes(self):
        for seconds, succeeds in ((40, True), (70, False)):
            fixture = fixtures.ApplicationTests()
            with fixture.wheel() as (_, tree, console, contract, measured):
                start = time.monotonic()
                clock = [start]
                calls = []
                inventory = tree.inventory
                def counted():
                    calls.append(tree.deadline)
                    clock[0] += seconds
                    return inventory()
                with mock.patch.object(fixtures.overlay.time, "monotonic", side_effect=lambda: clock[0]):
                    smoke.application_budget(fixtures.overlay, fixtures.app, contract, tree, console, start + 540)
                    with mock.patch.object(tree, "inventory", side_effect=counted):
                        if succeeds:
                            before = fixtures.app.inspect(fixtures.overlay, tree, contract, measured, console)
                            fixtures.app.apply(fixtures.overlay, tree, contract, tool.checker.verify_stages(self.bundle), before, measured, console)
                            self.assertEqual(len(calls), 8)
                        else:
                            with self.assertRaises(ValueError):
                                before = fixtures.app.inspect(fixtures.overlay, tree, contract, measured, console)
                                fixtures.app.apply(fixtures.overlay, tree, contract, tool.checker.verify_stages(self.bundle), before, measured, console)
                    self.assertEqual(set(calls), {start + 540})
                    self.assertEqual(console[1].deadline, start + 540)
        with overlay.directory(self.root) as fd:
            start = time.monotonic()
            self.assertLessEqual(overlay.Tree(fd).deadline - start, 301)

    def test_budget_rejects_unknown_image_or_unbounded_extension(self):
        fixture = fixtures.ApplicationTests()
        with fixture.wheel() as (_, tree, console, contract, _):
            deadline = tree.deadline
            for value in (0, time.monotonic() + 541, float("inf")):
                with self.assertRaises(ValueError):
                    smoke.application_budget(fixtures.overlay, fixtures.app, contract, tree, console, value)
                self.assertEqual(tree.deadline, deadline)
            contract["base_image"] = "unknown"
            with self.assertRaises(ValueError):
                smoke.application_budget(fixtures.overlay, fixtures.app, contract, tree, console, time.monotonic() + 500)
            self.assertEqual(tree.deadline, deadline)

    def test_publish_no_overwrite_and_source_reader_special_files(self):
        destination = self.root / "payload"
        tool.recorder.prep.publish(destination, self.files)
        self.assertEqual(tool.read_source(destination / "launch.py"), self.files["launch.py"])
        with self.assertRaises(FileExistsError):
            tool.recorder.prep.publish(destination, self.files)
        link = self.root / "link"
        link.symlink_to(destination / "launch.py")
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        for path in (link, fifo, destination):
            with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                tool.read_source(path)

    def test_fixed_import_errors_and_raw_output_suppression(self):
        # Exercise the actual imports/entry functions in a disposable interpreter.
        # Only fake modules are used; no Docker or installed dependency import.
        script = self.root / "runtime.py"
        script.write_text("import sys,types,os,json,hashlib\n" +
            "m=types.ModuleType('unit');m.__file__='unit'\n" +
            "exec(compile(" + repr(self.extra[tool.BOOTSTRAP_FILE] + b"\n" + self.extra[tool.SMOKE]) + ", 'unit', 'exec'),m.__dict__)\n" +
            "m.__name__='__main__'\n" +
            "def context(_): return ({}, {}, [], {}, {})\n" +
            "def apply(*args):\n print('synthetic-secret-output');print('synthetic-secret-error',file=sys.stderr);return {'fixed':'pass'}\n" +
            "m.context=context;m.apply_and_smoke=apply\n" +
            "sys.argv=['unit','0'*64,'apply'];m.entry()\n")
        result = subprocess.run([sys.executable, "-I", "-S", "-B", str(script)], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {"fixed": "pass"})
        self.assertEqual(result.stderr, b"")
        runtime = types.ModuleType("smoke_failure_unit")
        exec(compile(self.extra[tool.BOOTSTRAP_FILE] + b"\n" + self.extra[tool.SMOKE], "unit", "exec"), runtime.__dict__)
        with mock.patch("importlib.import_module", side_effect=ValueError("synthetic-secret")):
            rows = runtime.imports()
        self.assertEqual(rows, [{"module": name, "status": "rejected" if i == 0 else "not-run"} for i, name in enumerate(smoke.MODULES)])
        self.assertNotIn("synthetic-secret", json.dumps(rows))

    def daemon(self, result, *, failure=None):
        commands = []
        state = {"present": False, "started": False}
        config = {"config": {"Env": ["PATH=old", "PRIVATE_IMAGE_VALUE=synthetic-private"]}, "rootfs": {"diff_ids": ["sha256:" + "2" * 64]}}
        payload = self.root / "payload"
        tool.recorder.prep.publish(payload, self.files)
        sha = overlay.digest(self.files["smoke.json"])
        image = {"Id": "sha256:" + "3" * 64, "Os": "linux", "Architecture": "amd64",
                 "RepoDigests": [overlay.BASE_IMAGE], "RootFS": {"Type": "layers", "Layers": config["rootfs"]["diff_ids"]}}
        def info():
            return {"Id": "4" * 64, "Image": image["Id"], "Config": {"Labels": {"nib.import-smoke": state["owner"]},
                "Image": overlay.BASE_IMAGE, "Entrypoint": ["/usr/bin/env"], "Cmd": state["cmd"], "User": "0:0",
                "WorkingDir": "/", "Healthcheck": {"Test": ["NONE"]}, "Env": state["env"]},
                "State": {"Status": "exited" if state["started"] else "created", "ExitCode": 0, "OOMKilled": False},
                "HostConfig": {"NetworkMode": "none", "ReadonlyRootfs": False, "Privileged": False, "Runtime": "runc",
                    "CapDrop": ["ALL"], "IpcMode": "private", "ShmSize": 16 << 20, "CgroupnsMode": "private",
                    "Memory": 8 << 30, "MemorySwap": 8 << 30, "NanoCpus": 2000000000, "PidsLimit": 128,
                    "RestartPolicy": {"Name": "no"}, "LogConfig": {"Type": "none"}, "SecurityOpt": ["no-new-privileges"],
                    "Tmpfs": {"/tmp": "rw,nosuid,nodev,noexec,size=512m,mode=1777"}},
                "Mounts": [{"Type": "bind", "Source": str(payload), "Destination": "/opt/nib", "RW": False}]}
        def execute(args, **kwargs):
            commands.append(args)
            data = b""
            if args[1:3] == ["image", "inspect"]:
                data = overlay.json_bytes(image)
            elif args[1:3] == ["container", "ls"]:
                data = b"owned\n" if state["present"] else b""
            elif args[1:3] == ["container", "create"]:
                state.update(present=True, owner=args[args.index("--label") + 1].split("=", 1)[1],
                    cmd=args[args.index(overlay.BASE_IMAGE) + 1:], env=[args[i + 1] for i, value in enumerate(args) if value == "--env"])
                self.assertEqual(args, tool.create_command(args[args.index("--name") + 1], state["owner"], payload, sha, config))
                if failure == "create-timeout":
                    raise subprocess.TimeoutExpired(args, 30)
                data = ("4" * 64).encode()
            elif args[1:3] == ["container", "inspect"]:
                value = info()
                if failure == "network": value["HostConfig"]["NetworkMode"] = "host"
                if failure == "ownership" and state["started"]: value["Config"]["Labels"] = {}
                if failure == "oom" and state["started"]: value["State"]["OOMKilled"] = True
                data = overlay.json_bytes(value)
            elif args[1:3] == ["container", "start"]:
                state["started"] = True
                self.assertEqual(kwargs["timeout"], 900)
                self.assertEqual(kwargs["max_output_bytes"], 65536)
                if failure == "runtime-timeout": raise subprocess.TimeoutExpired(args, 900)
                if failure == "interrupt": raise InterruptedError("synthetic-private")
                data = b"{bad private" if failure == "malformed" else overlay.json_bytes(result)
            elif args[1:3] == ["container", "rm"]:
                if failure == "removal": raise OSError("synthetic-private")
                state["present"] = False
            else:
                self.fail("unexpected Docker operation")
            return subprocess.CompletedProcess(args, 0, data, b"")
        return tool.run_container(payload, self.contract, config, {}, execute), commands, state

    def test_bounded_lifecycle_success_stays_non_qualifying_and_removes_owned_container(self):
        report, commands, state = self.daemon(self.valid_report())
        self.assertTrue(report["smoke_pass"])
        self.assertEqual(report["result"], "fail")
        self.assertEqual(report["cleanup"], "confirmed")
        self.assertFalse(state["present"])
        self.assertNotIn("synthetic-private", json.dumps(report))
        self.assertTrue(all(args[1] in {"image", "container"} for args in commands))
        create = next(args for args in commands if args[1:3] == ["container", "create"])
        self.assertEqual(create[create.index("--pull") + 1], "never")
        self.assertIn("PRIVATE_IMAGE_VALUE=", create)
        self.assertNotIn("PRIVATE_IMAGE_VALUE=synthetic-private", create)

    def test_lifecycle_failure_unknown_completion_and_ownership(self):
        valid = self.valid_report()
        for failure in ("create-timeout", "network", "runtime-timeout", "malformed", "oom", "interrupt", "ownership", "removal"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory(dir=self.root) as temporary:
                original = self.root
                self.root = Path(temporary)
                try:
                    report, commands, state = self.daemon(valid, failure=failure)
                finally:
                    self.root = original
                self.assertFalse(report["smoke_pass"])
                self.assertEqual(report["result"], "fail")
                self.assertNotIn("synthetic-private", json.dumps(report))
                if failure in {"create-timeout", "runtime-timeout", "malformed", "oom", "interrupt"}:
                    self.assertEqual(report["cleanup"], "completion-unknown")
                elif failure == "network":
                    self.assertEqual(report["cleanup"], "confirmed")
                    self.assertFalse(any(args[1:3] == ["container", "start"] for args in commands))
                elif failure == "ownership":
                    self.assertFalse(any(args[1:3] == ["container", "rm"] for args in commands))
                    self.assertTrue(state["present"])
                else:
                    self.assertEqual(report["cleanup"], "unknown")

    def test_generated_record_uses_authoritative_offline_schema(self):
        report = {"provenance": {"payload_sha256": overlay.bundle_digest(self.files)}}
        record = tool.evidence_record(tool.checker.git(ROOT, "rev-parse", "HEAD").decode().strip(), "unit-smoke", tool.recorder.now(), report)
        self.assertEqual(record["result"], "fail")
        self.assertIn("Imports execute pinned engine/dependency code", record["notes"])
        control = self.root / "control"
        env = tool.recorder.offline_environment(control)
        binary, _ = tool.recorder.build_validator(ROOT, record["git_sha"], self.root, env)
        tool.recorder.publish_evidence(record, report, binary, self.root, self.root / "evidence", env)
        self.assertEqual(json.loads((self.root / "evidence/run.json").read_bytes()), record)


if __name__ == "__main__":
    unittest.main()
