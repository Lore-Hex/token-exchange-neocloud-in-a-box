# SPDX-License-Identifier: Apache-2.0
"""Offline orchestration and failure receipts; no inference, credentials or HTTP."""

import ast
import copy
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import signal
import socket
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

import tau_telecom_offline as offline
import test_tau_telecom_catalog as fixtures

spec = importlib.util.spec_from_file_location("offline_recorder", Path(__file__).with_name("record-tau-telecom-offline.py"))
recorder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recorder)
external, worker = offline.external, offline.worker
ARCHIVE = Path("/synthetic/archive")


def request():
    body = fixtures.body()
    body["messages"] = [{"role": "system", "content": "PRIVATE_PROMPT_CANARY"},
                        {"role": "user", "content": "PRIVATE_USER_CANARY"}]
    return body


def following(body, response):
    body = copy.deepcopy(body)
    message = response["choices"][0]["message"]
    body["messages"].append(copy.deepcopy(message))
    body["messages"].append({"role": "tool", "tool_call_id": message["tool_calls"][0]["id"],
                             "content": "PRIVATE_TOOL_RESULT_CANARY"})
    return body


class OfflineTests(unittest.TestCase):
    def test_authenticated_pair_and_real_accounting_without_http(self):
        with fixtures.bound_fixture() as contract, mock.patch.object(external.bounded_https, "HTTPS") as http:
            held = offline.Offline(contract["stage"], ARCHIVE, [], time.monotonic() + 30)
            endpoint = held.endpoints["simulator"]
            body = request()
            endpoint.count(body, 32768, time.monotonic() + 10)
            first = endpoint.complete(body, offline.TOKENS, time.monotonic() + 10)
            self.assertEqual(first["choices"][0]["message"]["tool_calls"][0]["function"]["name"], "check_status_bar")
            body = following(body, first)
            endpoint.count(body, 32768, time.monotonic() + 10)
            endpoint.complete(body, offline.TOKENS, time.monotonic() + 10)
            self.assertEqual(held.observation()["simulator"], {"generation_steps": 2, "synthetic_count_calls": 2, "tool_round_trips": 1})
            row = endpoint.observation()
            self.assertEqual(row["known_usage"], {"prompt_tokens": 200, "completion_tokens": 2, "total_tokens": 202})
            self.assertEqual(row["unknown_responses"], 0)
            self.assertEqual(row["count_cost_reserved_usd"], "4.190304")
            self.assertEqual(endpoint.count_budget.attempts, 2)
            http.assert_not_called()
            held.close()
            self.assertIsNone(endpoint.ready)
            self.assertIsNone(endpoint.http.pair)

    def test_complete_rejects_request_change_and_usage_mismatch_is_retained(self):
        with fixtures.bound_fixture() as contract:
            for mutation in ("body", "prompt", "usage"):
                endpoint = offline.Simulator(contract, ARCHIVE, [], external.CountBudget())
                body = request()
                endpoint.count(body, 32768, time.monotonic() + 10)
                if mutation == "body":
                    body["messages"][1]["content"] += "changed"
                if mutation == "usage":
                    original = endpoint.http.post
                    def changed(*args, **kwargs):
                        result = original(*args, **kwargs)
                        result["usage"].update(prompt_tokens=101, total_tokens=102)
                        return result
                    endpoint.http.post = changed
                with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                    endpoint.complete(body, 101 if mutation == "prompt" else 100, time.monotonic() + 10)
                self.assertEqual(endpoint.attempts, 1 if mutation == "usage" else 0)
                if mutation == "usage":
                    self.assertEqual(endpoint.observation()["known_usage"]["prompt_tokens"], 101)
                    self.assertEqual(endpoint.unknown, 0)

    def test_source_catalog_budget_deadline_and_caps_reject_before_generation(self):
        with fixtures.bound_fixture() as contract:
            for mutation in ("catalog", "deadline", "count_cap", "pending", "source", "context"):
                endpoint = offline.Simulator(copy.deepcopy(contract), ARCHIVE, [], external.CountBudget())
                body = request()
                if mutation == "catalog": body["tools"].reverse()
                if mutation == "count_cap": endpoint.count_budget.attempts = 32
                if mutation == "pending": endpoint.ready = ("a", {}, 100)
                if mutation == "source": endpoint.contract["source_tree"] = "0" * 40
                with self.subTest(mutation=mutation), self.assertRaises((ValueError, TimeoutError)):
                    endpoint.count(body, 8192 if mutation == "context" else 32768,
                                   time.monotonic() + (-1 if mutation == "deadline" else 10))
                self.assertEqual(endpoint.attempts, 0)
            bad = copy.deepcopy(contract["stage"])
            bad["plan"]["budget"]["inputs"]["other_committed_usd"] = "10000"
            with self.assertRaises(ValueError): offline.Offline(bad, ARCHIVE, [], time.monotonic() + 10)

    def test_shared_candidate_and_live_simulator_stay_closed(self):
        body = request()
        with self.assertRaises(ValueError): external.simulator_request(body, full_task=True, support_chat=True)
        body["model"] = external.policy.PUBLIC_MODEL
        with self.assertRaises(ValueError): external.candidate_request(body, full_task=True)
        self.assertEqual(worker.MAX_TOOL_CALLS, 16)

    def test_scripted_peer_requires_round_trip_and_never_accepts_key_or_route(self):
        peer = offline.Peer("candidate", [])
        wire = {"model": external.policy.PUBLIC_MODEL, "messages": [], "tools": [{"function": {"name": "get_customer_by_name"}}]}
        for route, key in ((external.CHAT, b"PRIVATE_KEY_CANARY"), ("https://example.test", b"")):
            with self.assertRaises(ValueError): peer.post(route, wire, key, time.monotonic() + 10, before_send=lambda: None)
        peer.post(external.CHAT, wire, b"", time.monotonic() + 10, before_send=lambda: None)
        wire["messages"] = [{"role": "user", "content": "not a tool result"}]
        with self.assertRaises(ValueError): peer.post(external.CHAT, wire, b"", time.monotonic() + 10, before_send=lambda: None)

    def test_parent_manifest_keeps_original_stage_limits_and_synthetic_binding(self):
        with fixtures.bound_fixture() as contract:
            held = offline.Offline(contract["stage"], ARCHIVE, [], time.monotonic() + 10)
            config = external.policy.configuration(full_task=True, external_simulator=True, stage=contract["stage"])
            self.assertEqual(held.manifest["input"], config)
            self.assertEqual(config["limits"]["cost_usd"], "3.00")
            self.assertEqual(config["evaluation"]["type"], "all_with_nl_assertions")
            self.assertIs(held.endpoints["evaluator"].count_budget, held.endpoints["simulator"].count_budget)
            self.assertIs(held.endpoints["candidate"].count_budget, held.endpoints["simulator"].count_budget)
            self.assertEqual(held.manifest["framing"]["candidate"]["framing_sha256"], offline.BINDING)
            self.assertNotIn("PRIVATE", json.dumps(held.observation()))


