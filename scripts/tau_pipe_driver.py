# SPDX-License-Identifier: Apache-2.0
"""Bounded Docker-attach lifecycle; scripted by default, explicit admitted live mode."""

import os
import select
import signal
import stat
import subprocess
import time

import tau_transport as transport
import tau_aggregate as aggregate

worker, tau, require = transport.worker, transport.tau, transport.require
CPU_PROBE_REPORT = b"NIB-TAU-CPU-PROBE "


def validate_support_probe(row):
    followup = type(row) is dict and row.get("schema") == "nib.tau-cpu-identity-probe/v1"
    require(type(row) is dict and set(row) == {"schema", "result", "status", "plan_sha256", "selection", "upstream_revision",
            "observations", "roles", "target", "pricing", "quality_eligible", "confidential", "live_admission"}
            and row["schema"] == ("nib.tau-cpu-identity-probe/v1" if followup else "nib.tau-cpu-probe/v1")
            and row["result"] in ("pass", "fail")
            and row["status"] in worker.STATUSES and row["target"] == "scripted"
            and row["pricing"] == "not-measured"
            and row["upstream_revision"] == tau.REVISION
            and all(row[name] is False for name in ("quality_eligible", "confidential", "live_admission")))
    tau.sha(row["plan_sha256"])
    require(type(row["selection"]) is dict and set(row["selection"]) == {"domain", "task_id", "trial", "seed", "role"}
            and type(row["selection"]["trial"]) is int
            and row["selection"] == {"domain": "retail", "task_id": "2", "trial": 0,
                                     "seed": row["selection"]["seed"], "role": "candidate"})
    tau.integer(row["selection"]["seed"], 0, 2**32 - 1)
    require(not followup or row["selection"]["seed"] == 42)
    require(type(row["roles"]) is dict and set(row["roles"]) in (set(), {"agent", "simulator", "evaluator"}))
    for role, count in row["roles"].items():
        tau.integer(count, 0, (3 if role == "simulator" else 0) if followup else 1)
    require(type(row["observations"]) is list and len(row["observations"]) <= (3 if followup else 2))
    order = ("simulator",) * 3 if followup else ("simulator", "evaluator")
    for index, (role, observation) in enumerate(zip(order, row["observations"])):
        require(type(observation) is dict and set(observation) == {
            "role", "request_sha256", "prompt_tokens", "completion_tokens", "status"} | ({"step", "identity"} if followup else set())
            and observation["role"] == role and observation["status"] in ("pass", "not-completed", "context-exceeded"))
        if followup:
            require(observation["step"] == ("initial", "email", "name_zip")[index])
            facts = observation["identity"]
            require(facts is None or type(facts) is dict and set(facts) == {
                "email_like_literal", "first_name_literal", "last_name_literal", "zip_literal"}
                and all(type(value) is bool for value in facts.values()))
            require(observation["status"] != "pass" or facts is not None)
        tau.sha(observation["request_sha256"])
        if observation["prompt_tokens"] is not None:
            tau.integer(observation["prompt_tokens"], 1, 4096)
        if observation["completion_tokens"] is not None:
            tau.integer(observation["completion_tokens"], 0, 256)
        if observation["status"] == "pass":
            require(observation["prompt_tokens"] is not None and observation["completion_tokens"] is not None
                    and observation["prompt_tokens"] + observation["completion_tokens"] <= 4096)
    if row["result"] == "pass":
        expected = {"agent": 0, "simulator": 3, "evaluator": 0} if followup else {"agent": 1, "simulator": 1, "evaluator": 1}
        require(row["status"] == "evaluated" and row["roles"] == expected
                and len(row["observations"]) == len(order) and all(item["status"] == "pass" for item in row["observations"]))
    return row


