# SPDX-License-Identifier: Apache-2.0
"""Synthetic inventory recording only: no real device/proc/network calls."""

import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "host/record-probe.py"
recorder = types.ModuleType("probe_recorder")
recorder.__file__ = str(path)
exec(compile(path.read_bytes(), str(path), "exec"), recorder.__dict__)
SOURCE = {recorder.RECORDER: path.read_bytes(), recorder.PROBE: (ROOT / recorder.PROBE).read_bytes()}
REVISION = "1" * 40
START = datetime.datetime(2026, 10, 19, tzinfo=datetime.timezone.utc)
END = START + datetime.timedelta(seconds=2)
CANARY = "PRIVATE-HOST-UUID-TOKEN-CANARY"


def inventory(failed=False):
    probe = recorder.load_probe(SOURCE[recorder.PROBE], ROOT / recorder.PROBE)
    def run(args):
        if failed:
            raise ValueError(CANARY)
        return b"NVIDIA B200, 180000, 595.45.04\n" * 8
    def read(path, limit):
        return b"MemTotal: 1400000000 kB\n" if path.name == "meminfo" else b"vendor_id : AuthenticAMD\n"
    report = probe.collect(run, read, lambda p: p.name == "sev-guest")
    report.update(started_utc=START.isoformat(), ended_utc=(START + datetime.timedelta(seconds=1)).isoformat(),
                  script_sha256=recorder.digest(SOURCE[recorder.PROBE]))
    return report


def output(report=None, status=None):
    report = inventory() if report is None else report
    if status is None:
        status = 0 if report["result"] == "pass" else 1
    return json.dumps(report).encode() + recorder.MARKER + str(status).encode() + b"\n"


def run_fixture(root, capture=None, failed=False):
    times = iter((START, END, END))
    return recorder.record(root, REVISION, SOURCE, now=lambda: next(times),
                           capture=capture or (lambda *a, **k: output(inventory(failed))))


