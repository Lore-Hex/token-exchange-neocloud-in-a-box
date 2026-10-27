# SPDX-License-Identifier: Apache-2.0
"""Opt-in topology integration only: synthetic bytes, no guest/controller run."""
import copy
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

import test_debug_boot as legacy

host, wrapper, qemu, uki, boot = legacy.host, legacy.wrapper, legacy.qemu, legacy.uki, legacy.boot
ROOT = legacy.ROOT
PROFILE = "debug-topology/v1"


def producer():
    value = copy.deepcopy(legacy.PRODUCER)
    value["source"]["profile"] = "guest-network/v1"
    value["debug_source"]["profile"] = PROFILE
    return value


def worker():
    probe, image, original = map(boot.identity, (b"probe", b"debug image", b"original"))
    source = producer()
    return {"schema": "nib.debug-topology-boot/v1", "profile": PROFILE, "result": "pass", "stage": "complete",
        "source": source["source"], "debug_source": source["debug_source"], "boot_verified": True,
        "confidentiality_verified": False, "topology_down_only_verified": True, "root_hash": "a" * 64,
        "input_files": {"nib.efi": original}, "compiler": {"probe": probe},
        "assembly": {"schema": "nib.debug-topology-uki/v1", "profile": PROFILE, "result": "pass",
                     "signed": False, "confidentiality_verified": False, "probe": probe,
                     "original_uki": original, "root_hash": "a" * 64, "uki": {"image": image}},
        "qemu": {"schema": "nib.debug-topology-qemu/v1", "profile": PROFILE, "result": "pass",
                 "boot_verified": True, "confidentiality_verified": False, "topology_down_only_verified": True,
                 "debug_uki": image,
                 "proof": {"initrd": True, "root": True, "complete": True, "guest_failure": None,
                           "protocol_error": None, "topology_down_only": True},
                 "terminal": {"exit_code": 0, "stdout_bytes": 0, "stderr_bytes": 0,
                              "process_group_cleaned": True, "failure_kind": None}}}


