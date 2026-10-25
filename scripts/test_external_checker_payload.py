# SPDX-License-Identifier: Apache-2.0
"""Offline observer packaging boundaries; no endpoints, keys, Docker or installs."""

from contextlib import ExitStack, redirect_stderr
import copy
import io
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

import external_checker_payload as observer
import provider_conformance_worker as linux
import selected_service_conformance as subset
from test_tau_runtime import wheel


class PlatformTests(unittest.TestCase):
    def test_exact_tags_and_unchanged_linux_gate(self):
        for tag in ("py3-none-any", "py2.py3-none-any", "py312-none-any",
                    "cp312-cp312-macosx_10_13_universal2", "cp312-cp312-macosx_11_0_arm64"):
            name = "alpha-1.0-" + tag + ".whl"
            observer.wheel_target(name)
            self.assertEqual(observer.runtime.wheel_tags(name), "macosx" not in tag)
        for tag in ("py2-none-any", "cp313-cp313-macosx_11_0_arm64", "cp312-abi3-macosx_11_0_arm64",
                    "cp312-cp312-macosx_12_0_arm64", "cp312-cp312-macosx_10_9_x86_64",
                    "cp312-cp312-manylinux_2_17_x86_64", "cp312-cp312-win_amd64"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                observer.wheel_target("alpha-1.0-" + tag + ".whl")
        self.assertTrue(observer.runtime.wheel_tags("alpha-1.0-cp312-cp312-manylinux_2_17_x86_64.whl"))

    def test_runtime_requires_exact_python_abi_architecture_and_os_floor(self):
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(observer.sys, "platform", "darwin"))
            stack.enter_context(mock.patch.object(observer.sys, "version_info", (3, 12, 13)))
            stack.enter_context(mock.patch.object(observer.platform, "machine", return_value="arm64"))
            stack.enter_context(mock.patch.object(observer.platform, "mac_ver", return_value=("26.1", (), "")))
            stack.enter_context(mock.patch.object(observer.sysconfig, "get_config_var", return_value="cpython-312-darwin"))
            observer.check_runtime()
            for obj, name, value in ((observer.sys, "platform", "linux"),
                    (observer.sys, "version_info", (3, 12, 14)),
                    (observer.sys, "version_info", (3, 14, 4))):
                with mock.patch.object(obj, name, value), self.assertRaises(ValueError):
                    observer.check_runtime()
            for obj, name, value in ((observer.platform, "machine", "x86_64"),
                    (observer.platform, "mac_ver", ("10.15", (), "")),
                    (observer.sysconfig, "get_config_var", "cpython-312d-darwin")):
                with mock.patch.object(obj, name, return_value=value), self.assertRaises(ValueError):
                    observer.check_runtime()

    def test_native_archive_metadata_hash_and_import_hook_boundaries(self):
        tag = "cp312-cp312-macosx_11_0_arm64"
        raw = wheel(wheel_metadata="Wheel-Version: 1.0\nTag: " + tag + "\n")
        row = {"filename": "alpha-1.0-" + tag + ".whl", "version": "1.0",
               "bytes": len(raw), "sha256": observer.digest(raw)}
        deadline = time.monotonic() + 10
        files = {}
        observer.add_wheel(files, raw, row, deadline)
        self.assertIn("packages/alpha/__init__.py", files)
        with self.assertRaises(ValueError):
            observer.prep.add_wheel({}, raw, row, deadline)
        for changed in ({**row, "sha256": "0" * 64}, {**row, "version": "2.0"},
                        {**row, "filename": "alpha-1.0-py3-none-any.whl"}):
            with self.assertRaises(ValueError):
                observer.add_wheel({}, raw, changed, deadline)
        with self.assertRaises(ValueError):
            observer.add_wheel(files, raw, row, deadline)
        for name in ("alpha.pth", "sitecustomize.py", "../outside.py", "alpha.data/data/x", "alpha.pyc"):
            raw = wheel(members=[(name, b"PRIVATE_CANARY")])
            row = {"filename": "alpha-1.0-py3-none-any.whl", "version": "1.0",
                   "bytes": len(raw), "sha256": observer.digest(raw)}
            with self.subTest(name=name), self.assertRaises(ValueError):
                observer.add_wheel({}, raw, row, deadline)


class PayloadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.end = time.monotonic() + 30
        self.producer = {"git_sha": "1" * 40, "files": {name: "2" * 64 for name in observer.SOURCES}}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(observer, "check_runtime"))
        self.stack.enter_context(mock.patch.object(observer, "identity", return_value=self.producer))

    def fixture(self):
        files = {"packages/example.py": b"raise AssertionError('MUST_NOT_IMPORT')\n",
                 "packages/empty.py": b""}
        packages = observer.prep.requirements((observer.ROOT / "scripts/e2e/requirements.lock").read_bytes())
        wheels = {name: {"filename": name.replace("-", "_") + "-" + version + "-py3-none-any.whl",
                        "version": version, "bytes": 100, "sha256": sorted(hashes)[0]}
                  for name, (version, hashes) in packages.items()}
        wheels["trustedrouter-provider-check"]["sha256"] = observer.prep.CHECKER_SHA
        return files, {"schema": observer.SCHEMA, "target": observer.TARGET, "producer": self.producer,
            "requirements_sha256": observer.prep.LOCK_SHA, "checker_wheel_sha256": observer.prep.CHECKER_SHA,
            "model_profile": observer.prep.source(observer.prep.PROFILE_SOURCE, observer.prep.PROFILE_SHA).select("uncensored"),
            "wheels": wheels, "files": {name: {"bytes": len(raw), "sha256": observer.digest(raw)}
                                        for name, raw in files.items()}}

    def publish(self, files, plan, name="sealed"):
        raw = observer.encoded(plan)
        root = self.root / name
        observer.prep.publish(root, {**files, "trial.json": raw})
        return root / "payload", observer.digest(raw)

    def test_inventory_empty_files_and_linux_schema_separation(self):
        files, plan = self.fixture()
        root, sha = self.publish(files, plan)
        self.assertEqual(observer.verify(root, sha, self.end), plan)
        with self.assertRaises((ValueError, OSError)):
            linux.verify(root, sha)
        with self.assertRaises((ValueError, OSError)):
            subset.payload_files(root, self.end, sha)
        with self.assertRaises(ValueError):
            observer.verify(root, "0" * 64, self.end)

    def test_self_consistent_wrong_target_source_lock_or_wheel_rejected(self):
        files, base = self.fixture()
        changes = [lambda p: p["target"].update(abi="cp313"),
            lambda p: p["target"].update(extra=True), lambda p: p["producer"].update(git_sha="3" * 40),
            lambda p: p.update(schema="nib.private-provider-payload/v1"),
            lambda p: p.update(requirements_sha256="0" * 64), lambda p: p.update(extra="PRIVATE_CANARY"),
            lambda p: p["wheels"].pop("brotli"),
            lambda p: p["wheels"]["brotli"].update(sha256="0" * 64),
            lambda p: p["wheels"]["brotli"].update(filename="brotli-1.2.0-cp312-cp312-manylinux_2_17_x86_64.whl")]
        for index, change in enumerate(changes):
            plan = copy.deepcopy(base)
            change(plan)
            root, sha = self.publish(files, plan, str(index))
            with self.subTest(index=index), self.assertRaises(ValueError):
                observer.verify(root, sha, self.end)

    def test_bytes_extras_links_and_write_permissions_rejected(self):
        for kind in ("bytes", "extra", "link", "writable-file", "writable-directory"):
            files, plan = self.fixture()
            root, sha = self.publish(files, plan, kind)
            packages = root / "packages"
            packages.chmod(0o755)
            path = packages / "example.py"
            if kind == "bytes":
                path.chmod(0o644)
                path.write_bytes(b"different")
                path.chmod(0o444)
            if kind == "extra":
                (packages / "extra.py").write_bytes(b"PRIVATE_CANARY")
            if kind == "link":
                path.unlink()
                path.symlink_to(packages / "empty.py")
            if kind == "writable-file":
                path.chmod(0o644)
            if kind != "writable-directory":
                packages.chmod(0o555)
            with self.subTest(kind=kind), self.assertRaises((ValueError, OSError)):
                observer.verify(root, sha, self.end)

    def test_platform_verification_precedes_import_and_linux_delegation_unchanged(self):
        with mock.patch.object(observer.sys, "platform", "darwin"), \
                mock.patch.object(observer, "verify", side_effect=ValueError), \
                mock.patch.object(subset, "imported_checker") as load:
            with self.assertRaises(ValueError), observer.checker_modules(self.root, self.end, "0" * 64):
                self.fail("must not enter")
            load.assert_not_called()
        with mock.patch.object(observer.sys, "platform", "linux"), \
                mock.patch.object(observer, "verify") as verify, \
                mock.patch.object(subset, "checker_modules") as load:
            with observer.checker_modules(self.root, self.end, "0" * 64):
                pass
            load.assert_called_once_with(self.root, self.end, "0" * 64)
            verify.assert_not_called()

    def test_expiry_and_cli_errors_do_not_publish_or_echo(self):
        files, plan = self.fixture()
        root, sha = self.publish(files, plan)
        with self.assertRaises(ValueError):
            observer.verify(root, sha, time.monotonic() - 1)
        stderr = io.StringIO()
        with redirect_stderr(stderr), mock.patch.object(observer.prep, "publish") as publish:
            self.assertEqual(observer.main(["--PRIVATE_CANARY"]), 1)
        self.assertEqual(stderr.getvalue(), "external checker payload rejected\n")
        publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
