# SPDX-License-Identifier: Apache-2.0
"""Offline driver-build contracts; never run a compiler or load a module."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from test_mkosi_build import host, load

driver = load("scripts/mkosi_dkms.py")
guard = load("scripts/mkosi_confinement.py")


def put(root, name, raw):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def elf():
    raw = bytearray(64)
    raw[:7], raw[16:20] = b"\x7fELF\x02\x01\x01", b"\x01\x00\x3e\x00"
    return bytes(raw)


class DriverTests(unittest.TestCase):
    def test_regular_file_rejects_links_special_files_and_unbounded_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            file = put(root, "source", b"public")
            self.assertEqual(driver.regular(root, "source", 6), b"public")
            for name in ("", ".", "../source", "/source", "a/../source", "./source"):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    driver.regular(root, name, 6)
            for raw in (b"", b"x" * 7):
                file.write_bytes(raw)
                with self.assertRaises(ValueError):
                    driver.regular(root, "source", 6)
            file.write_bytes(b"public")
            (root / "link").symlink_to(file)
            (root / "parent").symlink_to(root, target_is_directory=True)
            os.link(file, root / "hardlink")
            os.mkfifo(root / "fifo")
            for name in ("link", "parent/source", "hardlink", "source", "fifo"):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    driver.regular(root, name, 6)

    def test_input_identity_and_no_autoinstall_are_required_before_build(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pins = {name: hashlib.sha256(name.encode()).hexdigest() for name in driver.SOURCE_PINS}
            for name in pins:
                put(root, name, name.encode())
            controls = {name: b"reviewed control\n" for name in driver.CONTROL_NAMES}
            for name, raw in controls.items():
                put(root, name, raw)
            link = root / "lib/modules" / driver.KERNEL / "build"
            link.parent.mkdir(parents=True)
            link.symlink_to("/usr/src/linux-headers-" + driver.KERNEL)
            with mock.patch.object(driver, "SOURCE_PINS", pins):
                driver.verify_inputs(root, controls)
                for name in (*pins, *controls):
                    original = (root / name).read_bytes()
                    (root / name).write_bytes(b"changed")
                    with self.subTest(name=name), self.assertRaises(ValueError):
                        driver.verify_inputs(root, controls)
                    (root / name).write_bytes(original)
                link.unlink()
                link.symlink_to("/host/headers")
                with self.assertRaises(ValueError):
                    driver.verify_inputs(root, controls)
                link.unlink()
                link.symlink_to("/usr/src/linux-headers-" + driver.KERNEL)
                (root / "var/lib/dkms/nvidia").mkdir(parents=True)
                with self.assertRaises(ValueError):
                    driver.verify_inputs(root, controls)
                with self.assertRaises(ValueError):
                    driver.verify_inputs(root, {})

    def test_fixed_commands_and_unchanged_final_confinement(self):
        self.assertEqual(driver.operation("compiler"),
                         ["/usr/bin/x86_64-linux-gnu-gcc-13", "-dumpfullversion", "-dumpversion"])
        for name in ("add", "build"):
            self.assertEqual(driver.operation(name), ["/usr/sbin/dkms", name, "-m", "nvidia",
                "-v", "595.91.07", "-k", "6.8.0-147-generic", "-a", "x86_64"])
            args = driver.command(name, guard.PREFIX)
            index = args.index("/usr/bin/setpriv")
            self.assertEqual(args[index:index + len(guard.PREFIX)], list(guard.PREFIX))
            self.assertEqual(args[-6:], ["/usr/bin/python3.12", "-I", "-S", "-B", "/nib/dkms-worker.py", name])
            self.assertEqual(args.count("--bind"), 1)
            self.assertIn(["--bind", str(driver.ROOTFS), str(driver.ROOTFS)],
                          [args[i:i+3] for i in range(len(args)-2)])
            self.assertIn(["--dev", str(driver.ROOTFS) + "/dev"],
                          [args[i:i+2] for i in range(len(args)-1)])
            self.assertNotIn("sys_module", " ".join(args))
        for name in ("autoinstall", "install", "modprobe", "build; other", "--force"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                driver.operation(name)

    def test_module_identity_is_exact_bounded_and_not_a_boot_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in driver.MODULES:
                put(root, driver.MODULE_DIRECTORY + name + ".ko", elf())
            rows = driver.inspect_modules(root)
            self.assertEqual([row["name"] for row in rows], list(driver.MODULES))
            self.assertTrue(all(row["bytes"] == 64 and row["sha256"] == hashlib.sha256(elf()).hexdigest()
                                for row in rows))
            file = root / driver.MODULE_DIRECTORY / "nvidia.ko"
            for raw in (b"", elf()[:63], elf().replace(b"ELF", b"BAD"),
                        elf()[:16] + b"\x03\x00" + elf()[18:], b"compressed"):
                file.write_bytes(raw)
                with self.subTest(raw=raw), self.assertRaises(ValueError):
                    driver.inspect_modules(root)
            file.unlink()
            with self.assertRaises(FileNotFoundError):
                driver.inspect_modules(root)

    def test_build_success_never_claims_installation_lkca_or_attestation(self):
        processes = host.base.processes
        results = [subprocess.CompletedProcess([], 0, b"13.3.0\n", b""),
                   subprocess.CompletedProcess([], 0, b"added\n", b""),
                   subprocess.CompletedProcess([], 0, b"built\n", b"")]
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            with mock.patch.object(driver, "verify_inputs") as verify, \
                    mock.patch.object(driver, "preserve_make_log") as logs, \
                    mock.patch.object(driver, "inspect_modules", return_value=[{"fixture": True}]), \
                    mock.patch.object(processes, "execute_process_group", side_effect=results) as execute:
                result = driver.build(processes, output, {}, {}, guard.PREFIX)
            verify.assert_called_once_with(driver.ROOTFS, {})
            logs.assert_called_once_with(driver.ROOTFS, output)
            self.assertEqual(result["result"], "pass")
            for flag in ("installed", "loaded", "lkca_verified", "reproducibility_verified", "bootable"):
                self.assertIs(result[flag], False)
            self.assertEqual([call.kwargs["timeout"] for call in execute.call_args_list], [30, 30, 1500])
            for call in execute.call_args_list:
                self.assertEqual(call.kwargs["max_output_bytes"], 2 << 20)
                self.assertEqual(call.kwargs["max_stderr_bytes"], 2 << 20)
                self.assertEqual(call.kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual((output / "dkms-build.stdout").read_bytes(), b"built\n")

    def test_compiler_mismatch_stops_before_dkms(self):
        processes = host.base.processes
        for stdout, stderr in ((b"13.2.0\n", b""), (b"13.3.0\n", b"unexpected")):
            with self.subTest(stdout=stdout), tempfile.TemporaryDirectory() as temporary, \
                    mock.patch.object(driver, "verify_inputs"), \
                    mock.patch.object(processes, "execute_process_group", return_value=
                                      subprocess.CompletedProcess([], 0, stdout, stderr)) as execute:
                with self.assertRaises(ValueError):
                    driver.build(processes, Path(temporary), {}, {}, guard.PREFIX)
                self.assertEqual(execute.call_count, 1)

    def test_rejected_source_never_executes(self):
        processes = host.base.processes
        with mock.patch.object(driver, "verify_inputs", side_effect=ValueError), \
                mock.patch.object(processes, "execute_process_group") as execute:
            with self.assertRaises(ValueError):
                driver.build(processes, Path("/unused"), {}, {}, guard.PREFIX)
            execute.assert_not_called()

    def test_failure_and_output_limit_preserve_bounded_diagnostics(self):
        processes = host.base.processes
        failures = (subprocess.CompletedProcess([], 2, b"public", b"failed"),
                    processes.OutputLimitExceeded("stderr", b"", b"bounded prefix"))
        for failure in failures:
            with self.subTest(failure=type(failure)), tempfile.TemporaryDirectory() as temporary, \
                    mock.patch.object(driver, "verify_inputs"), \
                    mock.patch.object(driver, "preserve_make_log") as logs, \
                    mock.patch.object(driver, "inspect_modules") as inspect, \
                    mock.patch.object(processes, "execute_process_group", side_effect=[
                        subprocess.CompletedProcess([], 0, b"13.3.0\n", b""),
                        subprocess.CompletedProcess([], 0, b"", b""), failure]):
                output = Path(temporary)
                with self.assertRaises((subprocess.CalledProcessError, processes.OutputLimitExceeded)):
                    driver.build(processes, output, {}, {}, guard.PREFIX)
                logs.assert_called_once_with(driver.ROOTFS, output)
                inspect.assert_not_called()
                self.assertEqual((output / "dkms-build.stderr").read_bytes(), failure.stderr)

    def test_only_known_bounded_make_logs_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, output = Path(temporary) / "root", Path(temporary) / "out"
            root.mkdir()
            output.mkdir()
            put(root, "private.txt", b"DO NOT COPY")
            driver.preserve_make_log(root, output)
            self.assertEqual(list(output.iterdir()), [])
            for name in driver.MAKE_LOGS:
                put(root, name, b"public build diagnostics")
            driver.preserve_make_log(root, output)
            self.assertEqual({p.name for p in output.iterdir()}, {"dkms-make-0.log", "dkms-make-1.log"})
            put(root, driver.MAKE_LOGS[0], b"x" * ((2 << 20) + 1))
            with self.assertRaises(ValueError):
                driver.preserve_make_log(root, output)


if __name__ == "__main__":
    unittest.main()
