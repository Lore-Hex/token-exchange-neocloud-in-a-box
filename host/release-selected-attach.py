#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Release one cancelled legacy owner's exact Docker attach reader, never its owner."""

import sys
from pathlib import Path

ROOT = Path(__file__).absolute().parents[1]
if __name__ == "__main__":
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        sys.exit("Use python3 -I -S -B host/release-selected-attach.py")
    sys.path.insert(0, str(ROOT / "scripts"))

from contextlib import ExitStack
import datetime
import json
import os
import re
import select
import signal
import stat
import time

import selected_service_owner as owner

network, smoke = owner.network, owner.smoke
cancel = network.source_module("attach_cancel_identity", "host/cancel-selected-service.py")
host = network.source_module("attach_owner_validation", "host/record-selected-service.py")
require, encoded, digest = cancel.require, cancel.encoded, cancel.digest
LEGACY_REVISION = "4e0bfd7052d9a1e74d9fa83dda54272efd425351"
VENUE = "scripts/fixtures/selected-nat-ubuntu26.json"
DOCKER = Path("/usr/bin/docker")
ADMISSION_SECONDS, OBSERVE_SECONDS = 60, 240


class Cancelled(BaseException):
    """Cancellation cannot be mistaken for a selector's recoverable EINTR."""


def argument(value, flag):
    argv = value["argv"]
    require(argv.count(flag) == 1 and argv.index(flag) + 1 < len(argv))
    return argv[argv.index(flag) + 1]


def prior_receipt(raw, value, expectation_sha):
    row = json.loads(raw, object_pairs_hook=cancel.unique_object)
    template = cancel.report()
    require(type(row) is dict and set(row) == set(template))
    for key, expected in {"schema": template["schema"], "outcome": "signal-requested",
            "stage": "completed", "budget_seconds": cancel.SECONDS,
            "expectation_sha256": expectation_sha, "source_revision": LEGACY_REVISION,
            "identity_verified": True, "pidfd_bound": True, "signal_sent": True,
            "signal_may_have_been_sent": True, "original_launch_identity_proven": False,
            "owner_exit_observed": False, "cleanup_confirmed": False,
            "original_run_pass": False, "soak_validated": False}.items():
        require(type(row[key]) is type(expected) and row[key] == expected)
    require(value["source_revision"] == LEGACY_REVISION and value["stdout"] == value["stderr"])
    require(argument(value, "--lifetime-profile") == smoke.service.ORDINARY_PROFILE)
    require(not {"--inference-smoke", "--conformance-smoke", "--protocol-smoke", "--tool-smoke"}
            .intersection(value["argv"]))
    for key in ("started_at", "signal_intent_at", "finished_at"):
        require(type(row[key]) is str)
        datetime.datetime.strptime(row[key], "%Y-%m-%dT%H:%M:%SZ")
    require(row["started_at"] <= row["signal_intent_at"] <= row["finished_at"] <= cancel.now())
    require(type(row["helper_sources"]) is dict and set(row["helper_sources"]) == set(template["helper_sources"]))
    for sha in (row["source_closure_sha256"], *row["helper_sources"].values()):
        require(type(sha) is str and re.fullmatch("[0-9a-f]{64}", sha))
    require(type(row["current_identity"]) is dict)
    return row


