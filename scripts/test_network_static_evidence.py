# SPDX-License-Identifier: Apache-2.0
"""Replay original static graph receipts without fetching or executing packages."""
import hashlib
import json
from pathlib import Path
import unittest

from test_evidence_history import git
import test_network_runtime as runtime_tests

p = runtime_tests.p

ROOT = Path(__file__).resolve().parent.parent
DIRECTORY = ROOT / "evidence/p3/2026-10-27-network-static-closure"
PRODUCER = "e90d78ce905170d2102fe6a8944716956d8e4b7f"
FAILED_PRODUCER = "9969645c198033eca5f61f888dbd113c6f8cd1d6"
FAILED_MERGED_SOURCE = "50e8a11f4bc0fc3de7039ff581968742a1072eb8"
ORIGINALS = {
    "closure.json": "9cea9f1b00300baf5b2729257d9d63420cca3c2871cfac5041c58232f8a40f3b",
    "inventory.json": "468282f9bdd630fa8c647ab4481b226016c79cfb7ad96d1c82c9d16e7c75aa2e",
    "output.log": "ed05ac97c9d2d4cb5dc24e8d42334bf42bd5ac75bc0c75ca782caefb1cf30e12",
    "missing-library.json": "4ee6c86c1e13d157cd4ee5a020abde6bdeb8f5a77b5307b371d3d2055987fdfb",
    "missing-library-inventory.json": "5a37491cbd92c47cc8dc4002fb5ea93a204501297ef3af637b81e0a314453ffd",
    "missing-library.log": "37989acc798c3d30fcac262cdcb5caa34abbd189f4cfb43e6f2b857d558134d3",
}


class NetworkStaticEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.rows = {name: json.loads((DIRECTORY / name).read_bytes()) for name in ORIGINALS}
        self.report = self.rows["closure.json"]

    def test_unchanged_originals_scoped_claims_and_documented_hashes(self):
        self.assertEqual({path.name for path in DIRECTORY.iterdir()}, {*ORIGINALS, "README.md"})
        readme = (DIRECTORY / "README.md").read_text()
        for name, digest in ORIGINALS.items():
            raw = (DIRECTORY / name).read_bytes()
            self.assertLess(len(raw), 1 << 20)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
            self.assertIn(digest, readme)
            for marker in (b"Bearer ", b"PRIVATE KEY", b"/Users/", b"/private/tmp/", b"hixtube"):
                self.assertNotIn(marker, raw)
        for name, log, inventory in (("closure.json", "output.log", "inventory.json"),
                                     ("missing-library.json", "missing-library.log", "missing-library-inventory.json")):
            report = self.rows[name]
            self.assertEqual(report["schema"], "nib.network-static-closure/v1")
            self.assertEqual(report["scope"], "restricted-offline-static-graph-only")
            self.assertEqual(report["inventory_sha256"], ORIGINALS[inventory])
            self.assertEqual(self.rows[log], {k: report[k] for k in ("result", "git_sha", "static_graph_closed")})
            for field in p.CLAIMS:
                self.assertIs(report[field], False)
            self.assertIs(report["compiler"]["compiler_authenticated"], False)
            self.assertEqual(report["compiler"]["version"], "go1.27.2")

    def test_historical_producers_complete_source_and_locked_selection(self):
        expected = set(p.tools.FILES) | set(p.EXTRA)
        for name, producer, source in (("closure.json", PRODUCER, PRODUCER),
                                       ("missing-library.json", FAILED_PRODUCER, FAILED_MERGED_SOURCE)):
            report = self.rows[name]
            self.assertEqual(report["git_sha"], producer)
            git(ROOT, "merge-base", "--is-ancestor", source, "HEAD")
            self.assertEqual(set(report["source_hashes"]), expected)
            for path, digest in report["source_hashes"].items():
                self.assertEqual(hashlib.sha256(git(ROOT, "show", source + ":" + path)).hexdigest(), digest)
            lock_raw = git(ROOT, "show", source + ":" + p.LOCK)
            self.assertEqual(hashlib.sha256(lock_raw).hexdigest(), report["lock_sha256"])
            lock = json.loads(lock_raw)
            packages = {row["Package"]: row for row in lock["packages"]}
            for row in report["packages"]:
                self.assertEqual(row, packages[row["Package"]])
            indexes = {row["id"]: row for row in lock["indexes"]}
            self.assertEqual(report["indexes"], [indexes[row["id"]] for row in report["indexes"]])
            self.assertEqual({row["id"] for row in report["indexes"]},
                             {row["index"] for row in report["packages"]})
        self.assertEqual([row["Package"] for row in self.report["packages"]],
                         list(runtime_tests.InputTests.ACTUAL_PACKAGES))
        self.assertEqual(sum(int(row["Size"]) for row in self.report["packages"]), 13960658)

    def test_complete_recorded_graph_provenance_aliases_and_edge_coverage(self):
        self.assertEqual((self.report["result"], self.report["static_graph_closed"]), ("pass", True))
        self.assertNotIn("blocker", self.report)
        graph = self.report["graph"]
        self.assertEqual(graph["resolution_policy"], "unique-explicit-virtual-paths-v1")
        nodes, edges = graph["nodes"], graph["edges"]
        self.assertEqual((len(nodes), len(edges)), (34, 77))
        inventory = self.rows["inventory.json"]
        members, origins = inventory["members"], inventory["origins"]
        archives = {row["Package"]: row["SHA256"] for row in self.report["packages"]}
        for path, node in nodes.items():
            member = members[path]
            self.assertEqual(member["kind"], "file")
            self.assertEqual((member["bytes"], member["sha256"]), (node["bytes"], node["sha256"]))
            self.assertEqual(origins[path], [node["source"]])
            self.assertEqual(node["source"]["archive_sha256"], archives[node["source"]["package"]])
            self.assertEqual(p.tools.resolve(members, node["source"]["archive_member"], ancestors_only=True), path)
        for path, (size, digest) in p.ROOTS.items():
            self.assertEqual((nodes[path]["bytes"], nodes[path]["sha256"]), (size, digest))
        expected, actual = set(), set()
        for path, node in nodes.items():
            expected.update((path, "library", needed) for needed in node["elf"]["needed"])
            if node["elf"]["interpreter"]:
                expected.add((path, "interpreter", node["elf"]["interpreter"]))
        for edge in edges:
            key = edge["from"], edge["kind"], edge["needed"]
            self.assertNotIn(key, actual)
            actual.add(key)
            self.assertIn(edge["to"], nodes)
            directories = [path[1:] for path in nodes[edge["from"]]["elf"]["runpath"]] + list(p.DEFAULT_DIRS)
            candidates = ([p.INTERPRETER] if edge["kind"] == "interpreter"
                          else [directory + "/" + edge["needed"] for directory in directories])
            found = {path: p.tools.resolve(members, path) for path in candidates}
            found = {path: target for path, target in found.items() if target in members}
            self.assertEqual(set(found.values()), {edge["to"]})
            self.assertEqual(edge["matched_paths"], list(found))
            if edge["kind"] == "library":
                self.assertEqual(nodes[edge["to"]]["elf"]["soname"], [edge["needed"]])
        self.assertEqual(actual, expected)
        reachable = set(p.ROOTS)
        while True:
            expanded = reachable | {edge["to"] for edge in edges if edge["from"] in reachable}
            if expanded == reachable:
                break
            reachable = expanded
        self.assertEqual(reachable, set(nodes))
        for name in ("bin", "sbin", "lib", "lib64"):
            self.assertEqual(members[name], {"kind": "link", "mode": 0o777, "target": "usr/" + name})
            self.assertEqual(origins[name][0]["package"], "base-files")

    def test_actual_failure_preserved_and_only_missing_package_added(self):
        failure = self.rows["missing-library.json"]
        self.assertEqual((failure["result"], failure["static_graph_closed"]), ("fail", False))
        self.assertNotIn("graph", failure)
        self.assertEqual(failure["blocker"]["category"], "missing-library")
        self.assertEqual(failure["blocker"]["needed"], "liblzma.so.5")
        self.assertEqual(failure["blocker"]["matches"], [])
        self.assertEqual({row["Package"] for row in self.report["packages"]}
                         - {row["Package"] for row in failure["packages"]}, {"liblzma5"})
        original = self.rows["missing-library-inventory.json"]
        current = self.rows["inventory.json"]
        for name, row in original["members"].items():
            self.assertEqual(current["members"][name], row)
        for name, row in original["origins"].items():
            self.assertEqual(current["origins"][name], row)
        for name in current["origins"].keys() - original["origins"].keys():
            self.assertEqual(current["origins"][name][0]["package"], "liblzma5")


if __name__ == "__main__":
    unittest.main()
