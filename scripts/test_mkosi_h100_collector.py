# SPDX-License-Identifier: Apache-2.0
"""Offline synthetic packaging/export contracts; no collector or image execution."""
from contextlib import contextmanager, ExitStack
import copy
import errno
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest import mock

import mkosi_export as export
import mkosi_guest_network as helper
from test_mkosi_build import host, launcher, worker, ROOT, load
import test_mkosi_export as export_tests
from test_mkosi_export import proof, publication

base = host.base
SHA = "ab" * 20
NAME, MEMBER = next(iter(export.COLLECTOR_HELPERS.items()))


def elf():
    raw = bytearray(120)
    raw[:7] = b"\x7fELF\x02\x01\x01"
    struct.pack_into("<HHI", raw, 16, 2, 62, 1)
    struct.pack_into("<Q", raw, 32, 64)
    struct.pack_into("<HHH", raw, 52, 64, 56, 1)
    struct.pack_into("<I", raw, 64, 1)
    return bytes(raw)


def fixture_inputs():
    raw = {name: (ROOT / name).read_bytes() for name in export.NETWORK_BASE.union(export.COLLECTOR_EXTRA)}
    source = host.source_identity(SHA, raw, h100_collector=True)
    value = {"schema": "nib.h100-collector-inputs/v1", "source": source,
             "compiler": {"version": "go1.27.2", "compiler_sha256": base.digest(b"fixture compiler"),
                          "compiler_authenticated": False, "source_hashes": export.network_sources(source["sources"])},
             "files": {NAME: helper.identity(elf())}}
    return raw, source, value


