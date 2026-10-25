# SPDX-License-Identifier: Apache-2.0
"""Private, read-only firewall/resolver snapshots around an approved NAT proof."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        sys.exit("Use python3 -I -S -B scripts/selected_host_baseline.py")
    sys.path.insert(0, str(ROOT / "scripts"))

import argparse
import copy
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import importlib.util
import os
import re
import signal
import stat
import subprocess
import time

import selected_network_policy as policy

SPEC = importlib.util.spec_from_file_location("baseline_nat_proof", ROOT / "scripts/prove-selected-nat.py")
proof = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(proof)
require, encoded, decode = policy.require, policy.encoded, policy.decode
SOURCE = "scripts/selected_host_baseline.py"
SOURCES = (*proof.SOURCES, SOURCE)
FILES = ("firewall.json", "resolver.json", "resolver.conf")
LIMIT = 262144
CAPTURE_SECONDS = 30
# Venue-observed numeric identity, not a portable systemd UID allocation.
RESOLVED_NAME, RESOLVED_ID = b"systemd-resolve", 989


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def stamp(row):
    return {key: getattr(row, "st_" + key) for key in
            ("dev", "ino", "uid", "gid", "mode", "size", "mtime_ns", "ctime_ns")}


def read_file(path, limit=LIMIT, *, private=True):
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
            and not before.st_mode & (0o077 if private else 0o022) and before.st_size <= limit)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        require(stamp(os.fstat(stream.fileno())) == stamp(before))
        raw = stream.read(limit + 1)
        require(len(raw) == before.st_size and len(raw) <= limit
                and stamp(os.fstat(stream.fileno())) == stamp(before))
    require(stamp(path.lstat()) == stamp(before))
    return raw, stamp(before)


def private_directory(path, *, create=False):
    require(path.is_absolute() and path.resolve() == path)
    if create:
        path.mkdir(mode=0o700)
    row = path.lstat()
    require(stat.S_ISDIR(row.st_mode) and row.st_uid == os.getuid() and not row.st_mode & 0o077)


def write_file(path, raw):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as stream:
        stream.write(raw)


def canonical_firewall(raw, version):
    """Ignore only the two pinned COUNTER forms; retain handles and all ordering."""
    require(version in policy.NFT_RELEASES)
    value = decode(raw)
    require(type(value) is dict and set(value) == {"nftables"} and type(value["nftables"]) is list)
    result, metadata = copy.deepcopy(value), 0
    def number(value):
        require(type(value) is int and 0 <= value < 1 << 64)
    def counts(counter):
        require(type(counter) is dict and {"packets", "bytes"} <= set(counter))
        for name in ("packets", "bytes"):
            number(counter[name])
            counter[name] = 0
    for row in result["nftables"]:
        require(type(row) is dict and len(row) == 1)
        kind, body = next(iter(row.items()))
        require(type(body) is dict)
        if kind == "metainfo":
            require(type(body.get("json_schema_version")) is int and body == {
                "version": version, "release_name": policy.NFT_RELEASES[version], "json_schema_version": 1})
            metadata += 1
        if kind == "counter":
            require(all(type(body.get(name)) is str for name in ("family", "table", "name")))
            counts(body)
        if kind == "rule":
            require(type(body.get("expr")) is list)
            for expression in body["expr"]:
                require(type(expression) is dict)
                if set(expression) == {"counter"} and type(expression["counter"]) is dict:
                    require(set(expression["counter"]) == {"packets", "bytes"})
                    counts(expression["counter"])
    require(metadata == 1)
    return encoded(result)


def source_identity(commands):
    revision = commands.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"]).stdout.decode().strip()
    require(re.fullmatch("[0-9a-f]{40}", revision))
    hashes = {}
    for name in SOURCES:
        raw = commands.run(["git", "-C", str(ROOT), "show", revision + ":" + name]).stdout
        require(raw == read_file(ROOT / name, private=False)[0])
        hashes[name] = digest(raw)
    return revision, hashes


def host_identity():
    # This fixed procfs file reports size zero despite its bounded generated contents.
    with os.fdopen(os.open("/proc/sys/kernel/random/boot_id", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        row = os.fstat(stream.fileno())
        require(stat.S_ISREG(row.st_mode) and row.st_uid == 0 and not row.st_mode & 0o022)
        boot = stream.read(65)
    require(re.fullmatch(rb"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\n", boot))
    return {"net": os.stat("/proc/self/ns/net").st_ino,
            "mount": os.stat("/proc/self/ns/mnt").st_ino, "boot_sha256": digest(boot)}


def resolver_service_identity():
    # Bounded local files only; do not invoke NSS or retain account records.
    for filename, fields in (("passwd", 7), ("group", 4)):
        raw, _ = read_file(Path("/etc") / filename, private=False)
        rows = [line.split(b":") for line in raw.splitlines() if line and not line.startswith(b"#")]
        require(all(len(row) == fields and re.fullmatch(rb"0|[1-9][0-9]{0,9}", row[2])
                    and int(row[2]) < (1 << 32) - 1 for row in rows))
        matches = [row for row in rows if row[0] == RESOLVED_NAME
                   or int(row[2]) == RESOLVED_ID]
        require(len(matches) == 1 and len(matches[0]) == fields
                and matches[0][0] == RESOLVED_NAME and matches[0][2] == str(RESOLVED_ID).encode())
        if filename == "passwd":
            require(matches[0][3] == str(RESOLVED_ID).encode())


def resolver_owner(row, venue, *, link=False):
    ordinary = venue == proof.ORDINARY_VENUE
    require(row["uid"] == (RESOLVED_ID if ordinary and not link else 0))
    if ordinary:
        require(row["gid"] == (0 if link else RESOLVED_ID))


def read_resolved_stub():
    """Only the closed ordinary venue's service-owned stub; no general owner override."""
    with ExitStack() as stack:
        parents = []
        identity = lambda row: (row.st_dev, row.st_ino, row.st_uid, row.st_gid, row.st_mode)
        def directory(name, parent=None, owner=0):
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            stack.callback(os.close, fd)
            row = os.fstat(fd)
            require(stat.S_ISDIR(row.st_mode) and row.st_uid == owner and row.st_gid == owner
                    and not row.st_mode & 0o022)
            parents.append((parent, name, fd, identity(row)))
            return fd
        root = directory("/")
        directory("etc", root)
        run = directory("run", root)
        systemd = directory("systemd", run)
        resolve = directory("resolve", systemd, RESOLVED_ID)
        resolver_service_identity()
        name = "stub-resolv.conf"
        before = os.stat(name, dir_fd=resolve, follow_symlinks=False)
        resolver_owner(stamp(before), proof.ORDINARY_VENUE)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                and not before.st_mode & 0o022 and before.st_size <= 65536)
        with os.fdopen(os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=resolve), "rb") as stream:
            require(stamp(os.fstat(stream.fileno())) == stamp(before))
            raw = stream.read(65537)
            require(len(raw) == before.st_size and len(raw) <= 65536
                    and stamp(os.fstat(stream.fileno())) == stamp(before))
        require(stamp(os.stat(name, dir_fd=resolve, follow_symlinks=False)) == stamp(before))
        resolver_service_identity()
        for parent, name, fd, original in parents:
            require(identity(os.fstat(fd)) == original
                    and identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) == original)
        return raw, stamp(before)


