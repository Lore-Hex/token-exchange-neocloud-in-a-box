# SPDX-License-Identifier: Apache-2.0
"""Synthetic trees, subprocesses and Docker responses only; no image execution."""

import copy
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

import yaml

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


class ExceptionTests(unittest.TestCase):
    def capture(self, source, error_type=ValueError):
        namespace = {"Error": error_type}
        try:
            exec(compile(source, "/selected/synthetic.py", "exec"), namespace)
        except BaseException as error:
            with mock.patch.object(cpu, "read_source", return_value=source.encode(), create=True) as read:
                packet = cpu.exception_packet(error, ("/selected/",))
            return packet, read
        self.fail("fixture did not raise")

    def test_fixed_kind_source_hash_line_and_no_content(self):
        source = "raise Error('PRIVATE secret prompt')\n"
        packet, read = self.capture(source)
        self.assertEqual(len(packet), cpu.EXCEPTION_PACKET_BYTES)
        self.assertLess(cpu.STATE_PACKET_BYTES, 512)
        row = cpu.decode_exception(packet)
        cpu.validate_exception(row)
        self.assertEqual(row["type"], "value")
        self.assertEqual(row["frames"], [{"source_sha256": hashlib.sha256(source.encode()).hexdigest(), "line": 1}])
        self.assertNotIn("PRIVATE", json.dumps(row))
        self.assertNotIn("synthetic.py", json.dumps(row))
        read.assert_called_once_with("/selected/synthetic.py", 2 << 20)

    def test_custom_exception_hooks_are_not_invoked(self):
        class PrivateError(Exception):
            def __str__(self):
                raise AssertionError("formatted PRIVATE")
            def __repr__(self):
                raise AssertionError("formatted PRIVATE")
            def __getattribute__(self, key):
                raise AssertionError("looked up PRIVATE")
        packet, _ = self.capture("raise Error()\n", PrivateError)
        row = cpu.decode_exception(packet)
        self.assertEqual(row["type"], "other")
        self.assertFalse(row["observer_failed"])
        self.assertEqual(len(row["frames"]), 1)

    def test_none_and_each_known_builtin(self):
        self.assertIsNone(cpu.decode_exception(cpu.exception_packet(None, ())))
        for index, cls in enumerate(cpu.EXCEPTION_TYPES[1:], 1):
            row = cpu.decode_exception(cpu.exception_packet(cls("PRIVATE"), ()))
            self.assertEqual(row["type"], cpu.EXCEPTION_NAMES[index])
            self.assertEqual(row["frames"], [])
            cpu.validate_exception(row)

    def validation_fixture(self, rows, count=None):
        class ValidationError(Exception):
            def error_count(self):
                return len(rows) if count is None else count
            def errors(self, **kwargs):
                self_kwargs.append(kwargs)
                return rows
        module = types.ModuleType("pydantic_core")
        module.ValidationError = ValidationError
        self_kwargs = []
        return module, ValidationError("PRIVATE wrapped model input"), self_kwargs

    def test_validation_cause_preserves_only_source_coordinates(self):
        source = "raise ValueError('PRIVATE model value')\n"
        try:
            exec(compile(source, "/selected/validator.py", "exec"), {})
        except ValueError as error:
            cause = error
        module, error, calls = self.validation_fixture([
            {"ctx": {"error": cause}, "msg": "PRIVATE", "loc": ("PRIVATE",), "input": "PRIVATE"}])
        with mock.patch.dict(sys.modules, {"pydantic_core": module}), \
                mock.patch.object(cpu, "read_source", return_value=source.encode(), create=True):
            row = cpu.decode_exception(cpu.exception_packet(error, ("/selected/",)))
        self.assertEqual(row["frames"], [{"source_sha256": hashlib.sha256(source.encode()).hexdigest(), "line": 1}])
        self.assertEqual(calls, [{"include_url": False, "include_context": True, "include_input": False}])
        self.assertEqual(row["type"], "other")
        self.assertFalse(row["observer_failed"])
        self.assertNotIn("PRIVATE", json.dumps(row))
        self.assertNotIn("validator.py", json.dumps(row))

    def test_validation_cause_is_exact_class_bounded_and_nonrecursive(self):
        module, error, calls = self.validation_fixture([], count=9)
        with mock.patch.dict(sys.modules, {"pydantic_core": module}):
            row = cpu.decode_exception(cpu.exception_packet(error, ()))
            self.assertTrue(row["observer_failed"])
            self.assertEqual(calls, [])
            class Child(module.ValidationError):
                def error_count(self):
                    self.fail("subclass hook must not run")
            self.assertEqual(cpu.validation_causes(Child()), ())
            self.assertEqual(cpu.validation_causes(ValueError()), ())
        module, error, _ = self.validation_fixture([{"ctx": {"error": RuntimeError("PRIVATE")}}])
        with mock.patch.dict(sys.modules, {"pydantic_core": module}):
            self.assertEqual(cpu.validation_causes(error), ())
        for rows, count in ((["PRIVATE"], 1), ([], True), ([{}], 0)):
            module, error, _ = self.validation_fixture(rows, count)
            with mock.patch.dict(sys.modules, {"pydantic_core": module}):
                row = cpu.decode_exception(cpu.exception_packet(error, ()))
                self.assertTrue(row["observer_failed"])
                self.assertNotIn("PRIVATE", json.dumps(row))

    def test_stack_depth_and_source_reads_are_bounded(self):
        source = "def recurse(n):\n if n: recurse(n-1)\n else: raise Error('PRIVATE')\nrecurse(100)\n"
        packet, read = self.capture(source)
        row = cpu.decode_exception(packet)
        self.assertTrue(row["truncated"])
        self.assertEqual(len(row["frames"]), cpu.EXCEPTION_FRAMES)
        self.assertLessEqual(read.call_count, 64)

    def test_unreadable_source_is_not_replaced_with_path_or_message(self):
        try:
            raise ValueError("PRIVATE")
        except ValueError as error:
            with mock.patch.object(cpu, "read_source", side_effect=OSError("PRIVATE"), create=True):
                row = cpu.decode_exception(cpu.exception_packet(error, (str(ROOT) + "/",)))
        self.assertTrue(row["source_unavailable"])
        self.assertEqual(row["frames"], [])
        self.assertNotIn("PRIVATE", json.dumps(row))

    def test_malformed_packets_and_rows_reject(self):
        good, _ = self.capture("raise Error()\n")
        for raw in (good[:-1], good + b"x", bytes([255]) + good[1:],
                    good[:1] + b"\xff" + good[2:], good[:2] + b"\xff" + good[3:],
                    bytes([0]) + good[1:], good[:-1] + b"x"):
            with self.subTest(raw=raw[:3]), self.assertRaises(ValueError):
                cpu.decode_exception(raw)
        row = cpu.decode_exception(good)
        for key, value in (("type", "PRIVATE"), ("frames", ["PRIVATE"]),
                           ("truncated", 1), ("observer_failed", "PRIVATE")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                cpu.validate_exception({**row, key: value})
        for frame in ({"source_sha256": "PRIVATE", "line": 1},
                      {"source_sha256": "a" * 64, "line": True},
                      {"source_sha256": "a" * 64, "line": 1000001}):
            with self.assertRaises(ValueError):
                cpu.validate_exception({**row, "frames": [frame]})


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
                    cpu.validate_finders(cpu.decode_finders(packet[2:2 + cpu.FINDER_PACKET_BYTES]))
                    fault = cpu.decode_exception(packet[2 + cpu.FINDER_PACKET_BYTES:])
                    self.assertEqual(fault["type"], "value")
                    self.assertNotIn("PRIVATE", json.dumps(fault))
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
                    cpu.validate_finders(cpu.decode_finders(packet[2:2 + cpu.FINDER_PACKET_BYTES]))
                    self.assertIsNone(cpu.decode_exception(packet[2 + cpu.FINDER_PACKET_BYTES:]))
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
                packet = bits + finder_same() + bytes(cpu.EXCEPTION_PACKET_BYTES)
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
        self.assertEqual(len(raw), cpu.FINDER_PACKET_BYTES)
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
                module, function = self.synthetic(index, method, method == "find_distributions")
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

    def test_identical_vendored_method_requires_its_own_source_and_module_binding(self):
        for canonical, vendored in ((3, 4), (2, 5)):
            spec = cpu.FINDER_SPECS[vendored]
            site = spec["sites"][0]
            method = site["qualname"].split(".")[-1]
            module, function = self.synthetic(vendored, method, method == "find_distributions")
            top, top_function = self.synthetic(canonical, method, method == "find_distributions")
            self.assertTrue(helpers.same_code(function.__code__, top_function.__code__))
            top_spec = cpu.FINDER_SPECS[canonical]
            top_ref = (top_spec["sites"][0]["id"], top_spec["id"], top_function.__code__, None)
            own_ref = (site["id"], spec["id"], function.__code__, None)
            with self.subTest(category=site["id"]), mock.patch.dict(
                    sys.modules, {module.__name__: module, top.__name__: top}):
                old = (self.sources, [top_ref], "/selected")
                self.assertEqual(self.observe([module.Hook()], old)["entries"], ["unknown"])
                current = (self.sources, [top_ref, own_ref], "/selected")
                row = self.observe([*self.expected, module.Hook()], current)
                self.assertEqual(row["entries"][-1], site["id"])
                self.assertFalse(row["same_identity_list"])
                self.assertEqual(self.observe([top.Hook()], current)["entries"], [top_ref[0]])
                # A matching reference cannot substitute for the loaded module.
                shadow = types.ModuleType(module.__name__)
                shadow.__file__ = module.__file__
                with mock.patch.dict(sys.modules, {module.__name__: shadow}):
                    self.assertEqual(self.observe([module.Hook()], current)["entries"], ["unknown"])
                # Missing or changed vendor bytes provide no vendor reference.
                sources = copy.deepcopy(self.sources)
                sources[vendored]["status"] = "source-mismatch"
                self.assertEqual(self.observe([module.Hook()], (sources, [top_ref], "/selected"))["entries"],
                                 ["unknown"])
                bad = copy.deepcopy(row)
                bad["sources"][vendored]["status"] = "source-mismatch"
                with self.assertRaises(ValueError):
                    cpu.validate_finders(bad)

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
        for name in (tool.SCRIPT, tool.BASE, tool.RUNTIME, tool.LAB, tool.LAB_BOOTSTRAP, tool.EXCEPTIONS,
                     tool.TEST, tool.SIX_TEST, tool.OPENCV_TEST, "scripts/test_deepseek_lab.py"):
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
                          "finder_diagnostic": cpu.decode_finders(finder_same()), "exception_diagnostic": None}}

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
        changed = {**self.extra, tool.SIX_TEST: self.extra[tool.SIX_TEST] + b"\n"}
        _, changed_contract = tool.prepare_files("1" * 40, self.bundle, changed)
        self.assertNotEqual(changed_contract["source_bundle_sha256"], contract["source_bundle_sha256"])
        for name, digest in contract["sources"].items():
            self.assertEqual(tool.overlay.digest(files[name]), digest)
        self.assertFalse(any(name.startswith("pack/") or name == "nib" for name in files))
        self.assertEqual(contract["arguments"], tool.lab.lab_args(tool.recorder.prep.launch_plan()))
        self.assertEqual(contract["environment"], tool.lab.lab_environment(tool.recorder.prep.launch_plan()))
        self.assertIn(self.extra[tool.LAB], files["lab.py"])
        self.assertIn(self.extra[tool.LAB_BOOTSTRAP], files["lab.py"])
        self.assertIn(self.extra[tool.RUNTIME], files["launch.py"])
        self.assertEqual(hashlib.sha256(self.extra[tool.BASE]).hexdigest(), tool.BASE_SHA)

    def test_exception_observer_accepts_exact_prepared_source_layout(self):
        roots = (cpu.ROOT + "/", *tuple(path.rstrip("/") + "/" for path in self.contract["paths"]))
        self.assertEqual(len(roots), 5)
        row = cpu.decode_exception(cpu.exception_packet(ValueError("PRIVATE"), roots))
        self.assertFalse(row["observer_failed"])
        self.assertTrue(cpu.decode_exception(cpu.exception_packet(ValueError(), roots + ("/extra/",)))["observer_failed"])

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

    def test_exception_observation_cannot_be_claimed_as_entry_success(self):
        report = self.valid_report()
        report["entry"]["exception_diagnostic"] = cpu.decode_exception(
            cpu.exception_packet(RuntimeError("PRIVATE"), ()))
        with self.assertRaises(ValueError):
            self.validate(report)
        report["entry_pass"] = False
        self.assertFalse(self.validate(report))
        self.assertNotIn("PRIVATE", json.dumps(report))

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
        tool.publish_container_payload(payload, self.files)
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

    def test_container_payload_readable_without_changing_private_parent_or_bytes(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp" if sys.platform == "darwin" else None) as temp:
            parent = Path(temp).resolve()
            private_file = parent / "host-only"
            private_file.write_bytes(b"host-only")
            private_file.chmod(0o600)
            destination = parent / "payload"
            tool.publish_container_payload(destination, self.files)
            self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(private_file.stat().st_mode), 0o600)
            self.assertEqual({name: tool.read_source(destination / name) for name in self.files}, self.files)
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o555)
            for name in self.files:
                path = destination / name
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o555 if name == "python" else 0o444)
                for directory in path.parents[:len(Path(name).parts)]:
                    self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o555)
            with self.assertRaises(FileExistsError):
                tool.publish_container_payload(destination, self.files)

    def test_container_payload_refuses_public_parent_and_unsafe_sources(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp" if sys.platform == "darwin" else None) as temp:
            parent = Path(temp).resolve()
            parent.chmod(0o755)
            with self.assertRaises(ValueError):
                tool.publish_container_payload(parent / "public", self.files)
            self.assertFalse((parent / "public").exists())
            parent.chmod(0o700)
            with self.assertRaises(ValueError):
                tool.publish_container_payload(parent / "unsafe", {"../outside": b"data"})
            self.assertFalse((parent / "outside").exists())
            self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o700)

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

    def evidence_fixture(self, host_kind="github-actions"):
        report, _, _ = self.daemon(self.valid_report())
        report["provenance"] = {
            "git_sha": "1" * 40, "source_bundle_sha256": self.contract["source_bundle_sha256"],
            "payload_sha256": tool.overlay.bundle_digest(self.files), "requested_image": tool.overlay.BASE_IMAGE,
            "expected_config_sha256": self.contract["official_config"]["sha256"],
            "measurement_sha256": self.contract["measurement_sha256"], "raw_oci_metadata_verified": True,
            "validator_source_sha256": "a" * 64,
        }
        with mock.patch.object(tool.recorder, "now", return_value="2026-10-20T00:01:00Z"):
            record = tool.evidence_record("1" * 40, "cpu-unit", "2026-10-20T00:00:00Z", report, host_kind)
        return record, report

    def check_evidence(self, record, report, host_kind="github-actions"):
        return tool.check_evidence(record, report, "1" * 40, "cpu-unit", self.files,
                                   self.contract, host_kind, "a" * 64)

    def test_evidence_recheck_exact_producer_and_host(self):
        for host_kind in ("local", "github-actions"):
            record, report = self.evidence_fixture(host_kind)
            self.assertTrue(self.check_evidence(record, report, host_kind))
            self.assertEqual(record["host"]["provider"], host_kind)
            self.assertFalse(record["hardware_validated"])
            self.assertEqual(record["result"], "fail")
            suffix = "" if host_kind == "local" else " --host-kind github-actions"
            self.assertEqual(record["command"], "python3 -I -S scripts/record-engine-cpu-entry.py --slug cpu-unit" + suffix)
            with self.assertRaises(ValueError):
                self.check_evidence(record, report, "local" if host_kind == "github-actions" else "github-actions")
        with self.assertRaises(ValueError):
            tool.evidence_record("1" * 40, "cpu-unit", "", report, "unreviewed")

    def test_evidence_recheck_rejects_unearned_success_and_provenance_changes(self):
        record, report = self.evidence_fixture()
        mutations = [
            ("record", ["git_sha"], "2" * 40),
            ("record", ["result"], "pass"),
            ("record", ["hardware_validated"], 0),
            ("record", ["started_utc"], "2026-02-30T00:00:00Z"),
            ("record", ["ended_utc"], "2026-10-19T00:00:00Z"),
            ("record", ["host", "provider"], "local"),
            ("record", ["notes"], "PRIVATE"),
            ("report", ["cleanup"], "completion-unknown"),
            ("report", ["stage"], "result-validation"),
            ("report", ["private"], "PRIVATE"),
            ("report", ["entry_pass"], 1),
            ("report", ["experiment", "entry", "exit_code"], 1),
            ("report", ["image", "raw_config_sha256"], "b" * 64),
            ("report", ["image", "manifest_sha256"], "b" * 64),
            ("report", ["image", "docker_reported_id"], "invalid"),
            ("report", ["image", "raw_config_link_verified"], 1),
            ("report", ["image", "local_layers_rehashed"], True),
        ]
        mutations += [("report", ["provenance", key], "b" * 64)
                      for key in report["provenance"]]
        for kind, keys, value in mutations:
            bad_record, bad_report = copy.deepcopy((record, report))
            target = bad_record if kind == "record" else bad_report
            for key in keys[:-1]:
                target = target[key]
            target[keys[-1]] = value
            with self.subTest(kind=kind, keys=keys), self.assertRaises((ValueError, KeyError, TypeError)):
                self.check_evidence(bad_record, bad_report)
        report["entry_pass"] = report["experiment"]["entry_pass"] = False
        report["experiment"]["entry"]["exit_code"] = 1
        self.assertFalse(self.check_evidence(record, report))

    def test_vendored_raw_oci_metadata_is_exact_and_image_binding_consistent(self):
        config = tool.recorder.raw_metadata(ROOT / "bench/engine-cpu-entry/oci", self.contract)
        self.assertEqual((config["os"], config["architecture"]), ("linux", "amd64"))
        record, report = self.evidence_fixture()
        actual = tool.recorder.image_binding({
            "Os": "linux", "Architecture": "amd64", "Id": report["image"]["docker_reported_id"],
            "RepoDigests": [tool.overlay.BASE_IMAGE],
            "RootFS": {"Type": "layers", "Layers": config["rootfs"]["diff_ids"]},
        }, config, self.contract)
        self.assertEqual(report["image"], actual)
        self.assertTrue(self.check_evidence(record, report))

    def test_native_workflow_is_manual_main_only_and_rechecks_failure_artifacts(self):
        workflow = yaml.load((ROOT / ".github/workflows/engine-cpu-entry.yml").read_text(),
                             Loader=yaml.BaseLoader)
        self.assertEqual(set(workflow["on"]), {"workflow_dispatch"})
        inputs = workflow["on"]["workflow_dispatch"]["inputs"]
        self.assertEqual(set(inputs), {"tokenizer_continuation", "tokenizer_materialization"})
        for item in inputs.values():
            self.assertEqual(item["type"], "boolean")
            self.assertEqual(item["default"], "false")
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertEqual(workflow["concurrency"]["cancel-in-progress"], "false")
        self.assertEqual(set(workflow["jobs"]), {"diagnostic"})
        job = workflow["jobs"]["diagnostic"]
        self.assertEqual(job["if"], "github.ref == 'refs/heads/main'")
        self.assertEqual(job["runs-on"], "ubuntu-24.04")
        self.assertEqual(job["timeout-minutes"], "45")
        steps = job["steps"]
        self.assertEqual(steps[0]["with"]["persist-credentials"], "false")
        self.assertEqual(steps[1]["with"]["cache"], "false")
        self.assertEqual([step["uses"] for step in steps if "uses" in step], [
            "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
            "actions/setup-go@40f1582b2485089dde7abd97c1529aa768e1baff",
            "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
        ])
        self.assertEqual(steps[3]["run"], "python3 -I -S host/pull-lab-image.py")
        self.assertIn('python3 -I -S scripts/record-engine-cpu-entry.py --slug "$SLUG" --host-kind github-actions',
                      steps[4]["run"])
        self.assertEqual(steps[4]["continue-on-error"], "true")
        self.assertEqual(steps[5]["if"], "always()")
        self.assertEqual(steps[5]["with"]["retention-days"], "7")
        self.assertEqual(steps[5]["with"]["if-no-files-found"], "error")
        self.assertEqual(steps[5]["with"]["path"], "evidence/p2/*-${{ env.SLUG }}/")
        self.assertEqual(steps[6]["if"], "always()")
        self.assertIn('--check-result "$1" --host-kind github-actions', steps[6]["run"])
        self.assertIn("label=nib.cpu-entry", steps[6]["run"])
        self.assertNotIn("secrets.", json.dumps(workflow))

    def test_native_workflow_cleanup_requires_successful_empty_query(self):
        workflow = yaml.load((ROOT / ".github/workflows/engine-cpu-entry.yml").read_text(),
                             Loader=yaml.BaseLoader)
        cleanup = workflow["jobs"]["diagnostic"]["steps"][-1]["run"].split("set --", 1)[0]
        fake_docker = ('docker() {\n'
                       '  test "$*" = "container ls --all --quiet --filter label=nib.cpu-entry" || exit 127\n'
                       '  printf "%s" "$QUERY_OUTPUT"\n'
                       '  return "$QUERY_STATUS"\n'
                       '}\n')
        for status, output, expected in ((0, "", 0), (1, "", 1), (0, "owned", 1), (1, "owned", 1)):
            with self.subTest(status=status, output=output):
                result = subprocess.run(["bash", "-c", fake_docker + cleanup],
                                        env={**os.environ, "QUERY_STATUS": str(status), "QUERY_OUTPUT": output},
                                        capture_output=True, timeout=10)
                self.assertEqual(result.returncode, expected)

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