class SourceTests(unittest.TestCase):
    def test_default_preparation_bytes_and_source_members_stay_opt_out(self):
        raw, _, _ = fixture_inputs()
        raw = {p: v for p, v in raw.items() if p in export.NETWORK_BASE}
        default = host.source_identity(SHA, raw)
        self.assertEqual(default, host.source_identity(SHA, raw, h100_collector=False))
        self.assertNotIn("profile", default)
        upstream = {host.mkosi.PATCH_TARGET: b"patch fixture", "mkosi/sandbox.py": b"sandbox fixture"}
        prepared = {"mkosi-overlay/" + host.mkosi.PATCH_TARGET: b"patched sandbox",
                    "nib/package-worker.py": b"package worker", "nib/confinement.py": b"confinement"}
        raw[host.BOOT_PREP] = b"def prepare(*args): return b'fixture boot inputs'\n"
        with mock.patch.object(base, "read", return_value=b"fixture archive"), \
                mock.patch.object(host, "runtime_preflight", return_value={}), \
                mock.patch.object(host.mkosi, "prepare", side_effect=lambda *a, **k: (dict(prepared), {})), \
                mock.patch.object(host.mkosi, "verify_source", side_effect=lambda *a: (None, dict(upstream))):
            args = (Path("/downloads"), Path("/archive"), SHA, raw, True, True, True, True)
            implicit = host.prepare(*args)
            explicit = host.prepare(*args, h100_collector=False)
        self.assertEqual(implicit, explicit)
        self.assertNotIn("profile", export.parsed(implicit[2]["artifact-inputs.json"]))
        self.assertFalse(any("collector" in p or "guest-network" in p for p in implicit[2]))
        self.assertEqual(len(default["sources"]), 42)

    def test_exact_source_closure_and_explicit_mutually_exclusive_profile(self):
        raw, source, _ = fixture_inputs()
        export.source_identity(source)
        self.assertEqual(len(raw), 54)
        self.assertEqual(len(export.COLLECTOR_EXTRA), 12)
        self.assertEqual(export.COLLECTOR_HELPERS, {"usr/libexec/nib-h100-appraisal": "collector/nib-h100-appraisal"})
        for option in ({}, {"guest_network": True}, {"guest_network": True, "h100_collector": True}):
            with self.subTest(option=option), self.assertRaises(ValueError): host.source_identity(SHA, raw, **option)
        for change in ("missing", "extra", "profile", "no-profile", "unsafe", "sha", "hash"):
            bad = copy.deepcopy(source)
            if change == "missing": bad["sources"].pop("guest/appraisal/claims.go")
            if change == "extra": bad["sources"]["guest/extra.go"] = base.digest(b"extra")
            if change == "profile": bad["profile"] = export.NETWORK_PROFILE
            if change == "no-profile": del bad["profile"]
            if change == "unsafe": bad["sources"]["../escape"] = bad["sources"].pop("go.mod")
            if change == "sha": bad["git_sha"] = "unknown"
            if change == "hash": bad["sources"]["go.mod"] = "unknown"
            with self.subTest(change=change), self.assertRaises(ValueError): export.source_identity(bad)

    def test_audits_require_same_revision_exact_members_and_unchanged_overlap(self):
        raw, _, _ = fixture_inputs()
        first = {p: raw[p] for p in export.NETWORK_BASE}
        second = {p: raw[p] for p in (*host.tools.FILES, *export.COLLECTOR_EXTRA)}
        for change in (None, "commit", "overlap", "missing", "extra"):
            candidate = dict(second)
            if change == "overlap": candidate[host.tools.FILES[0]] = b"changed"
            if change == "missing": candidate.pop("guest/appraisal/claims.go")
            if change == "extra": candidate["guest/appraisal/extra.go"] = b"extra"
            with self.subTest(change=change), mock.patch.object(host.tools, "committed", side_effect=[
                    (SHA, first), ("cd" * 20 if change == "commit" else SHA, candidate)]) as audit:
                if change is None:
                    self.assertEqual(host.committed_sources(True, True, True, True, h100_collector=True), (SHA, raw))
                else:
                    with self.assertRaises(ValueError): host.committed_sources(True, True, True, True, h100_collector=True)
                self.assertEqual(audit.call_args.args, (export.COLLECTOR_EXTRA,))

    def test_flag_requires_full_export_and_compiler_without_network_profile(self):
        good = ["--install-driver", "--verity-root", "--boot-uki", "--stage-boot", "--h100-collector"]
        self.assertEqual(worker.options(good), (True,) * 5)
        for bad in (good[::-1], good[1:], good[:-2] + good[-1:], good + [good[-1]], good + ["--guest-network"]):
            with self.assertRaises(ValueError): worker.options(bad)
        with mock.patch.object(host.os, "readlink", return_value="ns:[1]"), \
                mock.patch.object(host.probe.ownership, "prefix", return_value=[]):
            args = (Path("/root"), Path("/payload"), Path("/out"), {}, True, True, True, True, True)
            self.assertEqual(host.command(*args, h100_collector=True), host.command(*args) + ["--h100-collector"])
            self.assertEqual(host.command(*args, h100_collector=False), host.command(*args))
            for option in ({"h100_collector": 1}, {"h100_collector": True, "guest_network": True}):
                with self.assertRaises(ValueError): host.command(*args, **option)
        for entry in (host, launcher):
            for flags in (["--h100-collector"], ["--h100-collector", "--go", "/compiler"],
                          ["--h100-collector", "--guest-network", "--go", "/compiler"]):
                with mock.patch.object(entry.sys, "argv", ["host", "--downloads", "/unused", "--archive",
                        "/unused", "--output", "/unused", *flags]), self.assertRaises(ValueError): entry.main()

    def execute(self, argv, **kwargs):
        self.assertGreater(kwargs["timeout"], 0)
        self.assertLessEqual(kwargs["timeout"], 120)
        self.assertEqual(kwargs["max_output_bytes"], 65536)
        self.assertEqual(kwargs["max_stderr_bytes"], 65536)
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        env = kwargs["env"]
        self.assertNotIn("PRIVATE_SENTINEL", env)
        self.assertEqual([env[k] for k in ("GOOS", "GOARCH", "GOAMD64", "CGO_ENABLED")], ["linux", "amd64", "v1", "0"])
        self.assertTrue(all(env[k] == "off" for k in ("GOENV", "GOWORK", "GOPROXY", "GOSUMDB")))
        self.assertEqual((env["GOTOOLCHAIN"], env["GOFLAGS"]), ("local", "-mod=readonly"))
        result = b""
        if argv[1] == "env":
            self.assertEqual(argv[1:], ["env", "GOVERSION"])
            result = b"go1.27.2\n"
        else:
            self.assertEqual(argv[-2:], ["-tags=nibh100appraisal", "./guest/appraisal/cmd/nib-h100-appraisal"])
            if argv[1] == "list":
                result = b"\n".join(b"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/guest/" + p
                                    for p in (b"nftables", b"appraisal", b"appraisal/cmd/nib-h100-appraisal"))
            else:
                self.assertEqual(argv[1:6], ["build", "-trimpath", "-buildvcs=false", "-ldflags=-buildid=", "-o"])
                Path(argv[6]).write_bytes(elf())
        return subprocess.CompletedProcess(argv, 0, result, b"")

    def test_deterministic_staging_and_exact_tag_dependency_compiler_receipts(self):
        raw, source, _ = fixture_inputs()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            go = root / "go"
            go.write_bytes(b"fixture compiler")
            results = []
            with mock.patch.object(base.processes, "execute_process_group", side_effect=self.execute) as execute:
                for name in ("first", "second"):
                    results.append(helper.compile_helpers(go, root / name, raw, source, base, export))
            self.assertEqual(execute.call_count, 6)
            self.assertEqual(results[0], results[1])
            payload, inputs = results[0]
            self.assertEqual(set(payload), {"guest-network.py", "collector-inputs.json", MEMBER})
            self.assertEqual(payload[MEMBER], elf())
            self.assertEqual(inputs["compiler"]["source_hashes"], export.network_sources(source["sources"]))
            self.assertEqual(inputs["compiler"]["compiler_sha256"], base.digest(go.read_bytes()))
            self.assertFalse(inputs["compiler"]["compiler_authenticated"])
            export.collector_inputs(inputs, source)

    def test_compilation_rejects_mutation_failure_deadline_or_unexpected_imports(self):
        for change in ("bytes", "compiler", "tree", "imports", "stderr", "version", "exit", "timeout", "deadline", "elf"):
            raw, source, _ = fixture_inputs()
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
                root = Path(tmp).resolve()
                go = root / "go"
                go.write_bytes(b"fixture compiler")
                if change == "bytes": raw["guest/appraisal/claims.go"] += b"\n"
                def execute(argv, **kwargs):
                    if change == "timeout": raise subprocess.TimeoutExpired(argv, 1)
                    result = self.execute(argv, **kwargs)
                    if change == "stderr": result.stderr = b"PRIVATE_SENTINEL"
                    if change == "exit": result.returncode = 1
                    if change == "version" and argv[1] == "env": result.stdout = b"go1.99\n"
                    if change == "imports" and argv[1] == "list": result.stdout += b"\nthird.party/module"
                    if argv[1] == "build":
                        if change == "compiler": go.write_bytes(b"replaced")
                        if change == "tree": (kwargs["cwd"] / "go.mod").write_bytes(b"changed")
                        if change == "elf": Path(argv[6]).write_bytes(b"not static")
                    return result
                stack.enter_context(mock.patch.object(base.processes, "execute_process_group", side_effect=execute))
                if change == "deadline": stack.enter_context(mock.patch.object(helper.time, "monotonic", side_effect=[0, 121]))
                with self.assertRaises((ValueError, subprocess.SubprocessError)):
                    helper.compile_helpers(go, root / "build", raw, source, base, export)

    def test_elf_inspection_rejects_dynamic_wrong_arch_and_truncated_headers(self):
        helper.static_collector(elf())
        for change in ("dynamic", "interpreter", "arch", "type", "offset", "entry", "count", "load", "short"):
            bad = bytearray(elf())
            if change in ("dynamic", "interpreter", "load"):
                struct.pack_into("<I", bad, 64, {"dynamic": 2, "interpreter": 3, "load": 0}[change])
            if change == "arch": struct.pack_into("<H", bad, 18, 183)
            if change == "type": struct.pack_into("<H", bad, 16, 3)
            if change == "offset": struct.pack_into("<Q", bad, 32, (1 << 64) - 1)
            if change == "entry": struct.pack_into("<H", bad, 54, 0)
            if change == "count": struct.pack_into("<H", bad, 56, 129)
            if change == "short": del bad[63:]
            with self.subTest(change=change), self.assertRaises(ValueError): helper.static_collector(bytes(bad))

    def test_host_compile_failure_and_cancellation_remove_private_payload_and_pending(self):
        raw, _, _ = fixture_inputs()
        for cancellation in (False, True):
            with self.subTest(cancellation=cancellation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                output, go = root / "evidence", root / "go"
                go.write_bytes(b"fixture compiler")
                private = []
                def reject(argv, **kwargs):
                    private.append(kwargs["cwd"].parent.parent)
                    if cancellation: raise KeyboardInterrupt
                    return subprocess.CompletedProcess(argv, 1, b"", b"PRIVATE_SENTINEL")
                with mock.patch.object(host, "prepare", return_value=({"receipt.json": b"{}"}, {}, {}, {}, {})) as prepare, \
                        mock.patch.object(base.processes, "execute_process_group", side_effect=reject), \
                        mock.patch.object(host.probe, "materialize") as materialize:
                    args = (root, root / "unused", output, SHA, raw, True, True, True, True, True, True)
                    if cancellation:
                        with self.assertRaises(KeyboardInterrupt): host.run(*args, h100_collector=True, go=go)
                    else:
                        self.assertEqual(host.run(*args, h100_collector=True, go=go), 1)
                    self.assertTrue(prepare.call_args.kwargs["h100_collector"])
                materialize.assert_not_called()
                self.assertFalse(any(p.exists() for p in private))
                self.assertFalse(any(p.exists() for p in export.paths(output)))
                if not cancellation:
                    report = export.parsed((output / "result.json").read_bytes())
                    self.assertEqual((report["result"], report["stage"]), ("fail", "guest-helper-compilation"))
                    self.assertTrue(report["payload_removed"])
                    self.assertNotIn("PRIVATE_SENTINEL", json.dumps(report))

    def test_launcher_and_consumer_forward_only_explicit_collector_selection(self):
        validator = load("host/validate-native-boot-export.py")
        raw, source, _ = fixture_inputs()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp).resolve() / "evidence"
            args = ["host", "--downloads", "/downloads", "--archive", "/archive", "--output", str(output),
                    "--compile-driver", "--install-driver", "--guest-runtime", "--verity-root", "--boot-uki",
                    "--export-boot", "--h100-collector", "--go", "/compiler"]
            @contextmanager
            def receipt(*args): yield 99
            with mock.patch.object(launcher.sys, "argv", args), \
                    mock.patch.object(launcher, "identity", return_value=(os.getuid(), os.getgid())), \
                    mock.patch.object(launcher, "receipt_file", receipt), \
                    mock.patch.object(launcher.os, "write", side_effect=lambda fd, raw: len(raw)), \
                    mock.patch.object(launcher, "execute", return_value={"result": "pass"}) as execute, \
                    mock.patch.object(launcher, "finish_export") as finish:
                self.assertEqual(launcher.main(), 0)
            self.assertEqual(execute.call_args.args[2][-3:], ["--h100-collector", "--go", "/compiler"])
            self.assertNotIn("--guest-network", execute.call_args.args[2])
            self.assertEqual(finish.call_args.kwargs, {"h100_collector": True})
        with mock.patch.object(validator.sys, "argv", ["validator", "--build-result", "success", "--h100-collector"]), \
                mock.patch.object(validator.native.probe.native, "preflight"), \
                mock.patch.object(validator.native, "committed_sources", return_value=(SHA, raw)) as audit, \
                mock.patch.object(validator, "committed_validator", return_value={}), \
                mock.patch.object(validator, "validate", return_value="pass") as validate:
            self.assertEqual(validator.main(), 0)
        audit.assert_called_once_with(True, True, True, True, h100_collector=True)
        self.assertEqual(validate.call_args.args[2], source)

    def test_host_requires_original_compilation_bytes_in_worker_proof(self):
        raw, source, inputs = fixture_inputs()
        readlink = os.readlink
        for changed in (False, True):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                output, go = root / "evidence", root / "go"
                go.write_bytes(b"fixture compiler")
                images = {"root.raw": b"synthetic GPT", "nib.efi": b"synthetic UKI"}
                report, _ = proof(images)
                report["worker"]["artifact_stage"]["source"] = source
                receipt = copy.deepcopy(inputs)
                if changed: receipt["files"][NAME] = helper.identity(b"substituted collector")
                report["worker"]["h100_collector"] = {
                    "schema": "nib.h100-collector-composition/v1", "result": "pass", "inputs": receipt,
                    "collector_executed": False, "services_started": False,
                    "readiness_verified": False, "confidentiality_verified": False}
                def execute(argv, **kwargs):
                    if argv[0] == str(go): return self.execute(argv, **kwargs)
                    directory = Path(argv[argv.index("/out") - 1])
                    base.publish(directory / "boot-stage", images)
                    (directory / "result.json").write_bytes(export.encoded(report["worker"]))
                    return subprocess.CompletedProcess(argv, 0, b"", b"")
                with mock.patch.object(host, "prepare", return_value=(
                        {"receipt.json": b"{}", "runtime-hook-review.json": b"{}"}, {}, {}, {}, {})), \
                        mock.patch.object(host.prep, "assemble", return_value=({"receipt.json": b"{}"}, {})), \
                        mock.patch.object(host.probe.ownership, "host_inputs", return_value=({}, {})), \
                        mock.patch.object(host.probe.ownership, "prefix", return_value=[]), \
                        mock.patch.object(host.os, "readlink", side_effect=lambda p, **k:
                            "ns:[1]" if str(p).startswith("/proc/self/ns/") else readlink(p, **k)), \
                        mock.patch.object(host.probe, "materialize", side_effect=lambda files, path: path.mkdir()), \
                        mock.patch.object(base.processes, "execute_process_group", side_effect=execute):
                    self.assertEqual(host.run(root, root / "unused", output, SHA, raw,
                        True, True, True, True, True, True, h100_collector=True, go=go), 1 if changed else 0)
                observed = export.parsed((output / "result.json").read_bytes())
                self.assertEqual(observed["result"], "fail" if changed else "pass")
                self.assertEqual(export.paths(output)[0].exists(), not changed)
                self.assertFalse(export.paths(output)[1].exists())


