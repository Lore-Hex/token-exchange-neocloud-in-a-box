#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""One finite ordinary selected service on an explicitly approved IPv4 ingress."""

import sys
from pathlib import Path

ROOT = Path(__file__).absolute().parents[1]
if __name__ == "__main__":
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        sys.exit("Use python3 -I -S -B host/record-selected-service.py")
    sys.path.insert(0, str(ROOT / "scripts"))

from contextlib import contextmanager
import json
import os
import select
import signal
import stat
import subprocess
import time
import uuid

import selected_service_owner as owner

network, smoke, policy = owner.network, owner.smoke, owner.policy
require = owner.require
MONITOR_RESERVE = smoke.HOST_RESERVE


class Cancelled(BaseException):
    """Must escape selectors, which deliberately swallow InterruptedError."""


class GuardCommands(network.Commands):
    """Checks spend a smaller phase budget; cleanup retains the original cutoff."""
    reserve = MONITOR_RESERVE

    def remaining(self):
        return super().remaining() - self.reserve

    @property
    def end(self):
        self.remaining()
        return self.deadline - self.reserve


def report():
    return {**smoke.report(), "schema": "nib.selected-service-owner/v1",
            "tls_publication_requested": False, "private_inputs_removed": False,
            "watchdog_confirmed": False}


def environment():
    return {**network.proof.ENV, "HOME": "/nonexistent",
        "DOCKER_CONFIG": "/nonexistent/nib-lab-docker-config", "DOCKER_HOST": "unix:///var/run/docker.sock"}


def admitted_commands(admitted):
    """Validate the smoke boundary before selecting the closed network tool env."""
    contract, lab, recorder, env, *_ = admitted
    require(env == lab.controlled_env(environment()["DOCKER_HOST"]))
    return network.Commands(recorder, environment(), contract.hard_wall, contract.hard_monotonic)


@contextmanager
def empty_state_directory(root):
    with network.tau_runtime.directory(root) as fd:
        info = os.fstat(fd)
        require(info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700)
        with os.scandir(fd) as entries:
            require(next(entries, None) is None)
        yield fd


def state_directories(root):
    with empty_state_directory(root) as fd:
        for name in ("network", "container"):
            os.mkdir(name, mode=0o700, dir_fd=fd)
        os.fsync(fd)


def read_reservation(root):
    path = Path(root) / "network"
    value = policy.decode(network.tau_runtime.read_file(path / "contract.json", 4096, time.monotonic() + 1))
    selected = network.Network(policy.Policy(**value["network"]["policy"]),
                               value["network"]["subnet"], value["network"]["gateway"])
    journal = network.Journal(path, selected, value["hard_wall"], value["hard_monotonic"],
        lifetime_profile=value.get("lifetime_profile", smoke.service.SMOKE_PROFILE))
    journal.read()
    return journal


def acknowledge(raw):
    require(raw in (b"ready\n", b"serving\n"))
    os.write(sys.stdout.fileno(), raw)


