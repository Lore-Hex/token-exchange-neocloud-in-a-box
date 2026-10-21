# SPDX-License-Identifier: Apache-2.0
"""No package execution: fixed-command fakes and in-memory archive assertions."""
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    module = types.ModuleType("mkosi_probe_fixture")
    module.__file__ = str(ROOT / path)
    exec(compile(Path(module.__file__).read_bytes(), module.__file__, "exec",
                 dont_inherit=True), module.__dict__)
    return module


prep = load("scripts/prepare_mkosi_probe.py")
worker = load("scripts/mkosi_tools_probe.py")
REVISION = "a" * 40
INPUT = {"schema": "nib.mkosi-tools-probe-input/v1", "git_sha": REVISION,
         "tools_receipt_sha256": "b" * 64, "snapshot_sha256": "c" * 64}


def members(raw):
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        return {m.name: archive.extractfile(m).read() for m in archive if m.isfile()}


class WorkerTests(unittest.TestCase):
    def test_fixed_commands_budgets_and_no_raw_process_output(self):
        output = b"synthetic version text; never persist this\n"
        execute = mock.Mock(return_value=subprocess.CompletedProcess([], 0, output, b""))
        with mock.patch.object(worker.time, "monotonic", return_value=100):
            value = worker.probe(INPUT, execute)
        self.assertEqual(value["result"], "pass")
        self.assertEqual(value["stage"], "complete")
        self.assertEqual(value["programs_attempted"], 7)
        self.assertEqual(len(value["checks"]), 7)
        self.assertNotIn(output.decode().strip(), json.dumps(value))
        for (name, argv), call, row in zip(worker.COMMANDS, execute.call_args_list, value["checks"]):
            self.assertEqual(call.args, (list(argv),))
            self.assertEqual(call.kwargs, {"env": worker.ENV, "timeout": 10,
                             "max_output_bytes": 64 << 10, "stdin": subprocess.DEVNULL})
            self.assertEqual(row, {"program": name, "result": "pass", "stdout_bytes": len(output),
                                  "stdout_sha256": prep.base.digest(output)})
        for claim in worker.FALSE_CLAIMS:
            self.assertIs(value[claim], False)

    def test_empty_stderr_oversize_and_nonzero_exit_fail_without_continuing(self):
        invalid = [(0, b"", b""), (0, b"  \n", b""), (0, b"x" * (worker.MAX_OUTPUT + 1), b""),
                   (0, b"version\n", b"warning\n"), (3, b"version\n", b""), (0, "text", b"")]
        for code, out, err in invalid:
            execute = mock.Mock(return_value=subprocess.CompletedProcess([], code, out, err))
            with self.subTest(code=code, size=len(out)):
                value = worker.probe(INPUT, execute)
                self.assertEqual(value["result"], "fail")
                self.assertEqual(value["stage"], "python")
                self.assertEqual(value["programs_attempted"], 1)
                self.assertEqual(value["checks"], [])
                execute.assert_called_once()

    def test_later_failure_retains_prior_checks_and_redacts_exception(self):
        for failure in (OSError("private exception sentinel"), ValueError("private exception sentinel"),
                        subprocess.TimeoutExpired(["private exception sentinel"], 10)):
            execute = mock.Mock(side_effect=[subprocess.CompletedProcess([], 0, b"version", b""), failure])
            value = worker.probe(INPUT, execute)
            self.assertEqual(value["result"], "fail")
            self.assertEqual(value["stage"], "apt")
            self.assertEqual(len(value["checks"]), 1)
            self.assertEqual(value["programs_attempted"], 2)
            self.assertNotIn("private exception sentinel", json.dumps(value))

    def test_deadline_before_start_and_after_return(self):
        for sequence, attempts in (([0, 61], 0), ([0, 1, 61], 1)):
            execute = mock.Mock(return_value=subprocess.CompletedProcess([], 0, b"version", b""))
            with mock.patch.object(worker.time, "monotonic", side_effect=sequence):
                value = worker.probe(INPUT, execute)
            self.assertEqual(value["result"], "fail")
            self.assertEqual(value["programs_attempted"], attempts)
            self.assertEqual(len(value["checks"]), 0)

    def test_input_has_no_command_or_environment_override(self):
        cases = [None, [], {**INPUT, "commands": []}, {**INPUT, "env": {}}]
        for name in INPUT:
            cases.extend(({k: v for k, v in INPUT.items() if k != name}, {**INPUT, name: None},
                          {**INPUT, name: "../unexpected"}))
        for value in cases:
            execute = mock.Mock()
            with self.subTest(value=value), self.assertRaises(ValueError):
                worker.probe(value, execute)
            execute.assert_not_called()

    def test_runtime_preflight_rejects_wrong_platform_uid_and_snapshot(self):
        for platform, uid, data in (("darwin", 65534, b"x"), ("linux", 0, b"x"),
                                    ("linux", 65534, b"untrusted helper")):
            with mock.patch.object(worker.sys, "platform", platform), \
                    mock.patch.object(worker.sys, "argv", ["probe.py"]), \
                    mock.patch.object(worker.os, "geteuid", return_value=uid), \
                    mock.patch.object(worker, "read", side_effect=[json.dumps(INPUT).encode(), data]), \
                    mock.patch.object(worker, "probe") as execute, self.assertRaises(ValueError):
                worker.main()
            execute.assert_not_called()

    def test_read_rejects_links_fifo_size_and_changed_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.write_bytes(b"public bytes")
            self.assertEqual(worker.read(source, 100), b"public bytes")
            with self.assertRaises(ValueError):
                worker.read(source, 1)
            link = root / "link"
            link.symlink_to(source)
            with self.assertRaises(OSError):
                worker.read(link, 100)
            os.mkfifo(root / "fifo")
            with self.assertRaises(ValueError):
                worker.read(root / "fifo", 100)
            before = source.stat()
            changed = types.SimpleNamespace(**{name: getattr(before, name) for name in
                ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")})
            changed.st_mtime_ns += 1
            with mock.patch.object(worker.os, "fstat", side_effect=[before, changed]), \
                    self.assertRaises(ValueError):
                worker.read(source, 100)
            os.link(source, root / "hardlink")
            with self.assertRaises(ValueError):
                worker.read(source, 100)


class ImageTests(unittest.TestCase):
    def test_original_single_layer_apt_format_is_byte_identical(self):
        tools = prep.tools
        layer = tools.tar_bytes({"fixture": {"kind": "file", "mode": 0o444, "data": b"x"}})
        entrypoint = ["/usr/bin/python3.12", "-I", "-S", "-B", "/nib/simulate.py"]
        config = tools.encoded({
            "architecture": "amd64", "os": "linux", "created": "2026-10-20T00:00:00Z",
            "config": {"User": "65534:65534", "WorkingDir": "/work",
                "Env": ["PATH=/usr/bin:/bin", "HOME=/nonexistent", "LANG=C", "LC_ALL=C"],
                "Entrypoint": entrypoint, "Labels": {"org.nib.purpose": "offline-apt-simulation",
                                                       "org.opencontainers.image.revision": REVISION}},
            "rootfs": {"type": "layers", "diff_ids": ["sha256:" + tools.digest(layer)]},
            "history": [{"created": "2026-10-20T00:00:00Z",
                         "created_by": "nib nonexecuting authenticated package assembly"}],
        })
        sha = tools.digest(config)
        tag = "nib-apt-simulation:" + sha[:24]
        manifest = tools.encoded([{"Config": sha + ".json", "RepoTags": [tag], "Layers": ["layer.tar"]}])
        expected = tools.tar_bytes({name: {"kind": "file", "mode": 0o600, "data": raw}
            for name, raw in {sha + ".json": config, "manifest.json": manifest, "layer.tar": layer}.items()})
        observed = tools.docker_archive((layer,), REVISION, entrypoint=entrypoint,
                    purpose="offline-apt-simulation", tag_prefix="nib-apt-simulation")
        self.assertEqual(observed, (expected, sha, tag, [tools.digest(layer)]))

    def test_archive_parameters_and_size_are_bounded(self):
        valid = {"entrypoint": ["/bin/probe"], "purpose": "probe", "tag_prefix": "nib-probe"}
        for layers in ([], (), (b"",), (b"x",) * 3, ("x",)):
            with self.subTest(layers=layers), self.assertRaises(ValueError):
                prep.tools.docker_archive(layers, REVISION, **valid)
        for name, value in (("entrypoint", ["/bin/x;bad"]), ("entrypoint", ["relative"]),
                            ("tag_prefix", "docker.io/untrusted"), ("purpose", "bad value")):
            with self.subTest(name=name), self.assertRaises(ValueError):
                prep.tools.docker_archive((b"x",), REVISION, **{**valid, name: value})
        with mock.patch.object(prep.tools, "MAX_TREE", 1), self.assertRaises(ValueError):
            prep.tools.docker_archive((b"x", b"y"), REVISION, **valid)

    def fixture(self, inventory=None):
        tree = {"usr/bin/fixture": {"kind": "file", "mode": 0o555, "data": b"not executable"}}
        files = {"tools-root.tar": prep.tools.tar_bytes(tree),
                 "root-inventory.json": prep.base.encoded(inventory or prep.tools.observation(tree)),
                 "receipt.json": prep.base.encoded({"synthetic": True})}
        report = {"tools_packages": 203, "source_hashes": {"synthetic": "a" * 64}}
        sources = {prep.WORKER: b"synthetic worker, never executed",
                   prep.tools.FILES[4]: b"synthetic helper, never executed"}
        return files, report, sources

    def test_two_layer_payload_binds_input_and_preserves_original_tree(self):
        files, base_report, sources = self.fixture()
        with mock.patch.object(prep.prep, "assemble", return_value=(files, base_report)):
            output, receipt = prep.build(Path("/synthetic"), REVISION, sources)
            again, _ = prep.build(Path("/synthetic"), REVISION, sources)
        self.assertEqual(output, again)
        image = members(output["image.tar"])
        self.assertEqual(image["layer0.tar"], files["tools-root.tar"])
        config = json.loads(image[receipt["config_sha256"] + ".json"])
        self.assertEqual(config["config"]["User"], "65534:65534")
        self.assertNotIn("Volumes", config["config"])
        self.assertNotIn("ExposedPorts", config["config"])
        self.assertEqual(config["rootfs"]["diff_ids"], ["sha256:" + s for s in receipt["layer_sha256s"]])
        overlay = members(image["layer1.tar"])
        self.assertEqual(set(overlay), {"nib/probe.py", "nib/snapshot.py", "nib/input.json"})
        runtime_input = json.loads(overlay["nib/input.json"])
        worker.validate_input(runtime_input)
        self.assertEqual(runtime_input["tools_receipt_sha256"], prep.base.digest(output["tools-receipt.json"]))
        self.assertEqual(runtime_input["snapshot_sha256"], prep.base.digest(overlay["nib/snapshot.py"]))
        for claim in ("container_executed", "package_binaries_executed", "deployable", "abi_validated"):
            self.assertIs(receipt[claim], False)
        for name, binding in receipt["files"].items():
            self.assertEqual(binding, {"sha256": prep.base.digest(output[name]), "bytes": len(output[name])})

    def test_reserved_worker_namespace_collisions_reject(self):
        for name in ("nib", "nib/probe.py", "nib/input.json", "nib/unexpected"):
            files, report, sources = self.fixture({name: {"kind": "dir"}})
            with mock.patch.object(prep.prep, "assemble", return_value=(files, report)), \
                    self.subTest(name=name), self.assertRaises(ValueError):
                prep.build(Path("/synthetic"), REVISION, sources)

    def test_publication_reverify_rejects_tampering_and_overwrite(self):
        files, report, sources = self.fixture()
        with mock.patch.object(prep.prep, "assemble", return_value=(files, report)):
            output, _ = prep.build(Path("/synthetic"), REVISION, sources)
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary).resolve() / "bundle"
            prep.base.publish(destination, output)
            prep.base.verify(destination, output)
            with self.assertRaises((OSError, ValueError)):
                prep.base.publish(destination, output)
            (destination / "image.tar").write_bytes(b"changed")
            with self.assertRaises(ValueError):
                prep.base.verify(destination, output)


if __name__ == "__main__":
    unittest.main()
