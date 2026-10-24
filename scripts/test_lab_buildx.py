# SPDX-License-Identifier: Apache-2.0
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "host/install-lab-buildx.py"
SPEC = importlib.util.spec_from_file_location("lab_buildx", SOURCE)
buildx = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(buildx)


class LabBuildxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = b"test-only verified executable"
        self.source = self.root / "source"
        self.source.write_bytes(self.data)
        for name, value in (("SIZE", len(self.data)),
                            ("SHA256", hashlib.sha256(self.data).hexdigest()),
                            ("TARGET", self.root / "docker-buildx")):
            context = patch.object(buildx, name, value)
            context.start()
            self.addCleanup(context.stop)

    def test_exact_binary(self):
        self.assertEqual(buildx.verified_bytes(self.source), self.data)

    def test_wrong_size_and_hash(self):
        for content in (b"short", b"x" * len(self.data), self.data + b"x"):
            with self.subTest(content=content):
                self.source.write_bytes(content)
                with self.assertRaises(ValueError):
                    buildx.verified_bytes(self.source)

    def test_symlink_directory_fifo_rejected(self):
        link = self.root / "link"
        link.symlink_to(self.source)
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        for path in (link, self.root, fifo):
            with self.subTest(path=path), self.assertRaises((OSError, ValueError)):
                buildx.verified_bytes(path)

    def test_install_exact_bytes_without_replacement(self):
        with patch.object(buildx, "secure_directory"):
            self.assertTrue(buildx.install(self.data))
            self.assertEqual(buildx.TARGET.read_bytes(), self.data)
            self.assertEqual(buildx.TARGET.stat().st_mode & 0o777, 0o755)
            # Existing content must be reverified, not silently overwritten.
            buildx.TARGET.write_bytes(b"changed")
            with self.assertRaises(ValueError):
                buildx.install(self.data)
            self.assertEqual(buildx.TARGET.read_bytes(), b"changed")
            self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["docker-buildx", "source"])

    def test_unsafe_directory_rejected(self):
        with self.assertRaises(ValueError):
            buildx.secure_directory(self.root)

    def test_version_check_isolated_and_bounded(self):
        with patch.object(buildx.subprocess, "run") as run:
            run.return_value.stdout = b"github.com/docker/buildx v0.38.1 0dbe87f\n"
            buildx.verify_plugin()
            options = run.call_args.kwargs
            self.assertEqual(options["timeout"], 10)
            self.assertTrue(options["check"])
            self.assertNotIn("DOCKER_AUTH_CONFIG", options["env"])
            self.assertFalse(Path(options["env"]["DOCKER_CONFIG"]).exists())

    def test_wrong_version_and_failure_rejected(self):
        with patch.object(buildx.subprocess, "run") as run:
            for stdout in (b"", b"github.com/docker/buildx v0.38.0 abc", b"wrong v0.38.1 abc"):
                run.return_value.stdout = stdout
                with self.assertRaises(ValueError):
                    buildx.verify_plugin()
            run.side_effect = subprocess.TimeoutExpired("docker", 10)
            with self.assertRaises(subprocess.TimeoutExpired):
                buildx.verify_plugin()
