# SPDX-License-Identifier: Apache-2.0
"""Original-only verification hold, carried by a newly prepared service image."""

from dataclasses import replace
import hashlib
import json
import math
import os
from pathlib import Path
import re
import select
import socket
import stat
import struct
import time

import selected_service as service

require = service.require
PROFILE = service.engine_entry.ORIGINAL_MILLION_PROFILE
DIRECTORY = Path("/run/nib-inputs")
CONTRACT = "original-view.json"
SOCKET = "original-view.sock"
SCHEMA = "nib.original-view-consumer/v1"


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def namespace(pid="self"):
    info = os.stat("/proc/" + str(pid) + "/ns/mnt")
    return {"device": info.st_dev, "inode": info.st_ino}


def clock_namespace():
    info = os.stat("/proc/self/ns/time")
    return {"device": info.st_dev, "inode": info.st_ino}


def validate(value):
    require(type(value) is dict and set(value) == {"schema", "attempt_id", "mount_contract_sha256",
        "prepared_sha256", "model_profile", "model", "hard_stop_at", "consumer_token",
        "startup_deadline", "boot_id", "clock_namespace"}
        and value["schema"] == SCHEMA and value["model_profile"] == service.models.select("original"), "mounts")
    for name, length in (("attempt_id", 32), ("consumer_token", 32),
                         ("mount_contract_sha256", 64), ("prepared_sha256", 64)):
        require(type(value[name]) is str and re.fullmatch("[0-9a-f]{" + str(length) + "}", value[name]), "mounts")
    model = value["model"]
    require(type(model) is dict and set(model) == {"device", "inode"}
            and type(model["device"]) is int and model["device"] >= 0
            and type(model["inode"]) is int and model["inode"] > 0, "mounts")
    require(type(value["hard_stop_at"]) is str, "mounts")
    require(type(value["startup_deadline"]) in (int, float)
            and math.isfinite(value["startup_deadline"]) and value["startup_deadline"] > 0
            and type(value["boot_id"]) is str
            and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value["boot_id"]), "mounts")
    space = value["clock_namespace"]
    require(type(space) is dict and set(space) == {"device", "inode"}
            and all(type(space[k]) is int and space[k] > 0 for k in space), "mounts")
    return value


def load_contract(expected, contract):
    require(contract.execution_profile == PROFILE and os.getpid() == 1
            and os.getuid() == os.geteuid() == os.getgid() == os.getegid() == 65532, "mounts")
    require(type(expected) is str and re.fullmatch("[0-9a-f]{64}", expected), "mounts")
    raw = service.identity.private_read(DIRECTORY / CONTRACT, contract.startup_deadline)
    require(digest(raw) == expected, "mounts")
    value = validate(service.correctness.strict_json(raw))
    boot = service.observer.read_kernel("/proc/sys/kernel/random/boot_id", 64,
                                        contract.startup_deadline).decode("ascii").strip()
    require(boot == value["boot_id"] and clock_namespace() == value["clock_namespace"]
            and value["hard_stop_at"] == contract.hard_stop_at
            and time.monotonic() < min(contract.startup_deadline, value["startup_deadline"]), "deadline")
    return value


def clamp_contract(expected, contract):
    # Same boot and actual time namespace establish a shared monotonic clock.
    # Neither a late container start nor preparation can re-arm the host cutoff.
    value = load_contract(expected, contract)
    return replace(contract, startup_deadline=min(contract.startup_deadline, value["startup_deadline"]))


def acknowledgment(value, contract_sha, phase, space):
    validate(value)
    require(phase in ("joined", "verified"), "mounts")
    return {"schema": "nib.original-view-ack/v1", "phase": phase,
        "contract_sha256": contract_sha, "prepared_sha256": value["prepared_sha256"],
        "manifest_sha256": value["model_profile"]["manifest_sha256"], "uid": 65532, "gid": 65532,
        "pid": 1, "mount_namespace": space, "model": value["model"], "readonly": True,
        "startup_deadline": value["startup_deadline"], "boot_id": value["boot_id"],
        "clock_namespace": value["clock_namespace"],
        "files": 83 if phase == "verified" else 0,
        "verified_bytes": value["model_profile"]["total_bytes"] if phase == "verified" else 0}


def release(contract_sha):
    return {"schema": "nib.original-view-release/v1", "contract_sha256": contract_sha}


class Hold:
    """One connection; EOF/extra data is failure, never a reconnect or retry."""

    def __init__(self, expected, prepared, contract, shutdown):
        self.value = load_contract(expected, contract)
        require(self.value["prepared_sha256"] == prepared.receipt_sha256
                and contract.startup_deadline <= self.value["startup_deadline"], "mounts")
        self.sha, self.shutdown, self.prepared = expected, shutdown, prepared
        self.space, self.connection = namespace(), None
        self.model_check()
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        try:
            connection.settimeout(min(5, max(.001, contract.startup_deadline - time.monotonic())))
            connection.connect(str(DIRECTORY / SOCKET))
            _, uid, gid = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            require(uid == gid == 0, "mounts")
            self.connection = connection
            self.send("joined")
        except BaseException:
            connection.close()
            self.connection = None
            raise

    def model_check(self):
        fd = os.open("/model", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            require(stat.S_IMODE(info.st_mode) == 0o555 and info.st_uid == 0
                    and {"device": info.st_dev, "inode": info.st_ino} == self.value["model"]
                    and os.fstatvfs(fd).f_flag & os.ST_RDONLY
                    and namespace() == self.space, "mounts")
        finally:
            os.close(fd)

    def send(self, phase):
        self.model_check()
        raw = encoded(acknowledgment(self.value, self.sha, phase, self.space))
        require(self.connection.send(raw) == len(raw), "mounts")

    def check(self):
        require(not self.shutdown.is_set() and self.connection is not None, "cancelled")
        require(not select.select([self.connection], [], [], 0)[0], "mounts")

    def verified(self, deadline):
        self.check()
        self.send("verified")
        while True:
            require(not self.shutdown.is_set() and time.monotonic() < deadline, "cancelled")
            if select.select([self.connection], [], [], min(.1, max(0, deadline - time.monotonic())))[0]:
                raw = self.connection.recv(4097)
                require(raw == encoded(release(self.sha)), "mounts")
                self.model_check()
                require(not self.shutdown.is_set() and time.monotonic() < deadline, "deadline")
                return

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None
