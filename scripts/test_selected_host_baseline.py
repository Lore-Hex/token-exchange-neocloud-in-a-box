# SPDX-License-Identifier: Apache-2.0
"""Private snapshot fixtures only; never invoke nft, Docker, mounts or sockets."""

import copy
from contextlib import ExitStack, redirect_stdout
import io
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

import selected_host_baseline as baseline
import test_selected_quarantine_proof as quarantine_tests

proof = baseline.proof
DNS = b"nameserver ::1\n\n\n\n"
STAMP = {"dev": 1, "ino": 2, "uid": 0, "gid": 0, "mode": stat.S_IFREG | 0o644,
         "size": len(DNS), "mtime_ns": 1, "ctime_ns": 1}


def firewall(version="1.0.9"):
    # COUNTER object/statement forms from the pinned libnftables-json.adoc sections.
    return {"nftables": [
        {"metainfo": {"version": version, "release_name": baseline.policy.NFT_RELEASES[version], "json_schema_version": 1}},
        {"table": {"family": "inet", "name": "fixture", "handle": 1}},
        {"counter": {"family": "inet", "table": "fixture", "name": "named", "handle": 2, "packets": 3, "bytes": 4}},
        {"rule": {"family": "inet", "table": "fixture", "chain": "input", "handle": 3,
                  "expr": [{"counter": {"packets": 5, "bytes": 6}}, {"counter": "named"}, {"accept": None}]}}]}


def resolver(venue):
    ordinary = venue == proof.ORDINARY_VENUE
    return baseline.encoded({"path": str(proof.RESOLVER),
        "link": proof.venue_settings(venue)["resolver_link"] if ordinary else None,
        "target": str(proof.RESOLVER_TARGET if ordinary else proof.RESOLVER),
        "link_stat": {**STAMP, "mode": stat.S_IFLNK | 0o777} if ordinary else STAMP,
        "target_stat": {**STAMP, "uid": 989, "gid": 989} if ordinary else STAMP})


def fixture_capture(path, venue, cancelled=None):
    baseline.private_directory(path, create=True)
    files = {"firewall.json": baseline.encoded(firewall(proof.venue_settings(venue)["nftables"])),
             "resolver.json": resolver(venue), "resolver.conf": DNS}
    for name, raw in files.items():
        baseline.write_file(path / name, raw)
    when = "2026-10-25T00:00:0" + ("0" if path.name == "before" else "3") + "+00:00"
    manifest = {"schema": "nib.selected-host-baseline/v1", "git_sha": "a" * 40,
        "source_sha256": {name: baseline.digest((baseline.ROOT / name).read_bytes()) for name in baseline.SOURCES},
        "venue_profile": venue, "host_identity": {"net": 1, "mount": 2, "boot_sha256": "b" * 64},
        "started_at": when, "ended_at": when, "files": {name: baseline.digest(raw) for name, raw in files.items()}}
    baseline.write_file(path / "capture.json", baseline.encoded(manifest))
    return manifest


