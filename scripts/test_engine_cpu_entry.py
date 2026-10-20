# SPDX-License-Identifier: Apache-2.0
"""Synthetic trees, subprocesses and Docker responses only; no image execution."""

import copy
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def load(name, relative):
    value = types.ModuleType(name)
    value.__file__ = str(ROOT / relative)
    exec(compile((ROOT / relative).read_bytes(), value.__file__, "exec"), value.__dict__)
    return value


tool = load("cpu_record_test", "scripts/record-engine-cpu-entry.py")
fixture = load("cpu_smoke_fixture", "scripts/test_engine_import_smoke.py")
lab_fixture = load("cpu_lab_fixture", "scripts/test_deepseek_lab.py")
cpu, lab = tool.cpu, lab_fixture.lab
cpu.os, cpu.sys, cpu.json = os, sys, json
cpu.types, cpu.hashlib = types, hashlib
helpers = lab_fixture.helpers
cpu.compiled_catalog, cpu.same_code = helpers.compiled_catalog, helpers.same_code


def finder_empty():
    with mock.patch.object(sys, "_nib_lab_bootstrap", None, create=True):
        return cpu.finder_packet(None)


def finder_same():
    expected = [object(), object(), object()]
    with mock.patch.object(sys, "_nib_lab_bootstrap",
                           {"finders": expected, "pattern_status": "pending"}, create=True), \
            mock.patch.object(sys, "meta_path", expected):
        return cpu.finder_packet(None)