class ProfileTests(unittest.TestCase):
    def test_opt_in_requires_existing_network_profile_and_workflow_stays_dark(self):
        self.assertEqual(host.topology_options(False, False), {})
        self.assertEqual(host.topology_options(True, False), {"guest_network": True})
        self.assertEqual(host.topology_options(True, True), {"guest_network": True, "debug_topology": True})
        for args in ((False, True), (1, True), (True, 1)):
            with self.assertRaises(ValueError): host.topology_options(*args)
        workflow = legacy.yaml.safe_load((ROOT / host.WORKFLOW).read_bytes())
        option = workflow[True]["workflow_dispatch"]["inputs"]["debug_topology"]
        self.assertEqual(option["type"], "boolean")
        self.assertIs(option["default"], False)
        self.assertEqual(qemu.TIMEOUT, 180)
        self.assertEqual(wrapper.TIMEOUT, 900)
        self.assertEqual(qemu.MAX_PROOF_BYTES, 62)

    def test_exact_source_union_and_old_profile_unchanged(self):
        names = (*host.tools.FILES, *host.EXTRA, host.WORKFLOW)
        debug = {name: (ROOT / name).read_bytes() for name in (*names, *host.TOPOLOGY_GUEST)}
        self.assertEqual((len(set(names)), len(debug)), (30, 37))
        self.assertLessEqual(len(host.EXTRA + host.TOPOLOGY_GUEST), 32)
        expected = host.debug_identity("a" * 40, debug, True)
        self.assertEqual(expected["profile"], PROFILE)
        self.assertEqual(set(expected["sources"]), set(debug))
        self.assertNotIn("profile", host.debug_identity("a" * 40, {p: debug[p] for p in names}))
        for name in host.TOPOLOGY_GUEST:
            with self.assertRaises(ValueError): host.debug_identity("a" * 40, {p: v for p, v in debug.items() if p != name}, True)
        with self.assertRaises(ValueError): host.debug_identity("a" * 40, {**debug, "ambient": b"x"}, True)
        with mock.patch.object(host.native, "committed_sources", return_value=("a" * 40, {})) as native, \
             mock.patch.object(host.tools, "committed", return_value=("a" * 40, debug.copy())) as committed, \
             mock.patch.object(host, "workflow_source", return_value=debug[host.WORKFLOW]):
            self.assertEqual(host.committed(True, True)[2], debug)
        native.assert_called_once_with(True, True, True, True, guest_network=True)
        committed.assert_called_once_with(host.EXTRA + host.TOPOLOGY_GUEST)

    def test_exact_stage_flag_and_versioned_inputs_reject_downgrade(self):
        debug = {p: (ROOT / p).read_bytes() for p in host.GUEST}
        old = host.initrd_units(debug, False)
        selected = host.initrd_units(debug, True, True)
        self.assertEqual(selected["nib-debug-boot-stage.service"], old["nib-debug-boot-stage.service"].replace(
            b" --stage-initrd\n", b" --stage-initrd-topology\n"))
        self.assertEqual(selected["10-nib-debug-boot.conf"], old["10-nib-debug-boot.conf"])
        with self.assertRaises(ValueError): host.initrd_units(debug, False, True)
        for module, schema, fields in ((qemu, "nib.debug-qemu-input", {"runtime_members": {}, "debug_uki": {}}),
                (uki, "nib.debug-uki-input", {"root_hash": "a" * 64, "original": {}, "probe": {}, "units": {}})):
            old = {"schema": schema + "/v1", **fields}
            new = {"schema": schema + "/v2", "profile": PROFILE, **fields}
            self.assertFalse(module.input_profile(old))
            self.assertTrue(module.input_profile(new))
            for bad in ({**old, "profile": PROFILE}, {**new, "profile": "unknown"},
                        {**new, "schema": schema + "/v3"}, {**new, "extra": True},
                        {p: v for p, v in new.items() if p != "profile"}, None):
                with self.assertRaises(ValueError): module.input_profile(bad)

    def test_private_compile_has_exact_sources_imports_and_static_flags(self):
        prefix = b"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/guest/"
        for bad in (None, "imports", "source", "compiler", "version", "stderr"):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary).resolve()
                compiler = directory / "go"
                compiler.write_bytes(b"synthetic compiler")
                sources = {p: (ROOT / p).read_bytes() for p in ("go.mod", "go.sum", *host.GUEST, *host.TOPOLOGY_GUEST)}
                sources["unselected"] = b"not compiled"
                def execute(args, **kwargs):
                    self.assertEqual(kwargs["timeout"], 120)
                    self.assertEqual(kwargs["env"]["CGO_ENABLED"], "0")
                    self.assertEqual(kwargs["env"]["GOPROXY"], "off")
                    self.assertEqual(kwargs["cwd"], directory / "source")
                    if args[1] == "env": output = b"go1.0\n" if bad == "version" else b"go1.27.2\n"
                    elif args[1] == "list":
                        self.assertIn("-tags=nibdebugtopology", args)
                        output = b"\n".join(prefix + x for x in (b"debugtopology", b"debugboot", b"debugboot/cmd/nib-debug-topology"))
                        if bad == "imports": output += b"\nthird-party"
                    else:
                        self.assertIn("-trimpath", args)
                        self.assertIn("-tags=nibdebugtopology", args)
                        (directory / "probe").write_bytes(b"\x7fELF\x02\x01\x01synthetic")
                        if bad == "source": (directory / "source" / host.TOPOLOGY_GUEST[0]).write_bytes(b"changed")
                        if bad == "compiler": compiler.write_bytes(b"changed")
                        output = b""
                    return subprocess.CompletedProcess(args, 0, output, b"error" if bad == "stderr" else b"")
                with mock.patch.object(host.base.processes, "execute_process_group", side_effect=execute):
                    if bad is None:
                        _, receipt = host.compile_probe(compiler, directory, sources, True)
                        self.assertEqual(set(receipt["source_hashes"]), set(sources) - {"unselected"})
                        self.assertEqual(len(receipt["source_hashes"]), 20)
                        self.assertFalse(receipt["compiler_authenticated"])
                    else:
                        with self.assertRaises(ValueError): host.compile_probe(compiler, directory, sources, True)

    def test_independent_capture_binds_both_profiles_and_cleanup(self):
        for bad in (None, "image", "debug", "self"):
            value = producer()
            if bad == "image": value["source"].pop("profile")
            if bad == "debug": value["debug_source"].pop("profile")
            if bad == "self": value["debug_source"]["sources"][wrapper.SCRIPT] = "b" * 64
            child = mock.Mock(returncode=0)
            with mock.patch.object(wrapper.subprocess, "Popen", return_value=child) as popen, \
                 mock.patch.object(wrapper.base.processes, "bounded_output", return_value=(json.dumps(value).encode(), b"")), \
                 mock.patch.object(wrapper.base.processes, "cleanup_process_group") as cleanup:
                if bad is None: self.assertEqual(wrapper.capture_producer(1001, 1002, True, True), value)
                else:
                    with self.assertRaises(ValueError): wrapper.capture_producer(1001, 1002, True, True)
            self.assertEqual(popen.call_args.args[0][-3:], ["--source-only", "--guest-network", "--debug-topology"])
            self.assertEqual(popen.call_args.kwargs["user"], 1001)
            cleanup.assert_called_once_with(child)
        with mock.patch.object(wrapper.subprocess, "Popen") as spawn:
            with self.assertRaises(ValueError): wrapper.capture_producer(1001, 1002, False, True)
        spawn.assert_not_called()

    def test_profiled_assembly_preserves_original_bytes_and_binds_supplement(self):
        with legacy.UkiTests().fixture() as (_, config):
            config.update(schema="nib.debug-uki-input/v2", profile=PROFILE)
            debug = {p: (ROOT / p).read_bytes() for p in host.GUEST}
            for name, raw in host.initrd_units(debug, True, True).items():
                (uki.PAYLOAD / name).write_bytes(raw)
                config["units"][name] = boot.identity(raw)
            parts = {".initrd": b"original initrd", ".osrel": b"ID=ubuntu\n"}
            sections = {}
            def operation(_processes, _api, name):
                if name == "cpio":
                    staged = boot.snapshot()
                    self.assertEqual({p for p, item in staged.items() if item["kind"] == "file"},
                                     {"usr/libexec/nib-debug-boot", *uki.UNITS.values()})
                    (boot.WORK / "initrd.cpio").write_bytes(legacy.fixture_cpio(boot.WORK / "tree", staged))
                elif name == "ukify":
                    self.assertTrue((boot.WORK / "initrd.cpio").read_bytes().startswith(parts[".initrd"]))
                    self.assertEqual((boot.WORK / "cmdline").read_bytes(), boot.cmdline(config["root_hash"]) + uki.SUFFIX)
                    (boot.WORK / "nib.efi").write_bytes(b"distinct debug image")
                    sections.update(schema="nib.uki-sections/v1", image=boot.identity(b"distinct debug image"), signed=False,
                        sections={".initrd": boot.identity((boot.WORK / "initrd.cpio").read_bytes()),
                                  ".cmdline": boot.identity((boot.WORK / "cmdline").read_bytes())})
                return subprocess.CompletedProcess([], 0, json.dumps(sections).encode(), b"")
            with mock.patch.object(uki, "original", return_value=parts), \
                 mock.patch.object(uki, "operation", side_effect=operation), \
                 mock.patch.object(boot, "inspect_uki", side_effect=lambda _pe: sections):
                report = uki.build(boot, None, None, config, {"boot_verified": False, "confidentiality_verified": False, "signed": False})
            self.assertEqual((report["schema"], report["profile"], report["result"]), ("nib.debug-topology-uki/v1", PROFILE, "pass"))
            self.assertEqual(report["probe"], config["probe"])
            self.assertEqual(report["original_uki"], config["original"]["uki"]["image"])
            self.assertFalse(report["boot_verified"] or report["confidentiality_verified"] or report["signed"])


