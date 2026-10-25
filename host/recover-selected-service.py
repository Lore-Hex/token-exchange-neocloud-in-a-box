#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Remove only a resolved, empty pre-container reservation before its cutoff."""

import sys
from pathlib import Path

ROOT = Path(__file__).absolute().parents[1]
if __name__ == "__main__":
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        sys.exit("Use python3 -I -S -B host/recover-selected-service.py")
    sys.path.insert(0, str(ROOT / "scripts"))

import datetime
import fcntl
import hashlib
import json
import os
import signal
import stat
import time

import selected_service_owner as owner

network, policy, smoke = owner.network, owner.policy, owner.smoke
host = network.source_module("selected_recovery_host", "host/record-selected-service.py")
require = owner.require
BUDGET = 240


class Commands(network.Commands):
    def __init__(self, recorder, journal, end):
        super().__init__(recorder, host.environment(), journal.contract["hard_wall"],
                         journal.contract["hard_monotonic"])
        self.recovery_end = end

    def remaining(self):
        return min(super().remaining(), self.recovery_end - time.monotonic())

    @property
    def end(self):
        self.remaining()
        return min(self.deadline, self.recovery_end)


def report():
    return {"schema": "nib.selected-precontainer-recovery/v1", "result": "fail",
        "reason": "contract", "budget_seconds": BUDGET, "hard_stop_at": None,
        "contract_sha256": None, "container_absent": False, "network_absent": False,
        "creations_resolved": False, "quarantine_confirmed": False,
        "policy_removed": False, "quarantine_removed": False, "cleanup_confirmed": False,
        "runtime_started": False, "resources_created": False, "original_run_pass": False,
        "deployable": False, "hardware_validated": False, "confidential": False}


def empty_state(root):
    """No runtime/contract/intent file is recoverable by this command."""
    with network.tau_runtime.directory(root) as fd:
        info = os.fstat(fd)
        require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)
        with os.scandir(fd) as entries:
            names = set()
            for entry in entries:
                require(len(names) < 2)
                names.add(entry.name)
        require(names == {"network", "container"})
        with network.tau_runtime.directory(root / "container") as container:
            info = os.fstat(container)
            require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)
            with os.scandir(container) as entries:
                require(next(entries, None) is None)


def no_owner_process(commands):
    """Reject an active finite owner/watchdog; never signal or kill a process."""
    end = min(commands.end, time.monotonic() + 5)
    with os.scandir("/proc") as entries:
        for count, entry in enumerate(entries):
            require(count < 16384 and time.monotonic() < end)
            if not entry.name.isdecimal():
                continue
            try:
                # Kernel threads and zombies have an empty cmdline, unlike the
                # existing runtime observer's nonempty API-process contract.
                raw = bytearray()
                with open(Path(entry.path) / "cmdline", "rb", buffering=0) as stream:
                    while True:
                        block = stream.read(min(65536, 65537 - len(raw)))
                        require(time.monotonic() < end)
                        if not block:
                            break
                        raw.extend(block)
                        require(len(raw) <= 65536)
            except (FileNotFoundError, ProcessLookupError):
                continue
            require(all(arg.rsplit(b"/", 1)[-1] != b"record-selected-service.py"
                        for arg in bytes(raw).split(b"\0")))


def no_containers(commands):
    # Preserve the finite owner's otherwise-idle daemon boundary. An all-state
    # empty inventory also excludes renamed/owned and stopped pending endpoints;
    # Docker's network filter alone is not a stopped-container absence proof.
    require(not commands.call(["docker", "container", "ls", "--all", "--quiet",
                               "--no-trunc"], limit=262144).strip())


def network_absent(reservation, identifier):
    require(not reservation.inventory())
    require(not reservation.commands.call(["docker", "network", "ls", "--no-trunc", "--quiet",
        "--filter", "id=" + identifier], limit=4096).strip())


def tables_absent(commands, selected, tables):
    # Reuse the exact canonical verifier with an empty expected object set.
    raw = commands.rules(selected)
    for table in tables:
        policy.verify_objects(raw, table, selected.policy.nft_version, [])


def recover(root, journal, commands, result):
    reservation = network.Reservation(commands, journal)
    result["reason"] = "host-identity"
    reservation.admit()
    daemon = owner.daemon_identity(commands)
    result["reason"] = "pre-container"
    events = journal.read()
    require(list(events) == list(network.EVENTS[:-1]))
    identifier = events["network-resolved"]["id"]

    def absent():
        require(commands.remaining() > smoke.CLIENT_REAP)
        empty_state(root)
        no_owner_process(commands)
        require(owner.daemon_identity(commands, daemon[0]) == daemon)
        no_containers(commands)

    absent()
    result["reason"] = "network"
    # Initially require the known empty object, not merely its apparent absence.
    rows = reservation.inventory()
    require(len(rows) == 1 and rows[0]["ID"] == identifier)
    reservation.inspect(identifier)
    reservation.protected()
    result.update(container_absent=True, creations_resolved=True, quarantine_confirmed=True)
    absent()
    cleaned = reservation.cleanup()
    require(cleaned["network_cleanup_confirmed"] is True and cleaned["create_resolved"] is True)
    network_absent(reservation, identifier)
    result["network_absent"] = True
    result["reason"] = "policy"
    absent()
    network_absent(reservation, identifier)
    reservation.protected()
    commands.apply(journal.network.policy.remove_batch(containers_absent=True, creations_resolved=True))
    tables_absent(commands, journal.network, [journal.network.policy.table])
    result["policy_removed"] = True
    result["reason"] = "quarantine"
    absent()
    network_absent(reservation, identifier)
    reservation.quarantined()
    commands.apply(reservation.quarantine.remove_batch(
        containers_absent=True, networks_absent=True, creations_resolved=True))
    tables_absent(commands, journal.network, [journal.network.policy.table, reservation.quarantine.table])
    absent()
    network_absent(reservation, identifier)
    require(commands.remaining() > 0)
    result.update(result="pass", reason="completed", quarantine_removed=True, cleanup_confirmed=True)


def execute(args, result, end):
    require(os.getuid() == 0 and sys.platform == "linux")
    root = smoke.private_path(args.state_dir)
    cutoff = datetime.datetime.strptime(args.hard_stop_at, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=datetime.timezone.utc)
    require(cutoff.strftime("%Y-%m-%dT%H:%M:%SZ") == args.hard_stop_at)
    # No new journal or lock file. Serialize recovery with the existing directory.
    with network.tau_runtime.directory(root) as fd:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        empty_state(root)
        journal = host.read_reservation(root)
        require(journal.contract["hard_wall"] == cutoff.timestamp())
        result.update(hard_stop_at=args.hard_stop_at,
                      contract_sha256=hashlib.sha256(policy.encoded(journal.contract)).hexdigest())
        _, recorder = smoke.host_recorder()
        commands = Commands(recorder, journal, end)
        recover(root, journal, commands, result)


def main(argv=None):
    result = report()
    end = time.monotonic() + BUDGET
    handlers = {}
    try:
        parser = smoke.QuietParser(description=__doc__, allow_abbrev=False)
        parser.add_argument("--cleanup-only", action="store_true", required=True)
        parser.add_argument("--state-dir", required=True)
        parser.add_argument("--hard-stop-at", required=True)
        args = parser.parse_args(argv)
        def cancel(*_):
            raise InterruptedError("selected recovery cancelled")
        for sig in (signal.SIGINT, signal.SIGTERM):
            handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, cancel)
        execute(args, result, end)
    except BaseException:
        pass
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result["result"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
