#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Pinned lab GPU appraisal; readiness requires fresh appraisal and explicit approval."""

import sys

if __name__ == "__main__" and not (
        sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    sys.exit("Use python3 -I -S -B host/attest-lab-gpus.py")

import argparse
import base64
import ctypes as C
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import secrets
import stat
import subprocess
import tempfile
import time
import types

SOURCE = "11b2bfeb42a1158aa08744603ecb239ccb3d2265"
VERSION = "2.0.0.1790976895-1"
PACKAGES = {
    "nvattest": "f717e8749cf51921b06871ec54084f56c03a6e93d5daeff967f3694f3c1155e1",
    "libnvat": "cca9409160a7bf793323cc0aafcdc1f569ecc99f346fc6abc6961c850a153b22",
}
PACKAGE_SIZES = {"nvattest": 321056, "libnvat": 6152144}
# NVIDIA Ubuntu 26.04 amd64 packages, inspected without executing their code.
# https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2604/x86_64/
FILES = {
    "/usr/bin/nvattest": (1128696, "9c269a22c3f4710cdb115ce21d72c1c3b8e9c9e2db9cf4a391b510ed1a1dea42"),
    "/usr/lib/x86_64-linux-gnu/libnvat.so.2.0.0":
        (26794312, "e757a59f8241830553da3f99f717ddd44d47973ca756a4fa3feac78012dce896"),
}
LIB_ALIAS = "/usr/lib/x86_64-linux-gnu/libnvat.so.2"
PROBE_SHA256 = "dd196679dc4b6d7ee335b3d62900b25484734884bd3da6e74f925260ad7864ff"
NRAS = "https://nras.attestation.nvidia.com"
LIMIT = 4 << 20
SECONDS = 240
COUNT = 8
TRUE_CLAIMS = (
    "secboot", "x-nvidia-gpu-arch-check",
    "x-nvidia-gpu-attestation-report-cert-chain-fwid-match",
    "x-nvidia-gpu-attestation-report-parsed",
    "x-nvidia-gpu-attestation-report-nonce-match",
    "x-nvidia-gpu-attestation-report-signature-verified",
    "x-nvidia-gpu-driver-rim-fetched", "x-nvidia-gpu-driver-rim-signature-verified",
    "x-nvidia-gpu-driver-rim-version-match", "x-nvidia-gpu-driver-rim-measurements-available",
    "x-nvidia-gpu-vbios-rim-fetched", "x-nvidia-gpu-vbios-rim-signature-verified",
    "x-nvidia-gpu-vbios-rim-version-match", "x-nvidia-gpu-vbios-rim-measurements-available",
    "x-nvidia-gpu-vbios-index-no-conflict",
)
CHAINS = ("attestation-report", "driver-rim", "vbios-rim")
READINESS_HEADER = "1a1e86da474d340d7cf58274588f4a4afc8772e7"
# SDK at SOURCE: src/gpu/claims.cpp from_json/to_json and
# include/nv_attestation/utils.h deserialize_optional_shared_ptr.
# These are serialization defaults, never positive security claims.
SIGNED_SERIALIZATION_DEFAULTS = {
    "x-nvidia-device-type": "gpu",
    "x-nvidia-gpu-claims-version": "4.0",
    "x-nvidia-mismatch-measurement-records": None,
    "x-nvidia-mismatch-opaque-data-records": None,
}


def require(value):
    if not value:
        raise ValueError("attestation-check-rejected")


def file_identity(row):
    return (row.st_dev, row.st_ino, row.st_mode, row.st_nlink, row.st_size,
            row.st_mtime_ns, row.st_ctime_ns)


def read_regular(path, limit):
    """No symlinks in any component; no blocking special files or hard links."""
    path = Path(path).absolute()
    require(path.name not in ("", ".", ".."))
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:-1]:
            require(component not in ("", ".", ".."))
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        file = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                       dir_fd=descriptor)
        try:
            before = os.fstat(file)
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                    and 0 <= before.st_size <= limit)
            chunks = []
            remaining = limit + 1
            while remaining:
                chunk = os.read(file, min(262144, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            after = os.fstat(file)
            named = os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
            data = b"".join(chunks)
            require(file_identity(before) == file_identity(after) == file_identity(named)
                    and len(data) == before.st_size <= limit)
            return data
        finally:
            os.close(file)
    finally:
        os.close(descriptor)


def verify_installation(root=Path("/")):
    for name, (size, digest) in FILES.items():
        data = read_regular(root / name.lstrip("/"), size)
        require(len(data) == size and hashlib.sha256(data).hexdigest() == digest)
    alias = root / LIB_ALIAS.lstrip("/")
    before = alias.lstat()
    require(stat.S_ISLNK(before.st_mode) and before.st_nlink == 1
            and os.readlink(alias) == "libnvat.so.2.0.0"
            and file_identity(alias.lstat()) == file_identity(before))
    preload = root / "etc/ld.so.preload"
    if os.path.lexists(preload):
        require(not read_regular(preload, 4096).strip())


def load_capture():
    path = Path(__file__).absolute().with_name("probe.py")
    source = read_regular(path, 65536)
    require(hashlib.sha256(source).hexdigest() == PROBE_SHA256)
    module = types.ModuleType("pinned_lab_capture")
    module.__file__ = str(path)
    exec(compile(source, str(path), "exec", dont_inherit=True, optimize=0), module.__dict__)
    return module.capture


def cli_contract(capture, directory, clock=time.monotonic):
    """Inspect installed package metadata and actual pinned-binary help, offline."""
    deadline = clock() + 30
    env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": directory,
           "TMPDIR": directory, "LANG": "C", "LC_ALL": "C",
           "LD_LIBRARY_PATH": "/usr/lib/x86_64-linux-gnu"}

    def call(argv):
        remaining = deadline - clock()
        require(0 < remaining <= 30)
        return capture(argv, timeout=remaining, env=env, limit=65536).decode("utf-8")

    rows = call(["/usr/bin/dpkg-query", "-W",
                 "-f=${Package}\\t${Version}\\t${Architecture}\\t${db:Status-Abbrev}\\n",
                 "nvattest", "libnvat"]).splitlines()
    parsed = [row.split("\t") for row in rows]
    require(len(parsed) == 2 and all(len(row) == 4 for row in parsed))
    require({row[0] for row in parsed} == set(PACKAGES)
            and all(row[1:] == [VERSION, "amd64", "ii "] for row in parsed))
    for subcommand, expected in (
            ([], ("--format", "--log-level")),
            (["attest"], ("--device", "--nonce", "--verifier", "--gpu-evidence-source", "--nras-url")),
            (["verify-token"], ("--token-file", "--nonce", "--nras-url", "--token-type"))):
        text = call(["/usr/bin/nvattest", *subcommand, "--help"])
        for flag in expected:
            require(re.search(r"(?m)^[ \t]+(?:-[A-Za-z0-9],[ \t]*)?" + re.escape(flag)
                              + r"(?:[ =,\t]|$)", text))


def install_packages(capture, packages, directory, clock=time.monotonic):
    """Install only two pinned local archives, never resolve/download dependencies."""
    require(os.geteuid() == 0)
    release = read_regular("/usr/lib/os-release", 16384).decode("utf-8")
    fields = {}
    for line in release.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            require(key not in fields)
            fields[key] = value.strip('"')
    require(fields.get("ID") == "ubuntu" and fields.get("VERSION_ID") == "26.04")
    env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": directory,
           "TMPDIR": directory, "LANG": "C", "LC_ALL": "C",
           "DEBIAN_FRONTEND": "noninteractive"}
    deadline = clock() + 120

    def call(argv, limit=65536):
        remaining = deadline - clock()
        require(0 < remaining <= 120)
        return capture(argv, timeout=remaining, env=env, limit=limit)

    # Reject upgrades/reinstalls: otherwise old maintainer scripts could execute.
    installed = call(["/usr/bin/dpkg-query", "-W", "-f=${Package}\\n"], LIMIT).decode("utf-8")
    require(not any(name.split(":")[0] in PACKAGES for name in installed.splitlines()))
    require(not any(os.path.lexists(path) for path in (*FILES, LIB_ALIAS)))
    with tempfile.TemporaryDirectory(prefix="packages-", dir=directory) as temporary:
        os.chmod(temporary, 0o700)
        paths = []
        for name in ("libnvat", "nvattest"):
            filename = name + "_" + VERSION + "_amd64.deb"
            data = read_regular(Path(packages) / filename, PACKAGE_SIZES[name])
            require(len(data) == PACKAGE_SIZES[name]
                    and hashlib.sha256(data).hexdigest() == PACKAGES[name])
            target = Path(temporary) / filename
            with target.open("xb") as output:
                os.chmod(target, 0o600)
                output.write(data)
            paths.append(str(target))
        # Both exact archives contain control/md5sums only, no maintainer scripts.
        # dpkg's --no-triggers leaves unrelated pending activations untouched.
        call(["/usr/bin/dpkg", "--no-triggers", "--install", *paths])