def watchdog(root, life_fd):
    """The only process allowed to release quarantine; never extends the cutoff."""
    selected_owner, reservation, commands = None, None, None
    result = {"result": "fail", "cleanup": None}
    try:
        require(os.getuid() == 0 and sys.platform == "linux" and type(life_fd) is int and life_fd >= 3)
        journal = read_reservation(root)
        _, recorder = smoke.host_recorder()
        commands = GuardCommands(recorder, environment(), journal.contract["hard_wall"],
                                 journal.contract["hard_monotonic"])
        reservation = network.Reservation(commands, journal)
        commands.reserve = 0
        reservation.admit()
        commands.reserve = MONITOR_RESERVE
        daemon = owner.daemon_identity(commands)
        acknowledge(b"ready\n")
        serving = False
        while commands.remaining() > 0:
            ready, _, _ = select.select([life_fd], [], [], min(1, commands.remaining()))
            if ready:
                raw = os.read(life_fd, 8)
                if not raw:
                    break
                require(raw == b"serve\n" and not serving)
                require(owner.daemon_identity(commands, daemon[0]) == daemon)
                selected_owner = owner.Owner(commands, reservation, owner.State(Path(root) / "container"))
                selected_owner.release_for_serving()
                serving = True
                acknowledge(b"serving\n")
            if serving:
                require(owner.daemon_identity(commands, daemon[0]) == daemon)
                selected_owner.fresh(serving=True)
        else:
            raise ValueError("selected owner expiry")
        result["result"] = "pass"
    except BaseException:
        pass
    finally:
        if commands is not None:
            commands.reserve = 0
        try:
            if selected_owner is None and commands is not None and reservation is not None:
                state = owner.State(Path(root) / "container")
                if (state.root / "runtime.json").exists():
                    selected_owner = owner.Owner(commands, reservation, state)
            if selected_owner is not None:
                result["cleanup"] = selected_owner.cleanup()
            elif reservation is not None and "network-intent" in reservation.journal.read():
                # No container intent was published. Still retain quarantine:
                # this does not establish successful completion of the run.
                reservation.cleanup()
        except BaseException:
            pass
        try:
            os.close(life_fd)
        except OSError:
            pass
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result["result"] == "pass" and result["cleanup"] is not None \
        and result["cleanup"]["cleanup_confirmed"] else 1


def wait_ack(monitor, expected, commands):
    raw = bytearray()
    end = time.monotonic() + min(60, commands.remaining() - smoke.HOST_RESERVE)
    while len(raw) < len(expected):
        require(monitor.poll() is None and time.monotonic() < end)
        readable, _, _ = select.select([monitor.stdout], [], [], min(.1, end - time.monotonic()))
        if readable:
            chunk = os.read(monitor.stdout.fileno(), len(expected) - len(raw))
            require(chunk)
            raw.extend(chunk)
    require(bytes(raw) == expected)


def cleanup_record(raw):
    row = policy.decode(raw)
    require(type(row) is dict and set(row) == {"result", "cleanup"} and row["result"] in ("pass", "fail"))
    keys = {"container_absent", "network_absent", "creations_resolved", "quarantine_confirmed",
            "policy_removed", "cleanup_confirmed"}
    cleanup = row["cleanup"]
    require(cleanup is None or (type(cleanup) is dict and set(cleanup) == keys
                                and all(type(value) is bool for value in cleanup.values())))
    if cleanup is not None and cleanup["cleanup_confirmed"]:
        require(all(cleanup.values()))
    return row


class AttachedProgress:
    """Bounded stdout sink for the day protocol; only closed phases reach stderr."""
    def __init__(self, lifetime, prepared_sha256, execution_profile=smoke.service.engine_entry.EAGER_PROFILE):
        self.lifetime, self.prepared = lifetime, prepared_sha256
        self.execution_profile = execution_profile
        smoke.service.engine_entry.execution_limits(execution_profile)
        self.buffer, self.total, self.phases, self.terminal = bytearray(), 0, [], None

    def write(self, chunk):
        require(type(chunk) is bytes and self.terminal is None)
        self.total += len(chunk)
        require(self.total <= 16384)
        self.buffer.extend(chunk)
        while b"\n" in self.buffer:
            raw, _, tail = self.buffer.partition(b"\n")
            self.buffer = bytearray(tail)
            row = policy.decode(bytes(raw))
            require(type(row) is dict and self.terminal is None)
            if row.get("schema") in (smoke.service.PROGRESS_SCHEMA, "nib.selected-service-progress/v2"):
                phase = row.get("phase")
                require(row == smoke.service.progress_record(phase, self.lifetime, self.prepared, self.execution_profile))
                index = len(self.phases)
                require(phase not in self.phases and (phase == "draining" or
                    index < len(smoke.service.PROGRESS_PHASES) and phase == smoke.service.PROGRESS_PHASES[index]))
                require(not self.phases or self.phases[-1] != "draining")
                self.phases.append(phase)
                # Reconstruct, never relay arbitrary container/Docker output.
                print(json.dumps({**smoke.service.progress_record(phase, self.lifetime, self.prepared, self.execution_profile),
                    "observed_at": smoke.service.retirement_at(time.time())}, sort_keys=True),
                    file=sys.stderr, flush=True)
            else:
                require(row.get("schema") == (smoke.service.ORDINARY_SCHEMA
                    if self.execution_profile == smoke.service.engine_entry.EAGER_PROFILE else smoke.service.EXECUTION_SCHEMA))
                self.terminal = bytes(raw)
                require(not self.buffer)
        return len(chunk)

    def finish(self, *, success):
        require(not self.buffer and self.terminal is not None)
        require(not success or self.phases == list(smoke.service.PROGRESS_PHASES))
        return self.terminal


