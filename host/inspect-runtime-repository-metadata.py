#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Closed metadata validation core. The executable refuses all acquisition.

The underscore-prefixed composition boundary is exclusively for synthetic tests;
it cannot issue authenticated or operational verdicts. No live transport, parser
loader, keyring reader or verifier process adapter is implemented here.
"""
from email.utils import parsedate_to_datetime
import hashlib
import json
import lzma
import math
import re
import sys

VERSION = "255.4-1ubuntu8.18"
PACKAGES = ("systemd", "systemd-container", "libsystemd-shared")
POCKETS = {
    "security": ("https://security.ubuntu.com/ubuntu/dists/noble-security/", "noble-security"),
    "updates": ("https://archive.ubuntu.com/ubuntu/dists/noble-updates/", "noble-updates"),
}
INDEXES = ("main/binary-amd64/Packages", "main/source/Sources")
SOURCE_FILES = ("systemd_255.4.orig.tar.gz", "systemd_" + VERSION + ".debian.tar.xz",
                "systemd_" + VERSION + ".dsc")
INRELEASE = 1 << 20
COMPRESSED = 16 << 20
ACQUIRED = 64 << 20
EXPANDED = 64 << 20
TOTAL_EXPANDED = 256 << 20
PROCESSED = 512 << 20
DECODER_MEMORY = 64 << 20
OUTPUT = 16 << 10
HEADERS = 16 << 10
ERROR_BODY = 4 << 10
DIAGNOSTICS = 16 << 10
LINE = 16 << 10
STANZA = 256 << 10
RECORDS = 100000
CHUNK = 32 << 10
SECONDS = 60
OUTER_SECONDS = 90  # Total from the SAME origin, never 60 + 90.
IDLE_SECONDS = 10
MAX_AGE = 7 * 24 * 3600
FUTURE_SKEW = 60
AUTHORITY = ("repository_authenticated", "installed_status_verified", "dependency_closure",
             "installable", "executed_version_verified", "runtime_supported", "delegation_verified",
             "operationally_approved", "admitted", "cleanup_proven", "absence_confirmed")
LIVE_PRECONDITIONS = dict.fromkeys(("approval", "tool_pin", "parser_pin", "keyring_pin",
    "signers", "key_acceptability", "trusted_clock", "rollback_baselines", "scratch_limit",
    "cpu_limit", "memory_limit", "pid_limit", "lifecycle_owner"))
REASONS = frozenset(("live-disabled", "invocation", "malformed", "boundary", "read-limit",
    "read-budget", "parse-limit", "output-limit", "timeout", "http", "signature", "trust",
    "release", "rollback-unestablished", "rollback", "expired", "hash", "compression",
    "missing-record", "record-conflict", "dependency-mismatch", "cleanup", "internal"))


class Rejected(Exception):
    def __init__(self, code):
        self.code = code if code in REASONS else "internal"


def require(condition, code="malformed"):
    if not condition:
        raise Rejected(code)


def number(value):
    require(type(value) in (int, float) and math.isfinite(value), "boundary")
    return value


def sha(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None)
    return value


def integer(value, *, allow_zero=False):
    require(type(value) is str and re.fullmatch(r"[0-9]{1,19}", value) is not None)
    result = int(value)
    require((0 if allow_zero else 1) <= result <= (1 << 63) - 1)
    return result


class Budget:
    def __init__(self, clock, origin):
        self.clock, self.origin = clock, number(origin)
        self.deadline, self.outer_deadline = origin + SECONDS, origin + OUTER_SECONDS
        self.last = origin
        self.acquired = self.processed = self.expanded = self.requests = 0
        self.check()

    def check(self):
        now = number(self.clock())
        require(self.last <= now < self.deadline, "timeout")
        self.last = now

    def charge(self, count, channel="processed"):
        require(type(count) is int and count >= 0, "boundary")
        if channel == "acquired":
            self.acquired += count
        if channel == "expanded":
            self.expanded += count
        self.processed += count
        require(self.acquired <= ACQUIRED and self.expanded <= TOTAL_EXPANDED
                and self.processed <= PROCESSED, "read-budget")
        self.check()

    def digest(self, raw):
        self.charge(len(raw))
        result = hashlib.sha256(raw).hexdigest()
        self.check()
        return result


def _read(reader, maximum, budget, channel="processed"):
    """The fixture reader must honor the requested bound BEFORE allocation."""
    result = bytearray()
    while True:
        budget.check()
        room = PROCESSED - budget.processed
        if channel == "acquired":
            room = min(room, ACQUIRED - budget.acquired)
        count = min(CHUNK, maximum + 1 - len(result), room)
        require(count > 0, "read-budget")
        part = reader.read(count)
        require(type(part) is bytes, "boundary")
        budget.charge(len(part), channel)  # Includes rejected/sentinel bytes.
        require(len(part) <= count, "boundary")
        require(len(result) + len(part) <= maximum, "read-limit")
        if not part:
            return bytes(result)
        result.extend(part)


def _record(raw, parser, budget, maximum=STANZA):
    """Frame/duplicate guard before the standard parser's normalization."""
    require(type(raw) is bytes and 0 < len(raw) <= maximum, "parse-limit")
    budget.charge(len(raw))
    require(raw.endswith(b"\n") and b"\r" not in raw and b"\x00" not in raw)
    names = set()
    for line in raw.split(b"\n"):
        require(len(line) <= LINE, "parse-limit")
        require(all(byte >= 32 or byte == 9 for byte in line))
        if not line:
            continue
        if line[:1] in (b" ", b"\t"):
            require(bool(names))
            continue
        name, sep, unused = line.partition(b":")
        require(sep and re.fullmatch(rb"[A-Za-z0-9][A-Za-z0-9-]*", name) is not None)
        name = name.decode("ascii").lower()
        require(name not in names)
        names.add(name)
        require(len(names) <= 128, "parse-limit")
    # No production loader or runtime injection connects this parser to a CLI.
    row = parser(raw)
    budget.check()
    require(type(row) is dict and set(row) == names, "boundary")
    require(all(type(value) is str and len(value) <= maximum for value in row.values()), "boundary")
    return row


