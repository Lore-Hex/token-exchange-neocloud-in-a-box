# SPDX-License-Identifier: Apache-2.0
"""Filesystem/archive fixtures and fake process results; no packaged program runs."""
import copy
import gzip
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import struct
import tarfile
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    module = types.ModuleType(name)
    module.__file__ = str(ROOT / "scripts" / (name + ".py"))
    exec(compile(Path(module.__file__).read_bytes(), module.__file__, "exec",
                 dont_inherit=True), module.__dict__)
    return module


prep, sim = load("prepare_guest_tools"), load("guest_apt_simulation")
runner = types.ModuleType("guest_apt_host")
runner.__file__ = str(ROOT / "host/simulate-guest-apt.py")
exec(compile(Path(runner.__file__).read_bytes(), runner.__file__, "exec",
             dont_inherit=True), runner.__dict__)


def deb(tree):
    data = prep.tar_bytes(copy.deepcopy(tree))
    control = gzip.compress(b"unused control", mtime=0)

    def member(name, raw):
        header = f"{name + '/':<16}{0:<12}{0:<6}{0:<6}{'100644':<8}{len(raw):<10}`\n".encode()
        return header + raw + (b"\n" if len(raw) % 2 else b"")
    return (b"!<arch>\n" + member("debian-binary", b"2.0\n")
            + member("control.tar.gz", control) + member("data.tar", data))


def file(data=b"not executed", mode=0o755):
    return {"kind": "file", "data": data, "mode": mode}


def inputs():
    return {"schema": "nib.apt-simulation-input/v1", "apt_version": "2.8.3",
            "git_sha": "a" * 40, "guest_lock_sha256": "b" * 64,
            "targets": {"fixture": "1.0"}}


def plan(name="fixture", version="1.0"):
    return f"Inst {name} ({version} localhost [amd64])\nConf {name} ({version} localhost [amd64])\n".encode()


