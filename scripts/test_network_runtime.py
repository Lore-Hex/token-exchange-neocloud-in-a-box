# SPDX-License-Identifier: Apache-2.0
"""Synthetic metadata/virtual files and fake children only; no public fetches."""
import copy
import json
import lzma
from pathlib import Path
import subprocess
import tempfile
import time
import types
import unittest
from unittest import mock

ROOT = Path(__file__).absolute().parent.parent


def load(name):
    module = types.ModuleType(name)
    module.__file__ = str(ROOT / "scripts" / (name + ".py"))
    exec(compile(Path(module.__file__).read_bytes(), module.__file__, "exec"), module.__dict__)
    return module


p, fetch = load("prepare_network_runtime"), load("fetch_network_runtime")


def regular(raw):
    return {"kind": "file", "mode": 0o755, "data": raw}


def link(target):
    return {"kind": "link", "mode": 0o777, "target": target}


def metadata(needed=(), soname=(), interpreter="", runpath=()):
    return {"interpreter": interpreter, "needed": list(needed), "soname": list(soname),
            "rpath": [], "runpath": list(runpath)}


class ClosureTests(unittest.TestCase):
    def setUp(self):
        self.tree = {
            "usr/lib/systemd/systemd-networkd": regular(b"networkd"),
            "usr/sbin/nft": regular(b"nft"),
            "usr/lib/x86_64-linux-gnu/systemd/liba.so.1": regular(b"a"),
            "usr/lib/x86_64-linux-gnu/libb.so.1": regular(b"b"),
            "usr/lib/x86_64-linux-gnu/libc.so.6": regular(b"c"),
            "usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2": regular(b"loader"),
            "lib": link("usr/lib"), "lib64": link("usr/lib64"),
            "usr/lib64/ld-linux-x86-64.so.2": link("../lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"),
        }
        self.m = {
            b"networkd": metadata(["liba.so.1", "libc.so.6"], interpreter="/" + p.INTERPRETER, runpath=[p.RUNPATH]),
            b"nft": metadata(["libc.so.6"], interpreter="/" + p.INTERPRETER),
            b"a": metadata(["libb.so.1"], ["liba.so.1"]),
            b"b": metadata(["libc.so.6"], ["libb.so.1"]),
            b"c": metadata(["ld-linux-x86-64.so.2"], ["libc.so.6"], "/" + p.INTERPRETER),
            b"loader": metadata(soname=["ld-linux-x86-64.so.2"]),
        }
        self.origins = {path: [{"package": "synthetic", "archive_sha256": "a" * 64, "archive_member": path}]
                        for path, row in self.tree.items() if row["kind"] != "dir"}
        self.roots = {k: (len(self.tree[k]["data"]), p.base.digest(self.tree[k]["data"])) for k in p.ROOTS}
        self.patch = mock.patch.object(p, "ROOTS", self.roots)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def graph(self, deadline=None):
        return p.closure(self.tree, self.origins, lambda raw: copy.deepcopy(self.m[raw]),
                         deadline=time.monotonic() + 20 if deadline is None else deadline)

    def test_recursive_graph_interpreter_cycles_and_virtual_aliases(self):
        graph = self.graph()
        self.assertEqual(graph, self.graph())
        self.assertEqual(len(graph["nodes"]), 6)
        libc = graph["nodes"]["usr/lib/x86_64-linux-gnu/libc.so.6"]
        self.assertEqual(libc["sha256"], p.base.digest(b"c"))
        self.assertEqual(libc["source"]["archive_sha256"], "a" * 64)
        self.assertTrue(any(e["kind"] == "interpreter" for e in graph["edges"]))
        self.assertTrue(any(len(e["matched_paths"]) == 2 for e in graph["edges"]))

    def test_missing_nested_dependency_and_interpreter_fail_closed(self):
        for path, category in (("usr/lib/x86_64-linux-gnu/libb.so.1", "missing-library"),
                               ("usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2", "missing-interpreter")):
            with self.subTest(path=path):
                row = self.tree.pop(path)
                with self.assertRaises(p.Blocked) as caught: self.graph()
                self.assertEqual(caught.exception.detail["category"], category)
                self.assertEqual(caught.exception.detail["matches"], [])
                self.tree[path] = row

    def test_distinct_candidates_reject_even_with_identical_bytes(self):
        self.tree["usr/lib/libc.so.6"] = regular(b"c")
        with self.assertRaises(p.Blocked) as caught: self.graph()
        self.assertEqual(caught.exception.detail["category"], "ambiguous-library")
        self.assertEqual(len(caught.exception.detail["matches"]), 2)

    def test_parent_runpath_is_not_inherited(self):
        name = "usr/lib/x86_64-linux-gnu/libb.so.1"
        self.tree["usr/lib/x86_64-linux-gnu/systemd/libb.so.1"] = self.tree.pop(name)
        with self.assertRaises(p.Blocked) as caught: self.graph()
        self.assertEqual(caught.exception.detail["needed"], "libb.so.1")
        self.assertFalse(any("/systemd/" in c for c in caught.exception.detail["candidates"]))

    def test_unsupported_search_names_metadata_and_soname(self):
        for field, value in (("needed", ["../libc.so.6"]), ("needed", ["$ORIGIN/x"]),
                             ("needed", ["libc.so.6", "libc.so.6"]), ("needed", ["." ]),
                             ("runpath", ["$ORIGIN"]), ("runpath", ["/tmp"]),
                             ("runpath", [p.RUNPATH + ":/lib"]), ("rpath", ["/lib"]),
                             ("soname", ["wrong.so"]), ("interpreter", "/host/loader"),
                             ("needed", "libc.so.6"), ("needed", ["x"] * 33)):
            with self.subTest(field=field, value=value):
                old = self.m[b"b"][field]
                self.m[b"b"][field] = value
                with self.assertRaises(ValueError): self.graph()
                self.m[b"b"][field] = old

    def test_no_host_escape_cycles_or_special_files(self):
        for row in (link("../../../../outside"), link("libc.so.6"), {"kind": "dir", "mode": 0o755}):
            with self.subTest(row=row):
                name = "usr/lib/x86_64-linux-gnu/libc.so.6"
                old = self.tree[name]
                self.tree[name] = row
                with self.assertRaises(ValueError): self.graph()
                self.tree[name] = old

    def test_loader_overrides_and_ambiguous_provenance_reject(self):
        for name in ("etc/ld.so.cache", "etc/ld.so.preload", "usr/lib/glibc-hwcaps/x86-64-v3/libc.so.6"):
            self.tree[name] = regular(b"override")
            with self.assertRaises(ValueError): self.graph()
            del self.tree[name]
        path = "usr/lib/x86_64-linux-gnu/libc.so.6"
        self.origins[path] *= 2
        with self.assertRaises(ValueError): self.graph()

    def test_root_identity_object_and_deadline_bounds(self):
        with self.assertRaises(ValueError): self.graph(deadline=0)
        with mock.patch.object(p, "MAX_OBJECTS", 3), self.assertRaises(ValueError): self.graph()
        self.tree["usr/sbin/nft"]["data"] = b"substituted"
        with self.assertRaises(ValueError): self.graph()

    def test_no_scope_claims_or_default_activation(self):
        self.assertFalse(any(p.CLAIMS.values()))
        self.assertEqual((ROOT / "guest/network/network.json").read_bytes(), b'{"schema":"nib.guest-network/v1","egress":[]}\n')

    def test_success_and_blocked_receipts_retain_inventory_and_cleanup(self):
        for blocked in (False, True):
            with self.subTest(blocked=blocked), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp).resolve()
                output = directory / "output"
                sources = {k: (ROOT / k).read_bytes() for k in (*p.EXTRA, p.TOOLS)}
                args = ["prepare", "--metadata", str(directory / "metadata"), "--archives", str(directory / "archives"),
                        "--go", str(directory / "go"), "--output", str(output), "--package", "systemd", "--package", "nftables"]
                owned = []
                def compile_tool(go, work, sources):
                    owned.append(work)
                    return work / "not-executed", {"compiler_authenticated": False}
                if blocked: del self.tree["usr/lib/x86_64-linux-gnu/libb.so.1"]
                with mock.patch.object(p.sys, "argv", args), mock.patch.object(p.tools, "committed", return_value=("a" * 40, sources)), \
                     mock.patch.object(p, "metadata_check", return_value=[]), mock.patch.object(p, "compiler", side_effect=compile_tool), \
                     mock.patch.object(p, "assemble", return_value=(self.tree, self.origins)), \
                     mock.patch.object(p, "inspector", return_value=lambda raw: self.m[raw]):
                    self.assertEqual(p.main(), int(blocked))
                report = json.loads((output / "receipt.json").read_bytes())
                self.assertEqual(report["static_graph_closed"], not blocked)
                self.assertTrue(all(report[k] is False for k in p.CLAIMS))
                self.assertEqual(report["inventory_sha256"], p.base.digest((output / "inventory.json").read_bytes()))
                self.assertTrue(owned and all(not d.exists() for d in owned))
                if blocked: self.assertEqual(report["blocker"]["category"], "missing-library")