def _stanzas(chunks, parser, budget):
    line, block, count = bytearray(), bytearray(), 0
    for chunk in chunks:
        budget.check()
        require(type(chunk) is bytes and len(chunk) <= CHUNK, "boundary")
        start = 0
        while start < len(chunk):
            end = chunk.find(b"\n", start)
            end = len(chunk) if end < 0 else end + 1
            require(len(line) + end - start <= LINE, "parse-limit")
            line.extend(chunk[start:end])
            start = end
            if not line.endswith(b"\n"):
                continue
            if line == b"\n":
                if block:
                    count += 1
                    require(count <= RECORDS, "parse-limit")
                    yield _record(bytes(block), parser, budget)
                    block.clear()
            else:
                require(len(block) + len(line) <= STANZA, "parse-limit")
                block.extend(line)
            line.clear()
    require(not line, "malformed")
    if block:
        require(count < RECORDS, "parse-limit")
        yield _record(bytes(block), parser, budget)


def _fetch(boundary, pocket, relative, maximum, budget):
    require(pocket in POCKETS and relative in ("InRelease", *(p + ".xz" for p in INDEXES)))
    budget.check()
    budget.requests += 1
    require(budget.requests <= 6, "boundary")
    url = POCKETS[pocket][0] + relative
    response = boundary.open(url, budget.deadline, IDLE_SECONDS)
    try:
        budget.check()
        headers = _read(response.headers, HEADERS, budget, "acquired")
        require(headers.endswith(b"\r\n\r\n"), "http")
        row = _record(headers.replace(b"\r\n", b"\n"), boundary.parse, budget)
        require(response.url == url and response.redirected is False, "http")
        if type(response.status) is not int or response.status != 200:
            _read(response.body, ERROR_BODY, budget, "acquired")
            raise Rejected("http")
        require(not {"location", "transfer-encoding"} & row.keys()
                and row.get("content-encoding", "identity") == "identity", "http")
        expected = integer(row.get("content-length", ""))
        require(expected <= maximum, "read-limit")
        raw = _read(response.body, min(maximum, expected), budget, "acquired")
        require(len(raw) == expected, "http")
    finally:
        response.close()
    budget.check()  # Closing descriptors is inside the unchanged cutoff.
    return raw