class CompositionTests(unittest.TestCase):
    @contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "root"
            payload = root.parent / "payload"
            root.mkdir(mode=0o700)
            _, source, inputs = fixture_inputs()
            base.publish(payload, {MEMBER: elf(), "collector-inputs.json": export.encoded(inputs)})
            with mock.patch.object(helper, "ROOTFS", root), mock.patch.object(helper, "PAYLOAD", payload):
                yield root, payload, source, inputs

    def compose(self, root, payload, source):
        return helper.compose_collector(export, base.directory, source, root, payload)

    def test_exact_one_file_no_activation_or_execution_under_private_umasks(self):
        reports = []
        for mask in (0o022, 0o027, 0o077):
            with self.subTest(mask=mask), self.fixture() as (root, payload, source, inputs), \
                    mock.patch.object(base.processes, "execute_process_group", side_effect=AssertionError("no child")):
                old = os.umask(mask)
                try:
                    report = self.compose(root, payload, source)
                    self.assertEqual(os.umask(mask), mask)
                finally:
                    os.umask(old)
                reports.append(export.encoded(report))
                self.assertEqual({p.relative_to(root).as_posix() for p in root.rglob("*")}, {"usr", "usr/libexec", NAME})
                self.assertTrue(all(p.stat().st_mode & 0o777 == 0o755 for p in root.rglob("*")))
                self.assertEqual((root / NAME).read_bytes(), elf())
                self.assertEqual(report["inputs"], inputs)
                self.assertTrue(all(report[k] is False for k in
                    ("collector_executed", "services_started", "readiness_verified", "confidentiality_verified")))
                export.collector_proof(report, source)
        self.assertEqual(reports, [reports[0]] * 3)

    def test_collisions_and_unsafe_paths_preserve_existing_files(self):
        for change in ("collision", "leaf-link", "parent-link", "writable-parent", "writable-root", "payload-link",
                       "payload-hardlink", "payload-fifo", "payload-parent", "payload-changed", "receipt-link",
                       "receipt-writable", "receipt-oversize", "wrong-source", "alternate-root"):
            with self.subTest(change=change), self.fixture() as (root, payload, source, _):
                sentinel = root.parent / "sentinel"
                sentinel.write_bytes(b"keep")
                if change in ("collision", "leaf-link"):
                    (root / NAME).parent.mkdir(parents=True)
                    if change == "collision": (root / NAME).write_bytes(b"keep")
                    else: (root / NAME).symlink_to(sentinel)
                if change == "parent-link": (root / "usr").symlink_to(payload, target_is_directory=True)
                if change == "writable-parent": (root / "usr").mkdir(mode=0o777); (root / "usr").chmod(0o777)
                if change == "writable-root": root.chmod(0o777)
                if change in ("payload-link", "payload-hardlink", "payload-fifo"):
                    (payload / MEMBER).unlink()
                    if change == "payload-link": (payload / MEMBER).symlink_to(sentinel)
                    if change == "payload-hardlink": os.link(sentinel, payload / MEMBER)
                    if change == "payload-fifo": os.mkfifo(payload / MEMBER)
                if change == "payload-parent":
                    (payload / "collector").rename(payload / "other")
                    (payload / "collector").symlink_to(payload / "other", target_is_directory=True)
                if change == "payload-changed": (payload / MEMBER).write_bytes(b"changed")
                if change == "receipt-link":
                    (payload / "collector-inputs.json").unlink()
                    (payload / "collector-inputs.json").symlink_to(sentinel)
                if change == "receipt-writable": (payload / "collector-inputs.json").chmod(0o666)
                if change == "receipt-oversize": (payload / "collector-inputs.json").write_bytes(b"x" * 65537)
                if change == "wrong-source": source = {**source, "git_sha": "cd" * 20}
                if change == "alternate-root": root = root.parent
                with self.assertRaises((ValueError, OSError)): self.compose(root, payload, source)
                self.assertEqual(sentinel.read_bytes(), b"keep")
                if change == "collision": self.assertEqual((root / NAME).read_bytes(), b"keep")

    def test_proof_rejects_missing_mixed_or_inflated_claims(self):
        with self.fixture() as (root, payload, source, _):
            composition = self.compose(root, payload, source)
        report, default = proof({"root.raw": b"synthetic GPT", "nib.efi": b"synthetic UKI"})
        report["worker"]["artifact_stage"]["source"] = source
        report["worker"]["h100_collector"] = composition
        export.validate_proof(report, source)
        for key in composition:
            bad = copy.deepcopy(report)
            del bad["worker"]["h100_collector"][key]
            with self.subTest(key=key), self.assertRaises(ValueError): export.validate_proof(bad, source)
        for change in ("missing", "mixed", "source", "closure", "compiler", "files", "activation", "claim", "bool"):
            bad = copy.deepcopy(report)
            value = bad["worker"]["h100_collector"]
            if change == "missing": del bad["worker"]["h100_collector"]
            if change == "mixed": bad["worker"]["guest_network"] = {}
            if change == "source": value["inputs"]["source"]["git_sha"] = "cd" * 20
            if change == "closure": value["inputs"]["compiler"]["source_hashes"].pop("go.mod")
            if change == "compiler": value["inputs"]["compiler"]["compiler_authenticated"] = True
            if change == "files": value["inputs"]["files"]["etc/systemd/activation"] = helper.identity(b"extra")
            if change == "activation": value["collector_executed"] = True
            if change == "claim": value["confidentiality_verified"] = True
            if change == "bool": value["readiness_verified"] = 0
            with self.subTest(change=change), self.assertRaises(ValueError): export.validate_proof(bad, source)
        report["worker"]["artifact_stage"]["source"] = default
        with self.assertRaises(ValueError): export.validate_proof(report, default)

    def test_descriptor_cleanup_after_success_errors_and_cancellation(self):
        original_open, original_dup = os.open, os.dup
        for failure in (None, "copy", "cancel", "mode", "receipt"):
            with self.subTest(failure=failure), self.fixture() as (root, payload, source, _), ExitStack() as stack:
                descriptors = set()
                def opened(*args, **kwargs):
                    fd = original_open(*args, **kwargs)
                    descriptors.add(fd)
                    return fd
                def duplicated(*args):
                    fd = original_dup(*args)
                    descriptors.add(fd)
                    return fd
                stack.enter_context(mock.patch.object(os, "open", side_effect=opened))
                stack.enter_context(mock.patch.object(os, "dup", side_effect=duplicated))
                if failure in ("copy", "cancel"):
                    stack.enter_context(mock.patch.object(export, "copy",
                        side_effect=KeyboardInterrupt if failure == "cancel" else ValueError("fixture")))
                if failure == "mode": stack.enter_context(mock.patch.object(os, "fchmod", side_effect=OSError("fixture")))
                if failure == "receipt": (payload / "collector-inputs.json").write_bytes(b"{}")
                if failure:
                    with self.assertRaises((ValueError, OSError, KeyboardInterrupt)): self.compose(root, payload, source)
                else: self.compose(root, payload, source)
                self.assertTrue(descriptors)
                for fd in descriptors:
                    with self.assertRaises(OSError) as error: os.fstat(fd)
                    self.assertEqual(error.exception.errno, errno.EBADF)

    def test_private_export_binds_original_receipts_and_requires_selected_consumer(self):
        validator = load("host/validate-native-boot-export.py")
        fixture = export_tests.ExportTests()
        for selected in (False, True):
            with self.subTest(selected=selected), self.fixture() as (root, payload, source, _), \
                    fixture.fixture() as (evidence, stage, raws, report, default):
                report["worker"]["h100_collector"] = self.compose(root, payload, source)
                report["worker"]["artifact_stage"]["source"] = source
                original = export.encoded(report)
                (evidence / "result.json").write_bytes(original)
                fixture.prepare(evidence, stage, report)
                bundle = export.finish(evidence, base, publication, source)
                self.assertEqual({name: (bundle / name).read_bytes() for name in raws}, raws)
                self.assertEqual((bundle / "result.json").read_bytes(), original)
                manifest = export.parsed((bundle / "manifest.json").read_bytes())
                self.assertEqual(manifest["source"], source)
                self.assertEqual(manifest["receipts"]["result.json"], helper.identity(original))
                validator_source = {name: base.digest(b"fixture") for name in (validator.SCRIPT, validator.WORKFLOW)}
                metadata = evidence.parent / "metadata"
                result = validator.validate(evidence, metadata, source if selected else default,
                    validator_source, "success", export, publication)
                self.assertEqual(result, "pass" if selected else "fail")
                self.assertFalse(any(p.exists() for p in export.paths(evidence)))
                self.assertTrue(all(p.suffix == ".json" for p in metadata.iterdir()))

    def test_changed_receipt_or_reaudited_source_removes_pending_export(self):
        fixture = export_tests.ExportTests()
        for change in ("receipt", "source"):
            with self.subTest(change=change), self.fixture() as (root, payload, source, _), \
                    fixture.fixture() as (evidence, stage, _, report, _):
                report["worker"]["h100_collector"] = self.compose(root, payload, source)
                report["worker"]["artifact_stage"]["source"] = source
                (evidence / "result.json").write_bytes(export.encoded(report))
                fixture.prepare(evidence, stage, report)
                if change == "receipt":
                    report["worker"]["h100_collector"]["inputs"]["files"][NAME] = helper.identity(b"changed")
                    (evidence / "result.json").write_bytes(export.encoded(report))
                else:
                    source = copy.deepcopy(source)
                    source["sources"]["guest/appraisal/claims.go"] = base.digest(b"changed")
                with self.assertRaises(ValueError): export.finish(evidence, base, publication, source)
                self.assertFalse(any(p.exists() for p in export.paths(evidence)))


if __name__ == "__main__":
    unittest.main()