def strict_json(data):
    require(isinstance(data, bytes) and len(data) <= LIMIT)

    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value)
            value[key] = item
        return value

    def constant(_):
        raise ValueError("nonfinite-json")

    return json.loads(data.decode("utf-8", errors="strict"), object_pairs_hook=pairs,
                      parse_constant=constant)


def successful_output(data):
    result = strict_json(data)
    require(type(result) is dict and type(result.get("result_code")) is int
            and result["result_code"] == 0)
    return result


CLAIM_SOURCES = {"unspecified", "verified-cli", "signed-token", "attested-cli", "comparison"}
CLAIM_FAILURES = {"claim-count", "claim-shape", "claim-identity", "claim-value",
                  "claim-missing", "claim-type",
                  "claim-boolean", "claim-mismatch-records", "certificate-status",
                  "certificate-expiry-format", "certificate-expired",
                  "token-shape", "token-jwt-shape", "token-payload-encoding",
                  "token-payload-json", "device-binding", "claims-disagree"}
CLAIM_FIELDS = set(TRUE_CLAIMS) | {
    "claims", "ueid", "eat_nonce", "x-nvidia-device-type", "x-nvidia-gpu-claims-version",
    "measres", "dbgstat", "x-nvidia-mismatch-measurement-records",
    "x-nvidia-mismatch-opaque-data-records", "detached_eat",
}
CLAIM_FIELDS.update("x-nvidia-gpu-" + name + "-cert-chain." + field
                    for name in CHAINS for field in
                    ("x-nvidia-cert-status", "x-nvidia-cert-ocsp-status",
                     "x-nvidia-cert-expiration-date"))
