# SPDX-License-Identifier: Apache-2.0
"""Serial full-trial reservations; unknown/cancelled receipts never release funds."""

from decimal import Decimal
import os

import tau_external as external
import tau_external_selection as selection
import tau_runtime as runtime

tau, worker, require = selection.tau, external.worker, external.require
SCHEMA = "nib.tau-sequential-journal/v1"
STAGES = ("reservation", "preparation", "trial", "settlement", "completed")


def charge(record, *, plan, index, toolkit, payload_sha, contract_sha, source_sha):
    """Validate an owned original receipt before reclaiming any reservation."""
    stage = {"plan": plan, "index": index}
    selected, binding = selection.selected(stage), selection.binding(stage)
    require(type(record) is dict and record.get("schema") == "nib.tau-external-staged-run/v1"
            and record.get("result") == "pass" and record.get("stage") == "completed"
            and record.get("cleanup") == "confirmed" and record.get("toolkit") == toolkit
            and record.get("cancelled", False) is False
            and record.get("plan_sha256") == payload_sha and record.get("endpoint_binding_sha256") == contract_sha
            and record.get("selection") == selected and record.get("stage_binding") == binding
            and all(record.get(name) is False for name in
                    ("quality_eligible", "confidential", "live_admission", "public_owner_mutated", "full_benchmark")))
    diagnostic = record["trial"]
    require(diagnostic.get("schema") == "nib.tau-external-staged/v2" and external.accepted(diagnostic)
            and diagnostic.get("selection") == selected and diagnostic.get("stage_binding") == binding
            and diagnostic.get("nl_assertion_count") == selection.assertion_count(stage)
            and diagnostic.get("cost_ceiling_usd") == str(selection.TRIAL_RESERVATION)
            and diagnostic.get("generation_cost_ceiling_usd") == "3.00"
            and record["endpoint_usage"] == diagnostic["endpoints"])
    endpoints = diagnostic["endpoints"]
    config = external.policy.configuration(full_task=True, external_simulator=True, stage=stage)
    counts = requests = incoming = outgoing = 0
    generation = Decimal(0)
    ledger = None
    for role, row in endpoints.items():
        n, calls = row["count_attempts"], row["generation_attempts"]
        require(type(n) is int and 0 <= n <= 32 and type(calls) is int and 0 <= calls <= 32
                and type(row["unknown_responses"]) is int and row["unknown_responses"] == 0
                and row["generation_api"] == "chat-completions"
                and row["token_count_parity_verified"] is False and row["live_admission"] is False
                and (n == 0 if role == "candidate" else n == calls)
                and row["count_cost_reserved_usd"] == format(selection.COUNT_RESERVATION * n, "f")
                and row["support_source_sha256"] == source_sha
                and worker.digest(worker.encoded(row["support_source"])) == source_sha
                and row["support_source"]["source_authenticated"] is True
                and row["support_source"]["selection"] == selected
                and row["support_source"]["stage_binding"] == binding)
        usage = row["known_usage"]
        require(type(usage) is dict and set(usage) == {"prompt_tokens", "completion_tokens", "total_tokens"}
                and all(type(value) is int and value >= 0 for value in usage.values())
                and calls <= usage["prompt_tokens"] <= calls * 30720 and usage["completion_tokens"] <= calls * 2048
                and usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"])
        pairs = row["count_usage_pairs"]
        require(type(pairs) is list and len(pairs) == n
                and row["observed_pairs_match"] is (n > 0))
        for pair in pairs:
            require(type(pair) is dict and set(pair) == {"request_sha256", "chat_sha256", "count_sha256",
                    "counted_tokens", "prompt_tokens", "status"}
                    and type(pair["counted_tokens"]) is int and 0 < pair["counted_tokens"] <= 30720
                    and type(pair["prompt_tokens"]) is int and pair["prompt_tokens"] == pair["counted_tokens"]
                    and pair["status"] == "matched")
            for field in ("request_sha256", "chat_sha256", "count_sha256"):
                tau.sha(pair[field])
        require(role == "candidate" or sum(pair["prompt_tokens"] for pair in pairs) == usage["prompt_tokens"])
        prices = [Decimal(value) for value in config["prices"][role]]
        generation += (usage["prompt_tokens"] * prices[0] + usage["completion_tokens"] * prices[1]) / 1000000
        counts += n
        requests += calls
        incoming += usage["prompt_tokens"]
        outgoing += usage["completion_tokens"]
        require(ledger is None or ledger == row["generation_admission"])
        ledger = row["generation_admission"]
    require(counts <= 32 and 0 < requests <= 32 and generation <= Decimal("3.00")
            and ledger["schema"] == "nib.tau-support-generation-admission/v1"
            and ledger["ceiling_usd"] == "3.00" and ledger["basis"] == "standard-token-price-reservations-not-invoice"
            and type(ledger["support_input_reservation_tokens"]) is int
            and ledger["support_input_reservation_tokens"] == external.source_support().SUPPORT_INPUT_CEILING
            and ledger["generation_reservations"] == requests and type(ledger["generation_reservations"]) is int
            and ledger["held_usd"] == "0" and ledger["blocked"] is False
            and tau.amount(ledger["settled_usd"]) == generation)
    used = diagnostic["trial"]["usage"]
    require(type(used) is dict and set(used) == {"prompt_tokens", "completion_tokens", "total_tokens", "cost_usd"}
            and all(type(used[key]) is int for key in ("prompt_tokens", "completion_tokens", "total_tokens"))
            and used["prompt_tokens"] == incoming and used["completion_tokens"] == outgoing
            and used["total_tokens"] == incoming + outgoing and tau.amount(used["cost_usd"]) == generation)
    total = selection.COUNT_RESERVATION * counts + generation
    require(total <= selection.TRIAL_RESERVATION)
    return {"count_attempts": counts, "count_held_usd": format(selection.COUNT_RESERVATION * counts, "f"),
            "generation_known_usd": format(generation, "f"), "charged_reservation_usd": format(total, "f")}


class Journal:
    """Exclusive new run only. No recovery, retry, mutable balance file or resume."""
    def __init__(self, destination, plan, contract_sha, toolkit):
        selection.validate(plan)
        require(plan["schema"] == selection.SEQUENTIAL_SCHEMA and plan["result"] == "pass"
                and plan["producer_git_sha"] == toolkit["git_sha"])
        tau.sha(contract_sha)
        self.plan = tau.json_value(tau.encoded(plan))
        self.toolkit = tau.json_value(tau.encoded(toolkit))
        self.contract_sha = contract_sha
        self.cap = tau.amount(plan["aggregate_cap_usd"])
        self.index, self.spent, self.pending, self.failed = 0, Decimal(0), None, False
        self.events, self.previous, self.fd, self.stopped = 0, None, None, False
        self.event_failed = False
        try:
            with runtime.directory(destination.parent) as parent:
                os.mkdir(destination.name, 0o700, dir_fd=parent)
                self.fd = os.open(destination.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                os.fsync(parent)
            self.write("plan.json", tau.encoded(self.plan))
            self.event("opened", {"plan_sha256": tau.digest(tau.encoded(self.plan)),
                                  "contract_sha256": contract_sha, "toolkit": self.toolkit})
        except BaseException:
            self.close()
            raise

    def write(self, name, raw):
        require(self.fd is not None and type(raw) is bytes and len(raw) <= 1 << 20
                and type(name) is str and "/" not in name and name not in ("", ".", ".."))
        child = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
        with os.fdopen(child, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(self.fd)

    def event(self, kind, data):
        raw = tau.encoded({"schema": SCHEMA, "sequence": self.events, "previous_sha256": self.previous,
                           "kind": kind, "data": data})
        try:
            self.write(f"event-{self.events:03d}.json", raw)
        except BaseException:
            self.failed = True
            self.event_failed = True
            raise
        self.previous = tau.digest(raw)
        self.events += 1

    def reserve(self):
        require(not self.failed and self.pending is None and self.index < len(self.plan["selections"]))
        if self.spent + selection.TRIAL_RESERVATION > self.cap:
            return False
        # Hold in memory even if publication fails; dispatch requires its durable event.
        self.pending = {"index": self.index, "claimed": False}
        self.event("reserved", {"index": self.index, "held_usd": str(selection.TRIAL_RESERVATION)})
        return True

    def claim(self, stage, payload_sha, contract_sha, toolkit, source_sha):
        require(not self.failed and self.pending == {"index": self.index, "claimed": False}
                and tau.encoded(stage) == tau.encoded({"plan": self.plan, "index": self.index})
                and contract_sha == self.contract_sha and toolkit == self.toolkit)
        tau.sha(payload_sha)
        tau.sha(source_sha)
        self.event("claimed", {"index": self.index, "payload_sha256": payload_sha, "source_sha256": source_sha})
        self.pending.update(claimed=True, payload_sha=payload_sha, source_sha=source_sha)

    def settle(self, record, raw):
        try:
            require(not self.failed and self.pending is not None and self.pending["claimed"]
                    and type(raw) is bytes and tau.encoded(record) == raw)
            cost = charge(record, plan=self.plan, index=self.index, toolkit=self.toolkit,
                          payload_sha=self.pending["payload_sha"], contract_sha=self.contract_sha,
                          source_sha=self.pending["source_sha"])
            self.write(f"receipt-{self.index:02d}.json", raw)
            self.event("settled", {"index": self.index, "receipt_sha256": tau.digest(raw),
                                  "selection": record["selection"], "reward": record["trial"]["trial"]["reward"], **cost})
            self.spent += Decimal(cost["charged_reservation_usd"])
            self.pending = None
            self.index += 1
        except BaseException:
            self.failed = True
            raise

    def stop(self, reason, *, stage="completed"):
        require(not self.stopped and stage in STAGES
                and reason in ("complete", "cap-exhausted", "deadline", "cancelled", "failed"))
        complete = reason == "complete"
        require(not complete or not self.failed and self.pending is None and self.index == len(self.plan["selections"]))
        self.stopped = True
        self.failed = True  # Every terminal outcome closes admission permanently.
        held = selection.TRIAL_RESERVATION if self.pending is not None else Decimal(0)
        result = {"schema": "nib.tau-sequential-result/v1", "result": "pass" if complete else "incomplete",
            "stop_reason": reason, "planned_trials": len(self.plan["selections"]), "completed_trials": self.index,
            "stage": stage, "cancelled": reason == "cancelled", "source_authenticated": True,
            "aggregate_cap_usd": format(self.cap, "f"), "charged_reservation_usd": format(self.spent, "f"),
            "held_trial_usd": format(held, "f"), "remaining_usd": format(self.cap - self.spent - held, "f"),
            "basis": "standard-token-price-reservations-not-invoice", "quality_eligible": False,
            "live_admission": False, "confidential": False, "full_benchmark": False,
            "journal_complete": not self.event_failed}
        # A failed append may have left its exclusive file on disk. Preserve it,
        # and publish the held balance separately without retrying that sequence.
        if not self.event_failed:
            try:
                self.event("stopped", result)
            except BaseException:
                result.update(result="incomplete", stop_reason="failed", journal_complete=False)
                self.write("result.json", tau.encoded(result))
                raise
        self.write("result.json", tau.encoded(result))
        return result

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
