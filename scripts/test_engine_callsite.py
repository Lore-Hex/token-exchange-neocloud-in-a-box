# SPDX-License-Identifier: Apache-2.0
"""Bounded source compilation/code matching; no upstream module execution."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def load(name, path):
    module = types.ModuleType(name)
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    return module


bootstrap = load("callsite_bootstrap", ROOT / "scripts/engine_python_bootstrap.py")
smoke = load("callsite_smoke", ROOT / "scripts/engine_import_smoke.py")


def verify_public_sources(directory):
    """Optional explicit local source-pin audit; reads/compiles, never imports."""
    bootstrap.require(sys.version_info[:2] == (3, 12))
    specs = smoke.callsite_specs()
    by_path = {}
    for spec in specs:
        raw = bootstrap.read_source(str(directory.absolute() / (spec["id"] + ".py")), 256 << 10)
        if len(raw) != spec["bytes"] or hashlib.sha256(raw).hexdigest() != spec["sha256"]:
            raise ValueError("public source differs")
        by_path[spec["installed"]] = raw
    with mock.patch.object(bootstrap, "read_source", side_effect=lambda path, limit: by_path[path.removeprefix("/public/")]):
        sources, references = bootstrap.compiled_catalog(specs, {"stdlib": "/public", "packages": "/public"})
    if any(row["status"] != "verified" for row in sources):
        raise ValueError("public selector differs")
    for _, _, reference, _ in references:
        if not bootstrap.same_code(reference, reference):
            raise ValueError("public code comparison differs")
    return {"python": ".".join(map(str, sys.version_info[:3])), "sources": len(sources),
            "bytes": sum(s["bytes"] for s in specs), "sites": len(references)}


class CallsiteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nib-callsite-", dir="/private/tmp" if sys.platform == "darwin" else None)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.raw = b"def public_call():\n    return 'public'\nraise RuntimeError('reference must never execute')\n"
        self.path = self.root / "reference.py"
        self.path.write_bytes(self.raw)
        self.spec = {"id": "source", "root": "packages", "installed": "reference.py", "bytes": len(self.raw),
                     "sha256": hashlib.sha256(self.raw).hexdigest(), "sites": [{"id": "public-call", "qualname": "public_call"}]}
        self.roots = {"packages": str(self.root)}

    def catalog(self, specs=None):
        return bootstrap.compiled_catalog([self.spec] if specs is None else specs, self.roots)

    def frame(self, code, parent=None, offset=0):
        return types.SimpleNamespace(f_code=code, f_back=parent, f_lasti=offset)

    def code(self, raw=None):
        module = compile(self.raw if raw is None else raw, "/synthetic-private/filename.py", "exec", flags=0, dont_inherit=True, optimize=0)
        return next(c for c in module.co_consts if type(c) is types.CodeType)

    def test_verified_sources_compile_once_explicitly_without_execution(self):
        with mock.patch("builtins.compile", wraps=compile) as compile_spy:
            catalog = self.catalog()
        self.assertEqual(catalog[0], [{"id": "source", "status": "verified"}])
        self.assertEqual(compile_spy.call_count, 1)
        self.assertEqual(compile_spy.call_args.kwargs, {"flags": 0, "dont_inherit": True, "optimize": 0})
        with mock.patch("builtins.compile", side_effect=AssertionError("must not recompile")):
            for _ in range(3):
                row = bootstrap.callsite_observation(catalog, self.frame(catalog[1][0][2]))
                self.assertEqual(row["ids"], ["public-call"])

    def test_missing_mismatch_special_and_parser_failures_have_no_references(self):
        self.path.unlink()
        self.assertEqual(self.catalog(), ([{"id": "source", "status": "missing"}], []))
        self.path.write_bytes(b"synthetic-private")
        self.assertEqual(self.catalog(), ([{"id": "source", "status": "source-mismatch"}], []))
        self.path.unlink()
        self.path.symlink_to(self.root / "synthetic-private")
        self.assertEqual(self.catalog(), ([{"id": "source", "status": "read-rejected"}], []))
        self.path.unlink()
        self.path.write_bytes(self.raw)
        for error in (SyntaxError("synthetic-private"), ValueError("synthetic-private")):
            with mock.patch("builtins.compile", side_effect=error):
                self.assertEqual(self.catalog(), ([{"id": "source", "status": "compile-rejected"}], []))
        self.spec["sites"].append({"id": "missing", "qualname": "absent"})
        row = self.catalog()
        self.assertEqual(row, ([{"id": "source", "status": "selector-rejected"}], []))
        self.assertNotIn("private", json.dumps(row))

    def test_all_structural_fields_including_constants_lines_and_exceptions_matter(self):
        catalog = self.catalog()
        code = self.code()
        # Filename and globals are deliberately not authenticated by structure.
        self.assertTrue(bootstrap.same_code(code, catalog[1][0][2]))
        changes = {"co_consts": (None, "synthetic-private"), "co_exceptiontable": b"\x80\x01\x01\x00",
                   "co_linetable": b"", "co_firstlineno": code.co_firstlineno + 1,
                   "co_flags": code.co_flags | 0x1000000, "co_stacksize": code.co_stacksize + 1,
                   "co_qualname": "synthetic-private"}
        for field, value in changes.items():
            with self.subTest(field=field):
                changed = code.replace(**{field: value})
                self.assertEqual(changed.co_code, code.co_code)
                self.assertFalse(bootstrap.same_code(changed, code))
                self.assertEqual(bootstrap.callsite_observation(catalog, self.frame(changed))["ids"], [])

    def test_private_code_and_constants_are_never_hashed_serialized_or_converted(self):
        class Private:
            def __str__(self): raise AssertionError("converted")
            def __repr__(self): raise AssertionError("represented")
            def __hash__(self): raise AssertionError("hashed")
            def __eq__(self, other): raise AssertionError("compared")
        catalog = self.catalog()
        code = self.code().replace(co_consts=(None, Private()))
        with mock.patch.object(bootstrap.hashlib, "sha256", side_effect=AssertionError("hashed")):
            row = bootstrap.callsite_observation(catalog, self.frame(code))
        self.assertEqual(row["ids"], [])
        self.assertEqual(row["unmatched_frames"], 1)
        self.assertNotIn("synthetic-private", json.dumps(row))

    def test_signed_zero_and_nested_constant_keys_do_not_false_match(self):
        code = self.code()
        for left, right in ((0.0, -0.0), (complex(0.0, 0.0), complex(-0.0, 0.0)),
                            (complex(1.0, 0.0), complex(1.0, -0.0)),
                            ((0.0,), (-0.0,)), (frozenset((0.0,)), frozenset((-0.0,))),
                            (1, True), (1, 1.0)):
            with self.subTest(kind=type(left).__name__):
                first, second = code.replace(co_consts=(None, left)), code.replace(co_consts=(None, right))
                self.assertNotEqual(first, second)
                self.assertFalse(bootstrap.same_code(first, second))

    def test_phase_offsets_come_only_from_verified_compiled_reference(self):
        self.raw = b"import first\nimport second\n"
        self.path.write_bytes(self.raw)
        self.spec.update(bytes=len(self.raw), sha256=hashlib.sha256(self.raw).hexdigest(), sites=[
            {"id": "first-import", "qualname": "<module>", "line": 1, "op": "IMPORT_NAME", "arg": "first"}])
        catalog = self.catalog()
        code, offsets = catalog[1][0][2:]
        for offset in offsets:
            self.assertEqual(bootstrap.callsite_observation(catalog, self.frame(code, offset=offset))["ids"], ["first-import"])
        self.assertEqual(bootstrap.callsite_observation(catalog, self.frame(code, offset=len(code.co_code) - 2))["ids"], [])
        self.spec["sites"][0]["arg"] = "second"
        self.assertEqual(self.catalog(), ([{"id": "source", "status": "selector-rejected"}], []))

    def test_frame_scan_and_id_output_caps_are_independent_nearest_first(self):
        code = self.code()
        refs = [("id-" + str(i), "source", code, None) for i in range(10)]
        row = bootstrap.callsite_observation(([{"id": "source", "status": "verified"}], refs), self.frame(code))
        self.assertEqual(row["ids"], ["id-" + str(i) for i in range(8)])
        self.assertTrue(row["output_truncated"])
        self.assertFalse(row["scan_truncated"])
        self.assertEqual(row["frames_scanned"], 1)
        unknown = compile("pass", "synthetic-private", "exec")
        for depth, truncated, matched in ((127, False, True), (128, True, False)):
            frame = self.frame(code)
            for _ in range(depth):
                frame = self.frame(unknown, frame)
            row = bootstrap.callsite_observation(self.catalog(), frame)
            self.assertEqual(row["frames_scanned"], 128)
            self.assertEqual(row["scan_truncated"], truncated)
            self.assertFalse(row["output_truncated"])
            self.assertEqual(bool(row["ids"]), matched)
        # Repeated recursive frames do not consume extra ID slots.
        frame = None
        for _ in range(128):
            frame = self.frame(code, frame)
        row = bootstrap.callsite_observation(self.catalog(), frame)
        self.assertEqual(row["ids"], ["public-call"])
        self.assertFalse(row["output_truncated"])

    def test_comparison_failure_retains_source_availability_and_has_fixed_gap(self):
        catalog = self.catalog()
        with mock.patch.object(bootstrap, "same_code", side_effect=RuntimeError("synthetic-private")):
            row = bootstrap.callsite_observation(catalog, self.frame(self.code()))
        self.assertTrue(row["comparison_failed"])
        self.assertEqual(row["sources"], catalog[0])
        self.assertEqual(row["ids"], [])
        self.assertNotIn("private", json.dumps(row))

    def test_per_comparison_budget_is_finite_even_for_plain_constants(self):
        code = self.code().replace(co_consts=(None, (0,) * 20001))
        catalog = ([{"id": "source", "status": "verified"}], [("public-call", "source", code, None)])
        row = bootstrap.callsite_observation(catalog, self.frame(code))
        self.assertTrue(row["comparison_failed"])
        self.assertEqual(row["ids"], [])

    def test_unknown_frames_with_spoofed_names_are_not_source_matches(self):
        code = self.code(b"def forged():\n    return 'synthetic-private'\n")
        code = code.replace(co_name="public_call", co_qualname="public_call", co_filename="<public-callsite-reference>")
        row = bootstrap.callsite_observation(self.catalog(), self.frame(code))
        self.assertEqual(row["ids"], [])
        self.assertNotIn("private", json.dumps(row))

    def test_schema_requires_fixed_statuses_ids_and_consistent_bounds(self):
        specs = smoke.callsite_specs()
        valid = {"sources": [{"id": s["id"], "status": "verified"} for s in specs], "ids": [specs[0]["sites"][0]["id"]],
                 "frames_scanned": 1, "unmatched_frames": 0, "scan_truncated": False,
                 "output_truncated": False, "comparison_failed": False}
        smoke.validate_callsites(bootstrap.require, valid)
        for field, value in (("ids", ["synthetic-private"]), ("ids", valid["ids"] * 9), ("frames_scanned", 129),
                             ("frames_scanned", True), ("unmatched_frames", 2), ("unmatched_frames", 1),
                             ("scan_truncated", True), ("output_truncated", True), ("comparison_failed", "private")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                smoke.validate_callsites(bootstrap.require, {**valid, field: value})
        for status in bootstrap.CALLSITE_STATUSES:
            copy = json.loads(json.dumps(valid))
            copy["sources"][0]["status"] = status
            if status != "verified":
                with self.assertRaises(ValueError):
                    smoke.validate_callsites(bootstrap.require, copy)
            copy["ids"] = []
            copy["unmatched_frames"] = 1
            smoke.validate_callsites(bootstrap.require, copy)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--public-sources":
        print(json.dumps(verify_public_sources(Path(sys.argv[2]))))
    else:
        unittest.main()