def attach_probe_scorer(command, environment, held, deadline, snapshot):
    """The existing sealed-scorer owner supplies its fixed Docker attach argv."""
    require(type(held) is dict and set(held) == {"process", "ready_sha256", "controller_fd"})
    process = held["process"]
    tau.sha(held["ready_sha256"])
    require(isinstance(process, subprocess.Popen) and process.poll() is None
            and type(held["controller_fd"]) is int and held["controller_fd"] >= 0
            and stat.S_ISFIFO(os.fstat(held["controller_fd"]).st_mode))
    scorer, data, observed, failure = None, bytearray(), None, None
    try:
        scorer = subprocess.Popen(command, env=environment, stdin=process.stdout, stdout=process.stdin,
                                  stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)
        while observed is None:
            require(time.monotonic() < deadline and process.poll() is None)
            ready = select.select([process.stderr, held["controller_fd"]], [], [],
                                  min(0.1, max(0, deadline - time.monotonic())))[0]
            require(held["controller_fd"] not in ready)  # EOF or premature release cancels the probe.
            if process.stderr in ready:
                block = os.read(process.stderr.fileno(), 16385 - len(data))
                require(block)
                data.extend(block)
                require(len(data) <= 16384)
                if b"\n" in data:
                    require(data.startswith(CPU_PROBE_REPORT) and data.endswith(b"\n") and data.count(b"\n") == 1)
                    observed = validate_support_probe(worker.decode(bytes(data[len(CPU_PROBE_REPORT):-1])))
            if scorer.poll() is not None and observed is None:
                deadline = min(deadline, time.monotonic() + 5)
        if observed["result"] == "pass":
            require(scorer.wait(timeout=max(0.001, min(10, deadline - time.monotonic()))) == 0)
        return observed
    except BaseException as exc:
        failure = exc
        raise
    finally:
        if scorer is not None:
            try:
                snapshot.cleanup_process_group(scorer)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                # Retain the original protocol/cancellation identity without
                # treating denied signals or an unreaped group as cleanup proof.
                cleanup = ValueError("tau probe scorer cleanup unconfirmed")
                if failure is not None:
                    raise failure from cleanup
                raise cleanup from None


def scripted_manifest(manifest):
    config = manifest["settings"]
    return {"schema": transport.MANIFEST, "purpose": "nonthinking-transport-prerequisite",
            "quality_eligible": False, "benchmark_manifest_sha256": tau.digest(tau.encoded(manifest)),
            "upstream_revision": tau.REVISION, "settings": config,
            "framing": {role: {"reasoning_effort": "none", "parallel_tool_calls": True,
                "served_model": "scripted-" + role, "context_tokens": 1048576,
                "framing_sha256": tau.digest(tau.encoded({"scripted": role})),
                "tokenizer_sha256": config["serving"][role]["tokenizer_sha256"]
                    if role in ("candidate", "reference") else tau.digest(tau.encoded({"synthetic": role}))}
                for role in tau.ROLES}}


def selected_assertions(payload, selection):
    require(selection["domain"] in tau.DOMAINS)
    rows = worker.decode(worker.read(payload / "upstream/data/tau2/domains" /
                                    selection["domain"] / "tasks.json", 16 << 20))
    require(type(rows) is list and 0 < len(rows) <= 1024)
    matches = [row for row in rows if type(row) is dict and row.get("id") == selection["task_id"]]
    require(len(matches) == 1)
    criteria = matches[0].get("evaluation_criteria")
    require(criteria is None or type(criteria) is dict)
    assertions = (criteria or {}).get("nl_assertions")
    if assertions is None:
        assertions = []
    require(type(assertions) is list and all(type(item) is str for item in assertions))
    return transport.snapshot(assertions, worker.MAX_RESPONSE)


class ScriptedSession(transport.ParentSession):
    def prepare_record(self, role, body, deadline):
        framing = self.manifest["framing"][role]
        return {"bounds": {"manifest_sha256": self.manifest_sha, "role": role,
                           "request_sha256": tau.digest(tau.encoded(body)), "prompt_tokens": 1,
                           **{key: framing[key] for key in
                              ("framing_sha256", "tokenizer_sha256", "context_tokens")}}}

    def dispatch(self, category, request, bounds):
        require(self.current is not None and self.bridge.budget.pending is not None)
        body, _ = self.current
        require(request == {"messages": body["messages"], "tools": body.get("tools")}
                and bounds["max_tokens"] == body["max_completion_tokens"] and bounds["num_retries"] == 0)
        self.response_deadline = self.bridge.budget.pending[4]
        return worker.scripted(self.bridge, category, request, bounds)