def start_day(commands, command, lifetime, prepared_sha256, execution_profile=smoke.service.engine_entry.EAGER_PROFILE):
    output = AttachedProgress(lifetime, prepared_sha256, execution_profile)
    remaining = commands.remaining() - 120
    require(remaining > 0)
    result = commands.recorder.snapshot.execute_process_group(command, env=commands.env,
        timeout=remaining, stdout_file=output, max_output_bytes=16384, max_stderr_bytes=65536)
    require(type(result.returncode) is int)
    raw = output.finish(success=result.returncode == 0)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, command, output=raw)
    return raw


def runtime_plan(args, admitted, selected, commands, result):
    contract, lab, recorder, _, build, base, image, inputs = admitted
    require(getattr(args, "lifetime_profile", smoke.service.SMOKE_PROFILE) == contract.lifetime_profile)
    require(getattr(args, "execution_profile", smoke.service.engine_entry.EAGER_PROFILE) == contract.execution_profile)
    result["reason"] = "weights"
    weights = smoke.private_path(args.weights)
    smoke.weights_view(lab, weights, min(commands.end - contract.cleanup_reserve,
                                      time.monotonic() + smoke.SECONDS))
    stop_at = smoke.service.retirement_at(contract.hard_wall - smoke.HOST_RESERVE)
    if contract.lifetime_profile == smoke.service.ORDINARY_PROFILE:
        result.update(schema="nib.selected-service-owner/v2", lifetime={
            **smoke.service.lifetime_record(contract.lifetime_profile, contract.hard_wall - smoke.HOST_RESERVE,
                                           contract.execution_profile),
            "owner_hard_stop_at": contract.hard_stop_at})
    result["reason"] = "runtime-intent"
    require(commands.remaining() > smoke.HOST_RESERVE + contract.cleanup_reserve)
    labels = owner.image_labels(image["Config"].get("Labels"))
    def runtime(identifier):
        return {"command": owner.container_command(selected, identifier, build, base, weights, inputs, args, stop_at),
            "network_id": identifier, "image_id": build["image_id"], "entrypoint": image["Config"]["Entrypoint"],
            "image_labels": labels, "weights": str(weights), "inputs": str(inputs)}
    # Exact IDs have fixed width. Reject an oversized private intent before
    # any firewall/network mutation, not after resource creation.
    require(len(policy.encoded(runtime("0" * 64))) + 1 <= owner.State.file_limit("runtime.json"))
    return runtime


