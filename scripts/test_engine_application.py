# SPDX-License-Identifier: Apache-2.0
"""Real synthetic files and prepared contracts; no image/engine/dependency imports."""

from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
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


prep = load("application_preparer_test", "scripts/prepare-engine-application.py")
app, overlay = prep.application, prep.overlay
fixtures = load("application_files_fixture", "scripts/test_engine_overlay.py")
layout_fixtures = load("application_layout_fixture", "scripts/test_engine_python_descriptor.py")


class ApplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.OverlayTests.setUpClass()
        cls.bundle = prep.checker.working_bundle(ROOT)
        names = (prep.recorder.prep.SCRIPT, prep.recorder.prep.INSPECTOR, prep.recorder.prep.PINS, prep.recorder.prep.TEST,
                 prep.recorder.SCRIPT, prep.recorder.PROBE, prep.recorder.DIAGNOSTIC, prep.recorder.SNAPSHOT,
                 prep.recorder.TEST, prep.recorder.CENSUS, prep.recorder.DESCRIPTOR, prep.recorder.CANARY,
                 prep.recorder.DESCRIPTOR_TEST, prep.recorder.PYTHON_PINS, prep.SCRIPT, prep.APPLICATION, prep.TEST, app.EVIDENCE)
        cls.extra = {name: (ROOT / name).read_bytes() for name in names}
        cls.files, cls.receipt = prep.prepare_files("1" * 40, cls.bundle, cls.extra)
        cls.contract = json.loads(cls.files["payload/contract.json"])
        cls.measured = app.measurement(overlay, cls.extra[app.EVIDENCE])

    @contextmanager
    def wheel(self):
        fixture = fixtures.OverlayTests("test_inspection_does_not_import_and_full_overlay_preserves_native")
        fixture.setUp()
        try:
            with fixture.packaging_console() as (console, _, _):
                # The fixture console's Tree belongs to the same reviewed source
                # loaded in another isolated test module; it never imports vLLM.
                for hook in app.HOOKS:
                    fixture.write(hook, b"raise RuntimeError('hook must not execute')\n")
                fixture.write("vllm/__pycache__/envs.cpython-312.pyc", b"opaque forged bytecode never executed")
                stub = bytearray(64)
                stub[:7] = b"\x7fELF\x02\x01\x01"
                stub[16:24] = b"\x03\x00\xb7\x00\x01\x00\x00\x00"
                for name in overlay.CUTLASS_STUB_POLICY["paths"]:
                    fixture.write(name, bytes(stub))
                policy = {**overlay.CUTLASS_STUB_POLICY, "bytes": len(stub), "sha256": overlay.digest(stub)}
                contract = copy.deepcopy(self.contract)
                contract["diagnostic_nonhost_policy"] = policy
                with mock.patch.object(overlay, "CUTLASS_STUB_POLICY", policy), overlay.directory(fixture.root) as fd:
                    tree = app.experimental_tree(overlay, fd, contract)
                    inventory = tree.inventory()
                    before = overlay.inspect_integrity(tree, contract, inventory=inventory, console=console, packaging=True)
                    measured = copy.deepcopy(self.measured)
                    plan = before["packaging_removals"]
                    measured["packaging"].update(inventory_sha256=overlay.digest(overlay.json_bytes(inventory)),
                        removal_plan_sha256=overlay.digest(overlay.json_bytes(plan)), removal_count=len(plan),
                        removal_bytes=sum(row["bytes"] for row in plan))
                    yield fixture, tree, console, contract, measured
        finally:
            fixture.doCleanups()

    def test_context_is_deterministic_bound_and_non_executable(self):
        again, receipt = prep.prepare_files("1" * 40, self.bundle, self.extra)
        self.assertEqual(again, self.files)
        self.assertEqual(receipt, self.receipt)
        self.assertEqual(receipt["context_sha256"], overlay.bundle_digest({k: v for k, v in again.items() if k != "receipt.json"}))
        contract = json.loads(again["payload/contract.json"])
        self.assertEqual(set(contract["application_sources"]), {k.removeprefix("payload/") for k in again if k.startswith("payload/")} - {"contract.json"})
        for name, digest in contract["application_sources"].items():
            self.assertEqual(overlay.digest(again["payload/" + name]), digest)
        launch = json.loads(again["launch-plan.json"])
        self.assertFalse(launch["executable"])
        self.assertEqual(launch["fixed_engine_environment"]["VLLM_PLUGINS"], "")
        self.assertIn(b"BLOCKED: no engine launcher", again["Dockerfile"])
        self.assertFalse(receipt["image_built"])
        self.assertFalse(receipt["application_performed"])
        self.assertFalse(receipt["hardware_validated"])

    def test_measurement_is_exact_not_self_reported_success(self):
        raw = self.extra[app.EVIDENCE]
        for data in (raw + b" ", raw.replace(b'"result": "fail"', b'"result": "pass"')):
            with self.assertRaises(ValueError):
                app.measurement(overlay, data)
        self.assertEqual(self.measured["packaging"]["removal_count"], 2879)
        self.assertEqual(self.measured["files"][0]["sha256"], "e50d468e8b0adfb05733f5b87b3cff34829c4a8c1aea50c865aa8bdfe4bb150f")

    def test_import_contract_has_no_generic_python_or_worker_override(self):
        contract = app.import_contract(overlay, self.measured)
        self.assertEqual(contract["paths"], self.measured["proposed_paths"])
        self.assertEqual(contract["path_sha256"], "ecf3cf1ec68e98d1e5aca0ea5d79b2645d24746714e824b839a6a42bda96dd77")
        self.assertFalse(contract["executable"])
        self.assertFalse(contract["engine_launch_implemented"])
        self.assertEqual(contract["worker_method"], "spawn")
        self.assertFalse(contract["numa_bind"])
        self.assertIn("forkserver", contract["unsupported"])
        self.assertIn("arbitrary-python", contract["unsupported"])
        self.assertEqual(contract["flags"], ["-I", "-S", "-B"])

    def test_wrong_image_config_policy_and_measurement_reject_before_inventory(self):
        for path, bad in ((["base_image"], "unknown"), (["official_config", "sha256"], "0" * 64),
                          (["deployable"], True), (["application_measurement_sha256"], "0" * 64),
                          (["diagnostic_inventory_policy", "entry_limit"], 1000000),
                          (["diagnostic_nonhost_policy", "paths"], []), (["application_mode"], "skip")):
            contract = copy.deepcopy(self.contract)
            target = contract
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = bad
            with self.subTest(path=path), self.assertRaises(ValueError), mock.patch.object(overlay.Tree, "inventory", side_effect=AssertionError):
                app.experimental_tree(overlay, -1, contract)
        self.assertEqual(overlay.MAX_FILES, 100000)

    def test_import_contract_overrides_reject_before_inventory(self):
        for key, value in (("worker_method", "forkserver"), ("numa_bind", True), ("paths", ["/secret"]),
                           ("flags", ["-B"]), ("engine_launch_implemented", True),
                           ("required_environment", {"VLLM_PLUGINS": "synthetic-private-value"})):
            contract = copy.deepcopy(self.contract)
            contract["import_contract"][key] = value
            with self.subTest(key=key), mock.patch.object(overlay.Tree, "inventory", side_effect=AssertionError), self.assertRaises(ValueError) as caught:
                app.inspect(overlay, overlay.Tree(-1), contract, self.measured, None)
            self.assertNotIn("synthetic-private", str(caught.exception))

    def test_real_five_stage_application_removes_bytes_and_preserves_hooks_native_record(self):
        with self.wheel() as (fixture, tree, console, contract, measured):
            before = app.inspect(overlay, tree, contract, measured, console)
            preserved = {name: (fixture.root / name).read_bytes() for name in (*app.HOOKS, fixture.dist + "/RECORD", "vllm/_C.abi3.so")}
            result = app.apply(overlay, tree, contract, fixture.stages, before, measured, console)
            self.assertEqual(result["stages"], 5)
            self.assertEqual(result["packaging"]["removed_unlisted_bytecode_files"], 1)
            self.assertFalse(result["engine_executed"])
            self.assertFalse(result["abi_validated"])
            self.assertEqual(result["generic_overlay_admission"], "not-granted")
            self.assertFalse((fixture.root / "vllm/__pycache__/envs.cpython-312.pyc").exists())
            for name, data in preserved.items():
                self.assertEqual((fixture.root / name).read_bytes(), data)
            for item in contract["files"]:
                self.assertEqual(overlay.digest((fixture.root / item["path"]).read_bytes()), item["sha256"][5])

    def test_generic_inspector_still_rejects_exact_experimental_fixture(self):
        with self.wheel() as (_, tree, console, contract, measured):
            app.inspect(overlay, tree, contract, measured, console)
            with self.assertRaises(ValueError):
                overlay.inspect(overlay.Tree(tree.fd), contract, console=console, packaging=True)

    def test_build_application_uses_one_measured_budget_without_skipping_inventory(self):
        with self.wheel() as (_, tree, console, contract, measured):
            clock = [100.0]
            original_inventory = tree.inventory
            deadlines = []
            def inventory():
                deadlines.append((tree.deadline, console[1].deadline))
                clock[0] += 60
                return original_inventory()
            @contextmanager
            def selected(*args):
                yield tree, console
            with mock.patch.object(app.time, "monotonic", side_effect=lambda: clock[0]), \
                 mock.patch.object(app, "verify_layout", side_effect=lambda *args: clock.__setitem__(0, 120.0)), \
                 mock.patch.object(app, "selected_tree", selected), \
                 mock.patch.object(tree, "inventory", side_effect=inventory):
                result = app.run_application(overlay, None, None, contract, measured,
                                             fixtures.OverlayTests.stages, apply_requested=True)
            self.assertEqual(app.APPLICATION_SECONDS, 720)
            self.assertEqual(deadlines, [(820.0, 820.0)] * 8)
            self.assertEqual(result["stages"], 5)
            self.assertFalse(result["engine_executed"])
            self.assertFalse(result["hardware_validated"])

    def test_build_budget_exhausted_in_layout_never_inspects_or_applies(self):
        with self.wheel() as (_, tree, console, contract, measured):
            @contextmanager
            def selected(*args):
                yield tree, console
            for elapsed in (720, 721):
                clock = [100.0]
                with self.subTest(elapsed=elapsed), \
                     mock.patch.object(app.time, "monotonic", side_effect=lambda: clock[0]), \
                     mock.patch.object(app, "verify_layout", side_effect=lambda *args: clock.__setitem__(0, 100.0 + elapsed)), \
                     mock.patch.object(app, "selected_tree", selected), \
                     mock.patch.object(app, "inspect") as inspection, \
                     mock.patch.object(app, "apply") as application, self.assertRaises(ValueError):
                    try:
                        app.run_application(overlay, None, None, contract, measured, [], apply_requested=True)
                    finally:
                        inspection.assert_not_called()
                        application.assert_not_called()

    def test_build_budget_expiry_during_inspection_or_final_check_rejects(self):
        with self.wheel() as (_, tree, console, contract, measured):
            @contextmanager
            def selected(*args):
                yield tree, console
            for phase in ("inspection", "application", "inspection-only"):
                clock = [100.0]
                def inspection(*args):
                    if phase == "inspection":
                        clock[0] = 820.0
                        return tree.inventory()
                    if phase == "inspection-only":
                        clock[0] = 820.0
                    return {}
                def application(*args):
                    clock[0] = 820.0
                    return {"result": "must-not-escape"}
                with self.subTest(phase=phase), \
                     mock.patch.object(app.time, "monotonic", side_effect=lambda: clock[0]), \
                     mock.patch.object(app, "verify_layout"), \
                     mock.patch.object(app, "selected_tree", selected), \
                     mock.patch.object(app, "inspect", side_effect=inspection), \
                     mock.patch.object(app, "apply", side_effect=application) as applied, self.assertRaises(ValueError):
                    try:
                        app.run_application(overlay, None, None, contract, measured, [],
                                             apply_requested=phase != "inspection-only")
                    finally:
                        if phase != "application":
                            applied.assert_not_called()

    def test_mutated_self_consistent_inventory_never_rebases_measured_input(self):
        for name in ("distutils-precedence.pth", "vllm/_C.abi3.so", "vllm/envs.py", "vllm/__pycache__/envs.cpython-312.pyc"):
            with self.subTest(name=name), self.wheel() as (fixture, tree, console, contract, measured):
                before = app.inspect(overlay, tree, contract, measured, console)
                fixture.write(name, (fixture.root / name).read_bytes() + b"mutation")
                with self.assertRaises(ValueError), mock.patch.object(tree, "write", side_effect=AssertionError), mock.patch.object(tree, "unlink", side_effect=AssertionError):
                    app.apply(overlay, tree, contract, fixture.stages, before, measured, console)

    def test_extra_hook_symlink_fifo_and_foreign_native_rejected_without_writes(self):
        for kind in ("hook", "symlink", "fifo", "foreign", "package-shadow"):
            with self.subTest(kind=kind), self.wheel() as (fixture, tree, console, contract, measured):
                if kind == "symlink":
                    (fixture.root / "link").symlink_to("/nonexistent")
                elif kind == "fifo":
                    os.mkfifo(fixture.root / "fifo")
                elif kind == "foreign":
                    fixture.write("extra.so", b"not native")
                elif kind == "package-shadow":
                    fixture.write("vllm/utils/torch_utils/__init__.py", b"raise RuntimeError('shadow')")
                else:
                    fixture.write("extra.pth", b"import forged")
                with self.assertRaises(ValueError), mock.patch.object(tree, "write", side_effect=AssertionError):
                    app.inspect(overlay, tree, contract, measured, console)

    def test_stage_hash_or_suppressed_removal_never_writes_patch(self):
        for failure in ("stage", "unlink"):
            with self.subTest(failure=failure), self.wheel() as (fixture, tree, console, contract, measured):
                before = app.inspect(overlay, tree, contract, measured, console)
                stages = copy.deepcopy(fixture.stages)
                if failure == "stage":
                    stages[0][next(iter(stages[0]))] += b"changed"
                with mock.patch.object(tree, "write", side_effect=AssertionError), mock.patch.object(tree, "unlink"), self.assertRaises(ValueError):
                    app.apply(overlay, tree, contract, stages, before, measured, console)

    def test_native_mutation_during_stage_invalidates_application_receipt(self):
        with self.wheel() as (fixture, tree, console, contract, measured):
            before = app.inspect(overlay, tree, contract, measured, console)
            write = tree.write
            def changed(name, data, mode):
                write(name, data, mode)
                fixture.write("vllm/_C.abi3.so", b"changed native")
            with mock.patch.object(tree, "write", side_effect=changed), self.assertRaises(ValueError):
                app.apply(overlay, tree, contract, fixture.stages, before, measured, console)

    def test_cli_rejects_unknown_arguments_without_secret_output_or_image_action(self):
        for script in ("scripts/prepare-engine-application.py", "scripts/engine_application.py"):
            result = subprocess.run([sys.executable, "-I", "-S", "-B", str(ROOT / script),
                                     "--SYNTHETIC_PRIVATE_VALUE", "--unknown"],
                                    cwd=ROOT, env={"PATH": "/usr/bin:/bin"}, capture_output=True, timeout=15)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"")
            self.assertNotIn(b"SYNTHETIC_PRIVATE_VALUE", result.stderr)
            self.assertNotIn(b"Traceback", result.stderr)

    def test_layout_runtime_mismatch_fails_before_reading_image(self):
        expected = copy.deepcopy(self.measured["runtime"])
        for changed in ("flags", "path", "version"):
            current = copy.deepcopy(expected)
            if changed == "flags":
                current["flags"]["no_site"] = False
            elif changed == "path":
                current["locations"][-1]["path"] = "/secret/synthetic"
            else:
                current["python"] = "3.12.4"
            with mock.patch.object(prep.recorder.descriptor, "runtime_facts", return_value=current), mock.patch.object(overlay, "directory", side_effect=AssertionError), self.assertRaises(ValueError):
                app.verify_layout(overlay, prep.recorder.descriptor, prep.recorder.diagnostic, self.contract, self.measured)

    def test_real_layout_recheck_rejects_file_chain_root_and_worker_changes(self):
        for mutation in (None, "executable", "chain", "hook", "worker", "collision", "stdlib"):
            fixture = layout_fixtures.DescriptorTests("test_bounded_source_hashes_links_and_partial_independent_sections")
            fixture.setUp()
            try:
                measured = fixture.collect()
                if mutation == "executable":
                    fixture.write("/usr/bin/python3.12", b"changed")
                elif mutation == "chain":
                    path = fixture.root / "etc/alternatives/python3"
                    path.unlink()
                    path.symlink_to("/private/SYNTHETIC_SECRET")
                elif mutation == "hook":
                    fixture.write(fixture.selected + "/distutils-precedence.pth", b"changed")
                elif mutation == "worker":
                    fixture.write(fixture.selected + "/vllm/fixture.py", b"changed")
                elif mutation == "collision":
                    fixture.write(fixture.selected + "/subprocess.py", b"raise RuntimeError('shadow')")
                elif mutation == "stdlib":
                    fixture.write(fixture.stdlib + "/new.py", b"added")
                with mock.patch.object(prep.recorder.descriptor, "runtime_facts", return_value=copy.deepcopy(fixture.facts)):
                    if mutation is None:
                        app.verify_layout(overlay, prep.recorder.descriptor, prep.recorder.diagnostic, self.contract, measured, filesystem_root=fixture.root)
                    else:
                        with self.subTest(mutation=mutation), self.assertRaises(ValueError) as caught:
                            app.verify_layout(overlay, prep.recorder.descriptor, prep.recorder.diagnostic, self.contract, measured, filesystem_root=fixture.root)
                        self.assertNotIn("SYNTHETIC_SECRET", str(caught.exception))
            finally:
                fixture.doCleanups()

    def test_private_nooverwrite_publication_uses_existing_publisher(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            out = Path(tmp) / "context"
            prep.recorder.prep.publish(out, self.files)
            self.assertEqual(out.stat().st_mode & 0o777, 0o700)
            self.assertEqual((out / "receipt.json").stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                prep.recorder.prep.publish(out, self.files)
            self.assertEqual((out / "payload/measurement.json").read_bytes(), self.extra[app.EVIDENCE])

    def test_preparer_bootstrap_sources_reject_links_special_and_oversize(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            root = Path(tmp)
            source = root / "source"
            source.write_bytes(b"original")
            self.assertEqual(prep.bootstrap_read(source), b"original")
            for kind in ("link", "hard", "fifo", "directory", "large"):
                path = root / kind
                if kind == "link":
                    path.symlink_to(source)
                elif kind == "hard":
                    os.link(source, path)
                elif kind == "fifo":
                    os.mkfifo(path)
                elif kind == "directory":
                    path.mkdir()
                else:
                    path.write_bytes(b"x" * ((2 << 20) + 1))
                with self.subTest(kind=kind), self.assertRaises(ValueError):
                    prep.bootstrap_read(path)