class PayloadTests(unittest.TestCase):
    def test_deterministic_data_archive_no_execution_normalized_identity(self):
        tree = {"usr/bin/tool": file(b"#!/bin/sh\nexit 99\n", 0o4755),
                "bin": {"kind": "link", "target": "usr/bin", "mode": 0o777}}
        raw = deb(tree)
        result = prep.payload(raw)
        self.assertEqual(result["usr/bin/tool"]["mode"], 0o755)
        self.assertEqual(result["usr/bin/tool"]["data"], tree["usr/bin/tool"]["data"])
        self.assertEqual(prep.tar_bytes(result), prep.tar_bytes(prep.payload(raw)))
        self.assertEqual(prep.resolve(result, "bin/tool"), "usr/bin/tool")

    def test_traversal_and_escaping_targets_reject(self):
        for name in ("../secret", "/private/secret", "a/../secret", "a//b", "a/\nsecret"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                prep.path_name(name)
        for target in ("../../secret", "\nPRIVATE", ""):
            with self.subTest(target=target), self.assertRaises(ValueError):
                prep.link_target("usr/link", target)

    def test_cycles_reject_and_absolute_links_remain_virtual(self):
        tree = {"usr/link": {"kind": "link", "target": "/usr/other", "mode": 0o777},
                "usr/other": file(b"public")}
        self.assertEqual(prep.resolve(tree, "usr/link"), "usr/other")
        tree["usr/other"] = {"kind": "link", "target": "/usr/link", "mode": 0o777}
        with self.assertRaises(ValueError):
            prep.resolve(tree, "usr/link")

    def test_collision_and_file_ancestor_reject(self):
        tree = {"usr/bin/tool": file()}
        with self.assertRaises(ValueError):
            prep.merge(tree, {"usr/bin/tool": file(b"changed")})
        with self.assertRaises(ValueError):
            prep.merge({"usr": file()}, {"usr/bin/tool": file()})
        with self.assertRaises(ValueError):
            prep.complete_tree({"usr": file(), "usr/bin/tool": file()})

    def test_merged_usr_directory_alias_is_not_host_traversal(self):
        tree = {"bin": {"kind": "link", "target": "usr/bin", "mode": 0o777}}
        prep.merge(tree, {"bin": {"kind": "dir", "mode": 0o755}, "bin/tool": file()})
        self.assertEqual(tree["usr/bin/tool"], file())
        prep.complete_tree(tree)
        self.assertEqual(tree["bin"]["kind"], "link")

    def test_special_data_and_duplicate_names_reject(self):
        for kind in (tarfile.FIFOTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.REGTYPE):
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as archive:
                member = tarfile.TarInfo("name")
                member.type = kind
                archive.addfile(member)
                if kind == tarfile.REGTYPE:
                    archive.addfile(member)
            with mock.patch.object(prep.base, "archive_members",
                                   return_value=(("control.tar", b""), ("data.tar", buf.getvalue()))):
                with self.assertRaises(ValueError):
                    prep.payload(b"fake")

    def test_hardlinks_copy_only_known_regular_target(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as archive:
            member = tarfile.TarInfo("source")
            member.size, member.mode = 1, 0o644
            archive.addfile(member, io.BytesIO(b"x"))
            member = tarfile.TarInfo("link")
            member.type, member.linkname, member.mode = tarfile.LNKTYPE, "source", 0o644
            archive.addfile(member)
        with mock.patch.object(prep.base, "archive_members",
                               return_value=(("control.tar", b""), ("data.tar", buf.getvalue()))):
            result = prep.payload(b"fake")
        self.assertEqual(result["link"], result["source"])
        self.assertEqual(result["link"]["kind"], "file")

    def test_payload_expansion_and_entry_limits(self):
        raw = deb({"a": file()})
        with mock.patch.object(prep, "MAX_EXPANDED", 1), self.assertRaises(ValueError):
            prep.payload(raw)
        with mock.patch.object(prep, "MAX_ENTRIES", 0), self.assertRaises(ValueError):
            prep.payload(raw)

    def test_input_fifo_symlink_hardlink_reject_without_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "regular").write_bytes(b"x")
            (root / "link").symlink_to(root / "regular")
            os.mkfifo(root / "fifo")
            for name in ("link", "fifo"):
                with self.assertRaises((ValueError, OSError)):
                    prep.base.read(root / name, 10)
            os.link(root / "regular", root / "hard")
            with self.assertRaises(ValueError):
                prep.base.read(root / "hard", 10)

    def test_publication_nooverwrite_and_tamper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "bundle"
            files = {"image.tar": b"public image fixture", "receipt.json": b"{}\n"}
            prep.base.publish(root, files)
            prep.base.verify(root, files)
            with self.assertRaises(FileExistsError):
                prep.base.publish(root, files)
            (root / "image.tar").write_bytes(b"tamper")
            with self.assertRaises(ValueError):
                prep.base.verify(root, files)
            for output in (root, root / "child", root.parent):
                with self.assertRaises(ValueError):
                    prep.separate_output(output, (root,))

    def test_real_archive_writer_receipt_and_interpreter_binding(self):
        loader = b"\x7fELF\x02\x01" + b"\0" * 12 + b"\x3e\x00" + b"\0" * 44
        target = b"/lib64/loader.so\0"
        binary = bytearray(128 + len(target))
        binary[:20] = loader[:20]
        struct.pack_into("<Q", binary, 32, 64)
        struct.pack_into("<HH", binary, 54, 56, 1)
        struct.pack_into("<I", binary, 64, 3)
        struct.pack_into("<Q", binary, 72, 128)
        struct.pack_into("<Q", binary, 96, len(target))
        binary[128:] = target
        tree = {"usr/lib64/loader.so": file(loader)}
        tree.update({name: file(bytes(binary)) for name in
                     ("usr/bin/python3.12", "usr/bin/apt-get", "usr/bin/dpkg")})
        raw = deb(tree)
        row = {"Package": "fixture", "Version": "1.0", "Size": str(len(raw)),
               "SHA256": prep.digest(raw)}
        lock = {"packages": [row], "archive_bytes": len(raw)}
        sources = {name: Path(ROOT / name).read_bytes() for name in prep.FILES}
        sources[prep.FILES[-1]] = sources[prep.FILES[-2]] = prep.encoded(lock)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / (row["SHA256"] + ".deb")).write_bytes(raw)
            with mock.patch.object(prep.base.inputs, "check"), \
                    mock.patch.object(prep.base, "controls", return_value=(b"public metadata\n", {})):
                files, receipt = prep.build(root, root, "a" * 40, sources)
                self.assertEqual((files, receipt), prep.build(root, root, "a" * 40, sources))
                (root / (row["SHA256"] + ".deb")).write_bytes(raw[:-1] + b"x")
                with self.assertRaises(ValueError):
                    prep.build(root, root, "a" * 40, sources)
        self.assertEqual(prep.digest(files["image.tar"]), receipt["archive_sha256"])
        with tarfile.open(fileobj=io.BytesIO(files["image.tar"])) as archive:
            manifest = json.load(archive.extractfile("manifest.json"))[0]
            config = archive.extractfile(manifest["Config"]).read()
            layer = archive.extractfile("layer.tar").read()
        self.assertEqual(prep.digest(config), receipt["config_sha256"])
        self.assertEqual(prep.digest(layer), receipt["layer_sha256"])
        configuration = json.loads(config)
        self.assertEqual(configuration["rootfs"]["diff_ids"], ["sha256:" + receipt["layer_sha256"]])
        self.assertEqual(configuration["config"]["User"], "65534:65534")
        self.assertEqual(configuration["config"]["Entrypoint"],
                         ["/usr/bin/python3.12", "-I", "-S", "-B", "/nib/simulate.py"])
        self.assertEqual(receipt["executables"]["/usr/bin/python3.12"]["interpreter"]["sha256"],
                         prep.digest(loader))
        for key in ("package_scripts_executed", "package_binaries_executed",
                    "apt_simulation_executed", "installable_verified", "complete_mkosi_tools_tree"):
            self.assertFalse(receipt[key])
        tree["lib64"] = {"kind": "link", "target": "usr/lib64", "mode": 0o777}
        del tree["usr/lib64/loader.so"]
        with self.assertRaises(ValueError):
            prep.executable(tree, "/usr/bin/python3.12")