CLAIM_FIELDS.update("x-nvidia-gpu-" + name + "-cert-chain" for name in CHAINS)


class ClaimRejection(ValueError):
    def __init__(self, code, source, field):
        require(code in CLAIM_FAILURES and source in CLAIM_SOURCES and field in CLAIM_FIELDS)
        super().__init__("claims-policy-rejected")
        self.code, self.source, self.field = code, source, field


def claim_require(value, code, source, field):
    if not value:
        raise ClaimRejection(code, source, field)


def claim_field(row, name, expected_type, source, field=None):
    field = name if field is None else field
    claim_require(name in row, "claim-missing", source, field)
    value = row[name]
    claim_require(type(value) is expected_type, "claim-type", source, field)
    return value


def claim_map(claims, nonce, source="unspecified"):
    require(source in CLAIM_SOURCES)
    claim_require(type(claims) is list and len(claims) == COUNT, "claim-count", source, "claims")
    found = {}
    for claim in claims:
        claim_require(type(claim) is dict, "claim-shape", source, "claims")
        identity = claim_field(claim, "ueid", str, source)
        claim_require(type(identity) is str and 0 < len(identity) <= 256 and identity not in found,
                      "claim-identity", source, "ueid")
        for name, expected in (("eat_nonce", nonce), ("x-nvidia-device-type", "gpu"),
                               ("x-nvidia-gpu-claims-version", "4.0"),
                               ("measres", "success"), ("dbgstat", "disabled")):
            claim_require(claim_field(claim, name, str, source) == expected,
                          "claim-value", source, name)
        for name in TRUE_CLAIMS:
            claim_require(claim_field(claim, name, bool, source) is True,
                          "claim-boolean", source, name)
        for name in ("x-nvidia-mismatch-measurement-records", "x-nvidia-mismatch-opaque-data-records"):
            claim_require(name in claim, "claim-missing", source, name)
            claim_require(claim[name] is None or type(claim[name]) is list, "claim-type", source, name)
            claim_require(claim[name] in (None, []),
                          "claim-mismatch-records", source, name)
        for name in CHAINS:
            prefix = "x-nvidia-gpu-" + name + "-cert-chain."
            chain = claim_field(claim, prefix[:-1], dict, source)
            for field, expected in (("x-nvidia-cert-status", "valid"),
                                    ("x-nvidia-cert-ocsp-status", "good")):
                claim_require(claim_field(chain, field, str, source, prefix + field) == expected,
                              "certificate-status", source, prefix + field)
            field = prefix + "x-nvidia-cert-expiration-date"
            expiry = claim_field(chain, "x-nvidia-cert-expiration-date", str, source, field)
            claim_require(type(expiry) is str and expiry.endswith("Z"),
                          "certificate-expiry-format", source, field)
            try:
                parsed = datetime.datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            except ValueError:
                raise ClaimRejection("certificate-expiry-format", source, field) from None
            claim_require(parsed > datetime.datetime.now(datetime.timezone.utc),
                          "certificate-expired", source, field)
        found[identity] = claim
    return found


