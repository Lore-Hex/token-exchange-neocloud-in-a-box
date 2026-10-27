# SPDX-License-Identifier: Apache-2.0
"""Offline, content-free staged selection; never a spending authorization."""

from decimal import Decimal
import hashlib
import random
import re

import tau_preflight as tau

SCHEMA = "nib.tau-external-selection-plan/v2"
SEQUENTIAL_SCHEMA = "nib.tau-external-sequential-plan/v1"
AIRLINE_SEQUENTIAL_SCHEMA = "nib.tau-external-sequential-plan/v2"
INPUT_SCHEMA = "nib.tau-external-staged-input/v2"
TRANSPORT_SCHEMA = "nib.tau-external-staged-transport/v2"
MAX_ASSERTIONS = 3  # Maximum in the pinned first-five-per-domain smoke selection.
COUNT_RESERVATION = Decimal("2.095152")
TRIAL_RESERVATION = COUNT_RESERVATION * 32 + Decimal("3.00")
BUDGET_KEYS = ("weekly_cap_usd", "gpu_committed_usd", "cpu_disk_reserved_usd", "other_committed_usd")


def is_sequential(plan):
    return type(plan) is dict and plan.get("schema") in (SEQUENTIAL_SCHEMA, AIRLINE_SEQUENTIAL_SCHEMA)


def source_names(domains):
    tau.require(type(domains) is list and 1 <= len(domains) <= 3
                and all(type(domain) is str and domain in tau.DOMAINS for domain in domains)
                and domains == [domain for domain in tau.DOMAINS if domain in domains])
    return ["data/tau2/domains/" + domain + "/" + name for domain in domains
            for name in ("tasks.json", "split_tasks.json")]


def budget(inputs, count):
    tau.require(type(inputs) is dict and set(inputs) == set(BUDGET_KEYS))
    values = {name: tau.amount(inputs[name]) for name in BUDGET_KEYS}
    tau.require(0 < values["weekly_cap_usd"] <= 10000
                and all(value <= 10000 for value in values.values()))
    reserved = TRIAL_RESERVATION * count
    total = sum(values[name] for name in BUDGET_KEYS[1:]) + reserved
    return {"inputs": dict(inputs), "basis": "operator-declared-reservations-not-invoice",
            "count_attempts_per_trial": 32, "count_usd_per_attempt": str(COUNT_RESERVATION),
            "generation_usd_per_trial": "3.00", "total_usd_per_trial": str(TRIAL_RESERVATION),
            "planned_trials": count, "trials_reserved_usd": str(reserved),
            "total_reserved_usd": format(total, "f"),
            "remaining_usd": format(values["weekly_cap_usd"] - total, "f"),
            "within_cap": total <= values["weekly_cap_usd"]}


def assemble(producer, domains, trials, seed, tasks, source_files, budget_inputs, *, aggregate_cap_usd=None):
    tau.require(type(producer) is str and re.fullmatch("[0-9a-f]{40}", producer)
                and type(domains) is list and 1 <= len(domains) <= 3
                and all(type(domain) is str and domain in tau.DOMAINS for domain in domains)
                and domains == [domain for domain in tau.DOMAINS if domain in domains])
    tau.integer(trials, 1, 4)
    tau.integer(seed, 0, 1000000)
    tau.require(type(tasks) is dict and set(tasks) == set(domains)
                and type(source_files) is dict and set(source_files) == set(source_names(domains)))
    for row in source_files.values():
        tau.require(type(row) is dict and set(row) == {"sha256", "git_blob", "bytes"})
        tau.sha(row["sha256"])
        tau.require(type(row["git_blob"]) is str and re.fullmatch("[0-9a-f]{40}", row["git_blob"]))
        tau.integer(row["bytes"], 1, 16 << 20)
    rng = random.Random(seed)
    seeds = [rng.randint(0, 1000000) for _ in range(trials)]
    selections = []
    for domain in domains:
        row = tasks[domain]
        tau.require(type(row) is dict and set(row) == {"base_count", "task_ids", "assertion_counts"})
        tau.integer(row["base_count"], 5, 20000)
        ids, counts = row["task_ids"], row["assertion_counts"]
        tau.require(type(ids) is list and len(ids) == 5
                    and all(type(item) is str and re.fullmatch(r"[A-Za-z0-9_:\[\]|-]{1,256}", item) for item in ids)
                    and ids == sorted(set(ids)) and type(counts) is list and len(counts) == 5)
        for task_id, count in zip(ids, counts):
            tau.require(count is None or type(count) is int and 0 <= count <= 20000)
            for trial, trial_seed in enumerate(seeds):
                selections.append({"selection": {"domain": domain, "task_id": task_id, "trial": trial,
                                                  "seed": trial_seed, "role": "candidate"},
                                   "compatibility": "supported-shape" if count is not None and count <= MAX_ASSERTIONS
                                   else "unsupported-assertion-shape"})
    financial = budget(budget_inputs, len(selections))
    if aggregate_cap_usd is not None:
        cap = tau.amount(aggregate_cap_usd)
        tau.require(TRIAL_RESERVATION <= cap <= 10000)
        committed = sum(tau.amount(budget_inputs[name]) for name in BUDGET_KEYS[1:])
        financial.update(policy="sequential-full-trial-reservations", planned_worst_case_usd=financial["trials_reserved_usd"],
                         trials_reserved_usd=aggregate_cap_usd, total_reserved_usd=format(committed + cap, "f"),
                         remaining_usd=format(tau.amount(budget_inputs["weekly_cap_usd"]) - committed - cap, "f"),
                         within_cap=committed + cap <= tau.amount(budget_inputs["weekly_cap_usd"]))
    unsupported = sum(row["compatibility"] != "supported-shape" for row in selections)
    schema = AIRLINE_SEQUENTIAL_SCHEMA if "airline" in domains else SEQUENTIAL_SCHEMA
    return {"schema": SCHEMA if aggregate_cap_usd is None else schema,
            **({"aggregate_cap_usd": aggregate_cap_usd} if aggregate_cap_usd is not None else {}),
            "producer_git_sha": producer, "upstream_revision": tau.REVISION,
            "domains": domains, "trials_per_task": trials, "seed": seed, "trial_seeds": seeds,
            "split": "base", "task_order": "lexicographic-task-id-before-selection",
            "domain_tasks": tasks, "source_files": source_files, "selections": selections,
            "budget": financial, "unsupported_trials": unsupported, "status": "preflight-only",
            "result": "pass" if financial["within_cap"] and unsupported == 0 else "fail",
            "execution_authorized": False, "benchmark_executed": False, "quality_eligible": False,
            "full_benchmark": False, "confidential": False, "live_admission": False}


