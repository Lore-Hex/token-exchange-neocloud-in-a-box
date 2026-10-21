# SPDX-License-Identifier: Apache-2.0
"""Offline installer contracts; never run package hooks or alter host cgroups."""
import ast
from contextlib import contextmanager
import getpass
import json
import os
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    module = types.ModuleType("build_fixture")
    module.__file__ = str(ROOT / path)
    exec(compile(Path(module.__file__).read_bytes(), module.__file__, "exec"), module.__dict__)
    return module


host = load("host/build-mkosi-native.py")
launcher = load("host/run-mkosi-build.py")
worker = load("scripts/mkosi_build_worker.py")
inputs = load("scripts/guest_package_inputs.py")


class BuildTests(unittest.TestCase):
    def test_install_preserves_bounded_failure_and_does_not_accept_it(self):
        processes = host.base.processes
        report = {"installer_exit_code": None}
        error = processes.OutputLimitExceeded("stderr", b"public stdout", b"public stderr")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            with mock.patch.object(processes, "execute_process_group", side_effect=error) as execute:
                with self.assertRaises(processes.OutputLimitExceeded):
                    worker.install(processes, output, report)
            self.assertEqual(report, {"installer_exit_code": None, "output_limit_stream": "stderr"})
            self.assertEqual((output / "installer.stdout").read_bytes(), b"public stdout")
            self.assertEqual((output / "installer.stderr").read_bytes(), b"public stderr")
            self.assertEqual(execute.call_args.kwargs["max_output_bytes"], 2 << 20)
            self.assertEqual(execute.call_args.kwargs["timeout"], 1800)

    def test_install_records_nonzero_exit_without_success(self):
        processes = host.base.processes
        report = {}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            with mock.patch.object(processes, "execute_process_group", return_value=
                                   subprocess.CompletedProcess([], 1, b"", b"public failure")):
                with self.assertRaises(subprocess.CalledProcessError):
                    worker.install(processes, output, report)
            self.assertEqual(report, {"installer_exit_code": 1})
            self.assertEqual((output / "installer.stderr").read_bytes(), b"public failure")

    def test_candidate_tools_alias_names_only_the_private_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            config, prepared = root / "config", root / "prepared"
            config.mkdir()
            prepared.mkdir()
            original = b"[Build]\nToolsTree=tools-tree\nWithNetwork=no\n"
            (prepared / "mkosi.conf.candidate").write_bytes(original)
            worker.prepare_configuration(config, prepared)
            self.assertEqual((config / "mkosi.conf").read_bytes(), original)
            self.assertEqual((config / "tools-tree").readlink(), Path("/"))
            self.assertEqual({p.name for p in config.iterdir()},
                             {"mkosi.conf", "tools-tree", "repo", "mkosi-skeleton"})
            for name in ("repo", "mkosi-skeleton"):
                self.assertEqual((config / name).readlink(), prepared / name)
            with self.assertRaises(FileExistsError):
                worker.prepare_configuration(config, prepared)

    def test_private_build_identity_does_not_require_or_inherit_host_accounts(self):
        with mock.patch.dict(os.environ, {"USER": "private-host-user", "LOGNAME": "private-host-login",
                                          "SECRET": "private-secret"}, clear=True):
            environment = worker.installation_environment()
        self.assertEqual(environment, {"PATH": "/usr/bin:/usr/sbin:/bin:/sbin", "HOME": "/tmp",
            "USER": "root", "LOGNAME": "root", "LANG": "C", "LC_ALL": "C",
            "PYTHONDONTWRITEBYTECODE": "1", "SOURCE_DATE_EPOCH": "1792454400"})
        with mock.patch("pwd.getpwuid", side_effect=KeyError("uid not found")) as passwd:
            with mock.patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(KeyError):
                    getpass.getuser()
            passwd.reset_mock()
            with mock.patch.dict(os.environ, environment, clear=True):
                self.assertEqual(getpass.getuser(), "root")
            passwd.assert_not_called()
        environment["USER"] = "changed"
        self.assertEqual(worker.installation_environment()["USER"], "root")

    def test_payload_rejection_reports_stage_and_never_executes_packages(self):
        def materialize(files, root):
            root.mkdir()

        publish = host.base.publish

        def reject_payload(destination, files):
            if destination.name == "payload":
                raise ValueError("synthetic-payload-rejection")
            publish(destination, files)

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary).resolve() / "evidence"
            with mock.patch.object(host, "prepare", return_value=({}, {}, {}, {}, {})), \
                    mock.patch.object(host.prep, "assemble", return_value=({}, {})), \
                    mock.patch.object(host.probe.ownership, "host_inputs", return_value=({}, {})), \
                    mock.patch.object(host.probe, "materialize", side_effect=materialize), \
                    mock.patch.object(host.base, "publish", side_effect=reject_payload), \
                    mock.patch.object(host.base.processes, "execute_process_group") as execute:
                self.assertEqual(host.run(Path("/unused"), Path("/unused"), output, "a" * 40, {}), 1)
            execute.assert_not_called()
            report = json.loads((output / "result.json").read_bytes())
            self.assertEqual(report["stage"], "payload-publication")
            self.assertEqual(report["result"], "fail")
            self.assertTrue(report["payload_removed"])
            self.assertFalse(report["process_cleanup_confirmed"])

    def test_inventory_requires_exact_configured_versions(self):
        raw = b"Package: example\nVersion: 1.2\nArchitecture: amd64\nStatus: install ok installed\n\n"
        self.assertEqual(worker.configured(raw, {"example": "1.2"}, inputs.paragraphs), 1)
        for bad in (b"", raw + raw, raw.replace(b"1.2", b"1.3"),
                    raw.replace(b"installed", b"unpacked"), raw.replace(b"amd64", b"arm64"),
                    raw.replace(b"Status:", b"Unknown:"), raw + b"garbage\n",
                    raw.replace(b"Status:", b"Status: install ok installed\nStatus:")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                worker.configured(bad, {"example": "1.2"}, inputs.paragraphs)
        with self.assertRaises(ValueError):
            worker.configured(raw, {"example": "1.2", "missing": "2"}, inputs.paragraphs)

    def test_default_deb822_fields_unchanged(self):
        self.assertEqual(set(next(inputs.paragraphs(b"Package: example\n\n"))), set(inputs.FIELDS))
        self.assertNotIn("Status", inputs.FIELDS)

    def test_fixed_command_no_host_paths_or_network(self):
        original = [name + ":[1]" for name in ("user", "mnt", "net", "ipc", "pid")]
        with mock.patch.object(host.os, "readlink", side_effect=original), \
                mock.patch.object(host.probe.ownership, "prefix", return_value=["MAPPED"]):
            command = host.command(Path("/rootfs"), Path("/payload"), Path("/result"), {})
        self.assertEqual(command[:2], ["MAPPED", "/usr/bin/python3.10"])
        self.assertEqual(command[-6:], ["/nib/build-worker.py", *original])
        for part in ("--unshare-net", "--unshare-ipc", "--mount-proc=/proc", "--kill-child=KILL"):
            self.assertIn(part, command)
        for source, target in (("/rootfs", "/"), ("/payload", "/nib"), ("/proc", "/proc")):
            self.assertIn(["--ro-bind", source, target], [command[i:i+3] for i in range(len(command)-2)])
        self.assertEqual(command.count("--bind"), 1)
        self.assertIn(["--bind", "/result", "/out"], [command[i:i+3] for i in range(len(command)-2)])
        self.assertNotIn(str(ROOT), command)

    def test_build_command_uses_only_private_paths_and_pinned_interpreter(self):
        self.assertEqual(worker.COMMAND[:5], ["/usr/bin/python3.12", "-I", "-S", "-B", "/nib/mkosi-entry.py"])
        self.assertEqual(worker.COMMAND[-1], "build")
        for flag in ("--tools-tree=/", "--build-sources=", "--history=no", "--output=rootfs"):
            self.assertIn(flag, worker.COMMAND)
        self.assertNotIn("--force", worker.COMMAND)

    def test_root_launcher_bounds_child_and_closes_join_descriptor(self):
        child = mock.Mock(pid=123, returncode=0)
        child.wait.return_value = 0
        child.poll.return_value = 0
        with mock.patch.object(launcher.os, "open", return_value=91) as opened, \
                mock.patch.object(launcher.os, "close") as close, \
                mock.patch.object(launcher.os, "killpg") as kill, \
                mock.patch.object(launcher.subprocess, "Popen", return_value=child) as popen:
            self.assertEqual(launcher.launch(90, 1001, 1001, ["--output", "/fresh"]), 0)
        self.assertEqual(opened.call_args.kwargs["dir_fd"], 90)
        self.assertEqual(popen.call_args.kwargs["pass_fds"], (91,))
        self.assertEqual(popen.call_args.kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(popen.call_args.kwargs["stderr"], subprocess.DEVNULL)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        child.wait.assert_any_call(timeout=2500)
        kill.assert_not_called()
        close.assert_called_once_with(91)
        self.assertIn("os.setgroups([])", launcher.CHILD)
        self.assertLess(launcher.CHILD.index("os.close(fd)"), launcher.CHILD.index("os.setuid(uid)"))
        self.assertLess(launcher.CHILD.index("os.setuid(uid)"), launcher.CHILD.index("os.execve("))

    def test_launcher_timeout_still_kills_and_waits(self):
        child = mock.Mock(pid=123)
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired([], 2500), 0]
        with mock.patch.object(launcher.os, "open", return_value=91), \
                mock.patch.object(launcher.os, "close") as close, \
                mock.patch.object(launcher.os, "killpg") as kill, \
                mock.patch.object(launcher.subprocess, "Popen", return_value=child), \
                self.assertRaises(subprocess.TimeoutExpired):
            launcher.launch(90, 1001, 1001, [])
        kill.assert_called_once()
        close.assert_called_once_with(91)

    def test_cgroup_cleanup_and_oom_are_required_not_just_exit_zero(self):
        for bad in (None, "limits_read_back", "empty_verified", "group_removed", "oom_kill", "exit"):
            @contextmanager
            def group(state):
                state.update(limits_read_back=bad != "limits_read_back")
                yield 90, "nib-build-" + "a" * 32
                state.update(empty_verified=bad != "empty_verified", group_removed=bad != "group_removed",
                             oom_kill=1 if bad == "oom_kill" else 0)
            with mock.patch.object(launcher.resources, "cgroup", group), \
                    mock.patch.object(launcher.os, "fchmod") as chmod, \
                    mock.patch.object(launcher, "launch", return_value=1 if bad == "exit" else 0):
                result = launcher.execute(1001, 1001, [])
            self.assertEqual(result["result"], "pass" if bad is None else "fail")
            chmod.assert_called_once_with(90, 0o755)

    def test_receipt_rejects_existing_links_and_uses_open_descriptor(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            path, target = directory / "receipt.json", directory / "other"
            target.write_bytes(b"original")
            path.symlink_to(target)
            with self.assertRaises(OSError):
                with launcher.receipt_file(path, os.getuid(), os.getgid()):
                    self.fail("must not open existing link")
            path.unlink()
            with mock.patch.object(launcher.os, "fchown"):
                with launcher.receipt_file(path, os.getuid(), os.getgid()) as descriptor:
                    path.unlink()
                    path.symlink_to(target)
                    os.write(descriptor, b"fixed receipt")
            self.assertEqual(target.read_bytes(), b"original")

    def test_cancellation_exits_through_cgroup_cleanup_and_never_passes(self):
        @contextmanager
        def group(state):
            try:
                state["limits_read_back"] = True
                yield 90, "nib-build-" + "a" * 32
            finally:
                state.update(empty_verified=True, group_removed=True, oom_kill=0)
        with mock.patch.object(launcher.resources, "cgroup", group), \
                mock.patch.object(launcher.os, "fchmod"), \
                mock.patch.object(launcher, "launch", side_effect=KeyboardInterrupt):
            result = launcher.execute(1001, 1001, [])
        self.assertEqual(result["result"], "fail")
        self.assertIsNone(result["child_exit_code"])
        self.assertTrue(result["empty_verified"] and result["group_removed"])

    def test_parent_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            (directory / "actual").mkdir()
            (directory / "alias").symlink_to(directory / "actual", target_is_directory=True)
            with self.assertRaises(OSError):
                with launcher.receipt_file(directory / "alias" / "result", os.getuid(), os.getgid()):
                    self.fail("symlink parent accepted")

    def test_committed_source_closure_is_bounded(self):
        with mock.patch.object(host, "preflight"), mock.patch.object(host.tools, "separate_output"), \
                mock.patch.object(host.tools, "committed", return_value=("a" * 40, {})) as committed, \
                mock.patch.object(host, "run", return_value=1), \
                mock.patch.object(host.sys, "argv", ["host", "--downloads", "/downloads", "--archive", "/archive", "--output", "/output"]):
            self.assertEqual(host.main(), 1)
        paths = committed.call_args.args[0]
        self.assertLessEqual(len(paths), 24)
        for name in (host.SCRIPT, host.WORKER, host.ENTRY, host.LAUNCHER, host.RESOURCES,
                     "scripts/mkosi_package_worker.py", "scripts/mkosi_confinement.py"):
            self.assertIn(name, paths)

    def test_workflow_manual_main_only_without_credentials_in_build(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/mkosi-native-build.yml").read_text())
        self.assertEqual(workflow[True], {"workflow_dispatch": None})
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        job = workflow["jobs"]["install"]
        self.assertEqual(job["if"], "github.ref == 'refs/heads/main'")
        self.assertEqual(job["runs-on"], "ubuntu-22.04")
        self.assertEqual(job["timeout-minutes"], 55)
        self.assertIs(job["steps"][0]["with"]["persist-credentials"], False)
        fetch = job["steps"][1]["run"]
        self.assertIn("54def86b7b37b3b5178782965e499f0242a98f36bc7828105f112005576663a7", fetch)
        self.assertIn("--metadata-archive", fetch)
        self.assertIn("--include-guest", fetch)
        self.assertIn("host/run-mkosi-build.py", job["steps"][2]["run"])
        self.assertEqual(job["steps"][-1]["if"], "always()")

    def test_host_syntax_compatible_with_fixed_python310(self):
        for path in (host.SCRIPT, host.LAUNCHER, host.RESOURCES):
            ast.parse((ROOT / path).read_bytes(), filename=path, feature_version=(3, 10))


if __name__ == "__main__":
    unittest.main()
