# SPDX-License-Identifier: Apache-2.0
"""Pure policy/owner/fixture regressions. No Docker, sockets, mounts or nft calls."""

import copy
from contextlib import ExitStack, redirect_stdout
import importlib.util
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import types
import unittest
from unittest import mock

import selected_network_policy as policy
from fixtures import selected_nat_fixture as fixture

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("selected_nat_proof_test", ROOT / "scripts/prove-selected-nat.py")
proof = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(proof)


def contract():
    return policy.Policy("a" * 32, "nib-service", "nib-ingress", proof.S4, proof.H4, 443)


def readback(rows):
    return policy.encoded({"nftables": [{"metainfo": {"version": "1.0.9", "release_name": "Old Doc Yak #3",
                                                    "json_schema_version": 1}}, *rows]})


def value(expression, packet):
    kind, body = next(iter(expression.items()))
    return packet.get((kind, tuple(sorted(body.items()))))


def verdict(rows, family, chain, packet):
    """Test the renderer's exact conditions, not a replacement for kernel proof."""
    for row in rows:
        rule = row.get("rule", {})
        if rule.get("family") != family or rule.get("chain") != chain:
            continue
        for expression in rule["expr"]:
            if "match" not in expression:
                return next(iter(expression))
            match = expression["match"]
            left, right, op = value(match["left"], packet), match["right"], match["op"]
            if not ((left == right) if op in ("==", "in") else (left != right)):
                break
    return "accept"


def packet(**changes):
    values = [(policy.meta("iifname"), "nib-ingress"), (policy.meta("oifname"), "nib-service"),
        (policy.meta("nfproto"), "ipv4"), (policy.meta("l4proto"), "tcp"),
        (policy.payload("ip", "daddr"), proof.S4), (policy.payload("ip", "saddr"), proof.C4),
        (policy.payload("tcp", "dport"), 8443), (policy.payload("tcp", "sport"), 49123),
        (policy.ct("state"), "new"), (policy.ct("direction"), "original"), (policy.ct("status"), "dnat"),
        (policy.ct("ip daddr", dir="original"), proof.H4),
        (policy.ct("proto-dst", dir="original"), 443)]
    result = {(kind, tuple(sorted(body.items()))): item
              for expression, item in values for kind, body in expression.items()}
    for name, item in changes.items():
        expression = policy.meta(name) if name in ("iifname", "oifname", "nfproto", "l4proto") else policy.ct(name)
        kind, body = next(iter(expression.items()))
        result[(kind, tuple(sorted(body.items())))] = item
    return result


