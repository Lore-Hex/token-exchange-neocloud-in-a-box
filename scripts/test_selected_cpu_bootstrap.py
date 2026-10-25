# SPDX-License-Identifier: Apache-2.0
from contextlib import ExitStack
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("cpu_bootstrap", ROOT / "host/prepare-selected-cpu.py")
cpu = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cpu)


def archive(rows, mode="w:gz"):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode=mode) as tar:
        for name, data, kind, link in rows:
            member = tarfile.TarInfo(name)
            member.type, member.linkname, member.mode = kind, link, 0o755
            member.size = len(data) if kind == tarfile.REGTYPE else 0
            tar.addfile(member, io.BytesIO(data) if member.size else None)
    return output.getvalue()


class CpuBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.commands = Mock()
        self.commands.remaining.return_value = 100

    def test_public_pins_and_existing_ubuntu_pins_agree(self):
        self.assertEqual(len(cpu.artifacts()), 8)
        for pin in cpu.artifacts():
            self.assertGreater(pin["size"], 0)
            self.assertRegex(pin["sha256"], r"^[a-f0-9]{64}$")
            self.assertTrue(pin["url"].startswith("https://"))
        self.assertEqual(len(cpu.PINS["docker"]["binaries"]), 8)
        self.assertEqual(cpu.PINS["python"]["binary_sha256"],
                         "339de109f072e52523066d97a2b3372e358a10cc00c6a9df62c28a6618061900")
        locked = json.loads((ROOT / "image/inputs/guest-runtime-ubuntu2404.json").read_bytes())
        serialized = json.dumps(locked)
        for pin in cpu.PINS["ubuntu"]:
            if pin["package"] in ("nftables", "util-linux", "mount", "openssl"):
                self.assertIn(pin["sha256"], serialized)

    def test_input_reader_rejects_links_fifo_and_limits(self):
        regular = self.root / "regular"
        regular.write_bytes(b"abc")
        self.assertEqual(cpu.read(regular, 3), b"abc")
        link = self.root / "link"
        link.symlink_to(regular)
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        for path, cap in ((regular, 2), (link, 3), (fifo, 3), (self.root, 3)):
            with self.subTest(path=path), self.assertRaises((OSError, ValueError)):
                cpu.read(path, cap)

    def test_all_inputs_sealed_by_size_and_hash(self):
        pin = {"file": "input", "size": 3, "sha256": cpu.sha(b"abc")}
        target = self.root / "input"
        target.write_bytes(b"abc")
        with patch.object(cpu, "artifacts", return_value=[pin]):
            self.assertEqual(cpu.verify_inputs(self.root), {"input": b"abc"})
            for bad in (b"bad", b"abcd", b"a"):
                target.write_bytes(bad)
                with self.assertRaises(ValueError):
                    cpu.verify_inputs(self.root)

    def test_fetch_is_fixed_https_bounded_and_atomic_no_replace(self):
        pin = {"file": "input", "size": 3, "sha256": cpu.sha(b"abc"), "url": "https://example.invalid/fixed"}
        self.commands.run.side_effect = lambda _argv, **kw: kw["output"].write(b"abc")
        target = self.root / "inputs"
        with patch.object(cpu, "artifacts", return_value=[pin]):
            cpu.fetch(target, self.commands)
            self.assertEqual((target / "input").read_bytes(), b"abc")
            self.assertEqual((target / "input").stat().st_mode & 0o777, 0o400)
            self.assertIn("--max-time", self.commands.run.call_args.args[0])
            self.assertIn("--proto-redir", self.commands.run.call_args.args[0])
            self.assertEqual(self.commands.run.call_args.kwargs["cap"], 3)
            with self.assertRaises(FileExistsError):
                cpu.fetch(target, self.commands)

    def test_bad_download_not_published(self):
        pin = {"file": "input", "size": 3, "sha256": cpu.sha(b"abc"), "url": "https://example.invalid/fixed"}
        self.commands.run.side_effect = lambda _argv, **kw: kw["output"].write(b"bad")
        with patch.object(cpu, "artifacts", return_value=[pin]), self.assertRaises(ValueError):
            cpu.fetch(self.root / "inputs", self.commands)
        self.assertFalse((self.root / "inputs/input").exists())

    def test_archive_internal_symlink_and_escape_rejection(self):
        good = archive([("python/bin/python3.12", b"binary", tarfile.REGTYPE, ""),
                        ("python/bin/python3", b"", tarfile.SYMTYPE, "python3.12")])
        cpu.extract(good, self.root, "python", self.commands)
        self.assertEqual((self.root / "python/bin/python3").read_bytes(), b"binary")
        for name, kind, link in (("../outside", tarfile.REGTYPE, ""),
                                 ("/outside", tarfile.REGTYPE, ""),
                                 ("other/file", tarfile.REGTYPE, ""),
                                 ("python/escape", tarfile.SYMTYPE, "../../outside"),
                                 ("python/hard", tarfile.LNKTYPE, "python/bin/python3.12"),
                                 ("python/fifo", tarfile.FIFOTYPE, "")):
            with self.subTest(name=name), self.assertRaises((ValueError, tarfile.TarError)):
                cpu.extract(archive([(name, b"", kind, link)]), self.root, "python", self.commands)

    def test_duplicate_archive_member_rejected(self):
        rows = [("python/test", b"x", tarfile.REGTYPE, "")] * 2
        with self.assertRaises(ValueError):
            cpu.extract(archive(rows), self.root, "python", self.commands)

    def test_linux_case_distinct_terminfo_alias_from_pinned_python(self):
        (self.root / "CaseProbe").write_bytes(b"case")
        if (self.root / "caseprobe").exists():
            self.skipTest("Linux Python archive requires a case-sensitive filesystem")
        rows = [("python/share/terminfo/N/NCR260VT300WPP", b"", tarfile.SYMTYPE, "../n/ncr260vt300wpp"),
                ("python/share/terminfo/n/ncr260vt300wpp", b"fixture", tarfile.REGTYPE, "")]
        cpu.extract(archive(rows), self.root, "python", self.commands)
        self.assertEqual((self.root / rows[0][0]).read_bytes(), b"fixture")

    def test_deadline_does_not_reset_and_cancel_stops_commands(self):
        state = {"cancelled": False}
        with patch.object(cpu.time, "monotonic", return_value=100), patch.object(cpu.snapshot, "execute_process_group") as run:
            run.return_value = subprocess.CompletedProcess([], 0, b"ok", b"")
            commands = cpu.Commands(state)
            original = commands.end
            self.assertEqual(commands.run(["fixed"]), b"ok")
            self.assertEqual(commands.end, original)
            self.assertEqual(run.call_args.kwargs["timeout"], 120)
            self.assertEqual(run.call_args.kwargs["max_output_bytes"], 65536)
            state["cancelled"] = True
            with self.assertRaises(ValueError):
                commands.run(["fixed"])
            self.assertEqual(run.call_count, 1)
        with patch.object(cpu.time, "monotonic", return_value=original - 25), self.assertRaises(ValueError):
            commands.remaining()

    def test_repeated_cancellation_and_restoration(self):
        handlers = {}
        old = {signal.SIGINT: object(), signal.SIGTERM: object()}
        def change(sig, handler):
            previous = handlers.get(sig, old[sig])
            handlers[sig] = handler
            return previous
        with patch.object(cpu.signal, "signal", side_effect=change):
            with cpu.cancellation() as state:
                handlers[signal.SIGINT](signal.SIGINT, None)
                handlers[signal.SIGTERM](signal.SIGTERM, None)
                self.assertTrue(state["cancelled"])
            self.assertEqual(handlers, old)

    def test_signal_mask_covers_entire_handler_restoration(self):
        events = []
        def mask(how, signals):
            events.append(("mask", how, signals))
            return set()
        def handler(sig, value):
            events.append(("handler", sig, value))
            return signal.SIG_DFL
        with patch.object(cpu.signal, "signal", side_effect=handler), \
             patch.object(cpu.signal, "pthread_sigmask", side_effect=mask):
            with cpu.cancellation():
                events.clear()
        self.assertEqual([event[0] for event in events], ["mask", "handler", "handler", "mask"])
        self.assertEqual(events[0][1], signal.SIG_BLOCK)
        self.assertEqual(events[-1][1], signal.SIG_SETMASK)

    def test_ubuntu_binary_matches_sealed_package_and_mismatches_fail_closed(self):
        binary = self.root / "xtables-nft-multi"
        binary.write_bytes(b"fixed-tool")
        pin = {"package": "iptables", "version": "1.8.10-3ubuntu2", "binary": "xtables-nft-multi", "file": "package.deb"}
        packed = archive([("./usr/sbin/xtables-nft-multi", b"fixed-tool", tarfile.REGTYPE, ""),
                          ("./usr/share/bash-completion/completions/xtables-nft-multi", b"completion", tarfile.REGTYPE, "")], mode="w:")
        def run(args, **_kwargs):
            if args[0] == "/usr/bin/dpkg-query":
                return b"install ok installed\t1.8.10-3ubuntu2"
            if args[0] == "/usr/bin/dpkg-deb":
                self.assertEqual(Path(args[-1]).read_bytes(), b"sealed-package")
                return packed
            return {"nft": b"nftables v1.0.9 (Old Doc Yak #3)", "unshare": b"unshare from util-linux 2.39.3"}[args[0]]
        self.commands.run.side_effect = run
        original = Path.lstat
        def info(path):
            if path == binary:
                return SimpleNamespace(st_uid=0, st_mode=0o100755)
            return original(path)
        with ExitStack() as stack:
            stack.enter_context(patch.object(cpu, "PINS", {"ubuntu": [pin]}))
            stack.enter_context(patch.object(cpu, "secure"))
            stack.enter_context(patch.object(cpu.shutil, "which", return_value=str(binary)))
            stack.enter_context(patch.object(Path, "lstat", info))
            report = {}
            observed = cpu.ubuntu_tools({"package.deb": b"sealed-package"}, self.commands, report)
            self.assertEqual(observed["xtables-nft-multi"]["sha256"], cpu.sha(b"fixed-tool"))
            self.assertNotIn("package", report)
            binary.write_bytes(b"modified-tool")
            with self.assertRaises(ValueError):
                cpu.ubuntu_tools({"package.deb": b"sealed-package"}, self.commands, report)
            self.assertEqual(report["package"], "iptables")

    def test_ubuntu_package_version_mismatch_never_unpacks(self):
        pin = {"package": "nftables", "version": "fixed", "file": "nft.deb", "binary": "nft"}
        self.commands.run.return_value = b"install ok installed\tunexpected"
        with patch.object(cpu, "PINS", {"ubuntu": [pin]}), self.assertRaises(ValueError):
            cpu.ubuntu_tools({}, self.commands, {})
        self.assertEqual(self.commands.run.call_count, 1)

    def test_wrong_machine_fails_before_filesystem_changes(self):
        with ExitStack() as stack:
            stack.enter_context(patch.object(cpu.os, "geteuid", return_value=0))
            stack.enter_context(patch.object(cpu.platform, "system", return_value="Linux"))
            stack.enter_context(patch.object(cpu.platform, "machine", return_value="x86_64"))
            stack.enter_context(patch.object(cpu, "read", return_value=b"wrong-machine\n"))
            release = stack.enter_context(patch.object(cpu.platform, "freedesktop_os_release"))
            with self.assertRaises(ValueError):
                cpu.platform_check("0" * 64)
            release.assert_not_called()

    def test_resolver_preserves_original_bytes_and_link_privately(self):
        prefix = self.root / "prefix"
        prefix.mkdir()
        actual = self.root / "actual"
        actual.write_bytes(b"nameserver 127.0.0.53\noptions edns0 trust-ad\nsearch .\n")
        resolver = self.root / "resolv.conf"
        resolver.symlink_to(actual)
        before = {"link": str(actual), "raw": actual.read_bytes(), "stat": cpu.identity(resolver.lstat())}
        with patch.object(cpu, "PREFIX", prefix), patch.object(cpu, "RESOLVER", resolver), \
             patch.object(cpu, "resolver_input", return_value=before):
            self.assertTrue(cpu.regular_resolver(before))
        self.assertFalse(resolver.is_symlink())
        self.assertEqual(resolver.read_bytes(), actual.read_bytes())
        self.assertEqual((prefix / "resolver-bytes").read_bytes(), actual.read_bytes())
        self.assertEqual((prefix / "resolver-link").read_text(), str(actual))
        self.assertEqual((prefix / "resolver-bytes").stat().st_mode & 0o777, 0o600)
        self.assertEqual(resolver.stat().st_mode & 0o777, 0o644)

    def test_resolver_race_and_existing_regular_file(self):
        before = {"link": None, "raw": b"nameserver 127.0.0.53\n", "stat": ()}
        with patch.object(cpu, "resolver_input", return_value=before):
            self.assertFalse(cpu.regular_resolver(before))
        with patch.object(cpu, "resolver_input", return_value={**before, "raw": b"changed"}), self.assertRaises(ValueError):
            cpu.regular_resolver(before)

    def install_fixture(self):
        prefix, binary = self.root / "prefix", self.root / "bin"
        binary.mkdir()
        python, docker = b"python-test-only", b"docker-test-only"
        pins = {"python": {"file": "python.tgz", "binary_sha256": cpu.sha(python)},
                "docker": {"file": "docker.tgz", "binaries": {"dockerd": cpu.sha(docker)}}}
        data = {"python.tgz": archive([("python/bin/python3.12", python, tarfile.REGTYPE, "")]),
                "docker.tgz": archive([("docker/dockerd", docker, tarfile.REGTYPE, "")])}
        return prefix, binary, pins, data

    def test_install_publishes_only_verified_binaries_never_starts_daemon(self):
        prefix, binary, pins, data = self.install_fixture()
        self.commands.run.side_effect = [b"ok\n", b"Docker version 29.1.3, build fixed\n"]
        with patch.object(cpu, "PREFIX", prefix), patch.object(cpu, "BIN", binary), \
             patch.object(cpu, "PINS", pins), patch.object(cpu, "regular_resolver", return_value=True) as resolver:
            self.assertTrue(cpu.install(data, {}, self.commands))
            resolver.assert_called_once()
        self.assertEqual((binary / "dockerd").read_bytes(), b"docker-test-only")
        calls = [call.args[0] for call in self.commands.run.call_args_list]
        self.assertEqual(calls[-1], [str(prefix / "docker/dockerd"), "--version"])
        self.assertEqual(len(calls), 2)

    def test_existing_binary_not_overwritten_and_no_resolver_change(self):
        prefix, binary, pins, data = self.install_fixture()
        (binary / "dockerd").write_bytes(b"operator-owned")
        self.commands.run.side_effect = [b"ok\n", b"Docker version 29.1.3, build fixed\n"]
        with patch.object(cpu, "PREFIX", prefix), patch.object(cpu, "BIN", binary), \
             patch.object(cpu, "PINS", pins), patch.object(cpu, "regular_resolver") as resolver:
            with self.assertRaises(FileExistsError):
                cpu.install(data, {}, self.commands)
            resolver.assert_not_called()
        self.assertEqual((binary / "dockerd").read_bytes(), b"operator-owned")

    def test_wrong_interpreter_bytes_never_published(self):
        prefix, binary, pins, data = self.install_fixture()
        pins["python"]["binary_sha256"] = "0" * 64
        with patch.object(cpu, "PREFIX", prefix), patch.object(cpu, "BIN", binary), patch.object(cpu, "PINS", pins):
            with self.assertRaises(ValueError):
                cpu.install(data, {}, self.commands)
        self.commands.run.assert_not_called()
        self.assertFalse(list(binary.iterdir()))

    def test_check_mode_never_installs_and_failure_has_no_exception_text(self):
        for failure in (None, ValueError("PRIVATE-BODY-CANARY")):
            with self.subTest(failure=failure), ExitStack() as stack:
                stack.enter_context(patch.object(cpu.sys, "argv", ["bootstrap", "--check", "--inputs", str(self.root),
                    "--machine-id-sha256", "0" * 64]))
                stack.enter_context(patch.object(cpu, "source_identity", return_value={"revision": "0" * 40}))
                stack.enter_context(patch.object(cpu, "platform_check"))
                stack.enter_context(patch.object(cpu, "secure"))
                stack.enter_context(patch.object(cpu, "verify_inputs", return_value={}))
                stack.enter_context(patch.object(cpu, "ubuntu_tools", return_value={}, side_effect=failure))
                stack.enter_context(patch.object(cpu, "resolver_input", return_value={"link": None}))
                install = stack.enter_context(patch.object(cpu, "install"))
                output = stack.enter_context(patch("sys.stdout", new_callable=io.StringIO))
                self.assertEqual(cpu.main(), int(failure is not None))
                report = json.loads(output.getvalue())
                self.assertFalse(report["installed"])
                self.assertFalse(report["mutation_started"])
                self.assertNotIn("PRIVATE-BODY-CANARY", output.getvalue())
                install.assert_not_called()


if __name__ == "__main__":
    unittest.main()