def resolver_snapshot(venue):
    path = proof.RESOLVER
    before = stamp(path.lstat())
    target = proof.resolver_target(venue)
    link = str(path.readlink()) if path.is_symlink() else None
    if venue == proof.ORDINARY_VENUE:
        require(target == Path("/run/systemd/resolve/stub-resolv.conf"))
        resolver_owner(before, venue, link=True)
        raw, target_stat = read_resolved_stub()
    else:
        raw, target_stat = read_file(target, 65536, private=False)
    require(stamp(path.lstat()) == before and proof.resolver_target(venue) == target
            and (str(path.readlink()) if path.is_symlink() else None) == link
            and stamp(target.lstat()) == target_stat)
    return raw, encoded({"path": str(path), "link": link, "target": str(target),
                         "link_stat": before, "target_stat": target_stat})


def capture(output, venue, cancelled):
    require(sys.platform == "linux" and os.getuid() == 0)
    private_directory(output, create=True)
    end = time.monotonic() + CAPTURE_SECONDS
    started = datetime.now(timezone.utc).isoformat()
    # Reuse bounded read-only commands, including their existing child-reap reserve.
    commands = proof.Commands(end + proof.REAP, cancelled, output)
    proof.check_venue(commands, venue)
    revision, hashes = source_identity(commands)
    identity = host_identity()
    version = proof.venue_settings(venue)["nftables"]
    argv = ["nft", "--json", *(["--numeric-protocol"] if version == "1.1.6" else []), "list", "ruleset"]
    raw = commands.run(argv).stdout
    write_file(output / "firewall.json", raw)  # Keep the complete original, never just its hash.
    resolver, metadata = resolver_snapshot(venue)
    write_file(output / "resolver.conf", resolver)
    write_file(output / "resolver.json", metadata)
    canonical_firewall(raw, version)
    require(time.monotonic() < end and not cancelled["cancelled"] and host_identity() == identity)
    manifest = {"schema": "nib.selected-host-baseline/v1", "git_sha": revision, "source_sha256": hashes,
        "venue_profile": venue, "host_identity": identity, "started_at": started,
        "ended_at": datetime.now(timezone.utc).isoformat(),
        "files": {name: digest(read_file(output / name)[0]) for name in FILES}}
    write_file(output / "capture.json", encoded(manifest))


