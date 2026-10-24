# SPDX-License-Identifier: Apache-2.0
"""Bounded selected-scorer attachment; callers must first admit live owners."""

import math
import os
import signal
import stat
import subprocess
import time

import tau_pipe_driver as common
import tau_selected_session as selected

policy, owners = selected.policy, selected.owners
transport, worker, tau, require = common.transport, common.worker, common.tau, common.require
RESERVE = policy.LIMITS["run_seconds"] - policy.LIMITS["trial_seconds"]


def run_owned(command, environment, payload, plan, manifest, deadline, handles):
    """Internal borrowed pipes from the enclosing verified owner launcher.

    This validates transport shape, not provenance. The launcher must admit
    original readiness, retain its handles until the return, and join final
    owner evidence. Only duplicated descriptors transfer to this driver.
    """
    require(type(handles) is dict and set(handles) == {"candidate", "support"})
    policy.transport_manifest(manifest, tau.digest(tau.encoded(manifest)))
    seen, coordinators, blocking = set(), {}, {}
    for value in handles.values():
        require(type(value) is dict and set(value) == {"read_fd", "write_fd", "deadline"})
        require(type(value["deadline"]) in (int, float) and math.isfinite(value["deadline"])
                and RESERVE < value["deadline"] - time.monotonic() <= policy.LIMITS["run_seconds"])
        for key in ("read_fd", "write_fd"):
            fd = value[key]
            require(type(fd) is int and fd > 2 and fd not in seen and stat.S_ISFIFO(os.fstat(fd).st_mode))
            seen.add(fd)
            blocking[fd] = os.get_blocking(fd)
    try:
        for name, value in handles.items():
            read_fd = os.dup(value["read_fd"])
            try:
                write_fd = os.dup(value["write_fd"])
            except BaseException:
                os.close(read_fd)
                raise
            try:
                channel = transport.Channel(read_fd, write_fd)
            except BaseException:
                for fd in (read_fd, write_fd):
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                raise
            try:
                coordinators[name] = owners.Coordinator(channel, manifest["owners"][name], value["deadline"])
            except BaseException:
                channel.close()
                raise
        return run_process(command, environment, payload, plan, manifest, deadline, **coordinators)
    finally:
        for owner in coordinators.values():
            owner.close()
        for fd, mode in blocking.items():
            os.set_blocking(fd, mode)


def accepted(value):
    """Scorer/pipe completion only; enclosing containers must also be clean."""
    row = value["trial"]
    worker.validate_upstream_termination(row["upstream_termination"], evaluated=row["status"] == "evaluated")
    return (value["scorer_protocol_completed"] is True and value["attach_cleanup"] == "reaped"
            and value["scorer_exit_code"] == 0 and value["owners_finished"] is True
            and value["usage_known"] is True and row["status"] == "evaluated"
            and row["usage"] is not None and row["role_requests"]["agent"] > 0
            and row["role_requests"]["simulator"] > 0)


def failure(session, exc):
    if isinstance(exc, subprocess.TimeoutExpired):
        exc = worker.Abort("timeout")
    elif isinstance(exc, OSError):
        exc = worker.Abort("infrastructure_error")
    result = common.failure_result(session, exc)
    result["upstream_termination"] = (session.result["upstream_termination"] if session.result is not None else None)
    return result


def diagnostic(session, *, protocol_completed, attach_cleanup, exit_code, owner_finish):
    result = dict(session.result)
    result["upstream_termination"] = worker.validate_upstream_termination(
        result["upstream_termination"], evaluated=result["status"] == "evaluated")
    if result["usage"] is not None:
        result["usage"] = {**result["usage"], "cost_usd": None}
    result.update(schema="nib.tau-selected-operational-trial/v1", manifest_sha256=session.manifest_sha,
                  selection=dict(policy.support.SELECTION), role="candidate", role_requests=dict(session.bridge.counts),
                  pricing="not-measured", quality_eligible=False, confidential=False, live_admission=False)
    return {"schema": "nib.tau-selected-driver/v1", "trial": result,
            "scorer_protocol_completed": protocol_completed, "attach_cleanup": attach_cleanup,
            "scorer_exit_code": exit_code, "reserved_tokens": session.bridge.budget.tokens,
            "usage_known": session.bridge.usage_known, "quality_accepted": False,
            "owner_finish": owner_finish, "owners_finished": all(row is not None for row in owner_finish.values()),
            "confidential": False, "live_admission": False, "pricing": "not-measured"}


