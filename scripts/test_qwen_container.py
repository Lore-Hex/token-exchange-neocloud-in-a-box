# SPDX-License-Identifier: Apache-2.0
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("qwen_container", Path(__file__).parent / "pilot/container.py")
container = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(container)


class QwenContainerTests(unittest.TestCase):
    def test_recipe_keeps_portable_cpu_and_no_runtime_downloads(self):
        recipe = (container.ROOT / "scripts/pilot/Dockerfile").read_text()
        for option in ("-DGGML_NATIVE=OFF", "-DGGML_CUDA=OFF", "-DGGML_METAL=OFF",
                       "-DGGML_AVX=OFF", "-DGGML_AVX2=OFF", "-DGGML_AVX512=OFF",
                       "-DLLAMA_BUILD_UI=OFF", "-DLLAMA_USE_PREBUILT_UI=OFF", "-DLLAMA_SUBPROCESS=OFF"):
            self.assertIn(option, recipe)
        self.assertIn("USER 65532:65532", recipe)
        self.assertIn('"--prepared", "/opt/nib-pilot"', recipe)
        self.assertNotIn("apt-get", recipe)
        self.assertNotIn(":latest", recipe)
        self.assertNotIn("EXPOSE ", recipe)
        self.assertIn("--require-hashes", recipe)

    def test_runtime_is_offline_unprivileged_bounded_and_unpublished(self):
        identity = "sha256:" + "a" * 64
        args = container.runtime_command(identity, "nib-qwen-test")
        for flag, value in (("--network", "none"), ("--pull", "never"), ("--user", "65532:65532"),
                            ("--cap-drop", "ALL"), ("--security-opt", "no-new-privileges"),
                            ("--platform", "linux/amd64"), ("--memory", "3g"), ("--cpus", "4")):
            self.assertEqual(args[args.index(flag) + 1], value)
        self.assertIn("--read-only", args)
        self.assertIn("noexec,nosuid,nodev", args[args.index("--tmpfs") + 1])
        self.assertEqual(args[-1], identity)
        for forbidden in ("-v", "--volume", "--mount", "--device", "--privileged", "-p", "--publish",
                          "--publish-all", "-e", "--env", "--env-file"):
            self.assertNotIn(forbidden, args)

    def test_context_has_only_explicit_source_and_verified_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs, context, source = root / "inputs", root / "context", root / "source"
            for path in (inputs, context, source):
                path.mkdir()
            (inputs / "model.gguf").write_bytes(b"model")
            pins = {"model": {"sha256": hashlib.sha256(b"model").hexdigest(), "bytes": 5}, "engine": {}}
            bases = {"trcs": {}}
            for filename, data, target in (("llama.tar.gz", b"engine", pins["engine"]),
                                           ("trcs.tar.gz", b"front", bases["trcs"])):
                path = inputs / filename
                with tarfile.open(path, "w:gz") as archive:
                    member = tarfile.TarInfo("upstream/main.txt")
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
                target["archive_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            for name in ("pilot/run.py", "pilot/build-requirements.lock", "pilot/Dockerfile", "e2e/pins.json",
                         "e2e/snapshot.py", "e2e/exercise.py", "e2e/requirements.lock"):
                path = source / "scripts" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("committed fixture")
            (source / "scripts/pilot/pins.json").write_text(json.dumps(pins))
            (source / "secret.env").write_text("NEVER_IN_CONTEXT")
            (source / "scripts/pilot/__pycache__").mkdir()
            (source / "scripts/pilot/__pycache__/ignored.pyc").write_bytes(b"forged")
            (inputs / "credential").write_text("NEVER_IN_CONTEXT")
            with patch.object(container, "ROOT", source), patch.object(container.e2e, "load_pins", return_value=bases):
                result = container.build_context(inputs, context)
            self.assertRegex(result["context_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual((context / "engine/main.txt").read_bytes(), b"engine")
            self.assertEqual((context / "trcs/main.txt").read_bytes(), b"front")
            self.assertEqual((context / "inputs/model.gguf").read_bytes(), b"model")
            names = {path.relative_to(context).as_posix() for path in context.rglob("*") if path.is_file()}
            self.assertEqual(len(names), 11)
            self.assertFalse(any("secret" in name or "credential" in name or "__pycache__" in name for name in names))

    def test_prepared_model_must_match_before_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "model.gguf").write_bytes(b"model")
            pins = {"model": {"sha256": "0" * 64, "bytes": 5}}
            with self.assertRaises(container.pilot.PilotError):
                container.pilot.prepared_inputs(root, pins)
            pins["model"]["sha256"] = hashlib.sha256(b"model").hexdigest()
            with patch.object(container.pilot.shutil, "which", return_value="/checker"):
                self.assertEqual(container.pilot.prepared_inputs(root, pins)[2], root / "model.gguf")
            (root / "model.gguf").rename(root / "actual")
            (root / "model.gguf").symlink_to(root / "actual")
            with self.assertRaises(OSError):
                container.pilot.prepared_inputs(root, pins)

    def test_cleanup_failure_overrides_success_and_runs_on_timeout(self):
        runtime = {"execution": "real-cpu-model", "confidential": False,
                   "hardware_validated": False, "result": "pass",
                   "runtime_platform": {"system": "Linux", "machine": "x86_64"}}
        for timeout in (False, True):
            with self.subTest(timeout=timeout), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                summary = {"result": "fail"}

                def execute(args, **kwargs):
                    self.assertEqual(kwargs["env"]["DOCKER_HOST"], "unix:///local/docker.sock")
                    if args[1] == "build":
                        Path(args[args.index("--iidfile") + 1]).write_text("sha256:" + "a" * 64)
                        return subprocess.CompletedProcess(args, 0)
                    if timeout:
                        raise subprocess.TimeoutExpired(args, 600)
                    return subprocess.CompletedProcess(args, 0, json.dumps(runtime).encode(), b"")

                with patch.object(container.snapshot, "local_docker_endpoint", return_value="unix:///local/docker.sock"), \
                        patch.object(container.snapshot, "require_buildkit"), \
                        patch.object(container, "build_context", return_value={"base_images": {
                            "platform": "linux/amd64", "go_image": "go", "python_image": "python"}}), \
                        patch.object(container.snapshot, "execute_process_group", side_effect=execute), \
                        patch.object(container.e2e, "cleanup_resources", return_value={"result": "fail"}) as cleanup:
                    container.run(root, summary, {"PATH": "/bin"}, root)
                cleanup.assert_called_once()
                self.assertEqual(summary["result"], "fail")
                self.assertEqual(summary["failure_code"], "container-cleanup-unresolved")
                if timeout:
                    self.assertEqual(summary["container_cleanup"]["runtime_completion"], "unknown")

    def test_build_timeout_never_confirms_daemon_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = {"result": "fail"}
            with patch.object(container.snapshot, "local_docker_endpoint", return_value="unix:///local/docker.sock"), \
                    patch.object(container.snapshot, "require_buildkit"), \
                    patch.object(container, "build_context", return_value={"base_images": {
                        "platform": "linux/amd64", "go_image": "go", "python_image": "python"}}), \
                    patch.object(container.snapshot, "execute_process_group",
                                 side_effect=subprocess.TimeoutExpired(["docker", "build"], 1200)), \
                    patch.object(container.e2e, "cleanup_resources", return_value={"result": "pass"}):
                container.run(root, summary, {"PATH": "/bin"}, root)
            self.assertEqual(summary["result"], "fail")
            self.assertEqual(summary["container_cleanup"]["result"], "fail")
            self.assertEqual(summary["container_cleanup"]["build_completion"], "unknown")

    def test_timeout_kills_plugin_descendant_before_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child_code = "import time,pathlib; time.sleep(2); pathlib.Path('late-image').write_text('orphan')"
            parent_code = (
                "import pathlib,signal,subprocess,sys,time\n"
                "child=subprocess.Popen([sys.executable,'-c'," + repr(child_code) + "])\n"
                "pathlib.Path('child-pid').write_text(str(child.pid))\n"
                "def stop(signum, frame):\n"
                "    child.wait()\n"
                "    sys.exit(0)\n"
                "signal.signal(signal.SIGTERM, stop)\n"
                "time.sleep(60)\n"
            )
            with self.assertRaises(subprocess.TimeoutExpired):
                container.snapshot.execute_process_group(
                    [sys.executable, "-c", parent_code], env=container.snapshot.controlled_env(root / "env"),
                    cwd=root, timeout=0.5, capture_output=False)
            child = int((root / "child-pid").read_text())
            with self.assertRaises(ProcessLookupError):
                os.kill(child, 0)
            time.sleep(2)
            self.assertFalse((root / "late-image").exists())

    def test_cleanup_uses_isolated_environment(self):
        with patch.object(container.e2e.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"")) as run:
            result = container.e2e.cleanup_resources("image", "name", env={"ONLY": "safe"})
        self.assertEqual(result["result"], "pass")
        self.assertTrue(all(call.kwargs["env"] == {"ONLY": "safe"} for call in run.call_args_list))


if __name__ == "__main__":
    unittest.main()
