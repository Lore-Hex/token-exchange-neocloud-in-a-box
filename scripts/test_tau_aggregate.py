# SPDX-License-Identifier: Apache-2.0
"""Synthetic arithmetic/retention fixtures only; no evaluator or endpoint calls."""

import ast
import copy
from decimal import Decimal
from fractions import Fraction
import hashlib
import io
import math
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def module(name):
    path = ROOT / "scripts" / name
    result = types.ModuleType(path.stem)
    result.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), "exec"), result.__dict__)
    return result


agg = module("tau_aggregate.py")
fixtures = module("test_tau_preflight.py")
tau = agg.tau
PINS = tau.json_value((ROOT / tau.PINS).read_bytes())


def manifest(stage="smoke"):
    config = fixtures.configuration()
    config["stage"] = stage
    config["limits"].update(requests=100000, total_tokens=100000000, cost_usd="99999", resource_cost_usd="0")
    sources = fixtures.fixture_sources()
    toolkit = {"git_sha": "a" * 40, "files": {name: "a" * 64 for name in
               ("scripts/tau_preflight.py", tau.PINS, tau.PACK + "/pack.yaml", tau.PACK + "/files.sha256")}}
    row = tau.preflight(Path("/fixture"), config, fixtures.pins_for(sources), toolkit,
                        reader=lambda path, limit, deadline: sources[path.relative_to("/fixture").as_posix()])
    # A synthetic sealed-manifest fixture, not a claim that these task IDs were
    # measured from the public snapshot. Production consumes a separately frozen
    # preflight manifest and requires its caller-supplied digest.
    row["upstream"] = copy.deepcopy(PINS)
    row["dependency_lock_sha256"] = PINS["files"]["uv.lock"]["sha256"]
    row["inputs_verified_bytes"] = sum(item["bytes"] for item in PINS["files"].values())
    row["domains"] = {domain: {"base_count": count, "task_ids": sorted(str(i) for i in range(
                       5 if stage == "smoke" else count))} for domain, count in agg.BASE_COUNTS.items()}
    row["expected_trials_per_role"] = sum(len(item["task_ids"]) for item in row["domains"].values()) * row["trials_per_task"]
    return row


def reports(frozen):
    sha = tau.digest(tau.encoded(frozen))
    result = {}
    for role in ("candidate", "reference"):
        rows = [{"domain": domain, "task_id": task, "trial": trial, "seed": frozen["trial_seeds"][trial],
                 "status": "evaluated", "reward": "1", "elapsed_ms": 1, "requests": 1,
                 "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost_usd": "0"}}
                for domain in tau.DOMAINS for task in frozen["domains"][domain]["task_ids"]
                for trial in range(frozen["trials_per_task"])]
        result[role] = {"schema": "nib.tau-trials/v1", "manifest_sha256": sha, "role": role,
                        "kind": "synthetic-fixture",
                        "producer": {"git_sha": "b" * 40, "adapter_sha256": "c" * 64,
                                     "runtime_receipt_sha256": "d" * 64},
                        "binding": agg.expected_binding(frozen, role), "trials": rows}
    return result


def summarize(frozen, rows, *, deadline=None):
    with mock.patch.object(agg, "RESAMPLES", 80):
        return agg.aggregate(frozen, tau.digest(tau.encoded(frozen)), rows, PINS, {}, deadline=deadline)


