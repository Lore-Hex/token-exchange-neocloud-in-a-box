# SPDX-License-Identifier: Apache-2.0
"""One private engine lifetime: long C1 first, isolated short scaling second."""

import argparse
from contextlib import ExitStack
from dataclasses import replace
import signal
import subprocess
import threading
import time

import selected_combined_receipt as receipt
import selected_service_process as process

capacity, long, scaling = receipt.capacity, receipt.long, receipt.scaling
service, plan = process.service, capacity.plan
require = plan.require
BASE_PROFILE = process.engine_entry.LONG_GRAPHS_PROFILE
PREPARATION_MINIMUM = 60 + scaling.cohort.PREPARE_SECONDS + service.REAP_SECONDS


class Run:
    """Concrete, single-use mode; not a caller-supplied backend or policy."""
    def __init__(self, prepared_sha256, clocks):
        require(type(clocks) is dict and clocks["preparation_seconds"] >= PREPARATION_MINIMUM)
        self.plan = plan.DeadlinePlan.begin(execution_profile=plan.PROFILE, **clocks)
        self.prepared_sha256 = prepared_sha256
        self.row = receipt.empty(prepared_sha256)
        self.row["deadline_plan"] = dict(clocks)
        self.workload = self.long_row = self.scaling_row = None
        self.supervisor = None
        self.used = False
        self.metric_count = 0

    def deadline(self, stage):
        self.plan = self.plan.observe(now_wall=time.time(), now_monotonic=time.monotonic())
        return self.plan.deadline(stage)

    def bind_owner(self, owner, contract):
        require(type(owner) is process.ProcessOwner and owner.combined is self
                and type(owner.prepared) is process.Prepared
                and owner.prepared.receipt_sha256 == self.prepared_sha256
                and owner.prepared.lab["execution_profile"] == BASE_PROFILE
                and type(contract) is service.Contract and contract.execution_profile == BASE_PROFILE
                and contract.lifetime_profile == service.ORDINARY_PROFILE
                and contract.max_inflight == 1 and contract.context_tokens == 131072
                and contract.max_output_tokens == 16384)

    def prepare(self, owner, contract):
        self.row.update(stage="preparation", failure="preparation")
        self.bind_owner(owner, contract)
        deadline = self.deadline("preparation")
        with service.identity.worker.alarm(deadline), long.inference.cancellation(owner.shutdown):
            owner.prepared.check(deadline)
            capacity.verify_sources(process.bootstrap.read_source, owner.prepared.lab["paths"][-1])
            owner.verify_weights(deadline)
            timeout = min(scaling.cohort.PREPARE_SECONDS, deadline - time.monotonic() - service.REAP_SECONDS)
            require(timeout > 0)
            output = process.snapshot.execute_process_group([owner.prepared.interpreter, "-I", "-S", "-B",
                str(process.ROOT / "scripts/selected_combined_worker.py"), self.prepared_sha256, "prepare"],
                env=dict(owner.prepared.lab["environment"]), cwd="/", stdin=subprocess.DEVNULL,
                timeout=timeout, max_output_bytes=8192, max_stderr_bytes=4096)
            require(output.returncode == 0 and not output.stderr)
            row = service.correctness.strict_json(output.stdout)
            require(type(row) is dict and set(row) == {"prepared_sha256", "workload"}
                    and row["prepared_sha256"] == self.prepared_sha256)
            self.workload = scaling.binding.workload(row["workload"], scaling.cohort)
            owner.prepared.check(deadline)
        require(time.monotonic() < self.deadline("preparation"))

    def check(self, stage):
        deadline = self.deadline(stage)
        supervisor = self.supervisor
        long.inference.checkpoint(supervisor, self.identities, deadline)
        capacity.verify_sources(process.bootstrap.read_source, supervisor.owner.prepared.lab["paths"][-1])
        require(time.monotonic() < self.deadline(stage))
        return deadline

    def probe(self, endpoint, model, deadline):
        require(self.supervisor is not None and model == service.MODEL and self.metric_count < 4
                and endpoint.expected == self.identities["engine"])
        deadline = min(deadline, self.check("throughput"), time.monotonic() + capacity.capacity.SECONDS)
        with service.identity.worker.alarm(deadline):
            raw = capacity.capacity._probe_raw(endpoint, model, deadline, selected_model=True)
            # Retain the original even if parsing or the terminal identity check
            # rejects it. It is private evidence, never printable diagnostics.
            self.row["components"]["capacity-" + str(self.metric_count)] = receipt.component(
                "capacity-" + str(self.metric_count), raw)
            self.metric_count += 1
            observation = capacity.parse(raw, execution_profile=plan.PROFILE, model=model)
            self.check("throughput")
            return observation

    @staticmethod
    def stable(before, after):
        capacity.stable(before, after)

    def run(self, supervisor):
        require(not self.used and type(supervisor) is service.Supervisor
                and supervisor.combined is self and supervisor.owner.combined is self
                and supervisor.used and supervisor.phase == "ready" and self.workload is not None)
        self.used, self.supervisor = True, supervisor
        self.bind_owner(supervisor.owner, supervisor.contract)
        self.identities = {role: dict(supervisor.owner.identities[role]) for role in service.ROLES}
        self.row["engine_identity_sha256"] = long.digest(self.identities["engine"])
        try:
            self.row.update(stage="long", failure="long")
            deadline = self.check("long")
            self.long_row = long.run_owned(supervisor, stage_deadline=deadline, combined=True)
            self.row["components"]["long"] = receipt.component("long", long.encoded(self.long_row))
            print(receipt.encoded({"schema": "nib.selected-combined-progress/v1",
                "prepared_sha256": self.prepared_sha256, "long": self.row["components"]["long"]}).decode("ascii"), flush=True)
            long.validate_combined_report(long.encoded(self.long_row), self.prepared_sha256)
            require(self.long_row["checks_pass"] and self.readers_closed())
            self.check("long")
            self.row.update(stage="throughput", failure="throughput")
            deadline = self.check("throughput")
            with long.inference.cancellation(supervisor.shutdown), service.identity.worker.alarm(deadline):
                endpoint = service.identity.Endpoint(8000, {"process": self.identities["engine"]})
                self.scaling_row = scaling.run(endpoint, self.workload, deadline=deadline,
                    gpu_count=8, cohort_seconds=scaling.SECONDS, model=service.MODEL, condition="isolated", combined=self)
                self.row["components"]["scaling"] = receipt.component("scaling", receipt.encoded(self.scaling_row))
                require(scaling.validate_combined(self.scaling_row))
                self.check("throughput")
                self.row["terminal_identity_verified"] = True
        except BaseException as exc:
            self.row["cancelled"] = (self.row["cancelled"] or supervisor.shutdown.is_set()
                or isinstance(exc, (KeyboardInterrupt, SystemExit))
                or (self.long_row is not None and self.long_row["failure"] == "cancelled")
                or (self.scaling_row is not None and self.scaling_row["stop_reason"] == "cancelled"))
            if self.row["cancelled"]:
                self.row["failure"] = "cancelled"
            raise
        finally:
            for name, row in (("long", self.long_row), ("scaling", self.scaling_row)):
                if row is not None:
                    self.row["components"][name] = receipt.component(name, receipt.encoded(row))
            receipt.totals(self.row, self.long_row, self.scaling_row)

    def readers_closed(self):
        return ((self.long_row is None or all(s["resources_closed"] for s in self.long_row["samples"]))
                and (self.scaling_row is None or self.scaling_row["collectors_reaped"]))

    def checks_pass(self):
        return (self.row["terminal_identity_verified"] and self.long_row is not None
                and self.long_row["checks_pass"] and self.scaling_row is not None
                and self.scaling_row["completed"] and self.readers_closed())

    def finish(self, lifecycle, shutdown):
        self.row["components"]["lifecycle"] = receipt.component("lifecycle", receipt.encoded(lifecycle))
        self.row["cleanup_confirmed"] = lifecycle["cleanup_confirmed"]
        self.row["cancelled"] |= shutdown.is_set() or lifecycle["reason"] == "cancelled"
        if self.checks_pass() and lifecycle["status"] == "retired" and not self.row["cancelled"] \
                and time.monotonic() < self.deadline("service-cleanup"):
            self.row.update(stage="terminal", failure="none", result="pass")
        elif self.row["cancelled"]:
            self.row["failure"] = "cancelled"
        elif not lifecycle["cleanup_confirmed"] or self.checks_pass():
            self.row["failure"] = "cleanup"
        return receipt.validate(receipt.encoded(self.row), self.prepared_sha256)


class QuietParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("combined contract rejected")


def main(argv):
    run, lifecycle, owner = None, None, None
    shutdown = threading.Event()
    handlers = {sig: signal.signal(sig, lambda *_: shutdown.set()) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        parser = QuietParser(description=__doc__, allow_abbrev=False)
        for name in ("prepared-sha256", "provider-config", "api-key-file", "cert", "key", "hostname",
                     "input-price", "output-price", "hard-stop-at", "lifetime-profile", "execution-profile"):
            parser.add_argument("--" + name, required=True)
        parser.add_argument("--preparation-seconds", type=int, required=True)
        for name in ("started-wall", "started-monotonic", "original-hard-wall", "original-hard-monotonic"):
            parser.add_argument("--" + name, type=float, required=True)
        args = parser.parse_args(argv)
        require(args.lifetime_profile == service.ORDINARY_PROFILE and args.execution_profile == BASE_PROFILE)
        contract = service.Contract.begin(args.hard_stop_at, input_price=args.input_price, output_price=args.output_price,
            lifetime_profile=service.ORDINARY_PROFILE, execution_profile=BASE_PROFILE)
        clocks = dict(preparation_seconds=args.preparation_seconds, now_wall=args.started_wall,
            now_monotonic=args.started_monotonic, hard_wall=args.original_hard_wall,
            hard_monotonic=args.original_hard_monotonic)
        run = Run(args.prepared_sha256, clocks)
        require(time.monotonic() < run.deadline("preparation")
                and contract.hard_monotonic <= args.original_hard_monotonic
                and contract.hard_wall + 180 <= args.original_hard_wall)
        contract = replace(contract, startup_deadline=run.deadline("startup"),
            hard_monotonic=min(contract.hard_monotonic, run.deadline("service-cleanup")))
        run.row.update(stage="preparation", failure="preparation")
        with ExitStack() as stack:
            with service.identity.worker.alarm(run.deadline("preparation")):
                prepared = process.Prepared(args.prepared_sha256, run.deadline("preparation"))
                inputs = stack.enter_context(service.snapshot_inputs(provider=args.provider_config,
                    cert=args.cert, key=args.key, api_key=args.api_key_file))
                owner = process.ProcessOwner(prepared, inputs, args.hostname, shutdown)
                owner.combined = run
                run.prepare(owner, contract)
            run.row.update(stage="startup", failure="startup")
            lifecycle = service.Supervisor(contract, inputs, owner, shutdown).run(combined=run)
            run.finish(lifecycle, shutdown)
    except BaseException as exc:
        if run is not None:
            run.row.update(result="fail")
            run.row["cancelled"] |= shutdown.is_set() or isinstance(exc, (KeyboardInterrupt, SystemExit))
            if run.row["cancelled"]:
                run.row["failure"] = "cancelled"
            elif run.row["failure"] == "none":
                run.row["failure"] = "publication"
    finally:
        if owner is not None and lifecycle is None:
            try:
                deadline = min(run.deadline("service-cleanup"), time.monotonic() + service.REAP_SECONDS)
                run.row["cleanup_confirmed"] = owner.reap_unregistered(deadline) is True and time.monotonic() <= deadline
            except BaseException:
                run.row["cleanup_confirmed"] = False
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
    if run is None:
        # No child can have been started. Do not echo malformed operator inputs.
        print('{"schema":"nib.selected-combined-rejected/v1","result":"fail"}')
        return 1
    raw = receipt.encoded(run.row)
    require(len(raw) <= receipt.MAX_RECEIPT)
    print(raw.decode("ascii"), flush=True)
    return 0 if run.row["result"] == "pass" else 1
