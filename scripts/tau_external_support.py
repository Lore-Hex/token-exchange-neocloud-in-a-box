# SPDX-License-Identifier: Apache-2.0
"""Source-bound support transport; per-call parity is measured, never assumed."""

from decimal import Decimal
import time

import tau_external as external
import tau_runtime as runtime
import tau_telecom_catalog as catalog
import tau_telecom_projection as projection

worker, require, policy = external.worker, external.require, external.policy
SCHEMA = "nib.tau-source-bound-support/v1"
# Official GPT-4.1 context and standard rates, checked 2026-10-27:
# https://developers.openai.com/api/docs/models/gpt-4.1
# The count API guarantees Responses inputs, not arbitrary Chat equivalence:
# https://developers.openai.com/api/docs/guides/token-counting
SUPPORT_INPUT_CEILING = 1047576


class Source:
    def __init__(self, archive_path, stage, deadline):
        pins_raw = runtime.read_file(runtime.ROOT / runtime.tau.PINS, 1 << 20, deadline)
        require(runtime.digest(pins_raw) == runtime.tau.PINS_SHA)
        pins = runtime.tau.json_value(pins_raw)
        archive = runtime.read_file(archive_path, runtime.ARCHIVE_LIMIT, deadline)
        _, sources = runtime.source_inputs(archive, pins, deadline)
        selection = {"domain": "retail", "task_id": "2", "trial": 0, "seed": 42, "role": "candidate"}
        if stage is not None:
            catalog.selection.verify_sources(stage["plan"], sources, pins)
            selection = catalog.selection.selected(stage)
        require(selection["domain"] in ("retail", "telecom") or selection["domain"] == "airline"
                and stage is not None and stage["plan"]["schema"] == catalog.selection.AIRLINE_SEQUENTIAL_SCHEMA)
        rows = runtime.tau.json_value(sources["data/tau2/domains/" + selection["domain"] + "/tasks.json"])
        rows = rows["tasks"] if type(rows) is dict else rows
        matches = [row for row in rows if row["id"] == selection["task_id"]]
        require(len(matches) == 1)
        criteria = matches[0].get("evaluation_criteria")
        assertions = criteria.get("nl_assertions") if type(criteria) is dict else None
        assertions = [] if assertions is None else assertions
        require(type(assertions) is list and len(assertions) <= 3
                and all(type(item) is str and 0 < len(item.encode()) <= 16384 for item in assertions))
        worker.bounded_json(assertions, 65536)
        require(stage is not None or len(assertions) == 1)
        self.assertion_count = len(assertions)
        self.domain = selection["domain"]
        self.tools = None
        self.binding = {"schema": SCHEMA, "upstream_revision": runtime.REVISION,
            "source_archive_sha256": runtime.ARCHIVE_SHA, "source_tree": runtime.SOURCE_TREE,
            "runtime_lock_sha256": runtime.CATALOG_SHA, "selection": dict(selection),
            "nl_assertion_count": self.assertion_count, "source_authenticated": True,
            "live_admission": False, "quality_eligible": False}
        if stage is not None:
            self.binding["stage_binding"] = catalog.selection.binding(stage)
        if self.domain == "telecom":
            require(stage is not None)
            value = catalog.catalog(deadline)
            row = value["selected_tasks"][selection["task_id"]]
            require(row["tool_count"] == 30 and row["tool_catalog_sha256"] == value["tool_catalog_sha256"])
            self.tools = worker.decode(worker.encoded(value["tools"]))
            require(worker.digest(worker.encoded(self.tools)) == value["tool_catalog_sha256"])
            self.binding.update(catalog_sha256=catalog.CATALOG_SHA,
                                tool_catalog_sha256=value["tool_catalog_sha256"])
        require(time.monotonic() < deadline)
        self.sha256 = worker.digest(worker.encoded(self.binding))

    def project(self, role, body, deadline):
        require(time.monotonic() < deadline and role in ("simulator", "evaluator")
                and (role != "evaluator" or self.assertion_count > 0))
        if role == "simulator" and self.domain == "telecom":
            require(worker.digest(worker.encoded(self.tools)) == self.binding["tool_catalog_sha256"])
            pair = projection.project(body, self.tools)
            chat, count = pair["chat"], pair["count"]
        else:
            chat = external.simulator_request(body, full_task=True, support_chat=True)
            count = {"model": chat["model"], "input": chat["messages"], "truncation": "disabled"}
        worker.bounded_json(chat, 65536)
        worker.bounded_json(count, 65536)
        return chat, count


