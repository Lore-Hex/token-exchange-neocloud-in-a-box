# SPDX-License-Identifier: Apache-2.0
"""Fake APT outputs; exact historical report import; no package/container runs."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    module = types.ModuleType("apt_diagnostics_fixture")
    module.__file__ = str(ROOT / path)
    exec(compile(Path(module.__file__).read_bytes(), module.__file__, "exec",
                 dont_inherit=True), module.__dict__)
    return module


fixtures = load("scripts/test_guest_tools.py")
sim, runner = fixtures.sim, fixtures.runner
recorder = load("scripts/record_apt_report.py")
SECRET = "PRIVATE_SENTINEL_unsupported_path_and_credential"
HISTORICAL = ROOT / "evidence/p3/2026-10-20-apt-resolve-failure/output.log"
MISSING_PREFERENCES_STDERR = (
    b"W: Unable to read /work/etc/preferences.d/ - DirectoryExists "
    b"(2: No such file or directory)\n")


def historical():
    flags = {"installable_verified": False, "bootable": False, "deployable": False}
    return recorder.base.encoded({
        **flags, "schema": "nib.apt-container-run/v1", "result": "fail", "stage": "complete",
        "cleanup": "confirmed", "git_sha": recorder.PRODUCER,
        "started_at": "2026-10-20T18:52:06Z", "finished_at": "2026-10-20T18:52:08Z",
        "image_archive_sha256": "6c64249d47cef04c2b029fab997edc8561d527b7000acd1e267b231ff50ab714",
        "config_sha256": "d32f7f89bbbe8b9686d9702a27835b48feaec78457a6671198e8c2aaebcaccf9",
        "layer_sha256": "54443fb63ca3b8236520f4d34da26560eb30e19df0f819488c00bbb98f1b4c8d",
        "simulation": {
            **flags, "schema": "nib.apt-simulation/v1", "result": "fail", "stage": "resolve",
            "git_sha": recorder.PRODUCER, "target_packages": 145, "plan": None,
            "guest_lock_sha256": "ea8a93155131f15d460f6b3c147063e672e718d027366ab6c53c4264066a8883",
            "maintainer_scripts_executed": False, "package_installation_executed": False,
        },
    })


def observed_diagnostic():
    row = json.loads(historical())
    row.update(
        schema="nib.apt-container-run/v2", git_sha=recorder.DIAGNOSTIC_PRODUCER,
        started_at="2026-10-20T20:27:45Z", finished_at="2026-10-20T20:27:51Z",
        image_archive_sha256="b79cf4852ee74fa6236e64f8a7a4d51c154ae63a2522dfa520c9b278fbfbcd38",
        config_sha256="50f49a4c9d057635183cac413fe709590d0624234ef9c8df953d189d621cd8f4",
        layer_sha256="169da5519e85f73a281dc4a00ae8a72ada76f6e20f45466bf0342daacf3f133f",
    )
    row["simulation"].update(
        schema="nib.apt-simulation/v2", git_sha=recorder.DIAGNOSTIC_PRODUCER,
        diagnostic={
            "exit_code": 0, "plan": None, "reason": "stderr-missing-preferences-directory",
            "stderr_bytes": 92, "stdout_bytes": 19639,
            "stderr_kinds": {"error": 0, "notice": 0, "other": 0, "warning": 1},
        },
    )
    return recorder.base.encoded(row)


def result(output=None, *, status=0, error=b""):
    return subprocess.CompletedProcess(["private-never-published"], status,
                                       fixtures.plan() if output is None else output, error)


def resolve(last):
    execute = mock.Mock(side_effect=[result(b"apt 2.8.3 (amd64)\n"), result(b"local\n"), last])
    return sim.simulate(fixtures.inputs(), execute, setup_work=False)


class DiagnosticTests(unittest.TestCase):
    def validate(self, row):
        receipt = {"git_sha": "a" * 40, "source_hashes": {runner.prep.FILES[-1]: "b" * 64}}
        guest = {"packages": [{"Package": "fixture", "Version": "1.0"}]}
        runner.validate_result(row, receipt, guest)

    def test_actual_resolve_stage_now_distinguishes_three_prior_ambiguities(self):
        values = [(result(status=100, error=("E: " + SECRET).encode()), "process-nonzero"),
                  (result(error=("W: " + SECRET).encode()), "stderr-nonempty"),
                  (result(b"Inst " + SECRET.encode()), "plan-record-syntax")]
        for value, reason in values:
            row = resolve(value)
            self.assertEqual((row["result"], row["stage"]), ("fail", "resolve"))
            self.assertEqual(row["diagnostic"]["reason"], reason)
            self.validate(row)
            self.assertNotIn(SECRET, json.dumps(row))
            self.assertNotIn("private-never-published", json.dumps(row))
        failure = resolve(values[0][0])["diagnostic"]
        self.assertEqual(failure["exit_code"], 100)
        self.assertEqual(failure["stderr_kinds"], {"error": 1, "warning": 0, "notice": 0, "other": 0})

    def test_exact_missing_preferences_warning_is_identified_but_still_rejected(self):
        self.assertEqual(sim.MISSING_PREFERENCES_STDERR, MISSING_PREFERENCES_STDERR)
        row = resolve(result(error=MISSING_PREFERENCES_STDERR))
        self.assertEqual((row["result"], row["stage"], row["plan"]), ("fail", "resolve", None))
        self.assertEqual(row["diagnostic"], {
            "reason": "stderr-missing-preferences-directory", "exit_code": 0,
            "stdout_bytes": len(fixtures.plan()), "stderr_bytes": len(MISSING_PREFERENCES_STDERR),
            "stderr_kinds": {"error": 0, "warning": 1, "notice": 0, "other": 0}, "plan": None})
        self.assertNotIn("/work/etc/", json.dumps(row))
        self.assertNotIn("Unable to read", json.dumps(row))
        self.validate(row)
        self.assertEqual(resolve(result())["result"], "pass")

    def test_missing_preferences_near_matches_and_multiple_warnings_stay_generic(self):
        raw = MISSING_PREFERENCES_STDERR
        variants = [raw[:i] + bytes([raw[i] ^ 1]) + raw[i + 1:] for i in range(len(raw))]
        variants += [raw[:-1], raw + b"\n", raw.replace(b"\n", b"\r\n"), raw * 2,
                     b" " + raw, raw.replace(b".d/", b".d"), raw.replace(b"(2:", b"(13:"),
                     raw.replace(b"/work/etc", SECRET.encode()), SECRET.encode() + raw,
                     raw + ("W: " + SECRET + "\n").encode(), b"\x1b[33m" + raw]
        for index, value in enumerate(variants):
            with self.subTest(index=index):
                row = resolve(result(error=value))
                self.assertEqual(row["diagnostic"]["reason"], "stderr-nonempty")
                self.assertEqual(row["result"], "fail")
                self.assertNotIn(SECRET, json.dumps(row))
                self.assertNotIn("Unable to read", json.dumps(row))
                self.validate(row)

    def test_missing_preferences_match_requires_resolve_exit_zero_before_deadline(self):
        for status in (-9, 1, 100):
            row = resolve(result(status=status, error=MISSING_PREFERENCES_STDERR))
            self.assertEqual(row["diagnostic"]["reason"], "process-nonzero")
            self.validate(row)
        for prefix, stage in (([], "apt-version"),
                              ([result(b"apt 2.8.3 (amd64)\n")], "local-index")):
            execute = mock.Mock(side_effect=prefix + [result(error=MISSING_PREFERENCES_STDERR)])
            row = sim.simulate(fixtures.inputs(), execute, setup_work=False)
            self.assertEqual((row["stage"], row["diagnostic"]["reason"]), (stage, "stderr-nonempty"))
            self.validate(row)
        with mock.patch.object(sim.time, "monotonic", side_effect=[1024.] * 6 + [1144.]):
            row = resolve(result(error=MISSING_PREFERENCES_STDERR))
        self.assertEqual(row["diagnostic"]["reason"], "deadline")
        self.validate(row)

    def test_host_requires_exact_missing_preferences_diagnostic_shape(self):
        original = resolve(result(error=MISSING_PREFERENCES_STDERR))
        for stage in ("setup", "apt-version", "local-index", "complete"):
            row = copy.deepcopy(original)
            row["stage"] = stage
            with self.subTest(stage=stage), self.assertRaises(ValueError):
                self.validate(row)
        changes = [("exit_code", None), ("exit_code", 1), ("exit_code", False),
                   ("stderr_bytes", len(MISSING_PREFERENCES_STDERR) - 1),
                   ("stderr_bytes", len(MISSING_PREFERENCES_STDERR) + 1),
                   ("stderr_bytes", sim.MAX_STDERR + 1), ("stdout_bytes", sim.MAX_OUTPUT + 1),
                   ("plan", {}),
                   ("stderr_kinds", {"error": 0, "warning": 2, "notice": 0, "other": 0}),
                   ("stderr_kinds", {"error": 0, "warning": True, "notice": 0, "other": 0}),
                   ("stderr_kinds", {"error": 1, "warning": 1, "notice": 0, "other": 0}),
                   ("stderr_kinds", {"error": 0, "warning": 1, "notice": 1, "other": 0}),
                   ("stderr_kinds", {"error": 0, "warning": 1, "notice": 0, "other": 1})]
        for key, value in changes:
            row = copy.deepcopy(original)
            row["diagnostic"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.validate(row)

    def test_timeout_overflow_os_unknown_errors_do_not_publish_exception_data(self):
        errors = [
            (subprocess.TimeoutExpired(SECRET, 1, output=SECRET.encode(), stderr=SECRET.encode()),
             "process-timeout"),
            (ValueError("tool output limit exceeded"), "output-limit"),
            (ValueError(SECRET), "collector-error"),
            (subprocess.CalledProcessError(100, SECRET, SECRET.encode()), "collector-error"),
            (OSError(SECRET), "process-os-error"),
        ]
        for error, reason in errors:
            row = resolve(error)
            self.assertEqual(row["diagnostic"]["reason"], reason)
            self.assertIsNone(row["diagnostic"]["exit_code"])
            self.assertIsNone(row["diagnostic"]["stdout_bytes"])
            self.validate(row)
            self.assertNotIn(SECRET, json.dumps(row))

    def test_all_plan_rejections_preserve_gate_and_known_ids_only(self):
        variants = [
            (b"\xff", "plan-utf8"), (b"\x00", "plan-control-character"),
            (b"x" * 8193, "plan-line-limit"),
            (fixtures.plan() + b"Remv private\n", "plan-forbidden-operation"),
            (fixtures.plan(SECRET.lower().replace("_", "-")), "plan-package-unexpected"),
            (fixtures.plan(version=SECRET), "plan-version-mismatch"),
            (fixtures.plan() * 2, "plan-duplicate"),
            (fixtures.plan().replace(b")\nConf", b") [private]\nConf"), "plan-pending-rejected"),
            (b"", "plan-coverage"),
        ]
        for raw, reason in variants:
            with self.subTest(reason=reason):
                row = resolve(result(raw))
                self.assertEqual(row["diagnostic"]["reason"], reason)
                self.validate(row)
                self.assertNotIn(SECRET, json.dumps(row))
                self.assertNotIn(SECRET.lower(), json.dumps(row))
                self.assertNotIn(SECRET.lower().replace("_", "-"), json.dumps(row))
                self.assertIsNone(row["plan"])

    def test_coverage_counts_complete_output_ids_capped_and_sorted(self):
        targets = {"pkg" + str(n): "1.0" for n in range(40)}
        with self.assertRaises(sim.Rejected) as caught:
            sim.validate_plan(b"", targets)
        plan = caught.exception.plan
        self.assertEqual(plan["missing_installed_count"], 40)
        self.assertEqual(plan["missing_installed"], sorted(targets)[:16])
        self.assertEqual(plan["missing_configured"], sorted(targets)[:16])
        self.assertEqual(plan["installed"], 0)

    def test_validation_rejects_private_ids_arbitrary_fields_and_count_inconsistency(self):
        original = resolve(result(b""))
        for key, value in (("reason", SECRET), ("exit_code", True), ("stdout_bytes", -1),
                           ("stderr_bytes", 65537), ("plan", None)):
            row = copy.deepcopy(original)
            row["diagnostic"][key] = value
            with self.subTest(key=key), self.assertRaises((ValueError, TypeError)):
                self.validate(row)
        for key, value in (("subject", SECRET), ("missing_installed", [SECRET]),
                           ("missing_installed_count", 0), ("installed", True)):
            row = copy.deepcopy(original)
            row["diagnostic"]["plan"][key] = value
            with self.subTest(key=key), self.assertRaises((ValueError, TypeError)):
                self.validate(row)
        row = copy.deepcopy(original)
        row["diagnostic"]["arbitrary"] = SECRET
        with self.assertRaises(ValueError):
            self.validate(row)
        row = resolve(result())
        row["diagnostic"] = original["diagnostic"]
        with self.assertRaises(ValueError):
            self.validate(row)

    def test_shared_deadline_not_reset_and_environment_commands_unchanged(self):
        execute = mock.Mock(return_value=result(b"apt 2.8.3 (amd64)\n"))
        with mock.patch.object(sim.time, "monotonic", side_effect=[1024., 1024., 1144.]):
            row = sim.simulate(fixtures.inputs(), execute, setup_work=False)
        self.assertEqual(row["diagnostic"]["reason"], "deadline")
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(execute.call_args.kwargs["env"], sim.ENV)
        self.assertEqual(execute.call_args.kwargs["max_output_bytes"], 2 << 20)
        self.assertEqual(execute.call_args.kwargs["timeout"], 120)
        self.validate(row)
        self.assertIsNone(resolve(result())["diagnostic"])

    def test_setup_and_version_failures_are_separate(self):
        with mock.patch.object(sim, "setup", side_effect=OSError(SECRET)):
            row = sim.simulate(fixtures.inputs(), mock.Mock())
        self.assertEqual(row["diagnostic"]["reason"], "setup-error")
        self.validate(row)
        row = sim.simulate(fixtures.inputs(), mock.Mock(return_value=result(b"not apt")), setup_work=False)
        self.assertEqual(row["diagnostic"]["reason"], "apt-version-mismatch")
        self.validate(row)

    def test_original_plan_acceptance_unchanged_against_committed_producer(self):
        value = recorder.base.processes.execute_process_group(
            ["git", "-C", str(ROOT), "show", recorder.PRODUCER + ":scripts/guest_apt_simulation.py"],
            env=recorder.base.GIT_ENV, timeout=15, max_output_bytes=65536,
            stdin=subprocess.DEVNULL)
        value.check_returncode()
        original = types.ModuleType("historical_apt_contract")
        exec(compile(value.stdout, "historical_apt_contract", "exec", dont_inherit=True),
             original.__dict__)
        samples = [b"", fixtures.plan(), fixtures.plan() * 2, b"\xff", b"\r",
                   b"x" * 8193, fixtures.plan("extra"), fixtures.plan(version="2.0")]
        for prefix in (b"", b"Inst ", b"Conf ", b"Remv ", b"Purg ", b"E:", b"W:", b"Err:"):
            for suffix in (b"", b" []", b" [fixture:amd64 ]", b" [other]", b" [fixture fixture]"):
                samples.append(prefix + fixtures.plan().replace(b")\n", b")" + suffix + b"\n"))
        for raw in samples:
            accepted = []
            for contract in (original, sim):
                try:
                    accepted.append(contract.validate_plan(raw, fixtures.inputs()["targets"]))
                except (ValueError, UnicodeError):
                    accepted.append(None)
            self.assertEqual(accepted[0], accepted[1])


class ImportTests(unittest.TestCase):
    def test_committed_diagnostic_artifact_is_original_and_not_a_success(self):
        path = ROOT / "evidence/p3/2026-10-20-apt-missing-preferences/output.log"
        self.assertEqual(path.read_bytes(), observed_diagnostic())
        run = json.loads(path.with_name("run.json").read_bytes())
        self.assertEqual((run["git_sha"], run["result"], run["execution"]),
                         (recorder.DIAGNOSTIC_PRODUCER, "fail", "software"))
        report = json.loads(path.read_bytes())
        runner.validate_result(report["simulation"], {
            "git_sha": recorder.DIAGNOSTIC_PRODUCER,
            "source_hashes": {runner.prep.FILES[-1]: report["simulation"]["guest_lock_sha256"]}},
            json.loads((ROOT / runner.prep.FILES[-1]).read_bytes()))
        self.assertEqual(report["cleanup"], "confirmed")
        self.assertFalse(report["simulation"]["package_installation_executed"])
        self.assertFalse(report["simulation"]["maintainer_scripts_executed"])

    def test_exact_observed_diagnostic_preserves_failure_and_provenance(self):
        raw = observed_diagnostic()
        self.assertEqual(recorder.base.digest(raw), recorder.DIAGNOSTIC_SHA256)
        files = recorder.evidence(raw, "a" * 40, {recorder.SCRIPT: b"fixture"})
        self.assertEqual(files["output.log"], raw)
        run = json.loads(files["run.json"])
        self.assertEqual(run["git_sha"], recorder.DIAGNOSTIC_PRODUCER)
        self.assertEqual(run["started_utc"], "2026-10-20T20:27:45Z")
        self.assertEqual(run["ended_utc"], "2026-10-20T20:27:51Z")
        self.assertEqual((run["result"], run["execution"]), ("fail", "software"))
        self.assertFalse(run["hardware_validated"])
        self.assertIn("strict stderr gate", run["notes"])
        self.assertIn("not a reconstruction", run["notes"])
        self.assertIn(recorder.DIAGNOSTIC_SHA256, run["notes"])
        for changed in (raw + b"\n", raw.replace(b'"exit_code": 0', b'"exit_code": 1'),
                        raw.replace(b'"fail"', b'"pass"'),
                        raw.replace(b'"confirmed"', b'"unconfirmed"'),
                        raw.replace(b"92", b"93")):
            with self.assertRaises(ValueError):
                recorder.evidence(changed, "a" * 40, {})

    def test_committed_historical_artifact_with_original_producer_validator(self):
        raw = HISTORICAL.read_bytes()
        self.assertEqual(raw, historical())
        self.assertEqual(recorder.base.digest(raw), recorder.REPORT_SHA256)
        record = json.loads(HISTORICAL.with_name("run.json").read_bytes())
        self.assertEqual((record["git_sha"], record["result"], record["execution"]),
                         (recorder.PRODUCER, "fail", "software"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for name in runner.prep.FILES:
                value = recorder.base.processes.execute_process_group(
                    ["git", "-C", str(ROOT), "show", recorder.PRODUCER + ":" + name],
                    env=recorder.base.GIT_ENV, timeout=15, max_output_bytes=2 << 20,
                    stdin=subprocess.DEVNULL)
                value.check_returncode()
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(value.stdout)
            original = types.ModuleType("original_apt_host")
            original.__file__ = str(root / "host/simulate-guest-apt.py")
            exec(compile(Path(original.__file__).read_bytes(), original.__file__, "exec",
                         dont_inherit=True), original.__dict__)
            report = json.loads(raw)
            guest_raw = (root / original.prep.FILES[-1]).read_bytes()
            original.validate_result(report["simulation"], {
                "git_sha": recorder.PRODUCER,
                "source_hashes": {original.prep.FILES[-1]: recorder.base.digest(guest_raw)}},
                json.loads(guest_raw))
        self.assertIsNone(report["simulation"]["plan"])
        self.assertEqual(report["simulation"]["stage"], "resolve")

    def test_committed_importer_and_helpers_no_ambient_git_or_untracked_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for name in (*recorder.base.SOURCE_FILES, recorder.SCRIPT):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((ROOT / name).read_bytes())
            env = {**recorder.base.GIT_ENV, "GIT_AUTHOR_NAME": "Fixture",
                   "GIT_AUTHOR_EMAIL": "fixture@example.invalid", "GIT_COMMITTER_NAME": "Fixture",
                   "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
            def git(*args):
                return subprocess.run(["git", "-C", str(root), *args], env=env, check=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15).stdout
            git("init", "-q")
            git("add", ".")
            git("-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
            with mock.patch.object(recorder, "ROOT", root), mock.patch.object(recorder.base, "ROOT", root), \
                    mock.patch.object(recorder.base.processes, "execute_process_group",
                                      wraps=recorder.base.processes.execute_process_group) as execute:
                revision, sources = recorder.committed()
                self.assertEqual(revision, git("rev-parse", "HEAD").decode().strip())
                self.assertEqual(sources[recorder.SCRIPT], recorder.BOOTSTRAP)
                for call in execute.call_args_list:
                    self.assertEqual(call.kwargs["env"]["GIT_NO_REPLACE_OBJECTS"], "1")
                    self.assertEqual(call.kwargs["env"]["GIT_NO_LAZY_FETCH"], "1")
                    self.assertEqual(call.kwargs["env"]["GIT_ALLOW_PROTOCOL"], "")
                    self.assertEqual(call.kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
                (root / recorder.SCRIPT).write_bytes(b"uncommitted")
                with self.assertRaises(ValueError):
                    recorder.committed()

    def test_bad_cli_does_not_echo_arbitrary_arguments(self):
        value = subprocess.run([sys.executable, "-I", str(ROOT / recorder.SCRIPT),
                                "--report", SECRET, "--output", SECRET, "--unknown=" + SECRET],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        self.assertEqual(value.returncode, 2)
        self.assertNotIn(SECRET.encode(), value.stdout + value.stderr)

    def test_reject_unpinned_report_before_publication(self):
        for raw in (b"{}", b'{"arbitrary":"' + SECRET.encode() + b'"}'):
            with self.assertRaises(ValueError):
                recorder.evidence(raw, "a" * 40, {})

    def test_exact_historical_report_schema_and_byte_identity(self):
        raw = historical()
        self.assertEqual(recorder.base.digest(raw), recorder.REPORT_SHA256)
        files = recorder.evidence(raw, "a" * 40, {recorder.SCRIPT: b"fixture"})
        self.assertEqual(files["output.log"], raw)
        run = json.loads(files["run.json"])
        self.assertEqual(run["git_sha"], recorder.PRODUCER)
        self.assertEqual(run["result"], "fail")
        self.assertEqual(run["execution"], "software")
        self.assertFalse(run["hardware_validated"])
        self.assertEqual(run["started_utc"], "2026-10-20T18:52:06Z")
        self.assertEqual(files, recorder.evidence(raw, "a" * 40, {recorder.SCRIPT: b"fixture"}))
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary).resolve() / "evidence"
            recorder.base.publish(destination, files)
            recorder.base.verify(destination, files)
            with self.assertRaises(FileExistsError):
                recorder.base.publish(destination, files)
        with self.assertRaises(ValueError):
            recorder.evidence(raw + b"\n", "a" * 40, {})

    def test_no_follow_report_reader(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "private").write_bytes(b"private")
            (root / "link").symlink_to(root / "private")
            os.mkfifo(root / "fifo")
            for name in ("link", "fifo"):
                with self.assertRaises((ValueError, OSError)):
                    recorder.base.read(root / name, 16384)


if __name__ == "__main__":
    unittest.main()
