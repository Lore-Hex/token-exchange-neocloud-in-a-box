# SPDX-License-Identifier: Apache-2.0
"""Synthetic mixed-runtime receipts and loopback HTTP, never live model proof."""

import contextlib
import copy
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import test_tau_live_trial as fixtures

live, tau, worker = fixtures.live, fixtures.tau, fixtures.worker
ROOT = fixtures.ROOT


def documents(ports=None, target="candidate"):
    benchmark, docs = fixtures.fixture_documents(ports, selected_target=target)
    deployment = docs["deployment.json"]
    deployment["schema"] = "nib.tau-local-deployment/v3"
    for role, descriptor in deployment["roles"].items():
        descriptor["engine_kind"] = "vllm" if role == target else "llama.cpp"
    for role, model in zip(("simulator", "evaluator"), ("qwen/qwen3-0.6b", "qwen/qwen3-1.7b")):
        pinned = live.cpu.identity(model, time.monotonic() + 5)
        config = benchmark["settings"]["roles"][role]
        config.update(model=model, revision=pinned["revision"])
        framing = deployment["manifest"]["framing"][role]
        framing.update(served_model=model, context_tokens=4096, parallel_tool_calls=False,
                       tokenizer_sha256=pinned["tokenizer_sha256"], framing_sha256=pinned["framing_sha256"])
        runtime = docs[role + "-runtime.json"]
        runtime.update(schema="nib.tau-llama-runtime/v1", model=model, revision=pinned["revision"],
                       engine_revision=live.llama.REVISION, gguf=pinned["gguf"],
                       model_files_sha256=pinned["model_files_sha256"], observed_unix=int(time.time()))
        runtime["serving"] = {"image_sha256": "a" * 64, "helper_sha256": "b" * 64,
                              "arguments_sha256": runtime["process"]["argv_sha256"],
                              **{key: pinned[key] for key in ("tokenizer_sha256", "framing_sha256")}}
        runtime["measurements"] = {key: {"expected_sha256": value, "observed_sha256": value} for key, value in
            {**runtime["serving"], "gguf_sha256": pinned["gguf"]["sha256"],
             "model_files_sha256": pinned["model_files_sha256"], "executable_sha256": runtime["process"]["exe_sha256"]}.items()}
        tokenizer = docs[role + "-tokenizer.json"]
        del tokenizer["tokenizer_config_sha256"]
        tokenizer.update(schema="nib.tau-llama-tokenizer/v1", gguf_sha256=pinned["gguf"]["sha256"],
                         observed_unix=runtime["observed_unix"], tokenizer_sha256=pinned["tokenizer_sha256"],
                         framing_sha256=pinned["framing_sha256"], cases={name: {
                             "case_sha256": value, "independent_tokens_sha256": "d" * 64,
                             "endpoint_tokens_sha256": "d" * 64, "independent_count": 7, "endpoint_count": 7}
                             for name, value in live.cpu.case_hashes().items()})
    deployment["manifest"]["settings"] = copy.deepcopy(benchmark["settings"])
    deployment["manifest"]["benchmark_manifest_sha256"] = tau.digest(tau.encoded(benchmark))
    return benchmark, docs


