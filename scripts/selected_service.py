# SPDX-License-Identifier: Apache-2.0
"""Selected-service lifecycle shared by the closed prepared-runtime owner.

Owner is trusted in-process code, not a remote receipt/JSON/plugin interface.
The runnable entrypoint is selected_service_process.py.
"""

from contextlib import contextmanager
from dataclasses import dataclass, field
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time

import deepseek_model_profile as models
import engine_lab
import engine_local_correctness as correctness
import engine_runtime_observation as observer
import selected_service_engine as engine_entry
from pilot import serve as pilot
import tau_live_identity as identity

MODEL = "dealignai/deepseek-v4.1-flash-1017-uncensored-fp8"
CONTEXT, OUTPUT, ADMISSION = 8192, 256, 1
MEMORY_BYTES, SWAP_BYTES = 512 << 30, 0
ROLES = ("engine", "guard", "tls")
INFERENCE_MODE = "inference-smoke"
INFERENCE_SCHEMA = "nib.selected-service-lifecycle/v2"
CONFORMANCE_MODE = "conformance-smoke"
CONFORMANCE_SCHEMA = "nib.selected-service-lifecycle/v3"
PROTOCOL_MODE = "protocol-smoke"
PROTOCOL_SCHEMA = "nib.selected-service-lifecycle/v4"
TOOL_MODE = "tool-smoke"
TOOL_SCHEMA = "nib.selected-service-lifecycle/v5"
LONG_MODE = "long-qualification"
LONG_SCHEMA = "nib.selected-service-lifecycle/v8"
SMOKE_PROFILE, ORDINARY_PROFILE = "smoke", "ordinary-24h"
ORDINARY_SCHEMA = "nib.selected-service-lifecycle/v6"
PROGRESS_SCHEMA = "nib.selected-service-progress/v1"
EXECUTION_SCHEMA = "nib.selected-service-lifecycle/v7"
PROGRESS_PHASES = ("preparation", "inputs", "weights", "engine-starting",
                   "guard-starting", "tls-starting", "ready", "draining")
# pilot.stop_serving: 140s graceful + pilot.stop's 10s forced wait.
# snapshot.cleanup_process_group: 10s TERM + 10s KILL + 5s absence proof.
DRAIN_SECONDS, REAP_SECONDS = 150, observer.CLEANUP_SECONDS
CLEANUP_RESERVE = 2 * (DRAIN_SECONDS + REAP_SECONDS) + REAP_SECONDS
FAILURES = frozenset(("already-used", "cancelled", "child-exited", "contract", "deadline",
    "identity", "inputs-changed", "lifetime", "model", "mounts", "owner-failed", "price",
    "readiness", "registration", "runtime-limits"))


class Rejected(ValueError):
    def __init__(self, category):
        super().__init__("selected service rejected")
        self.category = category if type(category) is str and category in FAILURES else "owner-failed"


def require(condition, category):
    if not condition:
        raise Rejected(category)


def lifetime_seconds(profile):
    require(type(profile) is str and profile in (SMOKE_PROFILE, ORDINARY_PROFILE), "lifetime")
    return 4800 if profile == SMOKE_PROFILE else 86400


def request_seconds(execution_profile):
    engine_entry.execution_limits(execution_profile)
    return 900 if execution_profile in engine_entry.LONG_PROFILES else 120


def drain_seconds(execution_profile):
    return request_seconds(execution_profile) + 30


def cleanup_reserve(execution_profile):
    drain = drain_seconds(execution_profile)
    # Long requests reserve both frontend drains and all four cleanup steps:
    # TLS, guard, engine, then the unregistered-child sweep.
    if execution_profile in engine_entry.LONG_PROFILES:
        return 2 * drain + 4 * REAP_SECONDS
    return CLEANUP_RESERVE


def qualification_options(enabled, lifetime_profile, execution_profile, *, smoke=False):
    require(type(enabled) is bool and (not enabled or lifetime_profile == ORDINARY_PROFILE
            and execution_profile in engine_entry.LONG_PROFILES and not smoke), "contract")


