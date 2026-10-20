# SPDX-License-Identifier: Apache-2.0
"""Fake appraisal recording; no real NVIDIA service, NVML or ready transition."""

import copy
import datetime
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
PATH = ROOT / "host/record-gpu-appraisal.py"
tool = types.ModuleType("appraisal_recorder")
tool.__file__ = str(PATH)
exec(compile(PATH.read_bytes(), str(PATH), "exec"), tool.__dict__)
shared, appraiser, _ = tool.helpers(ROOT)
SOURCE = {name: (ROOT / name).read_bytes()
          for name in (tool.RECORDER, tool.APPRAISAL, tool.PROBE, tool.SHARED)}
REVISION = "1" * 40
START = datetime.datetime(2026, 10, 20, tzinfo=datetime.timezone.utc)
CANARY = "PRIVATE_UUID_NONCE_TOKEN_PATH"


def report(*, passed=True, enable=False):
    with mock.patch.object(appraiser.sys, "platform", "unavailable"):
        value = appraiser.worker(False, None)
    value["readiness_requested"] = enable
    value["script_sha256"] = shared.digest(SOURCE[tool.APPRAISAL])
    if passed:
        value.update(result="pass", stage="complete", cli_contract_verified=True,
                     gpu_appraisal_verified=True, nvml_shutdown=True, ready_before=0,
                     ready_after=1 if enable else 0, persistence_enabled_count=8,
                     readiness_transition="enabled-readback-verified" if enable else "unchanged",
                     claims={name: 8 if name == "authenticated_gpu_count" else True
                             for name in tool.CLAIMS})
    return value


def record(root, value=None, *, capture=None, enable=False, now=None):
    raw = json.dumps(report(enable=enable) if value is None else value).encode()
    return tool.record(root, REVISION, SOURCE, shared, appraiser, attest=True, enable=enable,
                       now=now or mock.Mock(side_effect=[START, START + datetime.timedelta(seconds=1)]),
                       capture=capture or mock.Mock(return_value=raw))