class SimulationTests(unittest.TestCase):
    def test_plan_requires_exact_full_inst_and_conf_no_removal(self):
        self.assertEqual(sim.validate_plan(plan(), inputs()["targets"])["installed"], 1)
        for suffix in (b" []", b" [fixture:amd64 ]"):
            self.assertEqual(sim.validate_plan(plan().replace(b")\nConf", b")" + suffix + b"\nConf"),
                                              inputs()["targets"])["installed"], 1)
        for raw in (b"", plan() + b"Remv old [1.0]\n", plan("extra"), plan(version="2.0"),
                    plan() * 2, plan().splitlines()[0] + b"\n",
                    plan() + b"W: PRIVATE diagnostic\n", plan().replace(b"Inst fixture", b"Inst fixture:arm64")):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                sim.validate_plan(raw, inputs()["targets"])
        for suffix in (b" [unknown:amd64 ]", b" [fixture on missing]", b" [fixture:arm64 ]",
                       b" [fixture fixture]", b" [PRIVATE]"):
            with self.assertRaises(ValueError):
                sim.validate_plan(plan().replace(b")\nConf", b")" + suffix + b"\nConf"), inputs()["targets"])

    def test_plan_input_output_bounds_and_invalid_versions(self):
        for name, version in (("--option", "1"), ("name", "-1"), ("name", "1\nPRIVATE"),
                              ("name", None)):
            with self.assertRaises(ValueError):
                sim.targets_valid({name: version})
        with self.assertRaises(ValueError):
            sim.validate_plan(b"x" * (sim.MAX_OUTPUT + 1), inputs()["targets"])

    def test_all_commands_are_bounded_fixed_and_no_installation_claim(self):
        outputs = [b"apt 2.8.3 (amd64)\n", b"Reading package lists...\n", plan()]
        execute = mock.Mock(side_effect=[subprocess.CompletedProcess([], 0, out, b"") for out in outputs])
        result = sim.simulate(inputs(), execute, setup_work=False)
        self.assertEqual(result["result"], "pass")
        for field in ("installable_verified", "package_installation_executed",
                      "maintainer_scripts_executed", "bootable", "deployable"):
            self.assertFalse(result[field])
        for call in execute.call_args_list:
            self.assertEqual(call.kwargs["env"], sim.ENV)
            self.assertTrue(0 < call.kwargs["timeout"] <= 120)
            self.assertEqual(call.kwargs["max_output_bytes"], 2 << 20)
        self.assertEqual(execute.call_args_list[-1].args[0], [
            "/usr/bin/apt-get", "--simulate", "--no-download", "--no-install-recommends",
            "install", "fixture=1.0"])

    def test_shared_deadline_timeout_and_command_errors_remain_failure(self):
        for error in (subprocess.TimeoutExpired("fixture", 1), ValueError("PRIVATE"),
                      OSError("PRIVATE")):
            result = sim.simulate(inputs(), mock.Mock(side_effect=error), setup_work=False)
            self.assertEqual(result["result"], "fail")
            self.assertNotIn("PRIVATE", json.dumps(result))
        execute = mock.Mock(return_value=subprocess.CompletedProcess([], 0, b"apt 2.8.3 (amd64)\n", b""))
        with mock.patch.object(sim.time, "monotonic", side_effect=[1024., 1024., 1144.]):
            result = sim.simulate(inputs(), execute, setup_work=False)
        self.assertEqual(result["result"], "fail")
        self.assertEqual(execute.call_count, 1)

    def test_stderr_and_bad_exit_are_not_hidden(self):
        for value in (subprocess.CompletedProcess([], 1, b"", b"PRIVATE"),
                      subprocess.CompletedProcess([], 0, b"", b"PRIVATE")):
            result = sim.simulate(inputs(), mock.Mock(return_value=value), setup_work=False)
            self.assertEqual(result["result"], "fail")
            self.assertNotIn("PRIVATE", json.dumps(result))

    def test_configuration_has_no_host_defaults_and_setup_nooverwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            sim.setup(root)
            self.assertEqual((root / "status").read_bytes(), b"")
            self.assertEqual((root / "sources.list").read_bytes(), b"deb [trusted=yes] file:/nib/repo ./\n")
            with self.assertRaises(ValueError):
                sim.setup(root)
        config = sim.configuration()
        for setting in (b'Dir::Etc::parts "/work/etc/parts";', b'Dir::State::status "/work/status";',
                        b"#clear DPkg::Pre-Install-Pkgs;", b'Acquire::Retries "0";'):
            self.assertIn(setting, config)

    def test_preferences_are_explicit_private_empty_regular_file_and_directory(self):
        for mask in (0, 0o077):
            with self.subTest(umask=mask), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                previous = os.umask(mask)
                try:
                    sim.setup(root)
                finally:
                    os.umask(previous)
                preferences = root / "etc/preferences"
                fragments = root / "etc/preferences.d"
                info = preferences.lstat()
                self.assertTrue(stat.S_ISREG(info.st_mode))
                self.assertEqual((stat.S_IMODE(info.st_mode), info.st_nlink, info.st_size),
                                 (0o600, 1, 0))
                self.assertEqual(preferences.read_bytes(), b"")
                info = fragments.lstat()
                self.assertTrue(stat.S_ISDIR(info.st_mode))
                self.assertEqual(stat.S_IMODE(info.st_mode), 0o700)
                self.assertEqual(list(fragments.iterdir()), [])
                self.assertEqual((root / "apt.conf").read_bytes(), sim.configuration())
        for key, path in (("preferences", "/work/etc/preferences"),
                          ("preferencesparts", "/work/etc/preferences.d")):
            setting = f'Dir::Etc::{key} "{path}";'.encode()
            self.assertEqual(sim.configuration().count(setting), 1)

    def test_preexisting_or_misplaced_preferences_reject_without_overwrite(self):
        for name in ("etc/preferences", "etc/preferences.d", "preferences", "preferences.d"):
            for kind in ("file", "directory", "symlink", "fifo"):
                with self.subTest(name=name, kind=kind), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory).resolve()
                    target = root / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if kind == "file":
                        target.write_bytes(b"PRIVATE existing state")
                    elif kind == "directory":
                        target.mkdir()
                    elif kind == "symlink":
                        target.symlink_to(root / "absent")
                    else:
                        os.mkfifo(target)
                    before = target.lstat()
                    with self.assertRaises(ValueError):
                        sim.setup(root)
                    after = target.lstat()
                    self.assertEqual((before.st_ino, before.st_mode, before.st_size),
                                     (after.st_ino, after.st_mode, after.st_size))
                    self.assertFalse((root / "apt.conf").exists())
                    if kind == "file":
                        self.assertEqual(target.read_bytes(), b"PRIVATE existing state")

    def test_setup_rejects_missing_relative_link_or_non_directory_work(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "file").write_bytes(b"PRIVATE")
            (root / "link").symlink_to(root, target_is_directory=True)
            for work in (root / "missing", root / "file", root / "link", Path("relative-work")):
                with self.subTest(work=work), self.assertRaises(ValueError):
                    sim.setup(work)

    def test_preferences_creation_failure_is_sanitized_and_prevents_all_commands(self):
        real_setup, real_open, real_mkdir = sim.setup, os.open, Path.mkdir
        for name in ("etc/preferences", "etc/preferences.d"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                target = root / name

                def open_file(path, *args, **kwargs):
                    if Path(path) == target:
                        raise OSError("PRIVATE creation failure")
                    return real_open(path, *args, **kwargs)

                def mkdir(path, *args, **kwargs):
                    if path == target:
                        raise OSError("PRIVATE creation failure")
                    return real_mkdir(path, *args, **kwargs)

                execute = mock.Mock()
                with mock.patch.object(sim, "setup", side_effect=lambda _: real_setup(root)), \
                        mock.patch.object(os, "open", side_effect=open_file), \
                        mock.patch.object(Path, "mkdir", autospec=True, side_effect=mkdir):
                    result = sim.simulate(inputs(), execute)
                execute.assert_not_called()
                self.assertFalse(target.exists())
                self.assertEqual((result["stage"], result["result"], result["diagnostic"]["reason"]),
                                 ("setup", "fail", "setup-error"))
                self.assertNotIn("PRIVATE", json.dumps(result))


def container(tag):
    return {
        "Config": {"Image": tag, "User": "65534:65534", "WorkingDir": "/work", "Cmd": None,
                   "Entrypoint": ["/usr/bin/python3.12", "-I", "-S", "-B", "/nib/simulate.py"],
                   "Env": ["PATH=/usr/bin:/bin", "HOME=/nonexistent", "LANG=C", "LC_ALL=C"]},
        "HostConfig": {"NetworkMode": "none", "ReadonlyRootfs": True, "Privileged": False,
                       "IpcMode": "private", "CapDrop": ["ALL"], "CapAdd": None, "Binds": None,
                       "Devices": [], "DeviceRequests": None, "PortBindings": {},
                       "Memory": 1 << 30, "MemorySwap": 1 << 30,
                       "NanoCpus": 2_000_000_000, "PidsLimit": 64,
                       "Init": True, "SecurityOpt": ["no-new-privileges"],
                       "Tmpfs": {"/work": "rw,nosuid,nodev,noexec,size=128m,uid=65534,gid=65534,mode=0700"}},
        "Mounts": [], "State": {"Running": False, "OOMKilled": False, "ExitCode": 0},
    }


class ContainerTests(unittest.TestCase):
    def setUp(self):
        self.receipt = {"git_sha": "a" * 40, "archive_sha256": "c" * 64,
                        "config_sha256": "d" * 64, "layer_sha256": "e" * 64,
                        "image_tag": "nib-apt-simulation:" + "d" * 24,
                        "source_hashes": {runner.prep.FILES[-1]: "b" * 64}}
        self.guest = {"packages": [{"Package": "fixture", "Version": "1.0"}]}
        values = [b"apt 2.8.3 (amd64)\n", b"local index\n", plan()]
        self.result = sim.simulate(inputs(), mock.Mock(side_effect=[
            subprocess.CompletedProcess([], 0, value, b"") for value in values]), setup_work=False)
        self.calls = []

    def call(self, args, **kwargs):
        self.calls.append(args)
        out = b""
        if args[:2] == ["image", "inspect"]:
            out = json.dumps([{"Os": "linux", "Architecture": "amd64",
                               "Id": "not-assumed-to-be-config-digest",
                               "RootFS": {"Type": "layers", "Layers": ["sha256:" + "e" * 64]},
                               "Config": {"Labels": {"org.opencontainers.image.revision": "a" * 40}}}]).encode()
        elif args[:2] == ["container", "inspect"]:
            out = json.dumps([container(self.receipt["image_tag"])]).encode()
        elif args[:2] == ["container", "start"]:
            out = json.dumps(self.result).encode()
        return subprocess.CompletedProcess(args, 0, out, b"")

    def test_real_producer_to_validator_and_lifecycle_success(self):
        runner.validate_result(self.result, self.receipt, self.guest)
        result = runner.execute(b"verified fixture", self.receipt, self.guest, self.call)
        self.assertEqual((result["result"], result["cleanup"]), ("pass", "confirmed"))
        create = next(c for c in self.calls if c[:2] == ["container", "create"])
        for flag in ("--pull=never", "--network=none", "--read-only", "--ipc=private",
                     "--init", "--memory=1g", "--pids-limit=64", "--user=65534:65534"):
            self.assertIn(flag, create)
        self.assertEqual(sum(c[:2] == ["container", "rm"] for c in self.calls), 1)
        self.assertEqual(sum(c[:2] == ["image", "rm"] for c in self.calls), 1)

    def test_host_result_rejects_promoted_claim_or_incomplete_denominator(self):
        for key, value in (("installable_verified", True), ("target_packages", True),
                           ("git_sha", "c" * 40), ("plan", None)):
            changed = copy.deepcopy(self.result)
            changed[key] = value
            with self.subTest(key=key), self.assertRaises((ValueError, TypeError)):
                runner.validate_result(changed, self.receipt, self.guest)
        changed = copy.deepcopy(self.result)
        changed["plan"]["installed"] = 0
        with self.assertRaises(ValueError):
            runner.validate_result(changed, self.receipt, self.guest)

    def test_tools_target_uses_its_own_lock_not_the_guest_lock(self):
        path, scope = prep.target_spec("mkosi-tools")
        self.assertEqual(scope, "mkosi-build-tools-dependency-coverage")
        self.receipt["target_profile"] = "mkosi-tools"
        self.receipt["source_hashes"][path] = "f" * 64
        with self.assertRaises(ValueError):
            runner.validate_result(self.result, self.receipt, self.guest)
        self.result["guest_lock_sha256"] = "f" * 64
        runner.validate_result(self.result, self.receipt, self.guest)
        result = runner.execute(b"verified fixture", self.receipt, self.guest, self.call)
        self.assertEqual(result["target_profile"], "mkosi-tools")
        self.assertEqual((result["result"], result["cleanup"]), ("pass", "confirmed"))
        self.receipt["target_profile"] = "unreviewed"
        with self.assertRaises(ValueError):
            runner.validate_result(self.result, self.receipt, self.guest)

    def test_target_selection_binds_committed_source_and_rejects_paths(self):
        with mock.patch.object(prep, "committed", return_value=("a" * 40, {})) as committed:
            prep.target_sources("guest")
            committed.assert_called_once_with(())
            committed.reset_mock()
            prep.target_sources("mkosi-tools")
            committed.assert_called_once_with(("image/inputs/mkosi-tools-ubuntu2404.json",))
        for value in ("/tmp/lock", "../lock", "all", "", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                prep.target_spec(value)

    def test_evidence_preserves_failure_and_reports_simulation_only(self):
        result = {"git_sha": "a" * 40, "started_at": "2026-10-21T00:00:00Z",
                  "finished_at": "2026-10-21T00:00:01Z", "result": "fail", "stage": "resolve"}
        files = runner.evidence_files(result, "mkosi-tools")
        record = json.loads(files["run.json"])
        self.assertEqual(record["result"], "fail")
        self.assertEqual(record["git_sha"], result["git_sha"])
        self.assertEqual(record["execution"], "software")
        self.assertFalse(record["hardware_validated"])
        self.assertIn("--target mkosi-tools", record["command"])
        self.assertEqual(files["report.json"], files["output.log"])
        self.assertEqual(json.loads(files["output.log"]), result)

    def test_runtime_policy_rejects_network_mounts_privileges_or_credentials(self):
        for key, value in (("NetworkMode", "host"), ("Privileged", True), ("Binds", ["/PRIVATE:/x"]),
                           ("Memory", 0), ("PidsLimit", 0), ("DeviceRequests", [{}]),
                           ("IpcMode", "host"), ("Init", False)):
            row = container(self.receipt["image_tag"])
            row["HostConfig"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                runner.validate_container(row, self.receipt["image_tag"])
        row = container(self.receipt["image_tag"])
        row["Config"]["Env"].append("TOKEN=PRIVATE")
        with self.assertRaises(ValueError):
            runner.validate_container(row, self.receipt["image_tag"])

    def test_preexisting_image_is_not_removed(self):
        def call(args, **kwargs):
            self.calls.append(args)
            return subprocess.CompletedProcess(args, 0, b"existing", b"")
        result = runner.execute(b"fixture", self.receipt, self.guest, call)
        self.assertEqual((result["result"], result["cleanup"]), ("fail", "not-created"))
        self.assertEqual(len(self.calls), 1)

    def test_load_or_create_unknown_completion_is_never_confirmed_cleanup(self):
        for where in (["image", "load"], ["container", "create"]):
            self.calls.clear()
            def call(args, **kwargs):
                if args[:2] == where:
                    raise subprocess.TimeoutExpired(args, 1)
                return self.call(args, **kwargs)
            result = runner.execute(b"fixture", self.receipt, self.guest, call)
            self.assertEqual((result["result"], result["cleanup"]), ("fail", "unconfirmed"))
            self.assertTrue(any(c[:2] == ["image", "rm"] for c in self.calls))

    def test_attach_failure_removes_container_and_image_independently(self):
        def call(args, **kwargs):
            if args[:2] == ["container", "start"]:
                raise ValueError("PRIVATE overflow/cancellation")
            if args[:2] == ["container", "rm"]:
                self.calls.append(args)
                raise OSError("PRIVATE removal failed")
            return self.call(args, **kwargs)
        result = runner.execute(b"fixture", self.receipt, self.guest, call)
        self.assertEqual((result["result"], result["cleanup"]), ("fail", "unconfirmed"))
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertTrue(any(c[:2] == ["image", "rm"] for c in self.calls))

    def test_listing_failure_does_not_count_as_absence(self):
        def call(args, **kwargs):
            if args[:2] == ["container", "ls"]:
                raise subprocess.CalledProcessError(1, args, b"")
            return self.call(args, **kwargs)
        result = runner.execute(b"fixture", self.receipt, self.guest, call)
        self.assertEqual((result["result"], result["cleanup"]), ("fail", "unconfirmed"))

    def test_oom_nonzero_and_invalid_output_fail_with_cleanup(self):
        for kind in ("oom", "nonzero", "invalid-json"):
            self.calls.clear()
            def call(args, **kwargs):
                if args[:2] == ["container", "start"]:
                    if kind == "invalid-json":
                        return subprocess.CompletedProcess(args, 0, b"PRIVATE", b"")
                    if kind == "nonzero":
                        return subprocess.CompletedProcess(args, 125, b"", b"PRIVATE")
                if args[:2] == ["container", "inspect"] and kind == "oom":
                    row = container(self.receipt["image_tag"])
                    row["State"]["OOMKilled"] = True
                    return subprocess.CompletedProcess(args, 0, json.dumps([row]).encode(), b"")
                return self.call(args, **kwargs)
            result = runner.execute(b"fixture", self.receipt, self.guest, call)
            self.assertEqual((result["result"], result["cleanup"]), ("fail", "confirmed"))
            self.assertNotIn("PRIVATE", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
