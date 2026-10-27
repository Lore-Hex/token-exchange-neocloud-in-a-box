# SPDX-License-Identifier: Apache-2.0
"""Synthetic receipt/kernel fixtures, subprocess pipes and fake loopback HTTP only."""

import contextlib
import copy
import io
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

import tau_live_identity as live
import tau_live_trial as cli
import tau_pipe_driver as driver
import test_tau_local_vllm as http
import test_tau_pipe_driver as pipes
import test_tau_trial as fixtures

tau, worker, runner = live.tau, live.worker, fixtures.runner
ROOT = Path(__file__).resolve().parent.parent
CANARY = "private-prompt-canary-do-not-retain"


def fixture_documents(ports=None, *, selected_target=None):
    benchmark = fixtures.fixtures.manifest()
    for role in ("simulator", "evaluator"):
        benchmark["settings"]["roles"][role]["revision"] = "c" * 40
    manifest = driver.scripted_manifest(benchmark)
    deployment = {"schema": "nib.tau-local-deployment/v1", "expires_unix": int(time.time()) + 600,
                  "manifest": manifest, "roles": {}}
    if selected_target is not None:
        deployment.update(schema="nib.tau-local-deployment/v2", selected_target=selected_target)
    documents = {"deployment.json": deployment}
    producer = {"git_sha": "a" * 40, "script": "scripts/synthetic_fixture.py", "source_sha256": "b" * 64}
    for index, role in enumerate(tau.ROLES):
        framing = manifest["framing"][role]
        framing.update(served_model="synthetic-" + role, context_tokens=4096)
        if selected_target is not None and role not in live.local.trial_roles(selected_target):
            continue
        port = ports[role] if ports else 20000 + index
        serving = copy.deepcopy(benchmark["settings"]["serving"]["candidate"])
        serving["tokenizer_sha256"] = framing["tokenizer_sha256"]
        runtime = {"schema": "nib.tau-local-runtime/v1", "producer": producer, "role": role,
            "model": benchmark["settings"]["roles"][role]["model"],
            "revision": benchmark["settings"]["roles"][role]["revision"], "engine_revision": live.local.REVISION,
            "endpoint": {"host": "127.0.0.1", "port": port},
            "process": {"pid": 1000 + index, "start_ticks": 25, "boot_id_sha256": "a" * 64,
                        "netns_inode": 50, "exe_sha256": "a" * 64, "argv_sha256": serving["arguments_sha256"],
                        "listener_inode": 2000 + index}, "serving": serving,
            "model_files_sha256": tau.digest((ROOT / tau.PACK / "files.sha256").read_bytes()),
            "logging": {"stdout": "null", "stderr": "null", "request_logging": False, "access_logging": False}}
        if role in ("simulator", "evaluator"):
            runtime["model_files_sha256"] = tau.digest(tau.encoded({"synthetic-model-inventory": role}))
        runtime["measurements"] = {name: {"expected_sha256": sha, "observed_sha256": sha} for name, sha in
            {**serving, "model_files_sha256": runtime["model_files_sha256"],
             "framing_sha256": framing["framing_sha256"]}.items()}
        tokenizer = {"schema": "nib.tau-local-tokenizer/v1", "producer": producer,
            "runtime_receipt_sha256": "f" * 64,
            **{name: framing[name] for name in ("framing_sha256", "tokenizer_sha256", "context_tokens")},
            "tokenizer_config_sha256": serving["tokenizer_config_sha256"],
            "cases": {case: {"case_sha256": "b" * 64, "independent_tokens_sha256": "d" * 64,
                       "endpoint_tokens_sha256": "d" * 64, "independent_count": 7, "endpoint_count": 7}
                       for case in ("text", "tool-history")}}
        documents[role + "-runtime.json"] = runtime
        documents[role + "-tokenizer.json"] = tokenizer
        deployment["roles"][role] = {"host": "127.0.0.1", "port": port,
                                     "runtime_sha256": "f" * 64, "tokenizer_sha256": "f" * 64}
    return benchmark, documents


