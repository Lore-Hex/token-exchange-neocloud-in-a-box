# SPDX-License-Identifier: Apache-2.0
"""Selected scorer lifecycle with fake Docker and real private descriptors."""

import copy
from contextlib import ExitStack
import json
import os
from pathlib import Path
import stat
import tempfile
import time
import types
import unittest
from unittest import mock

import tau_selected_driver as driver
import test_tau_selected_session as sessions
import test_tau_selected_scorer as scorer
import test_tau_trial as fixtures

runner, tau = fixtures.runner, driver.tau


def diagnostic():
    return {"scorer_protocol_completed": True, "attach_cleanup": "reaped", "scorer_exit_code": 0,
            "owners_finished": True, "usage_known": True,
            "trial": {"status": "evaluated", "usage": {"total_tokens": 24},
                      "role_requests": {"agent": 1, "simulator": 1, "evaluator": 1}}}


class SelectedRunTests(unittest.TestCase):
    def test_borrowed_descriptors_are_duplicated_closed_and_modes_restored(self):
        with ExitStack() as stack:
            handles = {}
            for name in ("candidate", "support"):
                read_fd, peer_write = os.pipe()
                peer_read, write_fd = os.pipe()
                for fd in (read_fd, peer_write, peer_read, write_fd):
                    stack.callback(os.close, fd)
                handles[name] = {"read_fd": read_fd, "write_fd": write_fd, "deadline": time.monotonic() + 90}
            originals = {fd for value in handles.values() for key, fd in value.items() if key.endswith("_fd")}
            acquired = []
            def execute(*args, **kwargs):
                for owner in kwargs.values():
                    self.assertIs(type(owner), driver.owners.Coordinator)
                    acquired.extend((owner.channel.read_fd, owner.channel.write_fd))
                self.assertFalse(set(acquired) & originals)
                raise driver.worker.Abort("timeout")
            with mock.patch.object(driver, "run_process", side_effect=execute), self.assertRaises(driver.worker.Abort):
                driver.run_owned([], {}, None, None, sessions.manifest(), time.monotonic() + 65, handles)
            for fd in acquired:
                with self.assertRaises(OSError):
                    os.fstat(fd)
            for fd in originals:
                self.assertTrue(os.get_blocking(fd))
            for mutation in (lambda h: h["candidate"].update(deadline=time.monotonic() + 30),
                             lambda h: h["candidate"].update(deadline=float("nan")),
                             lambda h: h["candidate"].update(read_fd=h["support"]["read_fd"]),
                             lambda h: h["candidate"].update(read_fd=True)):
                bad = copy.deepcopy(handles)
                mutation(bad)
                with mock.patch.object(driver, "run_process") as run, self.assertRaises((ValueError, driver.worker.Abort)):
                    driver.run_owned([], {}, None, None, sessions.manifest(), time.monotonic() + 65, bad)
                run.assert_not_called()

    def test_partial_duplication_failure_does_not_leak(self):
        with ExitStack() as stack:
            pipes = [os.pipe() for _ in range(4)]
            for pair in pipes:
                for fd in pair:
                    stack.callback(os.close, fd)
            handles = {name: {"read_fd": pipes[2*i][0], "write_fd": pipes[2*i+1][1],
                              "deadline": time.monotonic() + 90}
                       for i, name in enumerate(("candidate", "support"))}
            duplicate = os.dup(pipes[0][0])
            with mock.patch.object(driver.os, "dup", side_effect=[duplicate, OSError()]), self.assertRaises(OSError):
                driver.run_owned([], {}, None, None, sessions.manifest(), time.monotonic() + 65, handles)
            with self.assertRaises(OSError):
                os.fstat(duplicate)

    def test_acceptance_requires_protocol_usage_owners_and_both_generated_roles(self):
        self.assertTrue(driver.accepted(diagnostic()))
        for key, value in (("scorer_protocol_completed", False), ("owners_finished", False),
                           ("usage_known", False), ("scorer_exit_code", 7), ("attach_cleanup", "unconfirmed")):
            self.assertFalse(driver.accepted({**diagnostic(), key: value}))
        for mutation in (lambda r: r.update(status="truncated"), lambda r: r.update(usage=None),
                         lambda r: r["role_requests"].update(agent=0),
                         lambda r: r["role_requests"].update(simulator=0)):
            row = diagnostic()
            mutation(row["trial"])
            self.assertFalse(driver.accepted(row))

    def test_shared_lifecycle_enforces_selected_plan_and_preserves_failure_accounting(self):
        for failure in (None, "image", "create", "isolation", "driver", "terminal", "cleanup", "unevaluated"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                frozen = sessions.manifest()
                plan = scorer.fixture()[3]
                state, published, calls = {"exists": False, "inspections": 0}, {}, []
                def capture(argv, **kwargs):
                    args = argv[5:]
                    calls.append(args)
                    if args[:2] == ["image", "inspect"]:
                        row = fixtures.image_inspection()
                        if failure == "image": row["Architecture"] = "arm64"
                        return tau.encoded([row])
                    if args[0] == "create":
                        self.assertIn("--selected-operational", args)
                        state.update(exists=True, info=fixtures.LifecycleTests().info(args))
                        state["info"]["Config"]["OpenStdin"] = True
                        if failure == "create": raise OSError("PRIVATE_CANARY")
                        return b"d" * 64
                    if args[0] == "inspect":
                        state["inspections"] += 1
                        row = copy.deepcopy(state["info"])
                        if failure == "isolation": row["HostConfig"]["NetworkMode"] = "host"
                        if failure == "terminal" and state["inspections"] == 2: row["State"]["ExitCode"] = 1
                        return tau.encoded([row])
                    if args[0] == "ps": return b"d" * 64 if state["exists"] else b""
                    if args[0] == "rm":
                        if failure == "cleanup": raise OSError("PRIVATE_CANARY")
                        state["exists"] = False
                        return b""
                    self.fail("unexpected Docker command")
                socket = mock.Mock()
                socket.is_absolute.return_value = True
                socket.stat.return_value = types.SimpleNamespace(st_mode=stat.S_IFSOCK)
                observed = diagnostic()
                if failure == "unevaluated": observed["trial"]["status"] = "budget_abort"
                private = types.SimpleNamespace(policy=driver.policy, RESERVE=driver.RESERVE,
                    run_owned=mock.Mock(return_value=observed), accepted=driver.accepted)
                if failure == "driver": private.run_owned.side_effect = driver.worker.Abort("timeout")
                selection = {"manifest": frozen, "handles": {}, "deadline": time.monotonic() + 90}
                with mock.patch.object(runner, "identity", return_value={"git_sha": "a" * 40}) as identity, \
                        mock.patch.object(runner, "validate_plan", return_value=(plan, frozen["input"])) as validate, \
                        mock.patch.object(runner, "parent_driver", return_value=private), \
                        mock.patch.object(runner.shutil, "which", return_value="/fixed/docker"), \
                        mock.patch.object(runner.probe, "capture", side_effect=capture), \
                        mock.patch.object(runner, "publish_evidence", side_effect=lambda p, f: published.update(f)):
                    result = runner.run(Path("/private/payload"), "c" * 64, Path(temporary) / "out", socket,
                                        parent_pipe=True, selected_owners=selection)
                self.assertEqual(result["result"], "fail" if failure else "pass")
                self.assertTrue(identity.call_args.kwargs["selected_operational"])
                self.assertTrue(validate.call_args.kwargs["selected_operational"])
                self.assertFalse(result["quality_eligible"] or result["confidential"] or result["live_admission"])
                self.assertNotIn(b"CANARY", b"".join(published.values()))
                self.assertEqual(json.loads(published["output.log"]), result)
                if failure in ("image", "create", "isolation"):
                    private.run_owned.assert_not_called()
                if failure in (None, "terminal", "cleanup", "unevaluated"):
                    self.assertEqual(result["diagnostic"], observed)
                    self.assertEqual(private.run_owned.call_args.args[-2], selection["deadline"])
                if failure in ("create", "isolation"):
                    self.assertEqual(result["cleanup"], "create-completion-unknown")

    def test_selected_mode_never_mixes_legacy_or_external_modes(self):
        for kwargs in ({}, {"parent_pipe": True, "live_deployment": ()},
                       {"parent_pipe": True, "support_probe": {}},
                       {"parent_pipe": True, "agent_prefix": True}):
            with mock.patch.object(runner, "identity") as identity, self.assertRaises(ValueError):
                runner.run(None, None, None, None, selected_owners={}, **kwargs)
            identity.assert_not_called()


if __name__ == "__main__":
    unittest.main()
