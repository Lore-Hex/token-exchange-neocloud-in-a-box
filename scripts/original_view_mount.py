# SPDX-License-Identifier: Apache-2.0
"""One original-only mount lease owned by the existing service watchdog."""

from contextlib import ExitStack, contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import select
import socket
import stat
import struct
import sys
import time
import types
import uuid

import original_view_consumer as consumer
import selected_service_owner as owner

require, network, smoke = owner.require, owner.network, owner.smoke
ROOT = Path(__file__).absolute().parents[1]
STAGING_SHA = "83e7c90b44c74865f20f81ad3f3c2dada7cf22b9547ba6c2949be838a9389c62"
FILES = frozenset({"contract.json", "watchdog.json", "mount-intent.json", "mount-created.json",
    "verified.json", "admitted.json", "registered.json", "consumer-joined.json",
    "consumer-verified.json", "closed.json", "unmounted.json"})
BOOTSTRAP = """
import sys
from pathlib import Path
path = sys.argv.pop(1)
sys.path.insert(0, str(Path(path).parent))
scope = {'__name__': 'bound_original_view_mount', '__file__': path}
try:
    exec(compile(sys.argv.pop(1), '<bound-original-view-mount>', 'exec'), scope)
    scope['worker'](*sys.argv[1:])
except BaseException:
    print('{"schema":"nib.original-view-worker-failure/v1","error":"rejected"}')
"""


def helpers():
    path = ROOT / "host/stage-original-weight-view.py"
    with path.open("rb") as stream:
        raw = stream.read(65537)
    require(len(raw) <= 65536 and hashlib.sha256(raw).hexdigest() == STAGING_SHA)
    staging = types.ModuleType("bound_original_view_staging")
    staging.__file__ = str(path)
    exec(compile(raw, str(path), "exec", dont_inherit=True), staging.__dict__)
    return staging, *staging.helpers()


def sha(value, length=64):
    require(type(value) is str and re.fullmatch("[0-9a-f]{" + str(length) + "}", value))
    return value


def startup_current(row):
    remaining = min(row["startup_deadline"] - time.monotonic(),
                    row["hard_monotonic"] - time.monotonic(), row["hard_wall"] - time.time())
    require(remaining > 0)
    return remaining


@contextmanager
def startup_window(row):
    startup_current(row)
    end = min(row["startup_deadline"], row["hard_monotonic"],
              time.monotonic() + row["hard_wall"] - time.time())
    # Preserve the pinned helpers' internals, including their intervening IO,
    # under the existing main-thread alarm. This never renews a cutoff.
    with smoke.service.identity.worker.alarm(end):
        startup_current(row)
        yield
        startup_current(row)


def startup_check(selected_owner):
    _, row = read(selected_owner.state.root.parent / "original-view/contract.json")
    startup_current(row)


def read(path, *, uid=None):
    _, _, cache, _ = helpers()
    path = Path(path)
    uid = os.getuid() if uid is None else uid
    with cache.directory(path.parent, private=uid == os.getuid()) as parent:
        info = os.fstat(parent)
        require(info.st_uid == uid and stat.S_IMODE(info.st_mode) == 0o700)
        before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        require(before.st_uid == uid and stat.S_IMODE(before.st_mode) == 0o600)
        raw = cache.read_regular(path, 16384)
        require(cache.file_identity(before) == cache.file_identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False)))
    return raw, json.loads(raw, object_pairs_hook=cache.unique)


class Records(network.Journal):
    def __init__(self, root):
        self.root = Path(root)

    @staticmethod
    def file_limit(name):
        return 16384

    def publish(self, name, value):
        require(name in FILES)
        _, _, cache, _ = helpers()
        publication = cache.load_pinned("scripts/pilot/export.py", cache.PUBLICATION_SHA256)
        temporary = ".pending-" + uuid.uuid4().hex
        with self._directory() as fd:
            self._check(fd)
            self._write(fd, temporary, value)
            publication.rename_exclusive(fd, temporary, name)
            os.fsync(fd)
        return consumer.digest(network.policy.encoded(value) + b"\n")


def resource_success(resources, inspection, cache):
    require(type(resources) is dict and set(resources) == {"limits", "limits_read_back", "empty_verified",
        "group_removed", "oom_kill", "memory_peak_bytes", "populated_before_cleanup"}
        and resources["limits"] == inspection.resource_helper(cache).LIMITS
        and all(resources[k] is True for k in ("limits_read_back", "empty_verified", "group_removed"))
        and type(resources["oom_kill"]) is int and resources["oom_kill"] == 0
        and type(resources["memory_peak_bytes"]) is int and resources["memory_peak_bytes"] >= 0
        and type(resources["populated_before_cleanup"]) is int and resources["populated_before_cleanup"] in (0, 1))