def run(args, admitted, selected, result):
    contract, lab, recorder, _, build, base, image, inputs = admitted
    commands = admitted_commands(admitted)
    env = commands.env
    result.update(preflight_pass=False, reason="state-directory")
    root = smoke.private_path(args.state_dir)
    state_directories(root)
    journal = network.Journal(root / "network", selected, contract.hard_wall, contract.hard_monotonic,
                              lifetime_profile=contract.lifetime_profile)
    journal.initialize()
    reservation = network.Reservation(commands, journal)
    result.update(preflight_pass=False, reason="host-identity")
    reservation.admit()
    owner.daemon_identity(commands)
    result["reason"] = "host-network"
    owner.host_target(commands, selected, initial=True)
    runtime = runtime_plan(args, admitted, selected, commands, result)
    result["preflight_pass"] = True
    reader, writer = os.pipe()
    monitor, selected_owner, accepted, monitor_reaped = None, None, False, False
    try:
        monitor = subprocess.Popen([sys.executable, "-I", "-S", "-B", str(Path(__file__).absolute()),
            "--watchdog", str(root), str(reader)], env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, pass_fds=(reader,), start_new_session=True)
        wait_ack(monitor, b"ready\n", commands)
        result.update(reason="network", cleanup="unconfirmed")
        # Recheck immediately before the first published-tuple drop insertion.
        owner.host_target(commands, selected, initial=True)
        identifier = reservation.reserve()
        state = owner.State(root / "container")
        state.initialize(owner.owner_intent(journal), runtime(identifier))
        selected_owner = owner.Owner(commands, reservation, state)
        result["reason"] = "container"
        container = selected_owner.create()
        result.update(container_created=True, tls_publication_requested=True)
        require(monitor.poll() is None)
        os.write(writer, b"serve\n")
        wait_ack(monitor, b"serving\n", commands)
        result.update(reason="lifecycle", runtime_started=None)
        join = {name: getattr(args, name, False) for name in (
            "inference_smoke", "conformance_smoke", "protocol_smoke", "tool_smoke")}
        if getattr(args, "long_qualification", False):
            join["long_qualification"] = True
        join["prepared_sha256"] = build["prepared_sha256"]
        if contract.execution_profile != smoke.service.engine_entry.EAGER_PROFILE:
            join["execution_profile"] = contract.execution_profile
        if contract.lifetime_profile == smoke.service.ORDINARY_PROFILE:
            join["lifetime"] = smoke.service.lifetime_record(contract.lifetime_profile,
                contract.hard_wall - smoke.HOST_RESERVE, contract.execution_profile)
        start = ["docker", "container", "start", "--attach", container]
        try:
            if contract.lifetime_profile == smoke.service.ORDINARY_PROFILE:
                raw = start_day(commands, start, join["lifetime"], build["prepared_sha256"],
                                **({"execution_profile": contract.execution_profile}
                                   if contract.execution_profile != smoke.service.engine_entry.EAGER_PROFILE else {}))
            else:
                raw = commands.call(start, seconds=commands.remaining() - 120, limit=16384)
        except subprocess.CalledProcessError as error:
            if error.cmd == start and type(error.returncode) is int and error.returncode == 1:
                result["owner_failure"] = smoke.lifecycle_failure(error.stdout, **join)
            raise
        result["lifecycle"] = smoke.lifecycle(raw, **join)
        info = selected_owner.fresh(serving=True)
        require(info["State"]["Status"] == "exited" and type(info["State"]["ExitCode"]) is int
                and info["State"]["ExitCode"] == 0 and monitor.poll() is None)
        result["runtime_started"] = True
        accepted = True
    except BaseException:
        pass
    finally:
        os.close(reader)
        os.close(writer)
        if monitor is not None:
            try:
                raw, _ = recorder.snapshot.bounded_output(monitor, ["selected-owner-watchdog"],
                    max(0, commands.remaining() - smoke.CLIENT_REAP), None, 4096, 4096)
                observed = cleanup_record(raw)
                result["owner_cleanup"] = observed["cleanup"]
                expected = 0 if observed["result"] == "pass" and observed["cleanup"] is not None \
                    and observed["cleanup"]["cleanup_confirmed"] else 1
                require(type(monitor.returncode) is int and monitor.returncode == expected)
                if observed["cleanup"] is not None and observed["cleanup"]["cleanup_confirmed"]:
                    result.update(watchdog_confirmed=observed["result"] == "pass", cleanup="confirmed")
            except BaseException:
                result["cleanup"] = "unconfirmed"
            finally:
                try:
                    recorder.snapshot.cleanup_process_group(monitor)
                    monitor_reaped = True
                except BaseException:
                    result.update(watchdog_confirmed=False, cleanup="unconfirmed")
        if result["cleanup"] != "confirmed" and monitor_reaped and selected_owner is not None:
            # The watchdog has been reaped before this fallback. It can no
            # longer race a serving transition against fresh quarantine.
            result["fallback_cleanup"] = selected_owner.cleanup()
        if accepted and result["watchdog_confirmed"] and commands.remaining() > 0:
            result.update(result="pass", status="PASS", reason="completed")