class CanonicalTests(unittest.TestCase):
    def test_only_documented_counter_values_ignored_both_pins(self):
        for version in baseline.policy.NFT_RELEASES:
            value = firewall(version)
            changed = copy.deepcopy(value)
            changed["nftables"][2]["counter"].update(packets=0, bytes=(1 << 64) - 1)
            changed["nftables"][3]["rule"]["expr"][0]["counter"].update(packets=900, bytes=0)
            self.assertEqual(baseline.canonical_firewall(baseline.encoded(value), version),
                             baseline.canonical_firewall(baseline.encoded(changed), version))
            self.assertEqual(value, firewall(version))

    def test_handles_order_predicates_and_other_state_are_significant(self):
        for version in baseline.policy.NFT_RELEASES:
            original = firewall(version)
            original["nftables"].extend([
                {"quota": {"family": "inet", "table": "fixture", "name": "q", "handle": 4, "bytes": 100, "used": 2}},
                {"set": {"family": "inet", "table": "fixture", "name": "s", "handle": 5,
                         "elem": [{"elem": {"val": 1, "expires": 100}}]}},
                {"unknown": {"handle": 6, "counter": {"packets": 7, "bytes": 8}}}])
            changes = []
            for index in range(1, len(original["nftables"])):
                changed = copy.deepcopy(original)
                next(iter(changed["nftables"][index].values()))["handle"] += 10
                changes.append(changed)
            for index, key in ((4, "used"), (4, "bytes")):
                changed = copy.deepcopy(original)
                changed["nftables"][index]["quota"][key] += 1
                changes.append(changed)
            changed = copy.deepcopy(original)
            changed["nftables"][5]["set"]["elem"][0]["elem"]["expires"] += 1
            changes.append(changed)
            changed = copy.deepcopy(original)
            changed["nftables"][6]["unknown"]["counter"]["bytes"] += 1
            changes.append(changed)
            changed = copy.deepcopy(original)
            changed["nftables"].reverse()
            changes.append(changed)
            for expr in ([{"drop": None}], [{"counter": "other"}], list(reversed(original["nftables"][3]["rule"]["expr"]))):
                changed = copy.deepcopy(original)
                changed["nftables"][3]["rule"]["expr"] = expr
                changes.append(changed)
            for changed in changes:
                with self.subTest(version=version, changed=changed):
                    self.assertNotEqual(baseline.canonical_firewall(baseline.encoded(original), version),
                                        baseline.canonical_firewall(baseline.encoded(changed), version))

    def test_invalid_or_unknown_counter_forms_cannot_be_silently_ignored(self):
        for value in (-1, True, 1.0, 1 << 64, "1", None):
            for anonymous in (False, True):
                changed = firewall()
                counter = (changed["nftables"][3]["rule"]["expr"][0]["counter"] if anonymous
                           else changed["nftables"][2]["counter"])
                counter["packets"] = value
                with self.subTest(value=value, anonymous=anonymous), self.assertRaises(ValueError):
                    baseline.canonical_firewall(baseline.encoded(changed), "1.0.9")
        changed = firewall()
        changed["nftables"][3]["rule"]["expr"][0]["counter"]["unknown"] = 1
        with self.assertRaises(ValueError):
            baseline.canonical_firewall(baseline.encoded(changed), "1.0.9")

    def test_duplicate_oversize_nonfinite_and_wrong_metadata_rejected(self):
        for raw in (b'{"nftables":[],"nftables":[]}', b" " * (baseline.LIMIT + 1), b'{"nftables":[NaN]}',
                    baseline.encoded(firewall("1.1.6")), baseline.encoded({"nftables": []})):
            with self.subTest(raw=raw[:40]), self.assertRaises(ValueError):
                baseline.canonical_firewall(raw, "1.0.9")
        changed = firewall()
        changed["nftables"][0]["metainfo"]["json_schema_version"] = True
        with self.assertRaises(ValueError):
            baseline.canonical_firewall(baseline.encoded(changed), "1.0.9")


