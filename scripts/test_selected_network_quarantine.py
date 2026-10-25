# SPDX-License-Identifier: Apache-2.0
"""Quarantine renderer/readback tests only; no kernel or network operations."""

import copy
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import unittest

import selected_network_policy as policy
from test_selected_network_policy import contract, packet, verdict


def quarantine(version="1.0.9"):
    return policy.Quarantine(replace(contract(), nft_version=version))


def readback(value, rows=None):
    return policy.encoded({"nftables": [{"metainfo": {
        "version": value.policy.nft_version,
        "release_name": policy.NFT_RELEASES[value.policy.nft_version], "json_schema_version": 1}},
        *(value.objects(canonical=True) if rows is None else rows)]})


def verify(value, raw):
    value.verify(raw, install_exit_code=0, readback_exit_code=0)


class QuarantineTests(unittest.TestCase):
    def test_requires_frozen_validated_policy(self):
        value = quarantine()
        with self.assertRaises(FrozenInstanceError):
            value.policy = contract()
        for bad in (None, {}, vars(contract()), "PRIVATE-CANARY"):
            with self.assertRaisesRegex(ValueError, "^selected network contract rejected$"):
                policy.Quarantine(bad)

    def test_distinct_create_only_tables_do_not_touch_serving_objects(self):
        source = contract()
        original = (source.batch(), source.objects(), source.objects(canonical=True),
                    source.remove_batch(containers_absent=True, creations_resolved=True))
        value = policy.Quarantine(source)
        commands = policy.decode(value.batch())["nftables"]
        tables = [row["create"]["table"] for row in commands if "create" in row]
        self.assertEqual(tables, [{"family": family, "name": value.table} for family in ("inet", "bridge")])
        self.assertNotEqual(source.table, value.table)
        self.assertEqual({next(iter(row)) for row in commands}, {"create", "add"})
        self.assertEqual([next(iter(row.values())) for row in commands], value.objects())
        self.assertEqual(original, (source.batch(), source.objects(), source.objects(canonical=True),
                                   source.remove_batch(containers_absent=True, creations_resolved=True)))

    def test_no_rule_accepts_replies_or_established_sessions(self):
        rows = quarantine().objects()
        rules = [row["rule"] for row in rows if "rule" in row]
        self.assertEqual(len(rules), 11)
        for rule in rules:
            self.assertEqual(rule["expr"][-1], {"drop": None})
            self.assertTrue(all(set(item) == {"match"} for item in rule["expr"][:-1]))
            self.assertFalse(any(item.get("match", {}).get("left") in
                                (policy.ct("state"), policy.ct("direction")) for item in rule["expr"]))

    def test_bridge_ip_traffic_drops_in_both_directions_for_all_states(self):
        rows = quarantine().objects()
        for version in ("ipv4", "ipv6"):
            for protocol in ("tcp", "udp", "icmpv6"):
                for state in ("new", "established", "related", "invalid", "untracked"):
                    for direction in ("original", "reply"):
                        for hook, interfaces in (("input", {"iifname": "nib-service"}),
                            ("forward", {"iifname": "nib-service", "oifname": "other"}),
                            ("forward", {"iifname": "other", "oifname": "nib-service"}),
                            ("output", {"oifname": "nib-service"})):
                            row = packet(nfproto=version, l4proto=protocol, state=state,
                                         direction=direction, **interfaces)
                            with self.subTest(version=version, protocol=protocol, state=state,
                                              direction=direction, hook=hook):
                                self.assertEqual(verdict(rows, "inet", hook, row), "drop")

    def test_l2_peers_drop_without_bridge_ip_firewall_sysctls(self):
        for key in ("ibrname", "obrname"):
            row = {("meta", (("key", key),)): "nib-service"}
            self.assertEqual(verdict(quarantine().objects(), "bridge", "peers", row), "drop")
        self.assertEqual(verdict(quarantine().objects(), "bridge", "peers", {}), "accept")

    def test_untracked_publication_is_closed_including_loopback_proxy(self):
        for hook in ("input", "output"):
            for interface in ("lo", "nib-ingress", "unrelated"):
                row = {("meta", (("key", "nfproto"),)): "ipv4",
                    ("meta", (("key", "l4proto"),)): "tcp",
                    ("meta", (("key", "iifname"),)): interface,
                    ("meta", (("key", "oifname"),)): interface,
                    ("payload", (("field", "daddr"), ("protocol", "ip"))): contract().published,
                    ("payload", (("field", "dport"), ("protocol", "tcp"))): 443}
                self.assertEqual(verdict(quarantine().objects(), "inet", hook, row), "drop")
                row[("payload", (("field", "dport"), ("protocol", "tcp")))] = 444
                self.assertEqual(verdict(quarantine().objects(), "inet", hook, row), "accept")

    def test_original_tuple_closes_redirected_publication_and_proxy_replies(self):
        # Neither current interface nor translated address needs to match the
        # old serving state. This models original tuples, not a kernel proof.
        for hook in ("input", "forward", "output"):
            for direction in ("original", "reply"):
                row = packet(iifname="elsewhere", oifname="elsewhere2", direction=direction)
                row[("payload", (("field", "daddr"), ("protocol", "ip")))] = "198.18.0.99"
                self.assertEqual(verdict(quarantine().objects(), "inet", hook, row), "drop")
                row[("ct", (("dir", "original"), ("key", "proto-dst")))] = 444
                self.assertEqual(verdict(quarantine().objects(), "inet", hook, row), "accept")

    def test_unrelated_host_and_forward_traffic_is_not_globally_dropped(self):
        for hook in ("input", "forward", "output"):
            self.assertEqual(verdict(quarantine().objects(), "inet", hook, {}), "accept")
        hooks = {(row["chain"]["family"], row["chain"]["hook"], row["chain"]["prio"])
                 for row in quarantine().objects() if "chain" in row}
        self.assertEqual(hooks, {("inet", name, -10) for name in ("input", "forward", "output")}
                         | {("bridge", "forward", -210)})

    def test_installation_bytes_identical_across_pins_and_exact_readback_argv(self):
        old, new = quarantine(), quarantine("1.1.6")
        self.assertEqual(old.batch(), new.batch())
        self.assertEqual(old.readback_args(), ["nft", "--json", "list", "ruleset"])
        self.assertEqual(new.readback_args(), ["nft", "--json", "--numeric-protocol", "list", "ruleset"])
        for value in (old, new):
            verify(value, readback(value))
            with self.assertRaises(ValueError):
                verify(value, readback(value, value.objects()))

    def test_expected_canonical_forms_reuse_upstream_dependency_fixtures(self):
        fixtures = Path(__file__).parent / "fixtures"
        old = json.loads((fixtures / "nftables-1.0.9-canonical.json").read_text())
        new = json.loads((fixtures / "nftables-1.1.6-canonical.json").read_text())
        self.assertEqual(old["upstream_cases"][0]["output"], old["upstream_cases"][0]["input"][1:])
        self.assertEqual(new["upstream_cases"][0]["output"], new["upstream_cases"][0]["input"][1:])
        self.assertEqual(new["upstream_cases"][2]["output"][-1]["match"]["right"], 6)
        tcp4 = [policy.match(policy.meta("nfproto"), "ipv4"), policy.match(policy.meta("l4proto"), "tcp")]
        for version in policy.NFT_RELEASES:
            changed = []
            value = quarantine(version)
            for before, after in zip(value.objects(), value.objects(canonical=True), strict=True):
                if before == after:
                    continue
                left, right = before["rule"]["expr"], after["rule"]["expr"]
                self.assertEqual(left[:2], tcp4)
                if "payload" in left[2]["match"]["left"]:
                    self.assertEqual(right, left[2:])
                    changed.append("payload")
                else:
                    self.assertEqual(version, "1.1.6")
                    self.assertEqual(right, [policy.match(policy.meta("l4proto"), 6), *left[2:]])
                    changed.append("conntrack")
            self.assertEqual(changed, ["payload", "payload"] if version == "1.0.9" else
                             ["payload", "conntrack", "conntrack", "payload", "conntrack"])

    def test_missing_or_corrupt_serving_state_does_not_prevent_quarantine_validation(self):
        for version in policy.NFT_RELEASES:
            value = quarantine(version)
            serving = value.policy.objects(canonical=True)
            for row in serving:
                if "rule" in row:
                    row["rule"]["expr"] = [{"accept": None}]
            for other in ([], serving, [{"table": {"family": "inet", "name": "foreign"}}]):
                verify(value, readback(value, [*other, *value.objects(canonical=True)]))
            with self.assertRaises(ValueError):
                value.policy.verify(readback(value, [*serving, *value.objects(canonical=True)]))
            verify(value, readback(value))

    def test_kernel_failure_timeout_or_unknown_status_never_verifies(self):
        value = quarantine()
        raw = readback(value)
        for key in ("install_exit_code", "readback_exit_code"):
            for bad in (1, 255, -9, None, False, True, 0.0, "0"):
                codes = {"install_exit_code": 0, "readback_exit_code": 0, key: bad}
                with self.subTest(key=key, code=bad), self.assertRaises(ValueError):
                    value.verify(raw, **codes)
        with self.assertRaises(TypeError):
            value.verify(raw)
        # Prior success is not cached; a subsequent failed readback still fails.
        verify(value, raw)
        with self.assertRaises(ValueError):
            value.verify(raw, install_exit_code=0, readback_exit_code=1)

    def test_every_object_and_predicate_is_required_for_each_pin(self):
        for version in policy.NFT_RELEASES:
            value = quarantine(version)
            rows = value.objects(canonical=True)
            for index, row in enumerate(rows):
                missing = copy.deepcopy(rows)
                del missing[index]
                with self.subTest(version=version, missing=index), self.assertRaises(ValueError):
                    verify(value, readback(value, missing))
                if "rule" not in row:
                    continue
                for expression in range(len(row["rule"]["expr"])):
                    missing = copy.deepcopy(rows)
                    del missing[index]["rule"]["expr"][expression]
                    with self.subTest(version=version, rule=index, expression=expression), self.assertRaises(ValueError):
                        verify(value, readback(value, missing))

    def test_rejects_verdict_extra_rule_field_and_predicate_order(self):
        for version in policy.NFT_RELEASES:
            value = quarantine(version)
            for mutation in ("accept", "extra-rule", "extra-field", "predicate-order", "rule-order",
                             "hook", "priority", "family", "address", "port", "protocol-type"):
                rows = value.objects(canonical=True)
                rule = rows[4]["rule"]
                if mutation == "accept":
                    rule["expr"][-1] = {"accept": None}
                elif mutation == "extra-rule":
                    rows.append(copy.deepcopy(rows[4]))
                elif mutation == "extra-field":
                    rule["comment"] = "unexpected"
                elif mutation == "predicate-order":
                    rule["expr"][0], rule["expr"][1] = rule["expr"][1], rule["expr"][0]
                elif mutation == "rule-order":
                    rows[2], rows[3] = rows[3], rows[2]
                elif mutation == "hook":
                    rows[1]["chain"]["hook"] = "prerouting"
                elif mutation == "priority":
                    rows[1]["chain"]["prio"] = -10.0
                elif mutation == "family":
                    rule["family"] = "ip"
                elif mutation in ("address", "port"):
                    rule["expr"][-3 if mutation == "address" else -2]["match"]["right"] = (
                        "198.19.0.99" if mutation == "address" else 444)
                else:
                    rule["expr"][0]["match"]["right"] = 6.0
                with self.subTest(version=version, mutation=mutation), self.assertRaises(ValueError):
                    verify(value, readback(value, rows))

    def test_only_handles_and_cross_chain_enumeration_are_ignored(self):
        for version in policy.NFT_RELEASES:
            value = quarantine(version)
            rows = value.objects(canonical=True)
            for index, row in enumerate(rows):
                next(iter(row.values()))["handle"] = index + 1
            # Move complete chains, never reorder rules within a chain.
            rows = rows[-4:] + rows[:-4]
            verify(value, readback(value, rows))

    def test_global_flowtable_unknown_metadata_and_malformed_readback_fail(self):
        for version in policy.NFT_RELEASES:
            value = quarantine(version)
            for malformed in (b"{}", b"PRIVATE-CANARY", b" " * 262145,
                              readback(value).replace(b'"json_schema_version":1', b'"json_schema_version":1.0'),
                              readback(value).replace(b'"version":', b'"extra":0,"version":'),
                              readback(value).replace(b'"nftables":', b'"nftables":[],"nftables":'),
                              readback(value, [*value.objects(canonical=True),
                                  {"flowtable": {"family": "inet", "table": "foreign", "name": "offload"}}])):
                with self.assertRaises(ValueError):
                    verify(value, malformed)
            other = quarantine("1.1.6" if version == "1.0.9" else "1.0.9")
            with self.assertRaises(ValueError):
                verify(value, readback(other))

    def test_no_removal_until_every_resource_absent_and_creation_resolved(self):
        value = quarantine()
        states = dict(containers_absent=True, networks_absent=True, creations_resolved=True)
        for key in states:
            for uncertain in (False, None, 1, "true"):
                with self.subTest(key=key, uncertain=uncertain), self.assertRaises(ValueError):
                    value.remove_batch(**{**states, key: uncertain})
        removed = policy.decode(value.remove_batch(**states))["nftables"]
        self.assertEqual(removed, [{"delete": {"table": {"family": family, "name": value.table}}}
                                  for family in ("bridge", "inet")])
        self.assertNotEqual(value.remove_batch(**states), value.policy.remove_batch(
            containers_absent=True, creations_resolved=True))
        self.assertEqual(value.remove_batch(**states), quarantine("1.1.6").remove_batch(**states))


if __name__ == "__main__":
    unittest.main()
