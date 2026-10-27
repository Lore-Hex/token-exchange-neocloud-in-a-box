# SPDX-License-Identifier: Apache-2.0
"""Serial admission with synthetic replies, no keys, HTTP or paid counts."""

import copy
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
import io
import hashlib
import os
from pathlib import Path
import signal
import stat
import sys
import subprocess
import tempfile
import time
import types
import unittest
from unittest import mock

import tau_external_aggregate as aggregate
import test_tau_external_selection as selections
import test_tau_external_support as support_tests

tau, selection = aggregate.tau, aggregate.selection
TOOLKIT = {"git_sha": "a" * 40, "files": {"synthetic": "b" * 64}}
PAYLOAD, CONTRACT = "c" * 64, "d" * 64


def plan(cap="200", trials=1, domains=None, assertions=1):
    old, sources, pins = selections.fixtures(domains, trials)
    if assertions != 1:
        for name, raw in list(sources.items()):
            if name.endswith("/tasks.json"):
                rows = tau.json_value(raw)
                for row in rows["tasks"]:
                    row["evaluation_criteria"]["nl_assertions"] = ["synthetic"] * assertions
                raw = sources[name] = tau.encoded(rows)
                pins["files"][name] = {"bytes": len(raw), "sha256": tau.digest(raw),
                    "git_blob": hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()}
    value = selection.build(sources, pins, TOOLKIT["git_sha"], old["domains"], trials, 42,
                            old["budget"]["inputs"], aggregate_cap_usd=cap)
    return value, sources, pins