def _verified_text(boundary, pocket, raw, trust, budget, utc):
    require(type(trust) is dict and set(trust) == {"keyring_sha256", "signers", "key_acceptable"}, "trust")
    sha(trust["keyring_sha256"])
    signers = trust["signers"]
    require(type(signers) is tuple and 0 < len(signers) <= 4 and len(set(signers)) == len(signers), "trust")
    require(all(type(s) is str and re.fullmatch(r"[A-F0-9]{40}", s) for s in signers), "trust")
    require(trust["key_acceptable"] is True, "trust")
    budget.check()
    verification = boundary.verify(pocket, raw, trust["keyring_sha256"], signers, budget.deadline)
    try:
        text = _read(verification.text, INRELEASE, budget)
        status = _read(verification.status, DIAGNOSTICS, budget)
        _read(verification.diagnostics, DIAGNOSTICS - len(status), budget)
        exit_status = verification.finish(budget.deadline)
        require(type(exit_status) is int and exit_status == 0 and verification.exited is True, "signature")
    finally:
        verification.close()
    budget.check()
    valid = []
    require(status.endswith(b"\n") and b"\r" not in status, "signature")
    for line in status.splitlines():
        require(line.startswith(b"[GNUPG:] "), "signature")
        fields = line[9:].split()
        require(fields and fields[0] in (b"NEWSIG", b"KEY_CONSIDERED", b"SIG_ID", b"GOODSIG", b"VALIDSIG"), "signature")
        if fields[0] == b"VALIDSIG":
            require(len(fields) in (10, 11), "signature")
            require(fields[1].decode("ascii") in signers, "signature")
            require(len(fields) == 10 or fields[10].decode("ascii") in signers, "signature")
            require(fields[8] in (b"8", b"9", b"10") and fields[9] == b"01", "signature")
            require(fields[4] == b"0" or integer(fields[4].decode("ascii")) > number(utc()), "signature")
            expiry = None if fields[4] == b"0" else integer(fields[4].decode("ascii"))
            valid.append((fields[1].decode("ascii"), expiry))
    require(len(valid) == 1, "signature")
    return text, *valid[0]


def _checksums(value):
    require(type(value) is str and len(value) <= INRELEASE)
    result = {}
    for line in value.splitlines():
        if not line.strip():
            continue
        fields = line.split()
        require(len(fields) == 3)
        digest, size, path = fields
        require(len(path) <= 256 and re.fullmatch(r"[A-Za-z0-9_+./-]+", path) is not None
                and not path.startswith("/") and all(p not in ("", ".", "..") for p in path.split("/")))
        require(path not in result and len(result) < 16384)
        result[path] = {"sha256": sha(digest), "bytes": integer(size, allow_zero=True)}
    return result


def _date(value):
    require(type(value) is str and 0 < len(value) <= 128, "release")
    parsed = parsedate_to_datetime(value)
    require(parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0, "release")
    return number(parsed.timestamp())


def _fresh(release, utc):
    now = number(utc())
    require(release["date"] <= now + FUTURE_SKEW and now < release["expires"], "expired")


def _release(text, raw, pocket, baseline, parser, budget, utc):
    row = _record(text, parser, budget, INRELEASE)
    require(row.get("origin") == "Ubuntu" and row.get("label") == "Ubuntu"
            and row.get("suite") == POCKETS[pocket][1] and row.get("codename") == "noble"
            and "amd64" in row.get("architectures", "").split()
            and "main" in row.get("components", "").split(), "release")
    dated = _date(row.get("date"))
    expiry = dated + MAX_AGE
    if "valid-until" in row:
        expiry = min(expiry, _date(row["valid-until"]))
    require(expiry > dated, "expired")
    digest = budget.digest(raw)
    require(type(baseline) is dict and set(baseline) == {"date", "inrelease_sha256"}, "rollback-unestablished")
    prior = number(baseline["date"])
    sha(baseline["inrelease_sha256"])
    require(dated >= prior and (dated != prior or digest == baseline["inrelease_sha256"]), "rollback")
    result = {"pocket": pocket, "inrelease_sha256": digest, "date": dated, "expires": expiry,
              "baseline_date": prior, "baseline_sha256": baseline["inrelease_sha256"]}
    _fresh(result, utc)
    table = _checksums(row.get("sha256"))
    for path in INDEXES:
        require(path in table and path + ".xz" in table, "release")
        require(0 < table[path]["bytes"] <= EXPANDED
                and 0 < table[path + ".xz"]["bytes"] <= COMPRESSED, "read-limit")
    return result, table


