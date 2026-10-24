# SPDX-License-Identifier: Apache-2.0
import hashlib
import importlib.util
import io
import json
import py_compile
import os
from pathlib import Path
import tarfile
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location("qwen_pilot", Path(__file__).parent / "pilot/run.py")
pilot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pilot)
RECORDER_SPEC = importlib.util.spec_from_file_location("qwen_recorder", Path(__file__).parent / "record-qwen-pilot.py")
recorder = importlib.util.module_from_spec(RECORDER_SPEC)
RECORDER_SPEC.loader.exec_module(recorder)


class QwenPilotTests(unittest.TestCase):
    def shutdown_stream(self, events, *, content_type="text/event-stream"):
        wire = b"".join((item if isinstance(item, bytes) else
                         b"data: " + json.dumps(item).encode() + b"\n\n") for item in events)
        response = Mock(status=200)
        response.readline.side_effect = io.BytesIO(wire).readline
        response.getheader.return_value = content_type
        connection = Mock()
        connection.getresponse.return_value = response
        child = Mock(returncode=0)
        child.poll.return_value = None
        with patch.object(pilot.http.client, "HTTPSConnection", return_value=connection):
            pilot.shutdown_during_stream(child, 8443, None, "synthetic-key", "synthetic-model")
        child.terminate.assert_called()
        child.wait.assert_called_once_with(timeout=140)
        connection.close.assert_called_once()

    def stream_events(self, reason="stop"):
        return [{"choices": [{"index": 0, "delta": {"content": "synthetic"}}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]},
                {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}},
                b"data: [DONE]\n\n"]

    def test_shutdown_proof_requires_successful_complete_stream(self):
        for reason in ("stop", "length"):
            self.shutdown_stream(self.stream_events(reason))
        for reason in ("error", "content_filter", "tool_calls", "", 0, False, [], {}):
            with self.subTest(reason=reason), self.assertRaises(pilot.PilotError):
                self.shutdown_stream(self.stream_events(reason))

    def test_shutdown_proof_rejects_errors_and_malformed_events(self):
        for event in ({"error": {"message": "synthetic failure"}}, {"error": None},
                      b'data:{"error":{"message":"synthetic failure"}}\n\n',
                      b'data:\t{"error":null}\n\n',
                      b"event: error\n", [], {"choices": {}}, {"choices": [None]},
                      {"choices": [{"index": 0, "delta": "bad"}]},
                      {"usage": []}, {"usage": {"prompt_tokens": True}},
                      {"usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 4}}):
            with self.subTest(event=event), self.assertRaises(pilot.PilotError):
                events = self.stream_events()
                events.insert(1, event)
                self.shutdown_stream(events)
        with self.assertRaises(pilot.PilotError):
            self.shutdown_stream(self.stream_events(), content_type="application/json")

    def test_shutdown_proof_accepts_optional_sse_data_space(self):
        events = [b"data:" + json.dumps(item).encode() + b"\n\n" if isinstance(item, dict)
                  else item.replace(b"data: ", b"data:") for item in self.stream_events()]
        self.shutdown_stream(events)

    def test_shutdown_proof_requires_whole_utf8_frames(self):
        wire = [b"data: " + json.dumps(item).encode() + b"\n\n" if isinstance(item, dict)
                else item for item in self.stream_events()]
        self.shutdown_stream([item.replace(b"\n", b"\r\n") for item in wire])
        self.shutdown_stream([b": keepalive\n\n", *wire])
        bad_streams = [
            [b"".join(wire).replace(b"\n\n", b"\n") + b"\n"],
            [*wire[:-1], b"data: [DONE]\n"],
            [*wire[:-1], b"data: [DONE]\n\n"[:-1]],
            [*wire[:-1], b"data: [DONE]\nevent:error\n\n"],
            [b'\xef\xbb\xbfdata:{"error":null}\n\n', *wire],
            [b': comment\rdata:{"error":null}\r\r\n', *wire],
            [b'data:' + '{"error":null}'.encode("utf-16") + b"\n\n", *wire],
            [b'data:' + '{"choices":[]}'.encode("utf-16-le") + b"\n\n", *wire],
            [b"id: unsupported\n\n", *wire],
        ]
        for events in bad_streams:
            with self.subTest(events=events), self.assertRaises(pilot.PilotError):
                self.shutdown_stream(events)

    def test_shutdown_proof_requires_terminal_unique_usage(self):
        content, finish, usage, done = self.stream_events()
        for events in ([usage, content, finish, done], [content, usage, finish, done],
                       [content, finish, usage, usage, done],
                       [content, finish, usage, {"choices": []}, done]):
            with self.subTest(events=events), self.assertRaises(pilot.PilotError):
                self.shutdown_stream(events)

    def test_shutdown_proof_rejects_missing_terminal_evidence(self):
        for index in range(4):
            with self.subTest(index=index), self.assertRaises(pilot.PilotError):
                events = self.stream_events()
                del events[index]
                self.shutdown_stream(events)
        events = self.stream_events()
        events.insert(2, events[0])
        with self.assertRaisesRegex(pilot.PilotError, "shutdown-content-after-finish"):
            self.shutdown_stream(events)

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
        for option in ("--threads", "--threads-http"):
            self.assertEqual(args.count(option), 1)
            self.assertEqual(args[args.index(option) + 1], "4")
        for flag in ("--offline", "--log-disable", "--no-webui", "--no-slots", "--api-key-file"):
            self.assertIn(flag, args)
        args = pilot.shim_command("trcs", 1234, 1235, "cert", "key")
        self.assertEqual(args[args.index("--tee") + 1], "none")
        self.assertEqual(args[args.index("--evidence-source") + 1], "none")
        self.assertNotIn("--allow-remote-upstream", args)
        self.assertEqual(args[args.index("--listen") + 1], "127.0.0.1:1234")

    def test_engine_http_threads_do_not_scale_with_host_cpu_count(self):
        for count in (1, 16, 240, 1024):
            with self.subTest(count=count), patch.object(os, "cpu_count", return_value=count):
                args = pilot.engine_command("server", "weights", "qwen/qwen3-0.6b", 1234, "key")
                self.assertEqual(args[args.index("--threads-http") + 1], "4")

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

    def test_ignored_bytecode_cannot_override_helper_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "helper.py"
            path.write_text("VALUE = 'forged'\n")
            original = path.stat()
            py_compile.compile(str(path), doraise=True)
            # Same source size and mtime: a normal import trusts the forged pyc.
            path.write_text("VALUE = 'source'\n")
            os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
            normal_spec = importlib.util.spec_from_file_location("normal_helper", path)
            normal = importlib.util.module_from_spec(normal_spec)
            normal_spec.loader.exec_module(normal)
            self.assertEqual(normal.VALUE, "forged")
            loaded, source = recorder.load_source("safe_helper", path)
            self.assertEqual(loaded.VALUE, "source")
            self.assertEqual(source, path.read_bytes())

    def test_bootstrap_must_match_committed_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts/e2e").mkdir(parents=True)
            (root / "scripts/record-qwen-pilot.py").write_bytes(recorder.BOOTSTRAP_SOURCE)
            helper = root / "scripts/e2e/snapshot.py"
            helper.write_bytes(recorder.SNAPSHOT_SOURCE)
            recorder.validate_bootstrap(root)
            helper.write_text("raise RuntimeError('modified helper')\n")
            with self.assertRaises(ValueError):
                recorder.validate_bootstrap(root)

    def test_recorder_refuses_nonisolated_python(self):
        result = subprocess.run([sys.executable, str(Path(recorder.__file__)), "--help"],
                                capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"python -I", result.stderr)


if __name__ == "__main__":
    unittest.main()