class ReceiptTests(unittest.TestCase):
    def test_exact_helper_closure_ignores_ambient_modules_and_rejects_wrong_hash(self):
        trial = fixtures.containers.runner
        provenance = {"files": {name: worker.digest((trial.ROOT / name).read_bytes()) for name in recorder.HELPERS}}
        owned = types.SimpleNamespace(parent_driver=mock.Mock(return_value=external), runtime=fixtures.catalog.runtime,
                                      load=trial.load)
        with mock.patch.dict(sys.modules, {"tau_telecom_catalog": types.SimpleNamespace(untrusted=True)}):
            held = recorder.driver(owned, {}, provenance)
        self.assertFalse(hasattr(held.catalog, "untrusted"))
        self.assertEqual(held.catalog.CATALOG_SHA, fixtures.catalog.CATALOG_SHA)
        provenance["files"]["scripts/tau_telecom_offline.py"] = "0" * 64
        with self.assertRaises(ValueError): recorder.driver(owned, {}, provenance)

    def test_real_recorder_lifecycle_reaps_only_owned_name_and_keeps_failure(self):
        trial = fixtures.containers.runner
        for failure in (None, "prepare", "create", "inspect", "attach", "cancel_attach", "returned_cancelled", "terminal", "cleanup"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory, \
                    socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                root = Path(directory).resolve()
                sock.bind(str(root / "docker.sock"))
                state = {"argv": None, "inspects": 0, "removed": False}
                provenance = {"git_sha": "a" * 40, "files": {}}
                plan = {"producer": {"git_sha": "a" * 40}, "selection": {"domain": "telecom", "task_id": "0"}}
                diagnostic = {"trial": {"status": "evaluated", "upstream_termination": {"reason": "user_stop"}}}
                if failure == "returned_cancelled":
                    diagnostic["trial"].update(status="cancelled", upstream_termination=None)
                held = types.SimpleNamespace(accepted=mock.Mock(return_value=failure != "returned_cancelled"),
                                             close=mock.Mock(), observation=lambda: {})
                def prepare(*args, **kwargs):
                    if failure == "prepare": raise ValueError("PRIVATE_EXCEPTION_CANARY")
                    return {"trial.json": b"{}"}, plan
                def attached(argv, env, *args):
                    self.assertEqual(argv[5:8], ["start", "--attach", "--interactive"])
                    self.assertEqual(set(env), {"PATH", "HOME", "DOCKER_CONFIG", "LANG", "LC_ALL"})
                    if failure == "attach": raise ValueError("PRIVATE_EXCEPTION_CANARY")
                    if failure == "cancel_attach": raise KeyboardInterrupt
                    return diagnostic
                fake = types.SimpleNamespace(catalog=types.SimpleNamespace(bind=lambda *args: {"stage_binding": {"index": 0}}),
                    Offline=lambda *args: held,
                    external=types.SimpleNamespace(pipe=types.SimpleNamespace(selected_assertions=lambda *args: []), run_process=attached))
                def capture(argv, **kwargs):
                    self.assertEqual(set(kwargs["env"]), {"PATH", "HOME", "DOCKER_CONFIG", "LANG", "LC_ALL"})
                    self.assertGreater(kwargs["timeout"], 0)
                    args = argv[5:]
                    if args[0] == "image": return worker.encoded([fixtures.containers.image_inspection()])
                    if args[0] == "create":
                        state["argv"] = args
                        if failure == "create": raise ValueError("PRIVATE_EXCEPTION_CANARY")
                        return b"owned"
                    if args[0] == "inspect":
                        state["inspects"] += 1
                        if failure == "inspect": raise ValueError("PRIVATE_EXCEPTION_CANARY")
                        info = fixtures.containers.LifecycleTests().info(state["argv"])
                        info["Config"]["OpenStdin"] = True
                        if state["inspects"] == 2 and failure == "terminal": info["State"]["ExitCode"] = 1
                        return worker.encoded([info])
                    self.assertEqual(args[:2], ["rm", "--force"])
                    self.assertEqual(args[2], state["argv"][state["argv"].index("--name") + 1])
                    self.assertTrue(held.close.called)
                    self.assertFalse((root / "receipt.json").exists())
                    if failure == "cleanup": raise ValueError("PRIVATE_EXCEPTION_CANARY")
                    state["removed"] = True
                    return b"removed"
                with mock.patch.object(recorder, "loader", return_value=trial), \
                        mock.patch.object(trial.tau, "committed_identity", return_value=provenance), \
                        mock.patch.object(trial, "identity", return_value=provenance), \
                        mock.patch.object(recorder, "driver", return_value=fake), \
                        mock.patch.object(trial, "selection_plan", return_value={"producer_git_sha": "a" * 40}), \
                        mock.patch.object(trial, "prepare", side_effect=prepare), \
                        mock.patch.object(trial, "publish", side_effect=lambda path, files: (path / "payload").mkdir(parents=True)), \
                        mock.patch.object(trial, "validate_plan"), \
                        mock.patch.object(recorder.shutil, "which", return_value="/docker"), \
                        mock.patch.object(trial.probe, "capture", side_effect=capture), \
                        mock.patch.object(recorder.sys, "flags", types.SimpleNamespace(isolated=True, no_site=True)), \
                        mock.patch.object(recorder.sys, "dont_write_bytecode", True), redirect_stdout(io.StringIO()) as output:
                    args = types.SimpleNamespace(source_archive=root / "archive", wheelhouse=root / "wheels",
                        docker_socket=root / "docker.sock", receipt=root / "receipt.json", index=0)
                    code = recorder.host(args)
                receipt = json.loads(args.receipt.read_bytes())
                self.assertEqual(code, 0 if failure is None else 1)
                self.assertEqual(receipt["cleanup"], "not_created" if failure == "prepare" else
                                 "unconfirmed" if failure == "cleanup" else "owned-container-removed")
                self.assertEqual(receipt["cancelled"], failure in ("cancel_attach", "returned_cancelled"))
                self.assertEqual(receipt["completed"], failure is None)
                self.assertNotIn("PRIVATE_EXCEPTION_CANARY", output.getvalue())
                if failure in ("terminal", "cleanup", "returned_cancelled"):
                    self.assertEqual(receipt["diagnostic"], diagnostic)
                if failure == "returned_cancelled":
                    self.assertEqual(receipt["stage"], "trial")
                    self.assertIs(receipt["source_authenticated"], False)
                if failure != "prepare": held.close.assert_called_once()

    def invoke(self, directory, action):
        args = types.SimpleNamespace(source_archive=Path("/synthetic/source"), wheelhouse=Path("/synthetic/wheels"),
            docker_socket=Path("/synthetic/docker.sock"), receipt=Path(directory) / "receipt.json", index=0)
        output = io.StringIO()
        with mock.patch.object(recorder.sys, "flags", types.SimpleNamespace(isolated=True, no_site=True)), \
                mock.patch.object(recorder.sys, "dont_write_bytecode", True), \
                mock.patch.object(recorder, "record", side_effect=action), redirect_stdout(output):
            code = recorder.host(args)
        return code, args.receipt, output.getvalue()

    def test_content_free_failed_receipts_and_cancellation_cleanup(self):
        for stage, cleanup, exception in (("source_verification", "not_created", ValueError),
                ("container_creation", "owned-container-removed", RuntimeError),
                ("trial", "owned-container-removed", KeyboardInterrupt), ("cleanup", "unconfirmed", RuntimeError)):
            def fail(args, result, progress):
                result["producer_git_sha"] = "a" * 40
                progress.update(stage=stage, cleanup=cleanup)
                raise exception("PRIVATE_EXCEPTION_CANARY")
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                code, receipt, output = self.invoke(directory, fail)
                result = json.loads(receipt.read_bytes())
                self.assertEqual(code, 1)
                self.assertEqual(result["stage"], stage)
                self.assertEqual(result["cleanup"], cleanup)
                self.assertEqual(result["cancelled"], exception is KeyboardInterrupt)
                for field in ("completed", "source_authenticated", "live_admission", "quality_eligible", "token_count_parity_verified"):
                    self.assertIs(result[field], False)
                self.assertNotIn("PRIVATE_EXCEPTION_CANARY", output + receipt.read_text())

    def test_existing_receipt_and_racing_creator_preserved(self):
        for existing in (True, False):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "receipt.json"
                if existing: path.write_bytes(b"original failed receipt")
                def action(args, result, progress):
                    self.assertFalse(existing)
                    path.write_bytes(b"original failed receipt")
                code, _, output = self.invoke(directory, action)
                self.assertEqual(code, 1)
                self.assertEqual(path.read_bytes(), b"original failed receipt")
                self.assertEqual(json.loads(output)["stage"], "receipt_output")

    def test_success_packaging_does_not_change_original_receipt(self):
        def passed(args, result, progress):
            result.update(result="pass", completed=True, source_authenticated=True, producer_git_sha="a" * 40)
            progress.update(stage="completed", cleanup="owned-container-removed")
        with tempfile.TemporaryDirectory() as directory:
            code, receipt, _ = self.invoke(directory, passed)
            raw = receipt.read_bytes()
            value = json.loads(raw)
            envelope = recorder.evidence_record(value)
            self.assertEqual(code, 0)
            self.assertEqual(envelope["git_sha"], value["producer_git_sha"])
            self.assertEqual(envelope["result"], value["result"])
            self.assertEqual(receipt.read_bytes(), raw)
            self.assertFalse(envelope["hardware_validated"])

    def test_signals_cancel_once_and_restore_handlers(self):
        signals = {}
        def host(args):
            callback = signals[signal.SIGTERM]
            with self.assertRaises(KeyboardInterrupt): callback(signal.SIGTERM, None)
            callback(signal.SIGINT, None)
            return 1
        with mock.patch.object(recorder.signal, "getsignal", return_value="original"), \
                mock.patch.object(recorder.signal, "signal", side_effect=lambda kind, handler: signals.update({kind: handler})), \
                mock.patch.object(recorder, "host", side_effect=host):
            self.assertEqual(recorder.main(["--source-archive", "/a", "--wheelhouse", "/b", "--docker-socket", "/c", "--receipt", "/d"]), 1)
        self.assertEqual(set(signals.values()), {"original"})

    def test_no_live_owner_imports_offline_helper(self):
        for path in Path(__file__).parent.glob("*.py"):
            if path.name.startswith("test_") or path.name in ("tau_telecom_offline.py", "record-tau-telecom-offline.py"):
                continue
            for node in ast.walk(ast.parse(path.read_bytes())):
                names = [row.name for row in node.names] if isinstance(node, ast.Import) else [node.module] if isinstance(node, ast.ImportFrom) else []
                self.assertNotIn("tau_telecom_offline", names, path.name)


if __name__ == "__main__":
    unittest.main()
