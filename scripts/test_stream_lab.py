# SPDX-License-Identifier: Apache-2.0
"""Synthetic owner, source-binding and receipt tests; no GPU or attestation."""

import copy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

import stream_lab as binding
import stream_cohort as cohort
import engine_local_correctness as correctness
import engine_tokenizer_parity as parity
import engine_runtime_observation as observation
import test_deepseek_lab as fixtures
import test_engine_tokenizer_parity as parity_fixtures
from test_stream_cohort import Tokenizer, workload, sample, fake_collection, endpoint

ROOT, host, lab = fixtures.ROOT, fixtures.host, fixtures.lab
SHA = "a" * 64


def contract():
    return {"stream_cohort": {"schema": binding.SCHEMA}, "operation": "local-correctness",
            "gpu_family": "B300", "data_parallel_size": 2, "tokenizer_parity": True,
            "tokenizer_materialization": {}, "runtime_observation": True,
            "interpreter": {"sha256": "b" * 64}}


def report(rows=None, *, deadline=120):
    with fake_collection(rows or [sample()] * 9):
        row = cohort.run(endpoint(), workload(), deadline=deadline, gpu_count=8)
    return {"schema": binding.RESULT_SCHEMA, "contract_sha256": SHA,
            "engine_identity_sha256": "c" * 64, "terminal_identity_verified": True, "cohort": row}


