# SPDX-License-Identifier: Apache-2.0
"""Synthetic boundary tests only; no model, GPU, Docker or upstream execution."""

import copy
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import signal
import tempfile
import time
import types
import sys
import unittest
from unittest import mock

import engine_tokenizer_parity as parity
import engine_local_correctness as correctness
import test_deepseek_lab as fixtures

lab, host, ROOT = fixtures.lab, fixtures.host, fixtures.ROOT
CANARY = "private-response-canary"
TOKEN_IDS = [10, 20, 30]


def local():
    tokenizer = mock.Mock()
    tokenizer.apply_chat_template.return_value = TOKEN_IDS
    tool_type = lambda **item: types.SimpleNamespace(model_dump=lambda: item)
    return parity.local_measure(tokenizer, tool_type, correctness)


def response(path, payload, deadline):
    if path == "/tokenize":
        return parity.encoded({"count": len(TOKEN_IDS), "max_model_len": 4096,
                               "tokens": TOKEN_IDS, "token_strs": None})
    return parity.encoded({"object": "chat.completion", "model": lab.MODEL,
        "choices": [{"index": 0, "finish_reason": "length", "message": {"role": "assistant", "content": CANARY}}],
        "usage": {"prompt_tokens": len(TOKEN_IDS), "completion_tokens": 1, "total_tokens": len(TOKEN_IDS) + 1}})


