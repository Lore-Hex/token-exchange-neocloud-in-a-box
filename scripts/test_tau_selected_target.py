# SPDX-License-Identifier: Apache-2.0
"""Selected-target admission with synthetic receipts and fake loopback APIs only."""

import copy
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import test_tau_aggregate as aggregation
import test_tau_live_trial as fixtures

live, tau, worker, runner = fixtures.live, fixtures.tau, fixtures.worker, fixtures.runner


class SelectedTargetTests(unittest.TestCase):
    def test_only_three_original_receipt_pairs_are_read_and_checked(self):
        for target in ("candidate", "reference"):
            benchmark, docs = fixtures.fixture_documents(selected_target=target)
            original = copy.deepcopy(benchmark)
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temp, \
                    mock.patch.object(live, "source_receipt") as source:
                root = Path(temp).resolve()
                sha = fixtures.write_bundle(root, docs)
                with mock.patch.object(live, "private_read", wraps=live.private_read) as read:
                    deployment = live.Deployment(root / "deployment.json", sha, benchmark, time.monotonic() + 5)
                self.assertEqual(read.call_count, 7)
                self.assertEqual(source.call_count, 6)
                active = {target, "simulator", "evaluator"}
                self.assertEqual(set(deployment.endpoints), active)
                self.assertEqual(set(deployment.binding["receipts"]), active)
                self.assertEqual(deployment.binding["schema"], "nib.tau-live-binding/v2")
                self.assertEqual(deployment.binding["selected_target"], target)
                self.assertEqual(benchmark, original)
                self.assertEqual(set(deployment.manifest["settings"]["roles"]), set(tau.ROLES))
                self.assertEqual(set(deployment.manifest["settings"]["serving"]), {"candidate", "reference"})
                with mock.patch.object(live.Endpoint, "check") as check:
                    deployment.check(time.monotonic() + 5)
                    self.assertEqual(check.call_count, 3)
                session = deployment.session(mock.Mock(), target, [])
                try:
                    self.assertEqual(set(session.endpoints), active)
                    self.assertEqual(session.bridge.budget.limits, benchmark["settings"]["limits"])
                    other = "reference" if target == "candidate" else "candidate"
                    with self.assertRaises(worker.Abort):
                        session.prepare_record(other, {}, time.monotonic() + 5)
                finally:
                    session.close()
                for secret in (str(root), '"port"', '"pid"', '"model"'):
                    self.assertNotIn(secret, tau.encoded(deployment.binding).decode())

    def test_selected_target_is_explicit_and_exact_role_set_is_required(self):
        for change in ("missing-target", "bad-target", "missing-simulator", "extra-reference", "legacy-schema"):
            benchmark, docs = fixtures.fixture_documents(selected_target="candidate")
            row = docs["deployment.json"]
            if change == "missing-target":
                del row["selected_target"]
            elif change == "bad-target":
                row["selected_target"] = "simulator"
            elif change == "missing-simulator":
                del row["roles"]["simulator"]
            elif change == "extra-reference":
                _, legacy = fixtures.fixture_documents()
                row["roles"]["reference"] = legacy["deployment.json"]["roles"]["reference"]
                docs.update({name: legacy[name] for name in ("reference-runtime.json", "reference-tokenizer.json")})
            else:
                row["schema"] = "nib.tau-local-deployment/v1"
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp, \
                    mock.patch.object(live, "source_receipt") as source:
                root = Path(temp).resolve()
                sha = fixtures.write_bundle(root, docs)
                with self.assertRaises(worker.Abort):
                    live.Deployment(root / "deployment.json", sha, benchmark, time.monotonic() + 5)
                source.assert_not_called()

    def test_inactive_reference_configuration_cannot_be_missing_or_drift(self):
        for change in ("missing", "revision", "generation", "serving"):
            benchmark, docs = fixtures.fixture_documents(selected_target="candidate")
            config = benchmark["settings"]
            if change == "missing":
                del config["roles"]["reference"]
            elif change == "revision":
                config["roles"]["reference"]["revision"] = "f" * 40
            elif change == "generation":
                config["roles"]["reference"]["generation"]["max_tokens"] += 1
            else:
                del config["serving"]["reference"]
            docs["deployment.json"]["manifest"]["benchmark_manifest_sha256"] = tau.digest(tau.encoded(benchmark))
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                root = Path(temp).resolve()
                sha = fixtures.write_bundle(root, docs)
                with self.assertRaises((worker.Abort, ValueError)):
                    live.Deployment(root / "deployment.json", sha, benchmark, time.monotonic() + 5)

    def test_other_target_is_rejected_before_session_scorer_or_docker_start(self):
        benchmark, docs = fixtures.fixture_documents(selected_target="candidate")
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(live, "source_receipt"):
            root = Path(temp).resolve()
            sha = fixtures.write_bundle(root, docs)
            deployment = live.Deployment(root / "deployment.json", sha, benchmark, time.monotonic() + 5)
            plan = fixtures.plan_for(benchmark, "reference")
            with mock.patch.object(live.Endpoint, "check") as check, \
                    mock.patch.object(fixtures.driver.subprocess, "Popen") as start:
                with self.assertRaises(worker.Abort):
                    deployment.session(mock.Mock(), "reference", [])
                with self.assertRaises(worker.Abort):
                    fixtures.driver.run_process([], {}, root, plan, benchmark, time.monotonic() + 5,
                                                deployment=deployment)
                check.assert_not_called()
                start.assert_not_called()
            with mock.patch.object(runner, "identity", return_value={}), \
                    mock.patch.object(runner, "validate_plan", return_value=(plan, benchmark)), \
                    mock.patch.object(runner, "parent_driver", return_value=types.SimpleNamespace(live=live)), \
                    mock.patch.object(runner.probe, "capture") as capture:
                with self.assertRaises(worker.Abort):
                    runner.run(root, "e" * 64, root / "output", root / "unused.sock", parent_pipe=True,
                               live_deployment=(root / "deployment.json", sha))
                capture.assert_not_called()

    def test_reference_cannot_relabel_candidate_receipt_or_reuse_support_weights(self):
        for change in ("role", "weights", "pid", "listener", "port", "tokenizer"):
            benchmark, docs = fixtures.fixture_documents(selected_target="reference")
            runtime = docs["reference-runtime.json"]
            if change == "role":
                runtime["role"] = "candidate"
            elif change == "weights":
                other = docs["evaluator-runtime.json"]
                other["model_files_sha256"] = runtime["model_files_sha256"]
                other["measurements"]["model_files_sha256"] = copy.deepcopy(runtime["measurements"]["model_files_sha256"])
            elif change in ("pid", "listener"):
                key = "pid" if change == "pid" else "listener_inode"
                runtime["process"][key] = docs["simulator-runtime.json"]["process"][key]
            elif change == "port":
                port = docs["deployment.json"]["roles"]["simulator"]["port"]
                docs["deployment.json"]["roles"]["reference"]["port"] = port
                runtime["endpoint"]["port"] = port
            else:
                docs["reference-tokenizer.json"]["cases"]["tool-history"]["endpoint_tokens_sha256"] = "e" * 64
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp, \
                    mock.patch.object(live, "source_receipt"):
                root = Path(temp).resolve()
                sha = fixtures.write_bundle(root, docs)
                with self.assertRaises(worker.Abort):
                    live.Deployment(root / "deployment.json", sha, benchmark, time.monotonic() + 5)

    def test_prepare_rejects_other_target_before_building_payload(self):
        benchmark, docs = fixtures.fixture_documents(selected_target="candidate")
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(live, "source_receipt"):
            root = Path(temp).resolve()
            sha = fixtures.write_bundle(root, docs)
            manifest = root / "benchmark.json"
            manifest.write_bytes(tau.encoded(benchmark))
            manifest.chmod(0o600)
            argv = ["tau_live_trial.py", "prepare", "--source-archive", str(root / "unused.tar.gz"),
                    "--wheelhouse", str(root / "unused-wheels"), "--manifest", str(manifest),
                    "--manifest-sha256", tau.digest(tau.encoded(benchmark)), "--domain", "airline",
                    "--task-id", "0", "--trial", "0", "--role", "reference",
                    "--deployment", str(root / "deployment.json"), "--deployment-sha256", sha,
                    "--output", str(root / "output")]
            with mock.patch.object(fixtures.cli.sys, "argv", argv), \
                    mock.patch.object(fixtures.cli, "runner_module", return_value=runner), \
                    mock.patch.object(runner, "identity", return_value={}), \
                    mock.patch.object(runner, "parent_driver", return_value=types.SimpleNamespace(live=live)), \
                    mock.patch.object(runner, "prepare") as prepare, mock.patch.object(runner, "publish") as publish:
                with self.assertRaises(worker.Abort):
                    fixtures.cli.main()
                prepare.assert_not_called()
                publish.assert_not_called()

    def test_three_fake_endpoints_execute_either_target_with_its_own_attribution(self):
        for target in ("candidate", "reference"):
            with self.subTest(target=target):
                result, requests = fixtures.LivePipeTests().exercise(selected_target=target)
                self.assertEqual(set(requests), {target, "simulator", "evaluator"})
                self.assertEqual(result["report"]["role"], target)
                self.assertEqual(result["report"]["trials"][0]["status"], "evaluated")
                self.assertEqual(result["accounting"]["role_requests"], {"agent": 1, "simulator": 1, "evaluator": 1})
                self.assertFalse(result["quality_accepted"])
                for observed in requests.values():
                    self.assertEqual([path for path, _ in observed], ["/tokenize", "/v1/chat/completions"])

    def test_selected_target_failure_preserves_usage_and_prevents_success(self):
        for failure in ("unknown-usage", "truncated", "child-exit"):
            with self.subTest(failure=failure):
                result, requests = fixtures.LivePipeTests().exercise(failure, selected_target="reference")
                row = result["report"]["trials"][0]
                self.assertNotEqual(row["status"], "evaluated")
                self.assertIsNone(row["reward"])
                self.assertEqual(result["report"]["role"], "reference")
                self.assertGreater(result["accounting"]["reserved_tokens"], 0)
                if failure == "unknown-usage":
                    self.assertIsNone(row["usage"])
                    self.assertFalse(result["accounting"]["usage_known"])
                    self.assertEqual(requests["simulator"], [])

    def test_candidate_only_results_still_cannot_satisfy_paired_coverage(self):
        benchmark = aggregation.manifest("comparison")
        reports = aggregation.reports(benchmark)
        with self.assertRaises(ValueError):
            aggregation.summarize(benchmark, {"candidate": reports["candidate"]})
        reports["reference"]["trials"] = []
        summary = aggregation.summarize(benchmark, reports)
        self.assertEqual(summary["status"], "incomplete")
        self.assertFalse(summary["quality_accepted"] or summary["evaluated_coverage_complete"])
        self.assertTrue(all(domain["paired"] is None for domain in summary["domains"].values()))


if __name__ == "__main__":
    unittest.main()
