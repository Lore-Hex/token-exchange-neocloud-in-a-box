# SPDX-License-Identifier: Apache-2.0
"""Content-free synthetic exception chains; no engine, GPU or network execution."""

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

import test_deepseek_lab as fixtures

errors = fixtures.lab


class ChainTests(unittest.TestCase):
    def capture(self, source, **namespace):
        try:
            exec(compile(source, "/selected/PRIVATE-source.py", "exec"), namespace)
        except BaseException as error:
            return error
        self.fail("fixture did not raise")

    def packet(self, error, source=b"synthetic source"):
        with mock.patch.object(errors, "read_source", return_value=source) as reader:
            raw = errors.worker_exception_packet(error, ("/selected/",))
        self.assertEqual(len(raw), 1175)
        self.assertNotIn(b"PRIVATE", raw)
        row = errors.decode_worker_exception(raw)
        errors.validate_worker_exception(row)
        self.assertNotIn("PRIVATE", json.dumps(row))
        return raw, row, reader

    def test_cleanup_masking_keeps_original_context_coordinates(self):
        source = "try:\n raise ValueError('PRIVATE original')\nfinally:\n raise RuntimeError('PRIVATE cleanup')\n"
        error = self.capture(source)
        _, row, _ = self.packet(error, source.encode())
        first, original = row["exceptions"]
        self.assertEqual((first["type"], first["cause"], first["context"]), ("runtime", None, 1))
        self.assertEqual((original["type"], original["cause"], original["context"]), ("value", None, None))
        digest = hashlib.sha256(source.encode()).hexdigest()
        self.assertEqual(first["frames"], [{"source_sha256": digest, "line": 4}])
        self.assertEqual(original["frames"], [{"source_sha256": digest, "line": 2}])
        self.assertFalse(row["truncated"])

    def test_explicit_cause_and_suppressed_context_are_preserved_without_mutation(self):
        for suffix in ("from caught", "from None"):
            source = ("try:\n raise ValueError('PRIVATE cause')\nexcept ValueError as caught:\n"
                      " raise RuntimeError('PRIVATE wrapper') " + suffix + "\n")
            error = self.capture(source)
            before = tuple(descriptor.__get__(error) for descriptor in
                           (BaseException.__cause__, BaseException.__context__, BaseException.__suppress_context__))
            _, row, _ = self.packet(error)
            self.assertEqual(len(row["exceptions"]), 2)
            self.assertEqual(row["exceptions"][0]["context"], 1)
            self.assertEqual(row["exceptions"][0]["cause"], 1 if suffix == "from caught" else None)
            after = tuple(descriptor.__get__(error) for descriptor in
                          (BaseException.__cause__, BaseException.__context__, BaseException.__suppress_context__))
            self.assertTrue(all(a is b for a, b in zip(before, after)))

    def test_distinct_links_cycles_and_exception_limit(self):
        first, second, third = RuntimeError(), ValueError(), KeyError()
        first.__cause__, first.__context__ = second, third
        second.__context__ = first
        _, row, _ = self.packet(first)
        self.assertEqual([(n["cause"], n["context"]) for n in row["exceptions"]],
                         [(1, 2), (None, 0), (None, None)])
        chain = [ValueError() for _ in range(100)]
        for before, after in zip(chain, chain[1:]):
            before.__context__ = after
        _, row, _ = self.packet(chain[0])
        self.assertEqual(len(row["exceptions"]), 4)
        self.assertTrue(row["truncated"])
        self.assertIsNone(row["exceptions"][-1]["context"])

    def test_traceback_scans_and_reads_share_one_budget(self):
        source = "def recurse(n):\n if n: recurse(n-1)\n else: raise ValueError('PRIVATE')\nrecurse(100)\n"
        chain = [self.capture(source) for _ in range(4)]
        for before, after in zip(chain, chain[1:]):
            before.__cause__ = after
        _, row, reader = self.packet(chain[0])
        self.assertLessEqual(reader.call_count, 64)
        self.assertTrue(all(n["truncated"] for n in row["exceptions"]))
        self.assertEqual(len(row["exceptions"][0]["frames"]), 8)
        self.assertTrue(all(not n["frames"] for n in row["exceptions"][1:]))

    def test_hostile_exception_attributes_messages_and_equality_are_never_used(self):
        class PrivateError(Exception):
            def __getattribute__(self, key):
                raise AssertionError("PRIVATE attribute lookup")
            def __str__(self):
                raise AssertionError("PRIVATE formatting")
            def __repr__(self):
                raise AssertionError("PRIVATE representation")
            def __eq__(self, other):
                raise AssertionError("PRIVATE comparison")
        first, second = PrivateError("PRIVATE args"), PrivateError("PRIVATE args")
        BaseException.__cause__.__set__(first, second)
        BaseException.__context__.__set__(second, first)
        _, row, _ = self.packet(first)
        self.assertEqual([n["type"] for n in row["exceptions"]], ["other", "other"])
        self.assertFalse(any(n["observer_failed"] for n in row["exceptions"]))

    def test_native_classification_requires_exact_loaded_module_and_class(self):
        class AcceleratorError(RuntimeError):
            pass
        class OutOfMemoryError(RuntimeError):
            pass
        class Child(AcceleratorError):
            pass
        torch, native = types.ModuleType("torch"), types.ModuleType("torch._C")
        torch._C = native
        native.AcceleratorError, native.OutOfMemoryError = AcceleratorError, OutOfMemoryError
        with mock.patch.dict(sys.modules, {"torch": torch, "torch._C": native}):
            for cls, expected in ((AcceleratorError, "accelerator"), (OutOfMemoryError, "accelerator-oom"),
                                  (Child, "other"), (RuntimeError, "runtime")):
                _, row, _ = self.packet(cls("PRIVATE"))
                self.assertEqual(row["exceptions"][0]["type"], expected)
            torch._C = object()
            _, row, _ = self.packet(AcceleratorError())
            self.assertEqual(row["exceptions"][0]["type"], "other")
        with mock.patch.dict(sys.modules, {"torch": None, "torch._C": None}):
            _, row, _ = self.packet(AcceleratorError())
            self.assertEqual(row["exceptions"][0]["type"], "other")

    def test_missing_source_and_invalid_roots_remain_content_free(self):
        error = self.capture("raise ValueError('PRIVATE')\n")
        with mock.patch.object(errors, "read_source", side_effect=OSError("PRIVATE path")):
            row = errors.decode_worker_exception(errors.worker_exception_packet(error, ("/selected/",)))
        self.assertTrue(row["exceptions"][0]["source_unavailable"])
        self.assertEqual(row["exceptions"][0]["frames"], [])
        row = errors.decode_worker_exception(errors.worker_exception_packet(error, ("relative",)))
        self.assertTrue(row["exceptions"][0]["observer_failed"])
        self.assertNotIn("PRIVATE", json.dumps(row))

    def test_legacy_entry_packet_bytes_and_validation_are_unchanged(self):
        source = "raise ValueError('PRIVATE')\n"
        error = self.capture(source)
        with mock.patch.object(errors, "read_source", return_value=source.encode()):
            raw = errors.exception_packet(error, ("/selected/",))
        expected = (bytes([3, 2, 1]) + (1).to_bytes(4, "big") + hashlib.sha256(source.encode()).digest()
                    + bytes(7 * 36))
        self.assertEqual(raw, expected)
        errors.validate_exception(errors.decode_exception(raw))
        self.assertEqual(errors.exception_packet(None, ()), bytes(291))
        self.assertIsNone(errors.decode_worker_exception(errors.worker_exception_packet(None, ())))
        with self.assertRaises(ValueError):
            errors.decode_exception(errors.worker_exception_packet(error, ()))

    def test_malformed_packets_and_unreachable_nodes_fail_closed(self):
        good, _, _ = self.packet(ValueError())
        mutations = ((0, 1), (1, 2), (2, 5), (3, 0), (3, 255), (4, 8), (5, 9), (6, 4), (7, 4),
                     (len(good) - 1, 1))
        for position, value in mutations:
            raw = bytearray(good)
            raw[position] = value
            with self.subTest(position=position, value=value), self.assertRaises(ValueError):
                errors.decode_worker_exception(bytes(raw))
        for raw in (good[:-1], good + b"x", bytearray(good)):
            with self.assertRaises(ValueError):
                errors.decode_worker_exception(raw)
        raw = bytearray(good)
        raw[2] = 2
        size = errors.WORKER_EXCEPTION_NODE_BYTES
        raw[3 + size:3 + size * 2] = raw[3:3 + size]
        with self.assertRaises(ValueError):
            errors.decode_worker_exception(bytes(raw))

    def test_json_schema_rejects_content_unknown_classes_and_bad_links(self):
        _, row, _ = self.packet(ValueError())
        changes = (lambda r: r.update(private="PRIVATE"), lambda r: r.update(schema="unknown"),
                   lambda r: r.update(truncated=1), lambda r: r.update(exceptions=[]),
                   lambda r: r["exceptions"][0].update(message="PRIVATE"),
                   lambda r: r["exceptions"][0].update(type="PRIVATE"),
                   lambda r: r["exceptions"][0].update(cause=True),
                   lambda r: r["exceptions"][0].update(context=1),
                   lambda r: r["exceptions"][0].update(context=-1),
                   lambda r: r["exceptions"][0].update(frames=[{"source_sha256": "f" * 64, "line": True}]))
        for change in changes:
            altered = copy.deepcopy(row)
            change(altered)
            with self.subTest(change=change), self.assertRaises(ValueError):
                errors.validate_worker_exception(altered)

    def test_host_validates_new_and_historical_worker_rows_using_pinned_source(self):
        _, diagnostic, _ = self.packet(ValueError())
        legacy = errors.decode_exception(errors.exception_packet(ValueError(), ()))
        row = {"schema": "nib.deepseek-lab-result/v2", "status": "BLOCKED", "deployable": False,
               "confidential": False, "functional_pass": False, "stage": "startup", "application": None,
               "hardware": None, "completion": None, "engine_stopped": True,
               "startup": {"outcome": "child-exited", "wait_ms": 1, "exit_code": 1, "health_probes": 1,
                           "checkpoints": {"status": "eof", "last_id": 17}, "exception_diagnostic": None}}
        source = (fixtures.ROOT / "scripts/engine_exception.py").read_bytes()
        contract = {"sources": {"exception.py": hashlib.sha256(source).hexdigest()}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "payload").mkdir()
            path = root / "payload/exception.py"
            path.write_bytes(source)
            for value in (None, legacy, diagnostic):
                row["startup"]["worker_exception_diagnostic"] = value
                fixtures.host.validate_result(row, root, contract, None)
            row["startup"]["worker_exception_diagnostic"] = {**diagnostic, "private": "PRIVATE"}
            with self.assertRaises(ValueError):
                fixtures.host.validate_result(row, root, contract, None)
            row["startup"]["worker_exception_diagnostic"] = diagnostic
            row["startup"].update(outcome="startup-timeout", exit_code=None)
            with self.assertRaises(ValueError):
                fixtures.host.validate_result(row, root, contract, None)
            row["startup"].update(outcome="child-exited", exit_code=1)
            path.write_bytes(source + b"\n")
            with self.assertRaises(ValueError):
                fixtures.host.validate_result(row, root, contract, None)


if __name__ == "__main__":
    unittest.main()
