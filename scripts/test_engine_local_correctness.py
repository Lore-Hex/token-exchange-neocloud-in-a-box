# SPDX-License-Identifier: Apache-2.0
"""Synthetic protocol checks only; no model, network, Docker, or GPU."""

import copy
import hashlib
import json
from pathlib import Path
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent
smoke = types.ModuleType("local_correctness")
exec(compile((ROOT / "engine_local_correctness.py").read_bytes(),
             "engine_local_correctness.py", "exec"), smoke.__dict__)
MODEL = "deepseek-v4.1-flash-1017"


def completion(message, finish="stop"):
    return json.dumps({"object": "chat.completion", "model": MODEL,
                       "choices": [{"index": 0, "message": {"role": "assistant", **message},
                                    "finish_reason": finish}],
                       "usage": {"prompt_tokens": 40, "completion_tokens": 4, "total_tokens": 44}})


def success(payload, _deadline):
    prompt = payload["messages"][0]["content"]
    case = next(case for case in smoke.CASES if case["prompt"] == prompt)
    if case["kind"] == "tool" and len(payload["messages"]) == 1:
        return completion({"content": None, "tool_calls": [
            {"id": "call_1", "type": "function", "function": {
                "name": "lookup_value", "arguments": json.dumps({"key": case["key"]})}}]},
                          "tool_calls")
    answer = json.dumps(case["answer"]) if case["kind"] == "json" else case["answer"]
    return completion({"content": answer})