def verified_payload(value):
    """Decode only after verify-token succeeded; never authenticate a JWT here."""
    claim_require(type(value) is str and len(value) <= LIMIT // COUNT,
                  "token-jwt-shape", "signed-token", "detached_eat")
    parts = value.split(".")
    claim_require(len(parts) == 3 and all(parts),
                  "token-jwt-shape", "signed-token", "detached_eat")
    try:
        payload = parts[1].encode("ascii")
        data = base64.b64decode(payload + b"=" * (-len(payload) % 4),
                                altchars=b"-_", validate=True)
    except ValueError:
        raise ClaimRejection("token-payload-encoding", "signed-token", "detached_eat") from None
    try:
        result = strict_json(data)
    except (ValueError, RecursionError):
        raise ClaimRejection("token-payload-json", "signed-token", "detached_eat") from None
    claim_require(type(result) is dict, "claim-shape", "signed-token", "detached_eat")
    return result


def token_claims(token):
    """After verify-token success, normalize only four GPU serialization fields."""
    claim_require(type(token) is list and len(token) == 2 and type(token[0]) is list
            and len(token[0]) == 2 and token[0][0] == "JWT"
            and type(token[1]) is dict and len(token[1]) == COUNT,
            "token-shape", "signed-token", "detached_eat")
    result = []
    for value in token[1].values():
        claim = verified_payload(value)
        # Preserve every present value and never mutate authenticated input.
        # claim_map still requires the complete GPU security/identity structure.
        result.append({**SIGNED_SERIALIZATION_DEFAULTS, **claim})
    return result


def certificate_serial(pem):
    """Extract only the leaf serial, NOT verification (the pinned SDK does that).

    RFC 5280 4.1 Certificate/TBSCertificate prefix; pinned NVIDIA
    nv_x509.cpp:get_end_entity_serial uses certificate 0, BN_bn2dec.
    """
    require(type(pem) is bytes and 0 < len(pem) <= 0x1400)
    match = re.match(rb"\s*-----BEGIN CERTIFICATE-----\s+([A-Za-z0-9+/=\r\n]+)"
                     rb"-----END CERTIFICATE-----", pem)
    require(match is not None)
    der = base64.b64decode(re.sub(rb"\s", b"", match[1]), validate=True)

    def field(data, at, tag):
        require(at + 2 <= len(data) and data[at] == tag)
        length = data[at + 1]
        start = at + 2
        if length & 128:
            count = length & 127
            require(1 <= count <= 3 and start + count <= len(data) and data[start] != 0)
            length = int.from_bytes(data[start:start + count], "big")
            require(length >= 128)
            start += count
        require(start + length <= len(data))
        return data[start:start + length], start + length

    outer, end = field(der, 0, 0x30)
    require(end == len(der))
    tbs, _ = field(outer, 0, 0x30)
    require(tbs)
    offset = 0
    if tbs[0] == 0xa0:
        version, offset = field(tbs, 0, 0xa0)
        require(version in (b"\x02\x01\x00", b"\x02\x01\x01", b"\x02\x01\x02"))
    serial, _ = field(tbs, offset, 0x02)
    require(1 <= len(serial) <= 20 and not serial[0] & 128
            and (len(serial) == 1 or serial[0] != 0 or serial[1] & 128))
    value = int.from_bytes(serial, "big")
    require(value > 0)
    return str(value)


