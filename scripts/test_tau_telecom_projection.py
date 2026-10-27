# SPDX-License-Identifier: Apache-2.0
"""Synthetic offline telecom projections, not live tool/token-count evidence."""

import copy
from contextlib import redirect_stderr, redirect_stdout
import io
import unittest
from unittest import mock

import tau_telecom_projection as projection
import test_tau_external as fixtures


def body():
    value = fixtures.body("simulator")
    value.update(max_completion_tokens=2048, seed=42, tool_choice="auto", tools=[
        {"type": "function", "function": {**fixtures.tool()["function"], "name": f"tool_{n}"}}
        for n in range(30)])
    value["messages"] += [
        {"role": "assistant", "content": "SYNTHETIC_TEXT_CANARY", "tool_calls": [
            {"id": "call_1", "type": "function", "name": "tool_0",
             "function": {"name": "tool_0", "arguments": '{ "key": "SYNTHETIC_ARG_CANARY" }'}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": ' { "result" : "SYNTHETIC_RESULT_CANARY" } '},
        {"role": "user", "content": "continue"}]
    return value


class ProjectionTests(unittest.TestCase):
    def project(self, value):
        return projection.project(value, body()["tools"])

    def test_exact_semantics_and_no_input_mutation(self):
        value = body()
        frozen = copy.deepcopy(value)
        result = self.project(value)
        self.assertEqual(value, frozen)
        self.assertEqual(result["chat"]["seed"], 42)
        self.assertEqual(result["chat"]["tools"], value["tools"])
        self.assertFalse(result["chat"]["store"])
        self.assertEqual(result["chat"]["service_tier"], "default")
        self.assertNotIn("reasoning_effort", result["chat"])
        self.assertEqual(result["count"]["input"][2:5], [
            {"role": "assistant", "content": "SYNTHETIC_TEXT_CANARY"},
            {"type": "function_call", "call_id": "call_1", "name": "tool_0",
             "arguments": value["messages"][2]["tool_calls"][0]["function"]["arguments"]},
            {"type": "function_call_output", "call_id": "call_1", "output": value["messages"][3]["content"]}])
        self.assertEqual(result["count"]["tools"], [
            {"type": "function", **tool["function"], "strict": False} for tool in value["tools"]])
        self.assertEqual(result["count"]["truncation"], "disabled")
        self.assertFalse(result["count"]["parallel_tool_calls"])
        self.assertEqual(result["count"]["tool_choice"], "auto")
        self.assertEqual(result["request_sha256"], projection.worker.digest(projection.worker.encoded(value)))
        for key in ("source_authenticated", "token_count_parity_verified", "live_admission", "quality_eligible"):
            self.assertIs(result[key], False)

    def test_null_assistant_content_and_optional_tool_description(self):
        value = body()
        value["messages"][2]["content"] = None
        del value["tools"][0]["function"]["description"]
        result = projection.project(value, value["tools"])
        self.assertEqual(result["count"]["input"][2]["type"], "function_call")
        self.assertNotIn("description", result["count"]["tools"][0])

    def test_catalog_must_match_without_reordering_or_repair(self):
        for change in (lambda t: t.pop(), lambda t: t.append(t[0]), lambda t: t.reverse(),
                       lambda t: t[0]["function"].update(description="changed"),
                       lambda t: t[0]["function"]["parameters"].update(required=["key"])):
            value = body()
            change(value["tools"])
            with self.assertRaises(ValueError):
                self.project(value)

    def test_unknown_duplicate_and_orphan_tool_history_rejected(self):
        mutations = (
            lambda v: v["messages"][2]["tool_calls"][0]["function"].update(name="unknown"),
            lambda v: v["messages"][2]["tool_calls"][0].update(name="wrong"),
            lambda v: v["messages"][2]["tool_calls"][0].update(id="bad id"),
            lambda v: v["messages"][3].update(tool_call_id="other"),
            lambda v: v["messages"].pop(3),
            lambda v: v["messages"].insert(4, copy.deepcopy(v["messages"][3])),
            lambda v: v["messages"].extend(copy.deepcopy(v["messages"][2:4])),
            lambda v: v["messages"][2]["tool_calls"][0]["function"].update(arguments="[]"),
            lambda v: v["messages"][2]["tool_calls"][0]["function"].update(arguments='{"x":1,"x":2}'))
        for change in mutations:
            value = body()
            change(value)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.project(value)

    def test_tool_results_preserve_call_order(self):
        value = body()
        second = copy.deepcopy(value["messages"][2]["tool_calls"][0])
        second["id"] = "call_2"
        value["messages"][2]["tool_calls"].append(second)
        value["messages"].insert(4, {"role": "tool", "tool_call_id": "call_2", "content": "second"})
        self.project(value)
        value["messages"][3], value["messages"][4] = value["messages"][4], value["messages"][3]
        with self.assertRaises(ValueError):
            self.project(value)

    def test_call_frame_bound_is_separate_from_catalog_bound(self):
        for count in (16, 17):
            value = body()
            call = value["messages"][2]["tool_calls"][0]
            value["messages"][2]["tool_calls"] = [{**call, "id": f"call_{n}"} for n in range(count)]
            value["messages"][3:] = [
                {"role": "tool", "tool_call_id": f"call_{n}", "content": "result"} for n in range(count)]
            if count == 16:
                self.project(value)
            else:
                with self.assertRaises(ValueError):
                    self.project(value)

    def test_oversized_and_unsupported_requests_rejected(self):
        mutations = (
            lambda v: v.update(model="other"), lambda v: v.update(temperature=1),
            lambda v: v.update(stream=True), lambda v: v.update(n=True),
            lambda v: v.update(seed=True), lambda v: v.update(seed=-1),
            lambda v: v.update(seed=2**32), lambda v: v.update(store=True),
            lambda v: v.update(previous_response_id="hidden"), lambda v: v.update(tool_choice="none"),
            lambda v: v.update(parallel_tool_calls=True), lambda v: v.update(max_completion_tokens=2049),
            lambda v: v["messages"][0].update(content=[{"type": "image_url", "image_url": "bad"}]),
            lambda v: v["messages"][3].update(content="x" * 65537),
            lambda v: v["messages"][2]["tool_calls"][0]["function"].update(arguments='{"x":"' + "x" * 32768 + '"}'),
            lambda v: v["tools"][0]["function"].update(strict=True))
        for change in mutations:
            value = body()
            change(value)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.project(value)

    def test_projection_never_calls_provider_or_prints_content(self):
        output = io.StringIO()
        with mock.patch.object(projection.external.bounded_https, "HTTPS") as https, \
                redirect_stdout(output), redirect_stderr(output):
            self.project(body())
            value = body()
            value["messages"][3]["content"] = "x" * 65537
            with self.assertRaises(ValueError) as caught:
                self.project(value)
        https.assert_not_called()
        self.assertEqual(output.getvalue(), "")
        self.assertNotIn("CANARY", str(caught.exception))

    def test_live_simulator_and_candidate_limits_remain_closed(self):
        value = body()
        with self.assertRaises(ValueError):
            projection.external.simulator_request(value, full_task=True, support_chat=True)
        value["model"] = projection.external.policy.PUBLIC_MODEL
        with self.assertRaises(ValueError):
            projection.external.candidate_request(value, full_task=True)


if __name__ == "__main__":
    unittest.main()
