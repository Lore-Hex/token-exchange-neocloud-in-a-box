# SPDX-License-Identifier: Apache-2.0
"""Synthetic fetch/materialization/lifecycle checks; no network or namespaces."""
import ast
import copy
import contextlib
import gzip
import io
import json
from pathlib import Path
import runpy
import subprocess
import tarfile
import tempfile
import types
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
host = types.SimpleNamespace(**runpy.run_path(str(ROOT / "host/probe-mkosi-native-tools.py"), run_name="test_native_tools"))
fetcher, tools, base = host.fetcher, host.tools, host.base
ORIGINAL = [name + ":[100]" for name in ("user", "mnt", "net", "ipc")]


def lock():
    return {"scope": "mkosi-build-tools-dependency-coverage", "archive_bytes": 3,
            "indexes": [{"id": "noble-main", "file": "noble-main.xz", "encoding": "xz",
                "base_url": "https://archive.ubuntu.com/ubuntu/", "bytes": 3,
                "sha256": base.digest(b"idx"), "index_path": "main/binary-amd64/Packages.xz",
                "inrelease_url": "https://archive.ubuntu.com/ubuntu/dists/noble/InRelease"}],
            "packages": [{"Package": "synthetic", "index": "noble-main", "Size": "3",
                "SHA256": base.digest(b"deb"), "Filename": "pool/main/s/synthetic/synthetic_1_amd64.deb"}]}


def files(tree=None):
    tree = copy.deepcopy(tree or {"usr/bin/tool": {"kind": "file", "mode": 0o755, "data": b"synthetic"},
                                 "bin": {"kind": "link", "mode": 0o777, "target": "usr/bin"}})
    raw = tools.tar_bytes(tree)
    return {"tools-root.tar": raw, "root-inventory.json": base.encoded(tools.observation(tree))}