class EntryTests(unittest.TestCase):
    def test_shared_entry_prepares_but_does_not_serve(self):
        parser, serve, check, checkpoints = (mock.Mock() for _ in range(4))
        fake_serve = types.ModuleType("vllm.entrypoints.cli.serve")
        fake_serve.ServeSubcommand = lambda: serve
        fake_parser = types.ModuleType("vllm.utils.argparse_utils")
        fake_parser.FlexibleArgumentParser = lambda: parser
        with mock.patch.dict(sys.modules, {"engine_lab": lab, "vllm.entrypoints.cli.serve": fake_serve,
                                          "vllm.utils.argparse_utils": fake_parser}), \
                mock.patch.object(lab, "install_lab", return_value={"check": check}, create=True):
            actual_serve, args = lab.prepare_engine({"paths": ["/stdlib", "/dyn", "/selected"], "arguments": ["fixed"]}, checkpoints)
        self.assertIs(actual_serve, serve)
        self.assertIs(args, parser.parse_args.return_value)
        parser.parse_args.assert_called_once_with(["serve", "fixed"])
        serve.validate.assert_called_once_with(args)
        serve.cmd.assert_not_called()
        check.assert_called_once_with()
        self.assertEqual([call.args[0] for call in checkpoints.emit.call_args_list], [11, 12, 13, 14, 15, 16])
        checkpoints.close.assert_not_called()

    def test_each_bootstrap_failure_closes_both_fds_and_retains_exact_boundary(self):
        boundaries = [("preconditions", None), *(("setuptools-source", i) for i in range(4)),
                      ("vendor-root", None), ("install", None), ("complete", None)]
        contract = {"paths": ["/stdlib", "/dyn", "/selected"], "arguments": []}
        for failed, boundary in enumerate(boundaries):
            checkpoints = lab.EntryCheckpointReader({"status": "open", "last_id": None})
            state = lab.EntryCheckpointReader({"status": "not-started", "last_id": None})
            first, second = os.dup(checkpoints.writer), os.dup(state.writer)
            checkpoints.close_writer()
            state.close_writer()
            def install(*args, checkpoint):
                for current in boundaries:
                    checkpoint(*current)
                    if current == boundary:
                        raise ValueError("PRIVATE bootstrap")
                self.fail("failure boundary missed")
            try:
                with self.subTest(boundary=boundary), \
                        mock.patch.dict(sys.modules, {"engine_lab": lab}), \
                        mock.patch.object(cpu, "context", return_value=(contract, {}, [None], {}, {}, lab)), \
                        mock.patch.object(lab, "recheck_sources"), \
                        mock.patch.object(lab, "install_lab", install, create=True), \
                        mock.patch.object(cpu, "state_mismatches", return_value=None):
                    with self.assertRaisesRegex(ValueError, "^PRIVATE bootstrap$"):
                        cpu.entry_child("a" * 64, str(first), str(second), lab)
                    for fd in (first, second):
                        with self.assertRaises(OSError):
                            os.fstat(fd)
                    first = second = None
                    checkpoints.finish()
                    self.assertEqual(checkpoints.observation, {"status": "eof", "last_id": failed + 3})
                    packet = os.read(state.reader, cpu.STATE_PACKET_BYTES + 1)
                    self.assertEqual(packet[:2], b"\xff\xff")
                    self.assertEqual(len(packet), cpu.STATE_PACKET_BYTES)
                    cpu.validate_finders(cpu.decode_finders(packet[2:]))
            finally:
                for fd in (first, second):
                    if fd is not None:
                        try:
                            os.close(fd)
                        except OSError:
                            pass
                checkpoints.finish()
                state.finish()

    def test_child_success_or_state_write_failure_still_closes_checkpoints(self):
        for write_failure in (False, True):
            checkpoints = lab.EntryCheckpointReader({"status": "open", "last_id": None})
            state = lab.EntryCheckpointReader({"status": "not-started", "last_id": None})
            first, second = os.dup(checkpoints.writer), os.dup(state.writer)
            checkpoints.close_writer()
            state.close_writer()
            def prepare(contract, writer):
                for value in range(3, 17):
                    writer.emit(value)
            try:
                if write_failure:
                    os.close(state.reader)
                    state.reader = None
                with mock.patch.object(cpu, "context", return_value=({"paths": ["/selected"]}, {}, [None], {}, {}, lab)), \
                        mock.patch.object(lab, "recheck_sources"), \
                        mock.patch.object(lab, "prepare_engine", side_effect=prepare), \
                        mock.patch.object(cpu, "state_mismatches", return_value=dict.fromkeys(cpu.STATE_FIELDS, False)):
                    if write_failure:
                        with self.assertRaises(BrokenPipeError):
                            cpu.entry_child("a" * 64, str(first), str(second), lab)
                    else:
                        cpu.entry_child("a" * 64, str(first), str(second), lab)
                checkpoints.finish()
                self.assertEqual(checkpoints.observation, {"status": "eof", "last_id": 16})
                if not write_failure:
                    packet = os.read(state.reader, cpu.STATE_PACKET_BYTES + 1)
                    self.assertEqual(cpu.decode_state(packet[:2]), dict.fromkeys(cpu.STATE_FIELDS, False))
                    cpu.validate_finders(cpu.decode_finders(packet[2:]))
            finally:
                checkpoints.finish()
                state.finish()

    def test_state_mask_exact_fields_and_unavailable_sentinel(self):
        self.assertIsNone(cpu.decode_state(b"\xff\xff"))
        for index, name in enumerate(cpu.STATE_FIELDS):
            row = dict.fromkeys(cpu.STATE_FIELDS, False)
            row[name] = True
            with mock.patch.object(cpu, "state_mismatches", return_value=row):
                self.assertEqual(cpu.decode_state(cpu.state_bytes()), row)
                self.assertEqual(cpu.state_bytes(), (1 << index).to_bytes(2, "big"))
        for raw in (b"", b"\x00", b"\x00\x00\x00", b"\x80\x00", "private", True):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                cpu.decode_state(raw)
        with mock.patch.object(cpu, "state_mismatches", side_effect=ValueError("PRIVATE")):
            self.assertEqual(cpu.state_bytes(), b"\xff\xff")

    def test_observed_conditions_match_existing_guard_without_changing_it(self):
        layout = {"paths": ["/stdlib", "/selected"], "wrapper": "/fixed/python"}
        state = {"layout": layout, "finders": ["fixed"]}
        spawn = types.SimpleNamespace(get_executable=lambda: b"/fixed/python")
        mp = types.SimpleNamespace(get_start_method=lambda: "spawn")
        with mock.patch.object(sys, "_nib_lab_bootstrap", state, create=True), \
                mock.patch.object(sys, "path", layout["paths"]), mock.patch.object(sys, "executable", layout["wrapper"]), \
                mock.patch.object(sys, "meta_path", ["fixed"]), mock.patch.object(sys, "pycache_prefix", None), \
                mock.patch.dict(sys.modules, {"multiprocessing.spawn": spawn, "multiprocessing": mp}), \
                mock.patch.dict(os.environ, {"VLLM_PLUGINS": "", "VLLM_WORKER_MULTIPROC_METHOD": "spawn"}, clear=True), \
                mock.patch.object(os, "getcwd", return_value="/"):
            self.assertEqual(cpu.state_mismatches(), dict.fromkeys(cpu.STATE_FIELDS, False))
            for name, target, attribute, bad in (
                ("paths", sys, "path", ["/private"]), ("executable", sys, "executable", "/private"),
                ("finders", sys, "meta_path", ["private"]), ("pycache_prefix", sys, "pycache_prefix", "/private"),
                ("spawn_executable", spawn, "get_executable", lambda: b"/private"),
                ("start_method", mp, "get_start_method", lambda: "fork"),
                ("cwd", os, "getcwd", lambda: "/private"),
            ):
                with self.subTest(name=name), mock.patch.object(target, attribute, bad):
                    self.assertEqual(cpu.state_mismatches(), {key: key == name for key in cpu.STATE_FIELDS})
            for key, field in (("VLLM_PLUGINS", "plugins"), ("VLLM_WORKER_MULTIPROC_METHOD", "worker_method"),
                               ("NUMACTL_PYTHON", "numa_environment")):
                with mock.patch.dict(os.environ, {key: "PRIVATE"}):
                    self.assertEqual(cpu.state_mismatches(), {name: name == field for name in cpu.STATE_FIELDS})
                    self.assertNotIn("PRIVATE", json.dumps(cpu.state_mismatches()))

    def test_real_synthetic_child_channels_are_bounded_and_not_stdout(self):
        popen = subprocess.Popen
        for code, bits, exit_code, expected in ((16, b"\0\0", 0, True), (15, b"\0\0", 1, False),
                                               (16, b"\0\2", 1, False), (16, b"PRIVATE", 0, False),
                                               (17, b"\0\0", 0, False)):
            def start(argv, **kwargs):
                self.assertEqual(argv[:3], ["/opt/nib/python", "entry", "--checkpoint-fd"])
                self.assertEqual(argv[4], "--state-fd")
                self.assertEqual(kwargs["pass_fds"], (int(argv[3]), int(argv[5])))
                packet = bits + finder_same()
                program = ("import os,sys\nprint('PRIVATE stdout')\nprint('PRIVATE stderr',file=sys.stderr)\n"
                           "os.write(int(sys.argv[1]),bytes(range(1," + str(code + 1) + ")))\n"
                           "os.write(int(sys.argv[2])," + repr(packet) + ")\nsys.exit(" + str(exit_code) + ")")
                return popen([sys.executable, "-I", "-S", "-B", "-c", program, argv[3], argv[5]], **kwargs)
            with self.subTest(code=code, bits=bits), mock.patch.object(cpu.subprocess, "Popen", side_effect=start):
                row = cpu.observe_entry(lab)
            self.assertEqual(cpu.diagnostic_pass(row), expected)
            self.assertEqual(row["exit_code"], exit_code)
            self.assertNotIn("PRIVATE", json.dumps(row))

    def test_timeout_kills_owned_child_and_launch_failure_closes_endpoints(self):
        child = mock.Mock(pid=987654321)
        child.wait.side_effect = [subprocess.TimeoutExpired("fixed", 300), -9]
        child.poll.return_value = None
        with mock.patch.object(cpu.subprocess, "Popen", return_value=child) as start, \
                mock.patch.object(cpu.os, "killpg") as kill:
            row = cpu.observe_entry(lab)
        self.assertEqual(row["outcome"], "timeout")
        kill.assert_called_once_with(child.pid, signal.SIGKILL)
        for fd in start.call_args.kwargs["pass_fds"]:
            with self.assertRaises(OSError):
                os.fstat(fd)
        with mock.patch.object(cpu.subprocess, "Popen", side_effect=OSError("PRIVATE")) as start:
            row = cpu.observe_entry(lab)
        self.assertEqual(row["outcome"], "launch-failed")
        self.assertEqual(row["checkpoints"], {"status": "not-started", "last_id": None})
        self.assertNotIn("PRIVATE", json.dumps(row))
        for fd in start.call_args.kwargs["pass_fds"]:
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_wrong_flags_and_all_worker_routes_reject_before_context(self):
        for isolated, no_site, no_bytecode in ((False, True, True), (True, False, True), (True, True, False)):
            with mock.patch.object(sys, "flags", types.SimpleNamespace(isolated=isolated, no_site=no_site)), \
                    mock.patch.object(sys, "dont_write_bytecode", no_bytecode), \
                    mock.patch.object(sys, "argv", ["launch.py", "a" * 64, "apply"]), \
                    mock.patch.object(cpu, "context") as context:
                with self.assertRaises(ValueError):
                    cpu.main()
                context.assert_not_called()
        for args in ([], ["engine"], ["probe"], ["entry"], ["entry", "--checkpoint-fd", "3"],
                     ["-c", "PRIVATE"], ["--multiprocessing-fork"], ["apply", "extra"],
                     ["entry", "--checkpoint-fd", "3", "--other-fd", "4"]):
            with self.subTest(args=args), mock.patch.object(sys, "flags", types.SimpleNamespace(isolated=True, no_site=True)), \
                    mock.patch.object(sys, "dont_write_bytecode", True), \
                    mock.patch.object(sys, "argv", ["launch.py", "a" * 64, *args]), \
                    mock.patch.object(cpu, "context") as context:
                with self.assertRaises(ValueError):
                    cpu.main()
                context.assert_not_called()

    def test_fixed_result_is_only_output_even_when_fake_dependency_prints(self):
        raw = (ROOT / tool.base.BOOTSTRAP_FILE).read_bytes() + b"\n" + (ROOT / tool.RUNTIME).read_bytes()
        program = ("import types,sys\nm=types.ModuleType('fake_entry')\n"
                   "exec(compile(" + repr(raw) + ",'unit','exec'),m.__dict__)\n"
                   "def context(_):\n print('PRIVATE context')\n return ({},{},[],{},{},None)\n"
                   "def apply(*args):\n print('PRIVATE stdout');print('PRIVATE stderr',file=sys.stderr);return {'fixed':'only'}\n"
                   "m.context=context;m.apply_and_observe=apply\n"
                   "sys.argv=['launch.py','a'*64,'apply'];m.main()\n")
        result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", program],
                                capture_output=True, timeout=10)
        self.assertEqual((result.returncode, result.stderr), (0, b""))
        self.assertEqual(json.loads(result.stdout), {"fixed": "only"})