class MixedAdmissionTests(unittest.TestCase):
    def test_exact_support_kinds_dispatch_without_changing_target_budget_or_reference(self):
        for target in ("candidate", "reference"):
            benchmark, docs = documents(target=target)
            original = copy.deepcopy(benchmark)
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temp, \
                    mock.patch.object(live, "source_receipt") as source:
                root = Path(temp).resolve()
                sha = fixtures.write_bundle(root, docs)
                deployment = live.Deployment(root / "deployment.json", sha, benchmark, time.monotonic() + 5)
                self.assertEqual(source.call_count, 6)
                self.assertEqual(deployment.binding["schema"], "nib.tau-live-binding/v3")
                self.assertEqual(deployment.binding["engine_kinds"],
                                 {target: "vllm", "simulator": "llama.cpp", "evaluator": "llama.cpp"})
                session = deployment.session(mock.Mock(), target, [])
                try:
                    self.assertIs(type(session.endpoints[target]), live.GuardedClient)
                    for role in ("simulator", "evaluator"):
                        self.assertIs(type(session.endpoints[role]), live.GuardedLlamaClient)
                    self.assertEqual(session.bridge.budget.limits, original["settings"]["limits"])
                    self.assertEqual(benchmark, original)
                    self.assertEqual(set(benchmark["settings"]["serving"]), {"candidate", "reference"})
                finally:
                    session.close()
                for private in (str(root), '"pid"', '"port"', '"gguf"', '"model"'):
                    self.assertNotIn(private, tau.encoded(deployment.binding).decode())

    def test_legacy_contracts_stay_vllm_only_and_retain_inventory_counts(self):
        for target in (None, "candidate"):
            benchmark, docs = fixtures.fixture_documents(selected_target=target)
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temp, \
                    mock.patch.object(live, "source_receipt"):
                root = Path(temp).resolve()
                sha = fixtures.write_bundle(root, docs)
                admitted = live.Deployment(root / "deployment.json", sha, benchmark, time.monotonic() + 5)
                self.assertEqual(len(admitted.endpoints), 3 if target else 4)
                self.assertEqual(set(admitted.kinds.values()), {"vllm"})
                self.assertNotIn("engine_kinds", admitted.binding)
                self.assertEqual(len({docs[role + "-runtime.json"]["model_files_sha256"]
                                      for role in admitted.endpoints}), 3)
                docs["deployment.json"]["roles"]["simulator"]["engine_kind"] = "llama.cpp"
                sha = fixtures.write_bundle(root, docs)
                with self.assertRaises(worker.Abort):
                    live.Deployment(root / "deployment.json", sha, benchmark, time.monotonic() + 5)

    def test_v3_can_retain_a_vllm_support_role_without_reinterpreting_its_receipts(self):
        benchmark, docs = documents()
        legacy_benchmark, legacy = fixtures.fixture_documents(selected_target="candidate")
        role = "evaluator"
        benchmark["settings"]["roles"][role] = legacy_benchmark["settings"]["roles"][role]
        deployment = docs["deployment.json"]
        deployment["manifest"]["framing"][role] = legacy["deployment.json"]["manifest"]["framing"][role]
        deployment["manifest"]["settings"] = copy.deepcopy(benchmark["settings"])
        deployment["manifest"]["benchmark_manifest_sha256"] = tau.digest(tau.encoded(benchmark))
        deployment["roles"][role]["engine_kind"] = "vllm"
        for kind in ("runtime", "tokenizer"):
            docs[role + "-" + kind + ".json"] = legacy[role + "-" + kind + ".json"]
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(live, "source_receipt"):
            root = Path(temp).resolve()
            sha = fixtures.write_bundle(root, docs)
            admitted = live.Deployment(root / "deployment.json", sha, benchmark, time.monotonic() + 5)
            session = admitted.session(mock.Mock(), "candidate", [])
            try:
                self.assertIs(type(session.endpoints["evaluator"]), live.GuardedClient)
                self.assertIs(type(session.endpoints["simulator"]), live.GuardedLlamaClient)
            finally:
                session.close()

    def test_kind_role_pin_framing_and_original_proof_mutations_fail_before_http(self):
        mutations = (
            lambda b, d: d["deployment.json"]["roles"]["simulator"].pop("engine_kind"),
            lambda b, d: d["deployment.json"]["roles"]["simulator"].update(engine_kind="other"),
            lambda b, d: d["deployment.json"]["roles"]["candidate"].update(engine_kind="llama.cpp"),
            lambda b, d: d["deployment.json"]["roles"]["simulator"].update(engine_kind="vllm"),
            lambda b, d: d["simulator-runtime.json"].update(engine_revision=live.local.REVISION),
            lambda b, d: d["simulator-runtime.json"].update(role="evaluator"),
            lambda b, d: d["simulator-runtime.json"].update(revision="e" * 40),
            lambda b, d: d["simulator-runtime.json"]["gguf"].update(sha256="e" * 64),
            lambda b, d: d["simulator-runtime.json"]["gguf"].update(bytes=1),
            lambda b, d: d["simulator-runtime.json"]["gguf"].update(filename="other.gguf"),
            lambda b, d: d["simulator-runtime.json"].update(model_files_sha256="e" * 64),
            lambda b, d: d["simulator-runtime.json"]["serving"].update(tokenizer_sha256="e" * 64),
            lambda b, d: d["simulator-runtime.json"]["serving"].update(tokenizer_config_sha256="e" * 64),
            lambda b, d: d["simulator-runtime.json"]["measurements"]["gguf_sha256"].update(observed_sha256="e" * 64),
            lambda b, d: d["simulator-runtime.json"]["measurements"]["helper_sha256"].update(observed_sha256="e" * 64),
            lambda b, d: d["simulator-runtime.json"]["logging"].update(access_logging=True),
            lambda b, d: d["simulator-runtime.json"]["process"].update(argv_sha256="e" * 64),
            lambda b, d: d["simulator-runtime.json"].update(observed_unix=int(time.time()) - 3600),
            lambda b, d: d["simulator-runtime.json"].update(observed_unix=int(time.time()) + 60),
            lambda b, d: d["simulator-runtime.json"].update(observed_unix=True),
            lambda b, d: d["simulator-tokenizer.json"].update(observed_unix=int(time.time()) - 60),
            lambda b, d: d["simulator-tokenizer.json"].update(gguf_sha256="e" * 64),
            lambda b, d: d["simulator-tokenizer.json"]["cases"]["tools"].update(case_sha256="e" * 64),
            lambda b, d: d["simulator-tokenizer.json"]["cases"].pop("tool-continuation"),
            lambda b, d: d["simulator-tokenizer.json"]["cases"]["text"].update(endpoint_count=8),
            lambda b, d: d["simulator-tokenizer.json"]["cases"]["text"].update(endpoint_tokens_sha256="e" * 64),
            lambda b, d: d["deployment.json"]["manifest"]["framing"]["simulator"].update(context_tokens=8192),
            lambda b, d: d["deployment.json"]["manifest"]["framing"]["simulator"].update(parallel_tool_calls=True),
            lambda b, d: d["deployment.json"]["manifest"]["framing"]["simulator"].update(served_model="alias"),
        )
        for index, mutate in enumerate(mutations):
            benchmark, docs = documents()
            mutate(benchmark, docs)
            with self.subTest(index=index), tempfile.TemporaryDirectory() as temp, \
                    mock.patch.object(live, "source_receipt"), mock.patch.object(socket.socket, "connect") as connect:
                root = Path(temp).resolve()
                sha = fixtures.write_bundle(root, docs)
                with self.assertRaises((worker.Abort, ValueError)):
                    live.Deployment(root / "deployment.json", sha, benchmark, time.monotonic() + 5)
                connect.assert_not_called()

    def test_unapproved_producer_and_missing_receipt_never_become_runtime_proof(self):
        benchmark, docs = documents()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            sha = fixtures.write_bundle(root, docs)
            with mock.patch.object(socket.socket, "connect") as connect:
                with self.assertRaises((worker.Abort, subprocess.CalledProcessError)):
                    live.Deployment(root / "deployment.json", sha, benchmark, time.monotonic() + 5)
                connect.assert_not_called()
            (root / "evaluator-tokenizer.json").unlink()
            with mock.patch.object(live, "source_receipt"), self.assertRaises(OSError):
                live.Deployment(root / "deployment.json", sha, benchmark, time.monotonic() + 5)

    def test_matching_but_unpinned_support_claims_and_larger_output_are_rejected(self):
        for mutation in ("model", "revision", "same-model", "output", "context-parity", "receipt-expiry"):
            benchmark, docs = documents()
            config = benchmark["settings"]["roles"]["simulator"]
            runtime = docs["simulator-runtime.json"]
            if mutation in ("model", "revision", "same-model"):
                key = "revision" if mutation == "revision" else "model"
                value = ("f" * 40 if key == "revision" else
                         benchmark["settings"]["roles"]["evaluator"]["model"] if mutation == "same-model" else
                         "qwen/unpinned-model")
                config[key] = runtime[key] = value
                if key == "model":
                    docs["deployment.json"]["manifest"]["framing"]["simulator"]["served_model"] = value
            elif mutation == "output":
                config["generation"]["max_tokens"] = 257
                config["settings_sha256"] = tau.digest(tau.encoded(config["generation"]))
                benchmark["settings"]["limits"]["output_tokens_per_request"] = 257
            elif mutation == "context-parity":
                docs["simulator-tokenizer.json"]["cases"]["text"].update(independent_count=1025, endpoint_count=1025)
            else:
                runtime["observed_unix"] = int(time.time()) - 3500
            docs["deployment.json"]["manifest"]["settings"] = copy.deepcopy(benchmark["settings"])
            docs["deployment.json"]["manifest"]["benchmark_manifest_sha256"] = tau.digest(tau.encoded(benchmark))
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp, \
                    mock.patch.object(live, "source_receipt"), mock.patch.object(socket.socket, "connect") as connect:
                root = Path(temp).resolve()
                sha = fixtures.write_bundle(root, docs)
                with self.assertRaises((worker.Abort, ValueError)):
                    live.Deployment(root / "deployment.json", sha, benchmark, time.monotonic() + 5)
                connect.assert_not_called()

    def test_new_source_closure_and_static_identity_inputs_are_required(self):
        seen = set()
        def committed(deadline, extra_files=()):
            seen.update(extra_files)
            return {"git_sha": "a" * 40, "files": {name: "b" * 64 for name in extra_files}}
        with mock.patch.object(fixtures.runner.tau, "committed_identity", side_effect=committed):
            fixtures.runner.identity(time.monotonic() + 5, True, live_local=True)
        self.assertTrue({"scripts/tau_local_llama.py", "scripts/tau_llama_identity.py",
                         "scripts/tau_cpu_support_models.json", "scripts/tau_cpu_parity.cpp",
                         "scripts/engine_tokenizer_parity.py"} <= seen)
        names = ("tau_preflight", "tau_trial_worker", "tau_aggregate", "tau_transport",
                 "tau_local_vllm", "tau_local_llama", "engine_tokenizer_parity",
                 "tau_llama_identity", "tau_live_identity", "tau_pipe_driver")
        files = {"scripts/" + name + ".py": tau.digest((ROOT / "scripts" / (name + ".py")).read_bytes())
                 for name in names}
        for name in ("tau_local_llama", "tau_llama_identity", "engine_tokenizer_parity"):
            changed = {**files, "scripts/" + name + ".py": "f" * 64}
            with self.subTest(name=name), self.assertRaises(ValueError):
                fixtures.runner.parent_driver({"files": changed}, live_local=True)

    def test_cpu_whole_gguf_identity_is_distinct_from_hf_files_and_from_other_model(self):
        values = [live.cpu.identity(model, time.monotonic() + 5) for model in sorted(live.llama.MODELS)]
        for key in ("model_files_sha256", "tokenizer_sha256", "framing_sha256"):
            self.assertNotEqual(values[0][key], values[1][key])
        self.assertNotIn("tokenizer_config_sha256", values[0])
        self.assertNotIn("chat_template_sha256", values[0])
        self.assertEqual(set(live.cpu.case_hashes()), {"text", "system", "tools", "tool-continuation"})
        with self.assertRaises(ValueError):
            live.cpu.identity("arbitrary-model", time.monotonic() + 5)

    def test_cpu_guard_applies_before_each_template_tokenize_and_chat_body(self):
        for path in live.llama.LocalLlamaCPP.PATHS:
            for after_connect in (False, True):
                with self.subTest(path=path, after_connect=after_connect), fixtures.http.endpoint([]) as (port, seen):
                    endpoint = mock.Mock(port=port)
                    endpoint.check.side_effect = ([None, worker.Abort("protocol_error")] if after_connect
                                                  else worker.Abort("protocol_error"))
                    client = live.GuardedLlamaClient(endpoint, mock.Mock(), "qwen/qwen3-0.6b")
                    with self.assertRaises(worker.Abort):
                        client.post(path, {"private": fixtures.CANARY}, time.monotonic() + 2)
                    self.assertEqual(seen, [])

    def test_mixed_pipe_runs_existing_accounting_and_scorer_without_quality_promotion(self):
        for failure in (None, "usage", "truncated"):
            with self.subTest(failure=failure), contextlib.ExitStack() as stack:
                ports, seen = {}, {}
                for role, model in (("candidate", "synthetic-candidate"),
                                    ("simulator", "qwen/qwen3-0.6b"), ("evaluator", "qwen/qwen3-1.7b")):
                    response = fixtures.http.completion(model)
                    if role == "evaluator":
                        response["choices"][0]["message"]["content"] = '{"results":[]}'
                    if role == "simulator" and failure == "usage":
                        response["usage"] = None
                    if role == "simulator" and failure == "truncated":
                        response["choices"][0]["finish_reason"] = "length"
                    replies = ([fixtures.http.counted()] if role == "candidate" else
                               [{"prompt": fixtures.CANARY}, {"tokens": [1] * 7}]) + [response]
                    ports[role], seen[role] = stack.enter_context(fixtures.http.endpoint(replies))
                root = Path(stack.enter_context(tempfile.TemporaryDirectory())).resolve()
                benchmark, docs = documents(ports)
                sha = fixtures.write_bundle(root, docs)
                stack.enter_context(mock.patch.object(live, "source_receipt"))
                stack.enter_context(mock.patch.object(live.Endpoint, "check"))
                deployment = live.Deployment(root / "deployment.json", sha, benchmark, time.monotonic() + 5)
                plan = fixtures.plan_for(benchmark)
                fixtures.task_payload(root, plan)
                child = fixtures.pipes.CHILD.replace("('agent', 'simulator')", "('agent', 'simulator', 'evaluator')")
                result = fixtures.driver.run_process(
                    [sys.executable, "-I", "-S", "-B", "-c", child, str(ROOT / "scripts")],
                    {}, root, plan, benchmark, time.monotonic() + 5, deployment=deployment)
                fixtures.runner.check_result(result, plan, benchmark, live_local=True)
                row = result["report"]["trials"][0]
                self.assertEqual([path for path, _ in seen["simulator"]], list(live.llama.LocalLlamaCPP.PATHS))
                self.assertFalse(result["quality_accepted"] or result["confidential"])
                self.assertEqual(result["report"]["kind"], "declared-unverified")
                if failure:
                    self.assertNotEqual(row["status"], "evaluated")
                    self.assertIsNone(row["reward"])
                    self.assertEqual(seen["evaluator"], [])
                    self.assertGreater(result["accounting"]["reserved_tokens"], 0)
                else:
                    self.assertEqual(row["status"], "evaluated")
                    self.assertEqual(result["accounting"]["role_requests"], {"agent": 1, "simulator": 1, "evaluator": 1})


if __name__ == "__main__":
    unittest.main()