def _decode(raw, expected, budget):
    decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=DECODER_MEMORY)
    digest, used, offset = hashlib.sha256(), 0, 0
    while offset < len(raw) or not decoder.needs_input:
        budget.check()
        require(not decoder.eof, "compression")
        part = raw[offset:offset + CHUNK] if decoder.needs_input else b""
        offset += len(part)
        budget.charge(len(part))
        limit = min(CHUNK, EXPANDED + 1 - used, TOTAL_EXPANDED - budget.expanded,
                    PROCESSED - budget.processed)
        require(limit > 0, "read-budget")
        try:
            expanded = decoder.decompress(part, max_length=limit)
        except lzma.LZMAError:
            raise Rejected("compression") from None
        budget.charge(len(expanded), "expanded")
        used += len(expanded)
        require(used <= EXPANDED, "read-limit")
        digest.update(expanded)
        if expanded:
            yield expanded
        if decoder.eof:
            require(not decoder.unused_data and offset == len(raw), "compression")
            break
        require(part or expanded, "compression")
    require(decoder.eof and used == expected["bytes"] and digest.hexdigest() == expected["sha256"], "hash")
    budget.check()


def _dependencies(row, boundary, budget):
    result, shared = {}, False
    for key in ("depends", "pre-depends"):
        expression = row.get(key, "")
        require(len(expression) <= LINE and "\n" not in expression, "parse-limit")
        budget.charge(len(expression.encode("utf-8")))
        groups = boundary.relations(expression)
        budget.check()
        require(type(groups) is list and len(groups) <= 128, "boundary")
        unresolved = 0
        for group in groups:
            require(type(group) is list and 0 < len(group) <= 8, "boundary")
            known = len(group) == 1
            for term in group:
                require(type(term) is dict and set(term) == {"name", "version", "qualified"}, "boundary")
                name, version = term["name"], term["version"]
                require(type(name) is str and re.fullmatch(r"[a-z0-9][a-z0-9+.-]{1,127}", name) is not None)
                require(type(term["qualified"]) is bool, "boundary")
                require(version is None or (type(version) is tuple and len(version) == 2
                        and all(type(v) is str and len(v) <= 128 for v in version)), "boundary")
                if name in PACKAGES and len(group) == 1 and not term["qualified"]:
                    require(version is None or version == ("=", VERSION), "dependency-mismatch")
                    if name == "libsystemd-shared" and version == ("=", VERSION):
                        shared = True
                else:
                    known = False
            unresolved += not known
        # Never print arbitrary dependency names or raw repository expressions.
        result[key] = {"sha256": budget.digest(expression.encode("utf-8")),
                       "groups": len(groups), "unresolved_groups": unresolved}
    return result, shared


def _select(rows, source, boundary, budget):
    selected = {}
    for row in rows:  # Always exhaust the index, including its final hash check.
        name = row.get("package")
        version = row.get("version")
        require(type(name) is str and re.fullmatch(r"[a-z0-9][a-z0-9+.-]{1,127}", name) is not None)
        require(type(version) is str and 0 < len(version) <= 128
                and re.fullmatch(r"[0-9][0-9A-Za-z.+:~-]*", version) is not None)
        if not source:
            require(row.get("architecture") in ("amd64", "all"), "release")
        if name not in (("systemd",) if source else PACKAGES) or version != VERSION:
            continue
        require(name not in selected, "record-conflict")
        if source:
            require(row.get("directory") == "pool/main/s/systemd", "release")
            artifacts = _checksums(row.get("checksums-sha256"))
            require(set(artifacts) == set(SOURCE_FILES), "release")
            require(all(artifact["bytes"] > 0 for artifact in artifacts.values()), "release")
            names = {part.strip() for part in row.get("binary", "").split(",")}
            require(set(PACKAGES) <= names, "release")
            selected[name] = artifacts
        else:
            require(row.get("architecture") == "amd64", "release")
            require(row.get("source", name) in ("systemd", "systemd (" + VERSION + ")"), "release")
            require(row.get("filename") == "pool/main/s/systemd/" + name + "_" + VERSION + "_amd64.deb", "release")
            dependencies, shared = _dependencies(row, boundary, budget)
            require(name == "libsystemd-shared" or shared, "dependency-mismatch")
            selected[name] = {"bytes": integer(row.get("size", "")), "sha256": sha(row.get("sha256")),
                              "dependencies": dependencies}
    require(set(selected) == ({"systemd"} if source else set(PACKAGES)), "missing-record")
    return selected