class AppraisalRecordingTests(unittest.TestCase):
    def test_content_free_source_bound_success_and_failure(self):
        for passed, enable in ((True, False), (True, True), (False, True)):
            with self.subTest(passed=passed, enable=enable), tempfile.TemporaryDirectory() as temporary:
                root, calls = Path(temporary), []
                value = report(passed=passed, enable=enable)
                def capture(argv, **kwargs):
                    self.assertEqual(argv[:4], [sys.executable, "-I", "-S", "-B"])
                    self.assertEqual(kwargs["timeout"], 285)
                    self.assertEqual(kwargs["limit"], 16384)
                    self.assertNotIn("NVAT_NRAS_BASE_URL", kwargs["env"])
                    self.assertEqual(Path(argv[4]).read_bytes(), SOURCE[tool.APPRAISAL])
                    self.assertIn("--attest", argv)
                    self.assertEqual("--enable-readiness" in argv, enable)
                    calls.append(argv[4])
                    return json.dumps(value).encode()
                with mock.patch.dict(os.environ, {"NVAT_NRAS_BASE_URL": CANARY, "SECRET": CANARY}):
                    relative, code = record(root, capture=capture, enable=enable)
                self.assertEqual(code, 0 if passed else 1)
                self.assertIn("-gpu-appraisal-", relative)
                directory = root / relative
                output = json.loads((directory / "output.log").read_bytes())
                run = json.loads((directory / "run.json").read_bytes())
                self.assertEqual(output["appraisal"], value)
                self.assertEqual(run["git_sha"], REVISION)
                self.assertEqual(run["execution"], "software")
                self.assertFalse(run["hardware_validated"])
                self.assertEqual(run["task"], "T3")
                self.assertEqual(run["command"].endswith("--enable-readiness"), enable)
                self.assertEqual(set(output["source"]["files"]), set(SOURCE))
                self.assertTrue(all(not Path(path).exists() for path in calls))
                for flag in ("runtime_ready", "confidential_qualified", "deployable"):
                    self.assertFalse(output[flag])
                text = json.dumps(output) + json.dumps(run)
                self.assertNotIn(CANARY, text)
                self.assertNotIn(temporary, text)
                self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
                self.assertEqual((directory / "run.json").stat().st_mode & 0o777, 0o600)

    def test_unknown_or_inconsistent_output_fails_without_disclosing_it(self):
        variants = []
        for key, value in (("hostname", CANARY), ("stage", CANARY), ("runtime_ready", True),
                           ("script_sha256", "0" * 64), ("nvml_shutdown", False),
                           ("gpu_appraisal_verified", False), ("ready_after", 0),
                           ("persistence_enabled_count", 7), ("readiness_requested", False)):
            row = report(enable=True)
            row[key] = value
            variants.append(json.dumps(row).encode())
        row = report(enable=True)
        row["claims"]["ueid"] = CANARY
        variants.append(json.dumps(row).encode())
        row = report(passed=False, enable=True)
        row.update(stage="claims-policy",
                   claim_failure={"code": "claim-value", "source": "verified-cli", "field": CANARY})
        variants.append(json.dumps(row).encode())
        variants.extend([CANARY.encode(), b"x" * 16385,
                         b'{"result":"pass","result":"fail"}'])
        for raw in variants:
            with self.subTest(raw=raw[:40]), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                relative, code = record(root, enable=True, capture=mock.Mock(return_value=raw))
                output = json.loads((root / relative / "output.log").read_bytes())
                self.assertEqual(code, 1)
                self.assertIsNone(output["appraisal"])
                self.assertNotIn(CANARY, json.dumps(output))

    def test_fixed_claim_failure_is_recorded_and_never_upgraded(self):
        row = report(passed=False)
        row.update(stage="claims-policy",
                   claim_failure={"code": "claim-value", "source": "verified-cli", "field": "eat_nonce"})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            relative, code = record(root, row)
            output = json.loads((root / relative / "output.log").read_bytes())
            self.assertEqual(code, 1)
            self.assertEqual(output["appraisal"]["claim_failure"], row["claim_failure"])
            self.assertFalse(output["appraisal"]["gpu_appraisal_verified"])

    def test_publication_requires_exact_transition_start_state(self):
        for transition, required_before in (("enabled-readback-verified", 0),
                                             ("already-ready-verified", 1)):
            for before in (None, 0, 1):
                row = report(enable=True)
                row.update(readiness_transition=transition, ready_before=before)
                with self.subTest(transition=transition, before=before), \
                     tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    relative, code = record(root, row, enable=True)
                    run = json.loads((root / relative / "run.json").read_bytes())
                    output = json.loads((root / relative / "output.log").read_bytes())
                    if before == required_before:
                        self.assertEqual(code, 0)
                        self.assertEqual(run["result"], "pass")
                        self.assertEqual(output["appraisal"], row)
                    else:
                        self.assertEqual(code, 1)
                        self.assertEqual(run["result"], "fail")
                        self.assertEqual(output["result"], "fail")
                        self.assertIsNone(output["appraisal"])
                        self.assertEqual(output["failure_code"],
                                         "appraisal-execution-or-report-failed")

    def test_timeout_and_cancellation_preserve_failed_record(self):
        for error in (ValueError(CANARY), KeyboardInterrupt(),
                      subprocess.TimeoutExpired(CANARY, 285, output=CANARY)):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                relative, code = record(root, capture=mock.Mock(side_effect=error))
                self.assertEqual(code, 1)
                output = json.loads((root / relative / "output.log").read_bytes())
                self.assertEqual(output["failure_code"], "appraisal-execution-or-report-failed")
                self.assertNotIn(CANARY, json.dumps(output))

    def test_no_overwrite_prevents_reexecution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first, _ = record(root)
            original = (root / first / "run.json").read_bytes()
            capture = mock.Mock()
            with self.assertRaises(ValueError):
                record(root, capture=capture)
            capture.assert_not_called()
            self.assertEqual((root / first / "run.json").read_bytes(), original)

    def test_source_snapshot_tamper_and_clock_regression_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def capture(argv, **kwargs):
                target = Path(argv[4])
                target.chmod(0o600)
                target.write_bytes(b"changed")
                return json.dumps(report()).encode()
            relative, code = record(root, capture=capture)
            self.assertEqual(code, 1)
            self.assertIsNone(json.loads((root / relative / "output.log").read_bytes())["appraisal"])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            relative, code = record(root, now=mock.Mock(side_effect=[START, START - datetime.timedelta(seconds=1)]))
            self.assertEqual(code, 1)
            run = json.loads((root / relative / "run.json").read_bytes())
            self.assertEqual(run["ended_utc"], run["started_utc"])

    def test_shared_helper_and_self_source_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "host").mkdir()
            for name, data in SOURCE.items():
                (root / name).write_bytes(data)
            env = {**shared.GIT_ENV, "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                   "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
            def git(*args):
                subprocess.run(["git", "-C", str(root), *args], env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
            git("init", "-q")
            git("add", "host")
            git("commit", "-qm", "fixture")
            revision, source, _, _ = tool.sources(root)
            self.assertEqual(source, SOURCE)
            self.assertRegex(revision, r"^[0-9a-f]{40}$")
            # Self bootstrap must match the committed file, not an arbitrary claimed SHA.
            with mock.patch.object(tool, "BOOTSTRAP", b"changed"):
                with self.assertRaises(ValueError):
                    tool.sources(root)
            (root / tool.APPRAISAL).write_bytes(b"raise RuntimeError('PRIVATE')")
            with self.assertRaises(ValueError):
                tool.sources(root)

    def test_shared_git_environment_forbids_replace_and_lazy_fetch(self):
        self.assertEqual(shared.GIT_ENV["GIT_NO_REPLACE_OBJECTS"], "1")
        self.assertEqual(shared.GIT_ENV["GIT_NO_LAZY_FETCH"], "1")
        self.assertEqual(shared.GIT_ENV["GIT_ALLOW_PROTOCOL"], "")
        self.assertEqual(shared.GIT_ENV["GIT_TERMINAL_PROMPT"], "0")

    def test_regular_source_reader_rejects_special_files_without_blocking(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            file = root / "source"
            file.write_bytes(b"source")
            self.assertEqual(tool.source_bytes(file), b"source")
            link = root / "link"
            link.symlink_to(file)
            with self.assertRaises(OSError):
                tool.source_bytes(link)
            link.unlink()
            os.link(file, link)
            with self.assertRaises(ValueError):
                tool.source_bytes(file)
            fifo = root / "fifo"
            os.mkfifo(fifo)
            with self.assertRaises(ValueError):
                tool.source_bytes(fifo)

    def test_authoritative_evidence_schema_for_all_result_types(self):
        # Reuse the existing cache-only, telemetry-disabled authoritative builder.
        path = ROOT / "scripts/record-engine-image.py"
        builder = tool.load(path.read_bytes(), path, "appraisal_schema_builder")
        revision = shared.git(ROOT, "rev-parse", "HEAD").decode().strip()
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary).resolve()
            env = builder.offline_environment(work / "control")
            binary, _ = builder.build_validator(ROOT, revision, work, env)
            for index, value in enumerate((report(), report(passed=False), report(enable=True))):
                root = work / ("case" + str(index))
                root.mkdir(mode=0o700)
                relative, _ = record(root, value, enable=value["readiness_requested"])
                builder.call([str(binary), "lint", str(root)], env, timeout=20)
                run = json.loads((root / relative / "run.json").read_bytes())
                bad = copy.deepcopy(run)
                bad["execution"] = "hardware"  # Missing driver/CUDA must still reject.
                (root / relative / "run.json").write_text(json.dumps(bad))
                with self.assertRaises(subprocess.CalledProcessError):
                    builder.call([str(binary), "lint", str(root)], env, timeout=20)


class RecordedAppraisalEvidenceTests(unittest.TestCase):
    def check_record(self, slug, revision, hashes, *, passed):
        directory = ROOT / "evidence/p2" / slug
        self.assertEqual({path.name for path in directory.iterdir()}, {"run.json", "output.log"})
        raw = {name: (directory / name).read_bytes() for name in hashes}
        for name, digest in hashes.items():
            self.assertEqual(shared.digest(raw[name]), digest)
        run, output = (json.loads(raw[name]) for name in ("run.json", "output.log"))
        self.assertEqual(run["git_sha"], revision)
        self.assertEqual(output["source"]["git_sha"], revision)
        self.assertEqual(run["result"], "pass" if passed else "fail")
        self.assertEqual(output["result"], run["result"])
        self.assertEqual(run["execution"], "software")
        self.assertFalse(run["hardware_validated"])
        self.assertIsNone(run["host"]["driver"])
        self.assertIsNone(run["host"]["cuda"])
        self.assertLess(run["started_utc"], run["ended_utc"])
        self.assertEqual(run["command"], "python3 -I -S -B host/record-gpu-appraisal.py --attest"
                         + (" --enable-readiness" if passed else ""))
        names = {tool.RECORDER, tool.APPRAISAL, tool.PROBE, tool.SHARED}
        self.assertEqual(set(output["source"]["files"]), names)
        source = {name: shared.git(ROOT, "show", revision + ":" + name) for name in names}
        for name, data in source.items():
            self.assertEqual(shared.digest(data), output["source"]["files"][name])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "host").mkdir()
            for name, data in source.items():
                (root / name).write_bytes(data)
            historical = tool.load(source[tool.RECORDER], root / tool.RECORDER,
                                   "recorded_appraisal_" + revision)
            _, original_appraiser, _ = historical.helpers(root)
            checked = historical.validate(json.dumps(output["appraisal"]).encode(),
                                          original_appraiser,
                                          output["source"]["files"][tool.APPRAISAL],
                                          attest=True, enable=passed)
        self.assertEqual(checked, output["appraisal"])
        for flag in ("runtime_ready", "confidential_qualified", "deployable"):
            self.assertFalse(output[flag])
            self.assertFalse(checked[flag])
        self.assertFalse(output["hardware_validated"])
        self.assertEqual(checked["persistence_enabled_count"], 8)
        self.assertTrue(checked["nvml_shutdown"])
        self.assertFalse(checked["installation_completed"])
        return checked

    def test_source_bound_failed_audit_is_preserved(self):
        checked = self.check_record(
            "2026-10-20-gpu-appraisal-120201396352",
            "a23e66b49acf62a8f8547a276f05fd9fec096297",
            {"run.json": "193e7fdb131a0ff8ac25f5607025d93d9cf3b24023ad5e2e3ff9d13e5c4a0eb3",
             "output.log": "e2b7d153a16fc18951e43f88d564991f42597944bb54634d1fd6cbcb2673c187"},
            passed=False)
        self.assertEqual(checked["stage"], "claims-policy")
        self.assertEqual(checked["claim_failure"],
                         {"code": "claim-missing", "source": "signed-token",
                          "field": "x-nvidia-device-type"})
        self.assertIsNone(checked["claims"])
        self.assertFalse(checked["gpu_appraisal_verified"])
        self.assertFalse(checked["readiness_requested"])
        self.assertEqual(checked["ready_before"], 0)
        self.assertIsNone(checked["ready_after"])
        self.assertEqual(checked["readiness_transition"], "unverified")

    def test_source_bound_eight_gpu_appraisal_and_transition_are_not_qualification(self):
        checked = self.check_record(
            "2026-10-20-gpu-appraisal-121447763215",
            "12914b4d7f576e0c0abd66d4915763dbbb68f0b5",
            {"run.json": "675d3ff523b084e513fb52135636e448eaf77a35c28427a58a24ead1eaa88c3b",
             "output.log": "ceab5d04d12014c1f1d441f20d76a985261cf4b4655315ab8d9b16c65517dcc5"},
            passed=True)
        self.assertEqual(checked["stage"], "complete")
        self.assertIsNone(checked["claim_failure"])
        self.assertTrue(checked["gpu_appraisal_verified"])
        self.assertTrue(checked["readiness_requested"])
        self.assertEqual((checked["ready_before"], checked["ready_after"]), (0, 1))
        self.assertEqual(checked["readiness_transition"], "enabled-readback-verified")
        self.assertEqual(checked["claims"]["authenticated_gpu_count"], 8)
        self.assertTrue(all(value is True for name, value in checked["claims"].items()
                            if name != "authenticated_gpu_count"))


if __name__ == "__main__":
    unittest.main()