class ProtocolTests(unittest.TestCase):
    def receive(self, chunks, topology=True):
        connection, listener = mock.Mock(), mock.Mock()
        connection.recv.side_effect = chunks
        listener.accept.return_value = (connection, None)
        stop = threading.Event(); stop.set()
        state = {"initrd": False, "root": False, "complete": False, "guest_failure": None, "protocol_error": None}
        qemu.receive(listener, stop, time.monotonic() + 1, state, topology)
        connection.close.assert_called_once()
        return state

    def test_all_fixed_frames_every_split_keep_original_cap(self):
        for raw in qemu.TOPOLOGY_RECORDS:
            self.assertLessEqual(len(raw), 62)
            for split in range(1, len(raw)):
                state = self.receive([raw[:split], raw[split:], b""])
                self.assertEqual(state["complete"], raw == qemu.TOPOLOGY_EXPECTED)
                self.assertEqual(state.get("topology_down_only", False), raw == qemu.TOPOLOGY_EXPECTED)
                self.assertIsNone(state["protocol_error"])
                if raw in qemu.TOPOLOGY_DIAGNOSTICS:
                    self.assertEqual(state["topology_failure"], qemu.TOPOLOGY_DIAGNOSTICS[raw])
                self.assertFalse(self.receive([raw[:split], raw[split:], b""], False)["complete"])
        self.assertFalse(self.receive([qemu.EXPECTED, b""])["complete"])

    def test_unknown_truncated_extra_and_missing_eof_never_pass_or_log_content(self):
        for raw in (qemu.TOPOLOGY_EXPECTED[:-1], qemu.TOPOLOGY_EXPECTED + b"x", qemu.TOPOLOGY_EXPECTED * 2,
                    qemu.TOPOLOGY_EXPECTED[len(qemu.INITRD):], b"private-canary", qemu.ROOT_READ_PREFIX + b"1 0 0\n",
                    qemu.INITRD + b"NIB_DEBUG_TOPOLOGY_V1 F 00 1\n", qemu.INITRD + b"NIB_DEBUG_TOPOLOGY_V1 F 01 2\n"):
            state = self.receive([raw, b""])
            self.assertFalse(state["complete"])
            self.assertIsNotNone(state["protocol_error"])
            self.assertNotIn("private-canary", json.dumps(state))
        state = self.receive([qemu.TOPOLOGY_EXPECTED, socket.timeout()])
        self.assertFalse(state["complete"])
        self.assertEqual(state["protocol_error"], "missing-eof")
        state = self.receive([qemu.NETWORK_PREFIX + b"2 1 70\n", b""])
        self.assertEqual(state["guest_failure"], "NETWORK_SERVICE")
        self.assertFalse(state["complete"])

    def test_clean_process_exit_still_requires_exact_topology_and_owned_cleanup(self):
        for raw, code, stderr in ((qemu.TOPOLOGY_EXPECTED, 0, b""), (qemu.EXPECTED, 0, b""),
                (qemu.TOPOLOGY_EXPECTED, 1, b""), (qemu.TOPOLOGY_EXPECTED, 0, b"diagnostic")):
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary).resolve() / "proof.sock"
                calls = []
                def execute(args, **kwargs):
                    calls.append((args, kwargs))
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                        client.connect(str(path)); client.sendall(raw)
                    return subprocess.CompletedProcess(args, code, b"", stderr)
                state, terminal = {}, {}
                with mock.patch.object(qemu, "SOCKET", path):
                    if raw == qemu.TOPOLOGY_EXPECTED and code == 0 and not stderr:
                        qemu.trial(execute, state, terminal, True)
                    else:
                        with self.assertRaises(ValueError): qemu.trial(execute, state, terminal, True)
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0][1]["timeout"], 180)
                self.assertTrue(terminal["process_group_cleaned"])
                self.assertFalse(path.exists())

    def test_confined_worker_binds_actual_image_before_trial(self):
        expected = worker()["qemu"]
        members = {p: boot.identity(p.encode()) for p in (qemu.QEMU, qemu.LOADER, qemu.CODE, qemu.VARS)}
        for bad in (None, "image", "profile"):
            config = {"schema": "nib.debug-qemu-input/v2", "profile": PROFILE,
                      "runtime_members": members, "debug_uki": expected["debug_uki"]}
            if bad == "image": config["debug_uki"] = boot.identity(b"different")
            if bad == "profile": config["profile"] = "unknown"
            api = mock.Mock(MAX_IMAGE=256 << 20, unique_object=boot.unique_object, identity=boot.identity)
            def read(path, limit):
                if str(path) == "/nib/qemu-input.json": return json.dumps(config).encode()
                if str(path) == "/input/debug.efi": return b"debug image"
                return str(path).encode()
            api.read.side_effect = read
            def trial(_execute, proof, terminal, topology):
                self.assertTrue(topology)
                proof.update(expected["proof"]); terminal.update(expected["terminal"])
            with mock.patch.object(qemu, "module", side_effect=lambda name: api if name == "boot-uki" else mock.Mock()), \
                 mock.patch.object(qemu.sys, "platform", "linux"), mock.patch.object(qemu.sys, "version_info", (3, 12)), \
                 mock.patch.object(qemu.sys, "flags", types.SimpleNamespace(isolated=True, no_site=True, dont_write_bytecode=True)), \
                 mock.patch.object(qemu.sys, "argv", ["debug-qemu.py", "a", "b", "c", "d", "e", "--confined"]), \
                 mock.patch.object(qemu.os, "umask"), mock.patch.object(qemu.resource, "setrlimit"), \
                 mock.patch.object(qemu.os.path, "lexists", return_value=False), mock.patch.object(qemu, "startups"), \
                 mock.patch.object(qemu, "trial", side_effect=trial) as run:
                result = qemu.main()
            report = json.loads(api.write.call_args.args[1])
            self.assertEqual(result, 0 if bad is None else 1)
            self.assertEqual(run.call_count, 1 if bad is None else 0)
            if bad is None:
                self.assertEqual(report["debug_uki"], expected["debug_uki"])
                self.assertEqual(report["schema"], expected["schema"])
                self.assertTrue(report["topology_down_only_verified"])
            else: self.assertFalse(report.get("topology_down_only_verified", False))