class ProbeEvidenceTests(unittest.TestCase):
    def test_pass_is_only_inventory_and_has_no_identifiers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []
            def capture(args, timeout):
                self.assertEqual(timeout, 30)
                self.assertEqual(args[:5], [sys.executable, "-I", "-B", "-c", recorder.LAUNCHER])
                self.assertEqual(Path(args[5]).read_bytes(), SOURCE[recorder.PROBE])
                calls.append(args[5])
                return output()
            with patch.dict(os.environ, {"HOSTNAME": CANARY, "HTTPS_PROXY": CANARY, "SECRET": CANARY}):
                relative, code = run_fixture(root, capture)
            self.assertEqual(code, 0)
            data = (root / relative / "output.log").read_text()
            report = json.loads(data)
            run = json.loads((root / relative / "run.json").read_text())
            self.assertNotIn(CANARY, data + json.dumps(run))
            self.assertNotIn(directory, data + json.dumps(run))
            self.assertEqual(run["execution"], "software")
            self.assertEqual(run["git_sha"], REVISION)
            self.assertEqual(run["phase"], "P2")
            self.assertEqual(run["task"], "T2")
            self.assertFalse(run["hardware_validated"])
            for flag in recorder.FLAGS:
                self.assertIs(report["inventory"][flag], False)
            self.assertEqual(report["source"]["probe_sha256"], recorder.digest(SOURCE[recorder.PROBE]))
            self.assertEqual(report["source"]["recorder_sha256"], recorder.digest(SOURCE[recorder.RECORDER]))
            self.assertFalse(Path(calls[0]).exists())
            self.assertEqual((root / relative).stat().st_mode & 0o777, 0o700)
            for name in ("run.json", "output.log"):
                self.assertEqual((root / relative / name).stat().st_mode & 0o777, 0o600)

    def test_failed_inventory_is_preserved_with_exit_one(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative, code = run_fixture(root, failed=True)
            self.assertEqual(code, 1)
            report = json.loads((root / relative / "output.log").read_text())
            self.assertEqual(report["result"], "fail")
            self.assertEqual(report["inventory"]["errors"], ["gpus-unavailable"])
            self.assertNotIn(CANARY, json.dumps(report))

    def test_timeout_bad_output_and_exception_record_sanitized_failure(self):
        def raises(*a, **k):
            raise subprocess.TimeoutExpired(CANARY, 30, output=CANARY, stderr=CANARY)
        for execute in (raises, lambda *a, **k: CANARY.encode(), lambda *a, **k: b"x" * 65537):
            with self.subTest(execute=execute), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                relative, code = run_fixture(root, execute)
                self.assertEqual(code, 1)
                contents = [(root / relative / name).read_text() for name in ("output.log", "run.json")]
                self.assertNotIn(CANARY, "".join(contents))
                report = json.loads(contents[0])
                self.assertIsNone(report["inventory"])
                self.assertEqual(report["failure_code"], "probe-execution-or-report-failed")

    def test_snapshot_cleanup_error_cannot_preserve_pass(self):
        original = tempfile.TemporaryDirectory
        class CleanupFailure(original):
            def __exit__(self, *args):
                super().__exit__(*args)
                raise OSError(CANARY)
        with original() as directory:
            root = Path(directory)
            with patch.object(recorder.tempfile, "TemporaryDirectory", CleanupFailure):
                relative, code = run_fixture(root)
            self.assertEqual(code, 1)
            for name in ("run.json", "output.log"):
                text = (root / relative / name).read_text()
                self.assertNotIn(CANARY, text)
                self.assertEqual(json.loads(text)["result"], "fail")
            self.assertIsNone(json.loads((root / relative / "output.log").read_text())["inventory"])

    def test_report_tamper_extra_fields_and_claims_rejected(self):
        reports = []
        for name, value in (("hostname", CANARY), ("script_sha256", "0" * 64),
                            ("scope", CANARY), ("result", "fail"), ("eight_b200_reported", 1)):
            report = inventory()
            report[name] = value
            reports.append(report)
        for flag in recorder.FLAGS:
            report = inventory()
            report[flag] = True
            reports.append(report)
        for field, value in (("family", CANARY), ("driver", "192.0.2.1"), ("driver", CANARY),
                             ("memory_mib", True), ("serial", CANARY)):
            report = inventory()
            report["checks"]["gpus"][0][field] = value
            reports.append(report)
        for field, value in (("cpu_vendor", CANARY), ("host_memory_kib", True),
                             ("env", CANARY), ("attestation_interfaces_present", {"uuid": CANARY})):
            report = inventory()
            report["checks"][field] = value
            reports.append(report)
        for report in reports:
            with self.subTest(report=report), self.assertRaises((ValueError, TypeError)):
                recorder.validate_output(output(report), recorder.digest(SOURCE[recorder.PROBE]), START, END)

    def test_exit_ordering_time_and_duplicate_json_rejected(self):
        variants = [output(status=1), output(status=-9), output() + b"extra", output() + recorder.MARKER + b"0\n",
                    output().replace(b'"result": "pass"', b'"result": "fail", "result": "pass"')]
        for time in ("not-time", (START - datetime.timedelta(seconds=1)).isoformat(),
                     "2026-10-19T00:00:00+02:00"):
            report = inventory()
            report["started_utc"] = time
            variants.append(output(report))
        report = inventory()
        report["errors"] = [CANARY]
        variants.append(output(report))
        for data in variants:
            with self.subTest(data=data[:40]), self.assertRaises(ValueError):
                recorder.validate_output(data, recorder.digest(SOURCE[recorder.PROBE]), START, END)

    def test_snapshot_changes_special_files_and_links_fail_without_raw_output(self):
        for kind in ("file", "fifo", "symlink"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                def capture(args, **kwargs):
                    path = Path(args[-1])
                    path.unlink()
                    if kind == "fifo":
                        os.mkfifo(path)
                    elif kind == "symlink":
                        path.symlink_to(root)
                    else:
                        path.write_text(CANARY)
                    return output()
                relative, code = run_fixture(root, capture)
                self.assertEqual(code, 1)
                self.assertNotIn(CANARY, (root / relative / "output.log").read_text())

    def test_existing_evidence_is_never_overwritten_or_reexecuted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative, _ = run_fixture(root)
            before = {p.name: p.read_bytes() for p in (root / relative).iterdir()}
            with self.assertRaises(ValueError):
                run_fixture(root, capture=lambda *a, **k: self.fail("must reject before execution"))
            self.assertEqual(before, {p.name: p.read_bytes() for p in (root / relative).iterdir()})

    def test_unsafe_evidence_directories_rejected(self):
        for kind in ("symlink", "writable"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                if kind == "symlink":
                    (root / "elsewhere").mkdir()
                    (root / "evidence").symlink_to(root / "elsewhere")
                else:
                    (root / "evidence").mkdir()
                    (root / "evidence").chmod(0o777)
                with self.assertRaises((OSError, ValueError)):
                    run_fixture(root, capture=lambda *a, **k: self.fail("must reject before execution"))

    def test_committed_sources_ignore_replacements_working_tree_and_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recorder.git(root, "init", "--quiet")
            for name, data in SOURCE.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            recorder.git(root, "add", ".")
            def commit():
                recorder.git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                             "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
                return recorder.git(root, "rev-parse", "HEAD").decode().strip()
            original = commit()
            (root / recorder.PROBE).write_text("raise Exception('forged')")
            recorder.git(root, "add", ".")
            forged = commit()
            recorder.git(root, "replace", original, forged)
            recorder.git(root, "update-ref", "HEAD", original)
            default = subprocess.run(["git", "-C", str(root), "show", original + ":" + recorder.PROBE],
                env={key: value for key, value in recorder.GIT_ENV.items() if key != "GIT_NO_REPLACE_OBJECTS"},
                check=True, capture_output=True, timeout=10)
            self.assertIn(b"forged", default.stdout)
            (root / "host/ignored.py").write_text(CANARY)
            with patch.dict(os.environ, {"GIT_DIR": CANARY, "GIT_CONFIG_COUNT": "1", "PYTHONPATH": CANARY}):
                revision, actual = recorder.sources(root)
            self.assertEqual(revision, original)
            self.assertEqual(actual, SOURCE)

    def test_uncommitted_recorder_and_nonregular_committed_probe_rejected(self):
        for symlink in (False, True):
            with self.subTest(symlink=symlink), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                recorder.git(root, "init", "--quiet")
                (root / "host").mkdir()
                (root / recorder.RECORDER).write_bytes(SOURCE[recorder.RECORDER] if symlink else b"different")
                if symlink:
                    (root / recorder.PROBE).symlink_to("elsewhere")
                else:
                    (root / recorder.PROBE).write_bytes(SOURCE[recorder.PROBE])
                recorder.git(root, "add", ".")
                recorder.git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                             "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
                with self.assertRaises(ValueError):
                    recorder.sources(root)

    def test_missing_promisor_blob_never_invokes_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recorder.git(root, "init", "--quiet")
            for name, data in SOURCE.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            recorder.git(root, "add", ".")
            recorder.git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                         "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
            oid = recorder.git(root, "rev-parse", "HEAD:" + recorder.PROBE).decode().strip()
            (root / ".git/objects" / oid[:2] / oid[2:]).unlink()
            marker, helper = root / "transport-called", root / "transport.py"
            helper.write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\n"
                              "raise SystemExit(1)\n")
            recorder.git(root, "config", "remote.origin.url", "ext::" + sys.executable + " " + str(helper))
            recorder.git(root, "config", "remote.origin.promisor", "true")
            recorder.git(root, "config", "remote.origin.partialclonefilter", "blob:none")
            recorder.git(root, "config", "protocol.ext.allow", "always")
            # Both independent guards prohibit transport, including older Git
            # versions that ignore GIT_NO_LAZY_FETCH.
            for ignored in (None, "GIT_NO_LAZY_FETCH"):
                env = {key: value for key, value in recorder.GIT_ENV.items() if key != ignored}
                with patch.object(recorder, "GIT_ENV", env), self.assertRaises(ValueError):
                    recorder.sources(root)
                self.assertFalse(marker.exists())
                self.assertFalse((root / "evidence").exists())
            # Local helper only: prove this fixture would attempt a lazy fetch
            # without either guard. It never connects to a remote service.
            env = {key: value for key, value in recorder.GIT_ENV.items()
                   if key not in ("GIT_NO_LAZY_FETCH", "GIT_ALLOW_PROTOCOL")}
            result = subprocess.run(["git", "-C", str(root), "cat-file", "-s", oid],
                                    env=env, capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(marker.exists())

    def test_launcher_preserves_failed_child_report_and_discards_stderr(self):
        probe = recorder.load_probe(SOURCE[recorder.PROBE], ROOT / recorder.PROBE)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.py"
            report = inventory(failed=True)
            path.write_text("import sys\nprint(" + repr(json.dumps(report)) + ")\n"
                            "print(" + repr(CANARY) + ", file=sys.stderr)\nraise SystemExit(1)\n")
            data = probe.capture([sys.executable, "-I", "-B", "-c", recorder.LAUNCHER, str(path)])
            checked = recorder.validate_output(data, recorder.digest(SOURCE[recorder.PROBE]), START, END)
            self.assertEqual(checked["result"], "fail")
            self.assertNotIn(CANARY, data.decode())

    def test_nonisolated_and_nonlinux_cli_fail_before_recording(self):
        result = subprocess.run([sys.executable, str(ROOT / recorder.RECORDER)],
                                capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"python3 -I", result.stderr)
        with patch.object(recorder.sys, "platform", "darwin"), self.assertRaises(ValueError):
            recorder.main()


def schema_fixtures(destination):
    """Called only by the Go schema test; all inventory is synthetic."""
    for name, failed in (("pass", False), ("fail", True)):
        root = destination / name
        root.mkdir()
        relative, _ = run_fixture(root, failed=failed)
        (destination / (name + ".json")).write_bytes((root / relative / "run.json").read_bytes())


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--schema-fixtures":
        schema_fixtures(Path(sys.argv[2]))
    else:
        unittest.main()
