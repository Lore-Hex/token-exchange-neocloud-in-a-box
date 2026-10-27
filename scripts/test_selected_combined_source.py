# SPDX-License-Identifier: Apache-2.0
"""Exact closed engine/collector source binding; fakes only, no GPU or sockets."""

import ast
from contextlib import ExitStack
import copy
import json
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

import selected_combined_worker as worker
import test_selected_combined_owner as fixtures
from test_selected_service_process import lab_contract

owner, service, process = fixtures.owner, fixtures.service, fixtures.process
ROOT = Path(__file__).absolute().parents[1]


def launch_plan():
    tree = ast.parse((ROOT / "scripts/prepare-engine-overlay.py").read_bytes())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "launch_plan")
    return ast.literal_eval(next(node.value for node in function.body if isinstance(node, ast.Return)))


def long_contract():
    return {**lab_contract(owner.BASE_PROFILE), "tokenizer_parity": True, "runtime_observation": True,
            "tokenizer_materialization": {}, "paths": ["/synthetic-vllm"]}


class SourceTests(unittest.TestCase):
    def test_closed_combined_engine_exact_transform_and_original_profile_unchanged(self):
        lab = long_contract()
        original = copy.deepcopy(lab)
        tuning = owner.plan.tuning
        run = mock.Mock()
        source = mock.Mock(return_value=json.dumps(launch_plan()).encode())
        namespace = {"require": owner.require, "sys": types.SimpleNamespace(argv=["launcher", "sha", "selected-combined-engine"]),
            "lab_context": mock.Mock(return_value=(lab, None, ["module"], "application", None)),
            "recheck_sources": mock.Mock(), "initialize_cache": mock.Mock(), "run_engine": run,
            "tuning_module": mock.Mock(return_value=tuning), "read_source": source,
            "json": json, "ROOT": "/opt/nib", "lab_args": service.engine_lab.lab_args}
        exec(compile((ROOT / "scripts/selected_service_engine.py").read_bytes(), "adapter", "exec"), namespace)
        with mock.patch.object(tuning, "verify_sources") as pins:
            namespace["selected_engine_entry"]()
        pins.assert_called_once_with(source, "/synthetic-vllm")
        arguments = run.call_args.args[0]["arguments"]
        self.assertEqual(arguments[arguments.index("--max-num-seqs") + 1], "32")
        self.assertEqual(arguments[arguments.index("--max-model-len") + 1], "131072")
        self.assertEqual(arguments[arguments.index("--served-model-name") + 1], service.MODEL)
        graph = json.loads(arguments[arguments.index("--compilation-config") + 1])
        self.assertEqual(graph["cudagraph_capture_sizes"], [1, 2, 4, 8, 16, 32])
        self.assertEqual(lab, original)
        run.reset_mock()
        for fault in ("pinned-source", "argv", "million"):
            changed = copy.deepcopy(original)
            if fault == "argv":
                changed["arguments"][changed["arguments"].index("--kv-cache-dtype") + 1] = "float16"
            if fault == "million":
                changed["execution_profile"] = process.engine_entry.MILLION_PROFILE
            namespace["lab_context"].return_value = (changed, None, ["module"], "application", None)
            with self.subTest(fault=fault), mock.patch.object(tuning, "verify_sources",
                    side_effect=ValueError("PRIVATE_CANARY") if fault == "pinned-source" else None), self.assertRaises(ValueError):
                namespace["selected_engine_entry"]()
            run.assert_not_called()

    def test_worker_rejects_prepared_imported_source_lab_mount_or_profile_substitution(self):
        root, payload = process.ROOT, process.PAYLOAD
        own_path = root / "scripts/selected_combined_worker.py"
        imported = types.SimpleNamespace(__file__=str(root / "scripts/imported.py"))
        lab = long_contract()
        lab_raw = json.dumps(lab).encode()
        files = {"scripts/selected_combined_worker.py": b"synthetic worker", "scripts/imported.py": b"synthetic imported closure"}
        preparation = {"schema": "nib.selected-service-prepared/v1", "lab_sha256": process.digest(lab_raw),
                       "files": {name: process.digest(raw) for name, raw in files.items()}}
        prepared = json.dumps(preparation).encode()
        source = {str(root / name): raw for name, raw in files.items()}
        source.update({str(root / "prepared.json"): prepared, str(payload / "lab.json"): lab_raw})
        for fault in (None, "prepared", "imported", "lab", "mount", "source-pin", "expired"):
            actual = dict(source)
            if fault in ("prepared", "imported", "lab"):
                name = {"prepared": str(root / "prepared.json"), "imported": imported.__file__,
                        "lab": str(payload / "lab.json")}[fault]
                actual[name] += b"other bytes"
            with self.subTest(fault=fault), ExitStack() as stack:
                stack.enter_context(mock.patch.object(worker, "__file__", str(own_path)))
                stack.enter_context(mock.patch.object(sys, "version_info", (3, 12, 3)))
                stack.enter_context(mock.patch.dict(sys.modules, {"synthetic_combined_import": imported}))
                stack.enter_context(mock.patch.object(process.bootstrap, "read_source", side_effect=lambda name: actual[name]))
                stack.enter_context(mock.patch.object(worker.os, "statvfs",
                    return_value=types.SimpleNamespace(f_flag=0 if fault == "mount" else worker.os.ST_RDONLY)))
                stack.enter_context(mock.patch.object(worker.capacity, "verify_sources",
                    side_effect=ValueError("PRIVATE_CANARY") if fault == "source-pin" else None))
                if fault is None:
                    worker.authenticate(process.digest(prepared), worker.time.monotonic() + 10)
                else:
                    with self.assertRaises(ValueError):
                        worker.authenticate(process.digest(prepared), worker.time.monotonic() + (-1 if fault == "expired" else 10))

    def test_preparation_child_exact_budget_and_failure_no_engine(self):
        for fault in (None, "cancel", "bad-source", "bad-workload", "late"):
            with self.subTest(fault=fault), fixtures.OwnerTests().fixture() as (supervisor, run, fake, clock, _):
                concrete = supervisor.owner
                concrete.prepared.interpreter = "/usr/bin/python3.12"
                concrete.prepared.lab["environment"] = {"PYTHONNOUSERSITE": "1"}
                raw = fixtures.receipt.encoded({"prepared_sha256": "b" * 64 if fault == "bad-source" else fixtures.SHA,
                    "workload": {} if fault == "bad-workload" else fixtures.workload().metadata()})
                def child(command, **kwargs):
                    self.assertEqual(command, ["/usr/bin/python3.12", "-I", "-S", "-B",
                        str(process.ROOT / "scripts/selected_combined_worker.py"), fixtures.SHA, "prepare"])
                    self.assertEqual(kwargs["timeout"], fixtures.scaling.cohort.PREPARE_SECONDS)
                    if fault == "cancel": raise KeyboardInterrupt()
                    if fault == "late": clock.advance(301)
                    return types.SimpleNamespace(returncode=0, stdout=raw, stderr=b"")
                with mock.patch.object(concrete.prepared, "check"), \
                        mock.patch.object(concrete, "verify_weights") as weights, \
                        mock.patch.object(process.snapshot, "execute_process_group", side_effect=child):
                    if fault is None:
                        run.prepare(concrete, supervisor.contract)
                        self.assertEqual(run.workload, fixtures.workload())
                    else:
                        with self.assertRaises((ValueError, KeyboardInterrupt, service.identity.worker.Abort)):
                            run.prepare(concrete, supervisor.contract)
                    weights.assert_called_once()
                self.assertFalse(fake.children)


if __name__ == "__main__":
    unittest.main()