def retirement_at(wall):
    return datetime.datetime.fromtimestamp(wall, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def lifetime_record(profile, wall, execution_profile=engine_entry.EAGER_PROFILE):
    lifetime_seconds(profile)
    return {"profile": profile, "hard_stop_at": retirement_at(wall),
            "admission_stop_at": retirement_at(wall - cleanup_reserve(execution_profile))}


def execution_result(row, profile, prepared_sha256):
    limits = engine_entry.execution_limits(profile)
    if profile == engine_entry.EAGER_PROFILE:
        return row
    return {**row, "schema": EXECUTION_SCHEMA, "lifecycle_schema": row["schema"],
            "execution": limits, "execution_prepared_sha256": prepared_sha256}


def progress_record(phase, lifetime, prepared_sha256, execution_profile=engine_entry.EAGER_PROFILE):
    require(type(phase) is str and phase in PROGRESS_PHASES and type(prepared_sha256) is str
            and re.fullmatch("[0-9a-f]{64}", prepared_sha256), "contract")
    limits = engine_entry.execution_limits(execution_profile)
    row = {"schema": PROGRESS_SCHEMA, "phase": phase, "lifetime": lifetime,
           "prepared_sha256": prepared_sha256}
    if execution_profile != engine_entry.EAGER_PROFILE:
        row.update(schema="nib.selected-service-progress/v2", execution=limits)
    return row


def progress(phase, contract, prepared_sha256):
    if contract.lifetime_profile == ORDINARY_PROFILE:
        print(json.dumps(progress_record(phase, contract.lifetime, prepared_sha256, contract.execution_profile),
                         sort_keys=True, allow_nan=False), flush=True)


@dataclass(frozen=True)
class Contract:
    hard_stop_at: str
    hard_wall: float
    hard_monotonic: float
    startup_deadline: float
    input_price: str
    output_price: str
    lifetime_profile: str = SMOKE_PROFILE
    execution_profile: str = engine_entry.EAGER_PROFILE
    model: str = field(default=MODEL, init=False)
    revision: str = field(default=models.select("uncensored")["hf_revision"], init=False)
    manifest_sha256: str = field(default=models.select("uncensored")["manifest_sha256"], init=False)
    context_tokens: int = field(default=CONTEXT, init=False)
    max_output_tokens: int = field(default=OUTPUT, init=False)
    max_inflight: int = field(default=ADMISSION, init=False)
    memory_bytes: int = field(default=MEMORY_BYTES, init=False)
    swap_bytes: int = field(default=SWAP_BYTES, init=False)

    def __post_init__(self):
        for name, value in self.execution.items():
            if name in ("context_tokens", "max_output_tokens", "max_inflight"):
                object.__setattr__(self, name, value)

    @property
    def execution(self):
        return engine_entry.execution_limits(self.execution_profile)

    @property
    def request_seconds(self):
        return request_seconds(self.execution_profile)

    @property
    def drain_seconds(self):
        return drain_seconds(self.execution_profile)

    @property
    def cleanup_reserve(self):
        return cleanup_reserve(self.execution_profile)

    @classmethod
    def begin(cls, hard_stop_at, *, input_price, output_price, lifetime_profile=SMOKE_PROFILE,
              execution_profile=engine_entry.EAGER_PROFILE):
        # Capture before snapshots/preparation. Neither startup nor restart may
        # replace the original absolute cutoff with a new duration.
        limit = lifetime_seconds(lifetime_profile)
        engine_entry.execution_limits(execution_profile)
        try:
            wall, monotonic = pilot.retirement_deadline(hard_stop_at, public=True)
        except (pilot.pilot.PilotError, TypeError, ValueError):
            raise Rejected("lifetime") from None
        reserve = cleanup_reserve(execution_profile)
        require(reserve < monotonic - time.monotonic() <= limit, "lifetime")
        for price in (input_price, output_price):
            require(type(price) is str and re.fullmatch(r"(0|[1-9][0-9]{0,5})(\.[0-9]{1,6})?", price), "price")
        return cls(hard_stop_at, wall, monotonic,
                   min(time.monotonic() + engine_lab.STARTUP_SECONDS, monotonic - reserve),
                   input_price, output_price, lifetime_profile, execution_profile)

    @property
    def admission_stop_at(self):
        return retirement_at(self.hard_wall - self.cleanup_reserve)

    @property
    def lifetime(self):
        return lifetime_record(self.lifetime_profile, self.hard_wall, self.execution_profile)

    def guard_arguments(self, inputs):
        # Paths are private snapshots. The existing Go CLI owns provider/schema
        # validation; no duplicate Python provider validator or model rewriting.
        return ("--profile", self.execution["guard_profile"], "--api-key-file", str(inputs.root / "api-key"),
                "--provider-config", str(inputs.root / "provider.json"),
                "--input-price", self.input_price, "--output-price", self.output_price,
                "--retirement-at", self.admission_stop_at, "--public")


class Inputs:
    """Private snapshots survive operator mount replacement, never secret logs."""
    def __init__(self, root, values):
        self.root = root
        self._files = tuple((name, hashlib.sha256(raw).digest(), self._stamp(root / name))
                            for name, raw in values.items())

    @staticmethod
    def _stamp(path):
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1, "inputs-changed")
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def check(self):
        require(stat.S_ISDIR(self.root.lstat().st_mode)
                and stat.S_IMODE(self.root.lstat().st_mode) == 0o700, "inputs-changed")
        for name, digest, stamp in self._files:
            path = self.root / name
            require(self._stamp(path) == stamp, "inputs-changed")
            raw = identity.private_read(path, time.monotonic() + 1)
            require(hashlib.sha256(raw).digest() == digest and self._stamp(path) == stamp, "inputs-changed")


