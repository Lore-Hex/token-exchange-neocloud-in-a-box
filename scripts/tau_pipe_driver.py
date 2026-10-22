# SPDX-License-Identifier: Apache-2.0
"""Bounded Docker-attach pipe driver with scripted replies only, never HTTP."""

import os
import signal
import subprocess
import time

import tau_transport as transport
import tau_aggregate as aggregate

worker, tau, require = transport.worker, transport.tau, transport.require


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


def diagnostic(session, plan, manifest, *, upstream_executed=True):
    result, bridge = session.result, session.bridge
    require(result is not None)
    row = {key: plan["selection"][key] for key in ("domain", "task_id", "trial", "seed")}
    row.update({key: result[key] for key in ("status", "reward", "elapsed_ms", "requests", "usage")})
    report = {"schema": "nib.tau-trials/v1", "manifest_sha256": plan["manifest_sha256"],
              "role": plan["selection"]["role"], "kind": "synthetic-fixture", "producer": plan["producer"],
              "binding": aggregate.expected_binding(manifest, plan["selection"]["role"]), "trials": [row]}
    aggregate.validate_report(report, manifest, plan["manifest_sha256"], plan["selection"]["role"])
    return {"schema": "nib.tau-scripted-trial/v1", "stage": "completed", "completed": True,
            "report": report, "accounting": {"role_requests": dict(bridge.counts),
                "reserved_tokens": bridge.budget.tokens, "reserved_cost_usd": format(bridge.budget.spent, "f"),
                "usage_known": bridge.usage_known},
            "quality_accepted": False, "confidential": False, "live_transport": False,
            "token_usage": "synthetic-not-tokenized", "upstream_executed": upstream_executed}


def failure_result(session, exc):
    bridge = session.bridge
    try:
        bridge.stop(transport.status(exc))
    except worker.Abort:
        pass
    return {"status": bridge.failed, "reward": None,
            "elapsed_ms": min(86400000, max(0, int((bridge.clock() - session.started) * 1000))),
            "requests": bridge.budget.requests, "usage": bridge.usage()}


def run_process(command, environment, payload, plan, manifest, deadline):
    """The caller supplies its verified, owned Docker attach command only."""
    require(time.monotonic() < deadline <= time.monotonic() + worker.SECONDS + 1
            and plan["transport"] == "scripted-parent-pipe")
    frozen = scripted_manifest(manifest)
    assertions = selected_assertions(payload, plan["selection"])
    process, channel, session, result = None, None, None, None
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=environment, bufsize=0, close_fds=True, start_new_session=True)
        channel = transport.Channel(os.dup(process.stdout.fileno()), os.dup(process.stdin.fileno()))
        process.stdout.close()
        process.stdin.close()
        session = ScriptedSession(channel, frozen, tau.digest(tau.encoded(frozen)),
                                  plan["selection"]["role"], [], assertions=assertions)
        session.bridge.budget.trial_deadline = min(session.bridge.budget.trial_deadline, deadline)
        while not session.serve_once():
            pass
        # Do not let a worker hang after its terminal acknowledgement. The
        # enclosing runner also inspects and removes the exact owned container.
        require(process.wait(timeout=max(0.001, min(10, deadline - time.monotonic()))) == 0)
        result = diagnostic(session, plan, manifest)
    except (worker.Abort, Exception, KeyboardInterrupt) as exc:
        if session is None:
            raise ValueError("tau pipe setup failed") from None
        session.result = failure_result(session, exc)
        result = diagnostic(session, plan, manifest, upstream_executed=False)
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
                result = diagnostic(session, plan, manifest, upstream_executed=False)
    return result
