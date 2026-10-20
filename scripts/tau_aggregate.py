#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Offline, content-free tau arithmetic. Never evaluates tasks or grants quality acceptance."""

import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    sys.exit("Use python3 -I -S -B scripts/tau_aggregate.py")

import argparse
from decimal import Decimal, localcontext
from fractions import Fraction
import hashlib
import math
import os
from pathlib import Path
import random
import re
import stat
import time
import types

ROOT = Path(__file__).absolute().parent.parent
PREFLIGHT_SHA = "37a6c53c31f78f952ca8abf47ee96f166feaaf1a83cf8171c76caa72637caeec"
METRICS_SHA = "136b5ebcc0c115b5817a4f6d6b485a871aba199ddedf56458f35f339569a54dc"
BASE_COUNTS = {"airline": 50, "retail": 114, "telecom": 114}
STATUSES = ("evaluated", "infrastructure_error", "timeout", "truncated", "protocol_error",
            "budget_abort", "cancelled")
RESAMPLES = 10000
MAX_SECONDS = 60


def require(value):
    if not value:
        raise ValueError("tau aggregation rejected")


def load_preflight():
    """Read pinned helper bytes without pyc, hooks, symlinks or blocking specials."""
    path = ROOT / "scripts/tau_preflight.py"
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        source = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(source, "rb") as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 < before.st_size <= 65536)
            raw = stream.read(65537)
            def identity(info):
                return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
                        info.st_mtime_ns, info.st_ctime_ns)
            require(len(raw) == before.st_size and hashlib.sha256(raw).hexdigest() == PREFLIGHT_SHA
                    and identity(before) == identity(os.fstat(stream.fileno()))
                    and identity(before) == identity(os.stat(path.name, dir_fd=fd, follow_symlinks=False)))
    finally:
        os.close(fd)
    module = types.ModuleType("tau_preflight")
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec", flags=0, dont_inherit=True), module.__dict__)
    return module


tau = load_preflight()


def validate_manifest(value, expected_sha, pins):
    tau.sha(expected_sha)
    require(tau.digest(tau.encoded(value)) == expected_sha)
    require(type(value) is dict and set(value) == {
        "schema", "status", "benchmark_executed", "scored_run", "quality_accepted", "confidential",
        "toolkit", "upstream", "dependency_lock_sha256", "dependencies_installed_or_verified",
        "inputs_verified_bytes", "domains", "split", "trial_seeds", "trials_per_task", "task_order",
        "role_order", "expected_trials_per_role", "settings", "settings_provenance", "upstream_contract",
        "retention", "blocked_on"})
    require(value["schema"] == "nib.tau-manifest/v1" and value["status"] == "preflight-only"
            and value["split"] == "base" and value["role_order"] == ["candidate", "reference"]
            and value["task_order"] == "lexicographic-task-id-before-selection"
            and value["settings_provenance"] == "operator-declared-not-independently-verified")
    require(all(value[key] is False for key in ("benchmark_executed", "scored_run", "quality_accepted",
                                                "confidential", "dependencies_installed_or_verified")))
    require(value["upstream"] == pins and pins["revision"] == tau.REVISION
            and value["dependency_lock_sha256"] == pins["files"]["uv.lock"]["sha256"]
            and value["inputs_verified_bytes"] == sum(row["bytes"] for row in pins["files"].values())
            and pins["files"]["src/tau2/metrics/agent_metrics.py"]["sha256"] == METRICS_SHA)
    config = tau.settings(value["settings"])
    trials = 1 if config["stage"] == "smoke" else 4
    rng = random.Random(config["seed"])
    require(type(value["trials_per_task"]) is int and value["trials_per_task"] == trials
            and type(value["trial_seeds"]) is list and all(type(seed) is int for seed in value["trial_seeds"])
            and value["trial_seeds"] == [rng.randint(0, 1000000) for _ in range(trials)])
    require(type(value["domains"]) is dict and set(value["domains"]) == set(tau.DOMAINS))
    for domain, row in value["domains"].items():
        require(type(row) is dict and set(row) == {"base_count", "task_ids"}
                and type(row["base_count"]) is int and row["base_count"] == BASE_COUNTS[domain])
        ids = row["task_ids"]
        require(type(ids) is list and len(ids) == (5 if trials == 1 else BASE_COUNTS[domain])
                and all(type(item) is str and re.fullmatch(r"[A-Za-z0-9_:\[\]|-]{1,256}", item) for item in ids)
                and ids == sorted(set(ids)))
    require(type(value["expected_trials_per_role"]) is int
            and value["expected_trials_per_role"] == sum(len(row["task_ids"]) for row in value["domains"].values()) * trials)
    toolkit = value["toolkit"]
    names = ("scripts/tau_preflight.py", tau.PINS, tau.PACK + "/pack.yaml", tau.PACK + "/files.sha256")
    require(type(toolkit) is dict and set(toolkit) == {"git_sha", "files"}
            and type(toolkit["git_sha"]) is str and re.fullmatch("[0-9a-f]{40}", toolkit["git_sha"])
            and toolkit["git_sha"] != "0" * 40
            and type(toolkit["files"]) is dict and set(toolkit["files"]) == set(names))
    for sha in toolkit["files"].values():
        tau.sha(sha)
    require(value["retention"] == {"raw_trajectories": False, "raw_logs": False, "allowed": list(tau.RETAINED)}
            and value["blocked_on"] == ["live-adapter-with-enforced-retention-and-transport-budgets",
                                        "qualified-candidate-and-same-revision-reference",
                                        "simulator-evaluator-and-resource-cost-approval"]
            and value["upstream_contract"] == {
                "entrypoint": "tau2.runner.batch.run_single_task", "config": "tau2.data_model.simulation.TextRunConfig",
                "evaluation_type": "ALL", "save_dir": None, "verbose_logs": False, "audio_debug": False,
                "audio_taps": False, "auto_review": False, "upstream_imported": False, "runtime_adapter": "not-implemented"})
    return config


