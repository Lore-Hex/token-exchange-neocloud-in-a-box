# SPDX-License-Identifier: Apache-2.0
"""Content-free pinned raise locations; no models, imports or live requests."""

from contextlib import ExitStack
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import sys
import tarfile
import types
import unittest
from unittest import mock

import tau_trial_worker as worker

ROOT = Path(__file__).resolve().parents[1]
CANARY = "PRIVATE_PROMPT_TOOL_ARGUMENT_AND_EXCEPTION"
NAME = "tau2.domains.retail.tools"


def captured(call):
    try:
        call()
    except Exception as error:
        return error
    raise AssertionError("expected synthetic failure")


class OriginTests(unittest.TestCase):
    def fixture(self, directory, stack):
        source = b"def broken():\n    raise ValueError('" + CANARY.encode() + b"')\n"
        path = Path(directory) / "private_filename.py"
        path.write_bytes(source)
        digest = hashlib.sha256(source).hexdigest()
        scope = {}
        exec(compile(source, str(path), "exec"), scope)
        module = types.ModuleType(NAME)
        module.__file__ = str(path)
        stack.enter_context(mock.patch.dict(worker.sys.modules, {NAME: module}))
        stack.enter_context(mock.patch.object(worker, "TOOL_ERROR_SOURCES", {NAME: (len(source), digest, 2)}))
        return path, digest, scope["broken"], module

    def test_pins_join_existing_sealed_source_lock(self):
        lock = json.loads((ROOT / "bench/tau-runtime-lock.json").read_text())
        self.assertEqual(len(worker.TOOL_ERROR_SOURCES), 3)
        for name, (size, digest, lines) in worker.TOOL_ERROR_SOURCES.items():
            row = lock["source_files"]["src/" + name.replace(".", "/") + ".py"]
            self.assertEqual((size, digest), (row["bytes"], row["sha256"]))
            self.assertGreater(lines, 0)
            self.assertLess(lines, 1000)

    def test_exact_deepest_source_is_counted_without_content(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            path, digest, call, _ = self.fixture(directory, stack)
            observer = worker.ToolErrors()
            self.assertIsNone(observer.sources)
            observer.record("assistant", True, captured(call))
            observer.record("assistant", True, captured(call))
            value = observer.project(2)
            self.assertEqual(value["counts"], [{"requestor": "assistant", "known_tool": True,
                "category": "value_error", "count": 2, "origin": {"source_sha256": digest, "line": 2}}])
            for private in (str(path), CANARY, "broken", "ValueError"):
                self.assertNotIn(private, json.dumps(value))

    def test_unavailable_or_changed_source_does_not_invent_an_origin(self):
        for change in ("bytes", "size", "symlink", "missing", "relative", "module", "fifo"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                path, _, call, module = self.fixture(directory, stack)
                error = captured(call)
                if change == "bytes": path.write_bytes(path.read_bytes().replace(b"broken", b"BROKEN"))
                if change == "size": path.write_bytes(b"short")
                if change == "symlink":
                    target = path.with_suffix(".target")
                    path.rename(target)
                    path.symlink_to(target)
                if change == "missing": path.unlink()
                if change == "relative": module.__file__ = "relative.py"
                if change == "module": worker.sys.modules[NAME] = object()
                if change == "fifo":
                    path.unlink()
                    os.mkfifo(path)
                observer = worker.ToolErrors()
                observer.record("assistant", True, error)
                self.assertNotIn("origin", observer.project(1)["counts"][0])

    def test_unknown_deepest_frame_and_exhausted_walk_are_not_outer_origins(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            _, digest, call, _ = self.fixture(directory, stack)
            sources = worker.tool_error_sources()
            unknown = captured(lambda: (_ for _ in ()).throw(ValueError(CANARY)))
            self.assertIsNone(worker.tool_error_origin(unknown, sources))
            def recurse(depth):
                if depth: return recurse(depth - 1)
                return call()
            self.assertIsNone(worker.tool_error_origin(captured(lambda: recurse(40)), sources))
            error = captured(call)
            self.assertEqual(worker.tool_error_origin(error, sources), (digest, 2))

    def test_hostile_exception_hooks_and_mixed_origins(self):
        class Hostile(ValueError):
            def __getattribute__(self, name):
                if name == "__traceback__": raise AssertionError(CANARY)
                return super().__getattribute__(name)
            def __str__(self): raise AssertionError(CANARY)
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            _, _, call, _ = self.fixture(directory, stack)
            observer = worker.ToolErrors()
            def hostile():
                raise Hostile(CANARY)
            for error in (captured(call), ValueError(CANARY), captured(hostile)):
                observer.record("assistant", True, error)
            value = observer.project(3)
            self.assertEqual(len(value["counts"]), 3)
            self.assertEqual(sum("origin" in row for row in value["counts"]), 1)
            self.assertNotIn(CANARY, json.dumps(value))

    def test_origin_validation_and_legacy_bytes(self):
        digest = worker.TOOL_ERROR_SOURCES[NAME][1]
        old = {"path": "live_make_tool_call", "counts": [{"requestor": "assistant",
               "known_tool": True, "category": "value_error", "count": 1}], "unattributed": 0}
        self.assertEqual(worker.validate_tool_error_attribution(old, 1), old)
        for origin in (None, {}, {"source_sha256": "0" * 64, "line": 2},
                       {"source_sha256": digest, "line": True},
                       {"source_sha256": digest, "line": 0},
                       {"source_sha256": digest, "line": 753},
                       {"source_sha256": digest, "line": 2, "filename": CANARY}):
            value = copy.deepcopy(old)
            value["counts"][0]["origin"] = origin
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                worker.validate_tool_error_attribution(value, 1)
        value = copy.deepcopy(old)
        value["counts"][0]["origin"] = {"source_sha256": digest, "line": 42}
        self.assertEqual(worker.validate_tool_error_attribution(value, 1), value)
        with self.assertRaises(ValueError):
            worker.validate_tool_error_attribution({**value, "counts": value["counts"] * 2}, 2)


def pinned_upstream_audit(archive):
    """Check exact source pins and execute original retail lookup methods only."""
    lock = json.loads((ROOT / "bench/tau-runtime-lock.json").read_text())
    worker.require(hashlib.sha256(Path(archive).read_bytes()).hexdigest() == lock["archive_sha256"])
    with tarfile.open(archive) as source, tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
        for name, (size, digest, lines) in worker.TOOL_ERROR_SOURCES.items():
            suffix = "/src/" + name.replace(".", "/") + ".py"
            members = [entry for entry in source.getmembers() if entry.name.endswith(suffix)]
            worker.require(len(members) == 1 and members[0].isfile() and members[0].size == size)
            raw = source.extractfile(members[0]).read(size + 1)
            worker.require(len(raw) == size and hashlib.sha256(raw).hexdigest() == digest
                           and len(raw.splitlines()) == lines)
            if name != NAME:
                continue
            path = Path(directory) / "retail.py"
            path.write_bytes(raw)
            loaded = types.ModuleType(NAME)
            loaded.__file__ = str(path)
            stack.enter_context(mock.patch.dict(worker.sys.modules, {NAME: loaded}))
            methods = [node for cls in ast.parse(raw).body if isinstance(cls, ast.ClassDef)
                       for node in cls.body if isinstance(node, ast.FunctionDef)
                       and node.name in ("_get_order", "_get_user")]
            worker.require(len(methods) == 2 and all(not node.decorator_list for node in methods))
            tree = ast.Module(body=[ast.ImportFrom(module="__future__",
                names=[ast.alias(name="annotations")], level=0), *methods], type_ignores=[])
            scope = {}
            exec(compile(ast.fix_missing_locations(tree), str(path), "exec"), scope)
            for method in methods:
                owner = types.SimpleNamespace(db=types.SimpleNamespace(orders={}, users={}))
                error = captured(lambda: scope[method.name](owner, CANARY))
                observer = worker.ToolErrors()
                observer.record("assistant", True, error)
                row, = observer.project(1)["counts"]
                raises = [node.lineno for node in ast.walk(method) if isinstance(node, ast.Raise)]
                worker.require(len(raises) == 1 and row == {"requestor": "assistant", "known_tool": True,
                    "category": "value_error", "count": 1, "origin": {"source_sha256": digest, "line": raises[0]}})
                worker.require(CANARY not in json.dumps(row) and str(path) not in json.dumps(row))
    print("Pinned source origins PASS; two original retail rejection sites, no model execution.")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--upstream-source":
        pinned_upstream_audit(sys.argv[2])
    else:
        unittest.main()