def diagnostic(session, plan, manifest, *, upstream_executed=True, live_local=False):
    result, bridge = session.result, session.bridge
    require(result is not None)
    row = {key: plan["selection"][key] for key in ("domain", "task_id", "trial", "seed")}
    row.update({key: result[key] for key in ("status", "reward", "elapsed_ms", "requests", "usage")})
    report = {"schema": "nib.tau-trials/v1", "manifest_sha256": plan["manifest_sha256"],
              "role": plan["selection"]["role"],
              "kind": "declared-unverified" if live_local else "synthetic-fixture", "producer": plan["producer"],
              "binding": aggregate.expected_binding(manifest, plan["selection"]["role"]), "trials": [row]}
    aggregate.validate_report(report, manifest, plan["manifest_sha256"], plan["selection"]["role"])
    return {"schema": "nib.tau-live-trial/v1" if live_local else "nib.tau-scripted-trial/v1",
            "stage": "completed", "completed": True,
            "report": report, "accounting": {"role_requests": dict(bridge.counts),
                "reserved_tokens": bridge.budget.tokens, "reserved_cost_usd": format(bridge.budget.spent, "f"),
                "usage_known": bridge.usage_known},
            "quality_accepted": False, "confidential": False, "live_transport": live_local,
            "token_usage": "engine-reported" if live_local else "synthetic-not-tokenized",
            "upstream_executed": upstream_executed}


def failure_result(session, exc):
    bridge = session.bridge
    try:
        bridge.stop(transport.status(exc))
    except worker.Abort:
        pass
    return {"status": bridge.failed, "reward": None,
            "elapsed_ms": min(86400000, max(0, int((bridge.clock() - session.started) * 1000))),
            "requests": bridge.budget.requests, "usage": bridge.usage()}


def run_process(command, environment, payload, plan, manifest, deadline, *, deployment=None):
    """The caller supplies its verified, owned Docker attach command only."""
    require(time.monotonic() < deadline <= time.monotonic() + worker.SECONDS + 1
            and plan["transport"] == "scripted-parent-pipe")
    if deployment is not None:
        import tau_live_identity as live
        require(type(deployment) is live.Deployment)
        deployment.require_target(plan["selection"]["role"])
        deployment.check(deadline)
    frozen = deployment.manifest if deployment is not None else scripted_manifest(manifest)
    assertions = selected_assertions(payload, plan["selection"])
    process, channel, session, result = None, None, None, None
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=environment, bufsize=0, close_fds=True, start_new_session=True)
        channel = transport.Channel(os.dup(process.stdout.fileno()), os.dup(process.stdin.fileno()))
        process.stdout.close()
        process.stdin.close()
        if deployment is None:
            session = ScriptedSession(channel, frozen, tau.digest(tau.encoded(frozen)),
                                      plan["selection"]["role"], [], assertions=assertions)
        else:
            session = deployment.session(channel, plan["selection"]["role"], assertions)
        session.bridge.budget.trial_deadline = min(session.bridge.budget.trial_deadline, deadline)
        while not session.serve_once():
            pass
        # Do not let a worker hang after its terminal acknowledgement. The
        # enclosing runner also inspects and removes the exact owned container.
        require(process.wait(timeout=max(0.001, min(10, deadline - time.monotonic()))) == 0)
        result = diagnostic(session, plan, manifest, live_local=deployment is not None)
    except (worker.Abort, Exception, KeyboardInterrupt) as exc:
        if session is None:
            raise ValueError("tau pipe setup failed") from None
        session.result = failure_result(session, exc)
        result = diagnostic(session, plan, manifest, upstream_executed=False, live_local=deployment is not None)
    finally:
        if session is not None:
            session.close()
        elif channel is not None:
            channel.close()
        if process is not None:
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()
            try:
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired) as exc:
                if session is None:
                    raise ValueError("tau pipe cleanup failed") from None
                session.result = failure_result(session, exc)
                result = diagnostic(session, plan, manifest, upstream_executed=False, live_local=deployment is not None)
    return result