def write_bundle(root, documents):
    # Tests deliberately manufacture fixtures, never admissible measurement evidence.
    for role in documents["deployment.json"]["roles"]:
        runtime_sha = tau.digest(tau.encoded(documents[role + "-runtime.json"]))
        documents[role + "-tokenizer.json"]["runtime_receipt_sha256"] = runtime_sha
        documents["deployment.json"]["roles"][role].update(runtime_sha256=runtime_sha,
            tokenizer_sha256=tau.digest(tau.encoded(documents[role + "-tokenizer.json"])))
    for name, value in documents.items():
        (root / name).write_bytes(tau.encoded(value))
        (root / name).chmod(0o600)
    return tau.digest(tau.encoded(documents["deployment.json"]))


def plan_for(benchmark, role="candidate"):
    return {"transport": "scripted-parent-pipe", "selection": {**fixtures.chosen(benchmark), "role": role},
            "manifest_sha256": tau.digest(tau.encoded(benchmark)), "producer": fixtures.producer(),
            "image": runner.runtime.TARGET["image"], "platform": "linux/amd64"}


def task_payload(root, plan, assertions=()):
    path = root / "upstream/data/tau2/domains/airline/tasks.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(tau.encoded([{"id": plan["selection"]["task_id"],
                                  "evaluation_criteria": {"nl_assertions": list(assertions)}}]))