class ParityTests(unittest.TestCase):
    def run_probe(self, request=response, **kwargs):
        return parity.run(request, local(), lab.MODEL, correctness, deadline=time.monotonic() + 5, **kwargs)

    def test_four_probes_eight_requests_no_content_and_explicit_nonqualification(self):
        request = mock.Mock(side_effect=response)
        row = self.run_probe(request)
        self.assertTrue(row["all_cases_pass"])
        self.assertEqual(row["requests_started"], 8)
        self.assertEqual([call.args[0] for call in request.call_args_list], ["/tokenize", "/v1/chat/completions"] * 4)
        raw = parity.encoded(row).decode()
        for text in (CANARY, "Reply with", "CEDAR", "parity_lookup", '"tokens":'):
            self.assertNotIn(text, raw)
        for key in ("quality_accepted", "confidential", "tau_acceptance"):
            self.assertFalse(row[key])
        for first, second in zip(request.call_args_list[::2], request.call_args_list[1::2]):
            tokenize, generate = first.args[1], second.args[1]
            self.assertEqual(tokenize["messages"], generate["messages"])
            self.assertEqual(tokenize.get("tools"), generate.get("tools"))
            self.assertEqual(tokenize["chat_template_kwargs"], parity.TEMPLATE)
            self.assertEqual(generate["max_completion_tokens"], 1)
            self.assertFalse(tokenize["add_special_tokens"])
            self.assertFalse(tokenize["return_token_strs"])
            self.assertLessEqual(first.args[2] - time.monotonic(), parity.REQUEST_SECONDS)

    def test_local_invocation_uses_pinned_tool_normalizer_and_explicit_framing(self):
        tokenizer, tool = mock.Mock(), mock.Mock()
        tokenizer.apply_chat_template.return_value = TOKEN_IDS
        tool.return_value.model_dump.return_value = {"normalized": True}
        report = parity.local_measure(tokenizer, tool, correctness)
        parity.validate_local(report, correctness)
        self.assertEqual(tokenizer.apply_chat_template.call_count, 4)
        self.assertEqual(tool.call_count, 2)
        for index, call in enumerate(tokenizer.apply_chat_template.call_args_list):
            self.assertEqual(call.kwargs, {"tools": [{"normalized": True}] if index >= 2 else None,
                "tokenize": True, "add_generation_prompt": True, "continue_final_message": False, **parity.TEMPLATE})
        self.assertNotIn("tokens", report["cases"][0])

    def test_same_length_different_token_sequence_fails_before_generation(self):
        request = mock.Mock(return_value=parity.encoded({"count": 3, "max_model_len": 4096,
                                                       "tokens": [10, 20, 31], "token_strs": None}))
        row = self.run_probe(request)
        self.assertEqual(row["cases"][0]["failure"], "token-ids")
        self.assertFalse(row["all_cases_pass"])
        self.assertEqual(request.call_count, 1)
        self.assertTrue(all(item["result"] == "not_run" for item in row["cases"][1:]))

    def test_generated_prompt_usage_mismatch_is_not_parity(self):
        def mismatch(path, payload, deadline):
            value = json.loads(response(path, payload, deadline))
            if path != "/tokenize":
                value["usage"].update(prompt_tokens=4, total_tokens=5)
            return parity.encoded(value)
        row = self.run_probe(mismatch)
        self.assertEqual(row["cases"][0]["failure"], "usage")
        self.assertEqual(row["requests_started"], 2)
        self.assertFalse(row["all_cases_pass"])

    def test_bad_tokenize_shapes_counts_and_overflows_fail_closed(self):
        good = json.loads(response("/tokenize", {}, 0))
        for changes in ({"tokens": []}, {"tokens": [True]}, {"tokens": [-1]}, {"tokens": [2**31]},
                        {"tokens": [1] * 1025}, {"tokens": [1.0]}, {"count": True}, {"count": 4},
                        {"max_model_len": 8192}, {"token_strs": [CANARY]}, {"prompt": CANARY}):
            with self.subTest(changes=list(changes)):
                row = self.run_probe(lambda *args: parity.encoded({**good, **changes}))
                self.assertEqual(row["cases"][0]["failure"], "protocol")
                self.assertEqual(row["requests_started"], 1)
                self.assertNotIn(CANARY, parity.encoded(row).decode())

    def test_duplicate_and_nonfinite_response_rejected(self):
        for raw in (b'{"count":3,"count":3}', b'{"tokens":[NaN]}', b'private', b'[]'):
            with self.subTest(raw=raw):
                row = self.run_probe(lambda *args: raw)
                self.assertFalse(row["all_cases_pass"])
                self.assertEqual(row["cases"][0]["failure"], "protocol")

    def test_local_receipt_binding_order_and_content_are_strict(self):
        good = local()
        changes = [{**good, "binding_sha256": "a" * 64}, {**good, "prompt": CANARY}]
        for key, value in (("count", True), ("tokens_sha256", CANARY), ("id", "other"), ("content", CANARY)):
            changed = copy.deepcopy(good)
            changed["cases"][0][key] = value
            changes.append(changed)
        changes.append({**good, "cases": list(reversed(good["cases"]))})
        for changed in changes:
            with self.assertRaises(ValueError):
                parity.validate_local(changed, correctness)

    def test_timeouts_cancel_and_transport_errors_stop_without_content(self):
        for error, category in ((TimeoutError(CANARY), "timeout"), (InterruptedError(CANARY), "cancelled"),
                                (OSError(CANARY), "transport"), (ValueError(CANARY), "protocol")):
            request = mock.Mock(side_effect=error)
            row = self.run_probe(request)
            self.assertEqual(row["cases"][0]["failure"], category)
            self.assertEqual(request.call_count, 1)
            self.assertNotIn(CANARY, parity.encoded(row).decode())

    def test_late_response_and_expired_budget_never_pass(self):
        now = [0.0]
        def late(*args):
            now[0] = args[2]
            return response(*args)
        row = parity.run(late, local(), lab.MODEL, correctness, deadline=5, clock=lambda: now[0])
        self.assertEqual(row["cases"][0]["failure"], "timeout")
        request = mock.Mock()
        row = parity.run(request, local(), lab.MODEL, correctness, deadline=0, clock=lambda: 1)
        request.assert_not_called()
        self.assertFalse(row["all_cases_pass"])

    def test_report_tamper_cannot_add_content_or_false_success(self):
        good = self.run_probe()
        for key, value in (("quality_accepted", True), ("confidential", True), ("requests_started", 7),
                           ("private", CANARY), ("binding_sha256", "a" * 64)):
            with self.assertRaises(ValueError):
                parity.validate({**good, key: value}, correctness)
        for key, value in (("local", {"count": 4, "tokens_sha256": "a" * 64}), ("requests", 1),
                           ("usage", None), ("content", CANARY), ("failure", "token-ids")):
            bad = copy.deepcopy(good)
            bad["cases"][0][key] = value
            with self.assertRaises(ValueError):
                parity.validate(bad, correctness)

    def test_pack_tokenizer_pins_match_committed_manifest(self):
        rows = {line.split()[2]: (int(line.split()[1]), line.split()[0])
                for line in (ROOT / host.PACK / "files.sha256").read_text().splitlines() if line.strip()}
        self.assertEqual({name: rows[name] for name in parity.MODEL_FILES}, parity.MODEL_FILES)

    def test_source_and_model_drift_fail_before_any_tokenization(self):
        for call in (lambda: parity.verify_sources(lambda *args: b"wrong", "/selected", correctness),
                     lambda: parity.verify_model(lambda *args: b"wrong")):
            with self.assertRaises(ValueError):
                call()

    def test_only_explicit_b300_cc_correctness_profile(self):
        good = {"tokenizer_parity": True, "operation": "local-correctness", "gpu_family": "B300"}
        self.assertTrue(parity.enabled(good))
        for key, value in (("tokenizer_parity", False), ("tokenizer_parity", 1), ("operation", "pre-serve"),
                           ("gpu_family", "B200"), ("gpu_security", "nonconfidential")):
            with self.assertRaises(ValueError):
                parity.enabled({**good, key: value})


class HarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.PayloadTests.setUpClass()
        cls.fixture = fixtures.PayloadTests
        cls.extra = {**cls.fixture.extra, host.PARITY: (ROOT / host.PARITY).read_bytes()}

    def test_opt_in_source_binding_keeps_argv_environment_and_container_bounds(self):
        args = (self.fixture.builder, "1" * 40, self.fixture.bundle, self.extra, b"fake", "local-correctness", "B300")
        normal_files, normal = host.make_payload(*args)
        files, contract = host.make_payload(*args, tokenizer_parity=True)
        self.assertNotIn("tokenizer_parity", normal)
        self.assertNotIn("tokenizer-parity.py", normal_files)
        self.assertIs(contract["tokenizer_parity"], True)
        self.assertEqual(contract["sources"]["tokenizer-parity.py"], hashlib.sha256(files["tokenizer-parity.py"]).hexdigest())
        for key in ("arguments", "environment", "pack_revision", "manifest_sha256", "pack_bytes"):
            self.assertEqual(contract[key], normal[key])
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp).resolve()
            (bundle / "payload").mkdir()
            (bundle / "payload/lab.json").write_bytes(files["lab.json"])
            command_args = ("name", "owner", bundle, "/weights")
            self.assertEqual(host.create_command(*command_args, contract, {"config": {"Env": []}}),
                             host.create_command(*command_args, normal, {"config": {"Env": []}}))
        with self.assertRaises(ValueError):
            host.make_payload(*args, tokenizer_parity=1)

    def test_existing_lifecycle_runs_parity_not_correctness_and_always_reaps(self):
        @contextmanager
        def selected(*args):
            yield types.SimpleNamespace(deadline=0), (None, types.SimpleNamespace(deadline=0))
        app = types.SimpleNamespace(verify_layout=lambda *args: None, selected_tree=selected,
                                   inspect=lambda *args: {}, apply=lambda *args: {"stages": 5})
        contract = {"operation": "local-correctness", "gpu_family": "B300", "tokenizer_parity": True,
                    "paths": ["/selected"]}
        for failure in (None, "sources", "child", "usage"):
            engine = mock.Mock(pid=99999999)
            engine.poll.return_value = None
            engine.wait.return_value = 0
            def command(argv, seconds, **kwargs):
                if argv[-1] == "tokenizer-parity":
                    if failure == "child":
                        raise TimeoutError(CANARY)
                    return parity.encoded(local())
                return b""
            def http(path, **kwargs):
                return b"ok" if path == "/health" else json.dumps({"data": [{"id": lab.MODEL}]}).encode()
            def request(*args):
                raw = response(*args)
                if failure == "usage" and args[0] == "/v1/chat/completions":
                    row = json.loads(raw)
                    row["usage"].update(prompt_tokens=4, total_tokens=5)
                    return parity.encoded(row)
                return raw
            with mock.patch.object(lab, "CORRECTNESS", correctness), mock.patch.object(lab, "PARITY", parity), \
                    mock.patch.object(correctness, "verify_sources"), \
                    mock.patch.object(parity, "verify_sources", side_effect=ValueError() if failure == "sources" else None), \
                    mock.patch.object(lab, "check_scratch_mounts"), \
                    mock.patch.object(lab, "GPU_PROBE", fixtures.probe), \
                    mock.patch.object(lab, "hardware", return_value={"fixed": True}), \
                    mock.patch.object(lab, "quiet_command", side_effect=command), \
                    mock.patch.object(lab, "http_request", side_effect=http), \
                    mock.patch.object(lab, "bounded_request", side_effect=request) as requested, \
                    mock.patch.object(lab, "correctness_request") as unchanged, \
                    mock.patch.object(lab.subprocess, "Popen", return_value=engine) as start, \
                    mock.patch.object(lab.os, "killpg"):
                row = lab.application_and_request(contract, {}, [None, None, None, app], {"files": []}, {})
            self.assertEqual(row["functional_pass"], failure is None)
            self.assertEqual(start.call_count, int(failure != "sources"))
            self.assertEqual(row["engine_stopped"], failure != "sources")
            self.assertNotIn("correctness", row)
            self.assertIsNone(row["completion"])
            self.assertNotIn(CANARY, json.dumps(row))
            unchanged.assert_not_called()
            self.assertEqual(requested.call_count, 8 if failure is None else 2 if failure == "usage" else 0)
            if failure != "sources":
                engine.wait.assert_called()

    def test_host_validates_source_bound_parity_schema_and_rejects_false_success(self):
        files, contract = host.make_payload(self.fixture.builder, "1" * 40, self.fixture.bundle,
                self.extra, b"fake", "local-correctness", "B300", tokenizer_parity=True)
        row = {"schema": "nib.deepseek-lab-tokenizer-parity/v1", "status": "BLOCKED",
               "deployable": False, "confidential": False, "functional_pass": True, "stage": "complete",
               "application": {"deployable": False}, "hardware": {"attestation_verified": False},
               "completion": None, "engine_stopped": True,
               "startup": {"outcome": "ready", "wait_ms": 1, "exit_code": None, "health_probes": 1,
                           "exception_diagnostic": None, "checkpoints": {"status": "eof", "last_id": 17}},
               "tokenizer_parity": parity.run(response, local(), lab.MODEL, correctness,
                                             deadline=time.monotonic() + 5)}
        real_load = host.load
        def load(name, path, sha):
            if name == "lab_hardware_validation":
                return types.SimpleNamespace(security_profile=lambda _: "confidential", validate_report=lambda *a, **k: None)
            if name == "lab_application_validation":
                return types.SimpleNamespace(validate=lambda *a: None)
            return real_load(name, path, sha)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            for name, data in files.items():
                path = root / "payload" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            # Only existing hardware/application receipt validation is synthetic;
            # the new host validator loads both comparison helpers by exact hash.
            with mock.patch.object(host, "load", side_effect=load):
                host.validate_result(row, root, contract, self.fixture.builder.recorder)
                for changes in ({"schema": "nib.deepseek-lab-correctness-result/v2"},
                                {"correctness": row["tokenizer_parity"]}, {"tokenizer_parity": None},
                                {"engine_stopped": False}, {"completion": {"private": CANARY}}):
                    with self.assertRaises(ValueError):
                        host.validate_result({**row, **changes}, root, contract, self.fixture.builder.recorder)
                failed = parity.run(lambda *_: b"{}", local(), lab.MODEL, correctness, deadline=time.monotonic() + 5)
                with self.assertRaises(ValueError):
                    host.validate_result({**row, "tokenizer_parity": failed}, root, contract, self.fixture.builder.recorder)
                host.validate_result({**row, "tokenizer_parity": failed, "functional_pass": False},
                                     root, contract, self.fixture.builder.recorder)
                (root / "payload/tokenizer-parity.py").write_bytes(b"changed")
                with self.assertRaises(ValueError):
                    host.validate_result(row, root, contract, self.fixture.builder.recorder)

    def test_tokenize_uses_same_whole_request_deadline(self):
        with mock.patch.object(lab.time, "monotonic", side_effect=[100.0, 101.0]), \
                mock.patch.object(lab.signal, "getsignal", return_value="outer"), \
                mock.patch.object(lab.signal, "getitimer", return_value=(30.0, 0.0)), \
                mock.patch.object(lab.signal, "signal"), mock.patch.object(lab.signal, "setitimer") as timer, \
                mock.patch.object(lab, "http_request", return_value=b"ok") as call:
            self.assertEqual(lab.bounded_request("/tokenize", {}, 105.0), b"ok")
        call.assert_called_once_with("/tokenize", data={}, timeout=5.0)
        self.assertEqual(timer.call_args_list[-1], mock.call(signal.ITIMER_REAL, 29.0))
        with self.assertRaises(ValueError):
            lab.bounded_request("http://outside", {}, time.monotonic() + 1)

    def test_local_child_pins_inputs_uses_sealed_bootstrap_and_closes_output_on_failure(self):
        contract = {"operation": "local-correctness", "gpu_family": "B300", "tokenizer_parity": True,
                    "paths": ["/stdlib", "/dyn", "/selected"]}
        tokenizer_module = types.ModuleType("vllm.tokenizers.deepseek_v41")
        tools_module = types.ModuleType("vllm.entrypoints.openai.chat_completion.protocol")
        tokenizer_module.DeepseekV41Tokenizer = mock.Mock()
        tokenizer_module.DeepseekV41Tokenizer.from_pretrained.return_value.apply_chat_template.return_value = TOKEN_IDS
        tools_module.ChatCompletionToolsParam = lambda **item: types.SimpleNamespace(model_dump=lambda: item)
        modules = {tokenizer_module.__name__: tokenizer_module, tools_module.__name__: tools_module, lab.__name__: lab}
        for fail in (False, True):
            tokenizer_module.DeepseekV41Tokenizer.from_pretrained.side_effect = ValueError(CANARY) if fail else None
            state = {"check": mock.Mock()}
            with mock.patch.dict(sys.modules, modules), \
                    mock.patch.object(lab, "CORRECTNESS", correctness), mock.patch.object(lab, "PARITY", parity), \
                    mock.patch.object(lab, "recheck_sources") as recheck, \
                    mock.patch.object(parity, "verify_sources") as sources, \
                    mock.patch.object(parity, "verify_model") as model, \
                    mock.patch.object(lab, "install_lab", create=True, return_value=state) as bootstrap, \
                    mock.patch.object(lab.signal, "signal"), mock.patch.object(lab.signal, "alarm"), \
                    mock.patch.object(lab.os, "dup2") as redirect, \
                    mock.patch.object(lab.os, "write", side_effect=lambda fd, data: len(data)) as write:
                if fail:
                    with self.assertRaises(ValueError):
                        lab.local_tokenizer(contract, [None], {})
                    write.assert_not_called()
                else:
                    lab.local_tokenizer(contract, [None], {})
                    fd, raw = write.call_args.args
                    with self.assertRaises(OSError):
                        fixtures.os.fstat(fd)
                    parity.validate_local(json.loads(raw), correctness)
                    state["check"].assert_called_once()
                self.assertEqual([call.args[1] for call in redirect.call_args_list], [0, 1, 2])
                recheck.assert_called_once()
                sources.assert_called_once_with(lab.read_source, "/selected", correctness)
                self.assertEqual(model.call_count, 1 if fail else 2)
                bootstrap.assert_called_once()
                tokenizer_module.DeepseekV41Tokenizer.from_pretrained.assert_called_with(
                    "/model", local_files_only=True, trust_remote_code=False)

    def test_preparation_flag_rejects_other_modes_and_run_override(self):
        commands = (["--prepare", "/not-written", "--tokenizer-parity"],
                    ["--prepare-pre-serve-b300", "/not-written", "--tokenizer-parity"],
                    ["--prepare-nonconfidential-baseline-b300", "/not-written", "--tokenizer-parity"],
                    ["--run", "/not-read", "--tokenizer-parity"])
        for args in commands:
            with mock.patch.object(host.sys, "argv", ["script", *args]), mock.patch.object(host, "prepare") as prep:
                with self.assertRaises(ValueError):
                    host.main()
                prep.assert_not_called()
        with mock.patch.object(host.sys, "argv", ["script", "--prepare-local-correctness-b300-dp2",
                                                  "/not-written", "--tokenizer-parity"]), \
                mock.patch.object(host, "prepare") as prep:
            self.assertEqual(host.main(), 0)
            prep.assert_called_once_with("/not-written", "local-correctness", "B300", "confidential", 2,
                                         tokenizer_parity=True)


if __name__ == "__main__":
    unittest.main()