class BindingTests(unittest.TestCase):
    def test_closed_loader_uses_exact_sources_not_ambient_same_name_modules(self):
        sources = {"stream/" + name + ".py": (ROOT / "scripts" / (name + ".py")).read_bytes() for name in binding.NAMES}
        pins = {name: hashlib.sha256(raw).hexdigest() for name, raw in sources.items()}
        with mock.patch.dict(sys.modules, {name: types.ModuleType("untrusted") for name in binding.NAMES}):
            loaded = binding.load(sources, pins)
            self.assertEqual(loaded.prepare(Tokenizer(), deadline=time.monotonic() + 2).metadata(), workload().metadata())
            self.assertIs(loaded.stream.local.worker, loaded.stream.identity.worker)
            self.assertIsNot(loaded, cohort)
        for path in sources:
            with self.subTest(path=path), self.assertRaises(ValueError):
                binding.load({**sources, path: sources[path] + b"\n"}, pins)

    def test_profile_requires_all_prerequisites_and_excludes_checker(self):
        binding.enabled(contract())
        for key, value in (("stream_cohort", True), ("operation", "functional"), ("gpu_family", "B200"),
                           ("gpu_security", "nonconfidential"), ("data_parallel_size", True),
                           ("tokenizer_parity", 1), ("runtime_observation", False), ("provider_conformance", {})):
            with self.subTest(key=key), self.assertRaises(ValueError):
                binding.enabled({**contract(), key: value})
        bad = contract()
        del bad["tokenizer_materialization"]
        with self.assertRaises(ValueError): binding.enabled(bad)

    def test_child_binds_current_contract_parity_and_exact_workload_metadata(self):
        value = binding.child(Tokenizer(), parity_fixtures.local(), SHA, cohort, time.monotonic() + 3)
        local, prepared = binding.admit_child(value, SHA, cohort, parity, correctness)
        self.assertEqual(local, parity_fixtures.local())
        self.assertEqual(prepared.metadata(), workload().metadata())
        for mutate in (lambda r: r.update(contract_sha256="b" * 64), lambda r: r.update(secret="canary"),
                       lambda r: r["workload"]["cases"][0].update(index=False),
                       lambda r: r["workload"]["cases"][0].update(prompt_tokens=2047),
                       lambda r: r["workload"]["cases"][0].update(padding=True),
                       lambda r: r["workload"]["cases"][0].update(messages_sha256="c" * 64),
                       lambda r: r["workload"].update(binding_sha256="c" * 64)):
            bad = copy.deepcopy(value)
            mutate(bad)
            with self.assertRaises(ValueError): binding.admit_child(bad, SHA, cohort, parity, correctness)

    def test_complete_partial_unknown_usage_and_deadline_reports_validate(self):
        variants = [(None, 120), ([sample("timeout", known=False)], 120),
                    ([sample(), sample(), sample("protocol-error")], 120), (None, 100), (None, 102)]
        for rows, deadline in variants:
            with self.subTest(deadline=deadline, rows=rows):
                value = report(rows, deadline=deadline)
                self.assertEqual(binding.validate(value, contract(), SHA, cohort), value["cohort"]["completed"])
        value = report()
        value["terminal_identity_verified"] = False
        self.assertFalse(binding.validate(value, contract(), SHA, cohort))
        self.assertEqual(value["cohort"]["known_output_tokens"], 450)

    def test_receipt_tampering_and_forged_success_rejected(self):
        mutations = [lambda r: r.update(contract_sha256="d" * 64), lambda r: r.update(terminal_identity_verified=1),
            lambda r: r["cohort"].update(concurrency=True), lambda r: r["cohort"].update(known_output_tokens=0),
            lambda r: r["cohort"].update(reserved_requests=10), lambda r: r["cohort"].update(confidential=True),
            lambda r: r["cohort"].update(elapsed_seconds=float("nan")),
            lambda r: r["cohort"]["measured"].update(completed_output_tokens_per_second_per_gpu=500),
            lambda r: r["cohort"]["measured"].update(failed=False),
            lambda r: r["cohort"]["samples"][0].update(prompt_tokens=2047),
            lambda r: r["cohort"]["samples"][0].update(completion_tokens=513),
            lambda r: r["cohort"]["samples"][0].update(cached_prompt_tokens=2049),
            lambda r: r["cohort"]["samples"][0].update(ttft_seconds=2),
            lambda r: r["cohort"]["samples"][0].update(output_events=True),
            lambda r: r["cohort"]["samples"][0].update(content="PRIVATE"),
            lambda r: r["cohort"].update(stop_reason="request-failed")]
        for mutate in mutations:
            value = report()
            mutate(value)
            with self.subTest(mutation=mutate), self.assertRaises((ValueError, TypeError)):
                binding.validate(value, contract(), SHA, cohort)

    def test_success_cannot_exceed_request_or_cohort_deadline_but_failed_usage_survives(self):
        # Internally consistent arithmetic must not bless an impossible success.
        for elapsed in (cohort.stream.REQUEST_SECONDS, cohort.stream.REQUEST_SECONDS + 1):
            value = sample(elapsed=elapsed).metadata()
            with self.assertRaises(ValueError): binding.validate_sample(value, cohort)
            value = sample("timeout", elapsed=elapsed).metadata()
            binding.validate_sample(value, cohort)
            self.assertEqual(value["completion_tokens"], 50)
        for elapsed in (cohort.COHORT_SECONDS, cohort.COHORT_SECONDS + 1):
            value = report()
            value["cohort"]["elapsed_seconds"] = elapsed
            with self.assertRaises(ValueError): binding.validate(value, contract(), SHA, cohort)
            value["cohort"].update(completed=False, stop_reason="deadline")
            self.assertFalse(binding.validate(value, contract(), SHA, cohort))
            self.assertEqual(value["cohort"]["known_output_tokens"], 450)
        value = report([sample(), sample("timeout")])
        value["cohort"]["samples"][0]["elapsed_seconds"] = 61
        value["cohort"]["elapsed_seconds"] = 62
        value["cohort"]["measured"] = cohort.meter._summarize_rows(value["cohort"]["samples"],
            scheduled=8, wall_seconds=61, gpu_count=8)
        self.assertFalse(binding.validate(value, contract(), SHA, cohort))

    def test_owned_process_gate_precedes_dispatch_and_terminal_failure_preserves_usage(self):
        engine = mock.Mock(pid=12345)
        engine.poll.return_value = None
        before = {"parent": os.getpid(), "start": 44, "boot": "d" * 64, "namespaces": {"net": 555}, "argv": "e" * 64}
        facts = {"start_ticks": 44, "boot_id_sha256": "d" * 64, "netns_inode": 555, "argv_sha256": "e" * 64}
        for failure in (None, "parent", "initial", "terminal", "reused-pid"):
            with self.subTest(failure=failure):
                observer = types.SimpleNamespace(process=mock.Mock(return_value=before), listener=mock.Mock(return_value=66))
                if failure == "parent": observer.process.return_value = {**before, "parent": os.getpid() + 1}
                if failure == "reused-pid": observer.process.side_effect = [before, {**before, "start": 45}]
                guard = mock.Mock()
                if failure == "initial": guard.check.side_effect = ValueError("PRIVATE")
                if failure == "terminal": guard.check.side_effect = [None, ValueError("PRIVATE")]
                observed = report()["cohort"]
                with mock.patch.object(cohort.stream.identity, "process_facts", return_value=facts), \
                        mock.patch.object(cohort.stream.identity, "Endpoint", return_value=guard) as bind, \
                        mock.patch.object(cohort, "run", return_value=observed) as run:
                    if failure in ("parent", "initial", "reused-pid"):
                        with self.assertRaises(ValueError):
                            binding.run(engine, contract(), SHA, workload(), cohort, observer, time.monotonic() + 5)
                        run.assert_not_called()
                    else:
                        value = binding.run(engine, contract(), SHA, workload(), cohort, observer, time.monotonic() + 5)
                        self.assertEqual(binding.validate(value, contract(), SHA, cohort), failure is None)
                        self.assertEqual(value["cohort"]["known_output_tokens"], 450)
                        self.assertNotIn("PRIVATE", json.dumps(value))
                        self.assertEqual(bind.call_args.args[1]["process"]["exe_sha256"], contract()["interpreter"]["sha256"])


class HarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.PayloadTests.setUpClass()
        cls.fixture = fixtures.PayloadTests
        cls.extra = {**cls.fixture.extra, **{name: (ROOT / name).read_bytes() for name in
            (host.PARITY, host.OBSERVATION, host.STREAM, *("scripts/" + n + ".py" for n in binding.NAMES))}}

    def test_opt_in_payload_binds_all_sources_and_keeps_launch_isolation(self):
        args = (self.fixture.builder, "1" * 40, self.fixture.bundle, self.extra, b"fake", "local-correctness", "B300", "confidential", 2)
        options = {"tokenizer_parity": True, "tokenizer_materialization": True, "runtime_observation": True}
        base_files, base = host.make_payload(*args, **options)
        files, selected = host.make_payload(*args, **options, stream_cohort=True)
        self.assertNotIn("stream_cohort", base)
        self.assertEqual(selected["stream_cohort"], {"schema": binding.SCHEMA})
        binding.load(files, selected["sources"])
        self.assertLessEqual(len(selected["sources"]), 110)
        for key in ("arguments", "environment", "pack_revision", "pack_bytes", "interpreter", "tokenizer_materialization"):
            self.assertEqual(selected[key], base[key])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "payload").mkdir()
            (root / "payload/lab.json").write_bytes(files["lab.json"])
            args = ("name", "owner", root, "/weights")
            self.assertEqual(host.create_command(*args, selected, {"config": {"Env": []}}),
                             host.create_command(*args, base, {"config": {"Env": []}}))

    def test_cli_only_explicit_preparation_with_all_prerequisites(self):
        valid = ["--prepare-local-correctness-b300-dp2", "/not-written", "--stream-cohort",
                 "--tokenizer-parity", "--tokenizer-materialization", "--runtime-observation"]
        for args in (["--run", "/not-read", *valid[2:]], ["--prepare", "/not-written", *valid[2:]],
                     *([item for item in valid if item != missing] for missing in valid[3:]),
                     [*valid, "--provider-conformance-wheels", "/not-read"]):
            with mock.patch.object(host.sys, "argv", ["script", *args]), mock.patch.object(host, "prepare") as prepare:
                with self.assertRaises(ValueError): host.main()
                prepare.assert_not_called()
        with mock.patch.object(host.sys, "argv", ["script", *valid]), mock.patch.object(host, "prepare") as prepare:
            self.assertEqual(host.main(), 0)
            self.assertTrue(prepare.call_args.kwargs["stream_cohort"])

    def test_private_tokenizer_child_checks_sources_before_and_after_count(self):
        selected = {**contract(), "paths": ["/stdlib", "/dyn", "/selected"]}
        tokenizer_module = types.ModuleType("vllm.tokenizers.deepseek_v41")
        tools_module = types.ModuleType("vllm.entrypoints.openai.chat_completion.protocol")
        tokenizer_module.DeepseekV41Tokenizer = mock.Mock()
        tokenizer_module.DeepseekV41Tokenizer.from_pretrained.return_value.apply_chat_template.return_value = [10, 20, 30]
        tools_module.ChatCompletionToolsParam = lambda **item: types.SimpleNamespace(model_dump=lambda: item)
        modules = {tokenizer_module.__name__: tokenizer_module, tools_module.__name__: tools_module, lab.__name__: lab}
        for failed in (False, True):
            state = {"check": mock.Mock()}
            with mock.patch.dict(sys.modules, modules), mock.patch.object(lab, "CORRECTNESS", correctness), \
                    mock.patch.object(lab, "PARITY", parity), mock.patch.object(lab, "STREAM_LAB", binding), \
                    mock.patch.object(lab, "STREAM_COHORT", cohort), mock.patch.object(lab, "read_source", return_value=b"contract"), \
                    mock.patch.object(lab, "recheck_sources") as recheck, mock.patch.object(parity, "verify_sources") as sources, \
                    mock.patch.object(parity, "verify_model") as model, \
                    mock.patch.object(lab, "install_lab", create=True, return_value=state), \
                    mock.patch.object(lab.signal, "signal"), mock.patch.object(lab.signal, "alarm"), \
                    mock.patch.object(lab.os, "dup2") as redirect, \
                    mock.patch.object(cohort, "prepare", side_effect=ValueError("PRIVATE") if failed else None,
                                      return_value=workload()), \
                    mock.patch.object(lab.os, "write", side_effect=lambda fd, data: len(data)) as write:
                if failed:
                    with self.assertRaises(ValueError): lab.local_tokenizer(selected, [None], {})
                    write.assert_not_called()
                else:
                    lab.local_tokenizer(selected, [None], {})
                    fd, raw = write.call_args.args
                    with self.assertRaises(OSError): os.fstat(fd)
                    binding.admit_child(json.loads(raw), hashlib.sha256(b"contract").hexdigest(), cohort, parity, correctness)
                    self.assertEqual((recheck.call_count, sources.call_count, model.call_count), (2, 2, 2))
                    state["check"].assert_called_once()
                    self.assertNotIn("PRIVATE", raw.decode())
                self.assertEqual([call.args[1] for call in redirect.call_args_list], [0, 1, 2])

    def test_owner_requires_fresh_parity_preserves_failed_cohort_and_always_reaps(self):
        @contextmanager
        def selected(*_):
            yield types.SimpleNamespace(deadline=0), (None, types.SimpleNamespace(deadline=0))
        app = types.SimpleNamespace(verify_layout=lambda *_: None, selected_tree=selected,
                                   inspect=lambda *_: {}, apply=lambda *_: {"stages": 5})
        raw_contract = b"contract"
        current_sha = hashlib.sha256(raw_contract).hexdigest()
        for failure in (None, "child", "parity", "stream", "terminal", "after-observation"):
            engine = mock.Mock(pid=99999999)
            engine.poll.return_value, engine.wait.return_value = None, 0
            child = {"schema": binding.CHILD_SCHEMA, "contract_sha256": current_sha,
                     "parity": parity_fixtures.local(), "workload": workload().metadata()}
            if failure == "child": child["contract_sha256"] = SHA
            result = report([sample(), sample("timeout", known=False)] if failure == "stream" else None)
            result["contract_sha256"] = current_sha
            if failure == "terminal": result["terminal_identity_verified"] = False
            def request(*args):
                return b"{}" if failure == "parity" else parity_fixtures.response(*args)
            def observe(engine, selected, row, started):
                if failure == "after-observation" and row["samples"]: raise ValueError("PRIVATE")
                row["samples"].append({"event": "synthetic"})
            with self.subTest(failure=failure), mock.patch.object(lab, "CORRECTNESS", correctness), \
                    mock.patch.object(lab, "PARITY", parity), mock.patch.object(lab, "STREAM_LAB", binding), \
                    mock.patch.object(lab, "STREAM_COHORT", cohort), mock.patch.object(lab, "RUNTIME_OBSERVER", observation), \
                    mock.patch.object(lab, "GPU_PROBE", fixtures.probe), mock.patch.object(parity, "verify_sources"), \
                    mock.patch.object(parity, "verify_model"), \
                    mock.patch.object(lab, "tokenizer_adapter_options", create=True, return_value={}), \
                    mock.patch.object(lab, "check_scratch_mounts"), mock.patch.object(lab, "read_source", return_value=raw_contract), \
                    mock.patch.object(lab, "hardware", return_value={"fixed": True}), \
                    mock.patch.object(lab, "quiet_command", return_value=parity.encoded(child)), \
                    mock.patch.object(lab, "observe_startup"), mock.patch.object(lab, "observe_runtime", side_effect=observe), \
                    mock.patch.object(lab, "bounded_request", side_effect=request), \
                    mock.patch.object(binding, "run", return_value=result) as stream, \
                    mock.patch.object(lab.subprocess, "Popen", return_value=engine) as launch, mock.patch.object(lab.os, "killpg"):
                row = lab.application_and_request({**contract(), "paths": ["/selected"]}, {},
                                                  [None, None, None, app], {"files": []}, {})
            self.assertEqual(row["functional_pass"], failure is None)
            if failure == "child":
                launch.assert_not_called()
                engine.wait.assert_not_called()
                self.assertFalse(row["engine_stopped"])
                self.assertIsNone(row["startup"])
                self.assertEqual(row["stage"], "prelaunch")
            else:
                self.assertTrue(row["engine_stopped"])
                engine.wait.assert_called()
            self.assertEqual(stream.call_count, 0 if failure in ("child", "parity") else 1)
            if stream.called: self.assertEqual(row["stream_cohort"], result)
            self.assertNotIn("PRIVATE", json.dumps(row))

    def test_host_rejects_forged_success_and_changed_source(self):
        files, selected = host.make_payload(self.fixture.builder, "1" * 40, self.fixture.bundle, self.extra,
            b"fake", "local-correctness", "B300", "confidential", 2, tokenizer_parity=True,
            tokenizer_materialization=True, runtime_observation=True, stream_cohort=True)
        current_sha = hashlib.sha256(files["lab.json"]).hexdigest()
        result = report()
        result["contract_sha256"] = current_sha
        row = {"schema": "nib.deepseek-lab-tokenizer-parity/v1", "status": "BLOCKED", "deployable": False,
            "confidential": False, "functional_pass": True, "stage": "complete", "engine_stopped": True,
            "application": {"deployable": False}, "hardware": {"attestation_verified": False}, "completion": None,
            "startup": {"outcome": "ready", "wait_ms": 1, "exit_code": None, "health_probes": 1,
                        "exception_diagnostic": None, "checkpoints": {"status": "eof", "last_id": 17}},
            "tokenizer_parity": parity.run(parity_fixtures.response, parity_fixtures.local(), lab.MODEL, correctness,
                                           deadline=time.monotonic() + 5),
            "tokenizer_materialization": selected["tokenizer_materialization"],
            "runtime_observation": {"samples": []}, "stream_cohort": result}
        original_load = host.load
        def load(name, path, sha):
            if name == "lab_hardware_validation":
                return types.SimpleNamespace(security_profile=lambda _: "confidential", validate_report=lambda *a, **k: None)
            if name in ("lab_application_validation", "lab_runtime_validation"):
                return types.SimpleNamespace(validate=lambda *a, **k: None)
            return original_load(name, path, sha)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for name, raw in files.items():
                path = root / "payload" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            with mock.patch.object(host, "load", side_effect=load):
                host.validate_result(row, root, selected, self.fixture.builder.recorder)
                for mutate in (lambda r: r.update(stream_cohort=None), lambda r: r.update(engine_stopped=False),
                               lambda r: r["stream_cohort"].update(terminal_identity_verified=False),
                               lambda r: r["stream_cohort"]["cohort"].update(known_output_tokens=0)):
                    bad = copy.deepcopy(row)
                    mutate(bad)
                    with self.assertRaises(ValueError): host.validate_result(bad, root, selected, self.fixture.builder.recorder)
                (root / "payload/stream/stream_cohort.py").write_bytes(b"changed")
                with self.assertRaises(ValueError): host.validate_result(row, root, selected, self.fixture.builder.recorder)


if __name__ == "__main__":
    unittest.main()