class Admission:
    """One serial, sticky generation ledger, additional to the trial budget."""
    def __init__(self, config):
        policy.validate(config)
        require(policy.is_external_trial(config) and config["limits"]["cost_usd"] == "3.00")
        self.prices = {role: tuple(Decimal(value) for value in rates) for role, rates in config["prices"].items()}
        self.spent = Decimal(0)
        self.pending = None
        self.reservations = 0
        self.failed = False

    def ceiling(self, role):
        incoming = 32768 if role == "candidate" else SUPPORT_INPUT_CEILING
        prices = self.prices[role]
        return (incoming * prices[0] + 2048 * prices[1]) / Decimal(1000000)

    def check(self, role):
        require(not self.failed and self.pending is None and self.reservations < 32)
        require(self.spent + self.ceiling(role) <= Decimal("3.00"))

    def reserve(self, role):
        self.check(role)
        amount = self.ceiling(role)
        self.pending = role, amount
        self.reservations += 1

    def settle(self, role, usage):
        require(self.pending is not None and self.pending[0] == role)
        incoming, outgoing = usage["prompt_tokens"], usage["completion_tokens"]
        require(type(incoming) is int and 0 < incoming <= 32768
                and type(outgoing) is int and 0 <= outgoing <= 2048)
        prices = self.prices[role]
        actual = (incoming * prices[0] + outgoing * prices[1]) / Decimal(1000000)
        require(actual <= self.pending[1])
        self.spent += actual
        self.pending = None

    def observation(self):
        return {"schema": "nib.tau-support-generation-admission/v1", "ceiling_usd": "3.00",
            "basis": "standard-token-price-reservations-not-invoice", "settled_usd": format(self.spent, "f"),
            "held_usd": format(self.pending[1] if self.pending else Decimal(0), "f"),
            "generation_reservations": self.reservations, "blocked": self.failed,
            "support_input_reservation_tokens": SUPPORT_INPUT_CEILING}


class Endpoint(external.Endpoint):
    def __init__(self, *args, source, admission, **kwargs):
        super().__init__(*args, **kwargs)
        require(self.full_task and self.support_chat and type(source) is Source and type(admission) is Admission)
        self.source, self.admission = source, admission
        self.parity = []

    def count(self, body, context, deadline):
        try:
            self.admission.check(self.role)
            if self.role == "candidate":
                return super().count(body, context, deadline)
            self.stage = "input-validation"
            require(context == self.context and self.ready is None and self.attempts < 32 and self.count_attempts < 32)
            chat, count_wire = self.source.project(self.role, body, deadline)
            row = {"request_sha256": worker.digest(worker.encoded(body)),
                "chat_sha256": worker.digest(worker.encoded(chat)), "count_sha256": worker.digest(worker.encoded(count_wire)),
                "counted_tokens": None, "prompt_tokens": None, "status": "count-pending"}
            self.parity.append(row)
            self.count_budget.reserve()
            self.count_attempts += 1
            self.stage = "support-count"
            counted = self.http.post(external.COUNT, count_wire, self.key, deadline)
            require(type(counted) is dict and set(counted) == {"object", "input_tokens"}
                    and counted["object"] == "response.input_tokens")
            tokens = counted["input_tokens"]
            require(type(tokens) is int and 0 < tokens <= context - self.output)
            row.update(counted_tokens=tokens, status="counted")
            self.ready = row["request_sha256"], chat, tokens
            self.stage = "counted"
            return tokens
        except BaseException:
            self.admission.failed = True
            raise

    def complete(self, body, prompt, deadline):
        before = len(self.measured)
        try:
            require(type(prompt) is int and self.ready is not None and self.ready[0] == worker.digest(worker.encoded(body))
                    and self.ready[2] == prompt)
            self.admission.reserve(self.role)
            response = super().complete(body, prompt, deadline)
            require(len(self.measured) == before + 1)
            self.admission.settle(self.role, self.measured[-1])
            if self.role != "candidate":
                self.parity[-1].update(prompt_tokens=self.measured[-1]["prompt_tokens"], status="matched")
            return response
        except BaseException:
            self.admission.failed = True
            if self.role != "candidate" and self.parity:
                row = self.parity[-1]
                if len(self.measured) == before + 1:
                    row["prompt_tokens"] = self.measured[-1]["prompt_tokens"]
                row["status"] = "mismatch" if row["prompt_tokens"] is not None and row["prompt_tokens"] != prompt else "rejected"
            raise

    def observation(self):
        return {**super().observation(), "support_source": worker.decode(worker.encoded(self.source.binding)),
            "support_source_sha256": self.source.sha256, "generation_admission": self.admission.observation(),
            "count_usage_pairs": worker.decode(worker.encoded(self.parity)),
            "observed_pairs_match": bool(self.parity) and all(row["status"] == "matched" for row in self.parity),
            "token_count_parity_verified": False, "live_admission": False}


def accepted(endpoints):
    if type(endpoints) is not dict or set(endpoints) != {"candidate", "simulator", "evaluator"}:
        return False
    for role, row in endpoints.items():
        ledger = row.get("generation_admission", {})
        if ledger.get("blocked") is not False or ledger.get("held_usd") != "0":
            return False
        if role != "candidate":
            pairs = row.get("count_usage_pairs", [])
            if len(pairs) != row.get("count_attempts") or len(pairs) != row.get("generation_attempts"):
                return False
            if any(pair.get("status") != "matched" or pair.get("counted_tokens") != pair.get("prompt_tokens") for pair in pairs):
                return False
    return True