def timestamp(value):
    require(type(value) is str)
    value = datetime.fromisoformat(value)
    require(value.utcoffset() is not None and value.utcoffset().total_seconds() == 0)
    return value


def check_resolver(raw, venue):
    value = decode(raw)
    require(type(value) is dict and set(value) == {"path", "link", "target", "link_stat", "target_stat"}
            and value["path"] == str(proof.RESOLVER))
    ordinary = venue == proof.ORDINARY_VENUE
    require(value["link"] == (proof.venue_settings(venue)["resolver_link"] if ordinary else None)
            and value["target"] == str(proof.RESOLVER_TARGET if ordinary else proof.RESOLVER))
    for key in ("link_stat", "target_stat"):
        row = value[key]
        require(type(row) is dict and set(row) == {"dev", "ino", "uid", "gid", "mode", "size", "mtime_ns", "ctime_ns"}
                and all(type(item) is int and item >= 0 for item in row.values()))
        resolver_owner(row, venue, link=key == "link_stat")
        if key == "link_stat" and ordinary:
            require(stat.S_ISLNK(row["mode"]))
        else:
            require(stat.S_ISREG(row["mode"]) and not row["mode"] & 0o022 and row["size"] <= 65536)
    if not ordinary:
        require(value["link_stat"] == value["target_stat"])


def load_capture(path):
    private_directory(path)
    require({entry.name for entry in path.iterdir()} == {*FILES, "capture.json"})
    manifest = decode(read_file(path / "capture.json")[0])
    require(type(manifest) is dict and set(manifest) == {"schema", "git_sha", "source_sha256", "venue_profile",
            "host_identity", "started_at", "ended_at", "files"}
            and manifest["schema"] == "nib.selected-host-baseline/v1" and manifest["venue_profile"] in proof.VENUES)
    require(type(manifest["git_sha"]) is str and re.fullmatch("[0-9a-f]{40}", manifest["git_sha"]))
    for key, names in (("source_sha256", set(SOURCES)), ("files", set(FILES))):
        require(type(manifest[key]) is dict and set(manifest[key]) == names)
        require(all(type(value) is str and re.fullmatch("[0-9a-f]{64}", value) for value in manifest[key].values()))
    identity = manifest["host_identity"]
    require(type(identity) is dict and set(identity) == {"net", "mount", "boot_sha256"}
            and all(type(identity[key]) is int and identity[key] > 0 for key in ("net", "mount"))
            and type(identity["boot_sha256"]) is str and re.fullmatch("[0-9a-f]{64}", identity["boot_sha256"]))
    require(timestamp(manifest["started_at"]) <= timestamp(manifest["ended_at"]))
    files = {name: read_file(path / name)[0] for name in FILES}
    require(all(digest(files[name]) == manifest["files"][name] for name in FILES))
    check_resolver(files["resolver.json"], manifest["venue_profile"])
    require(len(files["resolver.conf"]) == decode(files["resolver.json"])["target_stat"]["size"])
    return manifest, files