def receipt(value, index=0):
    stage = {"plan": value, "index": index}
    selected, binding = selection.selected(stage), selection.binding(stage)
    source = support_tests.retail_source()
    source.assertion_count = selection.assertion_count(stage)
    # Explicit synthetic source stand-in. Real ownership authenticates the archive
    # before recording this digest; these fixtures never grant source admission.
    source.binding.update(source_authenticated=True, selection=selected, stage_binding=binding)
    source.sha256 = tau.digest(tau.encoded(source.binding))
    config = aggregate.external.policy.configuration(full_task=True, external_simulator=True, stage=stage)
    admission = support_tests.support.Admission(config)
    budget = aggregate.external.CountBudget()
    clients = {role: support_tests.endpoint(role, source=source, admission=admission, budget=budget)
               for role in ("candidate", "simulator", "evaluator")}
    for role, client in clients.items():
        if role == "evaluator" and selection.assertion_count(stage) == 0:
            continue
        body = support_tests.full.body(role)
        count = client.count(body, 32768, time.monotonic() + 10)
        client.complete(body, count, time.monotonic() + 10)
    endpoints = {role: client.observation() for role, client in clients.items()}
    usage = {key: sum(row["known_usage"][key] for row in endpoints.values())
             for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
    usage["cost_usd"] = format(admission.spent, "f")
    diagnostic = {"schema": "nib.tau-external-staged/v2", "selection": selected, "stage_binding": binding,
        "nl_assertion_count": selection.assertion_count(stage), "cost_ceiling_usd": str(selection.TRIAL_RESERVATION),
        "generation_cost_ceiling_usd": "3.00", "endpoints": endpoints,
        "scorer_protocol_completed": True, "attach_cleanup": "reaped", "scorer_exit_code": 0,
        "trial": {"status": "evaluated", "reward": "0.0", "usage": usage,
                  "upstream_termination": {"reason": "user_stop", "tool_errors": 0}}}
    record = {"schema": "nib.tau-external-staged-run/v1", "result": "pass", "stage": "completed",
        "cleanup": "confirmed", "toolkit": copy.deepcopy(TOOLKIT), "plan_sha256": PAYLOAD,
        "endpoint_binding_sha256": CONTRACT, "selection": selected, "stage_binding": binding,
        "trial": diagnostic, "endpoint_usage": endpoints,
        **{key: False for key in ("quality_eligible", "confidential", "live_admission", "public_owner_mutated", "full_benchmark")}}
    return record, source.sha256


class PlanTests(unittest.TestCase):
    def test_legacy_plan_bytes_equal_reviewed_base(self):
        raw = subprocess.run(["git", "show", "87fbe32ad01f013de60b53eddfbfa592d1777ed7:scripts/tau_external_selection.py"],
            cwd=selections.runner.ROOT, capture_output=True, check=True, timeout=10).stdout
        original = types.ModuleType("prior_selection")
        exec(compile(raw, "<reviewed-selection>", "exec"), original.__dict__)
        for domains in (["retail"], ["telecom"], list(tau.DOMAINS)):
            value, sources, pins = selections.fixtures(domains, trials=3)
            self.assertEqual(tau.encoded(value), tau.encoded(original.build(sources, pins, "a" * 40,
                domains, 3, 42, selections.BUDGET)))

    def test_v1_sequential_bytes_unchanged_and_airline_requires_v2(self):
        raw = subprocess.run(["git", "show", "43ad42ddee27d1ece4a512331d5e42f7953c9c14:scripts/tau_external_selection.py"],
            cwd=selections.runner.ROOT, capture_output=True, check=True, timeout=10).stdout
        original = types.ModuleType("prior_sequential_selection")
        exec(compile(raw, "<reviewed-sequential-selection>", "exec"), original.__dict__)
        for domains in (["retail"], ["telecom"], ["retail", "telecom"]):
            for trials in (1, 3, 4):
                value, sources, pins = plan(domains=domains, trials=trials)
                self.assertEqual(tau.encoded(value), tau.encoded(original.build(sources, pins, TOOLKIT["git_sha"],
                    domains, trials, 42, selections.BUDGET, aggregate_cap_usd="200")))
        for domains in (["airline"], list(tau.DOMAINS)):
            for trials in (1, 3, 4):
                value, sources, pins = plan(domains=domains, trials=trials)
                self.assertEqual(value["schema"], selection.AIRLINE_SEQUENTIAL_SCHEMA)
                selection.verify_sources(value, sources, pins)
                old = selection.build(sources, pins, TOOLKIT["git_sha"], domains, trials, 42, selections.BUDGET)
                self.assertEqual(value["selections"], old["selections"])
                self.assertEqual(value["trial_seeds"], old["trial_seeds"])
                with self.assertRaises(ValueError): original.validate(value)
                with self.assertRaises(ValueError): selection.validate({**value, "schema": selection.SEQUENTIAL_SCHEMA})
        value = plan()[0]
        with self.assertRaises(ValueError): selection.validate({**value, "schema": selection.AIRLINE_SEQUENTIAL_SCHEMA})

    def test_sequential_cap_keeps_all_frozen_rows_and_legacy_full_sum(self):
        value, sources, pins = plan(trials=3)
        old = selection.build(sources, pins, TOOLKIT["git_sha"], ["retail"], 3, 42, selections.BUDGET)
        self.assertEqual(old["schema"], selection.SCHEMA)
        self.assertEqual(old["result"], "fail")
        self.assertEqual(old["budget"]["trials_reserved_usd"], "1050.672960")
        self.assertEqual(value["selections"], old["selections"])
        self.assertEqual(value["trial_seeds"], old["trial_seeds"])
        self.assertEqual(len(value["selections"]), 15)
        self.assertEqual(value["schema"], selection.SEQUENTIAL_SCHEMA)
        self.assertEqual(value["result"], "pass")
        self.assertEqual(value["budget"]["planned_worst_case_usd"], "1050.672960")
        self.assertEqual(value["budget"]["trials_reserved_usd"], "200")
        selection.verify_sources(value, sources, pins)
        self.assertEqual(selection.validate(value), value)
        for key in ("execution_authorized", "benchmark_executed", "quality_eligible", "full_benchmark", "live_admission"):
            self.assertIs(value[key], False)

    def test_cap_bounds_do_not_assume_counts_free_or_available_invoice(self):
        self.assertEqual(plan(str(selection.TRIAL_RESERVATION))[0]["result"], "pass")
        for cap in ("70.044863", "0", True, 200, "NaN", "Infinity", "1e2", "10000.000001"):
            with self.subTest(cap=cap), self.assertRaises(ValueError):
                plan(cap)
        value = plan("674")[0]
        self.assertTrue(value["budget"]["within_cap"])
        self.assertEqual(value["budget"]["remaining_usd"], "0")
        self.assertEqual(value["budget"]["basis"], "operator-declared-reservations-not-invoice")
        self.assertEqual(plan("674.000001")[0]["result"], "fail")
        for domains in (["retail"], ["telecom"], ["retail", "telecom"], ["airline"], list(tau.DOMAINS)):
            self.assertEqual(plan(domains=domains)[0]["result"], "pass")

    def test_forged_cap_source_order_and_prior_schema_cannot_admit(self):
        value, sources, pins = plan()
        for mutate in (lambda p: p.update(aggregate_cap_usd="300"), lambda p: p.update(schema=selection.SCHEMA),
                       lambda p: p["budget"].update(within_cap=False),
                       lambda p: p["budget"].update(count_usd_per_attempt="0"),
                       lambda p: p["selections"].reverse()):
            changed = copy.deepcopy(value)
            mutate(changed)
            with self.assertRaises(ValueError):
                selection.validate(changed)
        value["source_files"][next(iter(sources))]["sha256"] = "f" * 64
        selection.validate(value)
        with self.assertRaises(ValueError):
            selection.verify_sources(value, sources, pins)


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name).resolve() / "journal"
        self.value = plan()[0]

    def journal(self, value=None):
        journal = aggregate.Journal(self.path, value or self.value, CONTRACT, TOOLKIT)
        self.addCleanup(journal.close)
        return journal

    def claim(self, journal):
        record, source_sha = receipt(journal.plan, journal.index)
        self.assertTrue(journal.reserve())
        journal.claim({"plan": journal.plan, "index": journal.index}, PAYLOAD, CONTRACT, TOOLKIT, source_sha)
        return record

    def test_full_hold_precedes_claim_and_only_validated_known_unused_is_released(self):
        journal = self.journal()
        self.assertTrue(journal.reserve())
        event = tau.json_value((self.path / "event-001.json").read_bytes())
        self.assertEqual(event["kind"], "reserved")
        self.assertEqual(event["data"]["held_usd"], "70.044864")
        with self.assertRaises(ValueError):
            journal.reserve()
        record, sha = receipt(self.value)
        journal.claim({"plan": self.value, "index": 0}, PAYLOAD, CONTRACT, TOOLKIT, sha)
        raw = tau.encoded(record)
        journal.settle(record, raw)
        self.assertEqual(journal.spent, Decimal("4.19044935"))
        self.assertIsNone(journal.pending)
        self.assertEqual(journal.index, 1)
        self.assertEqual((self.path / "receipt-00.json").read_bytes(), raw)
        event = tau.json_value((self.path / "event-003.json").read_bytes())
        self.assertEqual(event["data"]["count_held_usd"], "4.190304")
        self.assertEqual(event["data"]["reward"], "0.0")
        self.assertEqual(event["data"]["receipt_sha256"], tau.digest(raw))
        self.assertTrue(journal.reserve())

    def test_next_trial_needs_full_ceiling_even_when_last_was_cheap(self):
        for cap, can_continue in (("74.23531335", True), ("74.235313349", False)):
            with self.subTest(cap=cap), tempfile.TemporaryDirectory() as directory:
                value = plan(cap)[0]
                journal = aggregate.Journal(Path(directory).resolve() / "run", value, CONTRACT, TOOLKIT)
                try:
                    record = self.claim(journal)
                    journal.settle(record, tau.encoded(record))
                    self.assertEqual(journal.reserve(), can_continue)
                    if not can_continue:
                        result = journal.stop("cap-exhausted", stage="reservation")
                        self.assertEqual(result["planned_trials"], 5)
                        self.assertEqual(result["completed_trials"], 1)
                        self.assertEqual(result["held_trial_usd"], "0")
                        self.assertEqual(result["result"], "incomplete")
                finally:
                    journal.close()

    def test_complete_repeated_plan_keeps_each_original_receipt_and_chain(self):
        value = plan(trials=3)[0]
        journal = self.journal(value)
        for index in range(15):
            self.assertEqual(journal.index, index)
            record = self.claim(journal)
            journal.settle(record, tau.encoded(record))
        result = journal.stop("complete")
        self.assertEqual(result["completed_trials"], 15)
        self.assertEqual(result["charged_reservation_usd"], "62.85674025")
        self.assertEqual(result["held_trial_usd"], "0")
        self.assertIs(result["quality_eligible"], False)
        previous = None
        for index, path in enumerate(sorted(self.path.glob("event-*.json"))):
            raw = path.read_bytes()
            event = tau.json_value(raw)
            self.assertEqual(event["sequence"], index)
            self.assertEqual(event["previous_sha256"], previous)
            previous = tau.digest(raw)
        self.assertEqual(len(list(self.path.glob("receipt-*.json"))), 15)
        with self.assertRaises(ValueError): journal.reserve()
        with self.assertRaises(ValueError): journal.stop("failed")

    def test_airline_multi_assertion_receipt_releases_only_known_usage(self):
        for count in (1, 2, 3):
            for unknown in (False, True):
                with self.subTest(count=count, unknown=unknown), tempfile.TemporaryDirectory() as directory:
                    value = plan(domains=["airline"], assertions=count)[0]
                    journal = aggregate.Journal(Path(directory).resolve() / "run", value, CONTRACT, TOOLKIT)
                    try:
                        record = self.claim(journal)
                        self.assertEqual(record["trial"]["nl_assertion_count"], count)
                        if unknown:
                            record["endpoint_usage"]["evaluator"].update(unknown_responses=1)
                            with self.assertRaises(ValueError): journal.settle(record, tau.encoded(record))
                            self.assertEqual(journal.stop("failed", stage="settlement")["held_trial_usd"], "70.044864")
                        else:
                            journal.settle(record, tau.encoded(record))
                            self.assertEqual(journal.spent, Decimal("4.19044935"))
                            self.assertTrue(journal.reserve())
                    finally:
                        journal.close()

    def test_exclusive_destination_and_original_receipts_never_overwritten(self):
        journal = self.journal()
        before = (self.path / "plan.json").read_bytes()
        with self.assertRaises(FileExistsError):
            aggregate.Journal(self.path, self.value, CONTRACT, TOOLKIT)
        self.assertEqual((self.path / "plan.json").read_bytes(), before)
        record = self.claim(journal)
        original = b"retained original failed receipt\n"
        (self.path / "receipt-00.json").write_bytes(original)
        with self.assertRaises(FileExistsError):
            journal.settle(record, tau.encoded(record))
        self.assertEqual((self.path / "receipt-00.json").read_bytes(), original)
        self.assertEqual(journal.stop("failed", stage="settlement")["held_trial_usd"], "70.044864")

    def test_unknown_cancelled_failed_cleanup_and_partial_receipts_hold_full(self):
        for change in (lambda r: r.update(result="fail"), lambda r: r.update(cancelled=True),
                       lambda r: r.update(cleanup="failed"), lambda r: r.update(cleanup="create-completion-unknown"),
                       lambda r: r["trial"]["trial"].update(status="cancelled"),
                       lambda r: r["endpoint_usage"]["simulator"].update(unknown_responses=1)):
            with tempfile.TemporaryDirectory() as directory:
                journal = aggregate.Journal(Path(directory).resolve() / "run", self.value, CONTRACT, TOOLKIT)
                try:
                    record = self.claim(journal)
                    change(record)
                    with self.assertRaises(ValueError): journal.settle(record, tau.encoded(record))
                    with self.assertRaises(ValueError): journal.reserve()
                    result = journal.stop("failed", stage="settlement")
                    self.assertEqual(result["held_trial_usd"], "70.044864")
                    self.assertEqual(result["charged_reservation_usd"], "0")
                finally:
                    journal.close()

    def test_invalid_binding_count_usage_parity_and_ledger_cannot_release(self):
        changes = (lambda r: r.update(toolkit={}), lambda r: r.update(plan_sha256="0" * 64),
            lambda r: r.update(endpoint_binding_sha256="0" * 64), lambda r: r.update(live_admission=True),
            lambda r: r["stage_binding"].update(index=1), lambda r: r["selection"].update(seed=1),
            lambda r: r["endpoint_usage"]["simulator"].update(support_source_sha256="0" * 64),
            lambda r: r["endpoint_usage"]["simulator"].update(count_attempts=True),
            lambda r: r["endpoint_usage"]["simulator"].update(count_cost_reserved_usd="0"),
            lambda r: r["endpoint_usage"]["simulator"].update(unknown_responses=False),
            lambda r: r["endpoint_usage"]["simulator"]["known_usage"].update(prompt_tokens=True),
            lambda r: r["endpoint_usage"]["simulator"]["count_usage_pairs"][0].update(counted_tokens=True),
            lambda r: r["endpoint_usage"]["simulator"]["generation_admission"].update(held_usd="2.111536"),
            lambda r: r["endpoint_usage"]["simulator"]["generation_admission"].update(settled_usd="0"),
            lambda r: r["trial"]["trial"]["usage"].update(cost_usd="0"))
        for change in changes:
            record, source_sha = receipt(self.value)
            change(record)
            with self.assertRaises(ValueError):
                aggregate.charge(record, plan=self.value, index=0, toolkit=TOOLKIT,
                                 payload_sha=PAYLOAD, contract_sha=CONTRACT, source_sha=source_sha)

    def test_zero_assertion_source_never_invents_judge_or_count_usage(self):
        value = plan(assertions=0)[0]
        journal = self.journal(value)
        record = self.claim(journal)
        self.assertEqual(record["endpoint_usage"]["evaluator"]["generation_attempts"], 0)
        journal.settle(record, tau.encoded(record))
        self.assertEqual(journal.spent, Decimal("2.09523335"))
        event = tau.json_value((self.path / "event-003.json").read_bytes())
        self.assertEqual(event["data"]["count_attempts"], 1)

    def test_fsync_failure_holds_and_never_dispatches_or_retries(self):
        journal = self.journal()
        with mock.patch.object(aggregate.os, "fsync", side_effect=OSError("PRIVATE_CANARY")):
            with self.assertRaises(OSError): journal.reserve()
        self.assertIsNotNone(journal.pending)
        self.assertIs(journal.failed, True)
        with self.assertRaises(ValueError): journal.reserve()
        retained = (self.path / "event-001.json").read_bytes()
        result = journal.stop("failed", stage="reservation")
        self.assertEqual(result["held_trial_usd"], "70.044864")
        self.assertIs(result["journal_complete"], False)
        self.assertEqual((self.path / "event-001.json").read_bytes(), retained)
        self.assertEqual(tau.json_value((self.path / "result.json").read_bytes()), result)
        self.assertNotIn("CANARY", tau.encoded(result).decode())

    def test_settlement_event_fsync_failure_retains_receipt_and_full_hold(self):
        for failure_call in (3, 4):
            with self.subTest(failure_call=failure_call), tempfile.TemporaryDirectory() as directory:
                path = Path(directory).resolve() / "journal"
                journal = aggregate.Journal(path, self.value, CONTRACT, TOOLKIT)
                self.addCleanup(journal.close)
                record = self.claim(journal)
                raw = tau.encoded(record)
                original_fsync, calls = aggregate.os.fsync, []

                def fail_settlement_event(fd):
                    calls.append(fd)
                    # The receipt's file and directory syncs must succeed first.
                    if len(calls) == failure_call:
                        raise OSError("PRIVATE_CANARY")
                    return original_fsync(fd)

                with mock.patch.object(aggregate.os, "fsync", side_effect=fail_settlement_event):
                    with self.assertRaises(OSError):
                        journal.settle(record, raw)
                retained = (path / "event-003.json").read_bytes()
                self.assertEqual(tau.json_value(retained)["kind"], "settled")
                self.assertEqual((path / "receipt-00.json").read_bytes(), raw)
                self.assertEqual(journal.index, 0)
                self.assertEqual(journal.spent, Decimal(0))
                self.assertIsNotNone(journal.pending)
                with self.assertRaises(ValueError):
                    journal.reserve()
                result = journal.stop("failed", stage="settlement")
                self.assertEqual(result["held_trial_usd"], "70.044864")
                self.assertEqual(result["charged_reservation_usd"], "0")
                self.assertIs(result["journal_complete"], False)
                self.assertEqual(result["result"], "incomplete")
                self.assertEqual((path / "event-003.json").read_bytes(), retained)
                self.assertEqual((path / "receipt-00.json").read_bytes(), raw)
                self.assertEqual(tau.json_value((path / "result.json").read_bytes()), result)
                self.assertNotIn("CANARY", tau.encoded(result).decode())

    def test_terminal_event_fsync_failure_never_publishes_success_or_reopens(self):
        for failure_call in (1, 2):
            with self.subTest(failure_call=failure_call), tempfile.TemporaryDirectory() as directory:
                path = Path(directory).resolve() / "journal"
                journal = aggregate.Journal(path, self.value, CONTRACT, TOOLKIT)
                self.addCleanup(journal.close)
                for _ in self.value["selections"]:
                    record = self.claim(journal)
                    journal.settle(record, tau.encoded(record))
                event_path = path / f"event-{journal.events:03d}.json"
                original_fsync, calls = aggregate.os.fsync, []

                def fail_terminal_event(fd):
                    calls.append(fd)
                    if len(calls) == failure_call:
                        raise OSError("PRIVATE_CANARY")
                    return original_fsync(fd)

                with mock.patch.object(aggregate.os, "fsync", side_effect=fail_terminal_event):
                    with self.assertRaises(OSError):
                        journal.stop("complete")
                retained = event_path.read_bytes()
                result = tau.json_value((path / "result.json").read_bytes())
                self.assertEqual(result["result"], "incomplete")
                self.assertEqual(result["stop_reason"], "failed")
                self.assertIs(result["journal_complete"], False)
                self.assertEqual(result["completed_trials"], len(self.value["selections"]))
                self.assertEqual(result["held_trial_usd"], "0")
                self.assertEqual(Decimal(result["charged_reservation_usd"]), journal.spent)
                with self.assertRaises(ValueError):
                    journal.reserve()
                with self.assertRaises(ValueError):
                    journal.stop("failed")
                self.assertEqual(event_path.read_bytes(), retained)
                self.assertEqual(tau.json_value((path / "result.json").read_bytes()), result)
                self.assertNotIn("CANARY", tau.encoded(result).decode())

    def test_wrong_claim_or_duplicate_dispatch_is_rejected(self):
        journal = self.journal()
        record, sha = receipt(self.value)
        stage = {"plan": self.value, "index": 0}
        with self.assertRaises(ValueError): journal.claim(stage, PAYLOAD, CONTRACT, TOOLKIT, sha)
        journal.reserve()
        with self.assertRaises(ValueError): journal.claim({**stage, "index": 1}, PAYLOAD, CONTRACT, TOOLKIT, sha)
        with self.assertRaises(ValueError): journal.claim(stage, PAYLOAD, "0" * 64, TOOLKIT, sha)
        journal.claim(stage, PAYLOAD, CONTRACT, TOOLKIT, sha)
        with self.assertRaises(ValueError): journal.claim(stage, PAYLOAD, CONTRACT, TOOLKIT, sha)
        with self.assertRaises(ValueError): journal.stop("complete")
        with self.assertRaises(ValueError): journal.settle(record, tau.encoded(record) + b" ")
        self.assertEqual(journal.stop("failed", stage="settlement")["held_trial_usd"], "70.044864")

    def test_no_symlink_or_permission_broadening(self):
        journal = self.journal()
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(self.path / "plan.json").st_mode & 0o777, 0o600)
        target = self.path.parent / "original"
        target.write_bytes(b"original")
        (self.path / "linked").symlink_to(target)
        with self.assertRaises(FileExistsError): journal.write("linked", b"replacement")
        self.assertEqual(target.read_bytes(), b"original")


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.entry = selections.runner.load("scripts/record-tau-sequential.py")
        self.trial = self.entry.trial
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.value = plan()[0]
        self.plan_path = self.root / "selection.json"
        self.plan_path.write_bytes(tau.encoded(self.value))
        self.args = types.SimpleNamespace(**{key: self.root / "unused" for key in
            ("source_archive", "wheelhouse", "docker_socket", "contract", "candidate_key", "simulator_key",
             "vllm_source", "tokenizer", "tokenizer_wheel")}, selection_plan=self.plan_path,
            selection_plan_sha256=tau.digest(tau.encoded(self.value)), contract_sha256=CONTRACT,
            output=self.root / "run", deadline_utc=self.entry.datetime.datetime.fromtimestamp(
                time.time() + 4000, self.entry.datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.progress = {"stage": "input-verification", "cancelled": False}

    def owner(self):
        owner = object.__new__(self.trial.AggregateOwner)
        owner.driver, owner.archive = aggregate.external, self.args.source_archive
        owner.deadline = time.monotonic() + 4000
        owner.journal = aggregate.Journal(self.args.output, self.value, CONTRACT, TOOLKIT)
        self.addCleanup(owner.journal.close)
        return owner

    def execution(self, *, change=None, preparation=None, after=None):
        owner = self.owner()
        deadlines = []
        def prepare(*args, **kwargs):
            self.assertIsNotNone(owner.journal.pending)
            self.assertEqual(owner.journal.pending["claimed"], False)
            self.assertTrue((self.args.output / f"event-{owner.journal.events - 1:03d}.json").is_file())
            deadlines.append(args[5])
            if preparation:
                raise preparation
            return {"trial.json": b"synthetic payload\n"}, {}
        def run(payload, sha, destination, socket, *, aggregate_owner, external_inputs, parent_pipe):
            self.assertIs(aggregate_owner, owner)
            self.assertTrue(parent_pipe)
            deadlines.append(external_inputs["deadline"])
            stage = {"plan": self.value, "index": owner.journal.index}
            record, source_sha = receipt(self.value, owner.journal.index)
            record["plan_sha256"] = sha
            with mock.patch.object(owner.driver.source_support(), "Source",
                                   return_value=types.SimpleNamespace(sha256=source_sha)):
                owner.claim({"stage": stage}, sha, TOOLKIT, external_inputs)
            if isinstance(change, BaseException):
                raise change
            if change:
                change(record)
            self.trial.publish_evidence(destination, {"run.json": b"{}\n", "output.log": tau.encoded(record)})
            if after:
                after()
            return record
        with mock.patch.object(self.trial, "AggregateOwner", return_value=owner) as construct, \
                mock.patch.object(self.trial, "prepare", side_effect=prepare) as prep, \
                mock.patch.object(self.trial, "run", side_effect=run) as run_mock:
            # The real owner normally creates the exclusive directory inside execute.
            original_exists = Path.exists
            with mock.patch.object(Path, "exists", lambda p: False if p == self.args.output else original_exists(p)):
                result = self.entry.execute(self.args, self.progress)
        self.assertTrue(all(value < construct.call_args.args[-1] for value in deadlines))
        self.assertIsNone(owner.journal.fd)
        return result, prep, run_mock

    def test_serial_controller_preserves_rows_and_settles_only_owned_originals(self):
        result, prep, run = self.execution()
        self.assertEqual(result["result"], "pass")
        self.assertEqual(result["completed_trials"], 5)
        self.assertEqual(prep.call_count, 5)
        self.assertEqual(run.call_count, 5)
        for index in range(5):
            self.assertEqual((self.args.output / f"receipt-{index:02d}.json").read_bytes(),
                             (self.args.output / f"trial-{index:02d}" / "output.log").read_bytes())
        self.assertIs(result["live_admission"], False)

    def test_returned_cancelled_diagnostic_retained_and_full_hold_not_released(self):
        result, _, run = self.execution(change=lambda r: r["trial"]["trial"].update(status="cancelled"))
        self.assertEqual(run.call_count, 1)
        self.assertEqual(result["stop_reason"], "cancelled")
        self.assertIs(result["cancelled"], True)
        self.assertEqual(result["held_trial_usd"], "70.044864")
        self.assertEqual(result["completed_trials"], 0)
        self.assertEqual(tau.json_value((self.args.output / "trial-00/output.log").read_bytes())[
            "trial"]["trial"]["status"], "cancelled")
        self.assertFalse((self.args.output / "receipt-00.json").exists())

    def test_keyboard_interrupt_never_releases_unknown_billing(self):
        result, _, run = self.execution(change=KeyboardInterrupt())
        self.assertEqual(run.call_count, 1)
        self.assertIs(result["cancelled"], True)
        self.assertEqual(result["held_trial_usd"], "70.044864")
        self.assertEqual(result["stage"], "trial")

    def test_preparation_failure_retains_reservation_and_never_dispatches(self):
        result, prep, run = self.execution(preparation=ValueError("PRIVATE_CANARY"))
        self.assertEqual(prep.call_count, 1)
        run.assert_not_called()
        self.assertEqual(result["stage"], "preparation")
        self.assertEqual(result["stop_reason"], "failed")
        self.assertEqual(result["held_trial_usd"], "70.044864")
        self.assertNotIn(b"CANARY", tau.encoded(result))

    def test_failed_cleanup_retains_original_without_next_dispatch(self):
        result, _, run = self.execution(change=lambda r: r.update(cleanup="failed"))
        self.assertEqual(run.call_count, 1)
        self.assertEqual(result["stage"], "settlement")
        self.assertEqual(result["held_trial_usd"], "70.044864")
        self.assertEqual(tau.json_value((self.args.output / "trial-00/output.log").read_bytes())["cleanup"], "failed")

    def test_deadline_failure_is_not_renewed_or_retried(self):
        result, _, run = self.execution(change=self.trial.worker.Abort("timeout"))
        self.assertEqual(run.call_count, 1)
        self.assertEqual(result["stop_reason"], "deadline")
        self.assertEqual(result["held_trial_usd"], "70.044864")

    def test_late_cancellation_does_not_release_successful_looking_receipt(self):
        result, _, run = self.execution(after=lambda: self.progress.update(cancelled=True))
        self.assertEqual(run.call_count, 1)
        self.assertIs(result["cancelled"], True)
        self.assertEqual(result["held_trial_usd"], "70.044864")

    def test_owner_authenticates_source_before_creating_journal(self):
        with mock.patch.object(self.trial, "identity", return_value=TOOLKIT), \
                mock.patch.object(self.trial, "parent_driver", return_value=aggregate.external), \
                mock.patch.object(aggregate.external.source_support(), "Source", side_effect=ValueError) as source, \
                mock.patch.object(aggregate, "Journal") as journal, self.assertRaises(ValueError):
            self.trial.AggregateOwner(self.args.output, self.value, self.args.source_archive, CONTRACT,
                                      time.monotonic() + 4000)
        source.assert_called_once()
        journal.assert_not_called()
        self.assertFalse(self.args.output.exists())

    def test_sequential_plan_cannot_use_single_trial_entry_without_owned_journal(self):
        for value in (self.value, plan(domains=["airline"])[0]):
            stage = {"plan": value, "index": 0}
            manifest = selections.configuration(stage)
            with self.subTest(schema=value["schema"]), \
                    mock.patch.object(self.trial, "identity", return_value=TOOLKIT), \
                    mock.patch.object(self.trial, "validate_plan", return_value=({}, manifest)), \
                    mock.patch.object(self.trial, "parent_driver", return_value=aggregate.external), \
                    mock.patch.object(aggregate.external, "Runtime") as runtime, \
                    mock.patch.object(self.trial.probe, "capture") as docker, self.assertRaises(ValueError):
                self.trial.run(None, PAYLOAD, None, None, parent_pipe=True, external_inputs={"full_task": True,
                    "deadline": time.monotonic() + 900, "arguments": (None,) * 7,
                    "stage_binding": selection.binding(stage)})
            runtime.assert_not_called()
            docker.assert_not_called()

    def test_inner_trial_early_cancellation_survives_owned_cleanup(self):
        stage = {"plan": self.value, "index": 0}
        owner = object.__new__(self.trial.AggregateOwner)
        owner.claim = mock.Mock()
        def capture(argv, **kwargs):
            command = argv[5:]
            if command[:2] == ["image", "inspect"]:
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
                self.fail("cancellation must interrupt image inspection")
            self.assertEqual(command[0], "ps")
            return b""
        sock = mock.Mock()
        sock.is_absolute.return_value = True
        sock.stat.return_value = types.SimpleNamespace(st_mode=stat.S_IFSOCK)
        with mock.patch.object(self.trial, "identity", return_value=TOOLKIT), \
                mock.patch.object(self.trial, "validate_plan", return_value=(
                    {"platform": "linux/amd64", "image": self.trial.runtime.TARGET["image"]}, selections.configuration(stage))), \
                mock.patch.object(self.trial, "parent_driver", return_value=aggregate.external), \
                mock.patch.object(self.trial.shutil, "which", return_value="/unused/docker"), \
                mock.patch.object(self.trial.probe, "capture", side_effect=capture), \
                mock.patch.object(aggregate.external, "Runtime") as runtime:
            record = self.trial.run(self.root / "unused", PAYLOAD, self.args.output, sock, parent_pipe=True,
                aggregate_owner=owner, external_inputs={"full_task": True, "deadline": time.monotonic() + 900,
                    "arguments": (None,) * 7, "stage_binding": selection.binding(stage)})
        owner.claim.assert_called_once()
        runtime.assert_not_called()
        self.assertIs(record["cancelled"], True)
        self.assertEqual(record["cleanup"], "confirmed")
        self.assertEqual(record["result"], "fail")
        self.assertIsNone(record["trial"])

    def test_invalid_global_deadline_plan_hash_and_existing_output_precede_owner(self):
        for changes in ({"deadline_utc": "2000-01-01T00:00:00Z"}, {"deadline_utc": "2100-01-01T00:00:00Z"},
                        {"selection_plan_sha256": "f" * 64}, {"source_archive": Path("relative")}):
            args = copy.copy(self.args)
            vars(args).update(changes)
            with mock.patch.object(self.trial, "AggregateOwner") as owner, self.assertRaises(ValueError):
                self.entry.execute(args, self.progress)
            owner.assert_not_called()
        self.args.output.mkdir()
        original = self.args.output / "original-failure.json"
        original.write_bytes(b"retained\n")
        with mock.patch.object(self.trial, "AggregateOwner") as owner, self.assertRaises(ValueError):
            self.entry.execute(self.args, self.progress)
        owner.assert_not_called()
        self.assertEqual(original.read_bytes(), b"retained\n")

    def test_main_source_failure_has_content_free_receipt_without_overwrite(self):
        arguments = ["record-tau-sequential.py"]
        for key, value in vars(self.args).items():
            arguments.extend(["--" + key.replace("_", "-"), str(value)])
        with mock.patch.object(sys, "argv", arguments), \
                mock.patch.object(self.trial, "AggregateOwner", side_effect=ValueError("PRIVATE_CANARY")), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(self.entry.main(), 1)
        raw = (self.args.output / "output.log").read_bytes()
        result = tau.json_value(raw)
        self.assertEqual(result["stage"], "source-verification")
        self.assertIs(result["source_authenticated"], False)
        self.assertNotIn("CANARY", errors.getvalue() + raw.decode())
        with mock.patch.object(sys, "argv", arguments), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(self.entry.main(), 1)
        self.assertEqual((self.args.output / "output.log").read_bytes(), raw)


class OfflineRecorderTests(unittest.TestCase):
    def test_failures_are_content_free_and_original_receipts_exclusive(self):
        recorder = selections.runner.load("scripts/record-tau-sequential-offline.py")
        for failure in (ValueError("PRIVATE_CANARY"), KeyboardInterrupt()):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory).resolve() / "receipt.json"
                arguments = ["--source-archive", "/unused", "--wheelhouse", "/unused", "--docker-socket", "/unused",
                             "--output", "/unused", "--receipt", str(path)]
                with mock.patch.object(recorder, "replay", side_effect=failure), redirect_stdout(io.StringIO()):
                    self.assertEqual(recorder.main(arguments), 1)
                raw = path.read_bytes()
                value = tau.json_value(raw)
                self.assertFalse(value["completed"] or value["source_authenticated"] or value["live_admission"])
                self.assertEqual(value["cancelled"], isinstance(failure, KeyboardInterrupt))
                self.assertNotIn(b"CANARY", raw)
                with mock.patch.object(recorder, "replay", side_effect=failure), redirect_stdout(io.StringIO()):
                    self.assertEqual(recorder.main(arguments), 1)
                self.assertEqual(path.read_bytes(), raw)

    def test_packaging_preserves_original_producer_and_receipt_bytes(self):
        recorder = selections.runner.load("scripts/record-tau-sequential-offline.py")
        value = {"schema": "nib.tau-sequential-offline/v1", "result": "fail", "producer_git_sha": "a" * 40,
                 "command_line": "fixed offline command", "started_utc": "2026-10-27T00:00:00Z",
                 "ended_utc": "2026-10-27T00:01:00Z"}
        raw = tau.encoded(value)
        wrapped = recorder.evidence_record(value)
        self.assertEqual(tau.encoded(value), raw)
        self.assertEqual(wrapped["git_sha"], "a" * 40)
        self.assertEqual(wrapped["result"], "fail")
        self.assertIs(wrapped["hardware_validated"], False)
        with self.assertRaises(ValueError): recorder.evidence_record({**value, "schema": "unknown"})


if __name__ == "__main__":
    unittest.main()
