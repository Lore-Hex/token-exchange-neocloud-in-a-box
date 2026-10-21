# SPDX-License-Identifier: Apache-2.0
"""Synthetic dispatch and exact-byte patch tests; no package process executes."""
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import runpy
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
patch = types.SimpleNamespace(**runpy.run_path(str(ROOT / "scripts/mkosi_package_patch.py")))
worker = types.SimpleNamespace(**runpy.run_path(str(ROOT / "scripts/mkosi_package_worker.py")))


class PatchTests(unittest.TestCase):
    def fixture(self):
        before = (b"# SPDX-License-Identifier: LGPL-2.1-or-later\nclass Apt:\n"
                  b"    @classmethod\n"
                  b"    def invoke(cls, context, operation, arguments=(), apivfs=False, options=(), stdout=None):\n"
                  + patch.BEFORE)
        after = before.replace(patch.BEFORE, patch.replacement())
        pin = json.loads((ROOT / patch.PIN).read_bytes())
        pin.update(input_bytes=len(before), input_sha256=hashlib.sha256(before).hexdigest(),
                   result_bytes=len(after), result_sha256=hashlib.sha256(after).hexdigest())
        return before, after, pin

    def test_transformation_is_exact_and_license_preserving(self):
        before, after, pin = self.fixture()
        self.assertEqual(patch.apply(before, pin), after)
        self.assertEqual(after.replace(patch.replacement(), patch.BEFORE), before)
        source_pin = json.loads((ROOT / "image/inputs/mkosi-27.1-service-denial.json").read_bytes())
        boundary = json.loads((ROOT / patch.PIN).read_bytes())
        self.assertEqual((source_pin["result_bytes"], source_pin["result_sha256"]),
                         (boundary["input_bytes"], boundary["input_sha256"]))

    def test_changed_source_patch_or_metadata_rejects(self):
        before, _, pin = self.fixture()
        for raw in (before + b"\n", before.replace(b"cls.cmd", b"cls.bad"), before + patch.BEFORE):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                patch.apply(raw, pin)
        for key, value in (("result_sha256", "0" * 64), ("result_bytes", 1), ("source_path", "bad"),
                           ("mkosi_git_sha", "0" * 40), ("schema", "unknown"), ("extra", True)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                patch.apply(before, {**pin, key: value})

    def test_real_synthetic_invocation_preserves_options_and_wraps_every_operation(self):
        before, _, pin = self.fixture()
        run = mock.Mock()
        namespace = {"run": run}
        exec(compile(patch.apply(before, pin), "synthetic-apt", "exec"), namespace)
        cls = namespace["Apt"]
        cls.cmd = mock.Mock(return_value=["apt-get", "-o", "fixed"])
        cls.sandbox = mock.Mock(return_value="sandbox")
        cls.finalize_environment = mock.Mock(return_value={"APT_CONFIG": "/etc/apt.conf"})
        for operation in ("update", "install", "purge"):
            cls.invoke("ctx", operation, ["pkg=1"], apivfs=True, options=["--dir", "/work"], stdout="out")
            argv = run.call_args.args[0]
            self.assertEqual(argv[:len(patch.guard.PREFIX)], list(patch.guard.PREFIX))
            self.assertEqual(argv[len(patch.guard.PREFIX):], ["/usr/bin/python3.12", "-I", "-S", "-B",
                "/nib/package-worker.py", "apt-get", "-o", "fixed", operation, "pkg=1"])
            cls.sandbox.assert_called_with("ctx", apivfs=True,
                options=["--dir", "/work", "--ro-bind", "/nib", "/nib", "--chdir", "/"])
            self.assertEqual(run.call_args.kwargs, {"sandbox": "sandbox",
                "env": {"APT_CONFIG": "/etc/apt.conf"}, "stdout": "out"})


class WorkerTests(unittest.TestCase):
    def environment(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        for target, name, value in ((worker.sys, "platform", "linux"), (worker.sys, "version_info", (3, 12)),
                (worker.sys, "flags", types.SimpleNamespace(isolated=1, no_site=1, dont_write_bytecode=1)),
                (worker.sys, "argv", ["worker", "apt-get", "install", "pkg=1"])):
            stack.enter_context(mock.patch.object(target, name, value))
        stack.enter_context(mock.patch.object(worker.os, "geteuid", return_value=0))
        stack.enter_context(mock.patch.object(worker.os, "getegid", return_value=0))
        stack.enter_context(mock.patch.object(worker.socket, "if_nameindex", return_value=[(1, "lo")]))
        stack.enter_context(mock.patch.object(worker.Path, "is_file", return_value=True))
        check, execute = mock.Mock(), mock.Mock()
        stack.enter_context(mock.patch.object(worker.runpy, "run_path", return_value={"check": check}))
        stack.enter_context(mock.patch.object(worker.os, "execv", execute))
        return check, execute

    def test_boundary_must_pass_before_absolute_apt_exec(self):
        check, execute = self.environment()
        ordered = mock.Mock()
        ordered.attach_mock(check, "check")
        ordered.attach_mock(execute, "execute")
        worker.main()
        self.assertEqual(ordered.mock_calls, [mock.call.check(),
            mock.call.execute("/usr/bin/apt-get", ["/usr/bin/apt-get", "install", "pkg=1"])])
        worker.runpy.run_path.assert_called_once_with("/nib/confinement.py", run_name="package_confinement")

    def test_unsafe_runtime_network_or_command_never_executes(self):
        check, execute = self.environment()
        changes = ((worker.sys, "argv", ["worker", "/tmp/apt", "install"]),
                   (worker.sys, "argv", ["worker", "apt-get"]),
                   (worker.sys, "platform", "darwin"), (worker.sys, "version_info", (3, 13)),
                   (worker.sys, "flags", types.SimpleNamespace(isolated=0, no_site=1, dont_write_bytecode=1)))
        for obj, field, value in changes:
            with mock.patch.object(obj, field, value), self.assertRaises(ValueError):
                worker.main()
        with mock.patch.object(worker.socket, "if_nameindex", return_value=[(1, "lo"), (2, "eth0")]), \
                self.assertRaises(ValueError):
            worker.main()
        with mock.patch.object(worker.os, "geteuid", return_value=1000), self.assertRaises(ValueError):
            worker.main()
        check.assert_not_called()
        execute.assert_not_called()

    def test_filter_or_readonly_failure_never_executes(self):
        check, execute = self.environment()
        check.side_effect = ValueError("synthetic")
        with self.assertRaises(ValueError):
            worker.main()
        execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
