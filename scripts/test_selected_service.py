# SPDX-License-Identifier: Apache-2.0
"""Fake clocks/owners only: no process creation, listening ports or hardware."""

from contextlib import ExitStack, contextmanager
from dataclasses import FrozenInstanceError, replace
import datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import selected_service as service


class Clock:
    def __init__(self):
        self.wall = datetime.datetime(2026, 10, 24, tzinfo=datetime.timezone.utc).timestamp()
        self.mono = 100.0

    def time(self):
        return self.wall

    def monotonic(self):
        return self.mono

    def advance(self, seconds):
        self.wall += seconds
        self.mono += seconds

    def timestamp(self, seconds):
        return datetime.datetime.fromtimestamp(self.wall + seconds, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Shutdown:
    def __init__(self, clock):
        self.clock, self.stopped, self.retire = clock, False, None

    def is_set(self):
        return self.stopped

    def wait(self, seconds):
        self.clock.advance(seconds if self.retire is None else max(seconds, self.retire - self.clock.mono))


class Child:
    def __init__(self, pid):
        self.pid, self.returncode = pid, None

    def poll(self):
        return self.returncode


def endpoint(pid):
    digest = hashlib.sha256(b"synthetic executable and argv").hexdigest()
    return service.identity.Endpoint(8000, {"process": {"pid": pid, "start_ticks": 1,
        "boot_id_sha256": digest, "netns_inode": 2, "exe_sha256": digest,
        "argv_sha256": digest, "listener_inode": 3}})


class Owner:
    def __init__(self, clock, shutdown):
        self.clock, self.shutdown = clock, shutdown
        self.children, self.events, self.launches = {}, [], []
        self.observation = None
        self.startup_seconds = 0
        self.mounts_changed = False

    def check_mounts(self, deadline):
        if self.mounts_changed:
            raise ValueError("PRIVATE_CANARY")
        return True

    def start(self, role, contract, inputs, register, deadline):
        self.events.append(("start", role))
        self.launches.append(contract)
        child = Child(1000 + len(self.children))
        self.children[role] = child
        register(child)

    def probe(self, role, child, deadline):
        if role == "engine":
            if self.observation is None:
                self.clock.advance(self.startup_seconds)
                self.observation = service.EngineReady(endpoint(child.pid),
                    json.dumps({"data": [{"id": service.MODEL, "max_model_len": service.CONTEXT}]}).encode(),
                    service.CONTEXT, service.MEMORY_BYTES, 0)
            return self.observation
        if role == "tls":
            self.shutdown.retire = self.launches[0].hard_monotonic - service.CLEANUP_RESERVE
        return True

    def drain(self, role, child, deadline):
        self.events.append(("drain", role))
        # The stream still needs its downstream layers until drain returns.
        assert all(self.children[name].poll() is None for name in service.ROLES[:service.ROLES.index(role)])
        self.clock.advance(1)
        child.returncode = 0
        return True

    def reap(self, role, child, deadline):
        self.events.append(("reap", role))
        child.returncode = 0
        return True

    def reap_unregistered(self, deadline):
        self.events.append(("reap-unregistered", None))
        return True


class LifecycleTests(unittest.TestCase):
    @contextmanager
    def setup_service(self, *, lifetime=3600):
        clock = Clock()
        shutdown = Shutdown(clock)
        owner = Owner(clock, shutdown)
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(service, "time", clock))
            stack.enter_context(mock.patch.object(service.pilot, "time", clock))
            stack.enter_context(mock.patch.object(service.identity.Endpoint, "check"))
            contract = service.Contract.begin(clock.timestamp(lifetime), input_price="0.10", output_price="0.20")
            inputs = service.Inputs(Path("/unused-private-snapshots"), {})
            stack.enter_context(mock.patch.object(inputs, "check"))
            supervisor = service.Supervisor(contract, inputs, owner, shutdown)
            yield supervisor, owner, clock, shutdown

    def assert_clean(self, result, owner):
        self.assertTrue(result["cleanup_confirmed"])
        self.assertTrue(all(child.poll() is not None for child in owner.children.values()))
        self.assertNotIn("PRIVATE_CANARY", json.dumps(result))
        self.assertFalse(any(result[name] for name in
            ("deployable", "hardware_validated", "tools", "reasoning", "zdr", "confidential", "e2ee")))

    def test_late_readiness_821_seconds_does_not_inherit_qwen_90(self):
        with self.setup_service() as (supervisor, owner, clock, _):
            owner.startup_seconds = 821
            original = supervisor.contract
            result = supervisor.run()
            self.assertEqual(result["status"], "retired")
            self.assertTrue(result["ready"])
            self.assertIs(supervisor.contract, original)
            self.assertTrue(all(contract is original for contract in owner.launches))
            self.assertEqual([event for event in owner.events if event[0] in ("drain", "reap")],
                             [("drain", "tls"), ("reap", "tls"), ("drain", "guard"),
                              ("reap", "guard"), ("reap", "engine")])
            self.assertLessEqual(clock.mono, original.hard_monotonic)
            self.assert_clean(result, owner)

    def test_startup_deadline_and_lifetime_reserve_both_fail_closed(self):
        for lifetime, startup in ((3600, 1800), (service.CLEANUP_RESERVE + 100, 100)):
            with self.subTest(lifetime=lifetime), self.setup_service(lifetime=lifetime) as (supervisor, owner, _, _):
                owner.startup_seconds = startup
                result = supervisor.run()
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["reason"], "deadline")
                self.assertEqual(list(owner.children), ["engine"])
                self.assertFalse(result["ready"])
                self.assert_clean(result, owner)

    def test_immutable_cutoff_survives_restart_wall_rollback_and_readiness(self):
        with self.setup_service() as (supervisor, owner, clock, shutdown):
            contract = supervisor.contract
            with self.assertRaises(FrozenInstanceError):
                contract.hard_monotonic += 10
            clock.wall += 600
            shortened = supervisor.cutoff()
            clock.wall -= 1200
            self.assertEqual(supervisor.cutoff(), shortened)
            restarted = service.Contract.begin(contract.hard_stop_at, input_price="0.10", output_price="0.20")
            self.assertEqual(restarted.hard_wall, contract.hard_wall)
            self.assertEqual(restarted.admission_stop_at, contract.admission_stop_at)
            owner.startup_seconds = 821
            original = owner.probe
            def probe(role, *args):
                observed = original(role, *args)
                if role == "tls":
                    shutdown.retire = shortened - service.CLEANUP_RESERVE
                return observed
            owner.probe = probe
            self.assertEqual(supervisor.run()["status"], "retired")
            self.assertEqual(supervisor.cutoff(), shortened)
            with self.assertRaises(service.Rejected):
                supervisor.run()
            self.assertEqual(len(owner.children), 3)

    def test_preparation_and_mount_check_time_counts_before_spawn(self):
        with self.setup_service(lifetime=service.CLEANUP_RESERVE + 5) as (supervisor, owner, clock, _):
            owner.check_mounts = lambda deadline: clock.advance(5)
            result = supervisor.run()
            self.assertEqual(result["reason"], "deadline")
            self.assertEqual(owner.children, {})
            self.assert_clean(result, owner)

    def test_startup_budget_is_shared_across_all_children(self):
        with self.setup_service() as (supervisor, owner, clock, _):
            owner.startup_seconds = 1000
            original = owner.start
            def start(role, *args):
                original(role, *args)
                if role == "guard":
                    clock.advance(800)
            owner.start = start
            result = supervisor.run()
            self.assertEqual(result["reason"], "deadline")
            self.assertFalse(result["ready"])
            self.assertEqual(list(owner.children), ["engine", "guard"])
            self.assert_clean(result, owner)

    def test_pending_readiness_never_renews_deadline(self):
        with self.setup_service(lifetime=service.CLEANUP_RESERVE + 1) as (supervisor, owner, _, _):
            deadlines = []
            def pending(role, child, deadline):
                deadlines.append(deadline)
                return None
            owner.probe = pending
            result = supervisor.run()
            self.assertEqual(result["reason"], "deadline")
            self.assertGreater(len(deadlines), 1)
            self.assertEqual(set(deadlines), {supervisor.contract.startup_deadline})
            self.assertEqual(list(owner.children), ["engine"])
            self.assert_clean(result, owner)

    def test_wrong_canonical_id_context_memory_swap_and_listener_block_guard(self):
        with self.setup_service() as (supervisor, owner, _, _):
            base = owner.probe("engine", Child(1000), supervisor.contract.startup_deadline)
        changes = [dict(models_json=json.dumps({"data": [{"id": value}]}).encode())
                   for value in (service.models.select("uncensored")["served_model_name"], "wrong/model")]
        changes += [dict(models_json=b'{"data":[]}'), dict(models_json=b'{"data":[],"data":[]}'),
                    dict(context_tokens=4096), dict(memory_bytes=1024 << 30), dict(swap_bytes=1),
                    dict(context_tokens=True), dict(endpoint=endpoint(12345))]
        changes += [dict(models_json=json.dumps({"data": [{"id": service.MODEL,
                     "max_model_len": value}]}).encode()) for value in (None, True, 4096, "8192")]
        changes.append(dict(models_json=json.dumps({"data": [{"id": service.MODEL}]}).encode()))
        wrong_port = endpoint(1000)
        wrong_port.port = 8001
        changes.append(dict(endpoint=wrong_port))
        for change in changes:
            with self.subTest(change=tuple(change)), self.setup_service() as (supervisor, owner, _, _):
                owner.observation = replace(base, **change)
                result = supervisor.run()
                self.assertEqual(result["status"], "failed")
                self.assertEqual(list(owner.children), ["engine"])
                self.assert_clean(result, owner)

    def test_owned_identity_change_while_starting_guard_prevents_tls(self):
        with self.setup_service() as (supervisor, owner, _, _):
            original = owner.start
            def start(role, *args):
                original(role, *args)
                if role == "guard":
                    owner.observation.endpoint.expected["start_ticks"] += 1
            owner.start = start
            result = supervisor.run()
            self.assertEqual(result["reason"], "identity")
            self.assertEqual(list(owner.children), ["engine", "guard"])
            self.assert_clean(result, owner)

    def test_child_exit_including_zero_during_startup_is_not_readiness(self):
        for code in (0, 1, -6):
            with self.subTest(code=code), self.setup_service() as (supervisor, owner, _, _):
                original = owner.probe
                def probe(role, child, deadline):
                    observed = original(role, child, deadline)
                    child.returncode = code
                    return observed
                owner.probe = probe
                result = supervisor.run()
                self.assertEqual(result["reason"], "child-exited")
                self.assertEqual(list(owner.children), ["engine"])
                self.assert_clean(result, owner)

    def test_serving_child_exit_closes_remaining_owned_children(self):
        for failed in service.ROLES:
            with self.subTest(role=failed), self.setup_service() as (supervisor, owner, _, shutdown):
                def wait(seconds):
                    owner.children[failed].returncode = 1
                shutdown.wait = wait
                result = supervisor.run()
                self.assertEqual(result["reason"], "child-exited")
                self.assertEqual(result["status"], "failed")
                self.assertTrue(result["ready"])
                self.assert_clean(result, owner)

    def test_signal_cancellation_at_each_start_cleans_registered_children(self):
        for interrupted in service.ROLES:
            with self.subTest(role=interrupted), self.setup_service() as (supervisor, owner, _, _):
                original = owner.start
                def start(role, *args):
                    original(role, *args)
                    if role == interrupted:
                        raise KeyboardInterrupt("PRIVATE_CANARY")
                owner.start = start
                result = supervisor.run()
                self.assertEqual(result["status"], "cancelled")
                self.assertFalse(result["ready"])
                self.assertEqual(list(owner.children), list(service.ROLES[:service.ROLES.index(interrupted) + 1]))
                self.assert_clean(result, owner)

    def test_shutdown_before_start_or_during_readiness_never_opens_next_layer(self):
        for before in (True, False):
            with self.subTest(before=before), self.setup_service() as (supervisor, owner, _, shutdown):
                shutdown.stopped = before
                original = owner.probe
                def probe(*args):
                    result = original(*args)
                    shutdown.stopped = True
                    return result
                owner.probe = probe
                result = supervisor.run()
                self.assertEqual(result["status"], "cancelled")
                self.assertEqual(len(owner.children), 0 if before else 1)
                self.assert_clean(result, owner)

    def test_failed_partial_spawn_runs_orphan_reaper_and_stays_failed(self):
        for registered in (True, False):
            with self.subTest(registered=registered), self.setup_service() as (supervisor, owner, _, _):
                original = owner.start
                def start(role, *args):
                    if registered:
                        original(role, *args)
                    raise OSError("PRIVATE_CANARY")
                owner.start = start
                result = supervisor.run()
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["reason"], "owner-failed")
                self.assertIn(("reap-unregistered", None), owner.events)
                self.assert_clean(result, owner)

    def test_failed_drain_and_denied_reap_cannot_skip_later_children(self):
        for operation in ("drain", "reap", "reap_unregistered"):
            with self.subTest(operation=operation), self.setup_service() as (supervisor, owner, _, _):
                original = getattr(owner, operation)
                def failed(*args):
                    original(*args)
                    raise PermissionError("PRIVATE_CANARY")
                setattr(owner, operation, failed)
                result = supervisor.run()
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["cleanup_confirmed"], operation == "drain")
                self.assertEqual([role for event, role in owner.events if event == "reap"], ["tls", "guard", "engine"])
                self.assertNotIn("PRIVATE_CANARY", json.dumps(result))

    def test_false_graceful_claim_and_nonzero_exit_do_not_pass(self):
        for code in (None, 1):
            with self.subTest(code=code), self.setup_service() as (supervisor, owner, _, _):
                def drain(role, child, deadline):
                    child.returncode = code
                    return True
                owner.drain = drain
                result = supervisor.run()
                self.assertEqual(result["status"], "failed")
                self.assertTrue(result["cleanup_confirmed"])
                self.assertFalse(result["cleanup"][0]["drained"])

    def test_downstream_exit_during_tls_drain_is_sticky_failure(self):
        with self.setup_service() as (supervisor, owner, _, _):
            original = owner.drain
            def drain(role, child, deadline):
                result = original(role, child, deadline)
                if role == "tls":
                    owner.children["engine"].returncode = 1
                return result
            owner.drain = drain
            result = supervisor.run()
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["reason"], "child-exited")
            self.assert_clean(result, owner)

    def test_cleanup_deadlines_are_clipped_and_late_proof_fails(self):
        with self.setup_service() as (supervisor, owner, clock, _):
            deadlines = []
            original = owner.reap
            def reap(role, child, deadline):
                deadlines.append(deadline)
                original(role, child, deadline)
                if role == "tls":
                    clock.advance(service.CLEANUP_RESERVE + 1)
                return True
            owner.reap = reap
            result = supervisor.run()
            self.assertEqual(result["status"], "failed")
            self.assertFalse(result["cleanup_confirmed"])
            self.assertEqual(len(deadlines), 3)
            self.assertTrue(all(value <= supervisor.contract.hard_monotonic for value in deadlines))

    def test_changed_weight_or_executable_mount_blocks_next_start(self):
        with self.setup_service() as (supervisor, owner, _, _):
            original = owner.probe
            def probe(*args):
                value = original(*args)
                owner.mounts_changed = True
                return value
            owner.probe = probe
            result = supervisor.run()
            self.assertEqual(result["status"], "failed")
            self.assertEqual(list(owner.children), ["engine"])
            self.assert_clean(result, owner)

    def test_mounts_require_positive_proof_and_error_categories_are_closed(self):
        for value in (None, False, 1):
            with self.subTest(value=value), self.setup_service() as (supervisor, owner, _, _):
                owner.check_mounts = lambda deadline: value
                result = supervisor.run()
                self.assertEqual(result["reason"], "mounts")
                self.assertEqual(owner.children, {})
                self.assert_clean(result, owner)
        with self.setup_service() as (supervisor, owner, _, _):
            owner.start = mock.Mock(side_effect=service.Rejected("PRIVATE_CANARY"))
            result = supervisor.run()
            self.assertEqual(result["reason"], "owner-failed")
            self.assert_clean(result, owner)

    def test_absolute_lifetime_and_explicit_prices_reject_before_start(self):
        clock = Clock()
        with mock.patch.object(service, "time", clock), mock.patch.object(service.pilot, "time", clock):
            for cutoff in (None, "invalid", clock.timestamp(-1), clock.timestamp(86401),
                           clock.timestamp(service.CLEANUP_RESERVE)):
                with self.subTest(cutoff=cutoff), self.assertRaises(ValueError):
                    service.Contract.begin(cutoff, input_price="0.1", output_price="0.2")
            for price in (None, 1, "-1", "1e3", "0.0000001", "1000000", "PRIVATE_CANARY"):
                for field in ("input_price", "output_price"):
                    prices = {"input_price": "0.1", "output_price": "0.2", field: price}
                    with self.subTest(field=field, price=price), self.assertRaises(service.Rejected):
                        service.Contract.begin(clock.timestamp(3600), **prices)
            cutoff = clock.timestamp(3600)
            service.Contract.begin(cutoff, input_price="0", output_price="0")
            clock.advance(3601)
            with self.assertRaises(ValueError):
                service.Contract.begin(cutoff, input_price="0", output_price="0")

    def test_closed_contract_joins_existing_api_pack_and_lab_pins(self):
        with self.setup_service() as (supervisor, _, _, _):
            contract = supervisor.contract
            self.assertEqual((contract.context_tokens, contract.max_output_tokens, contract.max_inflight), (8192, 256, 1))
            selected = service.models.select("uncensored")
            self.assertEqual(contract.revision, selected["hf_revision"])
            self.assertEqual(contract.manifest_sha256, selected["manifest_sha256"])
            self.assertNotEqual(contract.model, selected["served_model_name"])
            self.assertEqual((contract.memory_bytes, contract.swap_bytes), (512 << 30, 0))
            source = (Path(__file__).parents[1] / "internal/pilotapi/profile.go").read_text()
            self.assertIn('model: "' + contract.model + '"', source)
            self.assertIn("context: 8192, maxOutput: 256, concurrent: 1", source)
            arguments = contract.guard_arguments(supervisor.inputs)
            self.assertEqual(arguments[arguments.index("--profile") + 1], "selected-deepseek")
            self.assertEqual(arguments[arguments.index("--retirement-at") + 1], contract.admission_stop_at)
            self.assertNotIn("--upstream", arguments)
            self.assertNotIn("--model", arguments)


