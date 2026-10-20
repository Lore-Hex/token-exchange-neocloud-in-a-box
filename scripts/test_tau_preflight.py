# SPDX-License-Identifier: Apache-2.0
"""Offline foundation tests. All endpoint behavior and prices below are synthetic."""

import copy
import hashlib
import io
import json
import os
from pathlib import Path
import random
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
tau = types.ModuleType("tau_preflight")
tau.__file__ = str(ROOT / "scripts/tau_preflight.py")
exec(compile(Path(tau.__file__).read_bytes(), tau.__file__, "exec"), tau.__dict__)


def configuration():
    generation = {"temperature": "0", "max_tokens": 50, "num_retries": 0}
    roles = {}
    for role in tau.ROLES:
        roles[role] = {"model": "fixture-" + role, "revision": "fixture-immutable-v1",
                       "settings_sha256": tau.digest(tau.encoded(generation)), "generation": dict(generation),
                       "input_usd_per_million": "1.25", "output_usd_per_million": "3.5",
                       "rate_source_sha256": "1" * 64}
        if role in ("candidate", "reference"):
            roles[role].update(model="deepseek-ai/DeepSeek-V4.1-Flash-1017", revision=tau.WEIGHTS)
    serving = {key: "2" * 64 for key in ("image_sha256", "overlay_sha256", "arguments_sha256",
               "tokenizer_sha256", "tokenizer_config_sha256", "chat_template_sha256", "precision_sha256")}
    serving.update(tokenizer_sha256=tau.TOKENIZER, tokenizer_config_sha256=tau.TOKENIZER_CONFIG)
    return {"schema": "nib.tau-preflight-input/v1", "stage": "smoke", "seed": 42, "roles": roles,
            "serving": {"candidate": dict(serving), "reference": dict(serving)},
            "limits": {"concurrency": 1, "retries": 0, "turns": 10, "requests": 100,
                       "request_seconds": 10, "trial_seconds": 60, "run_seconds": 300,
                       "input_tokens_per_request": 100, "output_tokens_per_request": 50,
                       "total_tokens": 10000, "cost_usd": "1", "resource_cost_usd": "0.1"}}


def fixture_sources():
    # Original fake API shape only, not a replacement upstream implementation.
    fields = ("domain task_split_name llm_agent llm_args_agent llm_user llm_args_user max_steps max_errors "
              "num_trials seed max_retries hallucination_retries auto_review auto_resume verbose_logs timeout").split()
    sources = {
        "uv.lock": b"synthetic lockfile\n",
        "src/tau2/runner/batch.py": b"""
raise RuntimeError("PRIVATE-UPSTREAM-MUST-NOT-EXECUTE")
def run_single_task(config, task, *, seed=None, evaluation_type=None, save_dir=None,
                    verbose_logs=False, audio_debug=False, audio_taps=False, auto_review=False):
    raise RuntimeError("PRIVATE-UPSTREAM-MUST-NOT-EXECUTE")
""",
        "src/tau2/data_model/simulation.py":
            ("class BaseRunConfig:\n" + "".join("    " + f + ": object\n" for f in fields)
             + "class TextRunConfig:\n    pass\n").encode(),
    }
    for domain in tau.DOMAINS:
        prefix = "data/tau2/domains/" + domain + "/"
        ids = ["t" + str(n) for n in (8, 2, 1, 7, 3, 5, 6)]
        sources[prefix + "tasks.json"] = tau.encoded(
            [{"id": i, "user_scenario": "PRIVATE-SYNTHETIC-PROMPT"} for i in ids])
        sources[prefix + "split_tasks.json"] = tau.encoded({"base": ids[:6]})
    return sources


def pins_for(sources):
    return {"repository": "sierra-research/tau2-bench", "revision": tau.REVISION,
            "files": {name: {"sha256": tau.digest(raw), "bytes": len(raw),
                            "git_blob": hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()}
                      for name, raw in sources.items()}}


def write_sources(root, sources):
    for name, raw in sources.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)


