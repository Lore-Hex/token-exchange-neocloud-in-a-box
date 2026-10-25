# SPDX-License-Identifier: Apache-2.0
"""Fixed original-tau support probe; bodies remain transient in private pipes."""

import time

import tau_cpu_parity as cpu
import tau_pipe_driver as driver
import tau_support_probe as policy
import tau_cpu_identity as identity

transport, tau, worker = driver.transport, driver.tau, driver.worker
require = transport.require
ORDER = ("simulator", "candidate", "evaluator")
SECONDS = 300
REPORT = driver.CPU_PROBE_REPORT


class ProbeClient(cpu.MeasuredClient):
    def post(self, path, payload, deadline):
        row = super().post(path, payload, deadline)
        if path == "/tokenize":
            self.token_shape = type(row) is dict and set(row) == {"tokens"}
        return row


def inputs(plan, manifest, assertions, *, fixture=None):
    require(plan.get("support_probe") is True and plan.get("transport") == "scripted-parent-pipe"
            and plan["selection"] == policy.SELECTION
            and type(assertions) is list and len(assertions) == 1 and type(assertions[0]) is str)
    policy.validate(manifest, plan["manifest_sha256"])
    require((fixture is not None) == policy.is_identity(manifest))
    return prepared_input(transport.snapshot({"schema": "nib.tau-cpu-probe-input/v1",
        "plan_sha256": tau.digest(tau.encoded(plan)), "manifest": manifest,
        "selection": plan["selection"], "assertions": assertions,
        **({"identity_fixture": identity.validate_fixture(fixture)} if fixture is not None else {})}, 1 << 20))


def prepared_input(value):
    followup = policy.is_identity(value.get("manifest"))
    cpu.identity.keys(value, ("schema", "plan_sha256", "manifest", "selection", "assertions")
                      + (("identity_fixture",) if followup else ()))
    if followup:
        identity.validate_fixture(value["identity_fixture"])
    require(value["schema"] == "nib.tau-cpu-probe-input/v1")
    tau.sha(value["plan_sha256"])
    policy.validate(value["manifest"])
    require(type(value["selection"].get("trial")) is int
            and type(value["selection"].get("seed")) is int)
    require(value["selection"] == policy.SELECTION
        and type(value["assertions"]) is list and len(value["assertions"]) == 1
        and type(value["assertions"][0]) is str and 0 < len(value["assertions"][0]) <= 16384)
    return value