def journals(value, end):
    """Read the existing FD-checked state; never create or repair a journal."""
    root = cancel.path(value["state_dir"])
    with cancel.directory(root, private=True) as fd, os.scandir(fd) as entries:
        names = set()
        for entry in entries:
            require(len(names) < 2 and time.monotonic() < end)
            names.add(entry.name)
        require(names == {"network", "container"})
    journal = host.read_reservation(root)
    events = journal.read()
    require(tuple(events) == network.EVENTS[:-1])
    rows = owner.State(root / "container").read()
    require(set(rows) == owner.FILES and rows["contract.json"] == owner.owner_intent(journal))
    runtime = rows["runtime.json"]
    require(runtime["network_id"] == events["network-resolved"]["id"])
    require(journal.lifetime_profile == smoke.service.ORDINARY_PROFILE)
    require(smoke.service.retirement_at(journal.contract["hard_wall"]) == argument(value, "--hard-stop-at"))
    require(time.monotonic() < min(end, journal.contract["hard_monotonic"])
            and time.time() < journal.contract["hard_wall"])
    # Bind exact bytes and inode/stamps as well as the parsed contracts.
    stamps = {}
    for directory, names in (("network", {"contract.json", *(e + ".json" for e in events)}),
                             ("container", owner.FILES)):
        with cancel.directory(root / directory, private=True) as fd:
            stamps[directory] = cancel.inode(os.fstat(fd))
        for name in sorted(names):
            path = root / directory / name
            raw = cancel.read(path, owner.RUNTIME_LIMIT, end)
            stamps[directory + "/" + name] = (cancel.files.identity(cancel.file_info(path)), digest(raw))
    return rows, journal.contract, stamps


def process_fields(pid, end):
    raw = cancel.kernel(cancel.PROC / str(pid) / "stat", end, 4096)
    require(raw.startswith(str(pid).encode() + b" (") and b") " in raw)
    fields = raw.rsplit(b") ", 1)[1].split()
    require(len(fields) >= 20 and fields[0] not in (b"Z", b"X", b"x"))
    return fields


def pipe_descriptor(process, number, end):
    path = process / "fd" / str(number)
    target = os.readlink(path)
    require(re.fullmatch(r"pipe:\[[0-9]+\]", target))
    info = path.stat()
    require(stat.S_ISFIFO(info.st_mode) and info.st_uid == cancel.ROOT_UID)
    rows = cancel.kernel(process / "fdinfo" / str(number), end, 4096).splitlines()
    flags = [row.split()[1:] for row in rows if row.startswith(b"flags:")]
    require(len(flags) == 1 and len(flags[0]) == 1 and re.fullmatch(b"[0-7]+", flags[0][0]))
    return (info.st_dev, info.st_ino, target, int(flags[0][0], 8) & os.O_ACCMODE)


def child_identity(pid, parent, identifier, end):
    process = cancel.PROC / str(pid)
    command = {"argv": ["docker", "container", "start", "--attach", identifier]}
    require(cancel.kernel(process / "cmdline", end) == cancel.command(command))
    fields = process_fields(pid, end)
    require(int(fields[1]) == parent["pid"] and int(fields[2]) == int(fields[3]) == pid
            and int(fields[19]) >= parent["start_ticks"])
    status = cancel.kernel(process / "status", end).splitlines()
    require([line.split()[1:] for line in status if line.startswith(b"Uid:")] == [[b"0"] * 4])
    environment = cancel.kernel(process / "environ", end)
    require(environment.endswith(b"\0") and sorted(environment[:-1].split(b"\0")) == sorted(
        (key + "=" + item).encode() for key, item in host.environment().items()))
    namespaces = {}
    for name in ("pid", "user", "mnt", "net"):
        info = (process / "ns" / name).stat()
        namespaces[name] = (info.st_dev, info.st_ino)
    require(encoded(namespaces) == encoded(parent["namespaces"]))
    require(os.readlink(process / "exe") == str(DOCKER))
    executable = cancel.file_info(DOCKER)
    require(cancel.inode((process / "exe").stat()) == cancel.inode(executable))
    pin = json.loads((ROOT / VENUE).read_bytes())["files"][str(DOCKER)]
    require(digest(cancel.read(DOCKER, 64 << 20, end)) == pin)
    parent_root = cancel.PROC / str(parent["pid"])
    children = cancel.kernel(parent_root / "task" / str(parent["pid"]) / "children", end).split()
    require(children.count(str(pid).encode()) == 1)
    pipes = [pipe_descriptor(process, n, end) for n in (1, 2)]
    require(pipes[0][:2] != pipes[1][:2] and all(row[-1] == os.O_WRONLY for row in pipes))
    readers = []
    with os.scandir(parent_root / "fd") as entries:
        for count, entry in enumerate(entries):
            require(count < 4096 and time.monotonic() < end and entry.name.isdecimal())
            try:
                target = os.readlink(parent_root / "fd" / entry.name)
                if target in {row[2] for row in pipes}:
                    observed = pipe_descriptor(parent_root, entry.name, end)
                    require(observed[-1] == os.O_RDONLY)
                    readers.append((int(entry.name), observed))
            except FileNotFoundError:
                continue
    require(len(readers) == 2 and sorted(row[1][:3] for row in readers) == sorted(row[:3] for row in pipes))
    return {"pid": pid, "parent_pid": parent["pid"], "start_ticks": int(fields[19]),
        "process_group": pid, "session": pid, "executable": cancel.inode(executable),
        "executable_sha256": pin, "environment_sha256": digest(environment),
        "namespaces": namespaces, "pipes": pipes, "readers": sorted(readers)}