def preflight(args):
    result = report()
    try:
        require(args.docker_host == environment()["DOCKER_HOST"])
        token = uuid.uuid4().hex
        selected = network.Network(policy.Policy(token, "nib" + token[:12], args.ingress,
            args.address, args.published_address, args.published_port, "1.1.6"), args.subnet, args.gateway)
        profile = getattr(args, "lifetime_profile", smoke.service.SMOKE_PROFILE)
        smoke.service.lifetime_seconds(profile)
        with smoke.admitted_runtime(args, result,
                **({"lifetime_profile": profile} if profile != smoke.service.SMOKE_PROFILE else {})) as admitted:
            try:
                if args.run:
                    run(args, admitted, selected, result)
                else:
                    result.update(preflight_pass=False, reason="state-directory")
                    with empty_state_directory(smoke.private_path(args.state_dir)):
                        pass
                    result.update(preflight_pass=False, reason="host-identity")
                    contract = admitted[0]
                    commands = admitted_commands(admitted)
                    journal = network.Journal(Path(args.state_dir) / "network", selected,
                        contract.hard_wall, contract.hard_monotonic, lifetime_profile=contract.lifetime_profile)
                    network.Reservation(commands, journal).admit()
                    owner.daemon_identity(commands)
                    result["reason"] = "host-network"
                    owner.host_target(commands, selected, initial=True)
                    runtime_plan(args, admitted, selected, commands, result)
                    result.update(preflight_pass=True, reason="not-run")
            except BaseException:
                result.update(result="fail", status="BLOCKED")
        result["private_inputs_removed"] = True
    except BaseException:
        result.update(result="fail", status="BLOCKED")
        if result["reason"] == "completed":
            result["reason"] = "private-input-cleanup"
    return result


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    result = report()
    try:
        parser = smoke.QuietParser(description=__doc__, allow_abbrev=False)
        modes = parser.add_mutually_exclusive_group(required=True)
        modes.add_argument("--run", action="store_true")
        modes.add_argument("--inspect", action="store_true")
        probes = parser.add_mutually_exclusive_group()
        for name in ("inference", "conformance", "protocol", "tool"):
            probes.add_argument("--" + name + "-smoke", action="store_true")
        probes.add_argument("--long-qualification", action="store_true")
        for name in ("build-report", "build-sha256", "preparation", "preparation-sha256", "docker-host",
                     "provider-config", "api-key-file", "cert", "key", "hostname", "weights",
                     "input-price", "output-price", "hard-stop-at", "state-dir", "ingress",
                     "address", "published-address", "subnet", "gateway"):
            parser.add_argument("--" + name, required=True)
        parser.add_argument("--published-port", type=int, required=True)
        parser.add_argument("--lifetime-profile", default=smoke.service.SMOKE_PROFILE)
        parser.add_argument("--execution-profile", default=smoke.service.engine_entry.EAGER_PROFILE)
        cancelled = False
        def cancel(*_):
            nonlocal cancelled
            if not cancelled:
                cancelled = True
                raise Cancelled("selected owner cancelled")
        handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            for sig in handlers:
                signal.signal(sig, cancel)
            if argv[:1] == ["--watchdog"]:
                require(len(argv) == 3)
                return watchdog(argv[1], int(argv[2]))
            result = preflight(parser.parse_args(argv))
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
    except BaseException:
        pass
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result["result"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
