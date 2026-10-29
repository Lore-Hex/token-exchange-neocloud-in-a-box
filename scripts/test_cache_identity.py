# SPDX-License-Identifier: Apache-2.0
"""Closed official cache identity over synthetic tokenizers and in-memory HTTP."""

import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import time
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import Mock, patch

import cache_complete as complete
import cache_identity as identity
import cache_journal as journal
import cache_probe as probe
import cache_workload as workload
import test_cache_probe as legacy
import test_cache_stream_integration as integration
import test_stream_https as wire


class IdentityTests(unittest.TestCase):
    def preparation(self, candidate="original", binding=None):
        binding = sorted(identity.source_bindings())[0] if binding is None else binding
        bodies = []

        def tokens(body):
            bodies.append(body)
            return [7] * (body["messages"][0]["content"].count(" x") + 40)

        counter = types.SimpleNamespace(binding=binding, tokens=tokens, close=Mock())
        with patch.object(workload.tokenizer, "Counter", return_value=counter) as factory:
            try:
                row = workload.prepare("source", "model", "wheel", candidate=candidate,
                                       deadline=time.monotonic() + 20)
            finally:
                counter.close.assert_called_once_with()
        factory.assert_called_once_with("source", "model", "wheel", context_tokens=262144)
        self.assertTrue(all(body["model"] == identity.model(candidate) for body in bodies))
        return row

    def stream(self, cached=0, *, model=None):
        model = identity.model("original") if model is None else model
        return wire.data(cached).replace(probe.stream.transport.SELECTED_MODEL.encode(), model.encode())

    def run_original(self, *, preparation=None, responses=None, emit=None, candidate="original"):
        preparation = self.preparation() if preparation is None else preparation
        responses = ([wire.response(self.stream(1024 if index % 3 == 1 else 0)) for index in range(9)]
                     if responses is None else responses)
        client, rows, connections = integration.CacheStreamTests().fixture(responses, candidate=candidate)
        result = probe.run(client, wire.KEY, padding=2008, prompt_tokens=2048,
            run_id="a" * 64, deadline=time.monotonic() + 20, emit=rows.append if emit is None else emit,
            check_runtime=lambda: None, candidate="original", preparation=preparation)
        return result, rows, connections

    def encoded(self, rows):
        return b"".join(probe.worker.encoded(row) for row in rows)

    def test_official_preparation_binds_closed_model_and_pinned_sources(self):
        row = self.preparation()
        self.assertEqual(row["schema"], "nib.cache-workload/v2")
        self.assertEqual(identity.validate_preparation(row), row)
        for key, value in identity.quality_candidate.original_fields().items():
            self.assertEqual(row[key], value)
        base = row["tokenizer_source_binding_sha256"]
        self.assertNotEqual(row["tokenizer_binding_sha256"], base)
        self.assertEqual(row["tokenizer_binding_sha256"], probe.worker.digest(probe.worker.encoded({
            **identity.fields("original"), "tokenizer_source_binding_sha256": base})))
        self.assertNotIn("source", row)
        for flag in identity.PREPARATION_FLAGS:
            self.assertIs(row[flag], False)

    def test_legacy_preparation_and_wire_bytes_keep_their_contract(self):
        row = self.preparation("selected", binding="a" * 64)
        expected = {"schema": "nib.cache-workload/v1", "tokenizer_binding_sha256": "a" * 64,
                    "cases": [], **dict.fromkeys(identity.PREPARATION_FLAGS, False)}
        for prompt, output in probe.SHAPES.items():
            expected["cases"].append({"padding": prompt - 40, "prompt_tokens": prompt,
                "max_output_tokens": output, "tokens_sha256": probe.workload.digest([7] * prompt),
                "messages_sha256": probe.workload.digest(probe.workload.messages(3, prompt - 40))})
        self.assertEqual(probe.worker.encoded(row), probe.worker.encoded(expected))
        body = probe.request(2008, 2048, "a" * 64)
        self.assertEqual(body["model"], probe.stream.transport.SELECTED_MODEL)
        self.assertEqual(probe.stream.request_bytes(body, 2048), probe.worker.encoded(body))
        self.assertEqual(body, probe.request(2008, 2048, "a" * 64, candidate="selected"))

    def test_legacy_journal_and_audit_bytes_match_the_base_commit(self):
        # Captured from e3fad32fbebb674518c59a34fda2cc2ea5b13933, synthetic C1 only.
        fixture = legacy.ProbeTests()
        client, rows, _ = fixture.setup_run()
        fixture.run_probe(client, rows, clock=lambda: 100)
        rows[-1]["elapsed_seconds"] = 10
        encode = lambda value: json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
        raw = b"".join(encode(row) + b"\n" for row in rows)
        prefix = b"".join(encode(row) + b"\n" for row in rows[:-1])
        for value, size, digest in (
                (raw, 6631, "7a77086bbe6ce38aa4eb75276f218c3440d0bcd050d169b53a929d3bf2888f35"),
                (encode(journal.audit(prefix)), 921, "802a720c29b23f7880f576eb9d6d13b7b4cc4786ca8a5ed7b718025466227cf6"),
                (encode(complete.audit(raw)), 984, "7ba99314f6895325d6104ecfd51ce2afcf57526394c57f0c4dc5ac7bf6fcac51")):
            self.assertEqual(len(value), size)
            self.assertEqual(hashlib.sha256(value).hexdigest(), digest)

    def test_official_sequence_and_every_prefix_audit_keep_identity_and_source(self):
        preparation = self.preparation()
        result, rows, connections = self.run_original(preparation=preparation)
        binding = identity.evidence(preparation, 2048)
        self.assertTrue(result["completed"])
        self.assertEqual(rows[0]["prepared_workload"], preparation)
        for row in rows:
            self.assertEqual(row["schema"], "nib.cache-probe/v2")
            for key, value in binding.items():
                self.assertEqual(row[key], value)
        for key, value in binding.items():
            self.assertEqual(result[key], value)
        reports = [journal.audit(self.encoded(rows[:length])) for length in range(1, len(rows))]
        for length, report in enumerate(reports, 1):
            self.assertEqual(report["schema"], "nib.cache-journal-audit/v2")
            self.assertEqual(report["source_sha256"], hashlib.sha256(self.encoded(rows[:length])).hexdigest())
            self.assertEqual(report["source_bytes"], len(self.encoded(rows[:length])))
            self.assertEqual(report["orphaned_intents"], int(length % 2 == 0))
        report = complete.audit(self.encoded(rows))
        self.assertEqual(report["schema"], "nib.cache-complete-audit/v2")
        self.assertEqual(report["source_sha256"], hashlib.sha256(self.encoded(rows)).hexdigest())
        self.assertEqual(report["source_bytes"], len(self.encoded(rows)))
        self.assertTrue(report["recorded_sequence_completed"])
        for report in [*reports, report]:
            for key, value in binding.items():
                self.assertEqual(report[key], value)
            for flag in ("source_authenticated", "billing_reconciled", "accounting_holds_released",
                         "cache_isolation_qualified", "quality_qualified", "confidential", "tariff_change_justified"):
                self.assertIs(report[flag], False)
        bodies = [json.loads(connection.sent[0].split(b"\r\n\r\n", 1)[1]) for connection in connections]
        self.assertTrue(all(body["model"] == identity.model("original") for body in bodies))
        self.assertEqual(len({body["cache_salt"] for body in bodies}), 6)
        for canary in (wire.KEY.decode(), wire.CANARY, "CEDAR42", "candidate.example.test"):
            self.assertNotIn(canary, self.encoded(rows).decode())

    def test_wrong_request_model_rejects_before_intent_or_connection(self):
        for candidate in ("selected", "original"):
            client, _, connections = integration.CacheStreamTests().fixture([], candidate=candidate)
            wrong = "original" if candidate == "selected" else "selected"
            body = probe.request(2008, 2048, "a" * 64, candidate=wrong)
            intent = Mock()
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                client.collect(body, wire.KEY, prompt_tokens=2048,
                    deadline=time.monotonic() + 10, before_send=intent)
            intent.assert_not_called()
            self.assertEqual(connections, [])
            self.assertEqual(client.attempts[probe.stream.transport.PATH], 0)

    def test_each_response_event_requires_the_selected_candidate_model(self):
        for candidate in ("selected", "original"):
            correct = identity.model(candidate)
            wrong = identity.model("original" if candidate == "selected" else "selected")
            for index in range(3):
                events = self.stream(model=correct).split(b"\n\n")
                events[index] = events[index].replace(correct.encode(), wrong.encode())
                client, _, _ = integration.CacheStreamTests().fixture(
                    [wire.response(b"\n\n".join(events))], candidate=candidate)
                sample = client.collect(probe.request(2008, 2048, "a" * 64, candidate=candidate), wire.KEY,
                    prompt_tokens=2048, deadline=time.monotonic() + 10, before_send=lambda: None)
                self.assertEqual(sample.status, "protocol-error")
                self.assertEqual(client.attempts[probe.stream.transport.PATH], 1)

    def test_no_arbitrary_candidate_or_model_override(self):
        for candidate in (None, True, "other", identity.model("original"), "uncensored"):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                probe.request(2008, 2048, "a" * 64, candidate=candidate)
            with patch.object(probe.stream.bounded_https, "system_trust") as trust, self.assertRaises(ValueError):
                probe.stream.SelectedHTTPS("candidate.example.test", address="8.8.8.8", candidate=candidate)
            trust.assert_not_called()
            with patch.object(workload.tokenizer, "Counter") as counter, self.assertRaises(ValueError):
                workload.prepare("source", "model", "wheel", deadline=time.monotonic() + 10, candidate=candidate)
            counter.assert_not_called()

    def test_preparation_mismatch_rejects_before_manifest_or_send(self):
        original = self.preparation()
        bad = [None, self.preparation("selected")]
        for key, value in (("model", identity.model()), ("serving_profile", "other"),
                           ("model_profile", identity.quality_candidate.models.select("uncensored")),
                           ("tokenizer_source_binding_sha256", "f" * 64),
                           ("tokenizer_binding_sha256", "f" * 64)):
            bad.append({**original, key: value})
        changed = copy.deepcopy(original)
        changed["cases"][0]["padding"] += 1
        changed["cases"][0]["messages_sha256"] = probe.workload.digest(probe.workload.messages(3, 2009))
        bad.append(changed)
        for preparation in bad:
            client, rows, connections = integration.CacheStreamTests().fixture([], candidate="original")
            with self.subTest(preparation=preparation), self.assertRaises(ValueError):
                probe.run(client, wire.KEY, padding=2008, prompt_tokens=2048, run_id="a" * 64,
                    deadline=time.monotonic() + 10, emit=rows.append, check_runtime=lambda: None,
                    candidate="original", preparation=preparation)
            self.assertEqual(rows, [])
            self.assertEqual(connections, [])
        with self.assertRaises(ValueError): self.run_original(candidate="selected")

    def test_preparation_rejects_missing_extra_and_noncanonical_fields(self):
        original = self.preparation()
        for key in original:
            row = copy.deepcopy(original)
            del row[key]
            with self.subTest(missing=key), self.assertRaises(ValueError): identity.validate_preparation(row)
        mutations = [lambda row: row.update(private_content="PRIVATE_CANARY"),
                     lambda row: row.update(inference_executed=0),
                     lambda row: row["model_profile"].update(total_bytes=510311045295.0),
                     lambda row: row["cases"].reverse(),
                     lambda row: row["cases"][0].update(prompt_tokens=2048.0),
                     lambda row: row["cases"][0].update(padding=True),
                     lambda row: row["cases"][0].update(max_output_tokens=512.0),
                     lambda row: row["cases"][0].update(tokens_sha256="not-a-hash"),
                     lambda row: row["cases"][0].update(messages_sha256="f" * 64)]
        for mutate in mutations:
            row = copy.deepcopy(original)
            mutate(row)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError): identity.validate_preparation(row)

    def test_mixed_model_schema_and_source_histories_are_rejected(self):
        _, original, _ = self.run_original()
        for index in (0, 1, 2, -1):
            for key, value in (("schema", "nib.cache-probe/v1"), ("model", identity.model()),
                               ("serving_profile", "other"),
                               ("model_profile", identity.quality_candidate.models.select("uncensored")),
                               ("workload_sha256", "f" * 64), ("tokens_sha256", "f" * 64),
                               ("tokenizer_binding_sha256", "f" * 64),
                               ("tokenizer_source_binding_sha256", "f" * 64)):
                rows = copy.deepcopy(original)
                rows[index][key] = value
                with self.subTest(index=index, key=key), self.assertRaises(ValueError):
                    complete.audit(self.encoded(rows))
                if index != -1:
                    with self.assertRaises(ValueError): journal.audit(self.encoded(rows[:-1]))
        for key in identity.evidence(original[0]["prepared_workload"], 2048):
            rows = copy.deepcopy(original)
            del rows[1][key]
            with self.subTest(missing=key), self.assertRaises(ValueError): complete.audit(self.encoded(rows))
        rows = copy.deepcopy(original)
        rows[0]["prepared_workload"]["cases"][0]["tokens_sha256"] = "e" * 64
        with self.assertRaises(ValueError): complete.audit(self.encoded(rows))
        rows = copy.deepcopy(original)
        rows[0]["messages_sha256"] = "e" * 64
        with self.assertRaises(ValueError): journal.audit(self.encoded(rows[:-1]))

    def test_legacy_journal_cannot_be_relabelled_or_mixed_with_official_records(self):
        fixture = integration.CacheStreamTests()
        client, rows, _ = fixture.fixture([wire.response(wire.data(0))] * 9)
        fixture.run_probe(client, rows)
        self.assertEqual(complete.audit(self.encoded(rows))["schema"], "nib.cache-complete-audit/v1")
        for index in (0, 1, 2, -1):
            changed = copy.deepcopy(rows)
            changed[index].update(identity.fields("original"))
            with self.subTest(index=index), self.assertRaises(ValueError): complete.audit(self.encoded(changed))
        for row in rows:
            row["schema"] = "nib.cache-probe/v2"
        with self.assertRaises(ValueError): complete.audit(self.encoded(rows))

    def test_official_failure_keeps_bound_orphan_and_unknown_usage(self):
        def fail_result(row):
            if row["kind"] == "result":
                raise OSError("private receipt failure")
            rows.append(row)
        rows = []
        with self.assertRaises(OSError): self.run_original(emit=fail_result)
        report = journal.audit(self.encoded(rows))
        self.assertEqual(report["orphaned_intents"], 1)
        self.assertEqual(report["unknown_usage_requests"], 1)
        self.assertEqual(report["model"], identity.model("original"))
        result, rows, connections = self.run_original(responses=[wire.response(status=b"503 Unavailable")])
        self.assertEqual(result["stop_reason"], "request-failed")
        self.assertEqual(len(connections), 1)
        self.assertEqual(journal.audit(self.encoded(rows[:-1]))["unknown_usage_requests"], 1)

    def test_tokenizer_files_are_pinned_by_both_manifests_not_inferred_from_model_name(self):
        root = Path(__file__).resolve().parents[1]
        for candidate in identity.quality_candidate.models.NAMES:
            profile = identity.quality_candidate.models.select(candidate)
            raw = (root / profile["pack"] / "files.sha256").read_bytes()
            identity.quality_candidate.models.validate_manifest(candidate, raw)
            files = {name: (int(size), sha) for sha, size, name in
                     (line.split() for line in raw.decode().splitlines())}
            for name, pin in workload.tokenizer.parity.MODEL_FILES.items():
                self.assertEqual(files[name], pin)
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(workload.tokenizer.platform, "system", return_value="Linux"), \
                patch.object(workload.tokenizer.platform, "machine", return_value="x86_64"):
            path = Path(directory) / "unverified.whl"
            path.write_bytes(b"not the pinned wheel")
            with self.assertRaises(ValueError):
                workload.prepare(directory, directory, str(path), candidate="original",
                                 deadline=time.monotonic() + 10)
        with self.assertRaises(ValueError): self.preparation(binding="f" * 64)

    def test_original_cli_mode_is_explicit_and_errors_remain_private(self):
        args = ["--vllm-source", "source", "--tokenizer", "model", "--tokenizer-wheel", "wheel"]
        for candidate, status in (("original", 0), ("arbitrary-private-value", 1)):
            out, err = io.StringIO(), io.StringIO()
            with patch.object(workload, "prepare", return_value={}) as prepare, \
                    redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(workload.main([*args, "--candidate", candidate]), status)
            if status == 0:
                self.assertEqual(prepare.call_args.kwargs["candidate"], "original")
            else:
                prepare.assert_not_called()
                self.assertNotIn(candidate, out.getvalue() + err.getvalue())


if __name__ == "__main__":
    unittest.main()