class Settings(C.Structure):
    _fields_ = [(name, C.c_uint) for name in
                ("version", "environment", "feature", "devtools", "multigpu")]


class Certificate(C.Structure):
    _fields_ = [("cert_size", C.c_uint), ("attestation_size", C.c_uint),
                ("cert", C.c_ubyte * 0x1000), ("attestation", C.c_ubyte * 0x1400)]


class Nvml:
    # Read-only ABI: pinned SDK gpu/nvml_client.cpp and NVML R595.
    def __init__(self):
        self.library = C.CDLL("libnvidia-ml.so.1")
        self.functions = {}
        self.configuration = None
        signatures = {
            "nvmlInit_v2": [], "nvmlShutdown": [],
            "nvmlDeviceGetCount_v2": [C.POINTER(C.c_uint)],
            "nvmlDeviceGetHandleByIndex_v2": [C.c_uint, C.POINTER(C.c_void_p)],
            "nvmlSystemGetConfComputeSettings": [C.POINTER(Settings)],
            "nvmlSystemGetConfComputeGpusReadyState": [C.POINTER(C.c_uint)],
            "nvmlDeviceGetPersistenceMode": [C.c_void_p, C.POINTER(C.c_uint)],
            "nvmlDeviceGetUUID": [C.c_void_p, C.c_char_p, C.c_uint],
            "nvmlDeviceGetConfComputeGpuCertificate": [C.c_void_p, C.POINTER(Certificate)],
        }
        for name, signature in signatures.items():
            function = getattr(self.library, name)
            function.argtypes, function.restype = signature, C.c_int
            self.functions[name] = function
        self.call("nvmlInit_v2")

    def call(self, name, *args):
        require(self.functions[name](*args) == 0)

    def ready(self):
        value = C.c_uint()
        self.call("nvmlSystemGetConfComputeGpusReadyState", C.byref(value))
        require(value.value in (0, 1))
        return value.value

    def inventory(self):
        count = C.c_uint()
        self.call("nvmlDeviceGetCount_v2", C.byref(count))
        require(count.value == COUNT)
        settings = Settings(0x01000014)
        self.call("nvmlSystemGetConfComputeSettings", C.byref(settings))
        require((settings.environment, settings.feature, settings.devtools) == (2, 1, 0)
                and settings.multigpu in (1, 2))
        configuration = (settings.version, settings.environment, settings.feature,
                         settings.devtools, settings.multigpu)
        require(settings.version == 0x01000014)
        require(self.configuration is None or self.configuration == configuration)
        self.configuration = configuration
        rows = {}
        for index in range(COUNT):
            handle, persistence = C.c_void_p(), C.c_uint()
            self.call("nvmlDeviceGetHandleByIndex_v2", index, C.byref(handle))
            require(handle.value)
            uuid, certificate = C.create_string_buffer(96), Certificate()
            self.call("nvmlDeviceGetUUID", handle, uuid, len(uuid))
            self.call("nvmlDeviceGetPersistenceMode", handle, C.byref(persistence))
            self.call("nvmlDeviceGetConfComputeGpuCertificate", handle, C.byref(certificate))
            require(b"\0" in uuid.raw and uuid.value and persistence.value in (0, 1)
                    and 0 < certificate.attestation_size <= 0x1400
                    and certificate.cert_size <= 0x1000)
            pem = bytes(certificate.attestation[:certificate.attestation_size])
            serial = certificate_serial(pem)
            require(serial not in rows and uuid.value not in [row[0] for row in rows.values()])
            rows[serial] = (uuid.value, pem, persistence.value)
        return rows

    def close(self):
        self.call("nvmlShutdown")

    def enable_ready(self):
        # NVIDIA/go-nvml at READINESS_HEADER, pkg/nvml/nvml.h:
        # NVML_CC_ACCEPTING_CLIENT_REQUESTS_TRUE == 1; unsigned int ABI.
        function = getattr(self.library, "nvmlSystemSetConfComputeGpusReadyState")
        function.argtypes, function.restype = [C.c_uint], C.c_int
        require(function(1) == 0)