class ManifestTests(unittest.TestCase):
    def test_real_files_deterministic_manifest_no_upstream_execution_or_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            sources = fixture_sources()
            write_sources(root, sources)
            pins = pins_for(sources)
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                first = tau.preflight(root, configuration(), pins, {"git_sha": "a" * 40})
                second = tau.preflight(root, configuration(), pins, {"git_sha": "a" * 40})
            self.assertEqual(first, second)
            self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))
            self.assertNotIn("PRIVATE", tau.encoded(first).decode())
            self.assertEqual(first["expected_trials_per_role"], 15)
            self.assertEqual(first["trial_seeds"], [random.Random(42).randint(0, 1000000)])
            self.assertEqual(first["domains"]["airline"]["task_ids"], ["t1", "t2", "t3", "t5", "t7"])
            for flag in ("benchmark_executed", "scored_run", "quality_accepted", "confidential"):
                self.assertIs(first[flag], False)
            self.assertFalse(first["upstream_contract"]["upstream_imported"])
            config = configuration()
            config["stage"] = "comparison"
            full = tau.preflight(root, config, pins, {"git_sha": "a" * 40})
            self.assertEqual(full["expected_trials_per_role"], 18 * 4)
            rng = random.Random(42)
            self.assertEqual(full["trial_seeds"], [rng.randint(0, 1000000) for _ in range(4)])

    def test_pinned_source_tamper_size_blob_and_revision_reject(self):
        for mutation in ("bytes", "sha256", "git_blob", "revision", "path", "content"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                root, sources = Path(tmp).resolve(), fixture_sources()
                write_sources(root, sources)
                pins = pins_for(sources)
                row = pins["files"]["uv.lock"]
                if mutation == "bytes":
                    row["bytes"] += 1
                elif mutation in ("sha256", "git_blob"):
                    row[mutation] = "0" * len(row[mutation])
                elif mutation == "revision":
                    pins["revision"] = "0" * 40
                elif mutation == "path":
                    pins["files"]["../private"] = row
                else:
                    (root / "uv.lock").write_bytes(b"PRIVATE")
                with self.assertRaises((ValueError, OSError)):
                    tau.preflight(root, configuration(), pins, {})

    def test_one_shared_deadline_no_per_file_reset(self):
        sources, seen = fixture_sources(), []
        def reader(path, limit, deadline):
            seen.append(deadline)
            return sources[path.relative_to("/fixture").as_posix()]
        with mock.patch.object(tau.time, "monotonic", return_value=100):
            tau.preflight(Path("/fixture"), configuration(), pins_for(sources), {}, reader=reader, deadline=101)
            self.assertEqual(set(seen), {101})
            with self.assertRaises(ValueError):
                tau.preflight(Path("/fixture"), configuration(), pins_for(sources), {}, reader=reader, deadline=100)

    def test_tasks_duplicate_invalid_split_and_public_structured_ids(self):
        tasks = [{"id": f"[issue]user_{i}[PERSONA:None]"} for i in range(6)]
        split = {"base": [t["id"] for t in tasks]}
        self.assertEqual(tau.task_ids(tau.encoded(tasks), tau.encoded(split)), split["base"])
        for rows, selections in ((tasks + tasks[:1], split), (tasks, {"base": ["absent"] * 5}),
                                  (tasks, {"base": split["base"][:4]}),
                                  ([{"id": "/private/path"}] + tasks, split)):
            with self.subTest(rows=len(rows)), self.assertRaises(ValueError):
                tau.task_ids(tau.encoded(rows), tau.encoded(selections))
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}'):
            with self.assertRaises(ValueError):
                tau.json_value(raw)

    def test_actual_api_shape_is_checked_not_guessed(self):
        sources = fixture_sources()
        self.assertEqual(tau.upstream_contract(sources)["entrypoint"], "tau2.runner.batch.run_single_task")
        for old, new in ((b"config, task", b"configuration, task"), (b"save_dir", b"output_path")):
            changed = dict(sources)
            changed["src/tau2/runner/batch.py"] = changed["src/tau2/runner/batch.py"].replace(old, new)
            with self.assertRaises(ValueError):
                tau.upstream_contract(changed)