class CorrectnessTests(unittest.TestCase):
    def test_eight_fixed_cases_ten_requests_no_tau_claim(self):
        call = mock.Mock(side_effect=success)
        row = smoke.run(call, MODEL)
        self.assertTrue(row["all_cases_pass"])
        self.assertFalse(row["tau_acceptance"])
        self.assertEqual(row["requests_started"], 10)
        self.assertEqual([x["id"] for x in row["cases"]], [x["id"] for x in smoke.CASES])
        for args in call.call_args_list:
            payload = args.args[0]
            self.assertEqual(payload["temperature"], 0)
            self.assertEqual(payload["max_completion_tokens"], 512)
            self.assertEqual(payload["reasoning_effort"], "none")
            self.assertFalse(payload["stream"])
            self.assertEqual(payload["n"], 1)
        text = json.dumps(row)
        for content in ("CEDAR", "MAPLE", "Record:", "lookup_value", "call_1", "north"):
            self.assertNotIn(content, text)

    def test_only_fixed_read_only_tool_and_continuation(self):
        calls = []
        def request(payload, deadline):
            calls.append(copy.deepcopy(payload))
            return success(payload, deadline)
        smoke.run(request, MODEL)
        for first, second in ((calls[6], calls[7]), (calls[8], calls[9])):
            self.assertEqual(first["tool_choice"], "required")
            self.assertEqual(second["tool_choice"], "none")
            self.assertFalse(second["parallel_tool_calls"])
            self.assertEqual(second["messages"][1]["tool_calls"][0]["id"], "call_1")
            self.assertEqual(second["messages"][2]["tool_call_id"], "call_1")
            self.assertEqual(set(second["messages"][2]), {"role", "tool_call_id", "content"})

    def test_exact_answer_failure_not_hidden_by_valid_usage(self):
        def request(payload, deadline):
            if payload["messages"][0]["content"] == smoke.CASES[0]["prompt"]:
                return completion({"content": "3"})
            return success(payload, deadline)
        row = smoke.run(request, MODEL)
        self.assertFalse(row["all_cases_pass"])
        self.assertEqual(row["cases"][0]["failure"], "answer")
        self.assertEqual(row["cases"][0]["requests"][0]["usage"]["completion_tokens"], 4)

    def test_reasoning_only_length_refusal_and_tools_are_not_final_answers(self):
        for message, finish in (({"content": None, "reasoning": "2"}, "stop"),
                                ({"content": "2"}, "length"),
                                ({"content": "2", "refusal": "no"}, "stop"),
                                ({"content": "2", "tool_calls": [{}]}, "stop")):
            with self.subTest(message=message):
                row = smoke.run(lambda *_: completion(message, finish), MODEL)
                self.assertFalse(row["all_cases_pass"])
                self.assertNotEqual(row["cases"][0]["result"], "pass")

    def test_usage_model_choices_and_types_are_checked(self):
        baseline = json.loads(completion({"content": "2"}))
        mutations = (
            lambda r: r.update(model="other"),
            lambda r: r.update(error="secret"),
            lambda r: r["choices"].append(copy.deepcopy(r["choices"][0])),
            lambda r: r["choices"][0].update(index=False),
            lambda r: r["usage"].update(prompt_tokens=True),
            lambda r: r["usage"].update(completion_tokens=513, total_tokens=553),
            lambda r: r["usage"].update(total_tokens=0),
            lambda r: r["usage"].update(prompt_tokens=4097, total_tokens=4101),
        )
        for mutate in mutations:
            row = copy.deepcopy(baseline)
            mutate(row)
            with self.subTest(mutate=mutate), self.assertRaises(smoke.Rejected):
                smoke.decode_completion(json.dumps(row), MODEL, 512)

    def test_json_exact_types_keys_duplicates_constants_and_fences(self):
        case = smoke.CASES[4]
        for content in ('{"count":true,"ok":true}', '{"count":3.0,"ok":true}',
                        '{"count":3,"ok":1}', '{"count":3,"ok":true,"extra":0}',
                        '{"count":3,"count":3,"ok":true}', '{"count":NaN,"ok":true}',
                        '```json\n{"count":3,"ok":true}\n```'):
            with self.subTest(content=content), self.assertRaises(smoke.Rejected):
                smoke.final_answer(case, {"content": content}, "stop")
        smoke.final_answer(case, {"content": '{"ok":true,"count":3}'}, "stop")
        with self.assertRaises(smoke.Rejected):
            smoke.strict_json(b" " * ((1 << 20) + 1))

    def test_tool_never_dispatches_unknown_name_or_arguments(self):
        case = smoke.CASES[6]
        raw = success({"messages": [{"content": case["prompt"]}]}, 1)
        message, finish, _ = smoke.decode_completion(raw, MODEL, 512)
        mutations = (
            lambda m: m["tool_calls"][0]["function"].update(name="execute_shell"),
            lambda m: m["tool_calls"][0]["function"].update(arguments='{"key":"gold"}'),
            lambda m: m["tool_calls"][0]["function"].update(arguments='{"key":"blue","cmd":"x"}'),
            lambda m: m["tool_calls"][0]["function"].update(arguments='{"key":"blue","key":"blue"}'),
            lambda m: m["tool_calls"][0].update(id="../../secret"),
            lambda m: m["tool_calls"].append(copy.deepcopy(m["tool_calls"][0])),
            lambda m: m.update(content={"text": "unexpected"}),
        )
        for mutate in mutations:
            changed = copy.deepcopy(message)
            mutate(changed)
            with self.subTest(mutate=mutate), self.assertRaises(smoke.Rejected):
                smoke.tool_continuation(case, changed, finish)

    def test_tool_failure_does_not_make_second_call(self):
        calls = []
        def request(payload, deadline):
            calls.append(payload)
            if payload.get("tools"):
                return completion({"content": "cannot call"}, "stop")
            return success(payload, deadline)
        row = smoke.run(request, MODEL)
        self.assertEqual(len(calls), 8)
        self.assertEqual(row["requests_started"], 8)
        self.assertEqual([r["failure"] for r in row["cases"][6:]], ["tool", "tool"])

    def test_deadline_budget_and_no_retry(self):
        clock = mock.Mock(return_value=100)
        request = mock.Mock(side_effect=success)
        row = smoke.run(request, MODEL, clock=clock, deadline=99)
        request.assert_not_called()
        self.assertEqual(row["requests_started"], 0)
        self.assertTrue(all(r["result"] == "not_run" for r in row["cases"]))
        request = mock.Mock(side_effect=TimeoutError("private transport detail"))
        row = smoke.run(request, MODEL)
        self.assertEqual(request.call_count, 8)
        self.assertTrue(all(r["failure"] == "timeout" for r in row["cases"]))
        self.assertNotIn("private", json.dumps(row))

    def test_cancellation_stops_further_calls(self):
        request = mock.Mock(side_effect=InterruptedError("private detail"))
        row = smoke.run(request, MODEL)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(row["cases"][0]["result"], "fail")
        self.assertTrue(all(r["result"] == "not_run" and r["failure"] == "cancelled"
                            for r in row["cases"][1:]))

    def test_budget_expiring_between_case_and_request_retains_not_run(self):
        times = iter([100, 100, 102])
        def clock():
            return next(times, 102)
        request = mock.Mock()
        row = smoke.run(request, MODEL, clock=clock, deadline=101)
        request.assert_not_called()
        self.assertTrue(all(r["result"] == "not_run" and r["failure"] == "budget" for r in row["cases"]))

    def test_response_before_deadline_but_grading_after_is_recorded_timeout(self):
        times = iter([100, 100, 100, 100, 159.999, 160.002])
        def clock():
            return next(times, 160.002)
        row = smoke.run(success, MODEL, clock=clock)
        first = row["cases"][0]["requests"][0]
        self.assertFalse(row["all_cases_pass"])
        self.assertEqual(first["failure"], "timeout")
        self.assertEqual(first["result"], "fail")
        self.assertGreater(first["elapsed_ms"], 60000)
        self.assertIsNotNone(first["usage"])
        self.assertEqual(row["requests_started"], 10)
        smoke.validate(row)

    def test_opaque_transport_failure_does_not_leak(self):
        request = mock.Mock(side_effect=RuntimeError("credential sentinel"))
        row = smoke.run(request, MODEL)
        self.assertFalse(row["all_cases_pass"])
        self.assertTrue(all(r["failure"] == "transport" for r in row["cases"]))
        self.assertNotIn("credential", json.dumps(row))

    def test_report_validator_rejects_fabricated_success(self):
        baseline = smoke.run(success, MODEL)
        mutations = (
            lambda r: r.update(requests_started=0),
            lambda r: r.update(tau_acceptance=True),
            lambda r: r.update(extra="private"),
            lambda r: r["cases"][0].update(id="private"),
            lambda r: r["cases"][0]["requests"][0].update(usage=None),
            lambda r: r["cases"][0]["requests"][0].update(elapsed_ms=60001),
            lambda r: r["cases"][0]["requests"][0].update(failure="answer"),
            lambda r: r["cases"][6]["requests"][0].update(result="fail", failure="tool"),
        )
        for mutate in mutations:
            row = copy.deepcopy(baseline)
            mutate(row)
            with self.subTest(mutate=mutate), self.assertRaises(smoke.Rejected):
                smoke.validate(row)

    def test_source_contract_verifies_exact_bytes(self):
        source = b"synthetic upstream bytes"
        expected = hashlib.sha256(source).hexdigest()
        read = mock.Mock(return_value=source)
        with mock.patch.object(smoke, "SOURCE_SHA256", {"protocol.py": expected}):
            smoke.verify_sources(read, "/selected")
            read.assert_called_once_with("/selected/protocol.py", 128 << 10)
            with self.assertRaises(smoke.Rejected):
                smoke.verify_sources(lambda *_: b"different", "/selected")


if __name__ == "__main__":
    unittest.main()