class FinderTests(unittest.TestCase):
    def setUp(self):
        self.expected = [object(), object(), object()]
        self.state = {"finders": self.expected, "pattern_status": "pending"}
        self.sources = [{"id": spec["id"], "status": "verified"} for spec in cpu.FINDER_SPECS]

    def observe(self, actual, catalog=None):
        with mock.patch.object(sys, "_nib_lab_bootstrap", self.state, create=True), \
                mock.patch.object(sys, "meta_path", actual):
            raw = cpu.finder_packet(catalog)
        self.assertEqual(len(raw), cpu.STATE_PACKET_BYTES - 2)
        row = cpu.decode_finders(raw)
        cpu.validate_finders(row)
        self.assertNotIn("PRIVATE", json.dumps(row))
        return row

    def test_unchanged_add_remove_reorder_duplicate_and_bound(self):
        same = self.observe(self.expected)
        self.assertTrue(same["same_identity_list"])
        self.assertEqual(same["entries"], ["builtin", "frozen", "sealed"])
        self.assertEqual(same["pattern_adapter_stage"], "pending")
        added = self.observe([object(), *self.expected])
        self.assertEqual(added["entries"], ["unknown", "builtin", "frozen", "sealed"])
        self.assertFalse(added["same_identity_list"])
        self.assertFalse(added["expected_order_changed"])
        self.assertEqual(self.observe(self.expected[:2])["missing_expected_in_scan"], ["sealed"])
        self.assertTrue(self.observe(self.expected[::-1])["expected_order_changed"])
        self.assertTrue(self.observe([*self.expected, self.expected[2]])["expected_duplicated"])
        capped = self.observe([object()] * 33 + self.expected)
        self.assertEqual(capped["entries"], ["unknown"] * 32)
        self.assertTrue(capped["scan_truncated"])
        self.assertEqual(capped["missing_expected_in_scan"], ["builtin", "frozen", "sealed"])

    def synthetic(self, source_index, method, classmethod=False):
        # Original synthetic fixture, not an upstream import or copied class.
        raw = ("class Hook:\n" + (" @classmethod\n" if classmethod else "")
               + " def " + method + "(self, *args):\n  return -0.0\n")
        name = cpu.FINDER_MODULES[source_index]
        module = types.ModuleType(name)
        module.__file__ = "/selected/" + cpu.FINDER_SPECS[source_index]["installed"]
        exec(compile(raw, "/PRIVATE/name.py", "exec", dont_inherit=True), module.__dict__)
        function = vars(module.Hook)[method]
        if classmethod:
            function = function.__func__
        return module, function

    def test_each_fixed_category_requires_complete_code_and_exact_module_binding(self):
        for index, spec in enumerate(cpu.FINDER_SPECS):
            for site in spec["sites"]:
                method = site["qualname"].split(".")[-1]
                module, function = self.synthetic(index, method, index == 2)
                catalog = (self.sources, [(site["id"], spec["id"], function.__code__, None)], "/selected")
                with self.subTest(category=site["id"]), mock.patch.dict(sys.modules, {module.__name__: module}):
                    row = self.observe([*self.expected, module.Hook()], catalog)
                    self.assertEqual(row["entries"][-1], site["id"])
                    self.assertEqual(self.observe([module.Hook], catalog)["entries"], [site["id"]])
                    # Neither public-looking names nor filename establish identity.
                    original = function.__code__
                    function.__code__ = original.replace(co_firstlineno=original.co_firstlineno + 1)
                    self.assertEqual(self.observe([module.Hook()], catalog)["entries"], ["unknown"])
                    function.__code__ = original.replace(co_consts=tuple(
                        0.0 if type(value) is float and value == 0 else value for value in original.co_consts))
                    self.assertEqual(self.observe([module.Hook()], catalog)["entries"], ["unknown"])
                    function.__code__ = original
                    module.__file__ = "/PRIVATE/shadow.py"
                    self.assertEqual(self.observe([module.Hook()], catalog)["entries"], ["unknown"])
                    module.__file__ = "/selected/" + spec["installed"]
                self.assertEqual(self.observe([module.Hook()], catalog)["entries"], ["unknown"])

    def test_unknown_never_invokes_finder_descriptors_equality_or_stringification(self):
        called = []
        class Hostile:
            def __getattribute__(self, key):
                called.append("instance")
                raise AssertionError("PRIVATE attribute")
            def __eq__(self, other):
                called.append("equality")
                raise AssertionError("PRIVATE equality")
            def __repr__(self):
                called.append("repr")
                raise AssertionError("PRIVATE repr")
            @property
            def find_spec(self):
                called.append("descriptor")
                raise AssertionError("PRIVATE descriptor")
        module, function = self.synthetic(3, "find_spec")
        catalog = (self.sources, [("six", "six", function.__code__, None)], "/selected")
        self.assertEqual(self.observe([Hostile()], catalog)["entries"], ["unknown"])
        class Meta(type):
            def __getattribute__(self, name):
                called.append("metaclass")
                raise AssertionError("PRIVATE metaclass lookup")
            def __hash__(self):
                called.append("hash")
                raise AssertionError("PRIVATE metaclass hash")
            def __eq__(self, other):
                called.append("metaclass-equality")
                raise AssertionError("PRIVATE metaclass equality")
            @property
            def __mro__(self):
                called.append("mro-descriptor")
                raise AssertionError("PRIVATE mro descriptor")
            @property
            def __dict__(self):
                called.append("class-dict-descriptor")
                raise AssertionError("PRIVATE class dict descriptor")
        class MetaHook(metaclass=Meta):
            pass
        class DictHook:
            @property
            def __dict__(self):
                called.append("dict-descriptor")
                raise AssertionError("PRIVATE dict descriptor")
        self.assertEqual(self.observe([MetaHook, MetaHook(), DictHook()], catalog)["entries"], ["unknown"] * 3)
        self.assertEqual(called, [])
        with mock.patch.object(cpu, "same_code", side_effect=ValueError("PRIVATE comparison")), \
                mock.patch.dict(sys.modules, {module.__name__: module}):
            row = self.observe([module.Hook()], catalog)
        self.assertEqual(row["entries"], ["unknown"])
        self.assertTrue(row["comparison_failed"])

    def test_reference_compilation_statuses_are_bounded_and_do_not_execute(self):
        raw = b"class Hook:\n def find_spec(self, *args):\n  return None\nraise RuntimeError('PRIVATE source execution')\n"
        spec = {"id": "synthetic", "root": "selected", "installed": "hook.py", "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "sites": [{"id": "synthetic", "qualname": "Hook.find_spec"}]}
        for content, expected in ((raw, "verified"), (raw + b"\n", "source-mismatch")):
            with mock.patch.object(helpers, "read_source", return_value=content):
                sources, references = helpers.compiled_catalog([spec], {"selected": "/selected"})
            self.assertEqual(sources, [{"id": "synthetic", "status": expected}])
            self.assertEqual(len(references), int(expected == "verified"))
        for failure, status in ((FileNotFoundError("PRIVATE"), "missing"), (ValueError("PRIVATE"), "read-rejected")):
            with mock.patch.object(helpers, "read_source", side_effect=failure):
                self.assertEqual(helpers.compiled_catalog([spec], {"selected": "/selected"}),
                                 ([{"id": "synthetic", "status": status}], []))
        with mock.patch.object(cpu, "compiled_catalog", side_effect=ValueError("PRIVATE")):
            self.assertIsNone(cpu.finder_catalog("/selected"))
        for stage in cpu.PATTERN_STAGES[1:-1]:
            self.state["pattern_status"] = stage
            self.assertEqual(self.observe(self.expected)["pattern_adapter_stage"], stage)
        self.state["pattern_status"] = "PRIVATE stage"
        self.assertEqual(self.observe(self.expected)["pattern_adapter_stage"], "unknown")

    def test_packet_and_report_reject_raw_values_and_contradictions(self):
        good = finder_empty()
        for raw in (b"", b"PRIVATE", good + b"x", good[:-1], bytes([255]) + good[1:]):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                cpu.decode_finders(raw)
        row = self.observe(self.expected)
        for key, value in (("status", "PRIVATE"), ("entries", ["PRIVATE"]),
                           ("sources", [{"id": "PRIVATE", "status": "verified"}]),
                           ("pattern_adapter_stage", "PRIVATE"), ("same_identity_list", 1),
                           ("missing_expected_in_scan", ["builtin", "builtin"]),
                           ("expected_order_changed", True), ("scan_truncated", True)):
            bad = copy.deepcopy(row)
            bad[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                cpu.validate_finders(bad)
        with mock.patch.object(sys, "_nib_lab_bootstrap", {"finders": "PRIVATE"}, create=True):
            failed = cpu.decode_finders(cpu.finder_packet(None))
        self.assertEqual(failed["status"], "failed")
        self.assertNotIn("PRIVATE", json.dumps(failed))


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.SmokeTests.setUpClass()
        cls.extra = dict(fixture.SmokeTests.extra)
        for name in (tool.SCRIPT, tool.BASE, tool.RUNTIME, tool.LAB, tool.LAB_BOOTSTRAP, tool.TEST, "scripts/test_deepseek_lab.py"):
            cls.extra[name] = (ROOT / name).read_bytes()
        cls.bundle = tool.checker.working_bundle(ROOT)
        cls.files, cls.contract = tool.prepare_files("1" * 40, cls.bundle, cls.extra)
        cls.application = json.loads(cls.files["application/contract.json"])
        cls.measured = tool.builder.application.measurement(tool.overlay, cls.extra[tool.builder.application.EVIDENCE])

    def valid_report(self):
        return {"schema": cpu.SCHEMA, "status": "BLOCKED", "execution": "software", "deployable": False,
                "confidential": False, "hardware_validated": False, "abi_validated": False, "inference_started": False,
                "entry_pass": True, "stage": "complete", "application": fixture.SmokeTests().application_receipt(),
                "phase_elapsed_ms": {"application-preflight": 100, "application": 1000, "entry": 200},
                "entry": {"outcome": "exited", "exit_code": 0, "checkpoints": {"status": "eof", "last_id": 16},
                          "state_observation": "observed", "state_mismatches": dict.fromkeys(cpu.STATE_FIELDS, False),
                          "finder_diagnostic": cpu.decode_finders(finder_same())}}

    def validate(self, row):
        # Use the current CPU contract's source identity with the real fake-tree receipt.
        if row.get("application"):
            row["application"].update(git_sha=self.application["git_sha"],
                                      source_bundle_sha256=self.application["source_bundle_sha256"])
        return cpu.validate(tool.overlay, row, self.application, self.measured, tool.base.smoke)

    def test_payload_bound_to_every_source_and_no_model_or_binary_payload(self):
        files, contract = tool.prepare_files("1" * 40, self.bundle, self.extra)
        self.assertEqual((files, contract), (self.files, self.contract))
        self.assertEqual(set(files) - set(contract["sources"]), {"python", "entry.json"})
        self.assertEqual(contract["source_bundle_sha256"], tool.checker.bundle_digest({**self.bundle, **self.extra}))
        for name, digest in contract["sources"].items():
            self.assertEqual(tool.overlay.digest(files[name]), digest)
        self.assertFalse(any(name.startswith("pack/") or name == "nib" for name in files))
        self.assertEqual(contract["arguments"], tool.lab.lab_args(tool.recorder.prep.launch_plan()))
        self.assertEqual(contract["environment"], tool.lab.lab_environment(tool.recorder.prep.launch_plan()))
        self.assertIn(self.extra[tool.LAB], files["lab.py"])
        self.assertIn(self.extra[tool.LAB_BOOTSTRAP], files["lab.py"])
        self.assertIn(self.extra[tool.RUNTIME], files["launch.py"])
        self.assertEqual(hashlib.sha256(self.extra[tool.BASE]).hexdigest(), tool.BASE_SHA)

    def test_validator_reuses_exact_five_stage_receipt_and_rejects_unearned_pass(self):
        report = self.valid_report()
        self.assertTrue(self.validate(report))
        for keys, value in ((["hardware_validated"], True), (["execution"], "hardware"),
                            (["deployable"], 0), (["inference_started"], True),
                            (["application", "stages"], 4), (["application", "native_inventory_unchanged"], False),
                            (["entry", "exit_code"], True), (["entry", "checkpoints", "last_id"], 17),
                            (["entry", "checkpoints", "last_id"], True), (["entry", "checkpoints", "last_id"], 15),
                            (["entry", "state_mismatches", "finders"], 1),
                            (["entry", "state_mismatches", "private"], False),
                            (["entry", "state_mismatches", "finders"], True),
                            (["entry", "state_observation"], "unavailable"), (["private"], "PRIVATE")):
            bad = copy.deepcopy(report)
            target = bad
            for key in keys[:-1]:
                target = target[key]
            target[keys[-1]] = value
            with self.subTest(keys=keys), self.assertRaises((ValueError, TypeError, KeyError)):
                self.validate(bad)
        for field in cpu.STATE_FIELDS:
            bad = copy.deepcopy(report)
            bad["entry_pass"] = False
            bad["entry"]["state_mismatches"][field] = True
            bad["entry"]["exit_code"] = 1
            self.assertFalse(self.validate(bad))

    def test_partial_application_and_unavailable_state_never_pass(self):
        for stage, timing in (("application-preflight", [1, None, None]), ("application", [1, 1, None]),
                              ("entry", [1, 1, 1])):
            report = self.valid_report()
            report.update(entry_pass=False, stage=stage, entry=None, failure="bounded-cpu-entry-rejected")
            report["phase_elapsed_ms"] = dict(zip(report["phase_elapsed_ms"], timing))
            if stage != "entry":
                report["application"] = None
            self.assertFalse(self.validate(report))
        report = self.valid_report()
        report["entry_pass"] = False
        report["entry"].update(state_observation="unavailable", state_mismatches=None, exit_code=1)
        self.assertFalse(self.validate(report))

    def test_finder_observation_disagreement_or_unavailable_never_claims_pass(self):
        report = self.valid_report()
        for packet in (finder_empty(),):
            bad = copy.deepcopy(report)
            bad["entry"]["finder_diagnostic"] = cpu.decode_finders(packet)
            with self.assertRaises(ValueError):
                self.validate(bad)
            bad["entry_pass"] = False
            self.assertFalse(self.validate(bad))
        state = {"finders": [object(), object(), object()], "pattern_status": "pending"}
        for actual in ([*state["finders"], object()], state["finders"][::-1], [object()] * 33):
            with mock.patch.object(sys, "_nib_lab_bootstrap", state, create=True), \
                    mock.patch.object(sys, "meta_path", actual):
                packet = cpu.finder_packet(None)
            bad = copy.deepcopy(report)
            bad["entry"]["finder_diagnostic"] = cpu.decode_finders(packet)
            self.assertFalse(cpu.diagnostic_pass(bad["entry"]))
            with self.assertRaises(ValueError):
                self.validate(bad)
            bad["entry_pass"] = False
            self.assertFalse(self.validate(bad))
        bad = copy.deepcopy(report)
        bad["entry"]["finder_diagnostic"]["comparison_failed"] = True
        with self.assertRaises(ValueError):
            self.validate(bad)
        bad["entry_pass"] = False
        self.assertFalse(self.validate(bad))

    def daemon(self, result, failure=None):
        temporary = tempfile.TemporaryDirectory(dir="/private/tmp" if sys.platform == "darwin" else None)
        self.addCleanup(temporary.cleanup)
        payload = Path(temporary.name).resolve() / "payload"
        tool.recorder.prep.publish(payload, self.files)
        self.validate(result)
        config = {"config": {"Env": ["PATH=old", "PRIVATE=secret",
                    "LD_LIBRARY_PATH=" + tool.lab.IMAGE_LD_PATH, "CUDA_HOME=/usr/local/cuda"]},
                  "rootfs": {"diff_ids": ["sha256:" + "2" * 64]}}
        image = {"Id": "sha256:" + "3" * 64, "Os": "linux", "Architecture": "amd64",
                 "RepoDigests": [tool.overlay.BASE_IMAGE], "RootFS": {"Type": "layers", "Layers": config["rootfs"]["diff_ids"]}}
        commands, state = [], {"present": False, "started": False}
        def info():
            return {"Id": "4" * 64, "Image": image["Id"], "Config": {"Labels": {tool.LABEL: state["owner"]},
                "Image": tool.overlay.BASE_IMAGE, "Entrypoint": ["/usr/bin/env"], "Cmd": state["cmd"], "Env": state["env"],
                "User": "0:0", "WorkingDir": "/", "Healthcheck": {"Test": ["NONE"]}},
                "State": {"Status": "exited" if state["started"] else "created", "ExitCode": 0, "OOMKilled": False},
                "HostConfig": {"NetworkMode": "none", "ReadonlyRootfs": False, "Privileged": False, "Runtime": "runc",
                    "CapDrop": ["ALL"], "IpcMode": "private", "ShmSize": 16 << 20, "CgroupnsMode": "private",
                    "Memory": 8 << 30, "MemorySwap": 8 << 30, "NanoCpus": 2000000000, "PidsLimit": 128,
                    "RestartPolicy": {"Name": "no"}, "LogConfig": {"Type": "none"}, "SecurityOpt": ["no-new-privileges"],
                    "Tmpfs": {"/tmp": "rw,nosuid,nodev,noexec,size=512m,mode=1777",
                              "/cache": "rw,nosuid,nodev,noexec,size=512m,mode=0700"}},
                "Mounts": [{"Type": "bind", "Source": str(payload), "Destination": "/opt/nib", "RW": False}]}
        def execute(args, **kwargs):
            commands.append(args)
            raw = b""
            if args[1:3] == ["image", "inspect"]:
                raw = tool.overlay.json_bytes(image)
            elif args[1:3] == ["container", "ls"]:
                raw = b"owned\n" if state["present"] else b""
            elif args[1:3] == ["container", "create"]:
                state.update(present=True, owner=args[args.index("--label") + 1].split("=", 1)[1],
                    cmd=args[args.index(tool.overlay.BASE_IMAGE) + 1:],
                    env=[args[i + 1] for i, item in enumerate(args) if item == "--env"])
                if failure == "create-timeout":
                    raise subprocess.TimeoutExpired(args, 30)
                raw = ("4" * 64).encode()
            elif args[1:3] == ["container", "inspect"]:
                row = info()
                if failure in ("network", "runtime", "devices", "ports"):
                    key, value = {"network": ("NetworkMode", "host"), "runtime": ("Runtime", "nvidia"),
                                  "devices": ("DeviceRequests", [{"Capabilities": [["gpu"]]}]),
                                  "ports": ("PortBindings", {"8000/tcp": [{}]})}[failure]
                    row["HostConfig"][key] = value
                if failure == "weights":
                    row["Mounts"].append({"Type": "bind", "Source": "/private", "Destination": "/model", "RW": False})
                if failure == "ownership" and state["started"]:
                    row["Config"]["Labels"] = {}
                if failure == "oom" and state["started"]:
                    row["State"]["OOMKilled"] = True
                raw = tool.overlay.json_bytes(row)
            elif args[1:3] == ["container", "start"]:
                state["started"] = True
                self.assertEqual(kwargs["timeout"], cpu.ATTACH_SECONDS)
                self.assertEqual(kwargs["max_output_bytes"], 65536)
                if failure == "timeout":
                    raise subprocess.TimeoutExpired(args, cpu.ATTACH_SECONDS)
                raw = b'{"PRIVATE":"secret"}' if failure == "malformed" else tool.overlay.json_bytes(result)
            elif args[1:3] == ["container", "rm"]:
                if failure == "cleanup":
                    raise OSError("PRIVATE")
                state["present"] = False
            else:
                self.fail("unexpected command")
            return subprocess.CompletedProcess(args, 0, raw, b"")
        return tool.run_container(payload, self.contract, config, {}, execute), commands, state

    def test_cpu_policy_and_owned_cleanup_success_still_fail_qualification(self):
        report, commands, state = self.daemon(self.valid_report())
        self.assertTrue(report["entry_pass"])
        self.assertEqual(report["result"], "fail")
        self.assertEqual(report["execution"], "software")
        self.assertEqual(report["cleanup"], "confirmed")
        self.assertFalse(state["present"])
        create = next(args for args in commands if args[1:3] == ["container", "create"])
        for flag in ("--gpus", "--publish", "--volume"):
            self.assertNotIn(flag, create)
        self.assertEqual(create[create.index("--pull") + 1], "never")
        self.assertEqual(create[create.index("--runtime") + 1], "runc")
        self.assertIn("PRIVATE=", create)
        self.assertNotIn("PRIVATE=secret", create)
        self.assertNotIn("secret", json.dumps(report))

    def test_cleanup_and_isolation_failures_never_grant_entry_pass(self):
        for failure in ("network", "runtime", "devices", "ports", "weights", "create-timeout",
                        "timeout", "oom", "malformed", "ownership", "cleanup"):
            with self.subTest(failure=failure):
                report, commands, state = self.daemon(self.valid_report(), failure)
                self.assertFalse(report["entry_pass"])
                self.assertNotIn("PRIVATE", json.dumps(report))
                if failure in ("network", "runtime", "devices", "ports", "weights"):
                    self.assertFalse(any(args[1:3] == ["container", "start"] for args in commands))
                    self.assertEqual(report["cleanup"], "confirmed")
                if failure == "ownership":
                    self.assertFalse(any(args[1:3] == ["container", "rm"] for args in commands))
                    self.assertTrue(state["present"])
                if failure in ("create-timeout", "timeout", "oom", "malformed"):
                    self.assertEqual(report["cleanup"], "completion-unknown")

    def test_source_reader_rejects_links_fifo_and_changed_bytes(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp" if sys.platform == "darwin" else None) as temp:
            root = Path(temp).resolve()
            regular = root / "source.py"
            regular.write_bytes(b"public")
            link, fifo = root / "link", root / "fifo"
            link.symlink_to(regular)
            os.mkfifo(fifo)
            self.assertEqual(tool.read_source(regular), b"public")
            for path in (link, fifo, root):
                with self.assertRaises((ValueError, OSError)):
                    tool.read_source(path)
            with self.assertRaises(ValueError):
                tool.load("bad", regular, "0" * 64)

    def test_authoritative_offline_evidence_schema_and_no_overwrite(self):
        report = {"provenance": {"payload_sha256": tool.overlay.bundle_digest(self.files)}}
        revision = tool.checker.git(ROOT, "rev-parse", "HEAD").decode().strip()
        record = tool.evidence_record(revision, "cpu-unit", tool.recorder.now(), report)
        self.assertEqual(record["execution"], "software")
        self.assertEqual(record["host"]["gpus"], "none")
        self.assertEqual(record["result"], "fail")
        with tempfile.TemporaryDirectory(dir="/private/tmp" if sys.platform == "darwin" else None) as temp:
            root = Path(temp).resolve()
            env = tool.recorder.offline_environment(root / "control")
            binary, _ = tool.recorder.build_validator(ROOT, revision, root, env)
            tool.recorder.publish_evidence(record, report, binary, root, root / "evidence", env)
            self.assertEqual(json.loads((root / "evidence/run.json").read_bytes()), record)
            with self.assertRaises((ValueError, FileExistsError)):
                tool.recorder.publish_evidence(record, report, binary, root, root / "evidence", env)


if __name__ == "__main__":
    unittest.main()
