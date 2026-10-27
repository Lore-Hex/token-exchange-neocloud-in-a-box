# SPDX-License-Identifier: Apache-2.0
"""Offline core policy regressions: no Docker, network, signals or host writes."""

from contextlib import ExitStack
import copy
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

import selected_core_limits as core
import selected_service_owner as owner

host = owner.network.source_module("core_policy_test", "host/harden-selected-service-core.py")
ZERO = {"processes": 3, "zero": 3, "limits": [[0, 0]]}
OLD = {"processes": 3, "zero": 0, "limits": [[-1, -1]]}


class CoreTests(unittest.TestCase):
    def test_empty_pattern_and_zero_pid_are_joint_requirement(self):
        good = {key: value.decode() for key, value in core.SETTINGS.items()}
        with mock.patch.object(core, "host_values", return_value=good):
            core.check_host()
        for key, value in (("kernel/core_pattern", "|helper\n"), ("kernel/core_pattern", "/dev/null\n"),
                           ("kernel/core_pattern", ""), ("kernel/core_uses_pid", "1\n"), ("fs/suid_dumpable", "2\n")):
            with self.subTest(key=key, value=value), mock.patch.object(core, "host_values", return_value={**good, key: value}):
                with self.assertRaises(ValueError):
                    core.check_host()
        self.assertIn(b"kernel.core_pattern =\n", core.CONFIG)
        self.assertNotIn(b"/dev/null", core.CONFIG)
        self.assertLess(core.CONFIG.index(b"core_uses_pid = 0"), core.CONFIG.index(b"core_pattern =\n"))

    def test_bounded_nofollow_kernel_reader(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "value"
            path.write_bytes(b"\n")
            self.assertEqual(core.read(path, 1), b"\n")
            path.write_bytes(b"ab")
            with self.assertRaises(ValueError):
                core.read(path, 1)
            link = Path(directory) / "link"
            link.symlink_to(path)
            with self.assertRaises(OSError):
                core.read(link)

    def test_worker_namespace_user_and_mode_fail_closed(self):
        for mode, ids, space, pid in (("other", (65532,) * 3, [1, 2], 9), ("apply", (0,) * 3, [1, 2], 9),
                                      ("apply", (65532,) * 3, [9, 9], 9),
                                      ("inspect", (65532,) * 3, [1, 2], 1), ("apply", (65532,) * 3, [1, 2], 1)):
            with self.subTest(mode=mode, ids=ids, space=space, pid=pid), ExitStack() as stack:
                stack.enter_context(mock.patch.object(core.sys, "platform", "linux"))
                stack.enter_context(mock.patch.object(core.os, "getpid", return_value=pid))
                stack.enter_context(mock.patch.object(core.os, "getresuid", return_value=ids, create=True))
                stack.enter_context(mock.patch.object(core.os, "getresgid", return_value=ids, create=True))
                stack.enter_context(mock.patch.object(core, "namespace", return_value=space))
                setter = stack.enter_context(mock.patch.object(core.resource, "setrlimit"))
                scan = stack.enter_context(mock.patch.object(core, "scan"))
                with self.assertRaises(ValueError):
                    core.worker(mode, [1, 2])
                setter.assert_not_called()
                scan.assert_not_called()

    def test_worker_inspect_never_sets_and_apply_has_fixed_passes(self):
        for mode in ("inspect", "apply"):
            with self.subTest(mode=mode), ExitStack() as stack:
                stack.enter_context(mock.patch.object(core.sys, "platform", "linux"))
                stack.enter_context(mock.patch.object(core.os, "getpid", return_value=9))
                for name in ("getresuid", "getresgid"):
                    stack.enter_context(mock.patch.object(core.os, name, return_value=(65532,) * 3, create=True))
                stack.enter_context(mock.patch.object(core, "namespace", return_value=[1, 2]))
                setter = stack.enter_context(mock.patch.object(core.resource, "setrlimit"))
                scan = stack.enter_context(mock.patch.object(core, "scan", return_value=ZERO))
                self.assertEqual(core.worker(mode, [1, 2]), ZERO)
                self.assertEqual(scan.call_args_list, [mock.call(True), mock.call(True), mock.call(False)]
                                 if mode == "apply" else [mock.call(False)])
                self.assertEqual(setter.call_count, int(mode == "apply"))
                if mode == "apply":
                    scan.return_value = OLD
                    with self.assertRaises(ValueError):
                        core.worker(mode, [1, 2])

    def test_same_user_requires_all_four_uid_gid_fields(self):
        good = b"Uid:\t65532\t65532\t65532\t65532\nGid:\t65532\t65532\t65532\t65532\n"
        with mock.patch.object(core, "read", return_value=good):
            core.same_user(1)
        for raw in (good.replace(b"65532", b"0", 1), good + good, good.split(b"Gid:")[0]):
            with mock.patch.object(core, "read", return_value=raw), self.assertRaises(ValueError):
                core.same_user(1)

    def test_scan_sets_only_core_in_owned_namespace_and_checks_readback(self):
        with mock.patch.object(core.os, "getpid", return_value=9), \
                mock.patch.object(core, "processes", return_value=[1, 9, 23]), \
                mock.patch.object(core, "same_user") as same_user, \
                mock.patch.object(core.resource, "prlimit", return_value=(0, 0), create=True) as limit:
            self.assertEqual(core.scan(True), {"processes": 2, "zero": 2, "limits": [[0, 0]]})
            self.assertEqual(same_user.call_args_list, [mock.call(1), mock.call(23)])
            self.assertEqual(limit.call_args_list, [mock.call(pid, core.resource.RLIMIT_CORE, *args)
                for pid in (1, 23) for args in (((0, 0),), ())])
            same_user.reset_mock()
            limit.reset_mock()
            limit.return_value = (-1, -1)
            with self.assertRaises(ValueError):
                core.scan(True)
            same_user.assert_called_once_with(1)
            self.assertEqual(limit.call_args_list, [mock.call(1, core.resource.RLIMIT_CORE, (0, 0)),
                                                   mock.call(1, core.resource.RLIMIT_CORE)])

    def test_scan_inspect_only_reads_peer_limits_and_skips_self(self):
        with mock.patch.object(core.os, "getpid", return_value=9), \
                mock.patch.object(core, "processes", return_value=[1, 9, 23]), \
                mock.patch.object(core, "same_user") as same_user, \
                mock.patch.object(core.resource, "prlimit", side_effect=[(0, 0), (-1, -1)], create=True) as limit:
            self.assertEqual(core.scan(False), {"processes": 2, "zero": 1, "limits": [[-1, -1], [0, 0]]})
            self.assertEqual(same_user.call_args_list, [mock.call(1), mock.call(23)])
            self.assertEqual(limit.call_args_list, [mock.call(1, core.resource.RLIMIT_CORE),
                                                   mock.call(23, core.resource.RLIMIT_CORE)])

    def test_process_bound_and_missing_init(self):
        for values in (list(range(1, 1026)), [9]):
            entries = [types.SimpleNamespace(name=str(pid)) for pid in values]
            context = mock.MagicMock()
            context.__enter__.return_value = iter(entries)
            with mock.patch.object(core.os, "scandir", return_value=context), self.assertRaises(ValueError):
                core.processes()


class MigrationTests(unittest.TestCase):
    def test_binding_uses_real_owned_join_for_container_name_id_image_and_token(self):
        token, identifier, image = "c" * 32, "a" * 64, "sha256:" + "b" * 64
        info = {"Id": identifier, "Name": "nib-selected-" + token, "Image": image,
            "Config": {"User": "65532:65532", "Labels": {host.smoke.LABEL: token}},
            "HostConfig": {"PidMode": "", "Ulimits": []},
            "State": {"Running": True, "Paused": False, "Restarting": False, "Dead": False, "Pid": 7}}
        info["Name"] = "/" + info["Name"]
        rows = {"start-intent.json": {}, "container-resolved.json": {"id": identifier},
            "runtime.json": {"image_id": image}, "contract.json": {
                "wall": 1e11, "monotonic": 1e11, "network": {"policy": {"token": token}}}}
        state = mock.Mock()
        state.read.return_value = rows
        commands = host.Commands(None, {}, 1e11, 1e11)
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(host.owner, "daemon_identity", return_value=(10, 20)))
            stack.enter_context(mock.patch.object(core, "namespace", side_effect=lambda path:
                [1, 2] if path == "/proc/7/ns/pid" else [1, 3]))
            stack.enter_context(mock.patch.object(core, "read", return_value=b"7 (x) R " + b"0 " * 18 + b"10"))
            call = stack.enter_context(mock.patch.object(commands, "call", return_value=identifier.encode()))
            inspect = stack.enter_context(mock.patch.object(commands, "inspect", return_value=info))
            host.binding(commands, state, (10, 20))
            for key, value in (("Id", "d" * 64), ("Name", "/wrong"), ("Image", "sha256:" + "d" * 64),
                               ("Config", {"User": "65532:65532", "Labels": {host.smoke.LABEL: "d" * 32}})):
                inspect.return_value = {**info, key: value}
                with self.subTest(key=key), self.assertRaises(ValueError):
                    host.binding(commands, state, (10, 20))
            inspect.return_value = info
            call.return_value = ("d" * 64).encode()
            with self.assertRaises(ValueError):
                host.binding(commands, state, (10, 20))
            call.return_value = identifier.encode()
            for key in ("wall", "monotonic"):
                rows["contract.json"][key] = 0
                with self.subTest(key=key), self.assertRaises(ValueError):
                    host.binding(commands, state, (10, 20))
                rows["contract.json"][key] = 1e11

    def test_binding_rejects_identity_namespace_pid_user_and_lease_drift(self):
        info = {"Id": "a" * 64, "Image": "sha256:" + "b" * 64,
            "Config": {"User": "65532:65532"}, "HostConfig": {"PidMode": "", "Ulimits": []},
            "State": {"Running": True, "Paused": False, "Restarting": False, "Dead": False, "Pid": 7}}
        rows = {"start-intent.json": {}, "container-resolved.json": {"id": "a" * 64},
            "runtime.json": {"image_id": info["Image"]}, "contract.json": {
                "wall": 1e11, "monotonic": 1e11, "network": {"policy": {"token": "c" * 32}}}}
        commands, state = mock.Mock(), mock.Mock()
        state.read.return_value = rows
        commands.owned.return_value = info
        with ExitStack() as stack:
            daemon = stack.enter_context(mock.patch.object(host.owner, "daemon_identity", return_value=(10, 20)))
            namespace = stack.enter_context(mock.patch.object(core, "namespace", side_effect=lambda path:
                [1, 2] if path == "/proc/7/ns/pid" else [1, 3]))
            stack.enter_context(mock.patch.object(core, "read", return_value=b"7 (x) R " + b"0 " * 18 + b"10"))
            self.assertEqual(host.binding(commands, state, (10, 20))["container"], "a" * 64)
            commands.owned.assert_called_once_with("nib-selected-" + "c" * 32, "c" * 32, info["Image"], "a" * 64)
            for scope, key, value in (("Config", "User", "0:0"), ("HostConfig", "PidMode", "host"),
                    ("State", "Running", False), ("State", "Paused", True), ("State", "Restarting", True),
                    ("State", "Dead", True), ("State", "Pid", True), ("State", "Pid", 1)):
                changed = copy.deepcopy(info)
                changed[scope][key] = value
                commands.owned.return_value = changed
                with self.subTest(key=key), self.assertRaises(ValueError):
                    host.binding(commands, state, (10, 20))
            commands.owned.return_value = None
            with self.assertRaises(ValueError):
                host.binding(commands, state, (10, 20))
            commands.owned.return_value = info
            daemon.return_value = (10, 21)
            with self.assertRaises(ValueError):
                host.binding(commands, state, (10, 20))
            daemon.return_value = (10, 20)
            namespace.side_effect = None
            namespace.return_value = [1, 2]
            with self.assertRaises(ValueError):
                host.binding(commands, state, (10, 20))
            rows["contract.json"]["wall"] = 0
            with self.assertRaises(ValueError):
                host.binding(commands, state, (10, 20))

    def test_owned_docker_inspect_projects_no_content_fields(self):
        commands = host.Commands(None, {}, 100, 100)
        with mock.patch.object(commands, "call", return_value=b"{}") as call:
            commands.inspect("a" * 64)
        template = call.call_args.args[0][4]
        for field in (".Env", ".Cmd", ".Args", ".Error", ".LogPath", "{{json .}}", "{{json .State}}"):
            self.assertNotIn(field, template)
        self.assertIn(".State.Pid", template)

    def test_exact_exec_no_shell_no_privilege_no_profile_changes(self):
        commands = mock.Mock()
        commands.run.return_value.stdout = json.dumps(ZERO).encode()
        selected = {"container": "b" * 64, "namespace": [1, 2]}
        self.assertEqual(host.limits(commands, selected, "apply", b"source"), ZERO)
        self.assertEqual(commands.run.call_args, mock.call(["docker", "exec", "--interactive", "--user", "65532:65532",
            "b" * 64, "/usr/bin/python3.12", "-I", "-S", "-B", "-", "apply", "1", "2"], data=b"source"))
        for row in (OLD, {**ZERO, "extra": "PRIVATE_CANARY"}, {**ZERO, "zero": True},
                    {**ZERO, "processes": 1025}, {**ZERO, "limits": [[-1, -1]]}):
            commands.run.return_value.stdout = json.dumps(row).encode()
            with self.assertRaises(ValueError):
                host.limits(commands, selected, "apply", b"source")

    def transaction(self, apply, failure=None):
        events, saved = [], {}
        def save(name, row):
            events.append(name)
            if failure == "baseline" and name == "baseline.json":
                raise OSError("PRIVATE_CANARY")
            saved[name] = copy.deepcopy(row)
        def install(_):
            events.append("install")
            if failure == "install":
                raise OSError("PRIVATE_CANARY")
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(host.network.tau_runtime, "read_file", return_value=b"source"))
            stack.enter_context(mock.patch.object(host.owner, "daemon_identity", return_value=(1,)))
            stack.enter_context(mock.patch.object(host, "binding", return_value={"container": "a" * 64}))
            stack.enter_context(mock.patch.object(host, "config_bytes", return_value=core.CONFIG))
            stack.enter_context(mock.patch.object(host, "check_order"))
            stack.enter_context(mock.patch.object(host, "mask_state", return_value="masked"))
            stack.enter_context(mock.patch.object(core, "host_values", return_value={}))
            stack.enter_context(mock.patch.object(core, "check_host"))
            stack.enter_context(mock.patch.object(host, "limits", side_effect=lambda *args:
                (events.append(args[2]), ZERO)[1]))
            stack.enter_context(mock.patch.object(host, "install", side_effect=install))
            receipt = types.SimpleNamespace(save=save)
            if failure:
                with self.assertRaises(OSError):
                    host.execute(None, receipt, None, apply)
            else:
                host.execute(None, receipt, None, apply)
        return events, saved

    def test_baseline_durable_before_any_change_and_inspect_is_default(self):
        events, saved = self.transaction(True)
        self.assertEqual(events, ["inspect", "baseline.json", "install", "apply", "result.json"])
        self.assertTrue(saved["result.json"]["applied"])
        self.assertTrue(saved["result.json"]["persistent_policy_verified"])
        self.assertFalse(saved["result.json"]["effective_unit_mask_verified"])
        events, saved = self.transaction(False)
        self.assertEqual(events, ["inspect", "baseline.json", "result.json"])
        self.assertFalse(saved["result.json"]["applied"])
        self.assertFalse(saved["result.json"]["zdr"])
        self.assertFalse(saved["result.json"]["core_policy_verified"])
        self.assertFalse(saved["result.json"]["persistent_policy_verified"])
        self.assertFalse(saved["result.json"]["effective_unit_mask_verified"])

    def test_baseline_write_failure_blocks_mutation_and_postwrite_failure_retained(self):
        events, saved = self.transaction(True, "baseline")
        self.assertEqual(events, ["inspect", "baseline.json"])
        self.assertFalse(saved)
        events, saved = self.transaction(True, "install")
        self.assertEqual(events, ["inspect", "baseline.json", "install", "result.json"])
        self.assertEqual(saved["result.json"]["result"], "fail")
        self.assertFalse(saved["result.json"]["effective_unit_mask_verified"])
        self.assertNotIn("PRIVATE_CANARY", json.dumps(saved))

    def test_immutable_private_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            os.chmod(root, 0o700)
            receipt = host.Receipt(root)
            receipt.save("baseline.json", {"before": True})
            with self.assertRaises(ValueError):
                receipt.save("baseline.json", {"before": False})
            receipt.save("result.json", {"result": "fail"})
            with self.assertRaises(FileExistsError):
                receipt.save("result.json", {"result": "pass"})
            for path in root.iterdir():
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_install_exact_file_and_disk_mask_without_manager_reload_or_state_claim(self):
        commands = mock.Mock()
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory).resolve()
            config = root / "policy.conf"
            allowed = [["systemctl", "mask", "--no-reload", "apport.service"],
                       ["/usr/lib/systemd/systemd-sysctl", str(config)]]
            def call(command, **kwargs):
                self.assertIn(command, allowed)
                return b""
            commands.call.side_effect = call
            stack.enter_context(mock.patch.object(host, "CONFIG", config))
            stack.enter_context(mock.patch.object(host, "MASK", root / "apport.service"))
            stack.enter_context(mock.patch.object(host, "config_bytes", side_effect=lambda:
                config.read_bytes() if config.exists() else None))
            stack.enter_context(mock.patch.object(host, "mask_state", return_value="masked"))
            stack.enter_context(mock.patch.object(host, "check_order"))
            checked = stack.enter_context(mock.patch.object(core, "check_host"))
            host.install(commands)
            self.assertEqual(config.read_bytes(), core.CONFIG)
            checked.assert_called_once()
            self.assertEqual([call.args[0] for call in commands.call.call_args_list], allowed)

    def test_later_sysctl_file_rejected(self):
        context = mock.MagicMock()
        context.__enter__.return_value = iter([types.SimpleNamespace(name="zzz-override.conf")])
        with mock.patch.object(host.os, "scandir", return_value=context), self.assertRaises(ValueError):
            host.check_order()

    def test_persistence_fsync_or_readback_failure_does_not_continue(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory).resolve()
            config = root / "policy.conf"
            stack.enter_context(mock.patch.object(host, "CONFIG", config))
            stack.enter_context(mock.patch.object(host, "config_bytes", side_effect=lambda:
                config.read_bytes() if config.exists() else None))
            stack.enter_context(mock.patch.object(host, "check_order"))
            stack.enter_context(mock.patch.object(host.os, "fsync", side_effect=OSError("private")))
            commands = mock.Mock()
            with self.assertRaises(OSError):
                host.install(commands)
            commands.call.assert_not_called()
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(host, "config_bytes", return_value=core.CONFIG))
            stack.enter_context(mock.patch.object(host, "check_order"))
            stack.enter_context(mock.patch.object(host, "mask_state", side_effect=["absent", "absent"]))
            commands = mock.Mock()
            with self.assertRaises(ValueError):
                host.install(commands)
            commands.call.assert_called_once_with(["systemctl", "mask", "--no-reload", "apport.service"], limit=4096)

    def test_future_launches_fail_before_resource_work_if_host_policy_drifts(self):
        with mock.patch.object(core, "check_host", side_effect=ValueError("rejected")):
            with self.assertRaises(ValueError):
                host.smoke.run_container(*([None] * 9), {})
            with self.assertRaises(ValueError):
                host.host.run(None, None, None, {})


if __name__ == "__main__":
    unittest.main()