def finish_owners(session, deadline):
    """A closed pipe is not an acknowledged finish; preserve partial evidence."""
    result = {"candidate": None, "support": None}
    status = session.result["status"]
    for name, owner in zip(result, session.owners):
        try:
            require(owner.failed is None and owner.pending is None)
            until = min(deadline, owner.deadline, time.monotonic() + 5)
            result[name] = owner.finish(status, until)
            if status == "evaluated":
                counts = session.bridge.counts
                expected = counts["agent"] if name == "candidate" else counts["simulator"] + counts["evaluator"]
                require(result[name]["completed_requests"] == expected)
        except BaseException as exc:
            if session.result["status"] == "evaluated":
                session.result = failure(session, exc)
    return result


def run_process(command, environment, payload, plan, frozen, deadline, *, candidate, support):
    """Internal only: fixed verified Docker attach argv, never a caller endpoint.

    Finishing owner channels acknowledges the exact request counts before close.
    It does not establish their container lifecycles or final result receipts.
    The enclosing owner must independently inspect/remove every owned container.
    No success here upgrades runtime admission or benchmark qualification.
    """
    start = time.monotonic()
    require(type(deadline) in (int, float) and math.isfinite(deadline)
            and RESERVE < deadline - start <= policy.LIMITS["run_seconds"])
    require(type(plan) is dict and plan.get("schema") == "nib.tau-selected-scorer-plan/v1"
            and plan.get("transport") == "selected-operational-parent-pipe"
            and plan.get("selected_operational") is True
            and tau.encoded(plan.get("selection")) == tau.encoded(policy.support.SELECTION)
            and plan.get("seconds") == policy.LIMITS["run_seconds"])
    policy.validate(frozen["input"], plan["manifest_sha256"])
    frozen = transport.snapshot(frozen, worker.MAX_REQUEST)
    frozen_sha = tau.digest(tau.encoded(frozen))
    policy.transport_manifest(frozen, frozen_sha)
    assertions = common.selected_assertions(payload, plan["selection"])
    require(len(assertions) == 1)
    channel, session, process = None, None, None
    descriptors, completed, cleanup, exit_code = [], False, "not-started", None
    owner_finish = {"candidate": None, "support": None}
    try:
        # Construct the session before starting anything. Exact Coordinator types,
        # fresh sequences and matching readiness bindings are checked there.
        incoming, child_output = os.pipe()
        descriptors.extend((incoming, child_output))
        child_input, outgoing = os.pipe()
        descriptors.extend((child_input, outgoing))
        channel = transport.Channel(incoming, outgoing)
        descriptors.remove(incoming)
        descriptors.remove(outgoing)
        session = selected.Session(channel, frozen, frozen_sha, candidate, support, assertions=assertions)
        session.bridge.budget.trial_deadline = min(session.bridge.budget.trial_deadline, deadline - RESERVE,
                                                  candidate.deadline - RESERVE, support.deadline - RESERVE)
        trial_deadline = session.bridge.budget.trial_deadline
        require(time.monotonic() < trial_deadline)
        with worker.alarm(trial_deadline):
            process = subprocess.Popen(command, env=environment, stdin=child_input, stdout=child_output,
                                       stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)
            cleanup = "unconfirmed"
            for fd in descriptors:
                os.close(fd)
            descriptors.clear()
            while not session.serve_once():
                pass
            # A terminal frame is not enough: an abnormal or hanging exit
            # invalidates its reward without erasing already incurred usage.
            exit_code = process.wait(timeout=max(0.001, min(10, trial_deadline - time.monotonic())))
            require(exit_code == 0)
            completed = True
    except (worker.Abort, Exception, KeyboardInterrupt) as exc:
        if session is None:
            raise ValueError("selected scorer setup rejected") from None
        session.result = failure(session, exc)
    finally:
        try:
            for fd in descriptors:
                os.close(fd)
            if process is not None:
                try:
                    if process.poll() is None:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise worker.Abort("timeout")
                    exit_code = process.wait(timeout=min(10, remaining))
                    cleanup = "reaped"
                except (worker.Abort, OSError, subprocess.TimeoutExpired) as exc:
                    completed = False
                    session.result = failure(session, exc)
            if session is not None and session.result is not None:
                owner_finish = finish_owners(session, deadline)
        finally:
            if session is not None:
                session.close()
            elif channel is not None:
                channel.close()
    return diagnostic(session, protocol_completed=completed, attach_cleanup=cleanup, exit_code=exit_code,
                      owner_finish=owner_finish)


if __name__ == "__main__":
    raise SystemExit("Internal selected driver only; no live owner admission or launch command.")