@contextmanager
def snapshot_inputs(*, provider, cert, key, api_key):
    with tempfile.TemporaryDirectory(prefix="nib-selected-service-") as directory:
        root = Path(directory).resolve()
        values = {name: pilot.private_input(source, root / name, secret=secret, limit=limit)
                  for name, source, secret, limit in (
                      ("provider.json", provider, False, 16384), ("cert.pem", cert, False, 65536),
                      ("key.pem", key, True, 16384), ("api-key", api_key, True, 257))}
        pilot.validate_api_key(values["api-key"])
        certificate = pilot.ssl.SSLContext(pilot.ssl.PROTOCOL_TLS_SERVER)
        certificate.load_cert_chain(root / "cert.pem", root / "key.pem")
        pilot.tls_client_context(root / "cert.pem")
        inputs = Inputs(root, values)
        inputs.check()
        yield inputs


def owned_endpoint(process, *, executable_sha256, arguments_sha256, deadline):
    """Reuse lab checks only in the same private PID/net/cgroup namespace."""
    for digest in (executable_sha256, arguments_sha256):
        require(type(digest) is str and re.fullmatch("[0-9a-f]{64}", digest), "identity")
    require(process.poll() is None, "child-exited")
    before = observer.process(process.pid, deadline)
    require(before["parent"] == os.getpid() and before["argv"] == arguments_sha256, "identity")
    inode = observer.listener(process.pid, deadline)
    facts = identity.process_facts(process.pid)
    require(facts == {"start_ticks": before["start"], "netns_inode": before["namespaces"]["net"],
                      "boot_id_sha256": before["boot"], "argv_sha256": before["argv"]}, "identity")
    endpoint = identity.Endpoint(8000, {"process": {"pid": process.pid, **facts,
        "exe_sha256": executable_sha256, "listener_inode": inode}})
    endpoint.check(deadline)
    require(observer.process(process.pid, deadline) == before and process.poll() is None, "identity")
    return endpoint


@dataclass(frozen=True)
class EngineReady:
    """Internal observation, not an authenticated or hardware acceptance receipt.

    The concrete owner reads models on an Endpoint.check(deadline, socket)
    checked connection, and obtain limits from the verified launch/cgroup state.
    """
    endpoint: identity.Endpoint
    models_json: bytes
    context_tokens: int
    memory_bytes: int
    swap_bytes: int
    execution_profile: str = engine_entry.EAGER_PROFILE

    def check(self, process, deadline):
        context = engine_entry.execution_limits(self.execution_profile)["context_tokens"]
        require(type(self.endpoint) is identity.Endpoint and self.endpoint.port == 8000
                and self.endpoint.expected["pid"] == process.pid, "identity")
        self.endpoint.check(deadline)
        row = correctness.strict_json(self.models_json)
        require(type(row) is dict and type(row.get("data")) is list
                and all(type(item) is dict for item in row["data"])
                and [item.get("id") for item in row["data"]] == [MODEL], "model")
        # Pinned vLLM's ModelCard exposes the configured maximum, not a
        # caller-declared catalog limit. See the task note's primary source.
        require(type(row["data"][0].get("max_model_len")) is int
                and row["data"][0]["max_model_len"] == context, "runtime-limits")
        require(all(type(value) is int for value in (self.context_tokens, self.memory_bytes, self.swap_bytes))
                and (self.context_tokens, self.memory_bytes, self.swap_bytes) ==
                (context, MEMORY_BYTES, SWAP_BYTES), "runtime-limits")