def _report(reason="live-disabled"):
    return {"schema": "nib.runtime-repository-metadata/v1", "status": "blocked", "reason": reason,
            "synthetic": False, "validation_complete": False, "latest_established": False,
            "catalogue": "not-collected", "installed_status": "not-collected",
            "runtime_support": "unknown", **dict.fromkeys(AUTHORITY, False)}


def _encode(report):
    # iterencode avoids allocating an unbounded joined output before the cap.
    result = bytearray()
    for token in json.JSONEncoder(sort_keys=True, separators=(",", ":"), ensure_ascii=True).iterencode(report):
        require(len(token) <= OUTPUT, "output-limit")
        encoded = token.encode("ascii")
        require(len(result) + len(encoded) + 1 <= OUTPUT, "output-limit")
        result.extend(encoded)
    result.extend(b"\n")
    return bytes(result)


def _evaluate_synthetic(boundary, *, clock, utc, origin, baselines, trust):
    """Test composition only; callbacks can NEVER produce authenticated evidence.

    An eventual real boundary requires another reviewed implementation. In
    particular this function is not a live verifier-plugin/approval interface.
    """
    report = _report()
    report.update(synthetic=True, pockets={})
    budget = None
    cleanup_attempted = False
    try:
        budget = Budget(clock, origin)
        require(type(baselines) is dict and set(baselines) == set(POCKETS), "rollback-unestablished")
        descriptors = None
        for pocket in POCKETS:
            raw = _fetch(boundary, pocket, "InRelease", INRELEASE, budget)
            text, signer, signature_expiry = _verified_text(boundary, pocket, raw, trust, budget, utc)
            release, table = _release(text, raw, pocket, baselines[pocket], boundary.parse, budget, utc)
            if signature_expiry is not None:
                release["expires"] = min(release["expires"], signature_expiry)
            records, indexes = {}, {}
            for index, path in enumerate(INDEXES):
                compressed = _fetch(boundary, pocket, path + ".xz", COMPRESSED, budget)
                require(len(compressed) == table[path + ".xz"]["bytes"]
                        and budget.digest(compressed) == table[path + ".xz"]["sha256"], "hash")
                rows = _stanzas(_decode(compressed, table[path], budget), boundary.parse, budget)
                records["source" if index else "binaries"] = _select(rows, bool(index), boundary, budget)
                indexes[path] = {"compressed": table[path + ".xz"], "expanded": table[path]}
            if descriptors is not None:
                require(descriptors == records, "record-conflict")
            descriptors = records
            report["pockets"][pocket] = {**release, "signer": signer, "indexes": indexes,
                                         "records": records}
        cleanup_attempted = True
        boundary.cleanup(budget.outer_deadline)
        budget.check()
        for pocket in report["pockets"].values():
            _fresh(pocket, utc)  # Includes final cleanup, not just verification time.
        report.update(status="synthetic-complete", reason=None, validation_complete=True)
        report["bytes"] = {"acquired": budget.acquired, "expanded": budget.expanded,
                           "processed": budget.processed}
        encoded = _encode(report)
        budget.check()
        for pocket in report["pockets"].values():
            _fresh(pocket, utc)
        budget.check()
        return encoded
    except Rejected as error:
        reason = error.code
    except Exception:
        reason = "internal"
    # The synthetic boundary owns its fixture handles even after a failure.
    # A real owner would need independent cleanup/absence, never this assertion.
    if not cleanup_attempted:
        try:
            boundary.cleanup(origin + OUTER_SECONDS)
        except Exception:
            reason = "cleanup"
    completed_pockets = report["pockets"]
    report = _report(reason)
    report.update(synthetic=True, pockets=completed_pockets)
    if budget is not None:
        report["bytes"] = {"acquired": budget.acquired, "expanded": budget.expanded,
                           "processed": budget.processed}
    return _encode(report)


def main():
    # No pins, flags, environment variables or injected callback can enable live
    # execution here. Filling preconditions alone is not an implemented adapter.
    report = _report("live-disabled" if len(sys.argv) == 1 else "invocation")
    try:
        text = _encode(report).decode("ascii")
        if sys.stdout.write(text) != len(text):
            return 2
        sys.stdout.flush()
    except Exception:
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