def exited(pidfd):
    poll = select.poll()
    poll.register(pidfd, select.POLLIN)
    rows = poll.poll(0)
    require(all(fd == pidfd and mask & select.POLLIN
                and not mask & ~(select.POLLIN | select.POLLHUP) for fd, mask in rows))
    return bool(rows)


def terminal(raw, value, rows, contract):
    """Only the closed failed-owner envelope is copied, never arbitrary log lines."""
    row = json.loads(raw, object_pairs_hook=cancel.unique_object)
    required = set(host.report()) | {"lifetime", "build_sha256", "preparation_sha256",
                                    "prepared_sha256", "image_id", "owner_cleanup"}
    require(type(row) is dict and required <= set(row) <= required | {"fallback_cleanup"})
    for key in ("hardware_validated", "deployable", "published", "tools", "reasoning", "zdr", "confidential", "e2ee"):
        require(row[key] is False)
    require(row["schema"] == "nib.selected-service-owner/v2" and row["result"] == "fail"
            and row["status"] == "BLOCKED" and row["reason"] == "lifecycle"
            and row["preflight_pass"] is True and row["container_created"] is True
            and row["tls_publication_requested"] is True and row["runtime_started"] is None)
    for key in ("private_inputs_removed", "watchdog_confirmed"):
        require(type(row[key]) is bool)
    require(row["cleanup"] in ("confirmed", "unconfirmed"))
    for name in ("build_sha256", "preparation_sha256"):
        require(row[name] == argument(value, "--" + name.replace("_", "-")))
    require(type(row["prepared_sha256"]) is str and re.fullmatch("[0-9a-f]{64}", row["prepared_sha256"]))
    runtime = rows["runtime.json"]
    entry = runtime["entrypoint"]
    require(entry.count("--prepared-sha256") == 1
            and entry[entry.index("--prepared-sha256") + 1] == row["prepared_sha256"]
            and row["image_id"] == runtime["image_id"])
    lifetime = {**smoke.service.lifetime_record(smoke.service.ORDINARY_PROFILE,
        contract["hard_wall"] - smoke.HOST_RESERVE), "owner_hard_stop_at": argument(value, "--hard-stop-at")}
    require(row["lifetime"] == lifetime)
    for name in ("owner_cleanup", "fallback_cleanup"):
        if name in row:
            host.cleanup_record(encoded({"result": "fail", "cleanup": row[name]}))
    if row["cleanup"] == "confirmed":
        require(row["owner_cleanup"] is not None and row["owner_cleanup"]["cleanup_confirmed"])
    require(not row["watchdog_confirmed"] or row["cleanup"] == "confirmed")
    return row