class AggregationTests(unittest.TestCase):
    def test_complete_fixture_has_no_quality_or_provenance_acceptance(self):
        frozen = manifest()
        row = summarize(frozen, reports(frozen))
        self.assertEqual(row["status"], "complete-declared-inputs")
        for key in ("quality_accepted", "confidential", "provenance_verified", "benchmark_executed_by_aggregator"):
            self.assertIs(row[key], False)
        self.assertEqual(row["input_kind"], "synthetic-fixture")
        for domain in tau.DOMAINS:
            self.assertEqual(row["domains"][domain]["candidate"]["upstream_formula_pass"],
                             {"1": "1.000000000", "4": None})
            self.assertEqual(row["domains"][domain]["paired"]["1"]["difference"], "0.000000000")
            self.assertIsNone(row["domains"][domain]["paired"]["4"])

    def test_four_trial_formula_all_trials_not_best_of_four(self):
        frozen = manifest("comparison")
        rows = reports(frozen)
        for role in rows:
            for record in rows[role]["trials"]:
                record["reward"] = "0" if record["trial"] == 3 else "1"
        result = summarize(frozen, rows)
        self.assertEqual(result["status"], "complete-declared-inputs")
        self.assertEqual(result["domains"]["airline"]["candidate"]["upstream_formula_pass"],
                         {"1": "0.750000000", "4": "0.000000000"})
        self.assertEqual(result["domains"]["airline"]["candidate"]["expected_trials"], 200)
        self.assertEqual(result["domains"]["retail"]["candidate"]["expected_trials"], 456)

    def test_missing_and_failed_trials_keep_frozen_denominator(self):
        for mode in ("missing", *agg.STATUSES[1:]):
            frozen = manifest()
            rows = reports(frozen)
            if mode == "missing":
                rows["reference"]["trials"].pop(0)
            else:
                rows["reference"]["trials"][0].update(status=mode, reward=None)
            result = summarize(frozen, rows)
            self.assertEqual(result["status"], "incomplete")
            domain = result["domains"]["airline"]
            self.assertEqual(domain["reference"]["expected_trials"], 5)
            self.assertEqual(domain["reference"]["counts"][mode], 1)
            self.assertEqual(domain["reference"]["counts"]["evaluated"], 4)
            self.assertIsNone(domain["reference"]["upstream_formula_pass"])
            self.assertIsNone(domain["paired"])
            self.assertTrue(all(row["paired"] is None for row in result["domains"].values()))
            self.assertFalse(result["evaluated_coverage_complete"])
            self.assertEqual(domain["reference"]["evaluated_tasks"], 4)
            self.assertEqual(domain["reference"]["frozen_denominator_bounds"]["1"],
                             {"lower": "0.800000000", "upper": "1.000000000"})

    def test_evaluated_task_failure_counts_zero_not_missing(self):
        frozen = manifest()
        rows = reports(frozen)
        rows["candidate"]["trials"][0]["reward"] = "0"
        result = summarize(frozen, rows)
        self.assertEqual(result["status"], "complete-declared-inputs")
        row = result["domains"]["airline"]["candidate"]
        self.assertEqual(row["upstream_formula_pass"]["1"], "0.800000000")
        self.assertEqual(row["counts"]["missing"], 0)
        self.assertEqual(row["counts"]["evaluated"], 5)

    def test_four_trial_unknown_bounds_and_empty_reference_stay_incomplete(self):
        frozen = manifest("comparison")
        rows = reports(frozen)
        for trial in rows["reference"]["trials"]:
            if trial["trial"] == 3:
                trial.update(status="infrastructure_error", reward=None)
        result = summarize(frozen, rows)
        self.assertEqual(result["status"], "incomplete")
        bounds = result["domains"]["airline"]["reference"]["frozen_denominator_bounds"]
        self.assertEqual(bounds, {"1": {"lower": "0.750000000", "upper": "1.000000000"},
                                  "4": {"lower": "0.000000000", "upper": "1.000000000"}})
        rows["reference"]["trials"] = []
        result = summarize(frozen, rows)
        for domain in result["domains"].values():
            self.assertEqual(domain["reference"]["counts"]["missing"], domain["reference"]["expected_trials"])
            self.assertEqual(domain["reference"]["evaluated_tasks"], 0)
            self.assertIsNone(domain["paired"])

    def test_input_order_does_not_unpair_matched_tasks(self):
        frozen = manifest("comparison")
        rows = reports(frozen)
        for report in rows.values():
            for trial in report["trials"]:
                trial["reward"] = "0" if int(trial["task_id"]) % 3 == trial["trial"] else "1"
        baseline = summarize(frozen, rows)
        rows["reference"]["trials"].reverse()
        shuffled = summarize(frozen, rows)
        self.assertEqual(shuffled["domains"], baseline["domains"])
        for domain in shuffled["domains"].values():
            for interval in domain["paired"].values():
                self.assertEqual((interval["difference"], interval["lower"], interval["upper"]),
                                 ("0.000000000",) * 3)

    def test_duplicate_wrong_task_seed_and_role_do_not_hide_coverage(self):
        mutations = (lambda r: r["trials"].__setitem__(1, copy.deepcopy(r["trials"][0])),
                     lambda r: r["trials"][0].update(task_id="PRIVATE-UNKNOWN"),
                     lambda r: r["trials"][0].update(trial=True),
                     lambda r: r["trials"][0].update(seed=1),
                     lambda r: r.update(role="candidate"),
                     lambda r: r.update(manifest_sha256="e" * 64))
        for mutate in mutations:
            frozen = manifest()
            rows = reports(frozen)
            mutate(rows["reference"])
            with self.assertRaises(ValueError):
                summarize(frozen, rows)

    def test_each_missing_reference_provenance_item_blocks_paired_analysis(self):
        for group in ("producer", "binding"):
            for key in reports(manifest())["reference"][group]:
                frozen = manifest()
                rows = reports(frozen)
                rows["reference"][group][key] = None
                result = summarize(frozen, rows)
                self.assertEqual(result["status"], "incomplete")
                self.assertIn(group + "." + key, result["declared_bindings_missing"]["reference"])
                self.assertTrue(all(row["paired"] is None for row in result["domains"].values()))
        frozen = manifest()
        rows = reports(frozen)
        rows["reference"]["producer"]["adapter_sha256"] = "e" * 64
        result = summarize(frozen, rows)
        self.assertFalse(result["paired_producers_compatible"])
        self.assertEqual(result["status"], "incomplete")

    def test_wrong_declared_model_runtime_simulator_and_generation_reject(self):
        for key in agg.expected_binding(manifest(), "reference"):
            frozen = manifest()
            rows = reports(frozen)
            rows["reference"]["binding"][key] = "e" * (40 if key == "model_revision" else 64)
            with self.subTest(key=key), self.assertRaises(ValueError):
                summarize(frozen, rows)

    def test_usage_unknown_and_budget_excess_are_not_zero_or_complete(self):
        frozen = manifest()
        rows = reports(frozen)
        rows["reference"]["trials"][0]["usage"] = None
        result = summarize(frozen, rows)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["budget"]["unknown_usage_records"], 1)
        self.assertIsNone(result["domains"]["airline"]["paired"])
        for field, value in (("requests", 100001), ("reward", "NaN"), ("elapsed_ms", -1)):
            changed = reports(frozen)
            changed["reference"]["trials"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                summarize(frozen, changed)
        frozen["settings"]["limits"]["cost_usd"] = "1"
        rows = reports(frozen)
        rows["candidate"]["trials"][0]["usage"]["cost_usd"] = "2"
        result = summarize(frozen, rows)
        self.assertEqual(result["budget"]["exceeded_declared_limits"], ["cost_usd"])
        self.assertEqual(result["status"], "incomplete")
        self.assertTrue(all(row["paired"] is None for row in result["domains"].values()))
        for field in ("trial_seconds", "run_seconds"):
            frozen = manifest()
            rows = reports(frozen)
            if field == "trial_seconds":
                rows["candidate"]["trials"][0]["elapsed_ms"] = 1000 * frozen["settings"]["limits"][field] + 1
            else:
                frozen["settings"]["limits"].update(request_seconds=1, trial_seconds=60, run_seconds=60)
                rows = reports(frozen)
                for report in rows.values():
                    for trial in report["trials"]:
                        trial["elapsed_ms"] = 3000
            result = summarize(frozen, rows)
            self.assertIn(field, result["budget"]["exceeded_declared_limits"])
            self.assertEqual(result["status"], "incomplete")

    def test_strict_allowlist_and_fixed_errors_do_not_emit_bodies(self):
        for location in ("root", "producer", "trial", "usage"):
            frozen = manifest()
            rows = reports(frozen)
            report = rows["candidate"]
            target = {"root": report, "producer": report["producer"],
                      "trial": report["trials"][0], "usage": report["trials"][0]["usage"]}[location]
            target["content"] = "PRIVATE-CONTENT"
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err), self.assertRaises(ValueError) as rejected:
                summarize(frozen, rows)
            self.assertNotIn("PRIVATE", str(rejected.exception) + out.getvalue() + err.getvalue())
        completed = summarize(manifest(), reports(manifest()))
        self.assertNotIn("trials", completed["domains"]["airline"]["candidate"])

    def test_manifest_seal_catalog_settings_and_denominator_tamper_reject(self):
        frozen = manifest()
        sha = tau.digest(tau.encoded(frozen))
        changed = copy.deepcopy(frozen)
        changed["settings"]["seed"] += 1
        with self.assertRaises(ValueError):
            agg.validate_manifest(changed, sha, PINS)
        for field, value in (("expected_trials_per_role", 1), ("trial_seeds", [1]),
                             ("upstream", {}), ("dependencies_installed_or_verified", True),
                             ("retention", {"raw_logs": True})):
            changed = copy.deepcopy(frozen)
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                agg.validate_manifest(changed, tau.digest(tau.encoded(changed)), PINS)

    def test_elapsed_deadline_never_returns_partial_success(self):
        frozen = manifest()
        with self.assertRaises(ValueError):
            summarize(frozen, reports(frozen), deadline=time.monotonic() - 1)