def staging_evidence(plan):
    stage, inspection, cache, view = helpers()
    raw, receipt = read(plan["staging_receipt"])
    require(consumer.digest(raw) == sha(plan["staging_receipt_sha256"]))
    profile, _ = view.selection(cache, "original")
    expected = {"schema": stage.SCHEMA, "mode": stage.MODE, "model_profile": profile,
        "producer": sha(plan["staging_producer"], 40), "script_sha256": STAGING_SHA,
        "inspection_helper_sha256": stage.INSPECTION_SHA256, "view_helper_sha256": stage.VIEW_SHA256,
        "cache_helper_sha256": inspection.CACHE_SHA256, "resource_helper_sha256": inspection.RESOURCE_SHA256,
        "supervisor_sha256": cache.PROBE_SHA256, "profile_helper_sha256": cache.PROFILE_SHA256,
        "publication_helper_sha256": cache.PUBLICATION_SHA256, "result": "pass", "failure": None,
        "publication": "exclusive-directory-copy", "cleanup": "worker-completed", "worker_exit_confirmed": True,
        "files": 83, "verified_bytes": profile["total_bytes"], "source_preserved": True,
        "source_written": False, "source_removed": False, "partial_removed": False,
        "host_visible_mount_created": False, "immutable": False, "runtime_ready": False, "confidential": False,
        "private_namespace_verified": True, "private_mount_cleanup_confirmed": True}
    require(set(receipt) == set(expected) | {"attempt_id", "staging_intent_sha256", "completion_sha256",
        "timeout_seconds", "resources", "started_utc", "ended_utc"})
    require(all(type(receipt[k]) is type(v) and receipt[k] == v for k, v in expected.items()))
    attempt = sha(receipt["attempt_id"], 32)
    directory = Path(plan["staging_ownership"])
    iraw, intent = read(directory / "staging-intent.json")
    craw, created = read(directory / "destination-created.json")
    done_raw, done = read(directory / "completion.json")
    require(consumer.digest(iraw) == receipt["staging_intent_sha256"]
            and consumer.digest(done_raw) == receipt["completion_sha256"])
    require(set(intent) == {"schema", "attempt_id", "producer", "script_sha256", "model_profile", "mode",
        "source_export_sha256", "destination_export_sha256", "mount_targets_sha256", "source_relative_path",
        "destination_wrapper", "model_relative_path"})
    require(intent["schema"] == "nib.original-weight-staging-intent/v1"
        and all(intent[k] == receipt[k] for k in ("attempt_id", "producer", "script_sha256", "model_profile", "mode"))
        and intent["source_relative_path"] == "deepseek-v4.1-flash-1017"
        and intent["model_relative_path"] == "model" and intent["destination_wrapper"] == ".nib-original-view-" + attempt
        and intent["destination_export_sha256"] == consumer.digest(plan["destination_export"].encode("ascii")))
    for key in ("source_export_sha256", "destination_export_sha256", "mount_targets_sha256"):
        sha(intent[key])
    require(intent["source_export_sha256"] != intent["destination_export_sha256"])
    require(set(done) == {"schema", "staging_intent_sha256", "worker_acknowledgment", "resources"}
        and done["schema"] == "nib.original-weight-staging-completion/v1"
        and done["staging_intent_sha256"] == receipt["staging_intent_sha256"])
    destination = stage.destination_identity(done["worker_acknowledgment"]["destination"])
    require(consumer.encoded(done["worker_acknowledgment"]) == consumer.encoded(stage.acknowledgment(
        profile, attempt, receipt["staging_intent_sha256"], destination))
        and consumer.digest(craw) == destination["created_record_sha256"]
        and created == stage.created_record(receipt["staging_intent_sha256"],
            {k: destination[k] for k in ("wrapper_device", "wrapper_inode")}))
    resources = receipt["resources"]
    require(consumer.encoded(resources) == consumer.encoded(done["resources"]))
    resource_success(resources, inspection, cache)
    return {"receipt_sha256": consumer.digest(raw), "intent_sha256": consumer.digest(iraw),
        "created_sha256": consumer.digest(craw), "completion_sha256": consumer.digest(done_raw),
        "producer": receipt["producer"], "attempt_id": attempt,
        "wrapper": intent["destination_wrapper"], "model_profile": profile,
        "destination": destination}


