# SPDX-License-Identifier: Apache-2.0
"""Official parity receipt boundaries with synthetic responses; no engine calls."""

import copy
import hashlib
import json
import types
import unittest
from unittest import mock

import deepseek_model_profile as models
import original_tokenizer_parity as parity
import engine_local_correctness as correctness
import test_engine_tokenizer_parity as fixtures


class OriginalParityTests(unittest.TestCase):
    def response(self, path, payload, deadline):
        value = json.loads(fixtures.response(path, payload, deadline))
        if path == "/tokenize":
            value["max_model_len"] = 1048576
        else:
            value["model"] = parity.ORIGINAL_MODEL
        return parity.encoded(value)

    def run_probe(self, request=None, **changes):
        options = {"deadline": 10, "clock": lambda: 1, "max_model_len": 1048576,
                   "execution_profile": parity.ORIGINAL_PROFILE, **changes}
        return parity.run(request or self.response, fixtures.local(), parity.ORIGINAL_MODEL,
                          correctness, **options)

    def test_explicit_original_identity_matches_committed_profile(self):
        profile = models.select("original")
        request = mock.Mock(side_effect=self.response)
        row = self.run_probe(request)
        self.assertEqual(row["schema"], "nib.tokenizer-parity/v3")
        self.assertEqual(row["model"], "deepseek/" + profile["served_model_name"])
        self.assertEqual(row["model_revision"], profile["hf_revision"])
        self.assertEqual(row["manifest_sha256"], profile["manifest_sha256"])
        self.assertEqual(row["max_model_len"], 1048576)
        self.assertTrue(row["all_cases_pass"])
        self.assertEqual(request.call_count, 8)
        self.assertTrue(all(call.args[1]["model"] == parity.ORIGINAL_MODEL
                            for call in request.call_args_list))
        self.assertNotIn(fixtures.CANARY, parity.encoded(row).decode())
        self.assertTrue(all(row[name] is False for name in
                            ("quality_accepted", "confidential", "tau_acceptance")))
        parity.validate(row, correctness, max_model_len=1048576,
                        execution_profile=parity.ORIGINAL_PROFILE)

    def test_legacy_receipts_remain_byte_identical_at_both_limits(self):
        old = types.ModuleType("previous_original_parity")
        raw = fixtures.git(fixtures.ROOT, "show",
            "31162745cb90f4ab268f1b2b8eb8c768b750442a:scripts/engine_tokenizer_parity.py")
        exec(compile(raw, "previous_original_parity", "exec"), old.__dict__)
        current = (fixtures.ROOT / "scripts/engine_tokenizer_parity.py").read_bytes()
        self.assertEqual(current, raw)
        self.assertEqual(hashlib.sha256(current).hexdigest(),
                         "978814f3341b83fed33282f23ee76b173bc4235235ccf374149d0363252f3ce8")
        for limit in (4096, 8192):
            def response(*args):
                return fixtures.response(*args, max_model_len=limit)
            for request in (response, lambda *_: b"{}", mock.Mock(side_effect=TimeoutError)):
                args = (request, fixtures.local(), fixtures.lab.MODEL, correctness)
                options = {"deadline": 10, "clock": lambda: 1, "max_model_len": limit}
                self.assertEqual(parity.encoded(fixtures.parity.run(*args, **options)),
                                 old.encoded(old.run(*args, **options)))

    def test_model_profile_and_context_fail_before_dispatch(self):
        for changes in ({"execution_profile": None}, {"execution_profile": "long1m-graphs"},
                        {"execution_profile": True}, {"execution_profile": {}},
                        {"max_model_len": 8192}, {"max_model_len": 4096},
                        {"max_model_len": 1048576.0}, {"max_model_len": True}):
            request = mock.Mock()
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.run_probe(request, **changes)
            request.assert_not_called()
        for model in (fixtures.lab.MODEL, models.select("original")["served_model_name"],
                      "dealignai/deepseek-v4.1-flash-1017-uncensored-fp8", None):
            request = mock.Mock()
            with self.subTest(model=model), self.assertRaises(ValueError):
                parity.run(request, fixtures.local(), model, correctness, deadline=10,
                    clock=lambda: 1, max_model_len=1048576, execution_profile=parity.ORIGINAL_PROFILE)
            request.assert_not_called()

    def test_context_or_response_model_substitution_stops_at_first_failure(self):
        for limit in (4096, 8192, True, 1048576.0):
            def wrong_limit(path, payload, deadline):
                value = json.loads(self.response(path, payload, deadline))
                value["max_model_len"] = limit
                return parity.encoded(value)
            request = mock.Mock(side_effect=wrong_limit)
            row = self.run_probe(request)
            self.assertFalse(row["all_cases_pass"])
            self.assertEqual((request.call_count, row["cases"][0]["failure"]), (1, "protocol"))
        def old_model(path, payload, deadline):
            if path == "/tokenize":
                return self.response(path, payload, deadline)
            return fixtures.response(path, payload, deadline)
        request = mock.Mock(side_effect=old_model)
        row = self.run_probe(request)
        self.assertFalse(row["all_cases_pass"])
        self.assertEqual((request.call_count, row["cases"][0]["failure"]), (2, "protocol"))

    def test_original_receipt_cannot_be_relabelled_or_used_as_legacy(self):
        row = self.run_probe()
        for key, value in (("model", fixtures.lab.MODEL), ("model_revision", "a" * 40),
                           ("manifest_sha256", "a" * 64), ("execution_profile", "long1m-graphs"),
                           ("max_model_len", 8192), ("binding_sha256", parity.binding(correctness)),
                           ("schema", "nib.tokenizer-parity/v2"), ("quality_accepted", True)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                parity.validate({**row, key: value}, correctness, max_model_len=1048576,
                                execution_profile=parity.ORIGINAL_PROFILE)
        for limit in (4096, 8192):
            with self.assertRaises(ValueError):
                fixtures.parity.validate(row, correctness, max_model_len=limit)
        legacy = fixtures.parity.run(fixtures.response, fixtures.local(), fixtures.lab.MODEL,
                            correctness, deadline=10, clock=lambda: 1)
        with self.assertRaises(ValueError):
            parity.validate(legacy, correctness, max_model_len=1048576,
                            execution_profile=parity.ORIGINAL_PROFILE)
        changed = copy.deepcopy(row)
        changed["cases"][0]["local"]["count"] += 1
        with self.assertRaises(ValueError):
            parity.validate(changed, correctness, max_model_len=1048576,
                            execution_profile=parity.ORIGINAL_PROFILE)

    def test_timeouts_errors_and_expired_budget_keep_original_bounds(self):
        for error, category in ((TimeoutError(fixtures.CANARY), "timeout"),
                                (InterruptedError(fixtures.CANARY), "cancelled"),
                                (OSError(fixtures.CANARY), "transport")):
            request = mock.Mock(side_effect=error)
            row = self.run_probe(request)
            self.assertEqual((request.call_count, row["cases"][0]["failure"]), (1, category))
            self.assertNotIn(fixtures.CANARY, parity.encoded(row).decode())
        request = mock.Mock()
        row = self.run_probe(request, deadline=1)
        request.assert_not_called()
        self.assertFalse(row["all_cases_pass"])
        now = [0.0]
        deadlines = []
        def late(path, payload, deadline):
            deadlines.append(deadline)
            now[0] = deadline
            return self.response(path, payload, deadline)
        row = self.run_probe(late, deadline=1000, clock=lambda: now[0])
        self.assertEqual(deadlines, [30])
        self.assertEqual(row["cases"][0]["failure"], "timeout")
        self.assertEqual(row["requests_started"], 1)

    def test_token_ids_and_usage_are_compared_without_extra_requests(self):
        for mode, expected, count in (("ids", "token-ids", 1), ("usage", "usage", 2)):
            def wrong(path, payload, deadline):
                value = json.loads(self.response(path, payload, deadline))
                if path == "/tokenize" and mode == "ids":
                    value["tokens"][-1] += 1
                if path != "/tokenize" and mode == "usage":
                    value["usage"]["prompt_tokens"] += 1
                    value["usage"]["total_tokens"] += 1
                return parity.encoded(value)
            request = mock.Mock(side_effect=wrong)
            row = self.run_probe(request)
            self.assertEqual((request.call_count, row["cases"][0]["failure"]), (count, expected))
            self.assertFalse(row["all_cases_pass"])
            self.assertTrue(all(item["result"] == "not_run" for item in row["cases"][1:]))


if __name__ == "__main__":
    unittest.main()