def build(sources, pins, producer, domains, trials, seed, budget_inputs, *, aggregate_cap_usd=None):
    tau.require(pins["revision"] == tau.REVISION and type(domains) is list
                and all(type(domain) is str and domain in tau.DOMAINS for domain in domains))
    selected_files = {}
    for name in source_names(domains):
        raw, row = sources[name], pins["files"][name]
        tau.require(len(raw) == row["bytes"] and tau.digest(raw) == row["sha256"]
                    and hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == row["git_blob"])
        selected_files[name] = dict(row)
    tasks = {}
    for domain in domains:
        name, splits = source_names([domain])
        ordered = tau.task_ids(sources[name], sources[splits])
        raw_tasks = tau.json_value(sources[name])
        rows = {row["id"]: row for row in (raw_tasks["tasks"] if type(raw_tasks) is dict else raw_tasks)}
        counts = []
        for task_id in ordered[:5]:
            criteria = rows[task_id].get("evaluation_criteria")
            assertions = criteria.get("nl_assertions") if type(criteria) is dict else None
            counts.append(None if criteria is not None and type(criteria) is not dict else
                          0 if assertions is None else len(assertions)
                          if type(assertions) is list and len(assertions) <= 20000
                          and all(type(value) is str and 0 < len(value.encode()) <= 16384
                                  for value in assertions) else None)
        tasks[domain] = {"base_count": len(ordered), "task_ids": ordered[:5], "assertion_counts": counts}
    return assemble(producer, domains, trials, seed, tasks, selected_files, budget_inputs,
                    aggregate_cap_usd=aggregate_cap_usd)


def validate(plan):
    tau.require(type(plan) is dict and (plan.get("schema") == SCHEMA or is_sequential(plan)))
    expected = assemble(plan["producer_git_sha"], plan["domains"], plan["trials_per_task"], plan["seed"],
                        plan["domain_tasks"], plan["source_files"], plan["budget"]["inputs"],
                        aggregate_cap_usd=plan["aggregate_cap_usd"] if is_sequential(plan) else None)
    tau.require(tau.encoded(plan) == tau.encoded(expected))
    return plan


def verify_sources(plan, sources, pins):
    validate(plan)
    rebuilt = build(sources, pins, plan["producer_git_sha"], plan["domains"], plan["trials_per_task"],
                    plan["seed"], plan["budget"]["inputs"],
                    aggregate_cap_usd=plan["aggregate_cap_usd"] if is_sequential(plan) else None)
    tau.require(tau.encoded(plan) == tau.encoded(rebuilt))


def selected(stage):
    tau.require(type(stage) is dict and set(stage) == {"plan", "index"})
    plan = validate(stage["plan"])
    tau.integer(stage["index"], 0, len(plan["selections"]) - 1)
    row = plan["selections"][stage["index"]]
    tau.require(plan["budget"]["within_cap"] and row["compatibility"] == "supported-shape")
    return dict(row["selection"])


def binding(stage):
    selected(stage)
    return {"sha256": tau.digest(tau.encoded(stage["plan"])), "index": stage["index"]}


def assertion_count(stage):
    row = selected(stage)
    tasks = stage["plan"]["domain_tasks"][row["domain"]]
    return tasks["assertion_counts"][tasks["task_ids"].index(row["task_id"])]


def validate_assertions(stage, assertions):
    tau.require(type(assertions) is list and len(assertions) == assertion_count(stage)
                and all(type(value) is str and 0 < len(value.encode()) <= 16384 for value in assertions)
                and len(tau.encoded(assertions)) <= 65536)