class AdmissionTests(unittest.TestCase):
    def test_private_bound_input_retains_only_hashes(self):
        benchmark, docs = fixture_documents()
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(live, "source_receipt") as source:
            root = Path(temp).resolve()
            sha = write_bundle(root, docs)
            deployment = live.Deployment(root / "deployment.json", sha, benchmark, time.monotonic() + 5)
            self.assertEqual(source.call_count, 8)
            self.assertEqual(deployment.binding["deployment_sha256"], sha)
            self.assertEqual(set(deployment.binding["receipts"]), set(tau.ROLES))
            raw = tau.encoded(deployment.binding).decode()
            for private in (str(root), '"port"', '"pid"', '"model"', '"producer"'):
                self.assertNotIn(private, raw)

    def test_unknown_producer_is_not_accepted_or_executed(self):
        benchmark, docs = fixture_documents()
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(socket.socket, "connect") as connect:
            root = Path(temp).resolve()
            sha = write_bundle(root, docs)
            with self.assertRaises((worker.Abort, subprocess.CalledProcessError)):
                live.Deployment(root / "deployment.json", sha, benchmark, time.monotonic() + 5)
            connect.assert_not_called()

    def test_source_receipt_rejects_mismatch_and_does_not_import(self):
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
        source = "scripts/tau_preflight.py"
        row = {"git_sha": revision, "script": source, "source_sha256": tau.digest((ROOT / source).read_bytes())}
        live.source_receipt(row, time.monotonic() + 5)
        for name, value in (("source_sha256", "b" * 64), ("script", "../secret.py"),
                            ("git_sha", "unknown")):
            with self.subTest(name=name), self.assertRaises(worker.Abort):
                live.source_receipt({**row, name: value}, time.monotonic() + 5)

    def test_missing_mismatched_unpinned_or_unknown_identity_fails_before_network(self):
        mutations = (
            lambda b, d: d["deployment.json"].update(expires_unix=0),
            lambda b, d: d["deployment.json"].update(expires_unix=True),
            lambda b, d: d["deployment.json"].update(expires_unix=int(time.time()) + 3601),
            lambda b, d: d["deployment.json"]["roles"]["candidate"].update(host="localhost"),
            lambda b, d: d["deployment.json"]["roles"]["candidate"].update(port=True),
            lambda b, d: d["deployment.json"]["roles"]["evaluator"].update(port=20002),
            lambda b, d: d["candidate-runtime.json"].update(engine_revision="unknown"),
            lambda b, d: d["candidate-runtime.json"].update(model="wrong-model"),
            lambda b, d: d["simulator-runtime.json"].update(revision="main"),
            lambda b, d: d["evaluator-runtime.json"]["process"].update(pid=1002),
            lambda b, d: d["evaluator-runtime.json"]["process"].update(listener_inode=2002),
            lambda b, d: d["candidate-runtime.json"]["process"].update(argv_sha256="f" * 64),
            lambda b, d: d["candidate-runtime.json"]["logging"].update(request_logging=True),
            lambda b, d: d["candidate-runtime.json"]["measurements"]["image_sha256"].update(observed_sha256="f" * 64),
            lambda b, d: d["candidate-tokenizer.json"]["cases"]["text"].update(endpoint_count=6),
            lambda b, d: d["candidate-tokenizer.json"]["cases"]["text"].update(endpoint_tokens_sha256="f" * 64),
            lambda b, d: d["candidate-tokenizer.json"].update(context_tokens=123),
            lambda b, d: d["candidate-tokenizer.json"]["cases"].pop("tool-history"),
            lambda b, d: d["deployment.json"]["manifest"].update(benchmark_manifest_sha256="f" * 64),
            lambda b, d: d["deployment.json"]["manifest"].update(quality_eligible=True),
        )
        for index, mutate in enumerate(mutations):
            # Fixture I/O must not move the 3601-second rejection case back
            # inside the allowed one-hour window before admission reads it.
            with self.subTest(index=index), mock.patch.object(time, "time", return_value=1800000000.5), \
                    tempfile.TemporaryDirectory() as temp, \
                    mock.patch.object(live, "source_receipt"), mock.patch.object(socket.socket, "connect") as connect:
                benchmark, docs = fixture_documents()
                mutate(benchmark, docs)
                root = Path(temp).resolve()
                sha = write_bundle(root, docs)
                with self.assertRaises((worker.Abort, ValueError)):
                    live.Deployment(root / "deployment.json", sha, benchmark, time.monotonic() + 5)
                connect.assert_not_called()

    def test_freshness_exact_window_boundaries(self):
        deployment = object.__new__(live.Deployment)
        now = 1800000000
        with mock.patch.object(time, "time", return_value=now):
            for offset in (1, 3600):
                deployment.expires = now + offset
                deployment.fresh()
            for expires in (now, now - 1, now + 3601, True):
                deployment.expires = expires
                with self.subTest(expires=expires), self.assertRaises(worker.Abort):
                    deployment.fresh()

    def test_same_simulator_evaluator_model_is_rejected_even_on_distinct_ports(self):
        benchmark, docs = fixture_documents()
        benchmark["settings"]["roles"]["evaluator"]["model"] = benchmark["settings"]["roles"]["simulator"]["model"]
        docs["deployment.json"]["manifest"]["benchmark_manifest_sha256"] = tau.digest(tau.encoded(benchmark))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            sha = write_bundle(root, docs)
            with self.assertRaises(worker.Abort):
                live.Deployment(root / "deployment.json", sha, benchmark, time.monotonic() + 5)

    def test_model_aliases_cannot_hide_shared_evaluator_weights(self):
        benchmark, docs = fixture_documents()
        evaluator = docs["evaluator-runtime.json"]
        digest = docs["simulator-runtime.json"]["model_files_sha256"]
        evaluator["model_files_sha256"] = digest
        evaluator["measurements"]["model_files_sha256"] = {"expected_sha256": digest, "observed_sha256": digest}
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(live, "source_receipt"):
            root = Path(temp).resolve()
            sha = write_bundle(root, docs)
            with self.assertRaises(worker.Abort):
                live.Deployment(root / "deployment.json", sha, benchmark, time.monotonic() + 5)

    def test_session_budget_deadline_cannot_exceed_receipt_expiry(self):
        benchmark, docs = fixture_documents()
        docs["deployment.json"]["expires_unix"] = int(time.time()) + 2
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(live, "source_receipt"):
            root = Path(temp).resolve()
            sha = write_bundle(root, docs)
            deployment = live.Deployment(root / "deployment.json", sha, benchmark, time.monotonic() + 5)
            session = deployment.session(mock.Mock(), "candidate", [])
            try:
                self.assertGreater(session.bridge.budget.trial_deadline - time.monotonic(), 0)
                self.assertLess(session.bridge.budget.trial_deadline - time.monotonic(), 2)
            finally:
                session.close()

    def test_private_reader_rejects_modes_links_fifo_and_oversize(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            path = root / "input.json"
            path.write_bytes(b"{}")
            path.chmod(0o600)
            self.assertEqual(live.private_read(path, time.monotonic() + 2), b"{}")
            path.chmod(0o644)
            with self.assertRaises(worker.Abort):
                live.private_read(path, time.monotonic() + 2)
            path.chmod(0o600)
            os.link(path, root / "hard")
            with self.assertRaises(worker.Abort):
                live.private_read(path, time.monotonic() + 2)
            (root / "hard").unlink()
            (root / "link").symlink_to(path)
            os.mkfifo(root / "fifo", 0o600)
            for name in ("link", "fifo"):
                with self.subTest(name=name), self.assertRaises((worker.Abort, OSError)):
                    live.private_read(root / name, time.monotonic() + 2)
            path.write_bytes(b"x" * ((1 << 20) + 1))
            with self.assertRaises(worker.Abort):
                live.private_read(path, time.monotonic() + 2)

    def test_tampered_or_missing_receipt_cannot_reach_http(self):
        for failure in ("digest", "missing", "tamper"):
            benchmark, docs = fixture_documents()
            with tempfile.TemporaryDirectory() as temp, mock.patch.object(live, "source_receipt"), \
                    mock.patch.object(socket.socket, "connect") as connect:
                root = Path(temp).resolve()
                sha = write_bundle(root, docs)
                if failure == "missing":
                    (root / "candidate-tokenizer.json").unlink()
                elif failure == "tamper":
                    (root / "candidate-runtime.json").write_bytes(b"{}")
                else:
                    sha = "f" * 64
                with self.assertRaises((worker.Abort, OSError)):
                    live.Deployment(root / "deployment.json", sha, benchmark, time.monotonic() + 5)
                connect.assert_not_called()

    def test_source_closure_ignores_ambient_modules_and_binds_new_helpers(self):
        names = ("tau_preflight", "tau_trial_worker", "tau_aggregate", "tau_transport",
                 "tau_local_vllm", "tau_local_llama", "engine_tokenizer_parity",
                 "tau_llama_identity", "tau_live_identity", "tau_pipe_driver")
        files = {"scripts/" + name + ".py": tau.digest((ROOT / "scripts" / (name + ".py")).read_bytes())
                 for name in names}
        with mock.patch.dict(sys.modules, {name: object() for name in names}):
            loaded = runner.parent_driver({"files": files}, live_local=True)
        self.assertIs(loaded.live.worker, loaded.worker)
        files["scripts/tau_local_vllm.py"] = "f" * 64
        with self.assertRaises(ValueError):
            runner.parent_driver({"files": files}, live_local=True)

    def test_default_cli_has_no_live_switch_and_live_bootstrap_is_source_only(self):
        result = subprocess.run([sys.executable, "-I", "-S", "-B", str(ROOT / "scripts/tau_trial.py"), "run", "--help"],
            capture_output=True, timeout=10, check=True)
        self.assertNotIn(b"deployment", result.stdout)
        self.assertNotIn(b"live", result.stdout)
        with mock.patch.dict(sys.modules, {"tau_trial": object()}):
            self.assertEqual(cli.runner_module().RUNTIME_SHA, runner.RUNTIME_SHA)

    def test_live_payload_binding_is_hashed_and_cannot_run_as_scripted(self):
        benchmark, _ = fixture_documents()
        files = {runner.WORKER: (ROOT / runner.WORKER).read_bytes(),
                 "manifest.json": tau.encoded(benchmark), "inputs.json": b"{}\n",
                 "live-binding.json": b'{"synthetic-fixture":true}\n'}
        toolkit = {"git_sha": "a" * 40, "files": {runner.WORKER: tau.digest(files[runner.WORKER])}}
        plan = {**plan_for(benchmark), "schema": "nib.tau-scripted-plan/v1", "environment": runner.ENVIRONMENT,
                "seconds": runner.SECONDS, "producer": {"git_sha": toolkit["git_sha"],
                    "adapter_sha256": toolkit["files"][runner.WORKER],
                    "runtime_receipt_sha256": tau.digest(files["inputs.json"])},
                "files": {name: {"bytes": len(raw), "sha256": tau.digest(raw)} for name, raw in files.items()}}
        files["trial.json"] = tau.encoded(plan)
        sha = tau.digest(files["trial.json"])
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp).resolve()
            payload = parent / "payload"
            for name, raw in files.items():
                path = payload / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            self.assertEqual(runner.validate_plan(payload, sha, toolkit, time.monotonic() + 5,
                parent_pipe=True, live_local=True), (plan, benchmark))
            with self.assertRaises(ValueError):
                runner.validate_plan(payload, sha, toolkit, time.monotonic() + 5, parent_pipe=True)
            (payload / "live-binding.json").write_bytes(b"{}\n")
            with self.assertRaises(ValueError):
                runner.validate_plan(payload, sha, toolkit, time.monotonic() + 5, parent_pipe=True, live_local=True)


