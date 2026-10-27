# SPDX-License-Identifier: Apache-2.0
"""Source/paired-wire/admission tests; every HTTP reply is synthetic."""

import copy
from decimal import Decimal
from contextlib import redirect_stdout, redirect_stderr
import io
import os
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

import tau_external_support as support
import test_tau_external as fixtures
import test_tau_external_trial as full
import test_tau_telecom_catalog as catalogs
import test_tau_external_selection as selections

external, worker = support.external, support.worker
ARCHIVE = Path("/synthetic/archive")


def retail_source():
    source = object.__new__(support.Source)
    source.domain, source.tools, source.assertion_count = "retail", None, 1
    source.binding = {"schema": support.SCHEMA, "source_authenticated": False, "test_fixture": True}
    source.sha256 = worker.digest(worker.encoded(source.binding))
    return source


def endpoint(role="simulator", *, source=None, admission=None, budget=None):
    http = fixtures.FakeHTTP("candidate")
    http.result.update(model=external.policy.PUBLIC_MODEL if role == "candidate" else external.policy.EXTERNAL_MODEL,
                       service_tier="default")
    return support.Endpoint(role, http, b"SYNTHETIC_KEY_CANARY", types.SimpleNamespace(count=lambda body: 20),
        full_task=True, support_chat=True, source=source or retail_source(),
        admission=admission or support.Admission(full.configuration()), count_budget=budget or external.CountBudget())


def endpoint_contract():
    config = full.configuration()
    return {"schema": "nib.tau-external-trial-endpoint/v2", "hostname": "candidate.example.test",
        "address": "8.8.8.8", "port": 18443, "model": external.policy.PUBLIC_MODEL, "context_tokens": 32768,
        "output_tokens": 2048, "prices": config["prices"], "simulator": external.policy.EXTERNAL_MODEL,
        "evaluator": external.policy.EXTERNAL_MODEL, "candidate_profile": "quality32k-eager",
        "retire_at": int(time.time()) + 1000, "conformance_sha256": "a" * 64,
        "certificate": "/synthetic-cert.pem", "certificate_sha256": "c" * 64,
        "serving_profile": "long1m-graphs", "serving_context_tokens": 1048576}