def preserve(fd, name, raw):
    out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    with os.fdopen(out, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.fsync(fd)


def observe_completion(parent_fd, child_fd, value, parent, rows, contract, result, output, end):
    while time.monotonic() < end:
        result["attach_exit_observed"] = exited(child_fd)
        result["owner_exit_observed"] = exited(parent_fd)
        if result["attach_exit_observed"] and result["owner_exit_observed"]:
            break
        time.sleep(min(.1, max(0, end - time.monotonic())))
    require(result["attach_exit_observed"] and result["owner_exit_observed"] and time.monotonic() < end)
    logfile = cancel.path(value["stdout"])
    require(encoded(cancel.inode(cancel.file_info(logfile))) == encoded(parent["logs"]["stdout"]))
    raw = cancel.read(logfile, cancel.LIMIT, end)
    require(raw.endswith(b"\n"))
    last = raw.splitlines(keepends=True)[-1]
    row = terminal(last, value, rows, contract)
    preserve(output, "owner-terminal.json", last)
    result.update(terminal_sha256=digest(last), outcome="owner-failed", stage="completed",
        owner_reported_cleanup=row["cleanup"] == "confirmed" and row["watchdog_confirmed"]
            and row["private_inputs_removed"])


def report():
    sources = ("host/release-selected-attach.py", "host/cancel-selected-service.py",
        "host/record-selected-service.py", "host/record-selected-service-smoke.py",
        "scripts/selected_service_owner.py", "scripts/selected_service_network.py", VENUE)
    return {"schema": "nib.selected-attach-release/v1", "outcome": "rejected", "stage": "input",
        "started_at": cancel.now(), "finished_at": None, "signal_intent_at": None,
        "admission_seconds": ADMISSION_SECONDS, "observation_seconds": OBSERVE_SECONDS,
        "expectation_sha256": None, "cancel_receipt_sha256": None, "source_revision": LEGACY_REVISION,
        "source_closure_sha256": None, "current_identity": None, "attach_identity": None,
        "state_sha256": None, "identity_verified": False, "pidfds_bound": False,
        "signal_may_have_been_sent": False, "signal_sent": False, "attach_exit_observed": False,
        "owner_exit_observed": False, "terminal_sha256": None, "owner_reported_cleanup": False,
        "cleanup_confirmed": False, "original_run_pass": False, "soak_validated": False,
        "helper_sources": {name: digest((ROOT / name).read_bytes()) for name in sources}}


def request(value, prior, result, output, send, end):
    require(sys.platform == "linux" and os.getuid() == 0)
    with ExitStack() as stack:
        admission = stack.enter_context(ExitStack())
        admission.enter_context(cancel.alarm(end))
        result["stage"] = "source"
        closure, stamps = cancel.source(value, end)
        require(closure == prior["source_closure_sha256"])
        result.update(source_closure_sha256=closure, stage="owner")
        pid = cancel.discover(value, end)
        parent = cancel.observe(pid, value, end)
        require(encoded(parent) == encoded(prior["current_identity"]))
        parent_fd = os.pidfd_open(pid, 0)
        stack.callback(os.close, parent_fd)
        cancel.live(parent_fd)
        result["stage"] = "state"
        state = journals(value, end)
        rows, contract, _ = state
        identifier = rows["container-resolved.json"]["id"]
        expected_child = {"argv": ["docker", "container", "start", "--attach", identifier]}
        child_pid = cancel.discover(expected_child, end)
        child = child_identity(child_pid, parent, identifier, end)
        child_fd = os.pidfd_open(child_pid, 0)
        stack.callback(os.close, child_fd)
        result.update(pidfds_bound=True, stage="recheck")
        def recheck():
            require(cancel.discover(value, end) == pid and cancel.discover(expected_child, end) == child_pid)
            require(cancel.observe(pid, value, end) == parent and journals(value, end) == state)
            require(child_identity(child_pid, parent, identifier, end) == child)
            for name, stamp in stamps.items():
                require(time.monotonic() < end and cancel.files.identity(
                    cancel.file_info(cancel.path(value["source_root"]) / name)) == stamp)
            cancel.live(parent_fd)
            cancel.live(child_fd)
            require(time.monotonic() < end)
        require(cancel.source(value, end) == (closure, stamps))
        recheck()
        result.update(current_identity=parent, attach_identity=child, state_sha256=digest(encoded(state)),
                      identity_verified=True, outcome="identified", stage="identified")
        if not send:
            return
        # Docker v29.1.3 forwards catchable signals during start --attach.
        # SIGKILL only to this pidfd cannot be forwarded to the container.
        result.update(signal_may_have_been_sent=True, signal_intent_at=cancel.now(),
                      outcome="unconfirmed", stage="signal")
        cancel.persist(output, "signal-intent.json", result)
        recheck()
        signal.pidfd_send_signal(child_fd, signal.SIGKILL, None, 0)
        result.update(signal_sent=True, stage="observation")
        # The admission alarm must not cut off observation; no second mutation.
        admission.close()
        observation_end = min(time.monotonic() + OBSERVE_SECONDS, contract["hard_monotonic"],
                              time.monotonic() + contract["hard_wall"] - time.time())
        with cancel.alarm(observation_end):
            observe_completion(parent_fd, child_fd, value, parent, rows, contract, result, output, observation_end)


def main(argv=None):
    result, handlers = report(), {}
    cancelled, restoring = False, False
    def stop(*_):
        nonlocal cancelled
        if not cancelled:
            cancelled = True
            if not restoring:
                raise Cancelled()
    try:
        parser = cancel.QuietParser(description=__doc__, allow_abbrev=False)
        modes = parser.add_mutually_exclusive_group(required=True)
        modes.add_argument("--inspect", action="store_true")
        modes.add_argument("--release", action="store_true")
        for name in ("expectation", "cancel-receipt", "cancel-receipt-sha256", "output-dir"):
            parser.add_argument("--" + name, required=True)
        args = parser.parse_args(argv)
        for sig in (signal.SIGINT, signal.SIGTERM):
            handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, stop)
        end = time.monotonic() + ADMISSION_SECONDS
        with cancel.alarm(end):
            expectation_path, receipt_path = cancel.path(args.expectation), cancel.path(args.cancel_receipt)
            for path in (expectation_path, receipt_path):
                require(stat.S_IMODE(cancel.file_info(path).st_mode) == 0o600)
            raw = cancel.read(expectation_path, cancel.LIMIT, end)
            value = cancel.expectation(raw)
            result["expectation_sha256"] = digest(raw)
            raw = cancel.read(receipt_path, cancel.LIMIT, end)
            require(digest(raw) == args.cancel_receipt_sha256)
            result["cancel_receipt_sha256"] = digest(raw)
            prior = prior_receipt(raw, value, result["expectation_sha256"])
            destination = cancel.path(args.output_dir)
            for key in ("source_root", "state_dir"):
                protected = cancel.path(value[key])
                require(not destination.is_relative_to(protected) and not protected.is_relative_to(destination))
            with cancel.directory(destination.parent, private=True) as parent:
                os.mkdir(destination.name, mode=0o700, dir_fd=parent)
                os.fsync(parent)
        with cancel.directory(destination, private=True) as output:
            cancel.persist(output, "initial.json", result)
            try:
                request(value, prior, result, output, args.release, end)
            except BaseException:
                result["outcome"] = "unconfirmed" if result["signal_may_have_been_sent"] else "rejected"
            result["finished_at"] = cancel.now()
            cancel.persist(output, "receipt.json", result)
    except BaseException:
        result["outcome"] = "unconfirmed" if result["signal_may_have_been_sent"] else "rejected"
    finally:
        restoring = True
        if handlers:
            mask = signal.pthread_sigmask(signal.SIG_BLOCK, set(handlers))
            try:
                for sig, handler in handlers.items():
                    signal.signal(sig, handler)
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, mask)
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result["outcome"] == "identified" or (
        result["outcome"] == "owner-failed" and result["owner_reported_cleanup"]) else 1


if __name__ == "__main__":
    sys.exit(main())
