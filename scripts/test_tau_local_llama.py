# SPDX-License-Identifier: Apache-2.0
"""Synthetic HTTP tests, not model execution or runtime/tokenizer receipts."""

import copy
import json
import os
from pathlib import Path
import socket
import time
import traceback
import unittest
from unittest import mock

import tau_local_llama as live
import tau_local_vllm as vllm
import test_tau_local_vllm as fixtures

ROOT = Path(__file__).resolve().parents[1]


class LlamaTransportTests(unittest.TestCase):
    def body(self):
        body = fixtures.EndpointTests().body()
        return {**body, "model": "qwen/qwen3-0.6b", "max_completion_tokens": 32}

    def client(self, port, model="qwen/qwen3-0.6b"):
        return live.LocalLlamaCPP(port, model, 4096)

    def test_render_tokenize_generate_same_body_and_no_dns_or_proxy(self):
        body = self.body()
        before = copy.deepcopy(body)
        replies = [{"prompt": fixtures.CANARY}, {"tokens": [5] * 7},
                   fixtures.completion(model=body["model"])]
        with fixtures.endpoint(replies) as (port, seen):
            client = self.client(port)
            with mock.patch.dict(os.environ, {"HTTP_PROXY": fixtures.CANARY}), \
                    mock.patch.object(socket, "getaddrinfo", side_effect=AssertionError("DNS forbidden")):
                deadline = time.monotonic() + 2
                count = client.count(body, 4096, deadline)
                response = client.complete(body, count, deadline)
            self.assertEqual([item[0] for item in seen],
                             ["/apply-template", "/tokenize", "/v1/chat/completions"])
            self.assertEqual(seen[0][1], seen[2][1])
            self.assertEqual(seen[0][1]["chat_template_kwargs"], {"enable_thinking": False})
            self.assertEqual(seen[1][1], {"content": fixtures.CANARY, "add_special": True,
                                        "parse_special": True, "with_pieces": False})
            self.assertEqual(response["usage"]["total_tokens"], 8)
            self.assertEqual(body, before)

    def test_tools_and_full_history_pass_unchanged(self):
        body = self.body()
        body.update(messages=[{"role": "user", "content": fixtures.CANARY},
                              {"role": "assistant", "tool_calls": [{"id": "call-one", "type": "function",
                               "function": {"name": "lookup", "arguments": "{}"}}]},
                              {"role": "tool", "tool_call_id": "call-one", "content": "{}"}],
                    tools=[{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}],
                    tool_choice="auto")
        with fixtures.endpoint([{"prompt": fixtures.CANARY}, {"tokens": [1, 2, 3]}]) as (port, seen):
            self.assertEqual(self.client(port).count(body, 4096, time.monotonic() + 2), 3)
            self.assertEqual(seen[0][1]["messages"], body["messages"])
            self.assertEqual(seen[0][1]["tools"], body["tools"])
            self.assertEqual(seen[0][1]["tool_choice"], "auto")

    def test_each_model_requires_explicit_matching_identity(self):
        for model in live.MODELS:
            body = {**self.body(), "model": model}
            with fixtures.endpoint([fixtures.completion(model=model)]) as (port, _):
                self.assertEqual(self.client(port, model).complete(body, 7, time.monotonic() + 2)
                                 ["usage"]["prompt_tokens"], 7)
        for model, context in (("other-model", 4096), (None, 4096), ("qwen/qwen3-0.6b", True),
                               ("qwen/qwen3-0.6b", 8192)):
            with self.subTest(model=model, context=context), self.assertRaises(live.worker.Abort):
                live.LocalLlamaCPP(8000, model, context)

    def test_reject_unsupported_request_settings_before_http(self):
        changes = [{"model": "qwen/qwen3-1.7b"}, {"reasoning_effort": "high"},
                   {"stream": True}, {"n": True}, {"n": 2}, {"max_completion_tokens": 0},
                   {"max_completion_tokens": True}, {"max_completion_tokens": 257},
                   {"max_tokens": 32}, {"chat_template_kwargs": {"enable_thinking": True}},
                   {"messages": [{"role": "user", "content": [{"type": "image_url",
                                  "image_url": {"url": "https://invalid.invalid"}}]}]},
                   {"messages": [{"role": "user", "audio": "fixture"}]},
                   {"messages": []},
                   {"tool_choice": "required"}, {"url": "https://invalid.invalid"}]
        for change in changes:
            with self.subTest(change=change), fixtures.endpoint([]) as (port, seen):
                with self.assertRaises(live.worker.Abort):
                    self.client(port).count({**self.body(), **change}, 4096, time.monotonic() + 2)
                self.assertEqual(seen, [])

    def test_malformed_render_rejects_before_tokenizer(self):
        for row in ({}, {"prompt": ""}, {"prompt": []}, {"prompt": fixtures.CANARY, "tokens": [1]},
                    {"prompt": "x" * (live.worker.MAX_REQUEST + 1)}):
            with self.subTest(row_type=type(row).__name__), fixtures.endpoint([row]) as (port, seen):
                with self.assertRaises(live.worker.Abort) as caught:
                    self.client(port).count(self.body(), 4096, time.monotonic() + 2)
                self.assertEqual(len(seen), 1)
                self.assertNotIn(fixtures.CANARY, "".join(traceback.format_exception(caught.exception)))

    def test_strict_token_ids_and_context_bound(self):
        for row in ({}, {"tokens": []}, {"tokens": [True]}, {"tokens": [-1]},
                    {"tokens": [2**31]}, {"tokens": [1.0]}, {"tokens": ["1"]},
                    {"tokens": [{"id": 1, "piece": "x"}]}, {"tokens": [1] * 4097},
                    {"tokens": [1], "count": 1}):
            with self.subTest(row=row if len(str(row)) < 80 else "oversized"), \
                    fixtures.endpoint([{"prompt": "fixture"}, row]) as (port, seen):
                with self.assertRaises(live.worker.Abort):
                    self.client(port).count(self.body(), 4096, time.monotonic() + 2)
                self.assertEqual(len(seen), 2)
        with fixtures.endpoint([]) as (port, seen):
            with self.assertRaises(live.worker.Abort):
                self.client(port).count(self.body(), 8192, time.monotonic() + 2)
            self.assertEqual(seen, [])

    def test_one_deadline_is_shared_by_both_count_steps(self):
        client = self.client(8000)
        deadline = time.monotonic() + 2
        with mock.patch.object(client, "post", side_effect=[{"prompt": "fixture"}, {"tokens": [1]}]) as post:
            self.assertEqual(client.count(self.body(), 4096, deadline), 1)
        self.assertEqual([call.args[2] for call in post.call_args_list], [deadline, deadline])
        with fixtures.endpoint([]) as (port, seen):
            with self.assertRaises(live.worker.Abort) as caught:
                self.client(port).count(self.body(), 4096, time.monotonic() - 1)
            self.assertEqual(caught.exception.status, "timeout")
            self.assertEqual(seen, [])

    def test_completion_checks_usage_model_reasoning_and_no_truncation_repair(self):
        body = self.body()
        for changed in ("usage", "model", "reasoning"):
            row = fixtures.completion(model=body["model"])
            if changed == "usage":
                row["usage"]["prompt_tokens"] = 6
            elif changed == "model":
                row["model"] = "other-model"
            else:
                row["choices"][0]["message"]["reasoning_content"] = fixtures.CANARY
            with self.subTest(changed=changed), fixtures.endpoint([row]) as (port, _):
                with self.assertRaises(live.worker.Abort):
                    self.client(port).complete(body, 7, time.monotonic() + 2)
        row = fixtures.completion(model=body["model"])
        row["choices"][0]["finish_reason"] = "length"
        with fixtures.endpoint([row]) as (port, _):
            self.assertEqual(self.client(port).complete(body, 7, time.monotonic() + 2)
                             ["choices"][0]["finish_reason"], "length")

    def test_over_context_generation_is_never_sent(self):
        with fixtures.endpoint([]) as (port, seen):
            with self.assertRaises(live.worker.Abort):
                self.client(port).complete(self.body(), 4090, time.monotonic() + 2)
            self.assertEqual(seen, [])

    def test_paths_remain_class_specific_and_closed(self):
        with fixtures.endpoint([]) as (port, seen):
            for client, path in ((vllm.LocalChatHTTP(port), "/v1/chat/completions"),
                                 (vllm.LocalVllm(port), "/apply-template"),
                                 (self.client(port), "/completion"),
                                 (self.client(port), "https://invalid.invalid")):
                with self.assertRaises(live.worker.Abort):
                    client.post(path, {}, time.monotonic() + 2)
            self.assertEqual(seen, [])

    def test_pilot_pin_and_distinct_support_model_inventory(self):
        pins = json.loads((ROOT / "scripts/tau_cpu_support_models.json").read_bytes())
        pilot = json.loads((ROOT / "scripts/pilot/pins.json").read_bytes())
        self.assertEqual(pins["engine"], pilot["engine"])
        self.assertEqual(pins["engine"]["revision"], live.REVISION)
        self.assertEqual(pins["models"][pilot["model_id"]], pilot["model"])
        self.assertEqual(set(pins["models"]), live.MODELS)
        self.assertEqual(len({row["sha256"] for row in pins["models"].values()}), 2)
        self.assertFalse(pins["runtime_verified"])
        self.assertFalse(pins["quality_eligible"])


if __name__ == "__main__":
    unittest.main()