class KernelBindingTests(unittest.TestCase):
    def test_proc_reader_requires_eof_not_one_short_read(self):
        class ShortReads(io.BytesIO):
            def read(self, size=-1):
                return super().read(min(size, 3))
        with mock.patch("builtins.open", return_value=ShortReads(b"abcdefgh")):
            self.assertEqual(live.proc_read("/proc/synthetic", 8), b"abcdefgh")

    def test_proc_reader_rejects_empty_or_over_limit_across_short_reads(self):
        class ShortReads(io.BytesIO):
            def read(self, size=-1):
                return super().read(min(size, 3))
        for raw in (b"", b"abcdefghi"):
            with self.subTest(raw=raw), mock.patch("builtins.open", return_value=ShortReads(raw)):
                with self.assertRaises(worker.Abort):
                    live.proc_read("/proc/synthetic", 8)

    def test_tcp_table_cannot_hide_later_listener_after_short_read(self):
        first = (b"header\n"
                 b"0: 0100007F:4E20 00000000:0000 0A 00000000:00000000 00:00000000 00000000 1000 0 2000 1\n")
        later = b"1: 00000000:4E20 00000000:0000 0A 00000000:00000000 00:00000000 00000000 1000 0 3000 1\n"
        class ShortReads(io.BytesIO):
            def read(self, size=-1):
                return super().read(min(size, len(first)))
        with mock.patch("builtins.open", return_value=ShortReads(first + later)):
            self.assertEqual(live.tcp_rows(), [("0100007F:4E20", "00000000:0000", "0A", 2000),
                                               ("00000000:4E20", "00000000:0000", "0A", 3000)])

    def test_endpoint_rechecks_process_listener_and_connected_socket_before_http(self):
        _, docs = fixture_documents()
        receipt = docs["candidate-runtime.json"]
        endpoint = live.Endpoint(20000, receipt)
        facts = {key: receipt["process"][key] for key in ("start_ticks", "boot_id_sha256", "netns_inode", "argv_sha256")}
        address, peer = "0100007F:4E20", "0100007F:7530"
        connection = types.SimpleNamespace(getpeername=lambda: ("127.0.0.1", 20000),
                                           getsockname=lambda: ("127.0.0.1", 30000))
        with tempfile.TemporaryFile() as executable:
            executable.write(b"synthetic-executable")
            executable.flush()
            info = os.fstat(executable.fileno())
            endpoint.exe = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            @contextlib.contextmanager
            def opened(*args):
                yield executable
            def table(ipv6=False):
                return [] if ipv6 else [(address, "00000000:0000", "0A", 2000), (address, peer, "01", 3000)]
            with mock.patch.object(live.sys, "platform", "linux"), \
                    mock.patch.object(live, "process_facts", return_value=facts) as process, \
                    mock.patch.object(live.os, "stat", return_value=types.SimpleNamespace(st_ino=50)), \
                    mock.patch("builtins.open", opened), mock.patch.object(live, "tcp_rows", side_effect=table), \
                    mock.patch.object(live, "sockets", return_value={2000, 3000}) as owned:
                endpoint.check(time.monotonic() + 2, connection)
                self.assertEqual(process.call_count, 2)
                with mock.patch.object(live, "process_facts", return_value={**facts, "start_ticks": 26}):
                    with self.assertRaises(worker.Abort):
                        endpoint.check(time.monotonic() + 2)
                with mock.patch.object(live, "tcp_rows", return_value=[("00000000:4E20", "00000000:0000", "0A", 2000)]):
                    with self.assertRaises(worker.Abort):
                        endpoint.check(time.monotonic() + 2)
                owned.return_value = {2000}
                start = time.monotonic()
                with self.assertRaises(worker.Abort):
                    endpoint.check(start + 0.05, connection)
                self.assertLess(time.monotonic() - start, 0.5)

    def test_guarded_connection_rejects_before_sending_any_body(self):
        for after_connect in (False, True):
            with http.endpoint([http.counted()]) as (port, seen):
                guard = mock.Mock(port=port)
                guard.check.side_effect = [None, worker.Abort("protocol_error")] if after_connect else worker.Abort("protocol_error")
                client = live.GuardedClient(guard, mock.Mock())
                with self.assertRaises(worker.Abort):
                    client.post("/tokenize", {"private": CANARY}, time.monotonic() + 2)
                self.assertEqual(seen, [])


