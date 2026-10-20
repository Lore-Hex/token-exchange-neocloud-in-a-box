# SPDX-License-Identifier: Apache-2.0
"""Real fake-tree application, local bootstrap and mocked Docker; no image run."""

import copy
from contextlib import contextmanager
import json
import math
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
RECORDED = ROOT / "evidence/p0/2026-10-20-official-vllm-import-smoke"
RECORDED_SOURCE = "4b6b90613cfcd806ea0d40bf0b1aa7a415880313"
NUMPY_RECORDED = ROOT / "evidence/p0/2026-10-20-official-vllm-numpy-startup-smoke"
DENIED_RECORDED = ROOT / "evidence/p0/2026-10-20-official-vllm-denied-process-smoke"


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
    def callsites(self):
        return {"sources": [{"id": row["id"], "status": "verified"} for row in smoke.callsite_specs()],
                "ids": [], "frames_scanned": 1, "unmatched_frames": 1, "scan_truncated": False,
                "output_truncated": False, "comparison_failed": False}

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
        row = {"state": state, "guard_rejections": failures, "first_state_mismatch": None,
               "first_denied_process": None, "dependency_entrypoint_loads": 1,
               "imports": [{"module": name, "status": "pass", "error_category": None} for name in smoke.MODULES]}
        grandchild = {**copy.deepcopy(row), "before_unpickle": state}
        report = {"schema": "nib.engine-import-smoke/v1", "status": "BLOCKED", "deployable": False, "image": None,
                "abi_validated": False, "hardware_validated": False, "inference_started": False, "stage": "complete",
                "phase_elapsed_ms": {"application-preflight": 1000, "application": 672000, "import-smoke": 2000},
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
        self.assertEqual({key: contract["environment"][key] for key in smoke.IMPORT_ENVIRONMENT}, {
            "PYTORCH_NVML_BASED_CUDA_CHECK": "1", "TORCHINDUCTOR_COMPILE_THREADS": "1",
            "TRITON_CACHE_AUTOTUNING": "1", "TILELANG_CLEANUP_TEMP_FILES": "1", "TORCH_DEVICE_BACKEND_AUTOLOAD": "0",
            "OTEL_PYTHON_CONTEXT": "contextvars_context", "OTEL_PROPAGATORS": "none",
            "OPENBLAS_MAIN_FREE": "1", "GOTOBLAS_MAIN_FREE": "1"})
        self.assertEqual(contract["import_environment_sources"], list(smoke.ENVIRONMENT_SOURCES))
        self.assertEqual(contract["process_callsite_sources"], smoke.callsite_specs())
        self.assertEqual(len(contract["process_callsite_sources"]), 12)
        self.assertEqual(sum(row["bytes"] for row in contract["process_callsite_sources"]), 537634)
        self.assertEqual(contract["metadata_source_sha256"], tool.bootstrap.METADATA_SOURCES)
        self.assertEqual(contract["permitted_dependency_entrypoint"], list(tool.bootstrap.CONTEXT_ENTRYPOINT))
        self.assertEqual([p["revision"] for p in contract["import_environment_sources"]],
                         ["5df63a7203cdb9328d824a27b682e77c7b9928be"] * 2 + ["cf30153c4c131c8164ee7798e5022d810682e2cb"] * 2
                         + ["2b686f659642080e2fc708719385de6e8be0955f"])
        self.assertEqual(contract["import_environment_sources"][-1], {
            "repository": "numpy/numpy", "revision": "2b686f659642080e2fc708719385de6e8be0955f",
            "path": "numpy/_core/__init__.py", "bytes": 5612, "git_blob": "4b90877138a3ae5e06f35eb668200931073f394b",
            "sha256": "1fde7ecd24f4087ea99e739b8d75175e23e0b6e6fd337e4804669813eab8c2b5"})
        self.assertNotIn("DISABLE_CUPTI_LAZY_REINIT", contract["environment"])
        self.assertNotIn("TEARDOWN_CUPTI", contract["environment"])
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
                            (["smoke", "fresh", "imports", 0, "error_category"], "synthetic-private"),
                            (["smoke", "fresh", "first_state_mismatch"], "synthetic-private"),
                            (["smoke", "fresh", "first_state_mismatch"], "env"),
                            (["smoke", "fresh", "dependency_entrypoint_loads"], True),
                            (["smoke", "fresh", "dependency_entrypoint_loads"], -1),
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
            row["imports"][0]["error_category"] = "import"
            for item in row["imports"][1:]:
                item["status"] = "not-run"
            self.assertFalse(smoke.validate(overlay, report, self.application, self.measured))
        report = copy.deepcopy(original)
        report["smoke"]["fresh"]["guard_rejections"]["process"] = 1
        report["smoke"]["fresh"]["first_denied_process"] = {
            "event": "subprocess.Popen", "command": "other", "caller": "other", "callsites": self.callsites()}
        self.assertFalse(smoke.validate(overlay, report, self.application, self.measured))

    def test_process_diagnostics_are_required_bounded_and_counter_consistent(self):
        original = self.valid_report()
        for key in ("fresh", "spawn", "grandchild"):
            report = copy.deepcopy(original)
            row = report["smoke"]["spawn"]["grandchild"] if key == "grandchild" else report["smoke"][key]
            row["guard_rejections"]["process"] = 1
            for event in tool.bootstrap.PROCESS_EVENTS:
                for command in tool.bootstrap.PROCESS_COMMANDS:
                    for caller in (*tool.bootstrap.PROCESS_CALLERS.values(), "other"):
                        row["first_denied_process"] = {"event": event, "command": command, "caller": caller, "callsites": self.callsites()}
                        if command != "other" and event not in ("subprocess.Popen", "os.exec", "os.posix_spawn"):
                            with self.assertRaises(ValueError):
                                smoke.validate(overlay, report, self.application, self.measured)
                        else:
                            # A caught denial must fail even if all imports claim pass.
                            self.assertFalse(smoke.validate(overlay, report, self.application, self.measured))
            valid = {"event": "subprocess.Popen", "command": "gxx-version", "caller": "torch-cpp-builder", "callsites": self.callsites()}
            for invalid in (None, {}, [], "private", True, {**valid, "args": ["private"]},
                            {"event": "subprocess.Popen", "command": "gxx-version"},
                            *({**valid, field: value} for field in valid for value in (None, True, [], {}, "private"))):
                row["first_denied_process"] = invalid
                with self.subTest(key=key, invalid_type=type(invalid).__name__), self.assertRaises((ValueError, TypeError)):
                    smoke.validate(overlay, report, self.application, self.measured)
            row["first_denied_process"] = valid
            for count in (0, True, -1, 1000001):
                row["guard_rejections"]["process"] = count
                with self.assertRaises(ValueError):
                    smoke.validate(overlay, report, self.application, self.measured)
            row["guard_rejections"]["process"] = 1
            del row["first_denied_process"]
            with self.assertRaises(ValueError):
                smoke.validate(overlay, report, self.application, self.measured)

    def test_state_diagnostics_are_fixed_and_consistent_with_failure_counters(self):
        for field in ("env", "paths", "finders", "argv", "cwd", "executable", "method", "pycache"):
            report = self.valid_report()
            row = report["smoke"]["fresh"]
            row["first_state_mismatch"] = field
            row["guard_rejections"]["state"] = 1
            # Even a caught mismatch followed by passing imports must fail.
            self.assertFalse(smoke.validate(overlay, report, self.application, self.measured))
            for invalid in (None, True, "", "synthetic-private-name=value"):
                row["first_state_mismatch"] = invalid
                with self.subTest(field=field, invalid=invalid), self.assertRaises((ValueError, TypeError)):
                    smoke.validate(overlay, report, self.application, self.measured)

    def test_application_failure_preserves_fixed_stage_and_no_claim(self):
        for stage in ("application-preflight", "application", "import-smoke"):
            report = self.valid_report()
            report.update(stage=stage, smoke=None, failure="bounded-smoke-stage-rejected")
            if stage != "import-smoke":
                report["application"] = None
            phases = list(report["phase_elapsed_ms"])
            for name in phases[phases.index(stage) + 1:]:
                report["phase_elapsed_ms"][name] = None
            self.assertFalse(smoke.validate(overlay, report, self.application, self.measured))
            report["failure"] = "private raw error"
            with self.assertRaises(ValueError):
                smoke.validate(overlay, report, self.application, self.measured)

    def test_application_budget_is_one_shared_deadline_for_all_eight_passes(self):
        for seconds, succeeds in ((84, True), (90, False)):
            fixture = fixtures.ApplicationTests()
            with fixture.wheel() as (_, tree, console, contract, measured):
                # Exactly representable fake epoch: borrowing a real fractional
                # clock can round (start + 720) - start above the strict cap.
                start = 0.0
                clock = [start]
                calls = []
                inventory = tree.inventory
                def counted():
                    calls.append(tree.deadline)
                    clock[0] += seconds
                    return inventory()
                with mock.patch.object(fixtures.overlay.time, "monotonic", side_effect=lambda: clock[0]):
                    smoke.application_budget(fixtures.overlay, fixtures.app, contract, tree, console, start + 720)
                    with mock.patch.object(tree, "inventory", side_effect=counted):
                        if succeeds:
                            before = fixtures.app.inspect(fixtures.overlay, tree, contract, measured, console)
                            fixtures.app.apply(fixtures.overlay, tree, contract, tool.checker.verify_stages(self.bundle), before, measured, console)
                            self.assertEqual(len(calls), 8)
                        else:
                            with self.assertRaises(ValueError):
                                before = fixtures.app.inspect(fixtures.overlay, tree, contract, measured, console)
                                fixtures.app.apply(fixtures.overlay, tree, contract, tool.checker.verify_stages(self.bundle), before, measured, console)
                    self.assertEqual(len(calls), 8)
                    self.assertGreater(clock[0] - start, 300)
                    self.assertEqual(set(calls), {start + 720})
                    self.assertEqual(console[1].deadline, start + 720)
        (self.root / "inventory-input").write_bytes(b"synthetic")
        with overlay.directory(self.root) as fd:
            start = time.monotonic()
            tree = overlay.Tree(fd)
            self.assertGreaterEqual(tree.deadline - start, 300)
            self.assertLessEqual(tree.deadline - start, 301)
            with mock.patch.object(overlay.time, "monotonic", return_value=tree.deadline):
                with self.assertRaises(ValueError):
                    tree.inventory()

    def test_budget_rejects_unknown_image_or_unbounded_extension(self):
        fixture = fixtures.ApplicationTests()
        with fixture.wheel() as (_, tree, console, contract, _):
            deadline = tree.deadline
            with mock.patch.object(fixtures.overlay.time, "monotonic", return_value=0.0):
                for value in (0, math.nextafter(720.0, math.inf), 721, float("inf")):
                    with self.subTest(value=value), self.assertRaises(ValueError):
                        smoke.application_budget(fixtures.overlay, fixtures.app, contract, tree, console, value)
                    self.assertEqual(tree.deadline, deadline)
            contract["base_image"] = "unknown"
            with self.assertRaises(ValueError):
                smoke.application_budget(fixtures.overlay, fixtures.app, contract, tree, console, time.monotonic() + 500)
            self.assertEqual(tree.deadline, deadline)

    def test_fixed_phase_timings_reject_arbitrary_or_inconsistent_values(self):
        original = self.valid_report()
        for timings in (None, {}, {**original["phase_elapsed_ms"], "private": "synthetic-secret"},
                        *({**original["phase_elapsed_ms"], "application": value}
                          for value in (True, -1, 1.5, "1", None, 720001, 1080001)),
                        {"application-preflight": 1000, "application": 672000, "import-smoke": 408000}):
            report = copy.deepcopy(original)
            report["phase_elapsed_ms"] = timings
            with self.subTest(timings=timings), self.assertRaises(ValueError):
                smoke.validate(overlay, report, self.application, self.measured)

    def test_phase_timings_and_cleanup_headroom_on_every_stage_failure(self):
        original = self.valid_report()
        for failure in (None, "application-preflight", "application", "import-smoke"):
            with self.subTest(failure=failure):
                clock = [0.0]
                def advance(phase, seconds):
                    clock[0] += seconds
                    if phase == failure:
                        raise ValueError("synthetic-private")
                def verify(*args):
                    advance("application-preflight", 1)
                def apply(*args):
                    advance("application", 672)
                    return original["application"]
                tree = types.SimpleNamespace(deadline=0)
                console = ("unused", types.SimpleNamespace(deadline=0))
                @contextmanager
                def selected(*args):
                    yield tree, console
                app = types.SimpleNamespace(verify_layout=verify, selected_tree=selected,
                    inspect=lambda *args: {}, apply=apply, require_contract=lambda *args: None)
                def execute(*args, **kwargs):
                    self.assertEqual(kwargs["timeout"], 300)
                    # Model the exact shared helper's 10s TERM + 10s KILL wait
                    # + 5s group-absence bound after an import timeout.
                    advance("import-smoke", 300 + 10 + 10 + 5)
                    return subprocess.CompletedProcess(args, 0, overlay.json_bytes(original["smoke"]), b"")
                processes = types.SimpleNamespace(execute_process_group=execute)
                with mock.patch("time.monotonic", side_effect=lambda: clock[0]), \
                     mock.patch.object(smoke, "load_module", return_value=processes), \
                     mock.patch.object(smoke, "require", overlay.require, create=True):
                    report = smoke.apply_and_smoke(self.contract, {"processes.py": b"unused"},
                        (overlay, None, None, app), {"files": []}, self.measured)
                reached = {None: 3, "application-preflight": 1, "application": 2, "import-smoke": 3}[failure]
                self.assertEqual(list(report["phase_elapsed_ms"].values()),
                    [1000, 672000, 325000][:reached] + [None] * (3 - reached))
                self.assertEqual(report["stage"], failure or "complete")
                self.assertEqual(smoke.validate(overlay, report, self.application, self.measured), failure is None)
                self.assertNotIn("synthetic-private", json.dumps(report))
                self.assertLess(clock[0], smoke.ATTACH_SECONDS)
        self.assertEqual((smoke.APPLICATION_SECONDS, smoke.IMPORT_SECONDS, smoke.ATTACH_SECONDS), (720, 300, 1080))
        self.assertEqual(smoke.ATTACH_SECONDS - smoke.APPLICATION_SECONDS - smoke.IMPORT_SECONDS - 25, 35)

    def test_actual_process_helper_cleanup_fits_outer_attach_budget(self):
        helper = tool.recorder.snapshot
        clock, waits = [float(smoke.APPLICATION_SECONDS)], []
        process = types.SimpleNamespace(pid=300000, stdout=None, stderr=None)
        def wait(*, timeout):
            waits.append(timeout)
            clock[0] += timeout
            if len(waits) == 1:
                raise subprocess.TimeoutExpired("synthetic", timeout)
            return 0
        process.wait = wait
        def output(*args):
            self.assertEqual(args[2], smoke.IMPORT_SECONDS)
            clock[0] += args[2]
            raise subprocess.TimeoutExpired("synthetic", args[2])
        def killpg(pid, signal):
            self.assertEqual(pid, process.pid)
            if signal == 0 and clock[0] >= 1045:
                raise ProcessLookupError()
        with mock.patch.object(helper.subprocess, "Popen", return_value=process) as popen, \
             mock.patch.object(helper, "bounded_output", side_effect=output), \
             mock.patch.object(helper.os, "killpg", side_effect=killpg), \
             mock.patch.object(helper.time, "monotonic", side_effect=lambda: clock[0]), \
             mock.patch.object(helper.time, "sleep", side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)):
            with self.assertRaises(subprocess.TimeoutExpired):
                helper.execute_process_group(["synthetic"], env={}, timeout=smoke.IMPORT_SECONDS, max_output_bytes=65536)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertEqual(waits, [10, 10])
        self.assertAlmostEqual(clock[0], 1045, delta=0.051)
        self.assertLess(clock[0], smoke.ATTACH_SECONDS)

    def test_preflight_and_post_apply_cannot_reset_or_overrun_deadline(self):
        for preflight, application_seconds, expected in ((720, 0, "application-preflight"), (719, 2, "application")):
            clock = [0.0]
            tree, console_tree = types.SimpleNamespace(deadline=0), types.SimpleNamespace(deadline=0)
            @contextmanager
            def selected(*args):
                yield tree, ("unused", console_tree)
            def verify(*args):
                clock[0] += preflight
            def apply(*args):
                self.assertEqual(tree.deadline, 720)
                clock[0] += application_seconds
                return {}
            app = types.SimpleNamespace(verify_layout=verify, selected_tree=selected, inspect=lambda *args: {},
                                        apply=apply, require_contract=lambda *args: None)
            with mock.patch("time.monotonic", side_effect=lambda: clock[0]), mock.patch.object(smoke, "load_module") as launch:
                report = smoke.apply_and_smoke({}, {}, (overlay, None, None, app), {"files": []}, self.measured)
            launch.assert_not_called()
            self.assertEqual(report["stage"], expected)
            self.assertIsNone(report["application"])
            self.assertFalse(smoke.validate(overlay, report, self.application, self.measured))

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
        with mock.patch("importlib.import_module", side_effect=ValueError("synthetic-secret")), \
             mock.patch.object(sys, "_nib_smoke_guard", {"failures": {"import": 0}}, create=True):
            rows = runtime.imports()
        self.assertEqual(rows, [{"module": name, "status": "rejected" if i == 0 else "not-run",
                                "error_category": "other" if i == 0 else None} for i, name in enumerate(smoke.MODULES)])
        self.assertNotIn("synthetic-secret", json.dumps(rows))

    def test_import_error_categories_are_fixed_and_guard_rejection_takes_priority(self):
        runtime = types.ModuleType("smoke_error_unit")
        exec(compile(self.extra[tool.BOOTSTRAP_FILE] + b"\n" + self.extra[tool.SMOKE], "unit", "exec"), runtime.__dict__)
        for kind, expected in ((ModuleNotFoundError, "missing-module"), (ImportError, "import"), (RuntimeError, "runtime"),
                               (OSError, "OS"), (ValueError, "other"), (SystemExit, "other")):
            for rejected in (0, 1):
                with self.subTest(kind=kind, rejected=rejected), \
                     mock.patch("importlib.import_module", side_effect=kind("synthetic-private")), \
                     mock.patch.object(sys, "_nib_smoke_guard", {"failures": {"import": rejected}}, create=True):
                    rows = runtime.imports()
                self.assertEqual(rows[0]["error_category"], "guard" if rejected else expected)
                self.assertNotIn("synthetic-private", json.dumps(rows))
                report = self.valid_report()
                report["smoke"]["fresh"]["imports"] = rows
                self.assertFalse(smoke.validate(overlay, report, self.application, self.measured))

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
                self.assertEqual(kwargs["timeout"], 1080)
                self.assertEqual(kwargs["max_output_bytes"], 65536)
                if failure == "runtime-timeout": raise subprocess.TimeoutExpired(args, 1080)
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
        for directory in (RECORDED, NUMPY_RECORDED, DENIED_RECORDED):
            recorded = self.root / "recorded/evidence/p0" / directory.name
            recorded.mkdir(parents=True)
            (recorded / "run.json").write_bytes(tool.read_source(directory / "run.json"))
        tool.recorder.call([str(binary), "lint", str(self.root / "recorded")], env, timeout=20)

    def test_recorded_application_pass_and_guard_rejection_preserve_source_and_failure(self):
        self.assert_recorded_attempt(RECORDED, RECORDED_SOURCE,
            run_sha="4d7c9d6e7a896747e48a0e7ae3a0aa63bec71c1229bafd5649c63cce97b8eacb",
            output_sha="fe1105ae2dc62f3d4853606450eec9d917fc3f9f115127edece651ef1d0da21b",
            recorder_sha="be0a7ee856d85d4543d5485ce4eacf65a9b157d0d5290f46dce1ec478c3a7b8d",
            payload_sha="069c23588dc7e2381dd14ef6fc4a41561bed2cfbcaa7e45424779afe56734938",
            timestamps=("2026-10-20T04:03:31Z", "2026-10-20T04:11:55Z"),
            elapsed={"application-preflight": 8342, "application": 482225, "import-smoke": 4611}, guard=("state", 2))

    def test_recorded_numpy_attempt_preserves_process_rejection_without_state_mismatch(self):
        self.assert_recorded_attempt(NUMPY_RECORDED, "1c8c6a5edac427bfe4724159090cd461b4bab94e",
            run_sha="e064e35a481842b6ef3f6e01ffc83a7398c945b2afe4c3da3e0fb5b47cf4befd",
            output_sha="9892780bdaec342752f70164571e0a867564a9b860cd9c9ba9ee29ddabd304c8",
            recorder_sha="e0ec2c93748663904d1282833ad93d2d6f3279c0dbcb426f54a75a51c6bd863b",
            payload_sha="4c14aaebaa180952d896dd5f990d1d0c225800650a0c925e564905cd106ed664",
            timestamps=("2026-10-20T04:47:36Z", "2026-10-20T04:56:00Z"),
            elapsed={"application-preflight": 8142, "application": 480489, "import-smoke": 6149}, guard=("process", 1))

    def test_recorded_denied_process_attempt_preserves_other_categories_without_cause_claim(self):
        self.assert_recorded_attempt(DENIED_RECORDED, "b72f7a0a62ea0e4cf9540fa0ef832dcc6c69ace3",
            run_sha="82b399c97e0bee0ccc3840cc08d61e2b7ac1555e1c3a191ee0056d750d28ba8a",
            output_sha="e7c486ad51a451b2ff2d15e5c069fcf4912c34461f2b38c63804ca9187565d9c",
            recorder_sha="cb40ae7f14ed503d04ab6bbc9e4b90d56d5cce2d13980ef7a3eb54312073873b",
            payload_sha="09e233ab1fb334849456967332ab58fcf9fb90ea00667b2f3029a4e5502844b3",
            timestamps=("2026-10-20T05:27:39Z", "2026-10-20T05:36:04Z"),
            elapsed={"application-preflight": 8146, "application": 482321, "import-smoke": 6036}, guard=("process", 1),
            denied={"event": "subprocess.Popen", "command": "other", "caller": "other"})

    def assert_recorded_attempt(self, directory, revision, *, run_sha, output_sha, recorder_sha, payload_sha,
                                timestamps, elapsed, guard, denied=None):
        documents = []
        for name, expected in (("run.json", run_sha), ("output.log", output_sha)):
            raw = tool.read_source(directory / name)
            self.assertLessEqual(len(raw), 65536)
            self.assertEqual(overlay.digest(raw), expected)
            documents.append(overlay.load_json(raw))
        run, report = documents
        self.assertEqual((run["git_sha"], run["result"], run["execution"]), (revision, "fail", "software"))
        self.assertEqual((run["started_utc"], run["ended_utc"]), timestamps)
        self.assertEqual(run["command"], "python3 -I -S scripts/record-engine-import-smoke.py --slug " + directory.name[11:])
        self.assertFalse(run["hardware_validated"])

        # Rebuild the complete payload with its historical helper closure. This
        # compiles only committed repository helpers, never Docker or engine code.
        source_root = self.root / "source"
        names = (tool.SCRIPT, tool.BUILDER, tool.BOOTSTRAP_FILE, tool.SMOKE, tool.builder.APPLICATION,
                 tool.recorder.SCRIPT, tool.recorder.prep.SCRIPT, tool.recorder.prep.INSPECTOR, tool.checker.SCRIPT,
                 tool.recorder.SNAPSHOT, tool.recorder.DIAGNOSTIC, tool.recorder.DESCRIPTOR)
        for name in names:
            raw = tool.checker.git(ROOT, "show", revision + ":" + name)
            self.assertLessEqual(len(raw), 2 << 20)
            path = source_root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        source = (source_root / tool.SCRIPT).read_bytes()
        self.assertEqual(overlay.digest(source), recorder_sha)
        historical = types.ModuleType("recorded_import_smoke")
        historical.__file__ = str(source_root / tool.SCRIPT)
        exec(compile(source, historical.__file__, "exec"), historical.__dict__)
        original_git = historical.checker.git
        def source_git(repo, *args, **kwargs):
            if args == ("rev-parse", "--verify", "HEAD^{commit}"):
                return (revision + "\n").encode()
            return original_git(repo, *args, **kwargs)
        with mock.patch.object(historical.checker, "git", side_effect=source_git):
            files, contract = historical.prepare_files(*historical.inputs(ROOT))
        payload = overlay.bundle_digest(files)
        self.assertEqual(payload, payload_sha)
        provenance = report["provenance"]
        self.assertEqual(provenance["git_sha"], revision)
        self.assertEqual(provenance["payload_sha256"], payload)
        self.assertEqual(provenance["source_bundle_sha256"], contract["source_bundle_sha256"])
        self.assertEqual(provenance["validator_source_sha256"], "c18f7f7028758f683f01a67806cbe5db9d44f503c6f58357393329b01a243232")
        self.assertEqual(provenance["measurement_sha256"], contract["measurement_sha256"])
        self.assertEqual(provenance["expected_config_sha256"], contract["official_config"]["sha256"])
        self.assertEqual(provenance["requested_image"], overlay.BASE_IMAGE)
        self.assertTrue(provenance["raw_oci_metadata_verified"])
        self.assertTrue(report["image"]["raw_config_link_verified"])
        self.assertFalse(report["image"]["local_config_bytes_rehashed"])
        self.assertFalse(report["image"]["local_layers_rehashed"])
        application = overlay.load_json(files["application/contract.json"])
        measured = historical.builder.application.measurement(overlay, files["application/measurement.json"])
        experiment = report["experiment"]
        self.assertFalse(historical.smoke.validate(overlay, experiment, application, measured))
        self.assertEqual((report["stage"], report["cleanup"], report["result"], report["smoke_pass"]),
                         ("smoke-rejected", "confirmed", "fail", False))
        self.assertEqual(experiment["stage"], "complete")
        self.assertEqual(experiment["phase_elapsed_ms"], elapsed)
        applied = experiment["application"]
        self.assertEqual(applied["stages"], 5)
        self.assertTrue(applied["native_inventory_unchanged"])
        self.assertEqual(applied["removed_bytecode_files"], 2879)
        self.assertFalse(applied["packaging"]["plugins_loaded"])  # pre-import application only
        self.assertFalse(experiment["inference_started"])
        self.assertEqual(experiment["smoke"]["tracker"], {"launched": True, "exit_zero": True})
        for row in (experiment["smoke"]["fresh"], experiment["smoke"]["spawn"], experiment["smoke"]["spawn"]["grandchild"]):
            self.assertEqual(row["guard_rejections"], {key: guard[1] if key == guard[0] else 0 for key in row["guard_rejections"]})
            if guard[0] == "process":
                self.assertIsNone(row["first_state_mismatch"])
            if denied is None:
                self.assertNotIn("first_denied_process", row)
            else:
                self.assertEqual(row["first_denied_process"], denied)
            self.assertEqual(row["dependency_entrypoint_loads"], 0)
            self.assertEqual(row["imports"], [{"module": name, "status": "rejected" if i == 0 else "not-run",
                                               "error_category": "guard" if i == 0 else None}
                                              for i, name in enumerate(historical.smoke.MODULES)])
        for row in (report, experiment, applied):
            self.assertTrue(all(row[key] is False for key in ("deployable", "abi_validated", "hardware_validated")))


if __name__ == "__main__":
    unittest.main()