class Supervisor:
    """Bounded ordering for a trusted owner; deliberately no default backend.

    owner.start(role, contract, inputs, register, deadline) must register the
    owned child before any fallible post-spawn work. owner.probe returns None
    while pending, EngineReady for engine, or True for a verified guard/TLS.
    owner.check_mounts returns True only after verifying prepared executable/
    weight identities and mounts.
    owner.drain uses pilot.stop_serving for frontends (or equivalent bounded
    semantics); owner.reap proves child AND worker-group absence, even on denial.
    owner.reap_unregistered covers a spawn failure before register returns.
    Every owner operation must honor its passed absolute monotonic deadline.
    """
    def __init__(self, contract, inputs, owner, shutdown):
        require(type(contract) is Contract and type(inputs) is Inputs, "contract")
        self.contract, self.inputs, self.owner, self.shutdown = contract, inputs, owner, shutdown
        self.children, self.engine_identity = {}, None
        self.hard_deadline = contract.hard_monotonic
        self.used = False
        self.phase = "starting"

    def cutoff(self):
        # A forward wall jump shortens the bound permanently; rollback cannot
        # renew a previously shortened monotonic lifetime.
        self.hard_deadline = min(self.hard_deadline,
            time.monotonic() + self.contract.hard_wall - time.time())
        return self.hard_deadline

    def checkpoint(self, deadline):
        if self.shutdown.is_set():
            raise Rejected("cancelled")
        require(time.monotonic() < min(deadline, self.cutoff() - self.contract.cleanup_reserve), "deadline")
        require(all(child.poll() is None for child in self.children.values()), "child-exited")
        self.inputs.check()
        mounts = self.owner.check_mounts(deadline)
        if self.shutdown.is_set():
            raise Rejected("cancelled")
        require(time.monotonic() < min(deadline, self.cutoff() - self.contract.cleanup_reserve), "deadline")
        require(all(child.poll() is None for child in self.children.values()), "child-exited")
        require(mounts is True, "mounts")

    def ready(self, role, deadline):
        observed = self.owner.probe(role, self.children[role], deadline)
        self.checkpoint(deadline)
        if observed is None:
            return False
        if role == "engine":
            require(type(observed) is EngineReady, "identity")
            require(observed.execution_profile == self.contract.execution_profile, "runtime-limits")
            observed.check(self.children[role], deadline)
            current = dict(observed.endpoint.expected)
            require(self.engine_identity is None or current == self.engine_identity, "identity")
            self.engine_identity = current
        else:
            require(observed is True, "readiness")
        self.checkpoint(deadline)
        return True

    def run(self, *, inference_smoke=False, conformance_smoke=False, protocol_smoke=False, tool_smoke=False,
            long_qualification=False):
        require(type(inference_smoke) is bool and type(conformance_smoke) is bool
                and type(protocol_smoke) is bool and type(tool_smoke) is bool
                and sum((inference_smoke, conformance_smoke, protocol_smoke, tool_smoke)) <= 1,
                "contract")
        smoke = inference_smoke or conformance_smoke or protocol_smoke or tool_smoke
        qualification_options(long_qualification, self.contract.lifetime_profile,
                              self.contract.execution_profile, smoke=smoke)
        lifetime_seconds(self.contract.lifetime_profile)
        require(not smoke or self.contract.lifetime_profile == SMOKE_PROFILE, "contract")
        require(not self.used, "already-used")
        self.used = True
        reason, admitted, cleanup = "owner-failed", False, []
        inference_result = None
        conformance_result = None
        tool_result = None
        qualification_result = None
        protocol_run = protocol_result = None
        protocol_finished = False
        reader_closed = True
        def finish_protocol():
            nonlocal protocol_result, protocol_finished, reader_closed
            if protocol_run is None or protocol_finished:
                return
            protocol_finished = True
            try:
                protocol_result = protocol.validate_report(inference.encoded(protocol_run.finish(self)),
                    self.owner.prepared.receipt_sha256, inference_result)
            finally:
                drain = protocol_run.drain
                reader_closed = drain is None or drain.closed
        try:
            for role in ROLES:
                deadline = min(self.contract.startup_deadline, self.cutoff() - self.contract.cleanup_reserve)
                self.checkpoint(deadline)
                def register(process, role=role):
                    require(role not in self.children, "registration")
                    self.children[role] = process
                    require(type(process.pid) is int and process.pid > 1
                            and len({child.pid for child in self.children.values()}) == len(self.children), "registration")
                self.owner.start(role, self.contract, self.inputs, register, deadline)
                require(role in self.children, "registration")
                self.checkpoint(deadline)
                while not self.ready(role, deadline):
                    self.shutdown.wait(min(0.1, max(0, deadline - time.monotonic())))
                # Recheck the originally owned engine before exposing any next
                # layer; startup of another child must not refresh its identity.
                require(self.ready("engine", deadline), "readiness")
            admitted = True
            self.phase = "ready"
            if self.contract.lifetime_profile == ORDINARY_PROFILE:
                progress("ready", self.contract, self.owner.prepared.receipt_sha256)
            if long_qualification:
                import selected_long_qualification as qualification
                reader_closed = False
                qualification_result = qualification.run_owned(self)
                qualification.validate_report(qualification.encoded(qualification_result),
                                              self.owner.prepared.receipt_sha256)
                reader_closed = all(item["resources_closed"] for item in qualification_result["samples"])
                require(qualification_result["checks_pass"], "owner-failed")
                self.checkpoint(self.cutoff() - self.contract.cleanup_reserve)
            if smoke:
                import selected_service_inference as inference
                inference_result = inference.validate_report(inference.encoded(inference.run_once(self)),
                    self.owner.prepared.receipt_sha256)
                require(inference_result["result"] == "pass", "owner-failed")
                self.checkpoint(self.cutoff() - self.contract.cleanup_reserve)
            if conformance_smoke:
                import selected_service_conformance as conformance
                conformance_result = conformance.validate_report(
                    inference.encoded(conformance.run_once(self, inference_result)),
                    self.owner.prepared.receipt_sha256, inference_result)
                require(conformance_result["result"] == "pass", "owner-failed")
                self.checkpoint(self.cutoff() - self.contract.cleanup_reserve)
            if tool_smoke:
                import selected_service_tool as tool
                reader_closed = False
                tool_result = tool.validate_report(inference.encoded(tool.run_once(self, inference_result)),
                    self.owner.prepared.receipt_sha256, inference_result)
                reader_closed = all(tool_result["resources_closed"].values())
                require(tool_result["result"] == "pass", "owner-failed")
                self.checkpoint(self.cutoff() - self.contract.cleanup_reserve)
            if protocol_smoke:
                import selected_service_protocol as protocol
                protocol_run = protocol.start(self, inference_result)
                require(type(protocol_run) is protocol.Run and protocol_run.drain is not None
                        and protocol_run.report["drain"]["retirement_requested"], "owner-failed")
                # This deliberate whole-owner retirement is not external
                # cancellation. Ordinary checkpoints remain unchanged.
                self.phase = "draining"
            while not smoke and not long_qualification and time.monotonic() < self.cutoff() - self.contract.cleanup_reserve:
                deadline = min(self.cutoff() - self.contract.cleanup_reserve, time.monotonic() + observer.SECONDS)
                self.checkpoint(deadline)
                for role in ROLES:
                    require(self.ready(role, deadline), "readiness")
                self.shutdown.wait(min(1, max(0, self.cutoff() - self.contract.cleanup_reserve - time.monotonic())))
            reason = "retired"
        except Rejected as exc:
            reason = exc.category
        except KeyboardInterrupt:
            reason = "cancelled"
        except BaseException:
            reason = "owner-failed"
        finally:
            if self.contract.lifetime_profile == ORDINARY_PROFILE:
                try:
                    progress("draining", self.contract, self.owner.prepared.receipt_sha256)
                except BaseException:
                    # Observation failure must not bypass child cleanup.
                    reason = "owner-failed"
            try:
                if reason in ("retired", "cancelled") and any(child.poll() is not None
                        for child in self.children.values()):
                    reason = "child-exited"
            except BaseException:
                reason = "owner-failed"
            for role in reversed(ROLES):
                if role not in self.children:
                    continue
                child = self.children[role]
                drained = role == "engine"
                if role != "engine":
                    deadline = min(self.cutoff(), time.monotonic() + self.contract.drain_seconds)
                    try:
                        if role == "tls" and protocol_run is not None and self.phase == "draining":
                            deadline = min(deadline, protocol_run.drain.deadline)
                            try:
                                with identity.worker.alarm(deadline):
                                    drained = self.owner.begin_drain(role, child, deadline)
                                    if drained:
                                        protocol_run.drain.retiring()
                                        drained = self.owner.wait_drain(role, child, deadline)
                                    if not drained:
                                        protocol_run.drain.stop()
                            except BaseException:
                                protocol_run.drain.stop()
                                raise
                        else:
                            drained = self.owner.drain(role, child, deadline)
                        drained = drained is True and child.poll() == 0 \
                            and time.monotonic() <= deadline
                    except BaseException:
                        drained = False
                    try:
                        if any(self.children[name].poll() is not None for name in ROLES[:ROLES.index(role)]
                               if name in self.children) and reason in ("retired", "cancelled"):
                            reason = "child-exited"
                    except BaseException:
                        if reason in ("retired", "cancelled"):
                            reason = "owner-failed"
                deadline = min(self.cutoff(), time.monotonic() + REAP_SECONDS)
                try:
                    reaped = self.owner.reap(role, child, deadline) is True and child.poll() is not None \
                        and time.monotonic() <= deadline
                except BaseException:
                    reaped = False
                cleanup.append({"role": role, "drained": drained, "reaped": reaped})
                if role == "tls":
                    try:
                        finish_protocol()
                    except BaseException:
                        reason = "owner-failed"
            try:
                finish_protocol()
            except BaseException:
                reason = "owner-failed"
            try:
                deadline = min(self.cutoff(), time.monotonic() + REAP_SECONDS)
                unregistered = self.owner.reap_unregistered(deadline) is True and time.monotonic() <= deadline
            except BaseException:
                unregistered = False
        confirmed = unregistered and reader_closed and all(row["reaped"] for row in cleanup)
        graceful = all(row["drained"] for row in cleanup)
        result = {"schema": "nib.selected-service-lifecycle/v1", "reason": reason,
                "status": reason if reason in ("retired", "cancelled") and confirmed and graceful else "failed",
                "ready": admitted, "cleanup_confirmed": confirmed, "cleanup": cleanup,
                "deployable": False, "hardware_validated": False,
                "tools": False, "reasoning": False, "zdr": False, "confidential": False, "e2ee": False}
        if self.contract.lifetime_profile == ORDINARY_PROFILE:
            result.update(schema=ORDINARY_SCHEMA, lifetime=self.contract.lifetime,
                          prepared_sha256=self.owner.prepared.receipt_sha256)
        if long_qualification:
            result.update(schema=LONG_SCHEMA, mode=LONG_MODE, qualification=qualification_result)
            if reason != "retired" or self.shutdown.is_set() or qualification_result is None \
                    or qualification_result.get("checks_pass") is not True:
                result["status"] = "failed"
        if smoke:
            result.update(schema=INFERENCE_SCHEMA, mode=INFERENCE_MODE, inference=inference_result)
            if reason != "retired" or self.shutdown.is_set() or inference_result is None \
                    or inference_result["result"] != "pass":
                result["status"] = "failed"
            if conformance_smoke:
                result.update(schema=CONFORMANCE_SCHEMA, mode=CONFORMANCE_MODE, conformance=conformance_result)
                if conformance_result is None or conformance_result["result"] != "pass":
                    result["status"] = "failed"
            if tool_smoke:
                result.update(schema=TOOL_SCHEMA, mode=TOOL_MODE, tool_roundtrip=tool_result)
                if tool_result is None or tool_result["result"] != "pass":
                    result["status"] = "failed"
            if protocol_smoke:
                result.update(schema=PROTOCOL_SCHEMA, mode=PROTOCOL_MODE, protocol=protocol_result)
                if protocol_result is None or protocol_result["result"] != "pass":
                    result["status"] = "failed"
        return execution_result(result, self.contract.execution_profile,
                                self.owner.prepared.receipt_sha256) \
            if self.contract.execution_profile != engine_entry.EAGER_PROFILE else result


if __name__ == "__main__":
    raise SystemExit("Library only; use the closed selected_service_process.py entrypoint.")
