# SPDX-License-Identifier: Apache-2.0
"""Synthetic inventory recording only: no real device/proc/network calls."""

import datetime
import contextlib
import io
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


def run_fixture(root, capture=None, failed=False, *, destination=None):
    times = iter((START, END, END))
    return recorder.record(root, REVISION, SOURCE, now=lambda: next(times),
                           capture=capture or (lambda *a, **k: output(inventory(failed))), output=destination)


class ProbeEvidenceTests(unittest.TestCase):
    def test_default_receipts_replay_original_producer_bytes(self):
        revision = "ef4a2167c357609a4254f9de86626a88ee456294"
        raw = recorder.git(ROOT, "show", revision + ":" + recorder.RECORDER)
        self.assertEqual(recorder.digest(raw), "77858e523ae744bc585b28aa68cc759afbdc542dd972466185f52e3a699c40e2")
        legacy = types.ModuleType("legacy_probe_recorder")
        legacy.__file__ = str(path)
        exec(compile(raw, str(path), "exec"), legacy.__dict__)
        for failed in (False, True):
            with self.subTest(failed=failed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                previous, current = root / "previous", root / "current"
                previous.mkdir(mode=0o700)
                current.mkdir(mode=0o700)
                times = iter((START, END, END))
                old_name, old_code = legacy.record(previous, REVISION, SOURCE, now=lambda: next(times),
                    capture=lambda *a, **k: output(inventory(failed)))
                name, code = run_fixture(current, failed=failed)
                self.assertEqual((name, code), (old_name, old_code))
                for filename in ("run.json", "output.log"):
                    self.assertEqual((previous / name / filename).read_bytes(),
                                     (current / name / filename).read_bytes())

    def test_external_output_keeps_committed_source_clean_and_paths_private(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory).resolve()
            root, destination = parent / "source", parent / CANARY
            root.mkdir(mode=0o700)
            recorder.git(root, "init", "--quiet")
            for name, raw in SOURCE.items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
            recorder.git(root, "add", ".")
            recorder.git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                         "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
            revision, source = recorder.sources(root)
            times = iter((START, END, END))
            actual_record = recorder.record
            def invoke(*args, **kwargs):
                self.assertEqual(args, (root, revision, source))
                self.assertEqual(kwargs, {"output": str(destination)})
                return actual_record(*args, **kwargs, now=lambda: next(times), capture=lambda *a, **k: output())
            console = io.StringIO()
            with patch.object(recorder, "ROOT", root), patch.object(recorder.sys, "platform", "linux"), \
                    patch.object(recorder, "record", side_effect=invoke), contextlib.redirect_stdout(console):
                code = recorder.main(["--output", str(destination)])
            self.assertEqual(code, 0)
            self.assertEqual(console.getvalue(), "external-output\n")
            self.assertEqual(recorder.git(root, "status", "--porcelain", "--untracked-files=normal"), b"")
            self.assertFalse((root / "evidence").exists())
            self.assertEqual({item.name for item in destination.iterdir()}, {"run.json", "output.log"})
            self.assertEqual(destination.stat().st_mode & 0o777, 0o700)
            for name in ("run.json", "output.log"):
                data = (destination / name).read_text()
                self.assertNotIn(CANARY, data)
                self.assertNotIn(str(parent), data)
                self.assertEqual((destination / name).stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads((destination / "run.json").read_bytes())["command"],
                             "python3 -I host/record-probe.py --output <private-directory>")

    def test_external_failed_probe_retained_and_no_overwrite_or_reexecution(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory).resolve()
            root, target = parent / "source", parent / "result"
            root.mkdir(mode=0o700)
            marker, code = run_fixture(root, failed=True, destination=target)
            self.assertEqual((marker, code), ("external-output", 1))
            before = {item.name: item.read_bytes() for item in target.iterdir()}
            self.assertEqual(json.loads(before["output.log"])["inventory"]["errors"], ["gpus-unavailable"])
            with self.assertRaises((OSError, ValueError)):
                run_fixture(root, destination=target,
                            capture=lambda *a, **k: self.fail("must reject before execution"))
            self.assertEqual(before, {item.name: item.read_bytes() for item in target.iterdir()})

    def test_external_output_rejects_overlap_links_existing_and_unsafe_parents(self):
        kinds = ("source", "inside", "ancestor", "relative", "dotdot", "empty", "double-slash", "double-root",
                 "existing", "file", "leaf-link", "parent-link", "ancestor-link", "parent-file",
                 "public-parent", "writable-parent", "writable-ancestor", "sticky-ancestor", "wrong-owner")
        for kind in kinds:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                base = Path(directory).resolve()
                root, parent = base / "source", base / "private"
                root.mkdir(mode=0o700)
                parent.mkdir(mode=0o700)
                target = parent / "result"
                if kind in ("source", "inside", "ancestor", "relative", "dotdot", "empty", "double-slash", "double-root"):
                    target = {"source": root, "inside": root / "result", "ancestor": base,
                              "relative": "result", "dotdot": str(parent) + "/../result", "empty": "",
                              "double-slash": str(parent) + "//result",
                              "double-root": "/" + str(root / "result")}[kind]
                elif kind == "existing":
                    target.mkdir(mode=0o700)
                elif kind == "file":
                    target.write_text(CANARY)
                elif kind == "leaf-link":
                    target.symlink_to(root)
                elif kind in ("parent-link", "ancestor-link"):
                    link = base / "link"
                    link.symlink_to(parent)
                    if kind == "ancestor-link":
                        (parent / "nested").mkdir(mode=0o700)
                        target = link / "nested/result"
                    else:
                        target = link / "result"
                elif kind == "parent-file":
                    parent.rmdir()
                    parent.write_text(CANARY)
                elif kind in ("public-parent", "writable-parent"):
                    parent.chmod(0o755 if kind == "public-parent" else 0o777)
                elif kind in ("writable-ancestor", "sticky-ancestor"):
                    base.chmod(0o777 if kind == "writable-ancestor" else 0o1777)
                with contextlib.ExitStack() as stack:
                    if kind == "wrong-owner":
                        stack.enter_context(patch.object(recorder.os, "getuid", return_value=os.getuid() + 1))
                    with self.assertRaises((OSError, ValueError)):
                        run_fixture(root, destination=target,
                                    capture=lambda *a, **k: self.fail("must reject before execution"))
                self.assertEqual(list(root.iterdir()), [])

    def test_external_cli_rejects_unknown_or_repeated_arguments_without_path_output(self):
        cases = (["--output"], ["--output", CANARY, "--output", CANARY], ["--out", CANARY], [CANARY])
        for arguments in cases:
            with self.subTest(arguments=arguments), patch.object(recorder.sys, "platform", "linux"), \
                    patch.object(recorder, "sources") as sources, self.assertRaises(ValueError) as raised:
                recorder.main(arguments)
            sources.assert_not_called()
            self.assertNotIn(CANARY, str(raised.exception))

    def test_external_output_also_rejects_opened_source_ancestor_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            # Model an alternate spelling whose lexical overlap was missed;
            # retained directory identities must still prevent source writes.
            with patch.object(recorder.Path, "is_relative_to", return_value=False), \
                    self.assertRaises(ValueError):
                run_fixture(root, destination=root / "result",
                            capture=lambda *a, **k: self.fail("must reject before execution"))
            self.assertEqual(list(root.iterdir()), [])

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