class PolicyTests(unittest.TestCase):
    def test_closed_parameters_reject_injection_and_ambiguous_addresses(self):
        fields = vars(contract())
        for key, bad in (("token", "../bad"), ("bridge", 'x";flush ruleset'), ("bridge", "lo"),
                         ("ingress", "nib-service"), ("address", "::1"), ("published", "0.0.0.0"),
                         ("published", "127.0.0.1"), ("port", True), ("port", 65536)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                policy.Policy(**{**fields, key: bad})

    def test_create_only_batch_and_exact_four_hooks(self):
        result = policy.decode(contract().batch())["nftables"]
        self.assertEqual(hashlib.sha256(contract().batch()).hexdigest(),
                         "dd250199df014064cf7ca8d43281c9231477738b5e479688b7ddc66f90ee7ec6")
        self.assertEqual([next(iter(row)) for row in result].count("create"), 2)
        self.assertFalse(any("flush" in row or "delete" in row for row in result))
        hooks = {(row["chain"]["family"], row["chain"]["hook"]) for row in contract().objects() if "chain" in row}
        self.assertEqual(hooks, {("inet", "input"), ("inet", "forward"), ("inet", "output"), ("bridge", "forward")})

    def test_forward_only_original_tls_and_its_reply(self):
        rows = contract().objects()
        for state in ("new", "established"):
            self.assertEqual(verdict(rows, "inet", "forward", packet(state=state)), "accept")
        for changed in (dict(nfproto="ipv6"), dict(l4proto="udp"), dict(state="invalid"),
                        dict(state="untracked"), dict(status="none"), dict(iifname="unapproved")):
            self.assertEqual(verdict(rows, "inet", "forward", packet(**changed)), "drop")
        outbound = packet(iifname="nib-service", oifname="nib-ingress")
        for state in ("new", "established", "related"):
            outbound[("ct", (("key", "state"),))] = state
            self.assertEqual(verdict(rows, "inet", "forward", outbound), "drop")
        reply = packet(iifname="nib-service", oifname="nib-ingress", state="established", direction="reply")
        reply[("payload", (("field", "saddr"), ("protocol", "ip")))] = proof.S4
        reply[("payload", (("field", "sport"), ("protocol", "tcp")))] = 8443
        self.assertEqual(verdict(rows, "inet", "forward", reply), "accept")

    def test_host_access_ipv6_and_l2_denied_without_global_host_drop(self):
        rows = contract().objects()
        self.assertEqual(verdict(rows, "inet", "input", packet(iifname="nib-service")), "drop")
        self.assertEqual(verdict(rows, "inet", "output", packet(nfproto="ipv6")), "drop")
        self.assertEqual(verdict(rows, "inet", "output", packet()), "accept")
        for port in (53, 8000, 8001, 8450):
            row = packet()
            row[("payload", (("field", "dport"), ("protocol", "tcp")))] = port
            self.assertEqual(verdict(rows, "inet", "output", row), "drop")
        for key in ("ibrname", "obrname"):
            self.assertEqual(verdict(rows, "bridge", "peers", {("meta", (("key", key),)): "nib-service"}), "drop")
        self.assertEqual(verdict(rows, "inet", "forward", packet(iifname="other", oifname="other2")), "accept")

    def test_host_tls_reply_uses_exact_tuple_before_or_after_reverse_nat(self):
        reply = packet(iifname="nib-service", state="established", direction="reply")
        reply[("ct", (("dir", "reply"), ("key", "ip saddr")))] = proof.S4
        reply[("ct", (("dir", "reply"), ("key", "proto-src")))] = 8443
        for address, port in ((proof.H4, 443), (proof.S4, 8443)):
            reply[("ct", (("dir", "original"), ("key", "ip daddr")))] = address
            reply[("ct", (("dir", "original"), ("key", "proto-dst")))] = port
            self.assertEqual(verdict(contract().objects(), "inet", "input", reply), "accept")
        reply[("ct", (("dir", "reply"), ("key", "proto-src")))] = 8000
        self.assertEqual(verdict(contract().objects(), "inet", "input", reply), "drop")

    def test_readback_handles_order_and_every_policy_change(self):
        rows = contract().objects(canonical=True)
        for index, row in enumerate(rows):
            next(iter(row.values()))["handle"] = index + 1
        contract().verify(readback(rows))
        for index in range(len(rows)):
            broken = copy.deepcopy(rows)
            del broken[index]
            with self.subTest(missing=index), self.assertRaises(ValueError):
                contract().verify(readback(broken))
        broken = copy.deepcopy(rows)
        next(row["rule"] for row in broken if "rule" in row)["expr"][-1] = {"drop": None}
        with self.assertRaises(ValueError):
            contract().verify(readback(broken))
        broken = copy.deepcopy(rows)
        broken.append({"set": {"family": "inet", "table": contract().table, "name": "bypass"}})
        with self.assertRaises(ValueError):
            contract().verify(readback(broken))

    def test_upstream_derived_expected_form_changes_only_six_payload_rules(self):
        fixtures = json.loads((ROOT / "scripts/fixtures/nftables-1.0.9-canonical.json").read_text())
        self.assertEqual(fixtures["source"]["sha256"],
                         "a3c304cd9ba061239ee0474f9afb938a9bb99d89b960246f66f0c3a0a85e14cd")
        network, transport = fixtures["upstream_cases"]
        self.assertEqual(network["output"], network["input"][1:])
        self.assertEqual(transport["output"], transport["input"])
        before, after = contract().objects(), contract().objects(canonical=True)
        guard = fixtures["derived_policy_input_guard"]
        self.assertEqual(before[5]["rule"]["expr"], guard["input"])
        self.assertEqual(after[5]["rule"]["expr"], guard["output"])
        tcp4 = [policy.match(policy.meta("nfproto"), "ipv4"), policy.match(policy.meta("l4proto"), "tcp")]
        changed = []
        for original, canonical in zip(before, after, strict=True):
            if original == canonical:
                continue
            left, right = original["rule"], canonical["rule"]
            self.assertEqual({k: v for k, v in left.items() if k != "expr"},
                             {k: v for k, v in right.items() if k != "expr"})
            self.assertEqual([e for e in left["expr"] if e in tcp4], tcp4)
            self.assertEqual([e for e in left["expr"] if e not in tcp4], right["expr"])
            payloads = [e["match"]["left"]["payload"] for e in right["expr"]
                        if "payload" in e.get("match", {}).get("left", {})]
            self.assertEqual([p["protocol"] for p in payloads], ["ip", "tcp"])
            changed.append(left["chain"])
        self.assertEqual(changed, ["input", "forward", "forward", "forward", "output", "output"])
        for row in after[2:4]:
            self.assertEqual(row["rule"]["expr"][1:3], tcp4)
        contract().verify(readback(after))
        with self.assertRaises(ValueError):
            contract().verify(readback(before))

    def test_canonical_readback_rejects_every_predicate_removal_and_added_expression(self):
        rows = contract().objects(canonical=True)
        for i, row in enumerate(rows):
            if "rule" not in row:
                continue
            for j in range(len(row["rule"]["expr"])):
                broken = copy.deepcopy(rows)
                del broken[i]["rule"]["expr"][j]
                with self.subTest(rule=i, expression=j), self.assertRaises(ValueError):
                    contract().verify(readback(broken))
            broken = copy.deepcopy(rows)
            broken[i]["rule"]["expr"].insert(0, {"accept": None})
            with self.subTest(extra_expression=i), self.assertRaises(ValueError):
                contract().verify(readback(broken))

    def test_canonical_readback_keeps_rule_order_and_rejects_extra_rules_and_fields(self):
        rows = contract().objects(canonical=True)
        for left, right in ((2, 3), (7, 8), (13, 14), (18, 19)):
            broken = copy.deepcopy(rows)
            broken[left], broken[right] = broken[right], broken[left]
            with self.subTest(reorder=(left, right)), self.assertRaises(ValueError):
                contract().verify(readback(broken))
        for kind in ("extra-rule", "extra-field", "verdict", "port", "address", "family"):
            broken = copy.deepcopy(rows)
            rule = broken[5]["rule"]
            if kind == "extra-rule":
                broken.append(copy.deepcopy(broken[5]))
            elif kind == "extra-field":
                rule["comment"] = "unexpected"
            elif kind == "verdict":
                rule["expr"][-1] = {"accept": None}
            elif kind == "port":
                rule["expr"][1]["match"]["right"] = 8000
            elif kind == "address":
                rule["expr"][0]["match"]["right"] = "198.19.0.2"
            else:
                rule["expr"][0]["match"]["left"]["payload"]["protocol"] = "ip6"
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                contract().verify(readback(broken))

    def test_pinned_nft_json_keys_and_no_forward_bypass(self):
        raw = contract().batch()
        self.assertIn(b'"key":"ip daddr"', raw)
        self.assertNotIn(b'"family":"ip"', raw)
        self.assertIn(b'"key":"ibrname"', raw)
        for raw in (policy.encoded({"nftables": contract().objects(canonical=True)}),
                    readback(contract().objects(canonical=True)).replace(b'"1.0.9"', b'"1.0.8"'),
                    readback([*contract().objects(canonical=True),
                              {"flowtable": {"family": "inet", "table": "foreign"}}])):
            with self.assertRaises(ValueError):
                contract().verify(raw)

    def test_unknown_create_or_unproven_absence_never_removes_policy(self):
        for absent, resolved in ((False, True), (True, False), (True, 1)):
            with self.assertRaises(ValueError):
                contract().remove_batch(containers_absent=absent, creations_resolved=resolved)
        rows = policy.decode(contract().remove_batch(containers_absent=True, creations_resolved=True))["nftables"]
        self.assertEqual([row["delete"]["table"]["family"] for row in rows], ["bridge", "inet"])

    def test_dns_exact_loopback_override_no_inheritance_or_extra_options(self):
        host = {"Dns": ["127.0.0.1"]}
        policy.check_dns({"HostConfig": host})
        for key, value in (("Dns", []), ("Dns", ["127.0.0.11"]), ("Dns", ["127.0.0.1", "198.19.0.1"]),
                           ("DnsSearch", ["invalid"]), ("DnsOptions", ["ndots:0"]), ("ExtraHosts", ["host:host-gateway"])):
            with self.subTest(key=key), self.assertRaises(ValueError):
                policy.check_dns({"HostConfig": {**host, key: value}})

    def test_duplicate_and_nonfinite_json_rejected(self):
        for raw in (b'{"nftables":[],"nftables":[]}', b'{"x":NaN}', b'[]' * 200000):
            with self.assertRaises(ValueError):
                policy.decode(raw)


class ProofTests(unittest.TestCase):
    def test_nft_diagnostic_records_attempt_without_exception_content(self):
        for failure in (1, 2, None):
            with self.subTest(failure=failure):
                commands = proof.Commands(proof.time.monotonic() + 60, {"cancelled": False}, Path("/synthetic"))
                report = {}
                calls = []
                def run(argv, **kwargs):
                    calls.append((argv, kwargs))
                    if len(calls) == failure:
                        raise ValueError("PRIVATE-SYNTHETIC-CANARY")
                with mock.patch.object(commands, "run", side_effect=run):
                    if failure is None:
                        commands.nft(b"fixed-policy", report=report)
                    else:
                        with self.assertRaises(ValueError):
                            commands.nft(b"fixed-policy", report=report)
                self.assertEqual(report, {"policy_step": "check" if failure == 1 else "apply"})
                self.assertEqual(calls[0], (["nft", "--check", "--json", "--file", "-"], {"data": b"fixed-policy"}))
                if failure != 1:
                    self.assertEqual(calls[1], (["nft", "--json", "--file", "-"], {"data": b"fixed-policy"}))

    def test_outer_reaps_on_bad_output_timeout_and_cancelled_spawn(self):
        for failure in ("spawn", "malformed", "oversized", "timeout", "reap", "cancel-race"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                output = Path(directory).resolve() / "output"
                argv = ["proof", "--run", "--archive", "/synthetic/image.tar", "--archive-sha256", "a" * 64,
                        "--output", str(output)]
                old_stat = os.stat
                def status(path, *args, **kwargs):
                    if str(path) in ("/proc/self/ns/net", "/proc/self/ns/mnt"):
                        return types.SimpleNamespace(st_ino=123)
                    return old_stat(path, *args, **kwargs)
                commands = mock.Mock()
                commands.env = proof.ENV
                commands.run.return_value.stdout = b"unshare from util-linux 2.39.3\n"
                for target, name, options in (
                    (proof.sys, "argv", {"new": argv}), (proof.sys, "platform", {"new": "linux"}),
                    (proof.sys, "version_info", {"new": (3, 12, 13)}), (proof.os, "getuid", {"return_value": 0}),
                    (proof.platform, "machine", {"return_value": "x86_64"}),
                    (proof.Path, "is_file", {"return_value": True}), (proof.Path, "is_symlink", {"return_value": False}),
                    (proof.os, "stat", {"side_effect": status}), (proof, "copy_archive", {}),
                    (proof, "prepare_sources", {"return_value": ("a" * 40, {})}),
                    (proof, "Commands", {"return_value": commands}), (proof, "cleanup_cgroup", {})):
                    stack.enter_context(mock.patch.object(target, name, **options))
                child = mock.Mock(pid=123, returncode=0)
                def popen(*_args, **_kwargs):
                    if failure == "spawn":
                        raise OSError("PRIVATE-CANARY")
                    if failure == "cancel-race":
                        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
                    return child
                stack.enter_context(mock.patch.object(proof.subprocess, "Popen", side_effect=popen))
                collect = stack.enter_context(mock.patch.object(proof.snapshot, "bounded_output",
                    return_value=(b"PRIVATE-CANARY", b"PRIVATE-CANARY")))
                if failure in ("oversized", "timeout"):
                    collect.side_effect = proof.snapshot.OutputLimitExceeded("stdout", b"PRIVATE-CANARY", b"") \
                        if failure == "oversized" else subprocess.TimeoutExpired("synthetic", 1)
                reap = stack.enter_context(mock.patch.object(proof.snapshot, "cleanup_process_group"))
                if failure == "reap":
                    reap.side_effect = ValueError("PRIVATE-CANARY")
                stack.enter_context(mock.patch.object(proof.os, "killpg", side_effect=PermissionError("PRIVATE-CANARY")))
                stream = io.StringIO()
                with redirect_stdout(stream):
                    self.assertEqual(proof.main(), 1)
                self.assertNotIn("PRIVATE-CANARY", stream.getvalue())
                report = policy.decode((output / "observation.json").read_bytes())
                self.assertEqual(report["result"], "fail")
                self.assertEqual(reap.call_count, 0 if failure == "spawn" else 1)
                self.assertEqual(report["namespace_reaped"], failure not in ("spawn", "reap"))

    def inner_case(self, *, failure=None, cancel=False):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            work = Path(directory)
            (work / "fixture").mkdir()
            (work / "fixture/key.pem").write_bytes(b"synthetic")
            (work / "fixture/cert.pem").write_bytes(b"synthetic")
            args = types.SimpleNamespace(inner=work, archive=work / "image.tar", host_net=1, host_mount=2,
                                         deadline=proof.time.monotonic() + 400)
            commands = mock.Mock()
            commands.env = proof.ENV
            def result(raw=b""):
                return subprocess.CompletedProcess([], 0, raw, b"")
            def run(argv, **_kwargs):
                if argv[:2] == ["ip", "--json"]:
                    return result(policy.encoded([{"ifname": "lo"}]))
                if argv[:2] == ["nft", "--version"]:
                    return result(b"nftables v1.0.9 (Old Doc Yak #3)\n")
                return result(b"Docker version 29.1.3\n")
            commands.run.side_effect = run
            commands.rules.return_value = readback([])
            def docker(*argv, **_kwargs):
                if argv[0] == "version":
                    return result(policy.encoded({"Version": "29.1.3", "Os": "linux", "Arch": "amd64"}))
                if argv[:2] == ("container", "create"):
                    if failure == "create":
                        raise subprocess.TimeoutExpired("synthetic", 1)
                    return result(b"1" * 64)
                return result()
            commands.docker.side_effect = docker
            def inspect(kind, name):
                if kind == "image":
                    return {"Id": proof.CONFIG, "Os": "linux", "Architecture": "amd64", "Config": {}}
                if kind == "network":
                    number = 18 if name == proof.SERVICE else 19
                    return {"Name": name, "Driver": "bridge", "Internal": False, "EnableIPv6": True,
                        "Labels": {proof.LABEL: "a" * 32}, "Containers": {}, "Options": {
                            "com.docker.network.bridge.name": name, "com.docker.network.bridge.gateway_mode_ipv4": "nat",
                            "com.docker.network.bridge.gateway_mode_ipv6": "nat", "com.docker.network.bridge.enable_icc": "true"},
                        "IPAM": {"Config": [{"Subnet": f"198.{number}.0.0/24"},
                                               {"Subnet": f"fd00:4e49:{number - 17}::/64"}]}}
                return {"State": {"Status": "created", "Running": True}}
            commands.inspect.side_effect = inspect
            with mock.patch.object(proof, "query", return_value=True), mock.patch.object(proof, "host_query", return_value=True):
                positive = proof.observations(commands, work)
            protected = {key: key in ("tls_host", "tls_forward") for key in positive}
            controls = iter((positive, protected))
            def observe(*_args):
                if cancel:
                    signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
                return next(controls)
            for target, name, options in (
                (proof, "check_namespace", {}), (proof, "Commands", {"return_value": commands}),
                (proof.uuid, "uuid4", {"return_value": types.SimpleNamespace(hex="a" * 32)}),
                (proof.os, "chown", {}), (proof, "check_container", {}), (proof, "check_sinks", {}),
                (proof, "query", {"return_value": True}), (proof, "observations", {"side_effect": observe}),
                (proof, "host_query", {"return_value": {"tcp": 0, "dns_udp": 1, "dns_tcp": 1}})):
                stack.enter_context(mock.patch.object(target, name, **options))
            child = mock.Mock()
            child.poll.return_value = None
            stack.enter_context(mock.patch.object(proof, "spawn", return_value=child))
            reap = stack.enter_context(mock.patch.object(proof.snapshot, "cleanup_process_group"))
            cleanup = stack.enter_context(mock.patch.object(proof, "owned_cleanup", return_value=True))
            verify = stack.enter_context(mock.patch.object(policy.Policy, "verify"))
            if failure == "policy":
                commands.nft.side_effect = subprocess.TimeoutExpired("synthetic", 1)
            if failure == "readback":
                verify.side_effect = ValueError("PRIVATE-SYNTHETIC-CANARY")
            if failure == "cleanup":
                cleanup.side_effect = ValueError("synthetic")
            if failure == "reap":
                reap.side_effect = ValueError("synthetic")
            output = io.StringIO()
            with redirect_stdout(output):
                status = proof.inner(args)
            report = policy.decode(output.getvalue().encode())
            proof.check_report(report)
            return status, report, cleanup.call_args.args, reap.call_count, verify.call_count

    def test_inner_success_requires_controls_readback_and_cleanup(self):
        status, report, cleanup_args, reaps, verifications = self.inner_case()
        self.assertEqual(status, 0)
        self.assertEqual(report["result"], "pass")
        self.assertEqual(cleanup_args[-2:], (False, True))
        self.assertEqual((reaps, verifications), (2, 2))
        self.assertEqual(report["policy_step"], "protected-create")

    def test_readback_diagnostic_does_not_change_fail_closed_cleanup(self):
        status, report, cleanup_args, reaps, _ = self.inner_case(failure="readback")
        self.assertEqual((status, report["result"], report["policy_step"]), (1, "fail", "readback"))
        self.assertEqual(cleanup_args[-2:], (False, True))
        self.assertEqual(reaps, 2)
        self.assertNotIn("PRIVATE", json.dumps(report))
        for bad in ("PRIVATE-SYNTHETIC-CANARY", None, 7, [], {}):
            with self.assertRaises((ValueError, TypeError)):
                proof.check_report({**report, "policy_step": bad})

    def test_inner_failures_and_signal_never_promote_pass(self):
        for failure in ("create", "policy", "cleanup", "reap"):
            with self.subTest(failure=failure):
                status, report, cleanup_args, reaps, _ = self.inner_case(failure=failure)
                self.assertEqual((status, report["result"]), (1, "fail"))
                self.assertEqual(reaps, 2)
                if failure == "create":
                    self.assertEqual(cleanup_args[-2:], (True, False))
                if failure == "policy":
                    self.assertEqual(cleanup_args[-2:], (False, True))
        status, report, _, _, _ = self.inner_case(cancel=True)
        self.assertEqual((status, report["result"]), (1, "fail"))

    def test_sealed_archive_hash_size_symlinks_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "source", root / "sealed"
            source.write_bytes(b"synthetic-image")
            sha = hashlib.sha256(source.read_bytes()).hexdigest()
            proof.copy_archive(source, target, sha)
            source.write_bytes(b"changed")
            self.assertEqual(target.read_bytes(), b"synthetic-image")
            self.assertEqual(target.stat().st_mode & 0o777, 0o400)
            with self.assertRaises(FileExistsError):
                proof.copy_archive(source, target, sha)
            with self.assertRaises(ValueError):
                proof.copy_archive(source, root / "mismatch", sha)
            link = root / "link"
            link.symlink_to(source)
            with self.assertRaises(ValueError):
                proof.copy_archive(link, root / "symlink", sha)

    def test_staged_public_fixture_is_readable_under_private_operator_umask(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            commands = mock.Mock()
            def git(argv):
                raw = b"a" * 40 if argv[-2] == "rev-parse" else (ROOT / argv[-1].split(":", 1)[1]).read_bytes()
                return subprocess.CompletedProcess([], 0, raw, b"")
            commands.run.side_effect = git
            previous = os.umask(0o077)
            try:
                revision, hashes = proof.prepare_sources(work, commands)
            finally:
                os.umask(previous)
            self.assertEqual(revision, "a" * 40)
            self.assertEqual(set(hashes), set(proof.SOURCES))
            self.assertEqual((work / "fixture").stat().st_mode & 0o777, 0o755)
            self.assertEqual((work / "fixture/probe.py").stat().st_mode & 0o777, 0o444)

    def test_sinks_must_still_be_alive_after_negative_probes(self):
        server = mock.Mock()
        server.poll.return_value = None
        with mock.patch.object(proof, "query", return_value=True) as query:
            proof.check_sinks(mock.Mock(), server)
            self.assertEqual(query.call_count, 4)
        with mock.patch.object(proof, "query", return_value=False), self.assertRaises(ValueError):
            proof.check_sinks(mock.Mock(), server)
        server.poll.return_value = 1
        with self.assertRaises(ValueError):
            proof.check_sinks(mock.Mock(), server)

    def test_observation_requires_all_controls_and_literal_boolean(self):
        with mock.patch.object(proof, "host_query", return_value=True), mock.patch.object(proof, "query", return_value=True):
            positive = proof.observations(mock.Mock(), Path("/synthetic"))
        proof.check_observations(positive, False)
        protected = {key: key in ("tls_host", "tls_forward") for key in positive}
        proof.check_observations(protected, True)
        for key in protected:
            with self.subTest(key=key), self.assertRaises(ValueError):
                proof.check_observations({**protected, key: not protected[key]}, True)
        for broken in ({}, {**protected, "extra": False}, {**protected, "tls_host": 1}):
            with self.assertRaises(ValueError):
                proof.check_observations(broken, True)

    def test_report_rejects_unknown_content_and_unproven_pass(self):
        report = {"schema": "nib.selected-nat-proof/v1", "result": "fail", "stage": "preflight",
                  "namespace_verified": True, "cleanup_confirmed": False, "policy_removed": False,
                  "gpu_used": False, "deployment_eligible": False, "confidential": False, "published": False}
        proof.check_report(report)
        for extra in ({"body": "PRIVATE-CANARY"}, {"result": "pass"}, {"cleanup_confirmed": 1},
                      {"gpu_used": True}, {"engine_version": "29.4.0"}, {"observations": {}}):
            with self.assertRaises(ValueError):
                proof.check_report({**report, **extra})

    def test_namespace_flags_and_private_daemon_never_use_host_socket(self):
        work = Path("/tmp/synthetic")
        command = proof.namespace_command(work, Path("/tmp/image.tar"), 123, 124, 1000)
        for item in ("--mount", "--net", "--pid", "--fork", "--mount-proc=/proc", "--kill-child=KILL"):
            self.assertIn(item, command)
        self.assertEqual(command[command.index("--propagation") + 1], "private")
        daemon = proof.daemon_command(work)
        self.assertIn("unix:///tmp/synthetic/docker.sock", daemon)
        self.assertNotIn("/var/run/docker.sock", " ".join(daemon))
        self.assertNotIn("--iptables=false", daemon)
        self.assertNotIn("--network", daemon)

    def test_namespace_guard_rejects_host_before_any_spawn(self):
        with mock.patch.object(proof.os, "getpid", return_value=20), mock.patch.object(proof, "spawn") as spawn:
            with self.assertRaises(ValueError):
                proof.check_namespace(1, 2)
        spawn.assert_not_called()

    def test_absolute_deadline_reserve_and_stream_caps(self):
        with tempfile.TemporaryDirectory() as directory:
            commands = proof.Commands(200, {"cancelled": False}, Path(directory))
            with mock.patch.object(proof.time, "monotonic", return_value=100), \
                    mock.patch.object(proof.snapshot, "execute_process_group", return_value=
                        subprocess.CompletedProcess([], 0, b"", b"")) as execute:
                commands.run(["synthetic"])
            self.assertEqual(execute.call_args.kwargs["timeout"], 10)
            self.assertEqual(execute.call_args.kwargs["max_output_bytes"], 262144)
            self.assertEqual(commands.end, 200)
            commands.cleaning = True
            with mock.patch.object(proof.time, "monotonic", return_value=291), self.assertRaises(ValueError):
                commands.run(["synthetic"])

    def test_cancel_idempotent_restored_and_only_cleanup_allowed(self):
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous = signal.getsignal(sig)
            notify = mock.Mock()
            with proof.cancellation(notify) as cancelled:
                signal.getsignal(sig)(sig, None)
                signal.getsignal(sig)(sig, None)
                self.assertTrue(cancelled["cancelled"])
                notify.assert_called_once()
                commands = proof.Commands(proof.time.monotonic() + 100, cancelled, Path("/tmp/synthetic"))
                with self.assertRaises(ValueError):
                    commands.run(["synthetic"])
                commands.cleaning = True
                with mock.patch.object(proof.snapshot, "execute_process_group", return_value=
                        subprocess.CompletedProcess([], 0, b"", b"")):
                    commands.run(["synthetic"])
            self.assertEqual(signal.getsignal(sig), previous)

    def test_uncertain_creation_and_failed_inventory_retain_rules(self):
        commands = mock.Mock()
        commands.docker.return_value.stdout = b""
        with self.assertRaises(ValueError):
            proof.owned_cleanup(commands, "a" * 32, contract(), True, True)
        commands.nft.assert_not_called()

    def test_cleanup_readback_and_network_ambiguity(self):
        commands = mock.Mock()
        commands.docker.return_value.stdout = b""
        commands.rules.side_effect = [readback(contract().objects(canonical=True)), readback([])]
        self.assertTrue(proof.owned_cleanup(commands, "a" * 32, contract(), False, True))
        commands.nft.assert_called_once_with(contract().remove_batch(containers_absent=True, creations_resolved=True))
        commands.reset_mock()
        commands.rules.side_effect = [readback(contract().objects(canonical=True)),
                                      readback(contract().objects(canonical=True))]
        with self.assertRaises(ValueError):
            proof.owned_cleanup(commands, "a" * 32, contract(), False, True)
        commands.reset_mock()
        drift = contract().objects(canonical=True)
        del drift[5]["rule"]["expr"][0]
        commands.rules.side_effect = [readback(drift)]
        with self.assertRaises(ValueError):
            proof.owned_cleanup(commands, "a" * 32, contract(), False, True)
        commands.nft.assert_not_called()
        commands.reset_mock()
        commands.docker.side_effect = [subprocess.CompletedProcess([], 0, b"", b"")] * 4 + [
            subprocess.CompletedProcess([], 0, b"nib-service\n", b"")]
        with self.assertRaises(ValueError):
            proof.owned_cleanup(commands, "a" * 32, contract(), False, True)
        commands.nft.assert_not_called()
        commands.docker.side_effect = subprocess.TimeoutExpired("synthetic", 1)
        with self.assertRaises(subprocess.TimeoutExpired):
            proof.owned_cleanup(commands, "a" * 32, contract(), False, True)
        commands.nft.assert_not_called()

    def test_container_creation_pins_resources_no_gpu_and_opt_in_dns(self):
        argv = proof.container_command("candidate", "a" * 32, proof.SERVICE, proof.S4, proof.S6,
                                       Path("/tmp/synthetic"), 1000, protected=True)
        self.assertEqual(argv[argv.index("--dns") + 1], "127.0.0.1")
        for flag, value in (("--pull", "never"), ("--platform", "linux/amd64"), ("--memory", "128m"),
                            ("--memory-swap", "128m"), ("--cpus", "1"), ("--user", "65532:65532")):
            self.assertEqual(argv[argv.index(flag) + 1], value)
        for flag in ("--gpus", "--privileged", "--device", "--net=host"):
            self.assertNotIn(flag, argv)


class FixtureTests(unittest.TestCase):
    def test_fragmented_bounded_frame_and_absolute_deadline(self):
        stream = mock.Mock()
        stream.recv.side_effect = [b"NI", b"B-", b"NAT-OK\n"]
        with mock.patch.object(fixture.time, "monotonic", return_value=100):
            self.assertEqual(fixture.receive(stream, len(fixture.ANSWER), 101), fixture.ANSWER)
        stream.recv.return_value, stream.recv.side_effect = b"N", None
        with mock.patch.object(fixture.time, "monotonic", side_effect=[100, 100.5, 101]), self.assertRaises(TimeoutError):
            fixture.receive(stream, 10, 101)

    def test_closed_ports_and_literal_addresses(self):
        with mock.patch.object(fixture.socket, "socket") as socket:
            for address, port in (("example.invalid", 443), ("198.18.0.2", 22)):
                with self.assertRaises(ValueError):
                    fixture.request(address, port, "tcp")
            socket.assert_not_called()

    def test_tls_uses_fixed_identity_and_two_frames(self):
        raw = mock.MagicMock()
        raw.__enter__.return_value = raw
        tls = mock.MagicMock()
        tls.__enter__.return_value = tls
        tls.recv.side_effect = [fixture.ANSWER, fixture.ANSWER]
        context = mock.Mock()
        context.wrap_socket.return_value = tls
        with mock.patch.object(fixture.socket, "socket", return_value=raw), \
                mock.patch.object(fixture.ssl, "create_default_context", return_value=context) as create:
            self.assertTrue(fixture.request("198.19.0.1", 443, "tls", "/synthetic/cert.pem"))
        create.assert_called_once_with(cafile="/synthetic/cert.pem")
        context.wrap_socket.assert_called_once_with(raw, server_hostname="nib.invalid")
        self.assertEqual(tls.sendall.call_args_list, [mock.call(fixture.FRAME)] * 2)


if __name__ == "__main__":
    unittest.main()
