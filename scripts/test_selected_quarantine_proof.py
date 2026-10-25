# SPDX-License-Identifier: Apache-2.0
"""Quarantine proof orchestration only: no sockets, daemon, mount or nft execution."""

import copy
from contextlib import ExitStack, redirect_stdout
from dataclasses import replace
import hashlib
import io
from pathlib import Path
import signal
import subprocess
import tempfile
import types
import unittest
from unittest import mock

import selected_network_policy as policy
import test_selected_network_policy as core
from test_selected_network_quarantine import readback

proof = core.proof
NFT = proof.Commands.nft


class QuarantineProofTests(unittest.TestCase):
    def run_proof(self, failure=None, venue=proof.LEGACY_VENUE):
        events, captured = [], {}
        def configure(commands, args, stack, cleanup, verify):
            selected = replace(core.contract(), nft_version=proof.venue_settings(venue)["nftables"])
            quarantine = policy.Quarantine(selected)
            state = {"serving": False, "quarantine": False}
            original_run = commands.run.side_effect
            original_verify = policy.verify_objects
            verify.side_effect = lambda raw: original_verify(raw, selected.table,
                selected.nft_version, selected.objects(canonical=True))
            def run(argv, **kwargs):
                if argv == selected.readback_args():
                    name = "readback-quarantine" if state["quarantine"] else "readback-serving"
                    events.append(name)
                    if failure == name:
                        raise ValueError("PRIVATE-CANARY")
                    rows = (selected.objects(canonical=True) if state["serving"] else [])
                    rows += quarantine.objects(canonical=True) if state["quarantine"] else []
                    if state["quarantine"] and (failure == "drift"
                            or failure == "post-drift" and "denials" in events):
                        rows[-1]["rule"]["expr"][-1] = {"accept": None}
                    return subprocess.CompletedProcess(argv, 0, readback(quarantine, rows), b"")
                if "--file" in argv:
                    rows = policy.decode(kwargs["data"])["nftables"]
                    operation = next(iter(rows[0]))
                    table = rows[0][operation]["table"]["name"]
                    kind = "quarantine" if table == quarantine.table else "serving"
                    phase = "check" if "--check" in argv else "apply"
                    name = phase + "-" + operation + "-" + kind
                    events.append(name)
                    if failure == name:
                        raise subprocess.TimeoutExpired("PRIVATE-CANARY", 1)
                    if phase == "apply":
                        state[kind] = operation == "create"
                        if failure == "uncertain-withdrawal" and operation == "delete":
                            raise subprocess.TimeoutExpired("PRIVATE-CANARY", 1)
                        if failure == "cancel-apply" and kind == "quarantine":
                            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
                    return subprocess.CompletedProcess(argv, 0, b"", b"")
                return original_run(argv, **kwargs)
            commands.run.side_effect = run
            commands.rules.side_effect = lambda: run(selected.readback_args()).stdout
            commands.nft.side_effect = lambda data, report=None: NFT(commands, data, report)
            observed = proof.observations.side_effect
            def observations(*values):
                row = observed(*values)
                if state["quarantine"]:
                    events.append("denials")
                    self.assertFalse(state["serving"])
                    if failure in row:
                        row[failure] = True
                return row
            proof.observations.side_effect = observations
            def query(_commands, name, address, port, kind="tcp"):
                if state["quarantine"] and address in ("127.0.0.1", "::1"):
                    events.append("live-listener")
                    if failure == "dead-listener":
                        return False
                return True
            proof.query.side_effect = query
            def live_sinks(*_args):
                if failure == "dead-sink" and state["quarantine"]:
                    raise ValueError("PRIVATE-CANARY")
            proof.check_sinks.side_effect = live_sinks
            def container_check(*_args, **_kwargs):
                if failure == "container-drift" and state["quarantine"]:
                    raise ValueError("PRIVATE-CANARY")
            proof.check_container.side_effect = container_check
            def host_query(*values):
                if values[-1] == "stats":
                    return {"tcp": 0, "dns_udp": 2 if failure == "dns-drift" and state["quarantine"] else 1,
                            "dns_tcp": 1}
                return failure != "tls-control"
            proof.host_query.side_effect = host_query
            def cleanup_result(*_args, **kwargs):
                if kwargs and (kwargs["quarantine_install"] is None
                        or failure in ("cleanup", "drift", "post-drift", "readback-quarantine", "uncertain-withdrawal")):
                    raise ValueError("PRIVATE-CANARY")
                return True
            cleanup.side_effect = cleanup_result
            captured.update(commands=commands, cleanup=cleanup, args=args, quarantine=quarantine)
        result = core.ProofTests().inner_case(quarantine=True, venue=venue, configure=configure)
        return result, events, captured

    def test_both_profiles_prove_quarantine_without_serving_tables(self):
        for venue in proof.VENUES:
            with self.subTest(venue=venue):
                (status, report, _, reaps, _), events, captured = self.run_proof(venue=venue)
                self.assertEqual((status, report["result"], reaps), (0, "pass", 2))
                self.assertEqual(report["schema"], "nib.selected-nat-proof/v2")
                evidence = report["quarantine"]
                self.assertEqual(evidence["policy_step"], "complete")
                self.assertTrue(all(evidence[key] for key in
                    ("installed_verified", "serving_withdrawn", "verified", "removed")))
                self.assertEqual(evidence["tls_controls"], {"tls_host": True, "tls_forward": True})
                self.assertEqual(len(evidence["observations"]), 15)
                self.assertFalse(any(evidence["observations"].values()))
                self.assertLess(events.index("apply-create-quarantine"), events.index("apply-delete-serving"))
                self.assertLess(events.index("apply-delete-serving"), events.index("denials"))
                self.assertEqual(events.count("live-listener"), 6)
                extra = captured["cleanup"].call_args.kwargs
                self.assertEqual(extra, dict(quarantine=captured["quarantine"], quarantine_install=0,
                                            serving_withdrawn=True))
                self.assertEqual(captured["commands"].end, captured["args"].deadline)

    def test_kernel_failures_and_drift_do_not_claim_verified_quarantine(self):
        for failure, step in (("check-create-quarantine", "check"), ("apply-create-quarantine", "apply"),
                ("readback-quarantine", "readback"), ("drift", "readback"),
                ("check-delete-serving", "withdraw-serving"), ("apply-delete-serving", "withdraw-serving"),
                ("uncertain-withdrawal", "withdraw-serving")):
            with self.subTest(failure=failure):
                (status, report, _, reaps, _), events, captured = self.run_proof(failure)
                self.assertEqual((status, report["result"], reaps), (1, "fail", 2))
                evidence = report["quarantine"]
                self.assertEqual(evidence["policy_step"], step)
                self.assertFalse(evidence["verified"])
                self.assertFalse(evidence["serving_withdrawn"])
                self.assertNotIn("denials", events)
                self.assertNotIn("PRIVATE", policy.encoded(report).decode())
                self.assertIsNotNone(captured["cleanup"].call_args.kwargs["quarantine"])
                if failure in ("check-create-quarantine", "apply-create-quarantine",
                               "readback-quarantine", "drift", "uncertain-withdrawal"):
                    self.assertFalse(evidence["removed"])
                    self.assertFalse(report["cleanup_confirmed"])

    def test_each_negative_and_live_control_failure_remains_failure(self):
        success = self.run_proof()[0][1]
        for failure in (*success["quarantine"]["observations"], "dead-listener", "dead-sink", "container-drift",
                        "post-drift", "dns-drift", "tls-control", "cancel-apply", "cleanup"):
            with self.subTest(failure=failure):
                (status, report, _, reaps, _), _, _ = self.run_proof(failure)
                self.assertEqual((status, report["result"], reaps), (1, "fail", 2))
                if failure == "cleanup":
                    self.assertFalse(report["quarantine"]["removed"])
                    self.assertFalse(report["cleanup_confirmed"])
                if failure == "tls-control":
                    self.assertFalse(report["quarantine"]["installed_verified"])
                    self.assertNotIn("batch_sha256", report["quarantine"])

    def test_report_rejects_field_type_claim_and_version_tampering(self):
        report = self.run_proof()[0][1]
        mutations = [("schema", "nib.selected-nat-proof/v1"), ("schema", "other"),
                     ("published", True), ("unknown", "PRIVATE-CANARY")]
        for key, value in mutations:
            with self.subTest(key=key), self.assertRaises(ValueError):
                proof.check_report({**report, key: value})
        for key, values in {
            "installed_verified": (False, 1, "true"), "serving_withdrawn": (False, 1),
            "verified": (False, 1), "removed": (False, 1), "batch_sha256": (None, "bad"),
            "policy_step": ("pending", "PRIVATE-CANARY", [], None), "unknown": (False,),
            "tls_controls": ({"tls_host": True}, {"tls_host": 1, "tls_forward": True}),
            "observations": ({}, {**report["quarantine"]["observations"], "tls_host": True}),
        }.items():
            for value in values:
                changed = copy.deepcopy(report)
                changed["quarantine"][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    proof.check_report(changed)
        for key in report["quarantine"]:
            changed = copy.deepcopy(report)
            del changed["quarantine"][key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                proof.check_report(changed)

    def test_explicit_cli_selection_keeps_original_budget_and_default_argv(self):
        for venue in proof.VENUES:
            base = proof.namespace_command(Path("/private/work"), Path("/private/image"), 1, 2, 1234, venue)
            selected = proof.namespace_command(Path("/private/work"), Path("/private/image"), 1, 2, 1234,
                                               venue, quarantine=True)
            self.assertEqual(selected, [*base, "--quarantine"])
            self.assertEqual(selected[selected.index("--deadline") + 1], "1234")
        self.assertEqual((proof.OPERATIONS, proof.CLEANUP, proof.REAP), (480, 90, 25))
        for bad in (1, None, "true"):
            with self.assertRaises(ValueError):
                proof.namespace_command(Path("/work"), Path("/image"), 1, 2, 1234, quarantine=bad)

    def test_commands_never_extend_deadline_for_quarantine_and_cancel_stops_work(self):
        commands = proof.Commands(100, {"cancelled": False}, Path("/synthetic"))
        with mock.patch.object(proof.time, "monotonic", return_value=76), \
                mock.patch.object(proof.snapshot, "execute_process_group") as execute:
            with self.assertRaises(ValueError):
                commands.nft(policy.Quarantine(core.contract()).batch())
            execute.assert_not_called()
        commands.cancelled["cancelled"] = True
        with mock.patch.object(proof.time, "monotonic", return_value=1), \
                mock.patch.object(proof.snapshot, "execute_process_group") as execute:
            with self.assertRaises(ValueError):
                commands.run(core.contract().readback_args())
            execute.assert_not_called()
        self.assertEqual(commands.end, 100)

    def test_outer_requires_requested_mode_and_keeps_source_closure(self):
        serving = core.ProofTests().inner_case()[1]
        quarantined = self.run_proof()[0][1]
        hashes = {name: hashlib.sha256((proof.ROOT / name).read_bytes()).hexdigest()
                  for name in proof.SOURCES}
        self.assertEqual(set(hashes), {"scripts/prove-selected-nat.py", "scripts/selected_network_policy.py",
            "scripts/fixtures/selected_nat_fixture.py", "scripts/e2e/snapshot.py", proof.VENUE_FILE})
        for requested in (False, True):
            for returned in (False, True):
                with self.subTest(requested=requested, returned=returned), \
                        tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                    output = Path(directory).resolve() / "result"
                    argv = ["proof", "--run", "--archive", "/synthetic/image.tar", "--archive-sha256",
                            "a" * 64, "--output", str(output), *(["--quarantine"] if requested else [])]
                    commands = mock.Mock(env=proof.ENV)
                    child = mock.Mock(pid=123, returncode=0)
                    for target, name, options in (
                        (proof.sys, "argv", {"new": argv}), (proof.sys, "platform", {"new": "linux"}),
                        (proof.sys, "version_info", {"new": (3, 12, 13)}),
                        (proof.platform, "machine", {"return_value": "x86_64"}),
                        (proof.os, "getuid", {"return_value": 0}),
                        (proof.Path, "is_file", {"return_value": True}),
                        (proof, "resolver_target", {}), (proof, "check_venue", {}),
                        (proof, "copy_archive", {}), (proof, "cleanup_cgroup", {}),
                        (proof, "prepare_sources", {"return_value": ("a" * 40, hashes)}),
                        (proof, "Commands", {"return_value": commands}),
                        (proof.snapshot, "bounded_output", {"return_value": (
                            policy.encoded(quarantined if returned else serving), b"PRIVATE-CANARY")}),
                        (proof.snapshot, "cleanup_process_group", {})):
                        stack.enter_context(mock.patch.object(target, name, **options))
                    old_stat = proof.os.stat
                    def stat(path, *args, **kwargs):
                        if str(path).startswith("/proc/self/ns/"):
                            return types.SimpleNamespace(st_ino=123)
                        return old_stat(path, *args, **kwargs)
                    stack.enter_context(mock.patch.object(proof.os, "stat", side_effect=stat))
                    popen = stack.enter_context(mock.patch.object(proof.subprocess, "Popen", return_value=child))
                    stream = io.StringIO()
                    with redirect_stdout(stream):
                        status = proof.main()
                    self.assertEqual(status, 0 if requested == returned else 1)
                    self.assertNotIn("PRIVATE-CANARY", stream.getvalue())
                    report = policy.decode((output / "observation.json").read_bytes())
                    self.assertEqual(report["schema"], "nib.selected-nat-proof-host/v" + ("2" if requested else "1"))
                    self.assertEqual(report["source_sha256"], hashes)
                    self.assertIs(report["namespace_reaped"], True)
                    self.assertEqual("--quarantine" in popen.call_args.args[0], requested)
                    self.assertEqual("observation" in report, requested == returned)

    def test_withdrawal_requires_fresh_namespace_opt_in_and_quarantine(self):
        quarantine = policy.Quarantine(core.contract())
        args = types.SimpleNamespace(host_net=1, host_mount=2, quarantine=True, deadline=100)
        commands = mock.Mock(end=100, cleaning=False)
        for failure in ("namespace", "opt-in", "deadline", "cleaning", "quarantine"):
            with self.subTest(failure=failure):
                commands.reset_mock()
                changed = types.SimpleNamespace(**{**vars(args),
                    **({"quarantine": False} if failure == "opt-in" else {}),
                    **({"deadline": 101} if failure == "deadline" else {})})
                commands.cleaning = failure == "cleaning"
                with mock.patch.object(proof, "check_namespace",
                        side_effect=ValueError() if failure == "namespace" else None), \
                        mock.patch.object(proof, "quarantine_readback", side_effect=ValueError()):
                    with self.assertRaises(ValueError):
                        proof.withdraw_serving_in_namespace(commands, changed, quarantine, 0)
                commands.nft.assert_not_called()

    def test_opt_in_pre_quarantine_failure_does_not_invent_quarantine_attempt(self):
        for venue in proof.VENUES:
            for failure in ("create", "policy", "readback"):
                with self.subTest(venue=venue, failure=failure):
                    status, report, _, _, _ = core.ProofTests().inner_case(
                        quarantine=True, failure=failure, venue=venue)
                    self.assertEqual((status, report["result"]), (1, "fail"))
                    self.assertEqual(report["quarantine"], {"policy_step": "pending",
                        "installed_verified": False, "serving_withdrawn": False, "verified": False, "removed": False})


class CleanupTests(unittest.TestCase):
    def cleanup(self, *, failure=None, withdrawn=True, version="1.0.9"):
        selected = replace(core.contract(), nft_version=version)
        quarantine = policy.Quarantine(selected)
        commands = mock.Mock()
        events, state = [], {"quarantine": True, "serving": not withdrawn}
        def docker(*argv):
            events.append(argv[:2])
            if failure == "inventory":
                raise ValueError("PRIVATE-CANARY")
            raw = b"candidate" if failure == "networks" and argv[:2] == ("network", "ls") else b""
            if failure == "networks":
                raw = proof.SERVICE.encode()
                commands.inspect.return_value = {"Labels": {proof.LABEL: "wrong"}, "Containers": {}}
            return subprocess.CompletedProcess([], 0, raw, b"")
        commands.docker.side_effect = docker
        def raw():
            rows = selected.objects(canonical=True) if state["serving"] else []
            rows += quarantine.objects(canonical=True) if state["quarantine"] else []
            if failure == "drift":
                rows[-1]["rule"]["expr"][-1] = {"accept": None}
            return readback(quarantine, rows)
        def run(argv):
            self.assertEqual(argv, quarantine.readback_args())
            events.append("readback")
            return subprocess.CompletedProcess([], 1 if failure == "readback" else 0, raw(), b"")
        commands.run.side_effect = run
        commands.rules.side_effect = raw
        def nft(data):
            events.append("delete")
            rows = policy.decode(data)["nftables"]
            self.assertEqual(len(rows), 2)
            name = rows[0]["delete"]["table"]["name"]
            kind = "quarantine" if name == quarantine.table else "serving"
            if failure == "remove":
                raise subprocess.TimeoutExpired("PRIVATE-CANARY", 1)
            if failure != "retained":
                state[kind] = False
        commands.nft.side_effect = nft
        try:
            result = proof.owned_cleanup(commands, selected.token, selected, failure == "unknown", True,
                quarantine=quarantine, quarantine_install=None if failure == "install" else 0,
                serving_withdrawn=withdrawn)
        except (ValueError, subprocess.TimeoutExpired):
            result = False
        return result, events, commands

    def test_absence_then_fresh_readback_then_removal_both_pins_and_serving_states(self):
        for version in policy.NFT_RELEASES:
            for withdrawn in (False, True):
                result, events, commands = self.cleanup(withdrawn=withdrawn, version=version)
                self.assertTrue(result)
                self.assertLess(max(i for i, event in enumerate(events) if isinstance(event, tuple)),
                                events.index("delete"))
                self.assertLess(events.index("readback"), events.index("delete"))
                self.assertEqual(commands.nft.call_count, 1 if withdrawn else 2)

    def test_ambiguity_failed_commands_and_drift_cannot_remove_quarantine(self):
        for failure in ("unknown", "inventory", "networks", "install", "readback", "drift"):
            with self.subTest(failure=failure):
                result, _, commands = self.cleanup(failure=failure)
                self.assertFalse(result)
                commands.nft.assert_not_called()
        for failure in ("remove", "retained"):
            with self.subTest(failure=failure):
                self.assertFalse(self.cleanup(failure=failure)[0])

    def test_withdrawal_flag_cannot_bypass_original_cleanup_without_quarantine(self):
        commands = mock.Mock()
        for bad in (True, 1, "true"):
            with self.assertRaises(ValueError):
                proof.owned_cleanup(commands, core.contract().token, core.contract(), False, True,
                                    serving_withdrawn=bad)
        commands.docker.assert_not_called()

    def test_container_removal_failure_retains_quarantine(self):
        selected = core.contract()
        commands = mock.Mock()
        commands.docker.side_effect = [subprocess.CompletedProcess([], 0, b"1" * 64 + b"\n", b""),
                                      subprocess.TimeoutExpired("PRIVATE-CANARY", 1)]
        commands.inspect.return_value = {"Image": proof.CONFIG, "Name": "/candidate",
                                        "Config": {"Labels": {proof.LABEL: selected.token}}}
        with self.assertRaises(subprocess.TimeoutExpired):
            proof.owned_cleanup(commands, selected.token, selected, False, True,
                quarantine=policy.Quarantine(selected), quarantine_install=0, serving_withdrawn=True)
        commands.nft.assert_not_called()


if __name__ == "__main__":
    unittest.main()
