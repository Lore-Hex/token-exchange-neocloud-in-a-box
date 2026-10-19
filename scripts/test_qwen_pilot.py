# SPDX-License-Identifier: Apache-2.0
import hashlib
import importlib.util
import os
from pathlib import Path
import tarfile
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("qwen_pilot", Path(__file__).parent / "pilot/run.py")
pilot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pilot)
RECORDER_SPEC = importlib.util.spec_from_file_location("qwen_recorder", Path(__file__).parent / "record-qwen-pilot.py")
recorder = importlib.util.module_from_spec(RECORDER_SPEC)
RECORDER_SPEC.loader.exec_module(recorder)


class QwenPilotTests(unittest.TestCase):
    def test_hash_verified_private_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.write_bytes(b"weights")
            pilot.checked_copy(source, root / "copy", hashlib.sha256(b"weights").hexdigest(), 7)
            source.write_bytes(b"changed")
            self.assertEqual((root / "copy").read_bytes(), b"weights")
            with self.assertRaises(pilot.PilotError):
                pilot.checked_copy(source, root / "wrong", "0" * 64, 7)
            with self.assertRaises(pilot.PilotError):
                pilot.checked_copy(source, root / "large", "0" * 64, 1)

    def test_links_and_pipes_rejected_without_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "file").write_bytes(b"x")
            (root / "link").symlink_to(root / "file")
            with self.assertRaises(OSError):
                pilot.checked_copy(root / "link", root / "out1", "0" * 64, 1)
            os.mkfifo(root / "fifo")
            with self.assertRaises(pilot.PilotError):
                pilot.checked_copy(root / "fifo", root / "out2", "0" * 64, 1)

    def test_archive_traversal_and_links_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive.tar"
            for index, (name, kind) in enumerate((("../escape", tarfile.REGTYPE),
                                                ("root/link", tarfile.SYMTYPE))):
                with tarfile.open(archive, "w") as output:
                    item = tarfile.TarInfo(name)
                    item.type = kind
                    item.linkname = "../../escape" if kind == tarfile.SYMTYPE else ""
                    output.addfile(item)
                with self.assertRaises((pilot.PilotError, tarfile.TarError)):
                    pilot.unpack(archive, root / str(index))
                self.assertFalse((root / "escape").exists())

    def test_no_credentials_or_build_overrides_in_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            env = pilot.controlled_env(Path(directory))
            for name in ("GOOS", "GOTAGS", "MAKEFLAGS", "CFLAGS", "CXXFLAGS", "CMAKE_TOOLCHAIN_FILE",
                         "AWS_SECRET_ACCESS_KEY", "GOOGLE_APPLICATION_CREDENTIALS", "HF_TOKEN",
                         "TR_PROVIDER_API_KEY", "LLAMA_API_KEY", "TRCS_LISTEN"):
                self.assertNotIn(name, env)

    def test_only_loopback_and_no_confidential_claim(self):
        args = pilot.engine_command("server", "weights", "qwen/qwen3-0.6b", 1234, "key")
        self.assertEqual(args[args.index("--host") + 1], "127.0.0.1")
        self.assertEqual(args[args.index("--device") + 1], "none")
        for flag in ("--offline", "--log-disable", "--no-webui", "--no-slots", "--api-key-file"):
            self.assertIn(flag, args)
        args = pilot.shim_command("trcs", 1234, 1235, "cert", "key")
        self.assertEqual(args[args.index("--tee") + 1], "none")
        self.assertEqual(args[args.index("--evidence-source") + 1], "none")
        self.assertNotIn("--allow-remote-upstream", args)
        self.assertEqual(args[args.index("--listen") + 1], "127.0.0.1:1234")

    def test_required_conformance_never_silently_skipped(self):
        report = {"suite_version": "0.1.8", "target": {"authentication": {"authorization_header_sent": True}},
                  "checks": [{"id": name, "status": "pass"} for name in pilot.REQUIRED]}
        self.assertEqual(pilot.provider_result(report, 0)["result"], "pass")
        self.assertEqual(pilot.provider_result(report, 1)["result"], "fail")
        for value in ("skip", "fail", "warn"):
            report["checks"][0]["status"] = value
            self.assertEqual(pilot.provider_result(report, 0)["result"], "fail")
        report["checks"][0]["status"] = "private-content"
        with self.assertRaises(pilot.PilotError):
            pilot.provider_result(report, 0)

    def test_timeout_stops_child_process_group(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pid_file = root / "pid"
            code = "import os,time; open('pid','w').write(str(os.getpid())); time.sleep(60)"
            with self.assertRaises(subprocess.TimeoutExpired):
                recorder.execute_pilot([sys.executable, "-c", code], env=pilot.controlled_env(root / "env"),
                                       cwd=root, timeout=1)
            pid = int(pid_file.read_text())
            with self.assertRaises(ProcessLookupError):
                os.killpg(pid, 0)

    def test_evidence_uses_authoritative_schema_before_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(recorder.subprocess, "run") as run:
                run.side_effect = subprocess.CalledProcessError(1, ["nib", "lint"])
                with self.assertRaises(subprocess.CalledProcessError):
                    recorder.validate_record({"schema": "invalid"}, "/trusted/nib", root, {})
                self.assertEqual(run.call_args.args[0],
                                 ["/trusted/nib", "lint", str(root / "evidence-validation")])
                self.assertTrue(run.call_args.kwargs["check"])


if __name__ == "__main__":
    unittest.main()