class ReceiptTests(unittest.TestCase):
    def test_subordinate_bindings_fail_closed_on_mutation_or_wrong_types(self):
        self.assertTrue(wrapper.topology_worker_passed(worker()))
        changes = [("profile", "unknown"), ("topology_down_only_verified", False), ("root_hash", "b" * 64),
            ("qemu.schema", "nib.debug-qemu/v1"), ("qemu.profile", "unknown"), ("qemu.debug_uki", boot.identity(b"wrong")),
            ("qemu.proof.complete", False), ("qemu.proof.topology_down_only", False),
            ("qemu.proof.complete", 1), ("qemu.terminal.exit_code", False),
            ("qemu.terminal.process_group_cleaned", False), ("qemu.terminal.stderr_bytes", 1),
            ("qemu.confidentiality_verified", True), ("assembly.signed", True), ("assembly.probe", boot.identity(b"wrong")),
            ("assembly.original_uki", boot.identity(b"wrong")), ("compiler.probe.bytes", True)]
        changes += [(path, bad) for path in ("qemu", "assembly", "compiler", "input_files", "assembly.uki", "compiler.probe")
                    for bad in (None, [], "unexpected")]
        for path, bad in changes:
            value = worker(); target = value
            parts = path.split(".")
            for part in parts[:-1]: target = target[part]
            target[parts[-1]] = bad
            with self.subTest(path=path, bad=bad): self.assertFalse(wrapper.topology_worker_passed(value))

    def test_cleanup_preserves_failed_worker_and_separates_narrow_claims(self):
        for outcome in ("pass", "missing", "killed", "old", "source", "malformed", "unbound"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                private, bundle = root / "private", root / "bundle"
                private.mkdir(mode=0o700); bundle.mkdir(mode=0o700)
                value = worker()
                if outcome == "killed": value.update(result="fail", stage="boot", boot_verified=False)
                if outcome == "old": value["schema"] = "nib.debug-boot/v1"
                if outcome == "source": value["source"] = {}
                if outcome == "malformed": value["qemu"] = None
                if outcome == "unbound": value["qemu"]["debug_uki"] = boot.identity(b"different")
                raw = json.dumps(value, separators=(",", ":")).encode() + b"\n"
                if outcome != "missing": (root / "worker.json").write_bytes(raw)
                state = {"result": "pass", "empty_verified": True, "group_removed": True}
                with mock.patch.object(wrapper, "DIRECTORY", root), mock.patch.object(wrapper, "PRIVATE", private), \
                     mock.patch.object(wrapper, "BUNDLE", bundle):
                    result = wrapper.finish(state, private.stat(), bundle.stat(), "2026-10-27T00:00:00Z", producer())
                self.assertEqual(result, "pass" if outcome == "pass" else "fail")
                validation = json.loads((root / "metadata/validation.json").read_bytes())
                self.assertEqual(validation["schema"], "nib.debug-topology-validation/v1")
                self.assertEqual(validation["topology_down_only_verified"], outcome == "pass")
                for claim in ("networkd_verified", "packet_isolation_verified", "confidentiality_verified",
                              "reproducibility_verified", "production_service_verified", "signed"):
                    self.assertFalse(validation[claim])
                if outcome not in ("missing", "old"): self.assertEqual((root / "metadata/worker.json").read_bytes(), raw)
                self.assertFalse(private.exists() or bundle.exists())
                run = json.loads((root / "metadata/run.json").read_bytes())
                self.assertTrue(run["command"].endswith(" --guest-network --debug-topology"))
                self.assertFalse(run["hardware_validated"])

    def test_unknown_cleanup_never_deletes_or_publishes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            private, bundle = root / "private", root / "bundle"
            private.mkdir(); bundle.mkdir()
            with mock.patch.object(wrapper, "DIRECTORY", root), mock.patch.object(wrapper, "PRIVATE", private), \
                 mock.patch.object(wrapper, "BUNDLE", bundle), self.assertRaises(ValueError):
                wrapper.finish({"empty_verified": False, "group_removed": False}, private.stat(), bundle.stat(), "synthetic", producer())
            self.assertTrue(private.exists() and bundle.exists())
            self.assertFalse((root / "metadata").exists())


if __name__ == "__main__":
    unittest.main()
