# SPDX-License-Identifier: Apache-2.0
"""Selected-service lifecycle shared by the closed prepared-runtime owner.

Owner is trusted in-process code, not a remote receipt/JSON/plugin interface.
The runnable entrypoint is selected_service_process.py.
"""

from contextlib import contextmanager
from dataclasses import dataclass, field
import datetime
import hashlib
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
from pilot import serve as pilot
import tau_live_identity as identity

MODEL = "dealignai/deepseek-v4.1-flash-1017-uncensored-fp8"
CONTEXT, OUTPUT, ADMISSION = 8192, 256, 1
MEMORY_BYTES, SWAP_BYTES = 512 << 30, 0
ROLES = ("engine", "guard", "tls")
INFERENCE_MODE = "inference-smoke"
INFERENCE_SCHEMA = "nib.selected-service-lifecycle/v2"
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


@dataclass(frozen=True)
class Contract:
    hard_stop_at: str
    hard_wall: float
    hard_monotonic: float
    startup_deadline: float
    input_price: str
    output_price: str
    model: str = field(default=MODEL, init=False)
    revision: str = field(default=models.select("uncensored")["hf_revision"], init=False)
    manifest_sha256: str = field(default=models.select("uncensored")["manifest_sha256"], init=False)
    context_tokens: int = field(default=CONTEXT, init=False)
    max_output_tokens: int = field(default=OUTPUT, init=False)
    max_inflight: int = field(default=ADMISSION, init=False)
    memory_bytes: int = field(default=MEMORY_BYTES, init=False)
    swap_bytes: int = field(default=SWAP_BYTES, init=False)

    @classmethod
    def begin(cls, hard_stop_at, *, input_price, output_price):
        # Capture before snapshots/preparation. Neither startup nor restart may
        # replace the original absolute cutoff with a new duration.
        try:
            wall, monotonic = pilot.retirement_deadline(hard_stop_at, public=True)
        except (pilot.pilot.PilotError, TypeError, ValueError):
            raise Rejected("lifetime") from None
        require(monotonic - time.monotonic() > CLEANUP_RESERVE, "lifetime")
        for price in (input_price, output_price):
            require(type(price) is str and re.fullmatch(r"(0|[1-9][0-9]{0,5})(\.[0-9]{1,6})?", price), "price")
        return cls(hard_stop_at, wall, monotonic,
                   min(time.monotonic() + engine_lab.STARTUP_SECONDS, monotonic - CLEANUP_RESERVE),
                   input_price, output_price)

    @property
    def admission_stop_at(self):
        return datetime.datetime.fromtimestamp(self.hard_wall - CLEANUP_RESERVE,
            datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def guard_arguments(self, inputs):
        # Paths are private snapshots. The existing Go CLI owns provider/schema
        # validation; no duplicate Python provider validator or model rewriting.
        return ("--profile", "selected-deepseek", "--api-key-file", str(inputs.root / "api-key"),
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

    def check(self, process, deadline):
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
                and row["data"][0]["max_model_len"] == CONTEXT, "runtime-limits")
        require(all(type(value) is int for value in (self.context_tokens, self.memory_bytes, self.swap_bytes))
                and (self.context_tokens, self.memory_bytes, self.swap_bytes) ==
                (CONTEXT, MEMORY_BYTES, SWAP_BYTES), "runtime-limits")


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

    def cutoff(self):
        # A forward wall jump shortens the bound permanently; rollback cannot
        # renew a previously shortened monotonic lifetime.
        self.hard_deadline = min(self.hard_deadline,
            time.monotonic() + self.contract.hard_wall - time.time())
        return self.hard_deadline

    def checkpoint(self, deadline):
        if self.shutdown.is_set():
            raise Rejected("cancelled")
        require(time.monotonic() < min(deadline, self.cutoff() - CLEANUP_RESERVE), "deadline")
        require(all(child.poll() is None for child in self.children.values()), "child-exited")
        self.inputs.check()
        mounts = self.owner.check_mounts(deadline)
        if self.shutdown.is_set():
            raise Rejected("cancelled")
        require(time.monotonic() < min(deadline, self.cutoff() - CLEANUP_RESERVE), "deadline")
        require(all(child.poll() is None for child in self.children.values()), "child-exited")
        require(mounts is True, "mounts")

    def ready(self, role, deadline):
        observed = self.owner.probe(role, self.children[role], deadline)
        self.checkpoint(deadline)
        if observed is None:
            return False
        if role == "engine":
            require(type(observed) is EngineReady, "identity")
            observed.check(self.children[role], deadline)
            current = dict(observed.endpoint.expected)
            require(self.engine_identity is None or current == self.engine_identity, "identity")
            self.engine_identity = current
        else:
            require(observed is True, "readiness")
        self.checkpoint(deadline)
        return True

    def run(self, *, inference_smoke=False):
        require(type(inference_smoke) is bool, "contract")
        require(not self.used, "already-used")
        self.used = True
        reason, admitted, cleanup = "owner-failed", False, []
        inference_result = None
        try:
            for role in ROLES:
                deadline = min(self.contract.startup_deadline, self.cutoff() - CLEANUP_RESERVE)
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
            if inference_smoke:
                import selected_service_inference as inference
                inference_result = inference.validate_report(inference.encoded(inference.run_once(self)),
                    self.owner.prepared.receipt_sha256)
                require(inference_result["result"] == "pass", "owner-failed")
                self.checkpoint(self.cutoff() - CLEANUP_RESERVE)
            while not inference_smoke and time.monotonic() < self.cutoff() - CLEANUP_RESERVE:
                deadline = min(self.cutoff() - CLEANUP_RESERVE, time.monotonic() + observer.SECONDS)
                self.checkpoint(deadline)
                for role in ROLES:
                    require(self.ready(role, deadline), "readiness")
                self.shutdown.wait(min(1, max(0, self.cutoff() - CLEANUP_RESERVE - time.monotonic())))
            reason = "retired"
        except Rejected as exc:
            reason = exc.category
        except KeyboardInterrupt:
            reason = "cancelled"
        except BaseException:
            reason = "owner-failed"
        finally:
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
                    deadline = min(self.cutoff(), time.monotonic() + DRAIN_SECONDS)
                    try:
                        drained = self.owner.drain(role, child, deadline) is True and child.poll() == 0 \
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
            try:
                deadline = min(self.cutoff(), time.monotonic() + REAP_SECONDS)
                unregistered = self.owner.reap_unregistered(deadline) is True and time.monotonic() <= deadline
            except BaseException:
                unregistered = False
        confirmed = unregistered and all(row["reaped"] for row in cleanup)
        graceful = all(row["drained"] for row in cleanup)
        result = {"schema": "nib.selected-service-lifecycle/v1", "reason": reason,
                "status": reason if reason in ("retired", "cancelled") and confirmed and graceful else "failed",
                "ready": admitted, "cleanup_confirmed": confirmed, "cleanup": cleanup,
                "deployable": False, "hardware_validated": False,
                "tools": False, "reasoning": False, "zdr": False, "confidential": False, "e2ee": False}
        if inference_smoke:
            result.update(schema=INFERENCE_SCHEMA, mode=INFERENCE_MODE, inference=inference_result)
            if reason != "retired" or self.shutdown.is_set() or inference_result is None \
                    or inference_result["result"] != "pass":
                result["status"] = "failed"
        return result


if __name__ == "__main__":
    raise SystemExit("Library only; use the closed selected_service_process.py entrypoint.")