def readiness_lock(directory=Path("/run")):
    """Serialize this helper's system-wide transitions; never remove the lock."""
    parent = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fd = None
    try:
        info = os.fstat(parent)
        require(info.st_uid == os.geteuid() and not info.st_mode & 0o022)
        fd = os.open("nib-lab-gpu-readiness.lock",
                     os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600,
                     dir_fd=parent)
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size == 0
                and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        named = os.stat("nib-lab-gpu-readiness.lock", dir_fd=parent, follow_symlinks=False)
        require((info.st_dev, info.st_ino) == (named.st_dev, named.st_ino))
        return fd
    except BaseException:
        if fd is not None:
            os.close(fd)
        raise
    finally:
        os.close(parent)


def run_attestation(capture, nvml, report, *,
                    check=verify_installation, clock=time.monotonic, directory=None,
                    enable_readiness=False):
    # No ambient credentials, proxy, NVAT endpoint/policy/CA or loader overrides.
    deadline = clock() + SECONDS
    nonce = secrets.token_hex(32)
    with tempfile.TemporaryDirectory(prefix="appraisal-", dir=directory) as temporary:
        os.chmod(temporary, 0o700)
        env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": temporary,
               "TMPDIR": temporary, "LANG": "C", "LC_ALL": "C",
               "LD_LIBRARY_PATH": "/usr/lib/x86_64-linux-gnu"}
        common = ["/usr/bin/nvattest", "--format", "json", "--log-level", "off"]

        def command(args):
            check()
            remaining = deadline - clock()
            require(0 < remaining <= SECONDS)
            return successful_output(capture(common + args, timeout=remaining,
                                             env=env, limit=LIMIT))

        report["stage"] = "nvml-inventory"
        local = nvml.inventory()
        report["persistence_enabled_count"] = sum(row[2] for row in local.values())
        report["ready_before"] = nvml.ready()
        report["stage"] = "remote-attestation"
        attested = command(["attest", "--device", "gpu", "--verifier", "remote",
                            "--gpu-evidence-source", "nvml", "--nonce", nonce,
                            "--nras-url", NRAS])
        token = attested.get("detached_eat")
        require(type(token) is list)
        encoded = json.dumps(token, separators=(",", ":"), allow_nan=False).encode()
        require(len(encoded) <= LIMIT)
        path = Path(temporary) / "token.json"
        with path.open("xb") as output:
            os.chmod(path, 0o600)
            output.write(encoded)
        report["stage"] = "token-verification"
        verified = command(["verify-token", "--token-type", "detached-eat", "--token-file", str(path),
                            "--nonce", nonce, "--nras-url", NRAS])
        # Both outputs originate from the pinned CLI, but acceptance additionally
        # requires each claim from the exact authenticated signed token, not just
        # the CLI's reserialized fields (which have some compatibility defaults).
        report["stage"] = "claims-policy"
        authenticated = claim_map(verified.get("claims"), nonce, "verified-cli")
        original = claim_map(token_claims(token), nonce, "signed-token")
        claim_require(set(authenticated) == set(original) == set(local),
                      "device-binding", "comparison", "ueid")
        claim_require(claim_map(attested.get("claims"), nonce, "attested-cli") == authenticated,
                      "claims-disagree", "comparison", "claims")
        check()
        require(clock() < deadline)
        report["stage"] = "inventory-recheck"
        require(nvml.inventory() == local)
        report["ready_after"] = nvml.ready()
        require(report["ready_after"] == report["ready_before"])
        require(clock() < deadline)
        summary = {"authenticated_gpu_count": COUNT, "distinct_devices": True,
                   "nonce_matches": True, "measurements_match": True,
                   "certificates_and_revocation_valid": True, "secure_boot": True,
                   "debug_disabled": True, "local_device_identity_matches": True}
        report["claims"], report["gpu_appraisal_verified"] = summary, True
        if enable_readiness:
            report["stage"] = "readiness-precondition"
            require(all(row[2] == 1 for row in local.values()))
            # The same live session rechecks identity, CC policy and persistence.
            require(nvml.inventory() == local and clock() < deadline)
            if report["ready_after"] == 0:
                report["stage"] = "readiness-transition"
                report["readiness_transition"] = "attempted-unverified"
                nvml.enable_ready()
                report["stage"] = "readiness-readback"
                report["ready_after"] = nvml.ready()
                require(report["ready_after"] == 1 and nvml.inventory() == local
                        and clock() < deadline)
                report["readiness_transition"] = "enabled-readback-verified"
            else:
                report["readiness_transition"] = "already-ready-verified"
        else:
            report["readiness_transition"] = "unchanged"
    return summary


