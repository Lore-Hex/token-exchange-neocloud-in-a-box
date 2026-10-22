# SPDX-License-Identifier: Apache-2.0
"""Offline installer contracts; never run package hooks or alter host cgroups."""
import ast
import copy
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
    def test_host_preserves_bounded_symbol_outputs_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            expected = {"dkms-symbols.stdout": b"crypto_alloc_aead U\n",
                        "dkms-symbols.stderr": b"public failure\n"}
            for name, raw in expected.items():
                (root / name).write_bytes(raw)
            (root / "unexpected-private-file").write_bytes(b"PRIVATE SENTINEL")
            self.assertEqual(host.collect_diagnostics(root), expected)
            (root / "dkms-symbols.stdout").write_bytes(b"x" * ((2 << 20) + 1))
            with self.assertRaises(ValueError):
                host.collect_diagnostics(root)
            (root / "dkms-symbols.stdout").unlink()
            (root / "dkms-symbols.stdout").symlink_to(root / "unexpected-private-file")
            with self.assertRaises((ValueError, OSError)):
                host.collect_diagnostics(root)

    def test_failure_diagnostics_never_exports_exception_content_or_unknown_paths(self):
        try:
            worker.require(False)
        except ValueError as error:
            error.args = ("SENSITIVE EXCEPTION TEXT",)
            frames = worker.failure_diagnostics(error)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0]["source"], "build-worker")
        self.assertIs(type(frames[0]["line"]), int)
        self.assertNotIn("SENSITIVE", json.dumps(frames))
        namespace = {}
        exec(compile("def reject():\n raise ValueError('SECRET')\n", "/nib/snapshot.py", "exec"), namespace)
        try:
            namespace["reject"]()
        except ValueError as error:
            self.assertEqual(worker.failure_diagnostics(error), [{"source": "process-supervisor", "line": 2}])
        self.assertEqual(worker.failure_diagnostics(ValueError("SECRET")), [])

    def test_private_process_census_is_bounded_and_contains_only_counts(self):
        with tempfile.TemporaryDirectory() as temporary:
            proc = Path(temporary)
            for pid, state in (("1", "R"), ("2", "Z"), ("3", "Z")):
                (proc / pid).mkdir()
                (proc / pid / "status").write_bytes(b"Name:\tPRIVATE\nState:\t" + state.encode() + b" (description)\n")
            (proc / "gone").mkdir()
            (proc / "4").mkdir()
            counts = worker.process_states(proc)
            self.assertEqual(counts, {"R": 1, "S": 0, "D": 0, "Z": 2, "T": 0, "t": 0, "X": 0, "I": 0})
            for raw in (b"State: INVALID\n", b"State: Z\nState: R\n", b"x" * 16385, b"State:\n"):
                (proc / "2" / "status").write_bytes(raw)
                self.assertIsNone(worker.process_states(proc))
            with mock.patch.object(worker.Path, "iterdir", return_value=iter([proc / str(n) for n in range(257)])):
                self.assertIsNone(worker.process_states(proc))

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
            self.assertEqual(execute.call_args.kwargs["max_stderr_bytes"], 2 << 20)
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

    def test_output_limit_empty_stream_does_not_block_other_artifact(self):
        processes = host.base.processes
        for stream in ("stdout", "stderr"):
            stdout, stderr = ((b"public prefix", b"") if stream == "stdout" else (b"", b"public prefix"))
            error = processes.OutputLimitExceeded(stream, stdout, stderr)
            with self.subTest(stream=stream), tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary).resolve()
                with mock.patch.object(processes, "execute_process_group", side_effect=error):
                    with self.assertRaises(processes.OutputLimitExceeded):
                        worker.install(processes, output, {})
                self.assertEqual([p.name for p in output.iterdir()], ["installer." + stream])
                # Exercise the actual host reader, which intentionally rejects
                # empty artifacts instead of silently accepting bad evidence.
                self.assertEqual(host.base.read(output / ("installer." + stream), 2 << 20), b"public prefix")

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
                                          "SECRET": "private-secret", "TMPDIR": "/private-host-tmp"}, clear=True):
            environment = worker.installation_environment()
        self.assertEqual(environment, {"PATH": "/usr/bin:/usr/sbin:/bin:/sbin", "HOME": "/tmp", "TMPDIR": "/tmp",
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

    def test_install_scratch_uses_existing_private_tmpfs_not_readonly_tools_root(self):
        environment = worker.installation_environment()
        # Exact selection expression in pinned mkosi run.py:vartmpdir, without
        # importing upstream or executing its package hooks.
        with mock.patch.dict(os.environ, environment, clear=True):
            scratch = Path(os.getenv("TMPDIR", "/var/tmp")) / "mkosi-var-tmp-fixture"
        self.assertEqual(scratch.parent, Path("/tmp"))
        with mock.patch.object(host.os, "readlink", return_value="fixture-namespace"), \
                mock.patch.object(host.probe.ownership, "prefix", return_value=[]):
            command = host.command(Path("/private/tools"), Path("/private/payload"), Path("/private/output"), {})
        self.assertIn(["--tmpfs", str(scratch.parent)], [command[i:i+2] for i in range(len(command)-1)])
        self.assertIn(["--ro-bind", "/private/tools", "/"], [command[i:i+3] for i in range(len(command)-2)])

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
                     "scripts/mkosi_package_worker.py", "scripts/mkosi_confinement.py", host.DKMS):
            self.assertIn(name, paths)

    def test_runtime_source_closure_includes_lock_review_and_matching_apt_proof(self):
        with mock.patch.object(host, "preflight"), mock.patch.object(host.tools, "separate_output"), \
                mock.patch.object(host.tools, "committed", return_value=("a" * 40, {})) as committed, \
                mock.patch.object(host, "run", return_value=1) as run, \
                mock.patch.object(host.sys, "argv", ["host", "--downloads", "/downloads", "--archive", "/archive",
                                                    "--output", "/output", "--guest-runtime"]):
            self.assertEqual(host.main(), 1)
        paths = committed.call_args.args[0]
        self.assertLessEqual(len(paths), 28)
        for name in (host.HOOKS, host.hooks.RUNTIME_LOCK, host.hooks.RUNTIME_POLICY, host.RUNTIME_APT):
            self.assertIn(name, paths)
        self.assertIs(run.call_args.args[-1], True)

    def test_worker_accepts_only_the_two_fixed_source_bound_closures(self):
        for path in (host.hooks.LOCK, host.hooks.RUNTIME_LOCK):
            guest = json.loads((ROOT / path).read_bytes())
            expected = worker.expected_packages(guest)
            self.assertEqual(expected, {r["Package"]: r["Version"] for r in guest["packages"]})
            for mutate in (lambda p: p.update(scope="unknown"), lambda p: p["packages"].pop(),
                           lambda p: p["packages"].append(p["packages"][0])):
                bad = copy.deepcopy(guest)
                mutate(bad)
                with self.assertRaises(ValueError):
                    worker.expected_packages(bad)

    def test_runtime_preflight_rechecks_both_evidence_and_hook_inventory_before_execution(self):
        sources = {name: (ROOT / name).read_bytes() for name in
                   (host.hooks.RUNTIME_LOCK, host.hooks.RUNTIME_POLICY, host.RUNTIME_APT)}
        rows = [{"fixture": "not executed"}]
        with mock.patch.object(host.hooks, "inventory", return_value=rows) as inventory, \
                mock.patch.object(host.hooks, "validate", return_value={"result": "pass"}) as validate:
            self.assertEqual(host.runtime_preflight(sources, Path("/archives")), {"result": "pass"})
        self.assertEqual(inventory.call_args.args[1], Path("/archives"))
        self.assertEqual(validate.call_args.args[1:], (sources[host.hooks.RUNTIME_LOCK], rows))
        mutations = (
            lambda p: p.update(result="fail"), lambda p: p.update(cleanup="unknown"),
            lambda p: p.update(target_profile="guest"), lambda p: p["simulation"].update(result="fail"),
            lambda p: p["simulation"].update(guest_lock_sha256="0" * 64),
            lambda p: p["simulation"].update(diagnostic="unresolved-package"),
            lambda p: p["simulation"]["plan"].update(removed=1),
            lambda p: p["simulation"]["plan"].update(removed=False),
            lambda p: p["simulation"]["plan"].update(configured=211),
            lambda p: p["simulation"]["plan"].update(installed=212.0),
        )
        for mutate in mutations:
            proof = json.loads(sources[host.RUNTIME_APT])
            mutate(proof)
            bad = {**sources, host.RUNTIME_APT: json.dumps(proof).encode()}
            with mock.patch.object(host.hooks, "inventory") as inventory, self.assertRaises(ValueError):
                host.runtime_preflight(bad, Path("/archives"))
            inventory.assert_not_called()
        with mock.patch.object(host.hooks, "inventory", return_value=rows), \
                mock.patch.object(host.hooks, "validate", side_effect=ValueError("changed hook")), \
                mock.patch.object(host.base.processes, "execute_process_group") as execute, \
                self.assertRaises(ValueError):
            host.prepare(Path("/inputs"), Path("/archive"), "a" * 40, sources, True)
        execute.assert_not_called()

    def test_workflow_manual_main_only_without_credentials_in_build(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/mkosi-native-build.yml").read_text())
        self.assertEqual(workflow[True], {"workflow_dispatch": {"inputs": {"compile_driver": {
            "description": "Compile the pinned NVIDIA driver after installation (no loading or boot)",
            "type": "boolean", "default": False}, "install_driver": {
            "description": "Install verified driver modules into the private guest root (requires compile_driver; never loads)",
            "type": "boolean", "default": False}, "guest_runtime": {
            "description": "Install the reviewed 212-package runtime candidate instead of the kernel-only candidate",
            "type": "boolean", "default": False}}}})
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
        self.assertIn('if [ "$GUEST_RUNTIME" = true ]; then target=--guest-runtime; fi', fetch)
        self.assertEqual(job["steps"][1]["env"], {"GUEST_RUNTIME": "${{ inputs.guest_runtime }}"})
        self.assertIn("host/run-mkosi-build.py", job["steps"][2]["run"])
        self.assertEqual(job["steps"][2]["env"], {"COMPILE_DRIVER": "${{ inputs.compile_driver }}",
                                               "INSTALL_DRIVER": "${{ inputs.install_driver }}",
                                               "GUEST_RUNTIME": "${{ inputs.guest_runtime }}"})
        self.assertIn('if [ "$GUEST_RUNTIME" = true ]; then args+=(--guest-runtime); fi',
                      job["steps"][2]["run"])
        self.assertIn('if [ "$COMPILE_DRIVER" = true ]; then args+=(--compile-driver); fi',
                      job["steps"][2]["run"])
        self.assertNotIn("${{", job["steps"][2]["run"])
        self.assertEqual(job["steps"][-1]["if"], "always()")

    def test_driver_build_is_explicit_and_does_not_change_default_namespace(self):
        with mock.patch.object(host.os, "readlink", return_value="ns:[1]"), \
                mock.patch.object(host.probe.ownership, "prefix", return_value=[]):
            args = (Path("/rootfs"), Path("/payload"), Path("/result"), {})
            normal = host.command(*args)
            explicit = host.command(*args, compile_driver=True)
            install = host.command(*args, compile_driver=True, install_driver=True)
            with self.assertRaises(ValueError): host.command(*args, install_driver=True)
        self.assertEqual(explicit, normal + ["--compile-driver"])
        self.assertEqual(install, normal + ["--install-driver"])
        self.assertNotIn("--compile-driver", normal)

    def test_host_syntax_compatible_with_fixed_python310(self):
        for path in (host.SCRIPT, host.LAUNCHER, host.RESOURCES):
            ast.parse((ROOT / path).read_bytes(), filename=path, feature_version=(3, 10))


if __name__ == "__main__":
    unittest.main()
