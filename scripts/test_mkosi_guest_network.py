# SPDX-License-Identifier: Apache-2.0
"""Private synthetic composition only: no sysusers, nft, mount or VM execution."""
from contextlib import contextmanager, ExitStack, nullcontext
import configparser
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import yaml

import mkosi_export as export
import mkosi_guest_network as network
from test_mkosi_build import host, launcher, worker, ROOT, load
import test_mkosi_export as export_tests
from test_mkosi_export import proof, publication

base = host.base
SHA = "ab" * 20
ELF = b"\x7fELF\x02\x01\x01 synthetic helper, never executed"


def sources():
    return {name: (ROOT / name).read_bytes() for name in export.NETWORK_BASE.union(export.NETWORK_EXTRA)}


def fixture_inputs():
    raw = sources()
    source = host.source_identity(SHA, raw, True)
    value = {"schema": "nib.guest-network-inputs/v1", "source": source,
             "compiler": {"version": "go1.27.2", "compiler_sha256": base.digest(b"fixture compiler"),
                          "compiler_authenticated": False, "source_hashes": export.network_sources(source["sources"])},
             "files": {name: network.identity(ELF) for name in export.NETWORK_HELPERS}}
    value["files"].update({name: network.identity(raw[path]) for name, path in export.NETWORK_DATA.items()})
    return raw, source, value