class InputTests(unittest.TestCase):
    def setUp(self):
        self.lock = json.loads((ROOT / p.LOCK).read_bytes())
        self.names = ["systemd", "nftables", "libc6"]
        self.rows = p.selection(self.lock, self.names)

    def test_selection_stays_in_original_ubuntu_lock(self):
        self.assertEqual({r["Package"] for r in self.rows}, set(self.names))
        for names in (["systemd"], self.names * 2, self.names + ["not-locked"], self.names + ["../lib"],
                      self.names + ["linux-firmware"] * 32):
            with self.subTest(names=names), self.assertRaises(ValueError): p.selection(self.lock, names)
        bad = copy.deepcopy(self.lock)
        next(r for r in bad["packages"] if r["Package"] == "libc6")["index"] = "nvidia"
        with self.assertRaises(ValueError): p.selection(bad, self.names)

    def test_fetch_plan_reuses_exact_urls_sizes_hashes_and_bounds(self):
        rows, items = fetch.plan(self.lock, self.names)
        self.assertEqual(rows, self.rows)
        full = fetch.fetcher.plan(self.lock, guest=True, guest_scope=fetch.fetcher.tools.TARGETS["guest-runtime"][1])
        for row in rows:
            self.assertEqual(items["archives/" + row["SHA256"] + ".deb"], full["repo/pool/" + p.base.package_name(row)])
        self.assertTrue(all("nvidia" not in url for url, _, _ in items.values()))
        self.assertLessEqual(sum(size for _, size, _ in items.values()), 96 << 20)

    def fixture_indexes(self, directory):
        for index in self.lock["indexes"]:
            raw = b""
            for row in self.rows:
                if row["index"] == index["id"]:
                    raw += ("".join(k + ": " + v + "\n" for k, v in row.items() if k != "index" and v) + "\n").encode()
            packed = lzma.compress(raw)
            (directory / index["file"]).write_bytes(packed)
            index.update(bytes=len(packed), sha256=p.base.digest(packed))

    def test_exact_index_rows_hashes_signer_and_nofollow(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp).resolve()
            self.fixture_indexes(directory)
            p.metadata_check(self.lock, self.rows, directory)
            chosen = next(i for i in self.lock["indexes"] if i["id"] == self.rows[0]["index"])
            for key, bad in (("sha256", "0" * 64), ("bytes", 1), ("signer_fingerprint", "0" * 40)):
                old, chosen[key] = chosen[key], bad
                with self.assertRaises(ValueError): p.metadata_check(self.lock, self.rows, directory)
                chosen[key] = old
            rows = copy.deepcopy(self.rows)
            rows[0]["Version"] += ".changed"
            with self.assertRaises(ValueError): p.metadata_check(self.lock, rows, directory)
            path = directory / chosen["file"]
            original = directory / "original"
            path.rename(original)
            path.symlink_to(original)
            with self.assertRaises((OSError, ValueError)): p.metadata_check(self.lock, self.rows, directory)

    def test_hash_gate_precedes_archive_parser_and_source_origins(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp).resolve()
            raw = b"synthetic archive"
            sha = p.base.digest(raw)
            row = {"Package": "synthetic", "SHA256": sha, "Size": str(len(raw))}
            path = directory / (sha + ".deb")
            path.write_bytes(b"modified archive")
            with mock.patch.object(p.base, "controls") as controls, mock.patch.object(p.tools, "payload") as payload:
                with self.assertRaises(ValueError): p.assemble([row], directory, time.monotonic() + 20)
                controls.assert_not_called()
                payload.assert_not_called()
                path.write_bytes(raw)
                payload.return_value = {"lib": link("usr/lib"), "lib/libtest.so": regular(b"test")}
                tree, origins = p.assemble([row], directory, time.monotonic() + 20)
                self.assertIn("usr/lib/libtest.so", tree)
                self.assertEqual(origins["usr/lib/libtest.so"], [{"package": "synthetic", "archive_sha256": sha,
                                                               "archive_member": "lib/libtest.so"}])
                with self.assertRaises(ValueError): p.assemble([row], directory, 0)

    def test_inspector_bounds_anonymous_stdin_and_fixed_environment(self):
        m = metadata(soname=["synthetic.so"])
        def child(args, **kw):
            self.assertEqual(args, ["/private/synthetic-helper"])
            self.assertEqual(kw["stdin"].read(), b"not executed")
            self.assertEqual(kw["max_output_bytes"], 65536)
            self.assertLessEqual(kw["timeout"], 10)
            self.assertNotIn("LD_PRELOAD", kw["env"])
            return subprocess.CompletedProcess(args, 0, json.dumps(m).encode(), b"")
        with mock.patch.object(p.base.processes, "execute_process_group", side_effect=child) as execute:
            inspect = p.inspector(Path("/private/synthetic-helper"), time.monotonic() + 20)
            self.assertEqual(inspect(b"not executed"), m)
            execute.assert_called_once()
            with self.assertRaises(ValueError): inspect(b"x" * ((16 << 20) + 1))
            with self.assertRaises(ValueError): p.inspector(Path("/fake"), 0)(b"x")
        with self.assertRaises(ValueError): json.loads('{"x":1,"x":2}', object_pairs_hook=p.unique)

    def test_executable_pins_match_original_and_prior_reader(self):
        prior = (ROOT / "scripts/prepare_network_proof.py").read_text()
        go = (ROOT / "cmd/nib-network-elf-inputs/main.go").read_text()
        for path, (size, sha) in p.ROOTS.items():
            for value in (path, str(size), sha):
                self.assertIn(value, prior)
                self.assertIn(value, go)

    def test_compiler_only_builds_audited_local_stdlib_closure(self):
        for failure in (None, "version", "dependency", "changed-compiler"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp).resolve()
                work = directory / "work"
                work.mkdir()
                go = directory / "go"
                go.write_bytes(b"synthetic compiler not executed")
                sources = {n: (ROOT / n).read_bytes() for n in p.GO_SOURCES}
                def child(args, **kw):
                    self.assertEqual(args[0], str(go))
                    self.assertEqual(kw["env"]["GOPROXY"], "off")
                    self.assertEqual(kw["env"]["GOTOOLCHAIN"], "local")
                    self.assertEqual(kw["env"]["CGO_ENABLED"], "0")
                    self.assertLessEqual(kw["timeout"], 120)
                    self.assertEqual(kw["max_output_bytes"], 65536)
                    if args[1] == "env": out = b"go1.0\n" if failure == "version" else b"go1.27.2\n"
                    elif args[1] == "list":
                        prefix = b"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/"
                        out = b"unknown/package" if failure == "dependency" else prefix + b"internal/elfmeta\n" + prefix + b"cmd/nib-elf-metadata\n"
                    else:
                        self.assertEqual(args[1], "build")
                        Path(args[args.index("-o") + 1]).write_bytes(b"synthetic helper not executed")
                        if failure == "changed-compiler": go.write_bytes(b"changed")
                        out = b""
                    return subprocess.CompletedProcess(args, 0, out, b"")
                with mock.patch.object(p.base.processes, "execute_process_group", side_effect=child):
                    if failure:
                        with self.assertRaises(ValueError): p.compiler(go, work, sources)
                    else:
                        binary, report = p.compiler(go, work, sources)
                        self.assertEqual(binary, work / "metadata")
                        self.assertFalse(report["compiler_authenticated"])
                        self.assertEqual(report["helper_sha256"], p.base.digest(binary.read_bytes()))

    def test_fetch_failure_is_retained_without_verified_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp).resolve() / "output"
            sources = {fetch.prep.LOCK: (ROOT / p.LOCK).read_bytes(), fetch.prep.SCRIPT: fetch.PREP_SOURCE}
            error = fetch.fetcher.FetchFailure("metadata/noble-updates-main.xz", 1397104,
                "a398e4efccd241c15ea24a92e1aedfbb853f9e9100e73de9f6bc6f1d5b2581fa", "process-exit", 22)
            args = ["fetch", "--output", str(output), "--package", "systemd", "--package", "nftables"]
            with mock.patch.object(fetch.sys, "argv", args), \
                 mock.patch.object(fetch.fetcher, "committed", return_value=("a" * 40, sources)), \
                 mock.patch.object(fetch.fetcher, "fetch_parallel", side_effect=error):
                self.assertEqual(fetch.main(), 1)
            report = json.loads((output / "receipt.json").read_bytes())
            self.assertEqual(report["result"], "fail")
            self.assertEqual(report["failure"], error.report)
            self.assertFalse(report["static_graph_closed"])
            self.assertEqual({f.name for f in output.iterdir()}, {"receipt.json"})

    def test_failed_preparation_is_private_immutable_and_never_success(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp).resolve()
            out = directory / "output"
            sources = {k: (ROOT / k).read_bytes() for k in (*p.EXTRA, p.TOOLS)}
            args = ["prepare", "--metadata", str(directory / "metadata"), "--archives", str(directory / "archives"),
                    "--go", str(directory / "go"), "--output", str(out)]
            for name in self.names: args += ["--package", name]
            with mock.patch.object(p.sys, "argv", args), mock.patch.object(p.tools, "committed", return_value=("a" * 40, sources)), \
                 mock.patch.object(p, "metadata_check", side_effect=ValueError("DO NOT LOG")), \
                 mock.patch.object(p, "compiler") as compile_tool:
                self.assertEqual(p.main(), 1)
                compile_tool.assert_not_called()
                raw = (out / "receipt.json").read_bytes()
                report = json.loads(raw)
                self.assertNotIn(b"DO NOT LOG", raw)
                self.assertFalse(report["static_graph_closed"])
                self.assertEqual(report["result"], "fail")
                self.assertTrue(all(report[k] is False for k in p.CLAIMS))
                self.assertEqual((out / "receipt.json").stat().st_mode & 0o777, 0o600)
                with self.assertRaises(FileExistsError): p.main()


if __name__ == "__main__":
    unittest.main()