class LivePipeTests(unittest.TestCase):
    def exercise(self, failure=None, *, selected_target=None):
        with contextlib.ExitStack() as stack:
            target = selected_target or "candidate"
            ports, requests = {}, {}
            for role in live.local.trial_roles(target) if selected_target else tau.ROLES:
                response = http.completion("synthetic-" + role)
                if role == "evaluator":
                    response["choices"][0]["message"]["content"] = '{"results":[]}'
                if role == target and failure == "unknown-usage":
                    response["usage"] = None
                if role == target and failure == "truncated":
                    response["choices"][0]["finish_reason"] = "length"
                ports[role], requests[role] = stack.enter_context(http.endpoint([http.counted(), response]))
            root = Path(stack.enter_context(tempfile.TemporaryDirectory())).resolve()
            benchmark, docs = fixture_documents(ports, selected_target=selected_target)
            sha = write_bundle(root, docs)
            # Only these test fixtures replace provenance/kernel measurements.
            stack.enter_context(mock.patch.object(live, "source_receipt"))
            stack.enter_context(mock.patch.object(live.Endpoint, "check"))
            deployment = live.Deployment(root / "deployment.json", sha, benchmark, time.monotonic() + 5)
            plan = plan_for(benchmark, target)
            task_payload(root, plan)
            child = pipes.CHILD.replace("('agent', 'simulator')", "('agent', 'simulator', 'evaluator')")
            if failure == "child-exit":
                child = child.replace("return 0.0", "os._exit(9)")
            result = driver.run_process([sys.executable, "-I", "-S", "-B", "-c", child, str(ROOT / "scripts")],
                {}, root, plan, benchmark, time.monotonic() + 5, deployment=deployment)
            runner.check_result(result, plan, benchmark, live_local=True)
            with self.assertRaises(ValueError):
                runner.check_result(result, plan, benchmark)
            self.assertNotIn("canary", tau.encoded(result).decode())
            if selected_target:
                self.assertNotIn("reference" if target == "candidate" else "candidate", requests)
            else:
                self.assertEqual(requests["reference"], [])
            self.assertFalse(result["quality_accepted"] or result["confidential"])
            self.assertTrue(result["live_transport"])
            self.assertEqual(result["token_usage"], "engine-reported")
            self.assertEqual(result["report"]["kind"], "declared-unverified")
            return result, requests

    def test_all_roles_share_one_bridge_and_live_counts_not_synthetic_counts(self):
        result, requests = self.exercise()
        row = result["report"]["trials"][0]
        self.assertEqual((row["status"], row["requests"], row["usage"]["total_tokens"]), ("evaluated", 3, 24))
        self.assertEqual(result["accounting"]["role_requests"], {"agent": 1, "simulator": 1, "evaluator": 1})
        for role in ("candidate", "simulator", "evaluator"):
            self.assertEqual([path for path, _ in requests[role]], ["/tokenize", "/v1/chat/completions"])

    def test_unknown_generation_usage_keeps_reservation_and_stops_other_roles(self):
        result, requests = self.exercise("unknown-usage")
        row = result["report"]["trials"][0]
        self.assertEqual((row["status"], row["requests"], row["usage"]), ("protocol_error", 1, None))
        self.assertGreater(result["accounting"]["reserved_tokens"], 8)
        self.assertFalse(result["accounting"]["usage_known"])
        self.assertEqual(requests["simulator"], [])

    def test_truncation_retains_known_counts_but_never_passes(self):
        result, _ = self.exercise("truncated")
        row = result["report"]["trials"][0]
        self.assertEqual((row["status"], row["requests"], row["usage"]["total_tokens"]), ("truncated", 1, 8))

    def test_child_failure_preserves_all_settled_live_usage(self):
        result, _ = self.exercise("child-exit")
        row = result["report"]["trials"][0]
        self.assertEqual((row["status"], row["requests"], row["usage"]["total_tokens"]), ("protocol_error", 3, 24))

    def test_fake_docker_lifecycle_retains_live_counts_after_terminal_failures(self):
        for selected_target, failure in ((target, failure) for target in (None, "reference")
                for failure in (None, "exit", "oom", "inspect", "identity", "cleanup", "endpoint-drift")):
            with self.subTest(selected_target=selected_target, failure=failure), contextlib.ExitStack() as stack:
                ports = {}
                for role in live.local.trial_roles(selected_target) if selected_target else tau.ROLES:
                    ports[role], _ = stack.enter_context(http.endpoint([
                        http.counted(), http.completion("synthetic-" + role)]))
                root = Path(stack.enter_context(tempfile.TemporaryDirectory())).resolve()
                benchmark, docs = fixture_documents(ports, selected_target=selected_target)
                sha = write_bundle(root, docs)
                stack.enter_context(mock.patch.object(live, "source_receipt"))
                stack.enter_context(mock.patch.object(live.Endpoint, "check"))
                deployment = live.Deployment(root / "deployment.json", sha, benchmark, time.monotonic() + 5)
                plan = plan_for(benchmark, selected_target or "candidate")
                task_payload(root, plan)
                (root / "live-binding.json").write_bytes(tau.encoded(deployment.binding))
                sock = stack.enter_context(socket.socket(socket.AF_UNIX))
                sock.bind(str(root / "docker.sock"))
                state, published = {"exists": False, "inspections": 0, "checks": 0}, {}
                def check(deployment, deadline):
                    state["checks"] += 1
                    if failure == "endpoint-drift" and state["checks"] == 3:
                        raise worker.Abort("protocol_error")
                def capture(argv, **kwargs):
                    args = argv[5:]
                    if args[:2] == ["image", "inspect"]:
                        return tau.encoded([fixtures.image_inspection()])
                    if args[0] == "create":
                        state.update(exists=True, info=fixtures.LifecycleTests().info(args))
                        state["info"]["Config"]["OpenStdin"] = True
                        self.assertEqual(args[args.index("--network") + 1], "none")
                        self.assertEqual(args[args.index("--log-driver") + 1], "none")
                        return b"d" * 64
                    if args[0] == "inspect":
                        state["inspections"] += 1
                        info = copy.deepcopy(state["info"])
                        if state["inspections"] == 2:
                            if failure == "inspect":
                                raise TimeoutError(CANARY)
                            if failure == "identity":
                                info["Config"]["OpenStdin"] = False
                            if failure == "exit":
                                info["State"]["ExitCode"] = 9
                            if failure == "oom":
                                info["State"]["OOMKilled"] = True
                        return tau.encoded([info])
                    if args[0] == "ps":
                        return b"d" * 64 if state["exists"] else b""
                    if args[0] == "rm":
                        if failure == "cleanup":
                            raise RuntimeError(CANARY)
                        state["exists"] = False
                        return b""
                    self.fail("unexpected fake Docker command")
                def attach(command, environment, payload, plan, manifest, deadline, **kwargs):
                    self.assertEqual(command[5:8], ["start", "--attach", "--interactive"])
                    return driver.run_process([sys.executable, "-I", "-S", "-B", "-c", pipes.CHILD,
                        str(ROOT / "scripts")], {}, payload, plan, manifest, deadline, **kwargs)
                toolkit = {"git_sha": "a" * 40, "files": {}}
                for obj, name, options in (
                    (runner, "identity", {"return_value": toolkit}),
                    (runner, "validate_plan", {"return_value": (plan, benchmark)}),
                    (runner, "parent_driver", {"return_value": types.SimpleNamespace(live=live, run_process=attach)}),
                    (runner.shutil, "which", {"return_value": "/fake/docker"}),
                    (runner.probe, "capture", {"side_effect": capture}),
                    (runner, "publish_evidence", {"side_effect": lambda path, files: published.update(files)}),
                    (live.Deployment, "check", {"new": check}),
                ):
                    stack.enter_context(mock.patch.object(obj, name, **options))
                output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                status = runner.run(root, "e" * 64, root / "evidence", root / "docker.sock",
                    parent_pipe=True, live_deployment=(root / "deployment.json", sha))
                record, envelope = (tau.json_value(published[name]) for name in ("output.log", "run.json"))
                self.assertEqual(status, 0 if failure is None else 1)
                row = record["diagnostic"]["report"]["trials"][0]
                self.assertEqual((row["requests"], row["usage"]["total_tokens"]), (2, 16))
                self.assertEqual(record["result"], "pass" if failure is None else "fail")
                self.assertEqual(record["schema"], "nib.tau-live-run/v1")
                self.assertEqual(record["deployment"], deployment.binding)
                self.assertEqual(record["diagnostic"]["report"]["role"], selected_target or "candidate")
                self.assertIn("scripts/tau_live_trial.py run", envelope["command"])
                self.assertIn("--deployment-sha256 " + sha, envelope["command"])
                self.assertFalse(record["quality_accepted"] or envelope["hardware_validated"])
                self.assertEqual(envelope["host"]["gpus"], "unobserved")
                for raw in (*published.values(), output.getvalue().encode()):
                    self.assertNotIn(CANARY.encode(), raw)
                    self.assertNotIn(str(root).encode(), raw)


if __name__ == "__main__":
    unittest.main()