class SourceTests(unittest.TestCase):
    def test_network_and_probe_dependencies_are_required_not_ordering_only(self):
        for name in ("debug-boot.conf", "networkd.conf"):
            config = configparser.ConfigParser(interpolation=None)
            config.read_string((ROOT / "guest/network" / name).read_text())
            self.assertEqual(dict(config["Unit"]), {key: "nib-network.service" for key in ("requires", "after", "bindsto")})
        for name in ("nib-network.service", "nib-gpu-gate.service", "nib-weights.service", "nib-engine.service"):
            self.assertIn("OnFailure=nib-failclosed.target", (ROOT / "guest/systemd" / name).read_text())
        self.assertEqual(export.NETWORK_LINKS, {"etc/systemd/system/multi-user.target.wants/nib-network.service":
                                              "/usr/lib/systemd/system/nib-network.service"})

    def test_optional_boot_profile_is_explicit_from_capture_to_workflow(self):
        debug, wrapper = load("host/debug-boot.py"), load("host/run-debug-boot.py")
        _, source, _ = fixture_inputs()
        value = {"schema": "nib.debug-producer/v1", "source": source,
                 "debug_source": {"git_sha": SHA, "sources": {wrapper.SCRIPT: base.digest(wrapper.SELF)}}}
        for selected in (False, True):
            with mock.patch.object(wrapper.subprocess, "Popen", return_value=mock.Mock(returncode=0)) as popen, \
                    mock.patch.object(wrapper.base.processes, "bounded_output", return_value=(export.encoded(value), b"")), \
                    mock.patch.object(wrapper.base.processes, "cleanup_process_group"):
                if selected: self.assertEqual(wrapper.capture_producer(1001, 1002, True), value)
                else:
                    with self.assertRaises(ValueError): wrapper.capture_producer(1001, 1002)
                self.assertEqual("--guest-network" in popen.call_args.args[0], selected)
        bound_debug = {debug.SCRIPT: debug.SELF, debug.runtime.SCRIPT: debug.RUNTIME_SOURCE,
                       debug.validator.SCRIPT: debug.VALIDATOR_SOURCE}
        with mock.patch.object(debug.native, "committed_sources", return_value=(SHA, sources())) as audit, \
                mock.patch.object(debug.tools, "committed", return_value=(SHA, bound_debug)), \
                mock.patch.object(debug, "workflow_source", return_value=b"workflow"):
            debug.committed(True)
        audit.assert_called_once_with(True, True, True, True, guest_network=True)
        workflow = yaml.safe_load((ROOT / debug.WORKFLOW).read_bytes())
        self.assertIs(workflow[True]["workflow_dispatch"]["inputs"]["guest_network"]["default"], False)
        job = workflow["jobs"]["boot"]
        self.assertEqual((job["runs-on"], job["timeout-minutes"]), ("ubuntu-22.04", 35))
        for name in ("build", "debug", "unused_export"):
            step = next(s for s in job["steps"] if s.get("id") == name)
            self.assertEqual(step["env"]["GUEST_NETWORK"], "${{ inputs.guest_network }}")
            self.assertIn('"${network_args[@]}"', step["run"])
            self.assertIn("false)", step["run"])
            self.assertIn("*) exit 1", step["run"])

    def test_exact_profile_and_original_bound(self):
        raw, source, _ = fixture_inputs()
        export.source_identity(source)
        self.assertEqual(len(raw), 42 + 26)
        self.assertEqual(len(set(export.NETWORK_EXTRA)), 26)
        with mock.patch.object(host.tools, "committed", return_value=(SHA, {})) as audit:
            host.committed_sources(True, True, True, True)
        self.assertEqual(set(audit.call_args.args[0]) | set(host.tools.FILES), export.NETWORK_BASE)
        for mutation in ("missing", "extra", "profile", "no-profile", "wrong-hash", "wrong-sha"):
            bad = copy.deepcopy(source)
            if mutation == "missing": bad["sources"].pop(network.SCRIPT)
            if mutation == "extra": bad["sources"]["guest/extra.go"] = base.digest(b"extra")
            if mutation == "profile": bad["profile"] = "other"
            if mutation == "no-profile": bad.pop("profile")
            if mutation == "wrong-hash": bad["sources"][network.SCRIPT] = "unknown"
            if mutation == "wrong-sha": bad["git_sha"] = "unknown"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): export.source_identity(bad)
        with self.assertRaises(ValueError): host.source_identity(SHA, raw)
        with self.assertRaises(ValueError): host.committed_sources(guest_network=True)

    def test_two_source_audits_must_share_commit_and_overlap(self):
        raw = sources()
        first = {p: raw[p] for p in export.NETWORK_BASE}
        second = {p: raw[p] for p in (*host.tools.FILES, *export.NETWORK_EXTRA)}
        for bad in (None, "commit", "overlap", "extra", "missing"):
            candidate = dict(second)
            if bad == "overlap": candidate[host.tools.FILES[0]] = b"changed"
            if bad == "extra": candidate["unexpected"] = b"extra"
            if bad == "missing": candidate.pop(network.SCRIPT)
            with self.subTest(bad=bad), mock.patch.object(host.tools, "committed", side_effect=[
                    (SHA, first), ("cd" * 20 if bad == "commit" else SHA, candidate)]) as audit:
                if bad is None:
                    self.assertEqual(host.committed_sources(True, True, True, True, True), (SHA, raw))
                else:
                    with self.assertRaises(ValueError): host.committed_sources(True, True, True, True, True)
                self.assertEqual(audit.call_args.args, (export.NETWORK_EXTRA,))

    def test_commands_require_explicit_complete_opt_in(self):
        good = ["--install-driver", "--verity-root", "--boot-uki", "--stage-boot", "--guest-network"]
        self.assertEqual(worker.options(good), (True,) * 5)
        for bad in (good[::-1], good[1:], good[:-2] + good[-1:], good + [good[-1]], [good[-1]]):
            with self.assertRaises(ValueError): worker.options(bad)
        with mock.patch.object(host.os, "readlink", return_value="ns:[1]"), \
                mock.patch.object(host.probe.ownership, "prefix", return_value=[]):
            args = (Path("/root"), Path("/payload"), Path("/out"), {}, True, True, True, True, True)
            self.assertEqual(host.command(*args, guest_network=True), host.command(*args) + ["--guest-network"])
            with self.assertRaises(ValueError): host.command(*args[:-1], guest_network=True)
        for entry in (host, launcher):
            for flags in (["--guest-network"], ["--go", "/compiler"], ["--guest-network", "--go", "/compiler"]):
                with mock.patch.object(entry.sys, "argv", ["host", "--downloads", "/unused", "--archive",
                        "/unused", "--output", "/unused", *flags]), self.assertRaises(ValueError): entry.main()

    def test_compiler_closure_deadline_outputs_and_environment(self):
        raw, source, _ = fixture_inputs()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            go = root / "go"
            go.write_bytes(b"fixture compiler, never executed")
            def execute(argv, **kwargs):
                self.assertGreater(kwargs["timeout"], 0)
                self.assertLessEqual(kwargs["timeout"], 120)
                self.assertEqual(kwargs["max_output_bytes"], 65536)
                self.assertEqual(kwargs["max_stderr_bytes"], 65536)
                env = kwargs["env"]
                self.assertNotIn("PRIVATE_SENTINEL", env)
                self.assertEqual([env[k] for k in ("GOOS", "GOARCH", "GOAMD64", "CGO_ENABLED")], ["linux", "amd64", "v1", "0"])
                self.assertTrue(all(env[k] == "off" for k in ("GOPROXY", "GOSUMDB", "GOWORK", "GOENV")))
                output = b""
                if argv[1] == "env": output = b"go1.27.2\n"
                elif argv[1] == "list":
                    role = argv[-1].split("nib-")[-1]
                    prefix = "github.com/Lore-Hex/token-exchange-neocloud-in-a-box/guest/"
                    output = "\n".join(prefix + p for p in ("nftables", "internal/service", "cmd/nib-" + role)).encode()
                else:
                    self.assertEqual(argv[1:5], ["build", "-trimpath", "-buildvcs=false", "-ldflags=-buildid="])
                    Path(argv[6]).write_bytes(ELF)
                return subprocess.CompletedProcess(argv, 0, output, b"")
            with mock.patch.object(base.processes, "execute_process_group", side_effect=execute) as process:
                payload, inputs = network.compile_helpers(go, root / "build", raw, source, base, export)
            self.assertEqual(process.call_count, 9)
            self.assertEqual(set(payload), {"guest-network.py", "network-inputs.json", *export.NETWORK_HELPERS.values(),
                                           *("network/" + p for p in export.NETWORK_DATA.values())})
            export.network_inputs(inputs, source)
            self.assertFalse(inputs["compiler"]["compiler_authenticated"])
            self.assertEqual(inputs["compiler"]["source_hashes"], export.network_sources(source["sources"]))

    def test_compiler_rejects_unknown_version_stderr_imports_and_failure(self):
        raw, source, _ = fixture_inputs()
        for bad in ("version", "stderr", "imports", "exit", "timeout", "deadline"):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
                root = Path(tmp).resolve()
                go = root / "go"
                go.write_bytes(b"fixture compiler")
                def execute(argv, **kwargs):
                    if bad == "timeout": raise subprocess.TimeoutExpired(argv, 1)
                    return subprocess.CompletedProcess(argv, 1 if bad == "exit" else 0,
                        b"wrong\n" if bad == "version" or argv[1] == "list" else b"go1.27.2\n",
                        b"PRIVATE SENTINEL" if bad == "stderr" else b"")
                stack.enter_context(mock.patch.object(base.processes, "execute_process_group", side_effect=execute))
                if bad == "deadline": stack.enter_context(mock.patch.object(network.time, "monotonic", side_effect=[0, 121]))
                with self.assertRaises((ValueError, subprocess.SubprocessError)):
                    network.compile_helpers(go, root / "build", raw, source, base, export)