def worker(attest, directory, packages=None, enable_readiness=False):
    report = {"schema": "nib.lab-gpu-appraisal/v1", "result": "fail",
              "stage": "platform", "gpu_appraisal_verified": False, "claims": None,
              "runtime_ready": False, "confidential_qualified": False, "deployable": False,
              "readiness_transition": "unverified", "source_reference": SOURCE,
              "readiness_requested": enable_readiness,
              "readiness_header_reference": READINESS_HEADER,
              "claim_failure": None,
              "package_version": VERSION, "package_sha256": PACKAGES,
              "cli_contract_verified": False,
              "installation_completed": False,
              "ready_before": None, "ready_after": None,
              "persistence_enabled_count": None, "nvml_shutdown": None}
    nvml, lock = None, None
    try:
        require(sys.platform == "linux" and platform.machine() == "x86_64")
        if packages is not None:
            require(not attest)
            report["stage"] = "offline-install"
            install_packages(load_capture(), packages, directory)
            report["installation_completed"] = True
        report["stage"] = "pinned-files"
        verify_installation()
        report["script_sha256"] = hashlib.sha256(read_regular(Path(__file__), 65536)).hexdigest()
        report["stage"] = "cli-contract"
        cli_contract(load_capture(), directory)
        report["cli_contract_verified"] = True
        if not attest:
            require(not enable_readiness)
            report["stage"] = "inspection-complete-unappraised"
            return report
        if enable_readiness:
            report["stage"] = "readiness-lock"
            require(os.geteuid() == 0)
            lock = readiness_lock()
        report["stage"] = "nvml-initialization"
        nvml = Nvml()
        report["claims"] = run_attestation(load_capture(), nvml, report, directory=directory,
                                           enable_readiness=enable_readiness)
        report["gpu_appraisal_verified"] = True
        report["result"] = "pass"
        report["stage"] = "complete"
    except ClaimRejection as error:
        report["claim_failure"] = {"code": error.code, "source": error.source, "field": error.field}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError,
            subprocess.SubprocessError):
        pass
    finally:
        if nvml is not None:
            try:
                nvml.close()
                report["nvml_shutdown"] = True
            except (OSError, ValueError):
                report["nvml_shutdown"] = False
                if report["result"] == "pass":
                    report["stage"] = "nvml-shutdown"
                report["result"] = "fail"
        if lock is not None:
            os.close(lock)
    return report


def main():
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            self.exit(2, "Invalid appraisal arguments.\n")

    parser = Parser(description=__doc__)
    operations = parser.add_mutually_exclusive_group()
    operations.add_argument("--attest", action="store_true",
                            help="Contacts NVIDIA; operator approval is required before invocation.")
    operations.add_argument("--install-packages", metavar="DIRECTORY",
                            help="Explicitly install the two pinned local archives; requires approval.")
    parser.add_argument("--enable-readiness", action="store_true",
                        help="Requires --attest and separate explicit operator approval.")
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.enable_readiness and not args.attest:
        parser.error("invalid-operation")
    if args.worker:
        print(json.dumps(worker(args.attest, args.worker, args.install_packages,
                                args.enable_readiness), sort_keys=True))
        return 0
    try:
        with tempfile.TemporaryDirectory(prefix="nib-attestation-") as temporary:
            os.chmod(temporary, 0o700)
            command = [sys.executable, "-I", "-S", "-B", str(Path(__file__).absolute()),
                       "--worker", temporary]
            if args.attest:
                command.append("--attest")
            if args.enable_readiness:
                command.append("--enable-readiness")
            if args.install_packages is not None:
                command.extend(["--install-packages", str(Path(args.install_packages).absolute())])
            report = strict_json(load_capture()(command, timeout=SECONDS + 45,
                env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": temporary,
                     "TMPDIR": temporary, "LANG": "C", "LC_ALL": "C"}, limit=16384))
    except (OSError, ValueError, TypeError, KeyError, RecursionError, subprocess.SubprocessError):
        report = {"schema": "nib.lab-gpu-appraisal/v1", "result": "fail",
                  "stage": "supervision", "gpu_appraisal_verified": False,
                  "runtime_ready": False, "confidential_qualified": False, "deployable": False}
    print(json.dumps(report, sort_keys=True))
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