def compare(before, after):
    left, a = load_capture(before)
    right, b = load_capture(after)
    require(before != after and before.resolve() != after.resolve())
    for key in ("git_sha", "source_sha256", "venue_profile", "host_identity"):
        require(left[key] == right[key])
    require(timestamp(left["ended_at"]) <= timestamp(right["started_at"]))
    require(left["source_sha256"] == {name: digest(read_file(ROOT / name, private=False)[0]) for name in SOURCES})
    version = proof.venue_settings(left["venue_profile"])["nftables"]
    firewall_equal = canonical_firewall(a["firewall.json"], version) == canonical_firewall(b["firewall.json"], version)
    dns_equal = all(a[name] == b[name] for name in ("resolver.json", "resolver.conf"))
    return firewall_equal, dns_equal


def invoke_proof(args, cancelled):
    argv = [sys.executable, "-I", "-S", "-B", str(ROOT / "scripts/prove-selected-nat.py"),
            "--run", "--quarantine", "--archive", str(args.archive), "--archive-sha256", args.archive_sha256,
            "--output", str(args.output / "proof"), "--venue-profile", args.venue_profile]
    child = None
    def notify():
        if child is not None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    try:
        with os.fdopen(os.open(args.output / "proof-stdout.json",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as stream, \
                proof.cancellation(notify) as state:
            require(not cancelled["cancelled"] and not state["cancelled"])
            end = time.monotonic() + proof.OPERATIONS + proof.CLEANUP + proof.REAP
            child = subprocess.Popen(argv, env=proof.ENV, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, close_fds=True, umask=0o077)
            if state["cancelled"] or cancelled["cancelled"]:
                notify()
            proof.snapshot.bounded_output(child, argv, max(0, end - time.monotonic()), stream, LIMIT, 65536)
            require(not state["cancelled"] and not cancelled["cancelled"])
            return child.returncode
    finally:
        if child is not None:
            proof.snapshot.cleanup_process_group(child)


def proof_result(args, before, status):
    raw = read_file(args.output / "proof/observation.json")[0]
    require(raw == read_file(args.output / "proof-stdout.json")[0])
    report = decode(raw)
    required = {"schema", "git_sha", "source_sha256", "archive_sha256", "result", "namespace_reaped",
                "gpu_used", "published", "deployment_eligible", "stage", "started_at", "ended_at",
                "host_python", "unshare_version"}
    ordinary = args.venue_profile == proof.ORDINARY_VENUE
    if ordinary:
        required |= {"venue_profile", "venue_manifest_sha256"}
    require(type(report) is dict and required <= set(report) <= required | {"observation"}
            and report["schema"] == "nib.selected-nat-proof-host/v2"
            and report["git_sha"] == before["git_sha"]
            and report["source_sha256"] == {name: before["source_sha256"][name] for name in proof.SOURCES}
            and report["archive_sha256"] == args.archive_sha256
            and type(status) is int and status in (0, 1)
            and report["result"] == ("pass" if status == 0 else "fail")
            and type(report["namespace_reaped"]) is bool
            and all(report[key] is False for key in ("gpu_used", "published", "deployment_eligible"))
            and report["stage"] in ("namespace", "cgroup", "report", "complete"))
    settings = proof.venue_settings(args.venue_profile)
    require(report["host_python"] == settings["python"] and report["unshare_version"] == settings["unshare"]
            and timestamp(before["ended_at"]) <= timestamp(report["started_at"]) <= timestamp(report["ended_at"]))
    if ordinary:
        require(report["venue_profile"] == args.venue_profile
                and report["venue_manifest_sha256"] == before["source_sha256"][proof.VENUE_FILE])
    cleaned = False
    if "observation" in report:
        inner = report["observation"]
        proof.check_report(inner)
        require(inner["schema"] == "nib.selected-nat-proof/v2"
                and inner.get("venue_profile", proof.LEGACY_VENUE) == args.venue_profile
                and report["stage"] in ("report", "complete"))
        cleaned = report["namespace_reaped"] and inner["cleanup_confirmed"]
    if report["result"] == "pass":
        require(cleaned and report["stage"] == "complete" and report["observation"]["result"] == "pass")
    return report, cleaned, digest(raw)


def empty_result():
    return {"schema": "nib.selected-host-baseline-proof/v1", "result": "fail", "stage": "admission",
            "proof_pass": False, "namespace_reaped": False, "cleanup_confirmed": False,
            "baseline_complete": False, "firewall_equal": False, "dns_equal": False,
            "proof_receipt_sha256": None, "gpu_used": False, "published": False, "deployment_eligible": False}


def check_summary(result):
    require(type(result) is dict and set(result) == set(empty_result())
            and result["schema"] == "nib.selected-host-baseline-proof/v1"
            and result["result"] in ("pass", "fail")
            and result["stage"] in ("admission", "pre-snapshot", "proof", "post-snapshot", "compare", "complete"))
    flags = {"proof_pass", "namespace_reaped", "cleanup_confirmed", "baseline_complete", "firewall_equal", "dns_equal"}
    require(all(type(result[key]) is bool for key in flags)
            and all(result[key] is False for key in ("gpu_used", "published", "deployment_eligible")))
    receipt = result["proof_receipt_sha256"]
    require(receipt is None or type(receipt) is str and re.fullmatch("[0-9a-f]{64}", receipt))
    if result["proof_pass"]:
        require(result["cleanup_confirmed"])
    if result["cleanup_confirmed"]:
        require(result["namespace_reaped"])
    if result["namespace_reaped"]:
        require(receipt is not None)
    if result["firewall_equal"] or result["dns_equal"]:
        require(result["baseline_complete"])
    require((result["result"] == "pass") is (result["stage"] == "complete"))
    if result["result"] == "pass":
        require(all(result[key] for key in flags))


def run(args):
    require(args.run and sys.platform == "linux" and os.getuid() == 0
            and args.archive.is_absolute() and type(args.archive_sha256) is str
            and re.fullmatch("[0-9a-f]{64}", args.archive_sha256))
    private_directory(args.output, create=True)
    result = empty_result()
    result["stage"] = "pre-snapshot"
    with proof.cancellation() as cancelled:
        try:
            capture(args.output / "before", args.venue_profile, cancelled)
            before, _ = load_capture(args.output / "before")
            require(not cancelled["cancelled"])
            result["stage"] = "proof"
            status = None
            try:
                status = invoke_proof(args, cancelled)
            except BaseException:
                pass
            finally:
                result["stage"] = "post-snapshot"
                # Cancellation must not suppress this bounded, read-only final capture.
                capture(args.output / "after", args.venue_profile, {"cancelled": False})
            result["stage"] = "compare"
            result["firewall_equal"], result["dns_equal"] = compare(args.output / "before", args.output / "after")
            result["baseline_complete"] = True
            report, cleaned, receipt_hash = proof_result(args, before, status)
            after, _ = load_capture(args.output / "after")
            require(timestamp(report["ended_at"]) <= timestamp(after["started_at"]))
            result.update(proof_pass=report["result"] == "pass", namespace_reaped=report["namespace_reaped"],
                          cleanup_confirmed=cleaned, proof_receipt_sha256=receipt_hash)
            if all(result[key] for key in ("proof_pass", "cleanup_confirmed", "firewall_equal", "dns_equal")) \
                    and not cancelled["cancelled"]:
                result.update(result="pass", stage="complete")
        except BaseException:
            pass
    check_summary(result)
    write_file(args.output / "summary.json", encoded(result) + b"\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--venue-profile", choices=proof.VENUES, default=proof.LEGACY_VENUE)
    args = parser.parse_args()
    try:
        result = run(args)
    except BaseException:
        result = empty_result()
    check_summary(result)
    print(encoded(result).decode())
    return 0 if result["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