def reopened_identity(evidence, mounted, wrapper, model):
    original = evidence["destination"]
    # st_dev names a client-side mounted filesystem, not a durable NFS export
    # identity. Keep the historic devices in evidence; compare file IDs only
    # within the exact receipt-bound export, with fresh device/mount affinity.
    require(wrapper.st_dev == model.st_dev == mounted["device"]
        and wrapper.st_ino == original["wrapper_inode"] and model.st_ino == original["model_inode"]
        and stat.S_ISDIR(wrapper.st_mode) and stat.S_ISDIR(model.st_mode)
        and wrapper.st_uid == model.st_uid == 0 and stat.S_IMODE(wrapper.st_mode) == 0o700
        and stat.S_IMODE(model.st_mode) == 0o555)
    return {"device": model.st_dev, "inode": model.st_ino}


def prepare_configuration(args, admitted, selected, root, *, publish=True):
    contract, _, _, _, build, _, _, inputs = admitted
    require(args.execution_profile == consumer.PROFILE and not any(getattr(args, name, False) for name in
        ("inference_smoke", "conformance_smoke", "protocol_smoke", "tool_smoke", "long_qualification")))
    raw, plan = read(args.original_view_plan)
    require(consumer.digest(raw) == sha(args.original_view_plan_sha256))
    require(set(plan) == {"schema", "attempt_id", "producer", "mount_helper_sha256", "staging_receipt",
        "staging_receipt_sha256", "staging_producer", "staging_ownership", "destination_export", "target",
        "verification_seconds", "alias_exclusion_evidence_sha256", "alias_exclusion_scope"}
        and plan["schema"] == "nib.original-view-plan/v1"
        and plan["alias_exclusion_scope"] == "until-owned-mount-and-consumers-absent")
    sha(plan["alias_exclusion_evidence_sha256"])
    sha(plan["attempt_id"], 32)
    sha(plan["producer"], 40)
    require(plan["mount_helper_sha256"] == consumer.digest(Path(__file__).read_bytes()))
    require(type(plan["verification_seconds"]) is int and 60 < plan["verification_seconds"] <= 3600
            and time.monotonic() + plan["verification_seconds"] < contract.startup_deadline)
    stage, _, cache, _ = helpers()
    cache.cache_source(plan["destination_export"], "nfs")
    evidence = staging_evidence(plan)
    require(plan["attempt_id"] != evidence["attempt_id"])
    target = smoke.private_path(plan["target"])
    require(smoke.private_path(args.weights) == target / evidence["wrapper"] / "model")
    for path in (root, Path(inputs), Path(args.original_view_plan), Path(plan["staging_receipt"]),
                 Path(plan["staging_ownership"]), ROOT):
        require(not path.is_relative_to(target) and not target.is_relative_to(path))
    stage.record_filesystem(cache, root)
    with cache.directory(target, private=True) as fd, os.scandir(fd) as entries:
        require(next(entries, None) is None)
        identity = os.fstat(fd)
    cache.require_unmounted(target)
    row = {"schema": "nib.original-view-mount-contract/v1", "plan": plan, "staging": evidence,
        "plan_sha256": consumer.digest(raw), "inputs": str(inputs), "weights": str(args.weights),
        "prepared_sha256": build["prepared_sha256"], "image_id": build["image_id"],
        "consumer_token": selected.policy.token, "hard_wall": contract.hard_wall,
        "hard_monotonic": contract.hard_monotonic, "startup_deadline": contract.startup_deadline,
        "consumer_hard_stop_at": smoke.service.retirement_at(contract.hard_wall - smoke.HOST_RESERVE),
        "boot_id": helpers()[1].supervisor_identity()["boot_id"], "namespace": consumer.namespace(),
        "clock_namespace": consumer.clock_namespace(),
        "target": {"device": identity.st_dev, "inode": identity.st_ino}}
    if publish:
        with cache.directory(root / "original-view", private=True) as parent:
            fd = os.open("admission.lock", os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            os.fsync(fd)
            os.close(fd)
            os.fsync(parent)
        Records(root / "original-view").publish("contract.json", row)
    return row


def mount_readback(row):
    stage, inspection, cache, _ = helpers()
    target = Path(row["plan"]["target"])
    require(consumer.namespace() == row["namespace"]
            and inspection.supervisor_identity()["boot_id"] == row["boot_id"])
    mount, device = stage.mount_binding(inspection, cache, row["plan"]["destination_export"], target, True)
    value = json.loads(cache.command(["findmnt", "--json", "--id", str(mount), "--output", "PROPAGATION"]),
                       object_pairs_hook=cache.unique)
    require(value == {"filesystems": [{"propagation": "private"}]})
    return {"mount_id": mount, "device": device, "namespace": row["namespace"], "boot_id": row["boot_id"]}


def worker(directory, expected, operation):
    stage, inspection, cache, view = helpers()
    root = Path(directory)
    raw, row = read(root / "contract.json")
    require(consumer.digest(raw) == expected and operation in ("attach", "unmount"))
    target, export = Path(row["plan"]["target"]), row["plan"]["destination_export"]
    records = Records(root)
    if operation == "unmount":
        _, created = read(root / "mount-created.json")
        require(created["contract_sha256"] == expected and mount_readback(row) == created["mount"])
        cache.unmount_cache(export, target)
        records.publish("unmounted.json", {"contract_sha256": expected, "ordinary_unmount": True})
    else:
        startup_current(row)
        require(consumer.namespace() == row["namespace"] and inspection.supervisor_identity()["boot_id"] == row["boot_id"])
        cache.require_unmounted(target)
        with cache.directory(target, private=True) as fd, os.scandir(fd) as entries:
            info = os.fstat(fd)
            require(next(entries, None) is None and {"device": info.st_dev, "inode": info.st_ino} == row["target"])
        # Do not change propagation. Unsupported host topology is a pre-mount rejection.
        propagation = json.loads(cache.command(["findmnt", "--json", "--target", str(target),
            "--output", "PROPAGATION"]), object_pairs_hook=cache.unique)
        require(propagation == {"filesystems": [{"propagation": "private"}]})
        records.publish("mount-intent.json", {"contract_sha256": expected})
        with startup_window(row):
            cache.mount_cache(export, target, readonly=True)
        mounted = mount_readback(row)
        records.publish("mount-created.json", {"contract_sha256": expected, "mount": mounted})
        model = Path(row["weights"])
        with cache.directory(model.parent, private=True) as wrapper, cache.directory(model) as fd:
            require(inspection.mount_id(wrapper) == inspection.mount_id(fd) == mounted["mount_id"])
            info = os.fstat(fd)
            wrapper_info = os.fstat(wrapper)
            current_identity = reopened_identity(row["staging"], mounted, wrapper_info, info)
            profile = row["staging"]["model_profile"]
            rows = cache.manifest_rows(cache.read_regular(ROOT / profile["pack"] / "files.sha256"),
                profile["manifest_sha256"], profile["total_bytes"], 83)
            end = row["startup_deadline"]
            before = cache.strict_inventory(model, rows)
            verified = {r[2]: inspection.hash_file(cache, model, r, end, mounted["mount_id"]) for r in rows}
            view.view_modes(cache, model, rows, end)
            require(before == verified == cache.strict_inventory(model, rows) and mount_readback(row) == mounted
                and os.path.samestat(info, model.stat()) and os.path.samestat(wrapper_info, model.parent.stat())
                and reopened_identity(row["staging"], mounted, os.fstat(wrapper), os.fstat(fd)) == current_identity
                and time.monotonic() < end)
        records.publish("verified.json", {"contract_sha256": expected, "mount": mounted,
            "model": current_identity, "files": 83,
            "verified_bytes": profile["total_bytes"], "inventory_sha256": consumer.digest(consumer.encoded(verified))})
        startup_current(row)
    print(json.dumps({"schema": "nib.original-view-worker/v1", "operation": operation, "contract_sha256": expected}))


@contextmanager
def admission_lock(root):
    _, _, cache, _ = helpers()
    with cache.directory(root, private=True) as parent:
        fd = os.open("admission.lock", os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1
                    and stat.S_IMODE(info.st_mode) == 0o600)
            # Existing public-cache filesystem admission tests this exact
            # nonblocking flock contract. Contention rejects a new launch.
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            require(os.path.samestat(info, os.stat("admission.lock", dir_fd=parent, follow_symlinks=False)))
            yield fd
        finally:
            os.close(fd)


@contextmanager
def registration(selected_owner):
    root = selected_owner.state.root.parent / "original-view"
    with admission_lock(root):
        raw, contract = read(root / "contract.json")
        _, admitted = read(root / "admitted.json")
        require(not (root / "closed.json").exists() and admitted["contract_sha256"] == consumer.digest(raw)
            and time.monotonic() < contract["startup_deadline"] and time.time() < contract["hard_wall"]
            and selected_owner.selected.policy.token == contract["consumer_token"]
            and selected_owner.runtime["image_id"] == contract["image_id"]
            and selected_owner.runtime["weights"] == contract["weights"]
            and selected_owner.runtime["inputs"] == contract["inputs"])
        command = selected_owner.runtime["command"]
        require(command[command.index("--original-view-sha256") + 1] == admitted["consumer_sha256"])
        Records(root).publish("registered.json", {"contract_sha256": consumer.digest(raw),
            "runtime_sha256": consumer.digest(consumer.encoded(selected_owner.runtime)),
            "owner_sha256": consumer.digest(consumer.encoded(selected_owner.contract))})
        # The closure lock covers Docker create and its resolved-ID record, not
        # just the pre-create check. The watchdog cannot unmount in that gap.
        startup_current(contract)
        yield lambda: startup_window(contract)


def consumer_sha(root):
    _, row = read(Path(root) / "original-view/contract.json")
    raw, value = read(Path(row["inputs"]) / consumer.CONTRACT, uid=65532)
    consumer.validate(value)
    require(value["mount_contract_sha256"] == consumer.digest(read(Path(root) / "original-view/contract.json")[0]))
    _, admitted = read(Path(root) / "original-view/admitted.json")
    require(admitted["consumer_sha256"] == consumer.digest(raw)
            and admitted["contract_sha256"] == value["mount_contract_sha256"])
    return consumer.digest(raw)


def verify_release(selected_owner):
    root = selected_owner.state.root.parent / "original-view"
    raw, contract = read(root / "contract.json")
    _, proof = read(root / "consumer-verified.json")
    _, joined = read(root / "consumer-joined.json")
    _, admitted = read(root / "admitted.json")
    _, verified = read(root / "verified.json")
    _, registered = read(root / "registered.json")
    craw, value = read(Path(contract["inputs"]) / consumer.CONTRACT, uid=65532)
    consumer.validate(value)
    require(proof["contract_sha256"] == consumer.digest(raw)
        and value["mount_contract_sha256"] == consumer.digest(raw)
        and admitted["contract_sha256"] == verified["contract_sha256"] == joined["contract_sha256"] == consumer.digest(raw)
        and admitted["consumer_sha256"] == consumer.digest(craw)
        and registered == {"contract_sha256": consumer.digest(raw),
            "runtime_sha256": consumer.digest(consumer.encoded(selected_owner.runtime)),
            "owner_sha256": consumer.digest(consumer.encoded(selected_owner.contract))}
        and value["prepared_sha256"] == contract["prepared_sha256"]
        and value["model_profile"] == contract["staging"]["model_profile"]
        and value["attempt_id"] == contract["plan"]["attempt_id"]
        and value["consumer_token"] == contract["consumer_token"]
        and value["hard_stop_at"] == contract["consumer_hard_stop_at"]
        and value["startup_deadline"] == contract["startup_deadline"]
        and value["boot_id"] == contract["boot_id"]
        and value["clock_namespace"] == contract["clock_namespace"]
        and value["model"] == verified["model"]
        and joined["host_pid"] == proof["host_pid"]
        and joined["mount_namespace"] == proof["acknowledgment"]["mount_namespace"]
        and proof["container_id"] == selected_owner.rows()["container-resolved.json"]["id"]
        and consumer.encoded(proof["acknowledgment"]) == consumer.encoded(consumer.acknowledgment(
            value, consumer.digest(craw), "verified", proof["acknowledgment"]["mount_namespace"])))
    info = selected_owner.fresh(serving=False, verifying=True)
    require(info["State"]["Pid"] == proof["host_pid"] and info["State"]["Running"]
            and consumer.namespace(proof["host_pid"]) == proof["acknowledgment"]["mount_namespace"])
    startup_current(contract)
    return contract


class Lease:
    def __init__(self, root, commands):
        self.root, self.commands = Path(root) / "original-view", commands
        raw, self.row = read(self.root / "contract.json")
        self.sha, self.records = consumer.digest(raw), Records(self.root)
        self.stack, self.listener, self.connection, self.peer = ExitStack(), None, None, None
        self.closed, self.registered, self.released, self.mount_attempted = False, False, False, False
        self.unmount_attempted = False
        self.admission_sealed, self.closure_recorded = False, False
        self.worker_states = []
        try:
            self.bind()
        except BaseException:
            self.stack.close()
            raise

    def bind(self):
        _, inspection, cache, _ = helpers()
        require(self.row["boot_id"] == inspection.supervisor_identity()["boot_id"]
            and self.row["namespace"] == consumer.namespace()
            and self.row["clock_namespace"] == consumer.clock_namespace())
        daemon = owner.daemon_identity(self.commands)
        self.daemon_fd = os.pidfd_open(daemon[0], 0)
        self.stack.callback(os.close, self.daemon_fd)
        require(owner.daemon_identity(self.commands, daemon[0]) == daemon
                and consumer.namespace(daemon[0]) == consumer.namespace())
        self.daemon = daemon
        self.daemon_namespace = self.stack.enter_context(os.fdopen(
            os.open("/proc/" + str(daemon[0]) + "/ns/mnt", os.O_RDONLY), "rb"))
        space = os.fstat(self.daemon_namespace.fileno())
        require({"device": space.st_dev, "inode": space.st_ino} == self.row["namespace"])
        self.records.publish("watchdog.json", {"contract_sha256": self.sha,
            "identity": inspection.supervisor_identity(), "daemon": daemon, "namespace": consumer.namespace()})
        self.source = cache.read_regular(Path(__file__).absolute())
        require(consumer.digest(self.source) == self.row["plan"]["mount_helper_sha256"])

    def fresh(self):
        require(not select.select([self.daemon_fd], [], [], 0)[0]
            and owner.daemon_identity(self.commands, self.daemon[0]) == self.daemon
            and consumer.namespace(self.daemon[0]) == consumer.namespace() == self.row["namespace"])

    def capture(self, operation):
        _, inspection, cache, _ = helpers()
        if operation == "attach":
            startup_current(self.row)
        state = {}
        owned = inspection.Ownership(cache, self.root / ("worker-" + operation), self.row["plan"]["attempt_id"],
            self.row["plan"]["producer"], consumer.digest(self.source))
        self.worker_states.append((state, owned.directory))
        seconds = self.row["plan"]["verification_seconds"] - 60 if operation == "attach" else 45
        self.mount_attempted |= operation == "attach"
        if operation == "unmount":
            require(not self.unmount_attempted)
            self.unmount_attempted = True
        capture = cache.load_capture()
        argv = [sys.executable, "-I", "-S", "-B", "-c", BOOTSTRAP, str(Path(__file__).absolute()),
                self.source.decode(), str(self.root), self.sha, operation]
        if operation == "attach":
            seconds = min(seconds, startup_current(self.row))
        raw = inspection.bounded_capture(cache, capture, argv, seconds, state, owned)
        resource_success(state, inspection, cache)
        require(json.loads(raw, object_pairs_hook=cache.unique) == {
            "schema": "nib.original-view-worker/v1", "operation": operation, "contract_sha256": self.sha})

    def prepare(self):
        self.fresh()
        self.capture("attach")
        self.fresh()
        _, verified = read(self.root / "verified.json")
        require(verified["contract_sha256"] == self.sha)
        startup_current(self.row)
        self.consumer = consumer.validate({"schema": consumer.SCHEMA,
            "attempt_id": self.row["plan"]["attempt_id"], "mount_contract_sha256": self.sha,
            "prepared_sha256": self.row["prepared_sha256"], "model_profile": self.row["staging"]["model_profile"],
            "model": verified["model"], "hard_stop_at": self.row["consumer_hard_stop_at"],
            "startup_deadline": self.row["startup_deadline"], "boot_id": self.row["boot_id"],
            "clock_namespace": self.row["clock_namespace"],
            "consumer_token": self.row["consumer_token"]})
        raw = consumer.encoded(self.consumer)
        path = Path(self.row["inputs"]) / consumer.CONTRACT
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fchown(stream.fileno(), 65532, 65532)
            os.fsync(stream.fileno())
        self.consumer_sha = consumer.digest(raw)
        startup_current(self.row)
        self.listener = self.stack.enter_context(socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET))
        self.listener.bind(str(Path(self.row["inputs"]) / consumer.SOCKET))
        os.chmod(Path(self.row["inputs"]) / consumer.SOCKET, 0o600)
        os.chown(Path(self.row["inputs"]) / consumer.SOCKET, 65532, 65532)
        self.listener.listen(1)
        self.listener.setblocking(False)
        startup_current(self.row)
        self.records.publish("admitted.json", {"contract_sha256": self.sha, "consumer_sha256": self.consumer_sha,
            "resources": self.worker_states[0][0]})
        startup_current(self.row)

    def admitted(self, selected_owner):
        self.fresh()
        require(not self.closed and not self.registered)
        _, registered = read(self.root / "registered.json")
        require(registered == {"contract_sha256": self.sha,
            "runtime_sha256": consumer.digest(consumer.encoded(selected_owner.runtime)),
            "owner_sha256": consumer.digest(consumer.encoded(selected_owner.contract))})
        startup_current(self.row)
        self.registered = True

    def poll(self, selected_owner):
        self.fresh()
        require(not self.closed and self.registered)
        if self.connection is None:
            if not select.select([self.listener], [], [], 0)[0]:
                return False
            self.connection, _ = self.listener.accept()
            self.stack.callback(self.connection.close)
            self.connection.setblocking(False)
            pid, uid, gid = struct.unpack("3i", self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            require(uid == gid == 65532 and pid > 1)
            fd = os.pidfd_open(pid, 0)
            self.stack.callback(os.close, fd)
            info = selected_owner.fresh(serving=False, verifying=True)
            require(info["State"]["Running"] and info["State"]["Pid"] == pid)
            self.peer = (pid, fd, consumer.namespace(pid))
            require(self.peer[2] != self.row["namespace"] and not select.select([fd], [], [], 0)[0])
            self.namespace_handle = self.stack.enter_context(os.fdopen(
                os.open("/proc/" + str(pid) + "/ns/mnt", os.O_RDONLY), "rb"))
            space = os.fstat(self.namespace_handle.fileno())
            require({"device": space.st_dev, "inode": space.st_ino} == self.peer[2])
            _, inspection, cache, _ = helpers()
            resource = inspection.resource_helper(cache)
            raw_group = smoke.service.observer.read_kernel("/proc/" + str(pid) + "/cgroup", 4096,
                                                          time.monotonic() + 1).decode("ascii")
            require(re.fullmatch(r"0::/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\n", raw_group))
            relative = raw_group[4:-1]
            require(all(part not in ("", ".", "..") for part in relative.split("/")))
            self.cgroup_path = resource.BASE / relative
            self.cgroup_fd = self.stack.enter_context(cache.directory(self.cgroup_path, trusted=True))
            self.cgroup_identity = os.fstat(self.cgroup_fd)
            require(resource.numeric_fields(resource.read(self.cgroup_fd, "cgroup.events"))["populated"] == 1)
            self.records.publish("consumer-joined.json", {"contract_sha256": self.sha, "host_pid": pid,
                "mount_namespace": self.peer[2], "cgroup_relative": relative,
                "cgroup_device": self.cgroup_identity.st_dev, "cgroup_inode": self.cgroup_identity.st_ino})
            self.phase = "joined"
        pid, fd, space = self.peer
        require(not select.select([fd], [], [], 0)[0] and consumer.namespace(pid) == space)
        if not select.select([self.connection], [], [], 0)[0]:
            return False
        raw = self.connection.recv(4097)
        require(raw == consumer.encoded(consumer.acknowledgment(self.consumer, self.consumer_sha, self.phase, space)))
        if self.phase == "joined":
            self.phase = "verified"
            return False
        selected_owner.fresh(serving=False, verifying=True)
        self.records.publish("consumer-verified.json", {"contract_sha256": self.sha,
            "container_id": selected_owner.rows()["container-resolved.json"]["id"],
            "acknowledgment": json.loads(raw), "host_pid": pid})
        startup_current(self.row)
        return True

    def release(self):
        require(not self.closed and not self.released and self.phase == "verified")
        raw = consumer.encoded(consumer.release(self.consumer_sha))
        startup_current(self.row)
        require(self.connection.send(raw) == len(raw))
        self.released = True

    def observe(self):
        self.fresh()
        require(self.released and self.peer is not None)
        ready = select.select([self.peer[1], self.connection], [], [], 0)[0]
        if self.peer[1] in ready:
            return True
        # While PID 1 is alive, readable means EOF or an unsolicited packet.
        require(self.connection not in ready)
        return False

    def close_admission(self):
        self.closed = True
        if self.connection is not None:
            self.connection.close()
        if self.listener is not None:
            self.listener.close()
        if not self.admission_sealed:
            self.stack.enter_context(admission_lock(self.root))
            self.admission_sealed = True
        # The in-memory closure precedes IO and still applies if publication fails.
        self.records.publish("closed.json", {"contract_sha256": self.sha})
        self.closure_recorded = True

    def workers_absent(self):
        _, inspection, cache, _ = helpers()
        resource = inspection.resource_helper(cache)
        absent = True
        for state, directory in self.worker_states:
            if state.get("group_removed") is True and state.get("empty_verified") is True:
                continue
            try:
                _, row = read(directory / "group.json")
                require(row["schema"] == "nib.cache-inspection-ownership/v1" and row["kind"] == "group"
                    and row["attempt_id"] == self.row["plan"]["attempt_id"]
                    and row["producer"] == self.row["plan"]["producer"]
                    and row["script_sha256"] == consumer.digest(self.source)
                    and row["group"]["limits"] == resource.LIMITS
                    and re.fullmatch(r"nib-build-[0-9a-f]{32}", row["group"]["name"]))
                try:
                    info = (resource.BASE / row["group"]["name"]).stat()
                except FileNotFoundError:
                    continue
                require((info.st_dev, info.st_ino) == (row["group"]["device"], row["group"]["inode"]))
                # Read only the bound group, including detached descendants.
                with cache.directory(resource.BASE / row["group"]["name"], trusted=True) as fd:
                    require(os.path.samestat(info, os.fstat(fd)))
                    resource.numeric_fields(resource.read(fd, "cgroup.events"))
                absent = False
            except BaseException:
                absent = False
        require(absent)

    def daemon_check(self, observations):
        require(not select.select([self.daemon_fd], [], [], 0)[0]
            and owner.daemon_identity(observations, self.daemon[0]) == self.daemon
            and consumer.namespace(self.daemon[0]) == consumer.namespace() == self.row["namespace"])

    def containers_absent(self, selected_owner, observations):
        if (self.root / "registered.json").exists():
            require(selected_owner is not None)
            rows = selected_owner.rows()
            identifier = rows["container-resolved.json"]["id"]
            name, token, image = "nib-selected-" + self.row["consumer_token"], self.row["consumer_token"], self.row["image_id"]
            require(observations.owned(name, token, image, identifier) is None
                    and observations.owned(name, token, image) is None)
            require(observations.call(["docker", "container", "ls", "--all", "--quiet", "--no-trunc"], limit=128).strip() == b"")
            # Without an observed PID/namespace, a published start intent is
            # unresolved even if the container later disappears from inventory.
            require(("start-intent.json" not in rows and self.peer is None)
                    or (self.peer is not None and select.select([self.peer[1]], [], [], 0)[0]))

    def descendants_absent(self):
        if self.peer is not None:
            _, inspection, cache, _ = helpers()
            resource = inspection.resource_helper(cache)
            require(select.select([self.peer[1]], [], [], 0)[0])
            try:
                require(os.path.samestat(self.cgroup_identity, self.cgroup_path.stat())
                    and resource.numeric_fields(resource.read(self.cgroup_fd, "cgroup.events"))["populated"] == 0)
            except FileNotFoundError:
                # A removed cgroup cannot retain populated descendants.
                require(not self.cgroup_path.exists())

    def retire(self, selected_owner, cleanup):
        # This fresh, finite budget is ONLY for observation and one ordinary
        # unmount. It never renews serving, registration or launch deadlines.
        observations = network.Commands(self.commands.recorder, self.commands.env, time.time() + 60,
                                        time.monotonic() + 60)
        absent = True
        for observe in (lambda: self.daemon_check(observations),
                        lambda: self.containers_absent(selected_owner, observations),
                        self.descendants_absent, self.workers_absent):
            try:
                observe()
            except BaseException:
                absent = False
        # Failed closure publication never stops the independent observations
        # above and never grants permission to unmount.
        require(absent and self.closed and self.admission_sealed and self.closure_recorded)
        if self.peer is not None:
            self.namespace_handle.close()
        if self.mount_attempted:
            if self.unmount_attempted:
                _, done = read(self.root / "unmounted.json")
                require(done == {"contract_sha256": self.sha, "ordinary_unmount": True})
                helpers()[2].require_unmounted(Path(self.row["plan"]["target"]))
            elif (self.root / "mount-created.json").exists():
                self.capture("unmount")
            else:
                # Missing ownership never authorizes cleanup. Only already
                # absent mounts can end this unknown state without adoption.
                helpers()[2].require_unmounted(Path(self.row["plan"]["target"]))
        self.stack.close()

    def retain_until_absent(self, selected_owner, cleanup):
        # Logging is deliberately outside the cleanup decision. No failed
        # write, fsync or lost parent pipe abandons an uncertain host mount.
        while True:
            try:
                if not self.admission_sealed:
                    try:
                        self.close_admission()
                    except BaseException:
                        pass
                self.retire(selected_owner, cleanup)
                return
            except BaseException:
                try:
                    time.sleep(1)
                except BaseException:
                    pass
