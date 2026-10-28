# SPDX-License-Identifier: Apache-2.0
"""Calibration transport accounting; synthetic replies only, no inference."""
import importlib.util
import copy
from contextlib import ExitStack, redirect_stdout, redirect_stderr
import io
import json
import os
import py_compile
from pathlib import Path
import subprocess
import ssl
import sys
import time
import tempfile
import types
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("quality_calibration", Path(__file__).with_name("record-quality-calibration.py"))
quality = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quality)
report_spec = importlib.util.spec_from_file_location("quality_summary", Path(__file__).with_name("summarize-quality-calibration.py"))
summary = importlib.util.module_from_spec(report_spec)
report_spec.loader.exec_module(summary)


def reply():
    return {"model": quality.MODEL, "usage": {"prompt_tokens": 20, "completion_tokens": 3, "total_tokens": 23},
            "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "SYNTHETIC_REPLY_CANARY"}}]}


def public_contract():
    return {"schema": "nib.quality-calibration-endpoint/v1", "model": quality.MODEL,
            "serving_profile": "long1m-graphs", "serving_context_tokens": 1048576,
            "retire_at": int(time.time()) + 7200, "tls_mode": "system-roots",
            "hostname": "synthetic.invalid", "address": "8.8.8.8", "port": 443}


class CandidateTransportTests(unittest.TestCase):
    def test_public_contract_uses_existing_system_roots_with_fixed_address(self):
        contract = public_contract()
        with mock.patch.object(quality.tau_live_identity, "private_read") as read, \
             mock.patch.object(quality.bounded_https, "system_trust") as trust:
            http, mode = quality.candidate_transport(contract, time.monotonic() + 10)
        self.assertEqual(mode, "system-roots")
        self.assertEqual((http.hostname, http.address, http.port), ("synthetic.invalid", "8.8.8.8", 443))
        self.assertEqual(http.paths, ("/v1/chat/completions",))
        self.assertIsNone(http.peer_digest)
        self.assertIs(http.trust, trust.return_value)
        trust.assert_called_once_with()
        read.assert_not_called()

    def test_public_contract_rejects_ambiguous_tls_and_unbounded_lifetimes(self):
        cases = [("schema", "nib.tau-external-trial-endpoint/v3"), ("tls_mode", "insecure"),
                 ("tls_mode", "pinned"), ("certificate", "/unused"), ("certificate_sha256", "a" * 64),
                 ("model", "wrong"), ("serving_profile", "batch32-graphs"),
                 ("serving_context_tokens", True), ("serving_context_tokens", 32768),
                 ("retire_at", float("inf")), ("retire_at", float("nan")), ("retire_at", True),
                 ("retire_at", time.time() + 7200), ("retire_at", int(time.time()) + 3500),
                 ("retire_at", int(time.time()) + 8 * 86400), ("port", 18443),
                 ("port", "443"), ("address", None), ("unexpected", "PRIVATE_CANARY")]
        for key, value in cases:
            with self.subTest(key=key, value=value), \
                 mock.patch.object(quality.bounded_https, "HTTPS") as transport, \
                 mock.patch.object(quality.tau_live_identity, "private_read") as read:
                contract = public_contract()
                contract[key] = value
                with self.assertRaises(ValueError):
                    quality.candidate_transport(contract, time.monotonic() + 10)
                transport.assert_not_called()
                read.assert_not_called()
        for key in public_contract():
            contract = public_contract()
            del contract[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                quality.candidate_transport(contract, time.monotonic() + 10)

    def test_public_contract_keeps_transport_host_and_global_address_checks(self):
        for field, value in (("address", "127.0.0.1"), ("address", "10.0.0.1"),
                             ("address", "::1"), ("hostname", "https://synthetic.invalid")):
            contract = public_contract()
            contract[field] = value
            with self.subTest(field=field, value=value), \
                 mock.patch.object(quality.bounded_https, "system_trust") as trust:
                with self.assertRaises(ValueError):
                    quality.candidate_transport(contract, time.monotonic() + 10)
                trust.assert_not_called()

    def test_public_contract_rejects_environment_trust_overrides(self):
        for name in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
            with self.subTest(name=name), mock.patch.dict(os.environ, {name: "/unused"}), \
                 self.assertRaises(ValueError):
                quality.candidate_transport(public_contract(), time.monotonic() + 10)

    def test_public_tls_failure_never_sends_credentials_or_retries(self):
        with mock.patch.object(quality.bounded_https, "system_trust") as trust, \
             mock.patch.object(quality.bounded_https.socket, "socket") as sockets:
            trust.return_value.wrap_socket.side_effect = ssl.SSLCertVerificationError("synthetic rejection")
            client, _ = quality.candidate_transport(public_contract(), time.monotonic() + 10)
            before_send = mock.Mock()
            with self.assertRaises(ssl.SSLCertVerificationError):
                client.post("/v1/chat/completions", {}, b"SYNTHETIC_PRIVATE_CANARY",
                            time.monotonic() + 2, before_send=before_send)
            sockets.assert_called_once()
            socket = sockets.return_value.__enter__.return_value
            socket.connect.assert_called_once_with(("8.8.8.8", 443))
            trust.return_value.wrap_socket.assert_called_once_with(socket, server_hostname="synthetic.invalid")
            socket.sendall.assert_not_called()
            before_send.assert_not_called()
            self.assertEqual(client.attempts, {"/v1/chat/completions": 0})
            self.assertFalse(client.used)

    def test_pinned_contract_has_no_system_root_fallback(self):
        contract = public_contract()
        contract.update(schema="nib.tau-external-trial-endpoint/v2", port=18443,
                        certificate="/unused", certificate_sha256="a" * 64)
        del contract["tls_mode"]
        with mock.patch.object(quality.tau_live_identity, "private_read", return_value=b"pinned") as read, \
             mock.patch.object(quality.bounded_https, "HTTPS", side_effect=ssl.SSLError("synthetic")) as transport:
            with self.assertRaises(ssl.SSLError):
                quality.candidate_transport(contract, 1234)
        read.assert_called_once_with("/unused", 1234)
        transport.assert_called_once_with("synthetic.invalid", ("/v1/chat/completions",),
            address="8.8.8.8", port=18443, certificate_pem=b"pinned", certificate_sha256="a" * 64)
        contract["tls_mode"] = "system-roots"
        with mock.patch.object(quality.tau_live_identity, "private_read") as read, self.assertRaises(ValueError):
            quality.candidate_transport(contract, 1234)
        read.assert_not_called()


class CalibrationTests(unittest.TestCase):
    def test_real_writer_preserves_interrupted_transmission(self):
        contract = {"schema": "nib.tau-external-trial-endpoint/v2", "model": quality.MODEL,
                    "serving_profile": "long1m-graphs", "retire_at": time.time() + 7200,
                    "certificate": "/unused", "hostname": "synthetic.invalid", "address": "8.8.8.8",
                    "port": 18443, "certificate_sha256": "a" * 64}
        self.assert_interrupted_writer(contract)

    def test_public_tls_writer_preserves_interrupted_transmission(self):
        self.assert_interrupted_writer(public_contract())

    def assert_interrupted_writer(self, contract):
        rows = self.writer_receipt(contract)
        self.assertEqual(rows[0]["schema"], "nib.quality-calibration/v3")
        self.assertEqual(rows[1], {"kind": "intent", "index": 0})
        self.assertEqual(rows[2]["status"], "cancelled")
        self.assertIsNone(rows[2]["cached_prompt_tokens"])
        self.assertEqual(rows[-1]["generation_requests"], 1)
        self.assertEqual(rows[-1]["unknown_usage_requests"], 1)
        self.assertEqual(rows[-1]["known_cached_prompt_tokens"], 0)
        self.assertEqual(rows[-1]["unknown_cache_usage_requests"], 1)
        self.assertEqual(rows[-1]["missing"], 19)
        record = summary.report(b"".join(quality.worker.encoded(row) for row in rows))
        self.assertEqual(record["unknown_cache_usage_requests"], 1)

    def writer_receipt(self, contract, responses=None, exit_code=1):
        values = iter(responses or [])
        def post(*args, before_send, **kwargs):
            before_send()
            value = next(values, None)
            if value is None:
                raise KeyboardInterrupt
            http.last_status = 200
            return value
        http = types.SimpleNamespace(last_status=None, post=post)
        public = contract["schema"] in ("nib.quality-calibration-endpoint/v1", quality.quality_candidate.ORIGINAL_ENDPOINT)
        raw = json.dumps(contract).encode()
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            output = Path(temporary) / "receipt.jsonl"
            args = ["quality", "--suite", "gsm8k", "--count", "20", "--output", str(output),
                    "--contract-sha256", quality.digest(raw)]
            for name in ("trbench-root", "contract", "candidate-key", "vllm-source", "tokenizer", "tokenizer-wheel"):
                args += ["--" + name, "/unused"]
            stack.enter_context(mock.patch.object(quality.sys, "argv", args))
            isolated_sys = types.SimpleNamespace(**vars(quality.sys))
            isolated_sys.flags = types.SimpleNamespace(isolated=True)
            stack.enter_context(mock.patch.object(quality, "sys", isolated_sys))
            stack.enter_context(mock.patch.object(quality, "source", return_value="a" * 64))
            stack.enter_context(mock.patch.object(quality, "cases", return_value=(
                [{}] * 20, lambda r: "SYNTHETIC_PROMPT_CANARY",
                lambda *a: {"correct": 1, "total": 1, "errors": 0, "score": 100.0}, "b" * 64)))
            stack.enter_context(mock.patch.object(quality.tau_live_identity, "private_read",
                                                  side_effect=[raw, b"key"] if public else [raw, b"pem", b"key"]))
            stack.enter_context(mock.patch.object(quality.bounded_https, "HTTPS", return_value=http))
            stack.enter_context(mock.patch.object(quality.tau_external_tokenizer, "Counter", return_value=types.SimpleNamespace(count=lambda b: 20, close=lambda: None)))
            def git(argv, **kwargs):
                if "rev-parse" in argv:
                    return b"a" * 40
                return (quality.ROOT / argv[-1].split(":", 1)[1]).read_bytes()
            stack.enter_context(mock.patch.object(quality.subprocess, "check_output", side_effect=git))
            stack.enter_context(redirect_stdout(io.StringIO()))
            stack.enter_context(redirect_stderr(io.StringIO()))
            self.assertEqual(quality.main(), exit_code)
            rows = [json.loads(line) for line in output.read_bytes().splitlines()]
            self.assertEqual(rows[0]["candidate_tls_mode"], "system-roots" if public else "pinned")
            self.assertNotIn(b"CANARY", output.read_bytes())
            return rows

    def test_completed_v3_writer_keeps_full_denominator(self):
        rows = self.writer_receipt(public_contract(), [reply() for _ in range(20)], exit_code=0)
        self.assertEqual(sum(row["kind"] == "intent" for row in rows), 20)
        record = summary.report(b"".join(quality.worker.encoded(row) for row in rows))
        self.assertEqual((record["result"], record["expected"], record["evaluated"], record["correct"]),
                         ("pass", 20, 20, 20))
        self.assertEqual((record["generation_requests"], record["unknown_usage_requests"]), (20, 0))

    def test_cache_usage_does_not_change_payload_scoring_or_total_usage(self):
        for details, cached in ((None, None), ({}, None), ({"cached_tokens": None}, None),
                                ({"cached_tokens": 0}, 0), ({"cached_tokens": 12}, 12),
                                ({"cached_tokens": 20, "ignored": "PRIVATE_CANARY"}, 20)):
            with self.subTest(details=details):
                value = reply()
                value["usage"]["prompt_tokens_details"] = details
                http = types.SimpleNamespace(last_status=None, post=mock.Mock(return_value=value))
                grade = mock.Mock(return_value={"correct": 1})
                record = quality.evaluate(0, {}, lambda r: "synthetic", grade,
                    types.SimpleNamespace(count=lambda b: 20), http, b"synthetic", time.monotonic() + 10)
                self.assertEqual(record["status"], "evaluated")
                self.assertEqual(record["cached_prompt_tokens"], cached)
                self.assertEqual(record["usage"], {"prompt_tokens": 20, "completion_tokens": 3})
                grade.assert_called_once_with({}, "SYNTHETIC_REPLY_CANARY")
                self.assertEqual(http.post.call_args.args[1], {
                    "model": quality.MODEL, "messages": [{"role": "user", "content": "synthetic"}],
                    "max_completion_tokens": 8192, "temperature": 0, "reasoning_effort": "none", "stream": False})
                http.post.assert_called_once()
                self.assertNotIn("CANARY", json.dumps(record))

    def test_malformed_cache_usage_fails_without_losing_measured_totals(self):
        cases = [[], "PRIVATE_CANARY", 0, False]
        cases += [{"cached_tokens": value} for value in (-1, 21, True, 1.0, "12", [], {})]
        for details in cases:
            with self.subTest(details=details):
                value = reply()
                value["usage"]["prompt_tokens_details"] = details
                http = types.SimpleNamespace(last_status=None, post=mock.Mock(return_value=value))
                grade = mock.Mock()
                record = quality.evaluate(0, {}, lambda r: "synthetic", grade,
                    types.SimpleNamespace(count=lambda b: 20), http, b"synthetic", time.monotonic() + 10)
                self.assertEqual(record["status"], "protocol_or_transport_error")
                self.assertEqual(record["stage"], "response-validation")
                self.assertEqual(record["usage"], {"prompt_tokens": 20, "completion_tokens": 3})
                self.assertIsNone(record["cached_prompt_tokens"])
                grade.assert_not_called()
                http.post.assert_called_once()
                self.assertNotIn("CANARY", json.dumps(record))

    def test_cache_usage_survives_later_failure_and_truncation(self):
        for mutation, status in (({"model": "wrong"}, "protocol_or_transport_error"),
                                 ({"choices": []}, "protocol_or_transport_error"),
                                 ({}, "truncated")):
            value = reply()
            value["usage"]["prompt_tokens_details"] = {"cached_tokens": 12}
            value["choices"][0]["finish_reason"] = "length"
            value.update(mutation)
            grade = mock.Mock(return_value={"correct": 0})
            record = quality.evaluate(0, {}, lambda r: "synthetic", grade,
                types.SimpleNamespace(count=lambda b: 20),
                types.SimpleNamespace(last_status=None, post=mock.Mock(return_value=value)),
                b"synthetic", time.monotonic() + 10)
            self.assertEqual(record["status"], status)
            self.assertEqual(record["cached_prompt_tokens"], 12)
            if status == "truncated":
                grade.assert_called_once_with({}, "")
            else:
                grade.assert_not_called()

    def test_real_writer_and_summary_distinguish_missing_zero_and_cache_hits(self):
        responses = [reply() for _ in range(3)]
        responses[0]["usage"]["prompt_tokens_details"] = {"cached_tokens": 12}
        responses[1]["usage"]["prompt_tokens_details"] = {"cached_tokens": 0}
        rows = self.writer_receipt(public_contract(), responses)
        record = summary.report(b"".join(quality.worker.encoded(row) for row in rows))
        self.assertEqual(record["schema"], "nib.quality-calibration-summary/v3")
        self.assertEqual([r["cached_prompt_tokens"] for r in record["items"]], [12, 0, None, None])
        for key, value in {"known_prompt_tokens": 60, "known_completion_tokens": 9,
                           "known_cached_prompt_tokens": 12, "unknown_cache_usage_requests": 2,
                           "unknown_usage_requests": 1, "generation_requests": 4, "correct": 3,
                           "expected": 20, "missing": 16, "errors": 1}.items():
            self.assertEqual(record[key], value, key)
        self.assertNotIn("CANARY", json.dumps(record))
        invalid = [(2, "cached_prompt_tokens", v) for v in (-1, 21, True, 1.0, "12", [], {})]
        invalid += [(8, "cached_prompt_tokens", 0), (2, "usage", None),
                    (-1, "known_cached_prompt_tokens", 0), (-1, "unknown_cache_usage_requests", 1),
                    (-1, "unknown_cache_usage_requests", True)]
        for index, key, value in invalid:
            with self.subTest(index=index, key=key, value=value):
                bad = copy.deepcopy(rows)
                bad[index][key] = value
                with self.assertRaises(ValueError):
                    summary.report(b"".join(quality.worker.encoded(row) for row in bad))
        for index, key in ((2, "cached_prompt_tokens"), (2, "generation_started"),
                           (-1, "known_cached_prompt_tokens"), (-1, "unknown_cache_usage_requests")):
            bad = copy.deepcopy(rows)
            del bad[index][key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                summary.report(b"".join(quality.worker.encoded(row) for row in bad))
        rows[8]["generation_started"] = False
        del rows[7]  # A request that never reached before_send has no intent.
        rows[-1].update(generation_requests=3, unknown_usage_requests=0, unknown_cache_usage_requests=1)
        self.assertEqual(summary.report(b"".join(quality.worker.encoded(row) for row in rows))[
            "unknown_cache_usage_requests"], 1)

    def test_v2_receipts_remain_accepted_without_inventing_intents(self):
        rows = self.writer_receipt(public_contract(), [reply()])
        rows = [row for row in rows if row["kind"] != "intent"]
        rows[0]["schema"] = "nib.quality-calibration/v2"
        record = summary.report(b"".join(quality.worker.encoded(row) for row in rows))
        self.assertEqual(record["schema"], "nib.quality-calibration-summary/v2")
        self.assertEqual(record["generation_requests"], 2)
        self.assertEqual(record["unknown_usage_requests"], 1)

    def test_v3_rejects_missing_duplicate_reordered_or_unresolved_intents(self):
        rows = self.writer_receipt(public_contract(), [reply()])
        mutations = [lambda r: r.pop(1), lambda r: r.insert(1, dict(r[1])),
                     lambda r: r[1].update(index=1), lambda r: r[1].update(index=True),
                     lambda r: r[1].update(extra="PRIVATE_CANARY"),
                     lambda r: r[2].update(generation_started=False),
                     lambda r: r[2].update(generation_started=1),
                     lambda r: r.pop(-2), lambda r: r.pop(-1),
                     lambda r: r.insert(-1, {"kind": "intent", "index": 2})]
        for mutate in mutations:
            bad = copy.deepcopy(rows)
            mutate(bad)
            with self.subTest(rows=bad), self.assertRaises((ValueError, KeyError)):
                summary.report(b"".join(quality.worker.encoded(row) for row in bad))

    def test_journal_persists_manifest_intent_result_before_next_action(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.jsonl"
            observed = []
            fsync = os.fsync
            def sync(fd):
                fsync(fd)
                observed.append(path.read_bytes())
            with mock.patch.object(quality.os, "fsync", side_effect=sync), quality.journal(path) as journal:
                journal.write({"kind": "manifest"})
                journal.write({"kind": "intent", "index": 0})
                journal.write({"kind": "item", "index": 0})
            self.assertEqual([len(raw.splitlines()) for raw in observed], [0, 1, 2, 3])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            original = path.read_bytes()
            with self.assertRaises(FileExistsError), quality.journal(path):
                self.fail("Existing evidence must never be overwritten")
            self.assertEqual(path.read_bytes(), original)

    def test_journal_failure_prevents_send_and_any_more_writes(self):
        for failure in (OSError("PRIVATE_CANARY"), KeyboardInterrupt(), quality.worker.Abort("timeout")):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as directory:
                with quality.journal(Path(directory) / "receipt.jsonl") as journal:
                    journal.write({"kind": "manifest"})
                    sent = mock.Mock()
                    def post(*args, before_send, **kwargs):
                        before_send()
                        sent()
                        return reply()
                    http = types.SimpleNamespace(last_status=None, post=post)
                    with mock.patch.object(quality.os, "fsync", side_effect=failure), \
                         self.assertRaises(quality.RecordingFailure):
                        quality.evaluate(0, {}, lambda _: "PRIVATE_CANARY", mock.Mock(),
                            types.SimpleNamespace(count=lambda _: 20), http, b"PRIVATE_CANARY",
                            time.monotonic() + 10,
                            before_send=lambda: journal.write({"kind": "intent", "index": 0}))
                    sent.assert_not_called()
                    self.assertTrue(journal.failed)
                    with self.assertRaises(quality.RecordingFailure):
                        journal.write({"kind": "summary"})

    def test_short_write_and_flush_failure_poison_journal(self):
        for stage in ("write", "flush"):
            stream = mock.Mock()
            stream.write.return_value = 0 if stage == "write" else len(quality.worker.encoded({"kind": "intent"}))
            if stage == "flush":
                stream.flush.side_effect = OSError("PRIVATE_CANARY")
            journal = quality.Journal(stream)
            with mock.patch.object(quality.os, "fsync") as sync, self.assertRaises(quality.RecordingFailure):
                journal.write({"kind": "intent"})
            sync.assert_not_called()
            self.assertTrue(journal.failed)

    def test_abrupt_process_exit_keeps_intent_but_never_qualifies(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.jsonl"
            program = '''import importlib.util, os, pathlib, sys, time, types
s = importlib.util.spec_from_file_location("quality", "scripts/record-quality-calibration.py")
q = importlib.util.module_from_spec(s)
s.loader.exec_module(q)
def post(*args, before_send, **kwargs):
    before_send()
    os._exit(42)
with q.journal(pathlib.Path(sys.argv[1])) as journal:
    journal.write({"kind": "manifest", "schema": "nib.quality-calibration/v3", "suite": "gsm8k"})
    q.evaluate(0, {}, lambda _: "SYNTHETIC_PROMPT_CANARY", lambda *a: None,
        types.SimpleNamespace(count=lambda _: 20), types.SimpleNamespace(last_status=None, post=post),
        b"SYNTHETIC_KEY_CANARY", time.monotonic() + 10,
        before_send=lambda: journal.write({"kind": "intent", "index": 0}))
'''
            proc = subprocess.run([sys.executable, "-I", "-B", "-c", program, str(path)],
                                  cwd=quality.ROOT, capture_output=True, timeout=10)
            self.assertEqual(proc.returncode, 42)
            raw = path.read_bytes()
            self.assertEqual([r["kind"] for r in map(json.loads, raw.splitlines())], ["manifest", "intent"])
            self.assertNotIn(b"CANARY", raw + proc.stdout + proc.stderr)
            with self.assertRaises(ValueError):
                summary.report(raw)

    def test_real_writer_retains_usage_on_cache_and_later_protocol_failures(self):
        malformed, late_failure = reply(), reply()
        malformed["usage"]["prompt_tokens_details"] = {"cached_tokens": "PRIVATE_CANARY"}
        late_failure["usage"]["prompt_tokens_details"] = {"cached_tokens": 12}
        late_failure["model"] = "PRIVATE_CANARY"
        rows = self.writer_receipt(public_contract(), [malformed, late_failure])
        record = summary.report(b"".join(quality.worker.encoded(row) for row in rows))
        self.assertEqual([r["cached_prompt_tokens"] for r in record["items"]], [None, 12, None])
        for key, value in {"known_prompt_tokens": 40, "known_completion_tokens": 6,
                           "known_cached_prompt_tokens": 12, "unknown_cache_usage_requests": 2,
                           "unknown_usage_requests": 1, "generation_requests": 3,
                           "correct": 0, "errors": 3, "evaluated": 0}.items():
            self.assertEqual(record[key], value, key)
        self.assertNotIn("CANARY", json.dumps(record))

    def test_summary_full_denominator_and_no_input_text(self):
        manifest = {"schema": "nib.quality-calibration/v1", "suite": "gsm8k", "expected": 20,
                    "started_at": "2026-10-26T23:00:00+00:00", "git_sha": "a" * 40,
                    "trbench_revision": "b" * 40, "dataset_sha256": "c" * 64,
                    "trbench_tree_sha256": "d" * 64, "endpoint_binding_sha256": "e" * 64}
        item = {"kind": "item", "index": 0, "status": "evaluated", "score": {"correct": 1, "total": 1, "errors": 0, "score": 100.0},
                "usage": {"prompt_tokens": 20, "completion_tokens": 3}, "elapsed_seconds": 1.5,
                "http_status": 200, "finish_reason": "stop", "generation_started": True,
                "unexpected_text": "SYNTHETIC_PRIVATE_CANARY"}
        terminal = {"kind": "summary", "expected": 20, "attempted": 1, "evaluated": 1, "missing": 19,
                    "errors": 0, "known_prompt_tokens": 20, "known_completion_tokens": 3,
                    "generation_requests": 1, "unknown_usage_requests": 0, "latency_median_seconds": 1.5,
                    "ended_at": "2026-10-26T23:01:00+00:00"}
        raw = lambda: b"\n".join(json.dumps(row).encode() for row in (manifest, item, terminal))
        record = summary.report(raw())
        self.assertEqual(record["schema"], "nib.quality-calibration-summary/v1")
        self.assertNotIn("known_cached_prompt_tokens", record)
        self.assertNotIn("unknown_cache_usage_requests", record)
        self.assertNotIn("cached_prompt_tokens", record["items"][0])
        self.assertEqual(record["candidate_tls_mode"], "unrecorded")
        for mode in ("pinned", "system-roots"):
            manifest["candidate_tls_mode"] = mode
            self.assertEqual(summary.report(raw())["candidate_tls_mode"], mode)
        manifest["candidate_tls_mode"] = "PRIVATE_CANARY"
        with self.assertRaises(ValueError):
            summary.report(raw())
        del manifest["candidate_tls_mode"]
        self.assertEqual((record["result"], record["correct"], record["expected"]), ("fail", 1, 20))
        self.assertNotIn("CANARY", json.dumps(record))
        self.assertTrue(record["items"][0]["generation_started"])
        self.assertEqual(record["generation_requests"], 1)
        for target, key, invalid in ((item, "finish_reason", "length"),
                                     (item["usage"], "completion_tokens", 8193),
                                     (item["usage"], "prompt_tokens", 24577),
                                     (item, "generation_started", False),
                                     (terminal, "generation_requests", 2),
                                     (terminal, "unknown_usage_requests", 1),
                                     (terminal, "known_prompt_tokens", 19),
                                     (terminal, "errors", 1),
                                     (terminal, "ended_at", "2026-10-25T23:01:00+00:00")):
            original = target[key]
            target[key] = invalid
            with self.assertRaises(ValueError):
                summary.report(raw())
            target[key] = original
        terminal["missing"] = 0
        with self.assertRaises(ValueError):
            summary.report(raw())
        terminal["missing"] = 19
        for invalid in (None, {"correct": 9, "total": 1, "errors": 0, "score": 900.0}):
            item["score"] = invalid
            with self.assertRaises(ValueError):
                summary.report(raw())

    def test_verified_import_ignores_bytecode_and_untracked_modules(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "trbench"
            package.mkdir()
            file = package / "__init__.py"
            file.write_bytes(b"answer = 9\n")
            timestamp = int(time.time()) - 100
            os.utime(file, (timestamp, timestamp))
            py_compile.compile(str(file), doraise=True)
            verified = b"answer = 1\n"
            file.write_bytes(verified)
            os.utime(file, (timestamp, timestamp))
            (package / "untracked.py").write_text("answer = 9\n")
            with quality.verified_imports(root, {"trbench/__init__.py": verified}):
                import trbench
                self.assertEqual(trbench.answer, 1)
                with self.assertRaises(ImportError):
                    importlib.import_module("trbench.untracked")
            self.assertNotIn("trbench", sys.modules)

    def test_cli_failures_and_timestamp_never_echo_input(self):
        canary = "SYNTHETIC_PRIVATE_CANARY"
        with self.assertRaisesRegex(ValueError, "^Invalid calibration timestamp$"):
            summary.timestamp(canary)
        for script in ("record-quality-calibration.py", "summarize-quality-calibration.py"):
            proc = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__).with_name(script)),
                                   "--suite", canary], capture_output=True, timeout=10)
            self.assertNotEqual(proc.returncode, 0)
            self.assertNotIn(canary.encode(), proc.stdout + proc.stderr)

    def test_git_diagnostics_never_echo_input(self):
        code = ("import importlib.util,pathlib; "
                "s=importlib.util.spec_from_file_location('quality', 'scripts/record-quality-calibration.py'); "
                "q=importlib.util.module_from_spec(s); s.loader.exec_module(q); "
                "q.source(pathlib.Path('/SYNTHETIC_PRIVATE_PATH_CANARY'),q.REVISION)")
        # The test driver suppresses its own traceback; only child Git could
        # emit the raw path even with the production CLI exception wrapper.
        code = code.replace("q.source(pathlib", "\ntry: q.source(pathlib") + "\nexcept Exception: pass"
        proc = subprocess.run([sys.executable, "-I", "-B", "-c", code],
                              cwd=quality.ROOT, capture_output=True, timeout=10)
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn(b"CANARY", proc.stdout + proc.stderr)

    def test_summary_host_does_not_invent_a_container(self):
        with mock.patch.object(summary.platform, "system", return_value="Darwin"), \
             mock.patch.object(summary.platform, "machine", return_value="arm64"):
            self.assertEqual(summary.summary_host()["machine"], "calibration-summarizer-darwin-arm64")
        with mock.patch.object(summary.platform, "system", return_value="PRIVATE_CANARY"), \
             mock.patch.object(summary.platform, "machine", return_value="PRIVATE_CANARY"):
            self.assertNotIn("CANARY", json.dumps(summary.summary_host()))

    def test_pinned_source_enumeration_cannot_use_empty_index(self):
        with mock.patch.object(quality.subprocess, "check_output", side_effect=[b"a" * 40, b""]) as git:
            with self.assertRaises(ValueError):
                quality.source(Path("/unused"), "a" * 40)
            self.assertIn("ls-tree", git.call_args_list[1].args[0])

    def test_late_response_failure_retains_actual_usage(self):
        for mutate in (lambda r: r["choices"][0].update(finish_reason="content_filter"),
                       lambda r: r["usage"].update(prompt_tokens=21, total_tokens=24)):
            value = reply()
            mutate(value)
            http = types.SimpleNamespace(last_status=None, post=mock.Mock(return_value=value))
            record = quality.evaluate(0, {}, lambda r: "synthetic", mock.Mock(),
                types.SimpleNamespace(count=lambda b: 20), http, b"synthetic", time.monotonic() + 10)
            self.assertEqual(record["status"], "protocol_or_transport_error")
            self.assertIsNotNone(record["usage"])

    def test_interrupted_sent_request_is_unknown_but_tokenizer_failure_is_unsent(self):
        def interrupted(*args, before_send, **kwargs):
            before_send()
            raise KeyboardInterrupt
        http = types.SimpleNamespace(last_status=None, post=mock.Mock(side_effect=interrupted))
        counter = types.SimpleNamespace(count=mock.Mock(return_value=20))
        record = quality.evaluate(0, {}, lambda r: "synthetic", mock.Mock(), counter, http,
                                  b"synthetic", time.monotonic() + 10)
        self.assertEqual((record["status"], record["generation_started"], record["usage"]), ("cancelled", True, None))
        counter.count.side_effect = ValueError("private")
        record = quality.evaluate(0, {}, lambda r: "synthetic", mock.Mock(), counter, http,
                                  b"synthetic", time.monotonic() + 10)
        self.assertFalse(record["generation_started"])

    def test_absolute_deadline_covers_grading(self):
        http = types.SimpleNamespace(last_status=None, post=mock.Mock(return_value=reply()))
        record = quality.evaluate(0, {}, lambda r: "synthetic", lambda *args: time.sleep(0.1),
            types.SimpleNamespace(count=lambda b: 20), http, b"synthetic", time.monotonic() + 0.02)
        self.assertEqual(record["status"], "timeout")
        self.assertLess(record["elapsed_seconds"], 0.09)
        self.assertIsNotNone(record["usage"])

    def test_rejects_wrong_model_counts_and_nontext_outputs(self):
        for mutate in (lambda r: r.update(model="wrong"), lambda r: r["usage"].update(prompt_tokens=21),
                       lambda r: r["usage"].update(completion_tokens=True),
                       lambda r: r["usage"].update(total_tokens=24), lambda r: r.update(choices=[]),
                       lambda r: r["choices"][0]["message"].update(tool_calls=[{}]),
                       lambda r: r["choices"][0]["message"].update(reasoning_content="reasoning")):
            value = reply()
            mutate(value)
            with self.assertRaises(ValueError):
                quality.numeric_reply(value, 20)

    def test_real_scoring_and_truncation_keep_usage_without_content(self):
        for finish in ("stop", "length"):
            value = reply()
            value["choices"][0]["finish_reason"] = finish
            http = types.SimpleNamespace(last_status=500, post=mock.Mock(return_value=value))
            counter = types.SimpleNamespace(count=mock.Mock(return_value=20))
            grade = mock.Mock(return_value={"correct": int(finish == "stop"), "total": 1})
            record = quality.evaluate(0, {}, lambda r: "SYNTHETIC_PROMPT_CANARY", grade, counter,
                                      http, b"SYNTHETIC_KEY_CANARY", time.monotonic() + 10)
            self.assertEqual(record["status"], "evaluated" if finish == "stop" else "truncated")
            self.assertEqual(record["usage"], {"prompt_tokens": 20, "completion_tokens": 3})
            self.assertIsNone(record["http_status"])
            grade.assert_called_once_with({}, "SYNTHETIC_REPLY_CANARY" if finish == "stop" else "")
            http.post.assert_called_once()
            self.assertNotIn("CANARY", json.dumps(record))

    def test_failure_stage_and_usage_remain_honest(self):
        for stage in ("tokenize", "request", "response-validation", "grade"):
            counter = types.SimpleNamespace(count=mock.Mock(return_value=20))
            http = types.SimpleNamespace(last_status=500, post=mock.Mock(return_value=reply()))
            grade = mock.Mock(return_value={"correct": 1})
            if stage == "tokenize": counter.count.side_effect = ValueError("PRIVATE_CANARY")
            if stage == "request": http.post.side_effect = OSError("PRIVATE_CANARY")
            if stage == "response-validation": http.post.return_value = {}
            if stage == "grade": grade.side_effect = ValueError("PRIVATE_CANARY")
            record = quality.evaluate(0, {}, lambda r: "PRIVATE_CANARY", grade, counter, http,
                                      b"PRIVATE_CANARY", time.monotonic() + 10)
            self.assertEqual(record["stage"], stage)
            self.assertEqual(record["status"], "protocol_or_transport_error")
            self.assertEqual(record["usage"] is not None, stage == "grade")
            self.assertNotIn("CANARY", json.dumps(record))
            self.assertLessEqual(http.post.call_count, 1)


class OriginalCalibrationTests(unittest.TestCase):
    def contract(self):
        return {**public_contract(), "schema": quality.quality_candidate.ORIGINAL_ENDPOINT,
                **quality.quality_candidate.original_fields()}

    def response(self):
        value = reply()
        value["model"] = quality.quality_candidate.ORIGINAL_MODEL
        value["usage"]["prompt_tokens_details"] = {"cached_tokens": 12}
        return value

    def raw(self, rows):
        return b"".join(quality.worker.encoded(row) for row in rows)

    def test_official_contract_retains_closed_public_tls_transport(self):
        with mock.patch.object(quality.bounded_https, "system_trust") as trust, \
             mock.patch.object(quality.tau_live_identity, "private_read") as read:
            http, mode = quality.candidate_transport(self.contract(), time.monotonic() + 10)
        self.assertEqual(mode, "system-roots")
        self.assertEqual((http.hostname, http.address, http.port), ("synthetic.invalid", "8.8.8.8", 443))
        self.assertEqual(http.paths, ("/v1/chat/completions",))
        trust.assert_called_once_with()
        read.assert_not_called()

    def test_mixed_revision_identity_and_legacy_contract_rejected_before_transport(self):
        cases = []
        for key, value in (("model", quality.MODEL), ("serving_profile", "long1m-graphs"),
                           ("schema", "nib.quality-calibration-endpoint/v1"),
                           ("schema", "nib.tau-external-trial-endpoint/v2"),
                           ("model_profile", quality.quality_candidate.models.select("uncensored")),
                           ("certificate", "/unused"), ("tls_mode", "pinned")):
            cases.append({**self.contract(), key: value})
        for key in quality.quality_candidate.original_fields():
            contract = self.contract()
            del contract[key]
            cases.append(contract)
        for key in self.contract()["model_profile"]:
            contract = self.contract()
            contract["model_profile"][key] = "wrong"
            cases.append(contract)
        for contract in cases:
            with self.subTest(contract=contract), mock.patch.object(quality.bounded_https, "HTTPS") as transport, \
                 mock.patch.object(quality.tau_live_identity, "private_read") as read:
                with self.assertRaises(ValueError):
                    quality.candidate_transport(contract, time.monotonic() + 10)
                transport.assert_not_called()
                read.assert_not_called()

    def test_original_request_and_response_identity_are_paired(self):
        for response, expected in ((self.response(), "evaluated"), (reply(), "protocol_or_transport_error")):
            http = types.SimpleNamespace(last_status=None, post=mock.Mock(return_value=response))
            grade = mock.Mock(return_value={"correct": 1})
            row = quality.evaluate(0, {}, lambda r: "synthetic", grade,
                types.SimpleNamespace(count=lambda b: 20), http, b"synthetic", time.monotonic() + 10,
                model=quality.quality_candidate.ORIGINAL_MODEL)
            self.assertEqual(row["status"], expected)
            self.assertEqual(http.post.call_args.args[1]["model"], quality.quality_candidate.ORIGINAL_MODEL)
            self.assertEqual(row["usage"], {"prompt_tokens": 20, "completion_tokens": 3})
            self.assertEqual(grade.call_count, int(expected == "evaluated"))
        with self.assertRaises(ValueError):
            quality.numeric_reply(self.response(), 20)
        http.post.reset_mock()
        with self.assertRaises(ValueError):
            quality.evaluate(0, {}, None, None, None, http, b"synthetic", time.monotonic() + 10, model="wrong")
        http.post.assert_not_called()

    def test_original_writer_and_summary_retain_checkpoint_and_usage(self):
        rows = CalibrationTests().writer_receipt(self.contract(), [self.response() for _ in range(20)], exit_code=0)
        self.assertEqual(rows[0]["schema"], quality.quality_candidate.ORIGINAL_RECEIPT)
        record = summary.report(self.raw(rows))
        self.assertEqual(record["schema"], "nib.quality-calibration-summary/v4")
        for value in (rows[0], record):
            for key, expected in quality.quality_candidate.original_fields().items():
                self.assertEqual(value[key], expected)
        self.assertEqual((record["evaluated"], record["correct"], record["known_cached_prompt_tokens"]), (20, 20, 240))
        for flag in ("confidential", "full_benchmark", "matched_reference", "quality_qualified"):
            self.assertIs(record[flag], False)
        self.assertNotIn(b"CANARY", self.raw(rows))

    def test_original_interruption_preserves_unknown_usage_and_identity(self):
        rows = CalibrationTests().writer_receipt(self.contract())
        record = summary.audit_interrupted(self.raw(rows[:2]))
        self.assertEqual(record["schema"], "nib.quality-calibration-interruption/v2")
        self.assertEqual(record["unresolved_intents"], 1)
        self.assertEqual(record["unknown_usage_requests"], 1)
        self.assertEqual(record["unknown_cache_usage_requests"], 1)
        self.assertEqual(record["model_profile"], self.contract()["model_profile"])
        self.assertIs(record["receipt_complete"], False)
        self.assertIs(record["source_authentication_verified"], False)
        with self.assertRaises(ValueError):
            summary.report(self.raw(rows[:2]))

    def test_original_summaries_reject_mixed_identity(self):
        rows = CalibrationTests().writer_receipt(self.contract(), [self.response() for _ in range(20)], exit_code=0)
        for key, value in (("model", quality.MODEL), ("serving_profile", "long1m-graphs"),
                           ("model_profile", quality.quality_candidate.models.select("uncensored"))):
            changed = copy.deepcopy(rows)
            changed[0][key] = value
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    summary.report(self.raw(changed))
                with self.assertRaises(ValueError):
                    summary.audit_interrupted(self.raw(changed[:2]))

    def test_legacy_receipts_are_not_relabelled(self):
        rows = CalibrationTests().writer_receipt(public_contract(), [reply() for _ in range(20)], exit_code=0)
        record = summary.report(self.raw(rows))
        self.assertEqual(record["schema"], "nib.quality-calibration-summary/v3")
        self.assertNotIn("model_profile", record)
        self.assertNotIn("serving_profile", record)

    def test_pinned_tokenizer_files_match_both_model_manifests(self):
        models = quality.quality_candidate.models
        for name in models.NAMES:
            profile = models.select(name)
            raw = (quality.ROOT / profile["pack"] / "files.sha256").read_bytes()
            models.validate_manifest(name, raw)
            rows = {path: (int(size), sha) for sha, size, path in
                    (line.split() for line in raw.decode("ascii").splitlines())}
            for path, expected in quality.tau_external_tokenizer.parity.MODEL_FILES.items():
                self.assertEqual(rows[path], expected)


class InterruptionAuditTests(unittest.TestCase):
    def setUp(self):
        self.rows = CalibrationTests().writer_receipt(
            public_contract(), [reply() for _ in range(20)], exit_code=0)

    def raw(self, rows):
        return b"".join(quality.worker.encoded(row) for row in rows)

    def test_every_intact_prefix_is_audited_without_fabricating_completion(self):
        for end in range(1, len(self.rows)):
            with self.subTest(end=end):
                raw = self.raw(self.rows[:end])
                record = summary.audit_interrupted(raw)
                pending = int(end % 2 == 0)
                items = (end - 1) // 2
                self.assertEqual(record["item_receipts"], items)
                self.assertEqual(record["missing_item_receipts"], 20 - items)
                self.assertEqual(record["possibly_sent_requests"], items + pending)
                self.assertEqual(record["unknown_usage_requests"], pending)
                self.assertEqual(record["unknown_cache_usage_requests"], items + pending)
                self.assertEqual(record["unresolved_intents"], pending)
                self.assertEqual(record["known_prompt_tokens"], items * 20)
                self.assertEqual(record["known_completion_tokens"], items * 3)
                self.assertEqual(record["known_cached_prompt_tokens"], 0)
                self.assertEqual(record["result"], "interrupted")
                self.assertIsNone(record["ended_utc"])
                self.assertEqual(record["source_receipt_sha256"], quality.digest(raw))
                for flag in ("receipt_complete", "source_authentication_verified", "billing_reconciled",
                             "confidential", "full_benchmark", "matched_reference", "quality_qualified"):
                    self.assertIs(record[flag], False)
                with self.assertRaises((ValueError, KeyError)):
                    summary.report(raw)

    def test_known_failed_usage_is_not_erased_or_counted_as_evaluated(self):
        item = self.rows[2]
        item.update(status="protocol_or_transport_error", score=None, cached_prompt_tokens=12)
        record = summary.audit_interrupted(self.raw(self.rows[:4]))
        self.assertEqual((record["evaluated"], record["failed_item_receipts"]), (0, 1))
        self.assertEqual(record["known_prompt_tokens"], 20)
        self.assertEqual(record["known_cached_prompt_tokens"], 12)
        self.assertEqual(record["unknown_usage_requests"], 1)
        self.assertEqual(record["unknown_cache_usage_requests"], 1)

    def test_unsent_item_never_becomes_a_possible_charge(self):
        item = self.rows[2]
        item.update(status="cancelled", score=None, usage=None, cached_prompt_tokens=None,
                    generation_started=False, http_status=None, finish_reason=None)
        record = summary.audit_interrupted(self.raw([self.rows[0], item]))
        self.assertEqual(record["item_receipts"], 1)
        self.assertEqual(record["possibly_sent_requests"], 0)
        self.assertEqual(record["unknown_usage_requests"], 0)
        self.assertEqual(record["unknown_cache_usage_requests"], 0)

    def test_rejects_completed_receipts_and_legacy_without_intents(self):
        with self.assertRaises(ValueError):
            summary.audit_interrupted(self.raw(self.rows))
        for schema in ("nib.quality-calibration/v1", "nib.quality-calibration/v2"):
            self.rows[0]["schema"] = schema
            with self.subTest(schema=schema), self.assertRaises(ValueError):
                summary.audit_interrupted(self.raw(self.rows[:2]))

    def test_rejects_torn_lines_oversized_inputs_and_duplicate_json_keys(self):
        raw = self.raw(self.rows[:2])
        for invalid in (b"", raw[:-1], raw + b'{"kind":', raw + b"\n",
                        raw + b" " * (1 << 20), raw.replace(b'"index":0', b'"index":1,"index":0'),
                        raw.replace(b'"index":0', b'"index":NaN')):
            with self.subTest(size=len(invalid)), self.assertRaises((ValueError, KeyError)):
                summary.audit_interrupted(invalid)

    def test_rejects_missing_duplicate_out_of_order_or_excess_intents(self):
        invalid = [self.rows[:2] + [self.rows[1]], [self.rows[0], self.rows[2]],
                   self.rows[:3] + [dict(self.rows[1], index=2)],
                   self.rows[:-1] + [{"kind": "intent", "index": 20}],
                   self.rows[:1] + [{"kind": "intent", "index": True}],
                   self.rows[:2] + [dict(self.rows[2], generation_started=False)],
                   self.rows[:2] + [self.rows[0]]]
        for rows in invalid:
            with self.subTest(kinds=[r["kind"] for r in rows]), self.assertRaises(ValueError):
                summary.audit_interrupted(self.raw(rows))

    def test_shared_validation_rejects_invalid_usage_score_and_provenance(self):
        for index, field, invalid in ((0, "git_sha", "bad"), (0, "expected", True),
                                      (0, "started_at", "bad"), (2, "cached_prompt_tokens", 21),
                                      (2, "usage", None), (2, "score", None),
                                      (2, "elapsed_seconds", -1), (2, "http_status", 503)):
            rows = copy.deepcopy(self.rows[:3])
            rows[index][field] = invalid
            with self.subTest(field=field), self.assertRaises(ValueError):
                summary.audit_interrupted(self.raw(rows))

    def test_cli_is_read_only_and_never_echoes_arbitrary_input(self):
        self.rows[0]["ignored"] = "SYNTHETIC_PRIVATE_CANARY"
        raw = self.raw(self.rows[:4])
        script = Path(__file__).with_name("audit-quality-interruption.py")
        proc = subprocess.run([sys.executable, "-I", "-S", "-B", str(script)],
                              input=raw, capture_output=True, timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), summary.audit_interrupted(raw))
        self.assertNotIn(b"CANARY", proc.stdout + proc.stderr)
        proc = subprocess.run([sys.executable, "-I", "-S", "-B", str(script)],
                              input=b"SYNTHETIC_PRIVATE_CANARY", capture_output=True, timeout=10)
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn(b"CANARY", proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