def expected_binding(manifest, role):
    settings = manifest["settings"]
    return {"model_revision": settings["roles"][role]["revision"],
            "generation_sha256": settings["roles"][role]["settings_sha256"],
            "serving_sha256": tau.digest(tau.encoded(settings["serving"][role])),
            "simulator_sha256": tau.digest(tau.encoded(settings["roles"]["simulator"])),
            "evaluator_sha256": tau.digest(tau.encoded(settings["roles"]["evaluator"]))}


def validate_report(value, manifest, manifest_sha, role):
    require(type(value) is dict and set(value) == {"schema", "manifest_sha256", "role", "kind",
                                                  "producer", "binding", "trials"}
            and value["schema"] == "nib.tau-trials/v1" and value["manifest_sha256"] == manifest_sha
            and value["role"] == role and value["kind"] in ("synthetic-fixture", "declared-unverified"))
    producer, binding = value["producer"], value["binding"]
    require(type(producer) is dict and set(producer) == {"git_sha", "adapter_sha256", "runtime_receipt_sha256"}
            and type(binding) is dict and set(binding) == set(expected_binding(manifest, role)))
    missing = []
    for key, item in producer.items():
        if item is None:
            missing.append("producer." + key)
        elif key == "git_sha":
            require(type(item) is str and re.fullmatch("[0-9a-f]{40}", item) and item != "0" * 40)
        else:
            tau.sha(item)
    for key, expected in expected_binding(manifest, role).items():
        if binding[key] is None:
            missing.append("binding." + key)
        else:
            require(type(binding[key]) is str and binding[key] == expected)
    rows = value["trials"]
    require(type(rows) is list and len(rows) <= manifest["expected_trials_per_role"])
    indexed = {}
    for row in rows:
        require(type(row) is dict and set(row) == {"domain", "task_id", "trial", "seed", "status", "reward",
                                                  "elapsed_ms", "requests", "usage"})
        domain, task = row["domain"], row["task_id"]
        require(type(domain) is str and domain in tau.DOMAINS and type(task) is str
                and task in manifest["domains"][domain]["task_ids"])
        tau.integer(row["trial"], 0, manifest["trials_per_task"] - 1)
        require(type(row["seed"]) is int and row["seed"] == manifest["trial_seeds"][row["trial"]])
        require(type(row["status"]) is str and row["status"] in STATUSES)
        if row["status"] == "evaluated":
            require(type(row["reward"]) is str and re.fullmatch(r"-?(0|[1-9][0-9]{0,5})(\.[0-9]{1,12})?", row["reward"]))
        else:
            require(row["reward"] is None)
        tau.integer(row["elapsed_ms"], 0, 86400000)
        tau.integer(row["requests"], 0, 100000)
        usage = row["usage"]
        if usage is not None:
            require(type(usage) is dict and set(usage) == {"prompt_tokens", "completion_tokens", "total_tokens", "cost_usd"})
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                tau.integer(usage[key], 0, 100000000)
            require(usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"])
            tau.amount(usage["cost_usd"])
        key = (domain, task, row["trial"])
        require(key not in indexed)
        indexed[key] = row
    return indexed, sorted(missing)


def successful(reward):
    # Match the pinned upstream float predicate, not a new evaluator.
    value = float(reward)
    return (1 - 1e-6) <= value <= (1 + 1e-6)


def pass_fraction(n, successes, k):
    require(type(n) is int and type(successes) is int and type(k) is int and 1 <= k <= n and 0 <= successes <= n)
    return Fraction(math.comb(successes, k), math.comb(n, k))


def number(value):
    with localcontext() as context:
        context.prec = 40
        return format(Decimal(value.numerator) / Decimal(value.denominator), ".9f")


def percentile(ordered, numerator):
    index = Fraction((len(ordered) - 1) * numerator, 40)
    lower = index.numerator // index.denominator
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def paired_interval(candidate, reference, seed, deadline):
    """Engineering analysis: paired task-cluster percentile bootstrap."""
    require(len(candidate) == len(reference) and len(candidate) >= 2)
    differences = [left - right for left, right in zip(candidate, reference)]
    rng = random.Random(seed)
    samples = []
    for index in range(RESAMPLES):
        if index % 64 == 0:
            require(time.monotonic() < deadline)
        samples.append(sum((differences[rng.randrange(len(differences))] for _ in differences), Fraction())
                       / len(differences))
    samples.sort()
    return {"difference": number(sum(differences, Fraction()) / len(differences)),
            "lower": number(percentile(samples, 1)), "upper": number(percentile(samples, 39)),
            "confidence_level": "0.95", "resamples": RESAMPLES, "seed": seed,
            "clusters": len(differences), "degenerate": samples[0] == samples[-1],
            "method": "paired-task-cluster-percentile-linear",
            "rng": "python-random-randrange", "python": list(sys.version_info[:3]),
            "acceptance_assessed": False}


def aggregate(manifest, manifest_sha, reports, pins, toolkit, *, deadline=None):
    deadline = time.monotonic() + MAX_SECONDS if deadline is None else deadline
    config = validate_manifest(manifest, manifest_sha, pins)
    require(type(reports) is dict and set(reports) == {"candidate", "reference"})
    indexed, bindings = {}, {}
    for role in ("candidate", "reference"):
        indexed[role], bindings[role] = validate_report(reports[role], manifest, manifest_sha, role)
    compatible = (reports["candidate"]["kind"] == reports["reference"]["kind"]
                  and all(reports["candidate"]["producer"][key] == reports["reference"]["producer"][key]
                          for key in ("git_sha", "adapter_sha256")))
    result = {"schema": "nib.tau-summary/v1", "status": "incomplete", "manifest_sha256": manifest_sha,
              "toolkit": toolkit, "report_sha256": {role: tau.digest(tau.encoded(row)) for role, row in reports.items()},
              "benchmark_executed_by_aggregator": False, "quality_accepted": False, "confidential": False,
              "provenance_verified": False, "declared_bindings_missing": bindings,
              "input_kind": reports["candidate"]["kind"] if compatible else "incompatible",
              "stage": config["stage"], "trials_per_task": manifest["trials_per_task"],
              "paired_producers_compatible": compatible, "upstream_revision": tau.REVISION,
              "upstream_metrics_sha256": METRICS_SHA, "domains": {}, "budget": {}}
    complete = compatible and not any(bindings.values())
    total_requests = total_tokens = unknown_usage = total_trial_ms = max_trial_ms = 0
    total_cost = Decimal(0)
    for rows in indexed.values():
        for row in rows.values():
            total_requests += row["requests"]
            total_trial_ms += row["elapsed_ms"]
            max_trial_ms = max(max_trial_ms, row["elapsed_ms"])
            if row["usage"] is None:
                unknown_usage += 1
            else:
                total_tokens += row["usage"]["total_tokens"]
                total_cost += tau.amount(row["usage"]["cost_usd"])
    over = []
    for key, value in (("requests", total_requests), ("total_tokens", total_tokens),
                       ("cost_usd", total_cost + tau.amount(config["limits"]["resource_cost_usd"]))):
        ceiling = tau.amount(config["limits"][key]) if key == "cost_usd" else config["limits"][key]
        if value > ceiling:
            over.append(key)
    if max_trial_ms > 1000 * config["limits"]["trial_seconds"]:
        over.append("trial_seconds")
    if total_trial_ms > 1000 * config["limits"]["run_seconds"]:
        over.append("run_seconds")
    result["budget"] = {"observed_requests": total_requests, "known_tokens": total_tokens,
                        "known_llm_cost_usd": format(total_cost, "f"), "unknown_usage_records": unknown_usage,
                        "observed_trial_ms": total_trial_ms, "maximum_trial_ms": max_trial_ms,
                        "unobserved_trials": 2 * manifest["expected_trials_per_role"]
                                             - sum(len(rows) for rows in indexed.values()),
                        "declared_resource_cost_usd": config["limits"]["resource_cost_usd"],
                        "exceeded_declared_limits": over, "live_enforcement": False}
    coverage_complete = all(len(rows) == manifest["expected_trials_per_role"]
                            and all(row["status"] == "evaluated" for row in rows.values())
                            for rows in indexed.values())
    complete = complete and coverage_complete and not over and unknown_usage == 0
    result["evaluated_coverage_complete"] = coverage_complete
    trials = manifest["trials_per_task"]
    for domain in tau.DOMAINS:
        require(time.monotonic() < deadline)
        ids = manifest["domains"][domain]["task_ids"]
        domain_result, vectors = {}, {}
        domain_complete = complete
        for role in ("candidate", "reference"):
            counts = {key: 0 for key in (*STATUSES, "missing", "unknown_usage")}
            lower = {k: [] for k in (1, 4) if k <= trials}
            upper = {k: [] for k in lower}
            evaluated_tasks = 0
            for task in ids:
                successes = unknown = 0
                for trial in range(trials):
                    row = indexed[role].get((domain, task, trial))
                    if row is None:
                        counts["missing"] += 1
                        unknown += 1
                    else:
                        counts[row["status"]] += 1
                        counts["unknown_usage"] += row["usage"] is None
                        if row["status"] == "evaluated":
                            successes += successful(row["reward"])
                        else:
                            unknown += 1
                evaluated_tasks += unknown == 0
                for k in lower:
                    lower[k].append(pass_fraction(trials, successes, k))
                    upper[k].append(pass_fraction(trials, successes + unknown, k))
            role_complete = (counts["evaluated"] == len(ids) * trials and counts["unknown_usage"] == 0
                             and not bindings[role] and not over)
            domain_complete = domain_complete and role_complete
            vectors[role] = lower
            metrics = {"1": None, "4": None}
            metrics.update({str(k): number(sum(values, Fraction()) / len(ids)) for k, values in lower.items()})
            domain_result[role] = {"expected_tasks": len(ids), "evaluated_tasks": evaluated_tasks,
                                   "expected_trials": len(ids) * trials, "counts": counts,
                                   "complete": role_complete, "upstream_formula_pass": metrics if role_complete else None,
                                   "frozen_denominator_bounds": {
                                       str(k): {"lower": metrics[str(k)],
                                                "upper": number(sum(upper[k], Fraction()) / len(ids))}
                                       for k in lower}}
        domain_result["paired"] = None
        if domain_complete:
            seed = int(tau.digest((manifest_sha + ":" + domain).encode())[:16], 16)
            domain_result["paired"] = {"1": None, "4": None}
            domain_result["paired"].update({
                str(k): paired_interval(vectors["candidate"][k], vectors["reference"][k], seed, deadline)
                for k in vectors["candidate"]})
        complete = complete and domain_complete
        result["domains"][domain] = domain_result
    result["status"] = "complete-declared-inputs" if complete else "incomplete"
    require(time.monotonic() < deadline and len(tau.encoded(result)) <= 1 << 20)
    return result


def main():
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            raise ValueError("tau aggregation arguments rejected")
    parser = Parser(description=__doc__)
    for name in ("manifest", "manifest-sha256", "candidate", "reference", "output"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    deadline = time.monotonic() + MAX_SECONDS
    toolkit = tau.committed_identity(deadline, extra_files=("scripts/tau_aggregate.py",))
    raw = tau.regular(Path(args.manifest), 1 << 20, deadline)
    require(tau.digest(raw) == args.manifest_sha256)
    manifest = tau.json_value(raw)
    pins_raw = tau.regular(ROOT / tau.PINS, 1 << 20, deadline)
    require(tau.digest(pins_raw) == tau.PINS_SHA)
    reports = {role: tau.json_value(tau.regular(Path(getattr(args, role)), 2 << 20, deadline))
               for role in ("candidate", "reference")}
    summary = aggregate(manifest, args.manifest_sha256, reports, tau.json_value(pins_raw), toolkit, deadline=deadline)
    tau.publish(Path(args.output), summary, filename="summary.json")
    print(tau.encoded({"aggregation": summary["status"], "quality_accepted": False,
                       "summary_sha256": tau.digest(tau.encoded(summary))}).decode(), end="")
    return 0 if summary["status"] == "complete-declared-inputs" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit("Tau aggregation rejected; no quality acceptance may be inferred.")