class MetricsTests(unittest.TestCase):
    def test_combination_formula_grid(self):
        for trials in range(1, 9):
            for successes in range(trials + 1):
                for k in range(1, trials + 1):
                    self.assertEqual(agg.pass_fraction(trials, successes, k),
                                     Fraction(math.comb(successes, k), math.comb(trials, k)))
        for args in ((0, 0, 1), (1, 1, 4), (4, 5, 1), (4, True, 1)):
            with self.assertRaises(ValueError):
                agg.pass_fraction(*args)

    def test_upstream_float_success_tolerance(self):
        for value, expected in (("1", True), ("0.999999", True), ("1.000001", True),
                                ("0.999998999999", False), ("1.000001000001", False),
                                ("0", False), ("-1", False), ("2", False)):
            self.assertEqual(agg.successful(value), expected)

    def test_paired_clusters_keep_matched_outcomes_and_all_trials_together(self):
        values = [Fraction(0), Fraction(1, 4), Fraction(2, 4), Fraction(3, 4), Fraction(1)]
        with mock.patch.object(agg, "RESAMPLES", 500):
            same = agg.paired_interval(values, values, 42, time.monotonic() + 5)
            self.assertEqual((same["difference"], same["lower"], same["upper"]),
                             ("0.000000000",) * 3)
            self.assertTrue(same["degenerate"])
            shifted = agg.paired_interval(values, [Fraction(0)] * 5, 42, time.monotonic() + 5)
            repeated = agg.paired_interval(values, [Fraction(0)] * 5, 42, time.monotonic() + 5)
        self.assertEqual(shifted, repeated)
        self.assertEqual(shifted["difference"], "0.500000000")
        self.assertFalse(shifted["degenerate"])
        self.assertLess(Decimal(shifted["lower"]), Decimal(shifted["difference"]))
        self.assertGreater(Decimal(shifted["upper"]), Decimal(shifted["difference"]))
        self.assertFalse(shifted["acceptance_assessed"])

    def test_linear_percentile_and_deadline(self):
        values = [Fraction(i) for i in range(41)]
        self.assertEqual(agg.percentile(values, 1), 1)
        self.assertEqual(agg.percentile(values, 39), 39)
        with self.assertRaises(ValueError):
            agg.paired_interval([Fraction(0), Fraction(1)], [Fraction(0), Fraction(0)], 0, time.monotonic() - 1)