class ProbeSession(driver.ScriptedSession):
    """Exactly simulator, scripted candidate, evaluator; no reference or quality."""
    def __init__(self, channel, prepared, endpoints, runtimes, deadline):
        require(set(endpoints) == set(runtimes) == {"simulator", "evaluator"})
        prepared_input(prepared)
        manifest = prepared["manifest"]
        self.identity_fixture = prepared.get("identity_fixture")
        self.order = ("simulator",) * 3 if self.identity_fixture is not None else ORDER
        frozen = {"schema": "nib.tau-cpu-support-transport/v1", "input": manifest, "settings": policy.settings(manifest),
                  "framing": {role: {"served_model": "scripted-candidate", "context_tokens": 4096,
                    "parallel_tool_calls": False, "reasoning_effort": "none",
                    "tokenizer_sha256": tau.digest(b"scripted-candidate"),
                    "framing_sha256": tau.digest(b"scripted-candidate")}
                    for role in ("candidate", "simulator", "evaluator")}}
        for role, model in zip(("simulator", "evaluator"), cpu.MODEL_IDS):
            row = runtimes[role]
            require(row["model"] == model and row["process"] == endpoints[role].expected)
        bindings = manifest.get("owner_roles", {"simulator": "simulator", "evaluator": "evaluator"})
        self.simulator_binding = None
        if manifest["schema"] == policy.IDENTITY_17_SCHEMA:
            runtime = runtimes[bindings["simulator"]]
            pinned = cpu.identity.cpu.identity(runtime["model"], deadline)
            require(all(runtime[key] == pinned[key] for key in ("revision", "gguf", "model_files_sha256"))
                    and all(runtime["serving"][key] == pinned[key] for key in ("tokenizer_sha256", "framing_sha256")))
            self.simulator_binding = policy.runtime_binding(runtime)
            frozen["framing"]["evaluator"]["served_model"] = "disabled-evaluator"
        for role, owner_role in bindings.items():
            row, model = runtimes[owner_role], manifest["support"][role]["model"]
            require(row["model"] == model)
            frozen["framing"][role].update(served_model=model, context_tokens=4096, parallel_tool_calls=False,
                tokenizer_sha256=row["serving"]["tokenizer_sha256"], framing_sha256=row["serving"]["framing_sha256"])
        super().__init__(channel, frozen, tau.digest(tau.encoded(frozen)), "candidate", [],
                         assertions=prepared["assertions"], support_probe=True)
        self.bridge.budget.trial_deadline = min(self.bridge.budget.trial_deadline, deadline)
        self.clients = {role: ProbeClient(endpoints[owner_role].port, runtimes[owner_role]["model"], endpoints[owner_role])
                        for role, owner_role in bindings.items()}
        self.observations = []

    def prepare_record(self, role, body, deadline):
        order = getattr(self, "order", ORDER)
        require(self.sequence < len(order) and role == order[self.sequence])
        if role == "candidate":
            return super().prepare_record(role, body, deadline)
        observation = {"role": role, "request_sha256": tau.digest(tau.encoded(body)),
                       "prompt_tokens": None, "completion_tokens": None, "status": "not-completed"}
        if getattr(self, "identity_fixture", None) is not None:
            observation.update(step=identity.STEPS[self.sequence], identity=None)
        self.observations.append(observation)
        client = self.clients[role]
        client.ids, client.token_shape = None, False
        try:
            count = client.count(body, 4096, deadline)
        except worker.Abort as error:
            tokens = client.ids
            if error.status == "protocol_error" and client.token_shape is True and type(tokens) is list and len(tokens) > 4096 \
                    and all(type(token) is int and 0 <= token < 2**31 for token in tokens):
                observation["status"] = "context-exceeded"
                raise worker.Abort("budget_abort") from None
            raise
        finally:
            client.ids = None
        observation["prompt_tokens"] = count
        if count + body["max_completion_tokens"] > 4096:
            observation["status"] = "context-exceeded"
            raise worker.Abort("budget_abort")
        framing = self.manifest["framing"][role]
        return {"bounds": {"manifest_sha256": self.manifest_sha, "role": role,
            "request_sha256": observation["request_sha256"], "prompt_tokens": count,
            **{key: framing[key] for key in ("framing_sha256", "tokenizer_sha256", "context_tokens")}}}

    def dispatch(self, category, request, bounds):
        if category == "agent":
            return super().dispatch(category, request, bounds)
        require(category in self.clients and self.current is not None and self.bridge.budget.pending is not None)
        body, record = self.current
        require(request == {"messages": body["messages"], "tools": body.get("tools")}
                and bounds["max_tokens"] == body["max_completion_tokens"] and bounds["num_retries"] == 0)
        self.response_deadline = self.bridge.budget.pending[4]
        response = self.clients[category].complete(body, record["bounds"]["prompt_tokens"], self.response_deadline)
        self.observations[-1]["completion_tokens"] = response["usage"]["completion_tokens"]
        if self.identity_fixture is not None:
            content = response["choices"][0]["message"].get("content")
            # Invalid content is left to the existing bridge. Do not interrupt
            # its usage settlement with an additional observation parser.
            if type(content) is str and 0 < len(content) <= 1 << 20:
                self.observations[-1]["identity"] = identity.project(content, self.identity_fixture)
        return response


def execute(prepared, endpoints, runtimes, processes, read_fd, write_fd, deadline):
    require(time.monotonic() < deadline <= time.monotonic() + SECONDS + 1)
    channel = transport.Channel(read_fd, write_fd, cancelled=lambda: any(p.poll() is not None for p in processes))
    session = None
    followup = policy.is_identity(prepared.get("manifest"))
    selected_17b = prepared.get("manifest", {}).get("schema") == policy.IDENTITY_17_SCHEMA
    order = ("simulator",) * 3 if followup else ORDER
    report = {"schema": policy.report_schema(prepared.get("manifest", {})), "result": "fail", "status": "protocol_error",
              "plan_sha256": prepared["plan_sha256"], "selection": prepared["selection"],
              "upstream_revision": tau.REVISION, "observations": [], "roles": {},
              "target": "scripted", "pricing": "not-measured", "quality_eligible": False,
              "confidential": False, "live_admission": False}
    if selected_17b:
        report["simulator_binding"] = None
    try:
        session = ProbeSession(channel, prepared, endpoints, runtimes, deadline)
        if selected_17b:
            report["simulator_binding"] = session.simulator_binding
        while True:
            before = session.sequence
            finished = session.serve_once()
            if not finished and before < len(order) and order[before] in ("simulator", "evaluator"):
                session.observations[-1]["status"] = "pass"
            if finished:
                break
        report["status"] = session.result["status"]
        if report["status"] == "evaluated":
            require(session.bridge.counts == ({"agent": 0, "simulator": 3, "evaluator": 0} if followup else
                                              {"agent": 1, "simulator": 1, "evaluator": 1})
                    and len(session.observations) == (3 if followup else 2)
                    and all(row["status"] == "pass" for row in session.observations))
            report["result"] = "pass"
    except BaseException as error:
        report["status"] = transport.status(error)
    finally:
        if session is not None:
            report["observations"] = session.observations
            report["roles"] = dict(session.bridge.counts)
            session.close()
        else:
            channel.close()
    return validate(report)


validate = driver.validate_support_probe