class InputTests(unittest.TestCase):
    def test_strict_public_settings_reject_overrides_and_unknown_provenance(self):
        for path, value in (
                (("endpoint",), "https://PRIVATE"),
                (("roles", "candidate", "revision"), "latest"),
                (("roles", "simulator", "model"), configuration()["roles"]["candidate"]["model"]),
                (("roles", "candidate", "input_usd_per_million"), 1.25),
                (("roles", "candidate", "input_usd_per_million"), "NaN"),
                (("roles", "candidate", "input_usd_per_million"), "-1"),
                (("roles", "simulator", "model"), "https://PRIVATE"),
                (("roles", "simulator", "settings_sha256"), "0" * 64),
                (("roles", "simulator", "generation", "max_tokens"), 51),
                (("serving", "candidate", "tokenizer_sha256"), "a" * 64),
                (("limits", "concurrency"), 2), (("limits", "concurrency"), True),
                (("limits", "retries"), 1), (("limits", "run_seconds"), 86401),
                (("limits", "trial_seconds"), 301), (("limits", "cost_usd"), "0.1")):
            value_config = configuration()
            target = value_config
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            with self.subTest(path=path, value=value), self.assertRaises(ValueError):
                tau.settings(value_config)

    def test_settings_are_detached_from_mutable_input(self):
        original = configuration()
        frozen = tau.settings(original)
        original["limits"]["cost_usd"] = "99999"
        self.assertEqual(frozen["limits"]["cost_usd"], "1")

    def test_paired_generation_settings_must_match_even_with_valid_individual_hashes(self):
        for key, value in (("temperature", "0.5"), ("max_tokens", 49)):
            config = configuration()
            reference = config["roles"]["reference"]
            reference["generation"][key] = value
            reference["settings_sha256"] = tau.digest(tau.encoded(reference["generation"]))
            with self.subTest(key=key), self.assertRaises(ValueError):
                tau.settings(config)
        config = configuration()
        config["roles"]["reference"]["settings_sha256"] = "a" * 64
        with self.assertRaises(ValueError):
            tau.settings(config)

    def test_regular_reader_rejects_links_fifo_ancestors_and_oversize(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            normal = root / "normal"
            normal.write_bytes(b"safe")
            self.assertEqual(tau.regular(normal, 4, time.monotonic() + 1), b"safe")
            with self.assertRaises(ValueError):
                tau.regular(normal, 3, time.monotonic() + 1)
            with self.assertRaises(ValueError):
                tau.regular(normal, 4, time.monotonic() - 1)
            (root / "link").symlink_to(normal)
            (root / "alias").symlink_to(root, target_is_directory=True)
            os.mkfifo(root / "fifo")
            for path in (root / "link", root / "alias/normal", root / "fifo", root):
                with self.subTest(path=path.name), self.assertRaises((ValueError, OSError)):
                    tau.regular(path, 1024, time.monotonic() + 1)
            os.link(normal, root / "hard")
            with self.assertRaises(ValueError):
                tau.regular(normal, 1024, time.monotonic() + 1)

    def test_private_exclusive_durable_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            target = root / "result"
            manifest = {"schema": "fixture", "benchmark_executed": False}
            with mock.patch.object(tau.os, "fsync", wraps=os.fsync) as sync:
                tau.publish(target, manifest)
            self.assertEqual(sync.call_count, 3)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((target / "manifest.json").stat().st_mode), 0o600)
            original = (target / "manifest.json").read_bytes()
            with self.assertRaises(FileExistsError):
                tau.publish(target, {"overwrite": True})
            self.assertEqual((target / "manifest.json").read_bytes(), original)
            (root / "alias").symlink_to(root, target_is_directory=True)
            with self.assertRaises(OSError):
                tau.publish(root / "alias/new", manifest)
            self.assertFalse((root / "new").exists())

    def test_publication_failure_keeps_original_attempt_and_refuses_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp).resolve() / "attempt"
            with mock.patch.object(tau.os, "fsync", side_effect=OSError("PRIVATE")):
                with self.assertRaises(OSError):
                    tau.publish(target, {"synthetic": True})
            with self.assertRaises(FileExistsError):
                tau.publish(target, {"synthetic": True})

    def test_cli_failure_never_emits_input_path_or_secret(self):
        result = subprocess.run([sys.executable, "-I", "-S", "-B", str(ROOT / "scripts/tau_preflight.py"),
                                 "--PRIVATE", "PRIVATE-TOKEN"], capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertNotIn(b"PRIVATE", result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)
        self.assertLess(len(result.stderr), 100)


class BudgetTests(unittest.TestCase):
    def budget(self, config=None):
        self.now = 100.0
        return tau.Budget(config or configuration(), clock=lambda: self.now)

    def test_fake_endpoint_reservation_exact_decimal_and_content_free_receipt(self):
        budget = self.budget()
        budget.begin_trial()
        config = budget.reserve("candidate")
        self.assertEqual(config, {"timeout_seconds": 10, "max_tokens": 50, "num_retries": 0})
        self.assertEqual(str(budget.spent), "0.1003")
        # Fake endpoint only. Bodies are deliberately never passed to settle.
        response = {"content": "PRIVATE-COMPLETION", "usage":
                    {"prompt_tokens": 40, "completion_tokens": 10, "total_tokens": 50}}
        result = budget.settle(response["usage"])
        self.assertEqual(result, {"requests": 1, "tokens": 50, "cost_usd": "0.100085"})
        self.assertNotIn("PRIVATE", tau.encoded(result).decode())
        budget.end_trial()

    def test_missing_malformed_and_extra_usage_stays_unknown_and_aborts(self):
        for usage in (None, {}, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "raw": "PRIVATE"},
                      {"prompt_tokens": True, "completion_tokens": 1, "total_tokens": 2},
                      {"prompt_tokens": 101, "completion_tokens": 1, "total_tokens": 102},
                      {"prompt_tokens": 1, "completion_tokens": 51, "total_tokens": 52},
                      {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 1}):
            with self.subTest(usage=usage):
                budget = self.budget()
                budget.begin_trial()
                budget.reserve("simulator")
                with self.assertRaises(ValueError):
                    budget.settle(usage)
                self.assertTrue(budget.failed)
                self.assertEqual(str(budget.spent), "0.1003")
                self.assertEqual(budget.tokens, 150)
                with self.assertRaises(ValueError):
                    budget.reserve("simulator")
                with self.assertRaises(ValueError):
                    budget.end_trial()

    def test_cost_tokens_requests_turns_and_concurrency_before_any_request(self):
        for field, value in (("cost_usd", "0.1001"), ("total_tokens", 150), ("requests", 1), ("turns", 1)):
            config = configuration()
            config["limits"][field] = value
            budget = self.budget(config)
            budget.begin_trial()
            if field != "cost_usd":
                budget.reserve("reference")
                with self.assertRaises(ValueError):
                    budget.reserve("candidate")
                budget.settle({"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150})
            with self.subTest(field=field), self.assertRaises(ValueError):
                budget.reserve("reference")

    def test_deadline_and_cancellation_are_sticky_no_implicit_retry(self):
        for mode in ("deadline", "cancel"):
            budget = self.budget()
            budget.begin_trial()
            budget.reserve("candidate")
            if mode == "deadline":
                self.now = 110.0
                with self.assertRaises(ValueError):
                    budget.settle({"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
            else:
                budget.abandon()
            with self.assertRaises(ValueError):
                budget.reserve("candidate")
            self.assertEqual(budget.requests, 1)
            self.assertEqual(str(budget.spent), "0.1003")

    def test_trial_run_deadlines_and_configuration_cannot_be_extended(self):
        config = configuration()
        budget = self.budget(config)
        config["limits"]["run_seconds"] = 99999
        config["roles"]["candidate"]["output_usd_per_million"] = "0"
        budget.begin_trial()
        self.now = 159.0
        self.assertEqual(budget.reserve("candidate")["timeout_seconds"], 1)
        self.now = 160.0
        with self.assertRaises(ValueError):
            budget.settle({"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
        other = self.budget()
        self.now = 400.0
        with self.assertRaises(ValueError):
            other.begin_trial()


class BindingTests(unittest.TestCase):
    def test_committed_source_binding_dirty_rejection_and_offline_git_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            files = ("scripts/tau_preflight.py", tau.PINS, tau.PACK + "/pack.yaml", tau.PACK + "/files.sha256")
            for name in files:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((ROOT / name).read_bytes())
            env = {"PATH": os.defpath, "HOME": str(root), "GIT_CONFIG_NOSYSTEM": "1",
                   "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "Fixture",
                   "GIT_AUTHOR_EMAIL": "fixture@example.invalid", "GIT_COMMITTER_NAME": "Fixture",
                   "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
            for args in (("init", "-q"), ("add", "."), ("-c", "commit.gpgsign=false", "commit", "-qm", "fixture")):
                subprocess.run(["git", "-C", str(root), *args], env=env, check=True, capture_output=True, timeout=10)
            with mock.patch.object(tau, "ROOT", root), mock.patch.object(tau.subprocess, "run", wraps=subprocess.run) as run:
                row = tau.committed_identity(time.monotonic() + 10)
                self.assertEqual(set(row["files"]), set(files))
                controlled = run.call_args.kwargs["env"]
                self.assertEqual(controlled["GIT_NO_LAZY_FETCH"], "1")
                self.assertEqual(controlled["GIT_ALLOW_PROTOCOL"], "")
                self.assertEqual(controlled["GIT_NO_REPLACE_OBJECTS"], "1")
                (root / files[0]).write_bytes(b"PRIVATE")
                with self.assertRaises(ValueError):
                    tau.committed_identity(time.monotonic() + 10)


def public_source_audit(root):
    raw = (ROOT / tau.PINS).read_bytes()
    tau.require(tau.digest(raw) == tau.PINS_SHA)
    manifest = tau.preflight(Path(root), configuration(), tau.json_value(raw), {"audit": "software-only"})
    counts = {name: row["base_count"] for name, row in manifest["domains"].items()}
    tau.require(counts == {"airline": 50, "retail": 114, "telecom": 114})
    print(json.dumps({"public_sources": len(manifest["upstream"]["files"]),
                      "verified_bytes": manifest["inputs_verified_bytes"], "base_tasks": counts,
                      "upstream_imported": False, "benchmark_executed": False}, sort_keys=True))


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--public-sources":
        public_source_audit(sys.argv[2])
    else:
        unittest.main()