class FetchTests(unittest.TestCase):
    def guest(self):
        value = lock()
        value["scope"] = fetcher.GUEST_SCOPE
        value["indexes"].append({"id": "nvidia", "encoding": "plain", "file": "nvidia.packages",
            "index_path": "Packages", "base_url": fetcher.NVIDIA_BASE,
            "inrelease_url": fetcher.NVIDIA_BASE + "InRelease", "bytes": 3,
            "sha256": base.digest(b"gpu")})
        value["packages"].append({"Package": "nvidia-synthetic", "index": "nvidia", "Size": "3",
            "SHA256": base.digest(b"drv"), "Filename": "./nvidia-synthetic_1_amd64.deb"})
        value["archive_bytes"] = 6
        return value

    def test_guest_union_is_exact_and_reuses_shared_inputs(self):
        tools_lock = json.loads((ROOT / fetcher.prep.LOCK).read_bytes())
        guest = json.loads((ROOT / fetcher.tools.FILES[-1]).read_bytes())
        items = fetcher.build_plan(tools_lock, guest)
        archives = {name: row for name, row in items.items() if name.startswith("archives/")}
        self.assertEqual(len(archives), 269)
        self.assertEqual(sum(row[1] for row in archives.values()), 516283494)
        self.assertEqual(len(items), 276)
        self.assertTrue(all(name == "archives/" + row[2] + ".deb" for name, row in archives.items()))
        self.assertEqual(items["metadata/nvidia.packages"][0], fetcher.NVIDIA_BASE + "Packages")
        self.assertGreater(max(row[1] for row in archives.values()), 32 << 20)
        self.assertLessEqual(max(row[1] for row in archives.values()), base.MAX_ARCHIVE)
        with self.assertRaises(ValueError):
            fetcher.plan(guest)

    def test_guest_fetch_deduplicates_and_keeps_all_hash_gates(self):
        guest = self.guest()
        responses = {base.digest(raw): raw for raw in (b"idx", b"deb", b"gpu", b"drv")}
        items = fetcher.build_plan(lock(), guest)
        by_url = {url: responses[sha] for url, _, sha in items.values()}
        execute = mock.Mock(side_effect=lambda argv, **kw:
                            subprocess.CompletedProcess(argv, 0, by_url[argv[-1]], b""))
        files = fetcher.fetch(base.encoded(lock()), execute, clock=lambda: 0, guest_raw=base.encoded(guest))
        self.assertEqual(execute.call_count, 4)
        self.assertEqual(files["archives/" + base.digest(b"drv") + ".deb"], b"drv")
        self.assertEqual(files["guest-inputs.json"], base.encoded(guest))
        self.assertEqual(files["metadata/nvidia.packages"], b"gpu")
        by_url[fetcher.NVIDIA_BASE + "Packages"] = b"NEW"
        with self.assertRaises(ValueError):
            fetcher.fetch(base.encoded(lock()), execute, clock=lambda: 0, guest_raw=base.encoded(guest))

    def test_guest_rejects_mirrors_traversal_conflicts_and_larger_inputs(self):
        mutations = (
            lambda g: g["indexes"][-1].update(base_url="https://evil.test/"),
            lambda g: g["indexes"][-1].update(index_path="../Packages"),
            lambda g: g["indexes"][-1].update(encoding="xz"),
            lambda g: g["indexes"][-1].update(file="../../Packages"),
            lambda g: g["indexes"][0].update(sha256="1" * 64),
            lambda g: g["packages"][0].update(Version="different"),
            lambda g: g["packages"][-1].update(Filename="./../bad.deb"),
            lambda g: g["packages"][-1].update(Filename="./bad.deb?key=private"),
            lambda g: g["packages"][-1].update(Filename="pool/driver.deb"),
            lambda g: g["indexes"][-1].update(bytes=(32 << 20) + 1),
        )
        for mutate in mutations:
            guest = self.guest()
            mutate(guest)
            with self.subTest(guest=guest), self.assertRaises(ValueError):
                fetcher.build_plan(lock(), guest)
        guest = self.guest()
        guest["packages"][-1]["Size"] = str((128 << 20) + 1)
        guest["archive_bytes"] = (128 << 20) + 4
        with self.assertRaises(ValueError):
            fetcher.build_plan(lock(), guest)
        for url in (fetcher.NVIDIA_BASE + "../secret", fetcher.NVIDIA_BASE + "..",
                    fetcher.NVIDIA_BASE + "%2e", fetcher.NVIDIA_BASE + "Packages?token=private",
                    "https://user@developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/Packages"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                fetcher.public_url(url, nvidia=True)

    def test_real_committed_plan_has_exact_bounded_inputs(self):
        value = json.loads((ROOT / fetcher.prep.LOCK).read_bytes())
        items = fetcher.plan(value)
        self.assertEqual(len(items), 209)
        self.assertEqual(len([p for p in items if p.startswith("repo/pool/")]), 203)
        for name, (url, _, sha) in items.items():
            self.assertTrue(url.startswith(("https://archive.ubuntu.com/", "https://security.ubuntu.com/")))
            if name.startswith("metadata/"):
                self.assertTrue(url.endswith("/by-hash/SHA256/" + sha))

    def test_archived_metadata_is_verified_before_any_network_and_not_refetched(self):
        execute = mock.Mock(return_value=subprocess.CompletedProcess([], 0, b"deb", b""))
        metadata = {"metadata/noble-main.xz": b"idx"}
        result = fetcher.fetch(base.encoded(lock()), execute, clock=lambda: 0, metadata=metadata)
        self.assertEqual(result["metadata/noble-main.xz"], b"idx")
        self.assertEqual(execute.call_count, 1)
        self.assertTrue(execute.call_args.args[0][-1].endswith(".deb"))
        for bad in ({}, {**metadata, "extra": b"x"}, {"metadata/noble-main.xz": b"new"},
                    {"metadata/noble-main.xz": b"id"}, {"metadata/noble-main.xz": "idx"}):
            execute.reset_mock()
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                fetcher.fetch(base.encoded(lock()), execute, clock=lambda: 0, metadata=bad)
            execute.assert_not_called()

    def test_metadata_archive_roundtrip_is_deterministic_and_exact(self):
        items = fetcher.build_plan(lock(), self.guest())
        metadata = {"metadata/noble-main.xz": b"idx", "metadata/nvidia.packages": b"gpu"}
        raw = fetcher.archive_metadata(metadata, items)
        self.assertEqual(raw, fetcher.archive_metadata(metadata, items))
        self.assertEqual(fetcher.unpack_metadata(raw, items), metadata)
        with self.assertRaises(ValueError):
            fetcher.archive_metadata({**metadata, "unexpected": b"data"}, items)

    def test_metadata_archive_rejects_links_duplicates_paths_and_changed_bytes(self):
        items = fetcher.plan(lock())
        def make(kind):
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                entry = tarfile.TarInfo("noble-main.xz")
                entry.size, entry.mode, entry.mtime = 3, 0o444, tools.EPOCH
                raw = b"idx"
                if kind == "symlink":
                    entry.type, entry.size, entry.linkname = tarfile.SYMTYPE, 0, "/private"
                if kind == "path": entry.name = "../private"
                if kind == "hash": raw = b"bad"
                if kind == "mode": entry.mode = 0o777
                if kind == "owner": entry.uid = 1
                archive.addfile(entry, io.BytesIO(raw))
                if kind == "duplicate": archive.addfile(entry, io.BytesIO(raw))
            return gzip.compress(stream.getvalue(), mtime=0)
        for kind in ("symlink", "path", "hash", "mode", "owner", "duplicate"):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                fetcher.unpack_metadata(make(kind), items)
        raw = fetcher.archive_metadata({"metadata/noble-main.xz": b"idx"}, items)
        with mock.patch.dict(fetcher.unpack_metadata.__globals__, {"MAX_METADATA": 1}), self.assertRaises(ValueError):
            fetcher.unpack_metadata(raw, items)

    def test_fetch_is_exact_content_bounded_and_scrubs_environment(self):
        raw = base.encoded(lock())
        responses = [b"idx", b"deb"]
        def execute(argv, **kw):
            self.assertEqual(argv[:4], ["/usr/bin/curl", "--disable", "--noproxy", "*"])
            self.assertNotIn("--location", argv)
            self.assertNotIn("--retry", argv)
            self.assertEqual(argv[argv.index("--max-filesize") + 1], "3")
            self.assertEqual(kw, {"env": fetcher.ENV, "timeout": 60,
                "max_output_bytes": 65539, "stdin": subprocess.DEVNULL})
            return subprocess.CompletedProcess(argv, 0, responses.pop(0), b"")
        result = fetcher.fetch(raw, execute, clock=lambda: 0)
        self.assertEqual(result["mkosi-tools-inputs.json"], raw)
        self.assertEqual(result["metadata/noble-main.xz"], b"idx")
        self.assertEqual(result["repo/pool/synthetic_" + base.digest(b"deb") + ".deb"], b"deb")

    def test_wrong_hash_length_stderr_and_http_failure_reject(self):
        for output, error, code in ((b"bad", b"", 0), (b"id", b"", 0),
                                     (b"idx", b"private", 0), (b"idx", b"", 22)):
            execute = mock.Mock(return_value=subprocess.CompletedProcess([], code, output, error))
            with self.subTest(output=output, error=error, code=code), \
                    self.assertRaises((ValueError, subprocess.SubprocessError)):
                fetcher.fetch(base.encoded(lock()), execute, clock=lambda: 0)
            self.assertEqual(execute.call_count, 1)

    def test_total_deadline_does_not_reset_on_each_fetch(self):
        execute = mock.Mock(return_value=subprocess.CompletedProcess([], 0, b"idx", b""))
        clock = mock.Mock(side_effect=[0, 590, 601])
        with self.assertRaises(ValueError):
            fetcher.fetch(base.encoded(lock()), execute, clock=clock)
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(execute.call_args.kwargs["timeout"], 10)

    def test_fetch_failure_reports_only_fixed_category_and_public_lock_identity(self):
        cases = ((b"bad", b"", 0, "digest"), (b"id", b"", 0, "size"),
                 (b"idx", b"private response", 0, "stderr"), (b"private", b"private", 22, "process-exit"),
                 (b"idx", b"", True, "process-rejected"), (b"idx", b"", 256, "process-rejected"))
        for output, stderr, code, category in cases:
            with self.subTest(category=category):
                execute = mock.Mock(return_value=subprocess.CompletedProcess([], code, output, stderr))
                with self.assertRaises(fetcher.FetchFailure) as caught:
                    fetcher.fetch(base.encoded(lock()), execute, clock=lambda: 0)
                report = caught.exception.report
                self.assertEqual(report, {"schema": "nib.input-fetch-failure/v1", "result": "fail",
                    "input_kind": "metadata", "expected_sha256": base.digest(b"idx"), "expected_bytes": 3,
                    "failure": category, "returncode": 22 if category == "process-exit" else None,
                    "package_execution": False})
                self.assertNotIn("private", json.dumps(report) + str(caught.exception))
                execute.assert_called_once()

    def test_fetch_exception_diagnostics_do_not_include_exception_text_or_output(self):
        cases = ((subprocess.TimeoutExpired("private command", 1, b"private output"), "timeout"),
                 (OSError("private path"), "io"), (ValueError("private message"), "process-rejected"),
                 (subprocess.CalledProcessError(22, "private command", b"private"), "process-rejected"),
                 (base.processes.OutputLimitExceeded("stdout", b"private", b"private"), "output-limit"))
        for error, category in cases:
            with self.subTest(category=category):
                execute = mock.Mock(side_effect=error)
                output, sleep = io.StringIO(), mock.Mock()
                with contextlib.redirect_stdout(output), self.assertRaises(fetcher.FetchFailure) as caught:
                    fetcher.fetch(base.encoded(lock()), execute, clock=lambda: 0,
                                  sleep=sleep, metadata={"metadata/noble-main.xz": b"idx"})
                report = caught.exception.report
                self.assertEqual(report["failure"], category)
                self.assertEqual(report["input_kind"], "archive")
                self.assertEqual(report["expected_sha256"], base.digest(b"deb"))
                self.assertNotIn("private", json.dumps(report) + str(caught.exception) + output.getvalue())
                self.assertEqual(execute.call_count, 3 if category == "timeout" else 1)
                self.assertEqual(sleep.call_count, 2 if category == "timeout" else 0)

    def test_transient_fetch_retries_only_exact_input_and_revalidates_bytes(self):
        for code in fetcher.TRANSIENT_CODES:
            with self.subTest(code=code):
                execute = mock.Mock(side_effect=[
                    subprocess.CompletedProcess([], code, b"private partial bytes", b"private host"),
                    subprocess.CompletedProcess([], 0, b"idx", b""),
                    subprocess.CompletedProcess([], 0, b"deb", b""),
                ])
                output, sleep = io.StringIO(), mock.Mock()
                with contextlib.redirect_stdout(output):
                    result = fetcher.fetch(base.encoded(lock()), execute, clock=lambda: 0, sleep=sleep)
                self.assertEqual(result["metadata/noble-main.xz"], b"idx")
                self.assertEqual(execute.call_count, 3)
                self.assertEqual(execute.call_args_list[0], execute.call_args_list[1])
                sleep.assert_called_once_with(1)
                event = json.loads(output.getvalue())
                self.assertEqual(event, {"schema": "nib.input-fetch-retry/v1", "result": "retry",
                    "input_kind": "metadata", "expected_sha256": base.digest(b"idx"), "expected_bytes": 3,
                    "failure": "process-exit", "returncode": code, "package_execution": False,
                    "failed_attempt": 1, "delay_seconds": 1})
                self.assertNotIn("private", output.getvalue())

    def test_transient_fetch_has_three_attempts_and_exponential_backoff(self):
        execute = mock.Mock(return_value=subprocess.CompletedProcess([], 28, b"private", b"private"))
        output, sleep = io.StringIO(), mock.Mock()
        with contextlib.redirect_stdout(output), self.assertRaises(fetcher.FetchFailure) as caught:
            fetcher.fetch(base.encoded(lock()), execute, clock=lambda: 0, sleep=sleep)
        self.assertEqual(execute.call_count, 3)
        self.assertEqual(sleep.call_args_list, [mock.call(1), mock.call(2)])
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([row["failed_attempt"] for row in events], [1, 2])
        self.assertEqual(caught.exception.report["returncode"], 28)
        self.assertNotIn("private", output.getvalue())

    def test_retry_does_not_hide_integrity_failure_or_tls_rejection(self):
        for code, data, category in ((0, b"bad", "digest"), (0, b"id", "size"),
                                     (60, b"", "process-exit"), (63, b"", "process-exit"),
                                     (22, b"", "process-exit"), (35, b"", "process-exit")):
            with self.subTest(code=code, category=category):
                execute = mock.Mock(side_effect=[subprocess.CompletedProcess([], 28, b"", b""),
                                                subprocess.CompletedProcess([], code, data, b"")])
                sleep = mock.Mock()
                with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(fetcher.FetchFailure) as caught:
                    fetcher.fetch(base.encoded(lock()), execute, clock=lambda: 0, sleep=sleep)
                self.assertEqual(execute.call_count, 2)
                sleep.assert_called_once_with(1)
                self.assertEqual(caught.exception.report["failure"], category)

    def test_retry_cannot_extend_global_deadline_or_sleep_past_it(self):
        for times, calls, sleeps in (([0, 599, 599], 1, 0),
                                     ([0, 590, 598, 601], 1, 1),
                                     ([0, 590, 591, 592, 601], 2, 1)):
            with self.subTest(times=times):
                execute = mock.Mock(return_value=subprocess.CompletedProcess([], 28, b"", b""))
                sleep = mock.Mock()
                with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(fetcher.FetchFailure) as caught:
                    fetcher.fetch(base.encoded(lock()), execute, clock=mock.Mock(side_effect=times), sleep=sleep)
                self.assertEqual(caught.exception.report["failure"], "deadline")
                self.assertEqual(execute.call_count, calls)
                self.assertEqual(sleep.call_count, sleeps)
                self.assertEqual(execute.call_args_list[0].kwargs["timeout"], min(60, 600 - times[1]))

    def test_timeout_recovery_still_hash_checks_full_response(self):
        execute = mock.Mock(side_effect=[subprocess.TimeoutExpired("private", 60, b"private"),
                                        subprocess.CompletedProcess([], 0, b"deb", b"")])
        sleep = mock.Mock()
        with contextlib.redirect_stdout(io.StringIO()):
            result = fetcher.fetch(base.encoded(lock()), execute, clock=lambda: 0, sleep=sleep,
                                   metadata={"metadata/noble-main.xz": b"idx"})
        self.assertIn(b"deb", result.values())
        self.assertEqual(execute.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_expired_fetch_budget_never_starts_network(self):
        execute = mock.Mock()
        with self.assertRaises(fetcher.FetchFailure) as caught:
            fetcher.fetch(base.encoded(lock()), execute, clock=mock.Mock(side_effect=[0, 601]))
        self.assertEqual(caught.exception.report["failure"], "deadline")
        execute.assert_not_called()

    def test_no_untrusted_network_destinations_or_paths(self):
        for url in ("http://archive.ubuntu.com/ubuntu/a", "https://evil.test/ubuntu/a",
                    "https://user@archive.ubuntu.com/ubuntu/a", "https://archive.ubuntu.com:443/ubuntu/a",
                    "https://archive.ubuntu.com/ubuntu/../a", "https://archive.ubuntu.com/ubuntu/%2e/a",
                    "https://archive.ubuntu.com/ubuntu/a?x=1", "https://archive.ubuntu.com/ubuntu/a#x"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                fetcher.public_url(url)

    def test_duplicate_oversized_and_nonhash_inputs_reject(self):
        candidates = []
        value = lock(); value["packages"] *= 2; candidates.append(value)
        value = lock(); value["indexes"] *= 2; candidates.append(value)
        value = lock(); value["indexes"][0]["bytes"] = 33 << 20; candidates.append(value)
        value = lock(); value["packages"][0]["SHA256"] = "x" * 64; candidates.append(value)
        value = lock(); value["archive_bytes"] = 4; candidates.append(value)
        value = lock(); value["packages"][0]["Filename"] = "pool/../../bad"; candidates.append(value)
        value = lock(); value["packages"][0]["Package"] = "../../bad"; candidates.append(value)
        value = lock(); value["packages"][0]["Size"] = True; candidates.append(value)
        for value in candidates:
            with self.subTest(value=value), self.assertRaises(ValueError):
                fetcher.plan(value)


class MaterializationTests(unittest.TestCase):
    def test_files_written_before_links_without_owner_escalation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            host.materialize(files(), root)
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            self.assertEqual((root / "usr/bin/tool").read_bytes(), b"synthetic")
            self.assertEqual((root / "usr/bin/tool").stat().st_mode & 0o7777, 0o755)
            self.assertTrue((root / "bin").is_symlink())
            self.assertTrue((root / "proc/self/ns").is_dir())

    def test_absolute_link_is_created_but_never_traversed(self):
        value = files({"usr/share/data": {"kind": "file", "mode": 0o444, "data": b"synthetic"},
                       "link": {"kind": "link", "mode": 0o777, "target": "/usr/share/data"}})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            host.materialize(value, root)
            self.assertEqual((root / "link").readlink(), Path("/usr/share/data"))
            self.assertEqual((root / "usr/share/data").read_bytes(), b"synthetic")

    def test_changed_inventory_and_tar_cannot_materialize(self):
        for name in ("tools-root.tar", "root-inventory.json"):
            value = files()
            value[name] = value[name].replace(b"synthetic", b"corrupted", 1) if name.endswith("tar") else b"{}"
            with tempfile.TemporaryDirectory() as temporary, self.assertRaises(ValueError):
                host.materialize(value, Path(temporary) / "root")

    def test_existing_destination_or_symlink_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            root.symlink_to(Path(temporary) / "absent")
            with self.assertRaises(ValueError):
                host.materialize(files(), root)
            self.assertTrue(root.is_symlink())

    def test_unsafe_tar_types_and_paths_fail_before_publication(self):
        for name, kind in (("../escape", tarfile.REGTYPE), ("/escape", tarfile.REGTYPE),
                           ("device", tarfile.CHRTYPE), ("hard", tarfile.LNKTYPE)):
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w") as archive:
                row = tarfile.TarInfo(name)
                row.type, row.mtime, row.mode = kind, tools.EPOCH, 0o644
                archive.addfile(row)
            value = {"tools-root.tar": stream.getvalue(), "root-inventory.json": base.encoded({name: {}})}
            with tempfile.TemporaryDirectory() as temporary, self.assertRaises(ValueError):
                host.materialize(value, Path(temporary) / "root")

    def test_reserved_mountpoint_cannot_traverse_a_package_link(self):
        value = files({"proc": {"kind": "link", "mode": 0o777, "target": "/outside"}})
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(ValueError):
            host.materialize(value, Path(temporary) / "root")


class LifecycleTests(unittest.TestCase):
    def run_probe(self, *, stdout=host.SUCCESS, stderr=b"", code=0, error=None, pid_isolation=False,
                  mapped_ownership=False, final_confinement=False):
        execute = mock.Mock(side_effect=error, return_value=subprocess.CompletedProcess([], code, stdout, stderr))
        real_readlink = host.os.readlink
        def readlink(path, *args, **kwargs):
            names = ("user", "mnt", "net", "ipc", "pid")
            for name, original in zip(names, [*ORIGINAL, "pid:[100]"]):
                if str(path) == "/proc/self/ns/" + name:
                    return original
            return real_readlink(path, *args, **kwargs)
        with mock.patch.object(host.os, "readlink", side_effect=readlink):
            report = host.run(files(), {"mkosi-sandbox.py": b"synthetic"}, execute,
                              pid_isolation=pid_isolation, mapped_ownership=mapped_ownership,
                              final_confinement=final_confinement)
        return report, execute

    def test_pinned_root_replaces_host_runtime_and_cleanup_passes(self):
        report, execute = self.run_probe()
        argv = execute.call_args.args[0]
        self.assertEqual(argv[0], "/usr/bin/python3.10")
        self.assertEqual(argv[5], "--ro-bind")
        self.assertEqual(argv[7], "/")
        self.assertNotIn("/usr", argv)
        self.assertNotIn("/proc", argv)
        self.assertEqual(argv[-9:], ["/usr/bin/python3.12", "-I", "-S", "-B", "/nib/native-tools.py", *ORIGINAL])
        self.assertFalse(Path(argv[6]).exists())
        self.assertTrue(report["payload_removed"])
        self.assertTrue(report["process_cleanup_confirmed"])
        self.assertEqual(report["tools_checked"], 7)
        self.assertEqual(report["result"], "pass")
        for key in ("package_installation_executed", "maintainer_scripts_executed",
                    "mkosi_build_executed", "abi_validated", "bootable", "deployable"):
            self.assertIs(report[key], False)
        self.assertEqual(execute.call_args.kwargs, {"env": host.native.worker.ENV,
            "timeout": 90, "max_output_bytes": 65536, "stdin": subprocess.DEVNULL})

    def test_no_partial_success_or_diagnostic_leaks(self):
        for options in ({"stdout": b"wrong"}, {"stderr": b"PRIVATE"}, {"code": 1},
                        {"error": subprocess.TimeoutExpired(["PRIVATE"], 90)}, {"error": OSError("PRIVATE")}):
            report, _ = self.run_probe(**options)
            self.assertEqual(report["result"], "fail")
            self.assertFalse(report["namespaces_validated"])
            self.assertEqual(report["tools_checked"], 0)
            self.assertTrue(report["payload_removed"])
            self.assertNotIn("PRIVATE", json.dumps(report))

    def test_pid_mode_uses_pinned_unshare_and_kills_descendants(self):
        report, execute = self.run_probe(pid_isolation=True)
        argv = execute.call_args.args[0]
        offset = argv.index("/usr/bin/unshare")
        proc = argv.index("/proc")
        self.assertEqual(argv[proc - 1:proc + 2], ["--ro-bind", "/proc", "/proc"])
        self.assertNotIn("/proc/self/ns", argv)
        self.assertLess(proc, offset)
        self.assertEqual(argv[offset:offset + 6], ["/usr/bin/unshare", "--pid", "--fork",
            "--mount-proc=/proc", "--kill-child=KILL", "--"])
        self.assertEqual(argv[-5:], [*ORIGINAL, "pid:[100]"])
        self.assertEqual(report["schema"], "nib.mkosi-native-tools/v2")
        self.assertTrue(report["pid_namespace_validated"])
        self.assertTrue(report["host_processes_hidden"])
        self.assertTrue(report["process_cleanup_confirmed"])
        self.assertFalse(report["package_installation_executed"])

    def test_pid_failure_never_claims_process_isolation(self):
        for options in ({"code": 1}, {"stdout": b"wrong"}, {"error": OSError("synthetic")}):
            report, _ = self.run_probe(pid_isolation=True, **options)
            self.assertEqual(report["result"], "fail")
            self.assertFalse(report["pid_namespace_validated"])
            self.assertFalse(report["host_processes_hidden"])
            self.assertTrue(report["payload_removed"])

    def test_mapped_mode_uses_pinned_loader_and_existing_helper_contract(self):
        expected = {"uid": 1001, "gid": 1001, "subuid": 100000, "subgid": 200000}
        hashes = {"/usr/bin/newuidmap": "a" * 64, "/usr/bin/newgidmap": "b" * 64}
        with mock.patch.object(host.ownership, "host_inputs", return_value=(expected, hashes)):
            report, execute = self.run_probe(pid_isolation=True, mapped_ownership=True)
        argv = execute.call_args.args[0]
        self.assertTrue(argv[0].endswith("/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"))
        self.assertEqual(argv[1], "--inhibit-cache")
        self.assertIn("--map-users=1:100000:65535", argv)
        self.assertIn("--map-groups=1:200000:65535", argv)
        self.assertEqual(argv[-1], "mapped-ownership")
        self.assertNotIn("--suppress-chown", argv)
        self.assertEqual(report["schema"], "nib.mkosi-native-tools/v3")
        self.assertTrue(report["mapped_ownership_validated"])
        self.assertEqual(report["host_mapping_helpers_sha256"], hashes)
        self.assertNotIn("100000", json.dumps(report))
        self.assertTrue(report["payload_removed"])

    def test_missing_mapping_rejects_before_any_tool(self):
        with mock.patch.object(host.ownership, "host_inputs", side_effect=ValueError("PRIVATE")):
            report, execute = self.run_probe(pid_isolation=True, mapped_ownership=True)
        execute.assert_not_called()
        self.assertEqual(report["result"], "fail")
        self.assertEqual(report["stage"], "host-subordinate-mapping")
        self.assertFalse(report["mapped_ownership_validated"])
        self.assertTrue(report["payload_removed"])
        self.assertNotIn("PRIVATE", json.dumps(report))

    def test_mapped_ownership_requires_pid_and_boolean(self):
        for options in ({"mapped_ownership": True}, {"mapped_ownership": 1, "pid_isolation": True}):
            with self.assertRaises(ValueError):
                host.run({}, {}, mock.Mock(), **options)

    def test_final_boundary_requires_mapping_and_drops_caps_after_namespace_setup(self):
        mapped = {"uid": 1001, "gid": 1001, "subuid": 100000, "subgid": 200000}
        with mock.patch.object(host.ownership, "host_inputs", return_value=(mapped, {})):
            report, execute = self.run_probe(pid_isolation=True, mapped_ownership=True, final_confinement=True)
        argv = execute.call_args.args[0]
        drop = argv.index("/usr/bin/setpriv")
        self.assertEqual(argv[drop:drop + len(host.confinement.PREFIX)], list(host.confinement.PREFIX))
        self.assertGreater(drop, argv.index("--mount-proc=/proc"))
        self.assertEqual(argv[-2:], ["mapped-ownership", "final-confinement"])
        self.assertEqual(report["schema"], "nib.mkosi-native-tools/v4")
        self.assertTrue(report["final_confinement_validated"])
        self.assertFalse(report["package_installation_executed"])
        for options in ({"final_confinement": True}, {"final_confinement": True, "pid_isolation": True},
                        {"final_confinement": 1, "mapped_ownership": True, "pid_isolation": True}):
            with self.assertRaises(ValueError):
                host.run({}, {}, mock.Mock(), **options)

    def test_failed_final_boundary_never_promotes(self):
        mapped = {"uid": 1001, "gid": 1001, "subuid": 100000, "subgid": 200000}
        with mock.patch.object(host.ownership, "host_inputs", return_value=(mapped, {})):
            result, _ = self.run_probe(pid_isolation=True, mapped_ownership=True, final_confinement=True,
                                      code=1, stderr=b"NIB_NATIVE_TOOLS_REJECTED:confinement\n")
        self.assertEqual(result["result"], "fail")
        self.assertEqual(result["failure_kind"], "worker-confinement")
        self.assertFalse(result["final_confinement_validated"])

    def test_failure_classes_never_persist_process_output(self):
        cases = {
            b"unshare: mount PRIVATE /proc failed: Operation not permitted\n": "unshare-permission-denied",
            b"unshare: PRIVATE: No such file or directory\n": "unshare-missing-path",
            b"unshare: PRIVATE: Invalid argument\n": "unshare-invalid-argument",
            b"unshare: PRIVATE\n": "unshare-exit",
            b"PRIVATE\n": "process-exit",
            b"NIB_NATIVE_TOOLS_REJECTED:pid\nPRIVATE\n": "process-exit",
        }
        cases.update({("NIB_NATIVE_TOOLS_REJECTED:" + stage + "\n").encode(): "worker-" + stage
                      for stage in host.FAILURE_STAGES})
        for stderr, expected in cases.items():
            with self.subTest(expected=expected):
                report, _ = self.run_probe(code=1, stderr=stderr, pid_isolation=True)
                self.assertEqual(report["failure_kind"], expected)
                self.assertEqual(report["result"], "fail")
                self.assertFalse(report["pid_namespace_validated"])
                self.assertTrue(report["payload_removed"])
                self.assertNotIn("PRIVATE", json.dumps(report))
        self.assertEqual(host.failure_kind(subprocess.TimeoutExpired("PRIVATE", 1)), "timeout")
        self.assertEqual(host.failure_kind(OSError("PRIVATE")), "startup-error")
        self.assertEqual(host.failure_kind(ValueError("PRIVATE")), "invalid-result-or-cleanup")

    def test_pid_mode_requires_an_actual_boolean(self):
        for value in ("true", 1, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                host.run({}, {}, mock.Mock(), pid_isolation=value)

    def test_materialization_failure_never_executes(self):
        execute = mock.Mock()
        report = host.run({"tools-root.tar": b"bad", "root-inventory.json": b"{}"}, {}, execute)
        execute.assert_not_called()
        self.assertEqual(report["stage"], "materialization")
        self.assertTrue(report["payload_removed"])

    def test_syntax_and_workflow_restrictions(self):
        for path in (host.SCRIPT, host.FETCHER):
            ast.parse((ROOT / path).read_bytes(), feature_version=(3, 10))
        workflow = (ROOT / ".github/workflows/mkosi-native-tools.yml").read_text()
        for forbidden in ("sudo", "secrets.", "sysctl", "--privileged", "apt-get", "pip install"):
            self.assertNotIn(forbidden, workflow)
        value = yaml.safe_load(workflow)
        self.assertEqual(value["jobs"]["probe"]["runs-on"], "ubuntu-22.04")
        self.assertEqual(value["permissions"], {"contents": "read"})


class ChildTests(unittest.TestCase):
    def run_child(self, *, namespace_error=None, probe_result="pass", bad_snapshot=False,
                  pid=False, pid_error=None, mapped=False, ownership_error=None, confined=False, confinement_error=None):
        module = types.ModuleType("native_tools_child_test")
        exec(compile((ROOT / host.CHILD).read_bytes(), host.CHILD, "exec"), module.__dict__)
        snapshot = b"def execute_process_group(*args, **kwargs): raise AssertionError('synthetic')\n"
        config = {"schema": "nib.mkosi-tools-probe-input/v1", "git_sha": "a" * 40,
                  "tools_receipt_sha256": "b" * 64,
                  "snapshot_sha256": "c" * 64 if bad_snapshot else base.digest(snapshot)}
        check = mock.Mock(side_effect=namespace_error)
        probe = mock.Mock(return_value={"result": probe_result, "checks": [None] * 7})
        pid_check = mock.Mock(side_effect=pid_error)
        ownership_check = mock.Mock(side_effect=ownership_error)
        confinement_check = mock.Mock(side_effect=confinement_error)
        argv = ["native-tools.py", *ORIGINAL]
        if pid:
            argv.append("pid:[100]")
        if mapped:
            argv.append("mapped-ownership")
        if confined:
            argv.append("final-confinement")
        worker = dict(host.native.worker.__dict__)
        reader = mock.Mock(side_effect=([b'{"synthetic":true}'] if mapped else [])
                           + [base.encoded(config), snapshot])
        worker.update(read=reader, probe=probe)
        output = io.BytesIO()
        error = None
        with mock.patch.object(module.sys, "platform", "linux"), \
                mock.patch.object(module.sys, "version_info", (3, 12)), \
                mock.patch.object(module.sys, "flags", types.SimpleNamespace(isolated=True, no_site=True, dont_write_bytecode=True)), \
                mock.patch.object(module.sys, "argv", argv), \
                mock.patch.object(module.sys, "stdout", types.SimpleNamespace(buffer=output)), \
                mock.patch.object(module.os, "geteuid", return_value=0), \
                mock.patch.object(module.os, "getegid", return_value=0), \
                mock.patch.object(module.runpy, "run_path", side_effect=[worker, {"check": check},
                    {"check": pid_check}, {"check": ownership_check}, {"check": confinement_check}]):
            try:
                module.main()
            except ValueError as exc:
                error = exc
        if pid:
            pid_check.assert_called_once_with("pid:[100]")
        else:
            pid_check.assert_not_called()
        if mapped and pid_error is None:
            ownership_check.assert_called_once_with({"synthetic": True})
        else:
            ownership_check.assert_not_called()
        if confined and ownership_error is None:
            confinement_check.assert_called_once_with()
        else:
            confinement_check.assert_not_called()
        return error, output.getvalue(), reader, check, probe

    def test_child_requires_namespace_proof_before_seven_tools(self):
        error, output, reader, check, probe = self.run_child()
        self.assertIsNone(error)
        self.assertEqual(output, host.SUCCESS)
        check.assert_called_once_with(ORIGINAL)
        self.assertEqual(reader.call_count, 2)
        self.assertEqual(probe.call_count, 1)

    def test_namespace_or_snapshot_failure_never_starts_tools(self):
        for options in ({"namespace_error": ValueError("synthetic")}, {"bad_snapshot": True}):
            error, output, _, _, probe = self.run_child(**options)
            self.assertIsNotNone(error)
            self.assertEqual(output, b"")
            probe.assert_not_called()

    def test_failed_tools_do_not_emit_success(self):
        error, output, _, _, _ = self.run_child(probe_result="fail")
        self.assertIsNotNone(error)
        self.assertEqual(output, b"")

    def test_pid_proof_precedes_every_tool_and_failure_stops(self):
        error, output, _, _, probe = self.run_child(pid=True)
        self.assertIsNone(error)
        self.assertEqual(output, host.SUCCESS)
        probe.assert_called_once()
        error, output, reader, _, probe = self.run_child(pid=True, pid_error=ValueError("synthetic"))
        self.assertIsNotNone(error)
        self.assertEqual(error.stage, "pid")
        self.assertEqual(output, b"")
        reader.assert_not_called()
        probe.assert_not_called()

    def test_child_reports_fixed_stage_not_exception_text(self):
        for options, expected in (({"namespace_error": OSError("PRIVATE")}, "namespaces"),
                                  ({"bad_snapshot": True}, "inputs"),
                                  ({"probe_result": "fail"}, "tools")):
            error, output, _, _, _ = self.run_child(**options)
            self.assertEqual(error.stage, expected)
            self.assertEqual(str(error), expected)
            self.assertEqual(output, b"")

    def test_ownership_check_precedes_tools_and_rejects_without_output(self):
        error, output, _, _, probe = self.run_child(pid=True, mapped=True)
        self.assertIsNone(error)
        self.assertEqual(output, host.SUCCESS)
        probe.assert_called_once()
        error, output, _, _, probe = self.run_child(pid=True, mapped=True,
                                                   ownership_error=ValueError("PRIVATE"))
        self.assertEqual(error.stage, "ownership")
        self.assertEqual(output, b"")
        self.assertNotIn("PRIVATE", str(error))
        probe.assert_not_called()


    def test_final_confinement_must_pass_before_every_tool(self):
        error, output, _, _, probe = self.run_child(pid=True, mapped=True, confined=True)
        self.assertIsNone(error)
        self.assertEqual(output, host.SUCCESS)
        probe.assert_called_once()
        error, output, _, _, probe = self.run_child(pid=True, mapped=True, confined=True,
                                                   confinement_error=ValueError("PRIVATE"))
        self.assertEqual(error.stage, "confinement")
        self.assertEqual(output, b"")
        probe.assert_not_called()


class PIDTests(unittest.TestCase):
    def check(self, *, original="pid:[100]", current="pid:[101]", pid=1, ppid=0,
              self_link="1", entries=("1", "self", "sys"), status="NSpid:\t1\nPPid:\t0\n"):
        checker = types.SimpleNamespace(**runpy.run_path(str(ROOT / host.PID_CHECKER)))
        rows = mock.MagicMock()
        rows.__enter__.return_value = iter(types.SimpleNamespace(name=name) for name in entries)
        with mock.patch.object(checker.os, "getpid", return_value=pid), \
                mock.patch.object(checker.os, "getppid", return_value=ppid), \
                mock.patch.object(checker.os, "readlink", side_effect=[current, self_link]), \
                mock.patch.object(checker.os, "scandir", return_value=rows), \
                mock.patch.object(checker.Path, "read_text", return_value=status):
            checker.check(original)

    def test_fresh_pid_one_with_own_proc_passes(self):
        self.check()

    def test_same_namespace_or_wrong_process_identity_fails(self):
        for options in ({"current": "pid:[100]"}, {"current": "mnt:[101]"},
                        {"original": "bad"}, {"pid": 8}, {"ppid": 100}, {"self_link": "100"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.check(**options)

    def test_host_processes_visible_or_wrong_procfs_fails(self):
        for options in ({"entries": ("1", "100")}, {"entries": ()},
                        {"status": "NSpid:\t100 1\nPPid:\t0\n"},
                        {"status": "NSpid:\t1\nPPid:\t100\n"},
                        {"status": ""}, {"status": "a" * 16384}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.check(**options)


if __name__ == "__main__":
    unittest.main()