class SupportTests(unittest.TestCase):
    def test_airline_requires_authenticated_new_plan_before_any_transport(self):
        _, sources, pins = selections.fixtures(["airline"], trials=1)
        plan = catalogs.selection.build(sources, pins, "a" * 40, ["airline"], 1, 42, selections.BUDGET,
                                        aggregate_cap_usd="200")
        raw = worker.encoded(pins)
        with mock.patch.object(support.runtime.tau, "PINS_SHA", worker.digest(raw)), \
                mock.patch.object(support.runtime, "read_file", return_value=raw), \
                mock.patch.object(support.runtime, "source_inputs", return_value=({}, sources)), \
                mock.patch.object(external.bounded_https, "HTTPS") as http:
            stage = {"plan": plan, "index": 0}
            source = support.Source(ARCHIVE, stage, time.monotonic() + 10)
            self.assertEqual(source.domain, "airline")
            self.assertEqual(source.assertion_count, 1)
            self.assertIsNone(source.tools)
            self.assertEqual(source.binding["stage_binding"], catalogs.selection.binding(stage))
            for role in ("simulator", "evaluator"):
                chat, count = source.project(role, full.body(role), time.monotonic() + 10)
                self.assertEqual(count, {"model": chat["model"], "input": chat["messages"], "truncation": "disabled"})
            for mutate in (lambda p: p.update(schema=catalogs.selection.SEQUENTIAL_SCHEMA),
                           lambda p: p["domain_tasks"]["airline"]["assertion_counts"].__setitem__(0, 2),
                           lambda p: p["source_files"][next(iter(sources))].update(sha256="f" * 64)):
                forged = copy.deepcopy(plan)
                mutate(forged)
                with self.assertRaises(ValueError): support.Source(ARCHIVE, {"plan": forged, "index": 0}, time.monotonic() + 10)
            http.assert_not_called()

    def test_authenticated_catalog_and_original_tool_history_on_both_wires(self):
        with catalogs.bound_fixture() as contract:
            source = support.Source(ARCHIVE, contract["stage"], time.monotonic() + 10)
        client, body = endpoint(source=source), catalogs.body()
        frozen = copy.deepcopy(body)
        count = client.count(body, 32768, time.monotonic() + 5)
        client.complete(body, count, time.monotonic() + 5)
        self.assertEqual(body, frozen)
        counted, chat = [row[1] for row in client.http.calls]
        self.assertEqual(chat["tools"], frozen["tools"])
        self.assertEqual(chat["seed"], frozen["seed"])
        self.assertEqual(chat["messages"][-1], frozen["messages"][-1])
        self.assertEqual(len(counted["tools"]), 30)
        self.assertTrue(all(tool["strict"] is False for tool in counted["tools"]))
        self.assertEqual(counted["input"][-3]["arguments"], " { } ")
        self.assertEqual(counted["input"][-2]["output"], frozen["messages"][-2]["content"])
        observed = client.observation()
        self.assertIs(observed["support_source"]["source_authenticated"], True)
        self.assertEqual(observed["count_usage_pairs"][0]["status"], "matched")
        self.assertIs(observed["observed_pairs_match"], True)
        self.assertIs(observed["token_count_parity_verified"], False)
        self.assertIs(observed["live_admission"], False)
        self.assertNotIn("CANARY", str(observed))
        self.assertEqual(observed["generation_admission"]["held_usd"], "0")

    def test_text_simulator_and_original_evaluator_use_exact_messages(self):
        for role in ("simulator", "evaluator"):
            with self.subTest(role=role):
                client, body = endpoint(role), full.body(role)
                body["seed"] = 42
                count = client.count(body, 32768, time.monotonic() + 5)
                client.complete(body, count, time.monotonic() + 5)
                counted, chat = [row[1] for row in client.http.calls]
                self.assertEqual(counted["input"], body["messages"])
                self.assertEqual(chat["messages"], counted["input"])
                self.assertEqual(chat["seed"], 42)
                self.assertIs(chat["store"], False)
                self.assertEqual(chat["max_completion_tokens"], 2048)

    def test_new_tools_never_enter_legacy_or_candidate_paths(self):
        body = catalogs.body()
        with self.assertRaises(ValueError): external.simulator_request(body, full_task=True, support_chat=True)
        body["model"] = external.policy.PUBLIC_MODEL
        client = endpoint("candidate")
        with self.assertRaises(ValueError): client.count(body, 32768, time.monotonic() + 5)
        self.assertEqual(client.http.calls, [])

    def test_catalog_mutation_foreign_tools_and_unexpected_judge_fail_before_http(self):
        with catalogs.bound_fixture() as contract:
            source = support.Source(ARCHIVE, contract["stage"], time.monotonic() + 10)
        for mutate in (lambda b: b["tools"].pop(), lambda b: b["tools"][0]["function"].update(description="forged"),
                       lambda b: b.update(seed=True), lambda b: b.update(max_completion_tokens=2049),
                       lambda b: b["messages"].extend([{"role": "user", "content": "x"}] * 128)):
            body, client = catalogs.body(), endpoint(source=source)
            mutate(body)
            with self.assertRaises(ValueError): client.count(body, 32768, time.monotonic() + 5)
            self.assertEqual(client.http.calls, [])
        judge = endpoint("evaluator", source=source)
        with self.assertRaises(ValueError): judge.count(full.body("evaluator"), 32768, time.monotonic() + 5)
        self.assertEqual(judge.http.calls, [])
        source.tools[0]["function"]["name"] = "mutated"
        client = endpoint(source=source)
        with self.assertRaises(ValueError): client.count(catalogs.body(), 32768, time.monotonic() + 5)
        self.assertEqual(client.http.calls, [])

    def test_count_bounds_and_malformed_counts_are_sticky(self):
        for value in (0, -1, True, "20", 30721, 1047576):
            client = endpoint()
            client.http.count = value
            with self.assertRaises(ValueError): client.count(full.body("simulator"), 32768, time.monotonic() + 5)
            self.assertEqual(client.count_attempts, 1)
            self.assertEqual(client.attempts, 0)
            self.assertEqual(client.observation()["count_cost_reserved_usd"], "2.095152")
            with self.assertRaises(ValueError): client.count(full.body("simulator"), 32768, time.monotonic() + 5)
            self.assertEqual(len(client.http.calls), 1)
        client = endpoint()
        client.http.count = 30720
        self.assertEqual(client.count(full.body("simulator"), 32768, time.monotonic() + 5), 30720)
        client.http.result["usage"] = {"prompt_tokens": 30720, "completion_tokens": 2048, "total_tokens": 32768}
        client.complete(full.body("simulator"), 30720, time.monotonic() + 5)
        self.assertEqual(client.admission.spent, Decimal("0.077824"))

    def test_count_transport_failure_keeps_count_charge_and_never_generates(self):
        for failure in (OSError("PRIVATE_CANARY"), KeyboardInterrupt()):
            client = endpoint()
            client.http.post = mock.Mock(side_effect=failure)
            with self.assertRaises(BaseException): client.count(full.body("simulator"), 32768, time.monotonic() + 5)
            self.assertEqual(client.count_budget.attempts, 1)
            self.assertEqual(client.observation()["count_cost_reserved_usd"], "2.095152")
            self.assertIs(client.admission.failed, True)
            self.assertEqual(client.attempts, 0)
            self.assertNotIn("CANARY", str(client.observation()))

    def test_mismatch_lost_response_cancellation_and_content_failure_hold_reservation(self):
        for failure in ("mismatch", "lost", "cancel", "content"):
            with self.subTest(failure=failure):
                admission = support.Admission(full.configuration())
                client, body = endpoint(admission=admission), full.body("simulator")
                count = client.count(body, 32768, time.monotonic() + 5)
                if failure == "mismatch":
                    client.http.result["usage"].update(prompt_tokens=21, total_tokens=24)
                elif failure in ("lost", "cancel"):
                    client.http.result = KeyboardInterrupt() if failure == "cancel" else OSError("PRIVATE_EXCEPTION_CANARY")
                else:
                    client.http.result["choices"][0]["message"]["refusal"] = "PRIVATE_REFUSAL_CANARY"
                with self.assertRaises(BaseException): client.complete(body, count, time.monotonic() + 5)
                observed = client.observation()
                self.assertIs(admission.failed, True)
                self.assertEqual(admission.observation()["held_usd"], "2.111536")
                self.assertNotIn("CANARY", str(observed))
                self.assertEqual(observed["unknown_responses"], int(failure in ("lost", "cancel")))
                self.assertEqual(observed["count_usage_pairs"][0]["status"], "mismatch" if failure == "mismatch" else "rejected")
                other = endpoint("candidate", admission=admission)
                with self.assertRaises(ValueError): other.count(full.body(), 32768, time.monotonic() + 5)
                self.assertEqual(other.http.calls, [])

    def test_invalid_usage_and_tier_cannot_release_generation_reservation(self):
        for mutate in (lambda row: row["usage"].update(prompt_tokens=True),
                       lambda row: row["usage"].update(prompt_tokens=32769, total_tokens=32772),
                       lambda row: row["usage"].update(completion_tokens=2049, total_tokens=2069),
                       lambda row: row.update(service_tier="priority"),
                       lambda row: row.update(model="other-model")):
            client, body = endpoint(), full.body("simulator")
            count = client.count(body, 32768, time.monotonic() + 5)
            mutate(client.http.result)
            with self.assertRaises(ValueError): client.complete(body, count, time.monotonic() + 5)
            self.assertEqual(client.admission.observation()["held_usd"], "2.111536")
            self.assertIs(client.admission.failed, True)

    def test_success_releases_only_unused_known_generation_reservation(self):
        admission, budget = support.Admission(full.configuration()), external.CountBudget()
        clients = {role: endpoint(role, admission=admission, budget=budget) for role in ("candidate", "simulator", "evaluator")}
        for role in clients:
            client, body = clients[role], full.body(role)
            count = client.count(body, 32768, time.monotonic() + 5)
            client.complete(body, count, time.monotonic() + 5)
        self.assertEqual(admission.spent, Decimal("0.00014535"))
        self.assertIsNone(admission.pending)
        self.assertEqual(budget.attempts, 2)
        self.assertEqual(sum(Decimal(c.observation()["count_cost_reserved_usd"]) for c in clients.values()), Decimal("4.190304"))
        self.assertTrue(support.accepted({role: client.observation() for role, client in clients.items()}))
        for mutate in (lambda rows: rows["simulator"]["generation_admission"].update(blocked=True),
                       lambda rows: rows["evaluator"]["count_usage_pairs"][0].update(prompt_tokens=21),
                       lambda rows: rows["evaluator"].update(count_attempts=2),
                       lambda rows: rows.pop("candidate")):
            rows = {role: client.observation() for role, client in clients.items()}
            mutate(rows)
            self.assertFalse(support.accepted(rows))

    def test_worst_case_admission_boundary_precedes_paid_count(self):
        for spent, allowed in (("0.888464", True), ("0.888465", False)):
            client = endpoint()
            client.admission.spent = Decimal(spent)
            if allowed:
                client.count(full.body("simulator"), 32768, time.monotonic() + 5)
                self.assertEqual(client.admission.ceiling("simulator"), Decimal("2.111536"))
            else:
                with self.assertRaises(ValueError): client.count(full.body("simulator"), 32768, time.monotonic() + 5)
                self.assertEqual(client.http.calls, [])
                self.assertEqual(client.count_attempts, 0)

    def test_shared_generation_and_count_caps_remain_32(self):
        admission, budget = support.Admission(full.configuration()), external.CountBudget()
        clients = [endpoint(role, admission=admission, budget=budget) for role in ("simulator", "evaluator")]
        for index in range(32):
            client = clients[index % 2]
            body = full.body(client.role)
            count = client.count(body, 32768, time.monotonic() + 5)
            client.complete(body, count, time.monotonic() + 5)
        self.assertEqual(budget.attempts, 32)
        self.assertEqual(admission.reservations, 32)
        with self.assertRaises(ValueError): clients[0].count(full.body("simulator"), 32768, time.monotonic() + 5)
        self.assertEqual(sum(c.count_attempts for c in clients), 32)
        client = endpoint()
        client.count_budget.attempts = 32
        with self.assertRaises(ValueError): client.count(full.body("simulator"), 32768, time.monotonic() + 5)
        self.assertEqual(client.http.calls, [])

    def test_changed_body_bool_count_and_unsettled_generation_are_rejected(self):
        for failure in ("body", "bool", "pending"):
            client, body = endpoint(), full.body("simulator")
            count = client.count(body, 32768, time.monotonic() + 5)
            if failure == "body": body["messages"][0]["content"] += "altered"
            elif failure == "bool": count = True
            else: client.admission.reserve("candidate")
            with self.assertRaises(ValueError): client.complete(body, count, time.monotonic() + 5)
            self.assertEqual(client.attempts, 0)

    def test_source_forgery_domain_and_budget_reject_before_endpoint(self):
        with catalogs.bound_fixture() as contract:
            for mutate in (lambda p: p["plan"]["source_files"][next(iter(p["plan"]["source_files"]))].update(sha256="0" * 64),
                           lambda p: p["plan"]["budget"]["inputs"].update(other_committed_usd="10000"),
                           lambda p: p.update(index=5)):
                stage = copy.deepcopy(contract["stage"])
                mutate(stage)
                with self.assertRaises(ValueError): support.Source(ARCHIVE, stage, time.monotonic() + 5)
            with mock.patch.object(support.runtime, "source_inputs", side_effect=ValueError("private")), self.assertRaises(ValueError):
                support.Source(ARCHIVE, contract["stage"], time.monotonic() + 5)
            with self.assertRaises(ValueError): support.Source(ARCHIVE, contract["stage"], time.monotonic() - 1)

    def test_runtime_authenticates_source_before_keys_tokenizer_or_http(self):
        contract = endpoint_contract()
        raw = worker.encoded(contract)
        with mock.patch.object(external.private, "private_read", return_value=raw) as read, \
                mock.patch.object(support, "Source", side_effect=ValueError("private")), \
                mock.patch.object(external.tau_external_tokenizer, "Counter") as tokenizer, \
                mock.patch.object(external.bounded_https, "HTTPS") as http:
            with self.assertRaises(ValueError):
                external.Runtime(None, worker.digest(raw), None, None, None, None, None, time.monotonic() + 90,
                                 full_task=True, support_source=ARCHIVE)
            self.assertEqual(read.call_count, 1)
            tokenizer.assert_not_called()
            http.assert_not_called()

    def test_opt_in_cli_and_verified_module_closure(self):
        runner = fixtures.runner
        names = ("tau_preflight", "tau_trial_worker", "tau_aggregate", "tau_transport", *runner.SELECTED_PARENT_MODULES,
                 "tau_pipe_driver", "tau_selected_driver", *runner.EXTERNAL_PARENT_MODULES)
        toolkit = {"files": {"scripts/" + name + ".py": worker.digest((runner.ROOT / ("scripts/" + name + ".py")).read_bytes())
                             for name in names}}
        with mock.patch.dict(sys.modules, {"tau_external_support": object(), "tau_telecom_projection": object()}):
            driver = runner.parent_driver(toolkit, selected_operational=True, external_simulator=True)
            self.assertEqual(driver.source_support().SCHEMA, support.SCHEMA)
        toolkit["files"]["scripts/tau_external_support.py"] = "0" * 64
        with self.assertRaises(ValueError): runner.parent_driver(toolkit, selected_operational=True, external_simulator=True)
        entry = runner.load("scripts/record-tau-external.py")
        with tempfile.TemporaryDirectory() as directory:
            argv = ["record-tau-external.py", "run", "--full-task", "--support-source-archive", str(ARCHIVE)]
            for name in ("payload", "docker-socket", "contract", "candidate-key", "simulator-key", "vllm-source", "tokenizer", "tokenizer-wheel"):
                argv.extend(["--" + name, "/unused"])
            cutoff = entry.datetime.datetime.fromtimestamp(time.time() + 90, entry.datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            argv.extend(["--output", directory + "/result", "--plan-sha256", "a" * 64, "--contract-sha256", "b" * 64, "--deadline-utc", cutoff])
            with mock.patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()), \
                    mock.patch.object(entry.trial, "identity", return_value={}), \
                    mock.patch.object(entry.trial, "run", return_value={"result": "pass", "cleanup": "confirmed"}) as run:
                self.assertEqual(entry.main(), 0)
                self.assertEqual(run.call_args.kwargs["external_inputs"]["support_source"], ARCHIVE)
            argv.remove("--full-task")
            with mock.patch.object(sys, "argv", argv), redirect_stderr(io.StringIO()), \
                    mock.patch.object(entry.trial, "run") as run:
                self.assertEqual(entry.main(), 1)
                run.assert_not_called()

    @unittest.skipUnless(os.environ.get("NIB_TAU_SOURCE_ARCHIVE"), "explicit pinned archive required")
    def test_actual_source_legacy_evaluator_and_both_staged_domains(self):
        archive = Path(os.environ["NIB_TAU_SOURCE_ARCHIVE"])
        deadline = time.monotonic() + 60
        original = support.Source(archive, None, deadline)
        self.assertEqual(original.assertion_count, 1)
        pins = support.runtime.tau.json_value(support.runtime.read_file(support.runtime.ROOT / support.runtime.tau.PINS, 1 << 20, deadline))
        _, sources = support.runtime.source_inputs(support.runtime.read_file(archive, support.runtime.ARCHIVE_LIMIT, deadline), pins, deadline)
        budget = {"weekly_cap_usd": "10000", "gpu_committed_usd": "0", "cpu_disk_reserved_usd": "0", "other_committed_usd": "0"}
        for domain in ("retail", "telecom", "airline"):
            plan = catalogs.selection.build(sources, pins, "a" * 40, [domain], 1, 42, budget)
            for index in range(5):
                stage = {"plan": plan, "index": index}
                if domain == "airline":
                    with self.assertRaises(ValueError): support.Source(archive, stage, deadline)
                    stage = {"plan": catalogs.selection.build(sources, pins, "a" * 40, [domain], 1, 42, budget,
                                                               aggregate_cap_usd="200"), "index": index}
                source = support.Source(archive, stage, deadline)
                self.assertEqual(source.assertion_count, [1, 1, 1, 3, 2][index] if domain == "airline" else 0)
                self.assertIs(source.binding["source_authenticated"], True)
                self.assertEqual(len(source.tools) if source.tools is not None else 0, 30 if domain == "telecom" else 0)
                if domain == "airline":
                    for role in ("simulator", "evaluator"):
                        chat, counted = source.project(role, full.body(role), deadline)
                        self.assertNotIn("tools", chat)
                        self.assertEqual(counted, {"model": chat["model"], "input": chat["messages"], "truncation": "disabled"})
                    forged = copy.deepcopy(stage)
                    forged["plan"]["schema"] = catalogs.selection.SEQUENTIAL_SCHEMA
                    with self.assertRaises(ValueError): support.Source(archive, forged, deadline)
        raw = worker.encoded(endpoint_contract())
        counter = types.SimpleNamespace(binding="b" * 64, close=mock.Mock())
        with mock.patch.object(external.private, "private_read", side_effect=[raw, b"SYNTHETIC_PEM",
                b"SYNTHETIC_CANDIDATE_KEY", b"SYNTHETIC_SIMULATOR_KEY"]), \
                mock.patch.object(external.tau_external_tokenizer, "Counter", return_value=counter), \
                mock.patch.object(external.bounded_https, "HTTPS") as http:
            held = external.Runtime(None, worker.digest(raw), None, None, None, None, None, time.monotonic() + 90,
                                    full_task=True, support_source=archive)
            self.assertTrue(all(type(row) is support.Endpoint for row in held.endpoints.values()))
            self.assertIs(held.endpoints["candidate"].admission, held.endpoints["evaluator"].admission)
            self.assertIs(held.endpoints["simulator"].count_budget, held.endpoints["evaluator"].count_budget)
            self.assertEqual(http.call_args_list[-1].args, ("api.openai.com", (external.COUNT, external.CHAT)))
            self.assertNotEqual(held.manifest["framing"]["simulator"]["framing_sha256"], external.COUNT_BINDING)
            held.close()
            counter.close.assert_called_once()
            self.assertTrue(all(row.key == b"" and row.ready is None for row in held.endpoints.values()))


if __name__ == "__main__":
    unittest.main()