class CompositionTests(unittest.TestCase):
    @contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            directory = Path(tmp).resolve()
            root, payload, tool = directory / "root", directory / "payload", directory / "sysusers"
            root.mkdir(mode=0o700)
            payload.mkdir(mode=0o700)
            (root / "etc").mkdir()
            accounts = {"etc/passwd": b"root:x:0:0:root:/root:/bin/sh\n", "etc/group": b"root:x:0:\n",
                        "etc/shadow": b"root:!:20000::::::\n", "etc/gshadow": b"root:!::\n"}
            for name, data in accounts.items(): (root / name).write_bytes(data)
            raw, source, inputs = fixture_inputs()
            files = {member: ELF for member in export.NETWORK_HELPERS.values()}
            files.update({"network/" + member: raw[member] for member in export.NETWORK_DATA.values()})
            files["network-inputs.json"] = export.encoded(inputs)
            for name, data in files.items():
                path = payload / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            tool.write_bytes(b"fixture sysusers, never executed")
            tool.chmod(0o755)
            for name, value in (("ROOTFS", root), ("PAYLOAD", payload), ("SYSUSERS", tool),
                                ("SYSUSERS_SIZE", tool.stat().st_size), ("SYSUSERS_SHA256", base.digest(tool.read_bytes()))):
                stack.enter_context(mock.patch.object(network, name, value))
            def execute(argv, **kwargs):
                self.assertEqual(argv, ["fixed-confinement", str(tool), "--root=" + str(root), str(root / "etc/sysusers.d/nib.conf")])
                self.assertNotIn("/etc/sysusers.d/nib.conf", argv)
                self.assertLessEqual(kwargs["timeout"], 30)
                self.assertEqual(kwargs["max_output_bytes"], 65536)
                self.assertEqual(kwargs["max_stderr_bytes"], 65536)
                self.assertEqual(kwargs["env"]["SOURCE_DATE_EPOCH"], str(network.EPOCH))
                self.assertNotIn("CREDENTIALS_DIRECTORY", kwargs["env"])
                rows = {"etc/passwd": b"nib:x:999:999:Neocloud services:/nonexistent:/usr/sbin/nologin\n",
                        "etc/group": b"nib:x:999:\n", "etc/shadow": f"nib:!*:{network.EPOCH // 86400}::::::\n".encode(),
                        "etc/gshadow": b"nib:!*::\n"}
                for name, data in rows.items(): (root / name).write_bytes(accounts[name] + data)
                return subprocess.CompletedProcess(argv, 0, b"", b"PRIVATE SENTINEL")
            processes = mock.Mock()
            processes.execute_process_group.side_effect = execute
            yield root, payload, source, inputs, processes, execute

    def compose(self, root, payload, source, processes):
        return network.compose(processes, export, base.directory, source, ["fixed-confinement"], root, payload)

    def test_success_exact_bytes_account_links_and_no_runtime_claim(self):
        with self.fixture() as (root, payload, source, inputs, processes, _):
            report = self.compose(root, payload, source, processes)
            export.network_proof(report, source)
            self.assertEqual(report["inputs"], inputs)
            self.assertNotIn("PRIVATE SENTINEL", json.dumps(report))
            for name, row in inputs["files"].items():
                path = root / name
                self.assertEqual(network.identity(path.read_bytes()), row)
                self.assertEqual(path.stat().st_mode & 0o777, 0o755 if name in export.NETWORK_HELPERS else 0o644)
                self.assertEqual(path.stat().st_nlink, 1)
            for name, target in export.NETWORK_LINKS.items(): self.assertEqual(os.readlink(root / name), target)
            self.assertEqual(report["account"], {"name": "nib", "uid": 999, "gid": 999, "login_disabled": True})
            self.assertFalse(any(report[k] for k in ("services_started", "network_verified", "confidentiality_verified")))
            processes.execute_process_group.assert_called_once()

    def test_fresh_parent_modes_do_not_inherit_private_umask(self):
        for mask in (0o022, 0o027, 0o077):
            with self.subTest(umask=mask), self.fixture() as (root, payload, source, inputs, processes, _):
                old = os.umask(mask)
                try:
                    self.compose(root, payload, source, processes)
                    self.assertEqual(os.umask(mask), mask)
                finally:
                    os.umask(old)
                for name in (*inputs["files"], *export.NETWORK_LINKS):
                    for parent in (root / name).parents:
                        if parent == root:
                            break
                        self.assertEqual(parent.stat().st_mode & 0o7777, 0o755)
                self.assertEqual((root / "etc/nib/network.json").stat().st_mode & 0o7777, 0o644)

    def test_existing_parent_modes_reject_without_chmod_or_child(self):
        for mode in (0o700, 0o750, 0o711, 0o775, 0o1755):
            with self.subTest(mode=mode), self.fixture() as (root, payload, source, _, processes, _):
                parent = root / "etc/nib"
                parent.mkdir()
                parent.chmod(mode)
                self.assertEqual(parent.stat().st_mode & 0o7777, mode)
                with self.assertRaises(ValueError):
                    self.compose(root, payload, source, processes)
                self.assertEqual(parent.stat().st_mode & 0o7777, mode)
                self.assertFalse((parent / "network.json").exists())
                processes.execute_process_group.assert_not_called()

    def test_existing_setgid_parent_metadata_rejects(self):
        # Some host filesystems discard setgid on chmod; inject only that
        # metadata bit so the rejection remains exercised on every host.
        with self.fixture() as (root, payload, source, _, processes, _):
            parent = root / "etc/nib"
            parent.mkdir()
            identity = parent.stat()
            original = os.fstat
            def observed(fd):
                info = original(fd)
                if (info.st_dev, info.st_ino) == (identity.st_dev, identity.st_ino):
                    fields = list(info)
                    fields[0] |= 0o2000
                    return os.stat_result(fields)
                return info
            with mock.patch.object(network.os, "fstat", side_effect=observed), self.assertRaises(ValueError):
                self.compose(root, payload, source, processes)
            self.assertEqual(parent.stat().st_mode & 0o7777, 0o755)
            self.assertFalse((parent / "network.json").exists())
            processes.execute_process_group.assert_not_called()

    def test_new_parent_mode_failure_stops_before_copy_or_account_creation(self):
        for failure in ("chmod-error", "chmod-ignored"):
            with self.subTest(failure=failure), self.fixture() as (root, payload, source, _, processes, _):
                old = os.umask(0o077)
                try:
                    with mock.patch.object(network.os, "fchmod",
                            side_effect=OSError("synthetic mode failure") if failure == "chmod-error" else None), \
                            self.assertRaises((OSError, ValueError)):
                        self.compose(root, payload, source, processes)
                finally:
                    os.umask(old)
                self.assertFalse((root / "etc/nib/network.json").exists())
                processes.execute_process_group.assert_not_called()

    def test_unsafe_inputs_reject_before_child_and_without_activation(self):
        for bad in ("collision", "parent-link", "hardlink", "oversize", "writable", "source", "payload", "existing-user", "tool"):
            with self.subTest(bad=bad), self.fixture() as (root, payload, source, inputs, processes, _):
                if bad == "collision": (root / "etc/nib").mkdir(); (root / "etc/nib/network.json").write_bytes(b"occupied")
                if bad == "parent-link": (root / "usr").symlink_to(payload, target_is_directory=True)
                if bad == "hardlink": os.link(root / "etc/passwd", root / "etc/linked")
                if bad == "oversize": (root / "etc/passwd").write_bytes(b"x" * 65537)
                if bad == "writable": (root / "etc").chmod(0o777)
                if bad == "source": source = {**source, "git_sha": "cd" * 20}
                if bad == "payload": (payload / "network/nib-network").write_bytes(b"changed")
                if bad == "existing-user": (root / "etc/group").write_bytes(b"nib:x:999:\n")
                if bad == "tool": network.SYSUSERS.write_bytes(b"changed")
                with self.assertRaises((OSError, ValueError)): self.compose(root, payload, source, processes)
                processes.execute_process_group.assert_not_called()
                self.assertFalse(any((root / name).is_symlink() for name in export.NETWORK_LINKS))

    def test_failed_or_malformed_account_creation_cannot_activate(self):
        for bad in ("exit", "timeout", "changed-existing", "unlocked", "login", "id-collision", "group", "epoch"):
            with self.subTest(bad=bad), self.fixture() as (root, payload, source, inputs, processes, execute):
                def reject(argv, **kwargs):
                    if bad == "timeout": raise subprocess.TimeoutExpired(argv, 1)
                    result = execute(argv, **kwargs)
                    if bad == "exit": return subprocess.CompletedProcess(argv, 1, b"", b"PRIVATE SENTINEL")
                    changes = {"changed-existing": ("passwd", b"root:x:0:0", b"root:x:1:1"),
                               "unlocked": ("shadow", b"nib:!*:", b"nib::"),
                               "login": ("passwd", b"/usr/sbin/nologin", b"/bin/sh"),
                               "id-collision": ("passwd", b"nib:x:999:999", b"nib:x:0:999"),
                               "group": ("group", b"nib:x:999:", b"nib:x:998:"),
                               "epoch": ("shadow", str(network.EPOCH // 86400).encode(), b"1")}
                    name, old, new = changes[bad]
                    path = root / "etc" / name
                    path.write_bytes(path.read_bytes().replace(old, new))
                    return result
                processes.execute_process_group.side_effect = reject
                with self.assertRaises((ValueError, subprocess.SubprocessError)): self.compose(root, payload, source, processes)
                self.assertFalse(any((root / name).is_symlink() for name in export.NETWORK_LINKS))

    def test_proof_rejects_incomplete_composition_and_mixed_profiles(self):
        with self.fixture() as (root, payload, source, inputs, processes, _):
            composition = self.compose(root, payload, source, processes)
        report, original = proof({"root.raw": b"fixture GPT", "nib.efi": b"fixture UKI"})
        report["worker"]["artifact_stage"]["source"] = source
        report["worker"]["guest_network"] = composition
        export.validate_proof(report, source)
        for field in composition:
            bad = copy.deepcopy(report)
            del bad["worker"]["guest_network"][field]
            with self.subTest(field=field), self.assertRaises((KeyError, ValueError)): export.validate_proof(bad, source)
        for mutation in ("missing", "wrong-source", "trusted", "missing-compiler-source", "extra-file", "data-hash", "started", "uid", "stream"):
            bad = copy.deepcopy(report)
            value = bad["worker"]["guest_network"]
            if mutation == "missing": del bad["worker"]["guest_network"]
            if mutation == "wrong-source": value["inputs"]["source"]["git_sha"] = "cd" * 20
            if mutation == "trusted": value["inputs"]["compiler"]["compiler_authenticated"] = True
            if mutation == "missing-compiler-source": value["inputs"]["compiler"]["source_hashes"].pop("go.mod")
            if mutation == "extra-file": value["inputs"]["files"]["etc/extra"] = network.identity(b"extra")
            if mutation == "data-hash": value["inputs"]["files"]["etc/nib/network.json"] = network.identity(b"changed")
            if mutation == "started": value["services_started"] = True
            if mutation == "uid": value["account"]["uid"] = True
            if mutation == "stream": value["sysusers_stderr_bytes"] = 65537
            with self.subTest(mutation=mutation), self.assertRaises((KeyError, ValueError)): export.validate_proof(bad, source)
        report["worker"]["artifact_stage"]["source"] = original
        with self.assertRaises(ValueError): export.validate_proof(report, original)

    def test_private_export_consumer_rehashes_selected_profile_then_deletes_images(self):
        validator = load("host/validate-native-boot-export.py")
        fixture = export_tests.ExportTests()
        for selected in (False, True):
            with self.subTest(selected=selected), self.fixture() as (root, payload, source, inputs, processes, _), \
                    fixture.fixture() as (evidence, stage, raws, report, default_source):
                report["worker"]["guest_network"] = self.compose(root, payload, source, processes)
                report["worker"]["artifact_stage"]["source"] = source
                (evidence / "result.json").write_bytes(export.encoded(report))
                fixture.prepare(evidence, stage, report)
                bundle = export.finish(evidence, base, publication, source)
                self.assertEqual({name: (bundle / name).read_bytes() for name in raws}, raws)
                self.assertEqual(export.parsed((bundle / "manifest.json").read_bytes())["source"], source)
                validator_source = {name: base.digest(b"fixture source") for name in (validator.SCRIPT, validator.WORKFLOW)}
                metadata = evidence.parent / "metadata"
                result = validator.validate(evidence, metadata, source if selected else default_source,
                    validator_source, "success", export, publication)
                self.assertEqual(result, "pass" if selected else "fail")
                self.assertFalse(any(path.exists() for path in export.paths(evidence)))
                self.assertTrue(all(path.suffix == ".json" for path in metadata.iterdir()))

    def test_worker_composes_before_verity_and_never_exports_after_failure(self):
        _, source, _ = fixture_inputs()
        for failure in (False, True):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
                root = Path(tmp)
                def path(*parts): return root / Path(*parts).relative_to("/")
                for name in ("nib/prepared", "out", "work"): (root / name).mkdir(parents=True)
                (root / "nib/ownership-input.json").write_text("{}")
                (root / "nib/prepared/guest-inputs.json").write_text(json.dumps({"scope": "guest-runtime-dependency-coverage"}))
                modules = {name: mock.Mock() for name in ("namespace-check", "pid-check", "ownership", "snapshot",
                    "package-inputs", "dkms-worker", "confinement", "runtime-policy", "appraisal-probe",
                    "guest-network", "verity-root", "boot-uki", "boot-export")}
                modules["package-inputs"].regular.return_value = export.encoded(source)
                modules["dkms-worker"].CONTROL_NAMES = ()
                modules["boot-export"].NETWORK_PROFILE = export.NETWORK_PROFILE
                modules["boot-export"].source_identity.side_effect = export.source_identity
                modules["boot-export"].parsed.side_effect = export.parsed
                modules["boot-export"].owned.return_value = nullcontext()
                events = []
                def compose(*args):
                    events.append("compose")
                    self.assertEqual(args[3], source)
                    if failure: raise ValueError("PRIVATE SENTINEL")
                    return {"result": "pass"}
                def verity(*args, require_boot_ids, consume):
                    events.append("verity")
                    consume({"root_hash": "ab" * 32})
                    return {"result": "pass"}
                def boot(*args, diagnostics, consume):
                    events.append("boot")
                    consume({})
                    return {"result": "pass"}
                modules["guest-network"].compose.side_effect = compose
                modules["verity-root"].build.side_effect = verity
                modules["boot-uki"].build.side_effect = boot
                modules["boot-export"].stage.return_value = {"published": False}
                for name, method in (("dkms-worker", "build"), ("runtime-policy", "apply"), ("appraisal-probe", "probe")):
                    getattr(modules[name], method).return_value = {"result": "pass"}
                stack.enter_context(mock.patch.object(worker, "Path", side_effect=path))
                stack.enter_context(mock.patch.object(worker, "module", side_effect=modules.__getitem__))
                stack.enter_context(mock.patch.object(worker, "sys", mock.Mock(platform="linux", version_info=(3, 12),
                    argv=["worker", "1", "2", "3", "4", "5", "--install-driver", "--verity-root", "--boot-uki", "--stage-boot", "--guest-network"])))
                for name in ("geteuid", "getegid"): stack.enter_context(mock.patch.object(worker.os, name, return_value=0))
                for name in ("expected_packages", "prepare_configuration", "install", "installation_environment"):
                    stack.enter_context(mock.patch.object(worker, name, return_value={}))
                stack.enter_context(mock.patch.object(worker, "configured", return_value=212))
                self.assertEqual(worker.main(), 1 if failure else 0)
                report = json.loads((root / "out/result.json").read_bytes())
                self.assertEqual(events, ["compose"] if failure else ["compose", "verity", "boot"])
                self.assertEqual(report["stage"], "guest-network" if failure else "boot-uki")
                self.assertEqual("artifact_stage" in report, not failure)
                self.assertNotIn("PRIVATE SENTINEL", json.dumps(report))
                if failure: modules["boot-export"].stage.assert_not_called()

    def test_compiler_failure_retains_failure_receipt_and_cleans_pending(self):
        raw, source, _ = fixture_inputs()
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp).resolve()
            output, go = parent / "evidence", parent / "go"
            go.write_bytes(b"fixture compiler")
            private = []
            def prepare(*args, **kwargs):
                self.assertTrue(kwargs["guest_network"])
                return {"receipt.json": b"{}"}, {}, {}, {}, {}
            def reject(argv, **kwargs):
                private.append(kwargs["cwd"].parent.parent)
                return subprocess.CompletedProcess(argv, 1, b"", b"PRIVATE SENTINEL")
            with mock.patch.object(host, "prepare", side_effect=prepare), \
                    mock.patch.object(base.processes, "execute_process_group", side_effect=reject), \
                    mock.patch.object(host.probe, "materialize") as materialize:
                self.assertEqual(host.run(parent, parent / "unused", output, SHA, raw,
                    True, True, True, True, True, True, guest_network=True, go=go), 1)
            materialize.assert_not_called()
            report = export.parsed((output / "result.json").read_bytes())
            self.assertEqual(report["result"], "fail")
            self.assertEqual(report["stage"], "guest-helper-compilation")
            self.assertTrue(report["payload_removed"])
            self.assertFalse(any(p.exists() for p in private))
            self.assertFalse(any(p.exists() for p in export.paths(output)))
            self.assertNotIn("PRIVATE SENTINEL", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