class SnapshotTests(unittest.TestCase):
    def test_raw_private_snapshots_and_exact_comparison(self):
        for venue in proof.VENUES:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                for name in ("before", "after"):
                    fixture_capture(root / name, venue)
                    self.assertEqual((root / name).stat().st_mode & 0o777, 0o700)
                    self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in (root / name).iterdir()))
                self.assertEqual(baseline.compare(root / "before", root / "after"), (True, True))
                raw = (root / "before/firewall.json").read_bytes()
                baseline.compare(root / "before", root / "after")
                self.assertEqual(raw, (root / "before/firewall.json").read_bytes())

    def test_tampering_and_source_identity_and_metadata_are_closed(self):
        for mutation in ("raw", "files", "git_sha", "source_sha256", "host_identity", "started_at", "unknown", "permissions", "symlink"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                fixture_capture(root / "before", proof.LEGACY_VENUE)
                manifest = fixture_capture(root / "after", proof.LEGACY_VENUE)
                if mutation == "raw":
                    (root / "after/resolver.conf").write_bytes(b"PRIVATE-CANARY")
                elif mutation == "permissions":
                    (root / "after/firewall.json").chmod(0o644)
                elif mutation == "symlink":
                    (root / "after/firewall.json").unlink()
                    (root / "after/firewall.json").symlink_to(root / "before/firewall.json")
                else:
                    manifest[mutation] = {} if mutation.endswith("sha256") else "PRIVATE-CANARY"
                    (root / "after/capture.json").write_bytes(baseline.encoded(manifest))
                with self.assertRaises((ValueError, OSError)):
                    baseline.compare(root / "before", root / "after")

    def test_dns_identity_contents_and_firewall_handles_do_not_compare_equal(self):
        for name in baseline.FILES:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                fixture_capture(root / "before", proof.LEGACY_VENUE)
                manifest = fixture_capture(root / "after", proof.LEGACY_VENUE)
                path = root / "after" / name
                if name == "resolver.conf":
                    raw = DNS.replace(b"::1", b"::2")
                else:
                    value = baseline.decode(path.read_bytes())
                    if name == "resolver.json":
                        value["target_stat"]["ino"] += 1
                        value["link_stat"]["ino"] += 1
                    else:
                        value["nftables"][1]["table"]["handle"] += 1
                    raw = baseline.encoded(value)
                path.write_bytes(raw)
                manifest["files"][name] = baseline.digest(raw)
                (root / "after/capture.json").write_bytes(baseline.encoded(manifest))
                self.assertEqual(baseline.compare(root / "before", root / "after"),
                                 (False, True) if name == "firewall.json" else (True, False))

    def test_capture_is_read_only_bounded_and_keeps_exact_nft_bytes(self):
        for venue in proof.VENUES:
            with self.subTest(venue=venue), tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                output = Path(directory).resolve() / "capture"
                raw = baseline.encoded(firewall(proof.venue_settings(venue)["nftables"])) + b"\n"
                commands = mock.Mock()
                commands.run.return_value = subprocess.CompletedProcess([], 0, raw, b"")
                for obj, key, options in (
                    (baseline.sys, "platform", {"new": "linux"}), (baseline.os, "getuid", {"return_value": 0}),
                    (baseline, "private_directory", {"side_effect": lambda path, **_: path.mkdir(mode=0o700)}),
                    (proof, "Commands", {"return_value": commands}), (proof, "check_venue", {}),
                    (baseline, "source_identity", {"return_value": ("a" * 40, {})}),
                    (baseline, "host_identity", {"return_value": {}}),
                    (baseline, "resolver_snapshot", {"return_value": (DNS, resolver(venue))}),
                    (baseline, "read_file", {"side_effect": lambda path: (path.read_bytes(), {})}),
                    (baseline.time, "monotonic", {"return_value": 100})):
                    stack.enter_context(mock.patch.object(obj, key, **options))
                baseline.capture(output, venue, {"cancelled": False})
                self.assertEqual((output / "firewall.json").read_bytes(), raw)
                self.assertEqual(commands.run.call_args.args[0], ["nft", "--json",
                    *(["--numeric-protocol"] if venue == proof.ORDINARY_VENUE else []), "list", "ruleset"])
                self.assertEqual(commands.run.call_count, 1)
                self.assertEqual(proof.Commands.call_args.args[0], 100 + baseline.CAPTURE_SECONDS + proof.REAP)

    def test_no_overwrite_or_nonprivate_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            baseline.write_file(root / "original", b"private-original")
            with self.assertRaises(FileExistsError):
                baseline.write_file(root / "original", b"replacement")
            root.chmod(0o755)
            with self.assertRaises(ValueError):
                baseline.private_directory(root)
            self.assertEqual((root / "original").read_bytes(), b"private-original")

    def test_resolver_regular_file_and_link_identity_are_preserved_and_races_rejected(self):
        for symlink in (False, True):
            for mutate in (False, True):
                with self.subTest(symlink=symlink, mutate=mutate), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory).resolve()
                    target, link = root / "target", root / "resolv.conf"
                    baseline.write_file(target, DNS)
                    if symlink:
                        link.symlink_to("target")
                    else:
                        link = target
                    read = baseline.read_file
                    def changing_read(*args, **kwargs):
                        result = read(*args, **kwargs)
                        if mutate:
                            target.write_bytes(b"PRIVATE-CANARY")
                        return result
                    with mock.patch.object(proof, "RESOLVER", link), \
                            mock.patch.object(proof, "resolver_target", return_value=target), \
                            mock.patch.object(baseline, "read_file", side_effect=changing_read):
                        if mutate:
                            with self.assertRaises(ValueError):
                                baseline.resolver_snapshot(proof.LEGACY_VENUE)
                        else:
                            raw, metadata = baseline.resolver_snapshot(proof.LEGACY_VENUE)
                            self.assertEqual(raw, DNS)
                            row = baseline.decode(metadata)
                            self.assertEqual(row["link"], "target" if symlink else None)
                            self.assertEqual(row["target_stat"], baseline.stamp(target.stat()))

    def test_git_source_binding_includes_wrapper_and_all_original_proof_sources(self):
        commands = mock.Mock()
        def run(argv):
            raw = b"a" * 40 if argv[-2:] == ["rev-parse", "HEAD"] else (
                baseline.ROOT / argv[-1].split(":", 1)[1]).read_bytes()
            return subprocess.CompletedProcess(argv, 0, raw, b"")
        commands.run.side_effect = run
        revision, hashes = baseline.source_identity(commands)
        self.assertEqual(revision, "a" * 40)
        self.assertEqual(set(hashes), {*proof.SOURCES, baseline.SOURCE})
        with mock.patch.object(baseline, "read_file", return_value=(b"changed", {})):
            with self.assertRaises(ValueError):
                baseline.source_identity(commands)


class WrapperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inners = {venue: quarantine_tests.QuarantineProofTests().run_proof(venue=venue)[0][1] for venue in proof.VENUES}

    def exercise(self, venue=proof.LEGACY_VENUE, failure=None):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            args = types.SimpleNamespace(run=True, archive=Path("/synthetic/offline-image.tar"),
                archive_sha256="c" * 64, output=Path(directory).resolve() / "run", venue_profile=venue)
            events = []
            def capture(path, selected, cancelled):
                events.append(path.name)
                if failure == path.name:
                    raise ValueError("PRIVATE-CANARY")
                fixture_capture(path, selected, cancelled)
                if failure == "cancel-before" and path.name == "before":
                    signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
                if failure == "cancel-after" and path.name == "after":
                    signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
                    signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
            def invoke(given, cancelled):
                self.assertIs(given, args)
                events.append("proof")
                if failure == "exception":
                    raise RuntimeError("PRIVATE-CANARY")
                before, _ = baseline.load_capture(args.output / "before")
                settings = proof.venue_settings(venue)
                report = {"schema": "nib.selected-nat-proof-host/v2", "git_sha": before["git_sha"],
                    "source_sha256": {key: before["source_sha256"][key] for key in proof.SOURCES},
                    "archive_sha256": args.archive_sha256, "result": "pass", "namespace_reaped": True,
                    "gpu_used": False, "published": False, "deployment_eligible": False, "stage": "complete",
                    "started_at": "2026-10-25T00:00:01+00:00", "ended_at": "2026-10-25T00:00:02+00:00",
                    "host_python": settings["python"], "unshare_version": settings["unshare"],
                    "observation": copy.deepcopy(self.inners[venue])}
                if venue == proof.ORDINARY_VENUE:
                    report.update(venue_profile=venue, venue_manifest_sha256=before["source_sha256"][proof.VENUE_FILE])
                if failure == "cleanup":
                    report.update(result="fail", stage="cgroup", namespace_reaped=False)
                    del report["observation"]
                if failure in ("git_sha", "source_sha256", "schema", "archive_sha256", "host_python", "unknown"):
                    report[failure] = "PRIVATE-CANARY"
                if failure == "inner":
                    report["observation"]["quarantine"]["verified"] = False
                if failure == "late":
                    report["ended_at"] = "2026-10-25T00:00:04+00:00"
                if failure == "false-claim":
                    report["published"] = True
                raw = baseline.encoded(report) + b"\n"
                (args.output / "proof").mkdir(mode=0o700)
                baseline.write_file(args.output / "proof/observation.json", raw)
                baseline.write_file(args.output / "proof-stdout.json", raw if failure != "stdout" else b"{}")
                return 1 if failure in ("cleanup", "exit") else 0
            real_directory = baseline.private_directory
            def private_directory(path, **kwargs):
                with mock.patch.object(baseline.os, "getuid", return_value=uid):
                    real_directory(path, **kwargs)
            uid = baseline.os.getuid()
            read = baseline.read_file
            def read_file(*values, **kwargs):
                with mock.patch.object(baseline.os, "getuid", return_value=uid):
                    return read(*values, **kwargs)
            for obj, name, options in (
                (baseline.sys, "platform", {"new": "linux"}), (baseline.os, "getuid", {"return_value": 0}),
                (baseline, "capture", {"side_effect": capture}), (baseline, "invoke_proof", {"side_effect": invoke}),
                (baseline, "private_directory", {"side_effect": private_directory}),
                (baseline, "read_file", {"side_effect": read_file})):
                stack.enter_context(mock.patch.object(obj, name, **options))
            result = baseline.run(args)
            self.assertNotIn("PRIVATE-CANARY", baseline.encoded(result).decode())
            self.assertEqual(baseline.decode((args.output / "summary.json").read_bytes()), result)
            return result, events

    def test_complete_wrapper_both_profiles_and_no_claim_promotion(self):
        for venue in proof.VENUES:
            report, events = self.exercise(venue)
            self.assertEqual(events, ["before", "proof", "after"])
            self.assertEqual((report["result"], report["stage"]), ("pass", "complete"))
            self.assertTrue(all(report[key] for key in ("proof_pass", "namespace_reaped", "cleanup_confirmed",
                                                       "baseline_complete", "firewall_equal", "dns_equal")))
            self.assertTrue(all(report[key] is False for key in ("gpu_used", "published", "deployment_eligible")))

    def test_pre_capture_failure_and_cancellation_prevent_proof(self):
        for failure in ("before", "cancel-before"):
            report, events = self.exercise(failure=failure)
            self.assertEqual(events, ["before"])
            self.assertEqual(report["result"], "fail")

    def test_proof_failure_post_capture_failure_and_repeated_cancellation_fail_closed(self):
        for failure in ("exception", "after", "cancel-after", "cleanup", "stdout", "exit", "git_sha", "source_sha256",
                        "schema", "archive_sha256", "host_python", "unknown", "inner", "late", "false-claim"):
            with self.subTest(failure=failure):
                report, events = self.exercise(failure=failure)
                self.assertEqual(events, ["before", "proof", "after"])
                self.assertEqual(report["result"], "fail")
                if failure == "cleanup":
                    self.assertFalse(report["cleanup_confirmed"])
                    self.assertFalse(report["namespace_reaped"])
                if failure == "exception":
                    self.assertTrue(report["baseline_complete"])
                    self.assertTrue(report["firewall_equal"])
                    self.assertIsNone(report["proof_receipt_sha256"])

    def test_fixed_entry_collector_always_reaps_without_extending_proof_budget(self):
        for failure in (None, "spawn", "collect", "cleanup", "cancel-spawn", "cancel-before", "cancel-collect", "oversized"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                args = types.SimpleNamespace(archive=Path("/synthetic/image.tar"), archive_sha256="a" * 64,
                    output=Path(directory).resolve(), venue_profile=proof.ORDINARY_VENUE)
                child = mock.Mock(pid=123, returncode=0)
                def spawn(argv, **kwargs):
                    self.assertEqual(argv, [sys.executable, "-I", "-S", "-B", str(baseline.ROOT / "scripts/prove-selected-nat.py"),
                        "--run", "--quarantine", "--archive", str(args.archive), "--archive-sha256", "a" * 64,
                        "--output", str(args.output / "proof"), "--venue-profile", proof.ORDINARY_VENUE])
                    self.assertEqual(kwargs, dict(env=proof.ENV, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, start_new_session=True, close_fds=True, umask=0o077))
                    if failure == "spawn":
                        raise OSError("PRIVATE-CANARY")
                    if failure == "cancel-spawn":
                        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
                    return child
                def collect(process, argv, timeout, stream, limit, stderr_limit):
                    self.assertIs(process, child)
                    self.assertEqual(timeout, 595)
                    self.assertEqual((limit, stderr_limit), (262144, 65536))
                    stream.write(b'{"original":true}\n')
                    if failure == "cancel-collect":
                        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
                        signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
                    if failure in ("collect", "oversized"):
                        raise ValueError("PRIVATE-CANARY")
                    return b"", b"PRIVATE-CANARY"
                popen = stack.enter_context(mock.patch.object(baseline.subprocess, "Popen", side_effect=spawn))
                stack.enter_context(mock.patch.object(proof.snapshot, "bounded_output", side_effect=collect))
                cleanup = stack.enter_context(mock.patch.object(proof.snapshot, "cleanup_process_group",
                    side_effect=ValueError("PRIVATE-CANARY") if failure == "cleanup" else None))
                kill = stack.enter_context(mock.patch.object(baseline.os, "killpg"))
                stack.enter_context(mock.patch.object(baseline.time, "monotonic", return_value=10))
                original = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
                if failure:
                    with self.assertRaises((OSError, ValueError)):
                        baseline.invoke_proof(args, {"cancelled": failure == "cancel-before"})
                else:
                    self.assertEqual(baseline.invoke_proof(args, {"cancelled": False}), 0)
                self.assertEqual({sig: signal.getsignal(sig) for sig in original}, original)
                if failure in ("spawn", "cancel-before"):
                    cleanup.assert_not_called()
                else:
                    cleanup.assert_called_once_with(child)
                    self.assertEqual((args.output / "proof-stdout.json").read_bytes(), b'{"original":true}\n')
                if failure == "cancel-before":
                    popen.assert_not_called()
                if failure in ("cancel-spawn", "cancel-collect"):
                    kill.assert_called_once_with(child.pid, signal.SIGTERM)
                self.assertEqual((proof.OPERATIONS, proof.CLEANUP, proof.REAP), (480, 90, 25))

    def test_cli_suppresses_private_exceptions(self):
        argv = ["baseline", "--run", "--archive", "/synthetic/image.tar", "--archive-sha256", "a" * 64,
                "--output", "/synthetic/result"]
        stream = io.StringIO()
        with mock.patch.object(sys, "argv", argv), mock.patch.object(baseline, "run", side_effect=ValueError("PRIVATE-CANARY")), \
                redirect_stdout(stream):
            self.assertEqual(baseline.main(), 1)
        self.assertNotIn("PRIVATE", stream.getvalue())

    def test_summary_schema_cannot_promote_incomplete_results(self):
        baseline.check_summary(baseline.empty_result())
        good = self.exercise()[0]
        baseline.check_summary(good)
        for name in good:
            changed = {key: value for key, value in good.items() if key != name}
            with self.subTest(missing=name), self.assertRaises(ValueError):
                baseline.check_summary(changed)
        for name, value in (("schema", "unknown"), ("unknown", "PRIVATE-CANARY"), ("published", True),
                ("proof_pass", 1), ("baseline_complete", False), ("dns_equal", False),
                ("firewall_equal", False), ("namespace_reaped", False), ("cleanup_confirmed", False),
                ("proof_receipt_sha256", None), ("stage", "compare")):
            with self.subTest(name=name), self.assertRaises(ValueError):
                baseline.check_summary({**good, name: value})


if __name__ == "__main__":
    unittest.main()
