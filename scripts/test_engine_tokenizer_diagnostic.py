# SPDX-License-Identifier: Apache-2.0
"""Synthetic controls and source binding; these are not runtime evidence."""

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import types
import unittest
from unittest import mock

import engine_tokenizer_diagnostic as diagnostic
import engine_tokenizer_parity as parity
import test_engine_tokenizer_parity as parity_fixtures
import test_engine_cpu_entry as fixtures
import yaml


class LazyCalls:
    def __init__(self, calls):
        self.items = iter(calls)

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.items)

    def __deepcopy__(self, memo):
        raise TypeError("PRIVATE exception canary")


def request_type(**probe):
    messages = probe["messages"]
    for message in messages:
        if "tool_calls" in message:
            message["tool_calls"] = LazyCalls(message["tool_calls"])
    return types.SimpleNamespace(messages=messages)


def parse(messages, config, *, content_format):
    assert content_format == "openai" and config.enable_prompt_embeds is False
    result = []
    for message in messages:
        row = dict(message)
        if "tool_calls" in row:
            row["tool_calls"] = list(row["tool_calls"])
            for call in row["tool_calls"]:
                function = call["function"]
                if type(function["arguments"]) is str:
                    function["arguments"] = json.loads(function["arguments"])
        result.append(row)
    return result, None, None


async def parse_async(*args, **kwargs):
    return parse(*args, **kwargs)


def encode(messages, *, thinking_mode):
    assert thinking_mode == "chat"
    messages = copy.deepcopy(messages)
    for message in messages:
        for call in message.get("tool_calls", []):
            if type(call["function"]["arguments"]) is str:
                call["function"]["arguments"] = json.loads(call["function"]["arguments"])
    return json.dumps(messages, sort_keys=True)


class DiagnosticTests(unittest.TestCase):
    def exercise(self, **overrides):
        args = dict(probes=parity.probes(), request_type=request_type, normalize=copy.deepcopy,
                    encode=encode, parse=parse, parse_async=parse_async,
                    checkpoints=mock.Mock(), check=mock.Mock(),
                    chat_request_type=lambda **p: types.SimpleNamespace(messages=p["messages"]))
        args.update(overrides)
        diagnostic.exercise(**args)

    def test_actual_error_propagates_after_all_controls_without_probe_mutation(self):
        probes = parity.probes()
        before = copy.deepcopy(probes)
        checkpoints, check = mock.Mock(), mock.Mock()
        with self.assertRaises(TypeError):
            self.exercise(probes=probes, checkpoints=checkpoints, check=check)
        self.assertEqual(checkpoints.emit.call_args_list, [mock.call(i) for i in (12, 13, 14, 15)])
        check.assert_called_once_with()
        self.assertEqual(probes, before)

    def test_list_instead_of_lazy_iterator_stops_before_attribution(self):
        checkpoints = mock.Mock()
        with self.assertRaises(ValueError):
            self.exercise(request_type=lambda **p: types.SimpleNamespace(messages=p["messages"]),
                          checkpoints=checkpoints)
        self.assertEqual(checkpoints.emit.call_args_list, [mock.call(12)])

    def test_unexpected_plain_or_dict_failure_stops_before_parser(self):
        for changes in ({"normalize": mock.Mock(side_effect=TypeError("PRIVATE"))},
                        {"encode": mock.Mock(side_effect=["a", "b"])}):
            parser = mock.Mock()
            with self.assertRaises((TypeError, ValueError)):
                self.exercise(parse=parser, **changes)
            parser.assert_not_called()

    def test_media_and_sync_async_disagreement_fail_closed(self):
        for change in (lambda p: (p[0], {}, None), lambda p: ([], p[1], p[2])):
            async def wrong(*args, **kwargs):
                return change(parse(*args, **kwargs))
            checkpoints = mock.Mock()
            with self.assertRaises(ValueError):
                self.exercise(parse_async=wrong, checkpoints=checkpoints)
            self.assertNotIn(mock.call(14), checkpoints.emit.call_args_list)

    def test_missing_tool_call_never_becomes_successful_control(self):
        def empty(*args, **kwargs):
            rows, data, ids = parse(*args, **kwargs)
            for row in rows:
                if "tool_calls" in row:
                    row["tool_calls"] = []
            return rows, data, ids
        async def async_empty(*args, **kwargs):
            return empty(*args, **kwargs)
        with self.assertRaises(ValueError):
            self.exercise(parse=empty, parse_async=async_empty)

    def test_state_check_failure_is_not_hidden_by_expected_failure(self):
        checkpoints = mock.Mock()
        with self.assertRaises(RuntimeError):
            self.exercise(check=mock.Mock(side_effect=RuntimeError("PRIVATE")), checkpoints=checkpoints)
        self.assertEqual(checkpoints.emit.call_args_list[-1], mock.call(14))

    def test_source_pin_rejection_precedes_import(self):
        with self.assertRaises(ValueError):
            diagnostic.verify_sources(lambda path: b"substituted", "/selected")

    def test_payloads_equal_original_parity_requests_byte_for_byte(self):
        request = mock.Mock(side_effect=parity_fixtures.response)
        parity_fixtures.ParityTests().run_probe(request)
        original = [call.args[1] for call in request.call_args_list[::2]]
        self.assertEqual(parity.encoded(diagnostic.tokenize_payloads(parity, fixtures.lab.MODEL)),
                         parity.encoded(original))

    def test_chat_request_control_must_materialize_tool_calls(self):
        checkpoints = mock.Mock()
        with self.assertRaises(ValueError):
            self.exercise(chat_request_type=request_type, checkpoints=checkpoints)
        self.assertEqual(checkpoints.emit.call_args_list, [mock.call(12)])

    def test_exception_packet_never_formats_or_retains_content(self):
        try:
            self.exercise()
        except TypeError as error:
            with mock.patch.object(fixtures.cpu, "read_source", side_effect=lambda p, *args: Path(p).read_bytes(), create=True):
                result = fixtures.cpu.decode_exception(fixtures.cpu.exception_packet(
                    error, (str(Path(__file__).parent) + "/",)))
        self.assertEqual(result["type"], "type")
        raw = json.dumps(result)
        for forbidden in ("PRIVATE", "CEDAR", "parity_lookup", "tool_calls", "messages"):
            self.assertNotIn(forbidden, raw)


class BindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ContractTests.setUpClass()
        cls.fixture = fixtures.ContractTests
        cls.tool = fixtures.tool
        cls.extra = {**cls.fixture.extra,
                     **{p: (fixtures.ROOT / p).read_bytes() for p in cls.tool.TOKENIZER_FILES}}

    def test_opt_in_binds_sources_and_preserves_resources_and_default_payload(self):
        normal, before = self.tool.prepare_files("1" * 40, self.fixture.bundle, self.fixture.extra)
        files, contract = self.tool.prepare_files("1" * 40, self.fixture.bundle, self.extra, True)
        self.assertEqual((normal, before), (self.fixture.files, self.fixture.contract))
        self.assertNotIn("diagnostic", before)
        self.assertNotIn("tokenizer-diagnostic.py", normal)
        self.assertEqual(contract["diagnostic"], diagnostic.MODE)
        for path in ("tokenizer-diagnostic.py", "tokenizer-parity.py"):
            self.assertEqual(hashlib.sha256(files[path]).hexdigest(), contract["sources"][path])
        for field in ("arguments", "environment", "paths", "base_image", "interpreter"):
            self.assertEqual(before[field], contract[field])
        config = json.loads((fixtures.ROOT / "bench/engine-cpu-entry/oci/config.json").read_bytes())
        command = self.tool.create_command("n", "o", "/payload", "a" * 64, config, contract)
        self.assertEqual(command, self.tool.create_command("n", "o", "/payload", "a" * 64, config, before))
        self.assertNotIn("--gpus", command)
        self.assertIn("none", command)

    def test_report_mode_is_closed_and_bound_to_invocation(self):
        testcase = self.fixture()
        report = testcase.valid_report()
        report["diagnostic"] = diagnostic.MODE
        self.assertTrue(testcase.validate(report))
        report["diagnostic"] = "PRIVATE"
        with self.assertRaises(ValueError):
            testcase.validate(report)
        envelope = {"provenance": {"payload_sha256": "a" * 64}, "diagnostic": diagnostic.MODE}
        record = self.tool.evidence_record("1" * 40, "unit", "2026-10-22T00:00:00Z", envelope)
        self.assertTrue(record["command"].endswith(" --tokenizer-continuation"))
        self.assertIn("no BPE, renderer or HTTP execution", record["notes"])
        self.assertEqual(record["result"], "fail")

    def test_workflow_boolean_forwards_identical_default_argv_and_binds_validation(self):
        workflow = yaml.load((fixtures.ROOT / ".github/workflows/engine-cpu-entry.yml").read_text(),
                             Loader=yaml.BaseLoader)
        job = workflow["jobs"]["diagnostic"]
        self.assertEqual(job["env"]["TOKENIZER_CONTINUATION"], "${{ inputs.tokenizer_continuation }}")
        for step, action, argument in ((job["steps"][4], "--slug", "unit"),
                                       (job["steps"][6], "--check-result", "evidence/p2/*-unit")):
            for setting in ("false", "true", "", "TRUE", "true; false"):
                stub = 'docker() { return 0; }\npython3() { printf "%s\\n" "$@"; }\n'
                result = subprocess.run(["bash", "-c", stub + step["run"]],
                    cwd=fixtures.ROOT, env={**os.environ, "SLUG": "unit", "TOKENIZER_CONTINUATION": setting,
                                           "TOKENIZER_MATERIALIZATION": "false"},
                    capture_output=True, timeout=10)
                if setting in ("false", "true"):
                    self.assertEqual(result.returncode, 0)
                    expected = ["-I", "-S", "scripts/record-engine-cpu-entry.py", action, argument,
                                "--host-kind", "github-actions"]
                    self.assertEqual(result.stdout.decode().splitlines(), expected +
                                     (["--tokenizer-continuation"] if setting == "true" else []))
                else:
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stdout, b"")


if __name__ == "__main__":
    unittest.main()