class InputTests(unittest.TestCase):
    @contextmanager
    def prepared(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {name: root / name for name in ("provider", "cert", "key", "api_key")}
            for name, path in paths.items():
                path.write_bytes(b"x" * 64 if name == "api_key" else b"PRIVATE_CANARY")
                path.chmod(0o600)
            with mock.patch.object(service.pilot.ssl, "SSLContext") as tls, \
                    mock.patch.object(service.pilot, "tls_client_context") as trust:
                yield paths, tls, trust

    def test_private_mounts_are_snapshotted_once_and_original_changes_ignored(self):
        with self.prepared() as (paths, tls, trust), service.snapshot_inputs(**paths) as inputs:
            tls.return_value.load_cert_chain.assert_called_once_with(inputs.root / "cert.pem", inputs.root / "key.pem")
            trust.assert_called_once_with(inputs.root / "cert.pem")
            for path in paths.values():
                path.write_bytes(b"replaced")
            inputs.check()
            self.assertEqual((inputs.root / "api-key").read_bytes(), b"x" * 64)
            root = inputs.root
        self.assertFalse(root.exists())

    def test_snapshot_content_permissions_symlink_and_mount_replacement_fail(self):
        for mutation in ("content", "permissions", "symlink", "replace"):
            with self.subTest(mutation=mutation), self.prepared() as (paths, _, _), service.snapshot_inputs(**paths) as inputs:
                path = inputs.root / "api-key"
                if mutation == "content":
                    path.write_bytes(b"y" * 64)
                elif mutation == "permissions":
                    path.chmod(0o644)
                else:
                    path.unlink()
                    if mutation == "symlink":
                        path.symlink_to(paths["api_key"])
                    else:
                        path.write_bytes(b"x" * 64)
                        path.chmod(0o600)
                with self.assertRaises((service.Rejected, ValueError, OSError)):
                    inputs.check()

    def test_public_key_bad_key_and_mismatched_tls_fail_before_yield(self):
        for mutation in ("public", "short", "tls"):
            with self.subTest(mutation=mutation), self.prepared() as (paths, tls, _):
                if mutation == "public":
                    paths["key"].chmod(0o644)
                elif mutation == "short":
                    paths["api_key"].write_bytes(b"short")
                else:
                    tls.return_value.load_cert_chain.side_effect = ValueError("PRIVATE_CANARY")
                with self.assertRaises((service.pilot.pilot.PilotError, ValueError)):
                    with service.snapshot_inputs(**paths):
                        self.fail("invalid inputs admitted")

    def test_owned_endpoint_reuses_parent_namespace_argv_and_listener_checks(self):
        child = Child(1234)
        digest = hashlib.sha256(b"synthetic executable").hexdigest()
        runtime = {"pid": child.pid, "parent": os.getpid(), "start": 1,
                   "namespaces": {"net": 2, "pid": 3, "cgroup": 4}, "boot": digest, "argv": digest}
        facts = {"start_ticks": 1, "netns_inode": 2, "boot_id_sha256": digest, "argv_sha256": digest}
        with mock.patch.object(service.observer, "process", return_value=runtime) as process, \
                mock.patch.object(service.observer, "listener", return_value=5) as listener, \
                mock.patch.object(service.identity, "process_facts", return_value=facts), \
                mock.patch.object(service.identity.Endpoint, "check") as check:
            value = service.owned_endpoint(child, executable_sha256=digest, arguments_sha256=digest, deadline=200)
            self.assertEqual(value.port, 8000)
            self.assertEqual(value.expected["pid"], child.pid)
            self.assertEqual(value.expected["listener_inode"], 5)
            self.assertEqual(process.call_count, 2)
            listener.assert_called_once_with(child.pid, 200)
            check.assert_called_once_with(200)
            runtime["parent"] += 1
            with self.assertRaises(service.Rejected):
                service.owned_endpoint(child, executable_sha256=digest, arguments_sha256=digest, deadline=200)


if __name__ == "__main__":
    unittest.main()