class FilesystemTests(unittest.TestCase):
    def test_committed_cli_publication_incomplete_reference_nooverwrite_and_source_tamper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            names = ("scripts/tau_preflight.py", "scripts/tau_aggregate.py", tau.PINS,
                     tau.PACK + "/pack.yaml", tau.PACK + "/files.sha256")
            for name in names:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((ROOT / name).read_bytes())
            env = {"PATH": os.defpath, "HOME": str(root), "GIT_CONFIG_NOSYSTEM": "1",
                   "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "Fixture",
                   "GIT_AUTHOR_EMAIL": "fixture@example.invalid", "GIT_COMMITTER_NAME": "Fixture",
                   "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
            for args in (("init", "-q"), ("add", "."), ("-c", "commit.gpgsign=false", "commit", "-qm", "fixture")):
                subprocess.run(["git", "-C", str(root), *args], env=env, check=True, capture_output=True, timeout=10)
            frozen = manifest()
            rows = reports(frozen)
            (root / "manifest.json").write_bytes(tau.encoded(frozen))
            for role, row in rows.items():
                (root / (role + ".json")).write_bytes(tau.encoded(row))

            def run(destination):
                return subprocess.run([sys.executable, "-I", "-S", "-B", str(root / "scripts/tau_aggregate.py"),
                    "--manifest", str(root / "manifest.json"), "--manifest-sha256", tau.digest(tau.encoded(frozen)),
                    "--candidate", str(root / "candidate.json"), "--reference", str(root / "reference.json"),
                    "--output", str(root / destination)], capture_output=True, timeout=10)

            completed = run("complete")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            summary_path = root / "complete/summary.json"
            original = summary_path.read_bytes()
            summary = tau.json_value(original)
            self.assertEqual(set(summary["toolkit"]["files"]), set(names))
            self.assertEqual(stat.S_IMODE(summary_path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(summary_path.parent.stat().st_mode), 0o700)
            self.assertEqual(run("complete").returncode, 1)
            self.assertEqual(summary_path.read_bytes(), original)
            rows["reference"]["producer"]["runtime_receipt_sha256"] = None
            (root / "reference.json").write_bytes(tau.encoded(rows["reference"]))
            self.assertEqual(run("incomplete").returncode, 1)
            incomplete = tau.json_value((root / "incomplete/summary.json").read_bytes())
            self.assertEqual(incomplete["status"], "incomplete")
            self.assertTrue(all(row["paired"] is None for row in incomplete["domains"].values()))
            # The entrypoint uses the same anchored reader for all input roles.
            # A FIFO/duplicate key cannot hang or publish a success artifact.
            (root / "reference.json").unlink()
            os.mkfifo(root / "reference.json")
            self.assertEqual(run("fifo").returncode, 1)
            self.assertFalse((root / "fifo").exists())
            (root / "reference.json").unlink()
            (root / "reference.json").write_bytes(b'{"PRIVATE":1,"PRIVATE":2}')
            rejected = run("duplicate")
            self.assertEqual(rejected.returncode, 1)
            self.assertNotIn(b"PRIVATE", rejected.stderr + rejected.stdout)
            self.assertFalse((root / "duplicate").exists())
            (root / "reference.json").write_bytes(tau.encoded(rows["reference"]))
            with (root / "scripts/tau_aggregate.py").open("ab") as stream:
                stream.write(b"\n# uncommitted\n")
            rejected = run("tampered")
            self.assertEqual(rejected.returncode, 1)
            self.assertFalse((root / "tampered").exists())

    def test_pinned_loader_rejects_symlink_fifo_hardlink_changed_or_oversized_helper(self):
        original = (ROOT / "scripts/tau_preflight.py").read_bytes()
        for kind in ("symlink", "fifo", "hardlink", "changed", "oversize"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                (root / "scripts").mkdir()
                path = root / "scripts/tau_preflight.py"
                if kind == "fifo":
                    os.mkfifo(path)
                elif kind == "symlink":
                    path.symlink_to(ROOT / "scripts/tau_preflight.py")
                else:
                    path.write_bytes(original if kind == "hardlink" else b"x" * (65537 if kind == "oversize" else 1))
                    if kind == "hardlink":
                        os.link(path, root / "other")
                with mock.patch.object(agg, "ROOT", root), self.assertRaises((ValueError, OSError)):
                    agg.load_preflight()

    def test_invalid_summary_filename_and_source_escape_reject(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                tau.publish(Path(tmp).resolve() / "out", {}, filename="../private")
        for extra in (("../private",), ("/private",), ("a//b",)):
            with self.assertRaises(ValueError):
                tau.committed_identity(time.monotonic() + 1, extra_files=extra)

    def test_cli_failure_sanitization(self):
        run = subprocess.run([sys.executable, "-I", "-S", "-B", str(ROOT / "scripts/tau_aggregate.py"),
                              "--PRIVATE", "PRIVATE-CREDENTIAL"], capture_output=True, timeout=5)
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(run.stdout, b"")
        self.assertNotIn(b"PRIVATE", run.stderr)
        self.assertNotIn(b"Traceback", run.stderr)


def public_metric_audit(path):
    raw = tau.regular(Path(path), 65536, time.monotonic() + 5)
    tau.require(hashlib.sha256(raw).hexdigest() == agg.METRICS_SHA)
    nodes = [node for node in ast.parse(raw).body if isinstance(node, ast.FunctionDef)
             and node.name in ("is_successful", "pass_hat_k")]
    tau.require(len(nodes) == 2)
    # Only the two reviewed pure arithmetic functions, not the evaluator,
    # dataframe wrapper, package imports or model/tool code.
    scope = {"math": math}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "<pinned-public-metrics>", "exec",
                 flags=0, dont_inherit=True), scope)
    for trials in range(1, 9):
        for successes in range(trials + 1):
            for k in range(1, trials + 1):
                tau.require(float(agg.pass_fraction(trials, successes, k)) == scope["pass_hat_k"](trials, successes, k))
    for value in ("0", "1", "-1", "2", "0.999999", "1.000001", "0.999998999999", "1.000001000001"):
        tau.require(agg.successful(value) == scope["is_successful"](float(value)))
    print("Pinned pure metric functions agree with fixtures; no evaluator/package/endpoint execution.")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--public-metrics":
        public_metric_audit(sys.argv[2])
    else:
        unittest.main()
